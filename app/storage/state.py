"""
DynamoDB-backed state store for sync run tracking and idempotency.

This is the core mechanism that addresses the "same product must never
be sent twice" requirement.  Two tables are used:

``sync_runs``
    One row per sync run.  Tracks overall status, timing, and counters.

``sync_items``
    One row per (run_id, sku).  Tracks whether a specific product has
    been sent and what the WMS responded.  Before sending a batch, the
    sync service checks this table to skip already-processed SKUs.

In local mode, SQLite persists the state locally to disk (defaulting to
``runtime/state.db``) so that runs can be inspected and resumed across
separate process executions without requiring AWS DynamoDB.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

import boto3
from botocore.exceptions import ClientError

from app.config import Settings, get_settings
from app.observability import get_logger

logger = get_logger(__name__)


class RunStatus(str, Enum):
    """Lifecycle states of a sync run."""

    STARTED = "STARTED"
    EXPORTING = "EXPORTING"
    EXPORT_COMPLETE = "EXPORT_COMPLETE"
    SYNCING = "SYNCING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ItemStatus(str, Enum):
    """Processing state of a single product within a run."""

    PENDING = "PENDING"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass
class RunRecord:
    """In-memory representation of a sync run."""

    run_id: str
    status: RunStatus = RunStatus.STARTED
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    s3_key: Optional[str] = None
    total_products: int = 0
    total_accepted: int = 0
    total_rejected: int = 0
    total_failed: int = 0
    last_export_page: int = 0
    error: Optional[str] = None


class StateStore:
    """Abstraction over DynamoDB (or SQLite locally) for run and item state.

    In local mode, uses an on-disk SQLite database so state persists across
    CLI runs, enabling local resumption via ``--resume <run_id>``.
    In AWS mode, uses DynamoDB via boto3.
    """

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self._use_local = self._settings.USE_LOCAL_STORAGE
        self._lock = threading.Lock()

        if self._use_local:
            db_dir = self._settings.EXPORT_DIR
            os.makedirs(db_dir, exist_ok=True)
            self._db_path = os.path.join(db_dir, "state.db")
            self._init_sqlite()
        else:
            kwargs: Dict[str, Any] = {"region_name": self._settings.AWS_REGION}
            if self._settings.AWS_ENDPOINT_URL:
                kwargs["endpoint_url"] = self._settings.AWS_ENDPOINT_URL
            self._dynamodb = boto3.resource("dynamodb", **kwargs)
            self._runs_table = self._dynamodb.Table(self._settings.DYNAMODB_TABLE_RUNS)
            self._items_table = self._dynamodb.Table(self._settings.DYNAMODB_TABLE_ITEMS)

    def _init_sqlite(self) -> None:
        """Initialize SQLite tables for local mode."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    s3_key TEXT,
                    total_products INTEGER DEFAULT 0,
                    total_accepted INTEGER DEFAULT 0,
                    total_rejected INTEGER DEFAULT 0,
                    total_failed INTEGER DEFAULT 0,
                    last_export_page INTEGER DEFAULT 0,
                    error TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_items (
                    run_sku TEXT PRIMARY KEY,
                    run_id TEXT,
                    sku TEXT,
                    status TEXT,
                    batch_index INTEGER,
                    reason TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_run ON sync_items(run_id, status)
            """)
            conn.commit()

    # ── Run operations ───────────────────────────────────────────────

    def create_run(self, run_id: str) -> RunRecord:
        """Create a new sync run record."""
        record = RunRecord(run_id=run_id)

        if self._use_local:
            with self._lock, sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sync_runs
                    (run_id, status, started_at, total_products, total_accepted,
                     total_rejected, total_failed, last_export_page)
                    VALUES (?, ?, ?, 0, 0, 0, 0, 0)
                    """,
                    (run_id, record.status.value, str(record.started_at)),
                )
                conn.commit()
        else:
            item = {
                "run_id": run_id,
                "status": record.status.value,
                "started_at": str(record.started_at),
                "total_products": 0,
                "total_accepted": 0,
                "total_rejected": 0,
                "total_failed": 0,
                "last_export_page": 0,
            }
            self._runs_table.put_item(Item=item)

        logger.info("run_created", run_id=run_id)
        return record

    def update_run(
        self,
        run_id: str,
        *,
        status: Optional[RunStatus] = None,
        s3_key: Optional[str] = None,
        total_products: Optional[int] = None,
        total_accepted: Optional[int] = None,
        total_rejected: Optional[int] = None,
        total_failed: Optional[int] = None,
        last_export_page: Optional[int] = None,
        completed_at: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update specific fields on a run record."""
        updates: Dict[str, Any] = {}
        if status is not None:
            updates["status"] = status.value
        if s3_key is not None:
            updates["s3_key"] = s3_key
        if total_products is not None:
            updates["total_products"] = total_products
        if total_accepted is not None:
            updates["total_accepted"] = total_accepted
        if total_rejected is not None:
            updates["total_rejected"] = total_rejected
        if total_failed is not None:
            updates["total_failed"] = total_failed
        if last_export_page is not None:
            updates["last_export_page"] = last_export_page
        if completed_at is not None:
            updates["completed_at"] = str(completed_at)
        if error is not None:
            updates["error"] = error

        if not updates:
            return

        if self._use_local:
            set_clauses = [f"{k} = ?" for k in updates.keys()]
            values = list(updates.values()) + [run_id]
            with self._lock, sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    f"UPDATE sync_runs SET {', '.join(set_clauses)} WHERE run_id = ?",
                    values,
                )
                conn.commit()
        else:
            expr_parts = []
            expr_values = {}
            expr_names = {}
            for i, (k, v) in enumerate(updates.items()):
                attr_name = f"#a{i}"
                attr_val = f":v{i}"
                expr_parts.append(f"{attr_name} = {attr_val}")
                expr_names[attr_name] = k
                expr_values[attr_val] = v
            self._runs_table.update_item(
                Key={"run_id": run_id},
                UpdateExpression="SET " + ", ".join(expr_parts),
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            )

        logger.info("run_updated", run_id=run_id, updates=updates)

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a run record by ID."""
        if self._use_local:
            with self._lock, sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM sync_runs WHERE run_id = ?", (run_id,))
                row = cursor.fetchone()
                return dict(row) if row else None

        resp = self._runs_table.get_item(Key={"run_id": run_id})
        return resp.get("Item")

    # ── Item (per-product) operations ────────────────────────────────

    def mark_items_sent(
        self,
        run_id: str,
        skus: List[str],
        batch_index: int,
    ) -> None:
        """Mark a set of SKUs as sent to the WMS."""
        if self._use_local:
            records = [
                (f"{run_id}#{sku}", run_id, sku, ItemStatus.SENT.value, batch_index)
                for sku in skus
            ]
            with self._lock, sqlite3.connect(self._db_path) as conn:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO sync_items
                    (run_sku, run_id, sku, status, batch_index)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    records,
                )
                conn.commit()
        else:
            for sku in skus:
                item = {
                    "run_sku": f"{run_id}#{sku}",
                    "run_id": run_id,
                    "sku": sku,
                    "status": ItemStatus.SENT.value,
                    "batch_index": batch_index,
                }
                self._items_table.put_item(Item=item)

    def update_item_status(
        self,
        run_id: str,
        sku: str,
        status: ItemStatus,
        reason: Optional[str] = None,
    ) -> None:
        """Update the status of a single product within a run."""
        key = f"{run_id}#{sku}"
        if self._use_local:
            with self._lock, sqlite3.connect(self._db_path) as conn:
                if reason:
                    conn.execute(
                        "UPDATE sync_items SET status = ?, reason = ? WHERE run_sku = ?",
                        (status.value, reason, key),
                    )
                else:
                    conn.execute(
                        "UPDATE sync_items SET status = ? WHERE run_sku = ?",
                        (status.value, key),
                    )
                conn.commit()
        else:
            update_expr = "SET #s = :s"
            expr_names = {"#s": "status"}
            expr_values = {":s": status.value}
            if reason:
                update_expr += ", reason = :r"
                expr_values[":r"] = reason
            self._items_table.update_item(
                Key={"run_sku": key},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_names,
                ExpressionAttributeValues=expr_values,
            )

    def get_sent_skus(self, run_id: str) -> Set[str]:
        """Return all SKUs that have already been sent/accepted for a run.

        This is the **idempotency check**: before sending a batch to the
        WMS, the sync service calls this to filter out products that were
        already successfully processed (e.g. in a previous attempt before
        a crash).
        """
        sent_statuses = (ItemStatus.SENT.value, ItemStatus.ACCEPTED.value)

        if self._use_local:
            with self._lock, sqlite3.connect(self._db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT sku FROM sync_items WHERE run_id = ? AND status IN (?, ?)",
                    (run_id, sent_statuses[0], sent_statuses[1]),
                )
                return {row[0] for row in cursor.fetchall()}

        skus: Set[str] = set()
        scan_kwargs = {
            "FilterExpression": "#rid = :rid AND #s IN (:s1, :s2)",
            "ExpressionAttributeNames": {"#rid": "run_id", "#s": "status"},
            "ExpressionAttributeValues": {
                ":rid": run_id,
                ":s1": ItemStatus.SENT.value,
                ":s2": ItemStatus.ACCEPTED.value,
            },
        }
        while True:
            resp = self._items_table.scan(**scan_kwargs)
            for item in resp.get("Items", []):
                skus.add(item["sku"])
            if "LastEvaluatedKey" not in resp:
                break
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

        return skus
