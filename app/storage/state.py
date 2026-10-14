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

In local mode, an in-memory dictionary provides the same interface so
the app can run without DynamoDB / LocalStack.
"""

from __future__ import annotations

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
    """Abstraction over DynamoDB for run and item state tracking.

    In local mode, uses in-memory dicts.  In AWS mode, uses DynamoDB
    via boto3 (works with LocalStack endpoint override).
    """

    def __init__(self, settings: Optional[Settings] = None):
        self._settings = settings or get_settings()
        self._use_local = self._settings.USE_LOCAL_STORAGE

        if self._use_local:
            self._runs: Dict[str, Dict[str, Any]] = {}
            self._items: Dict[str, Dict[str, Any]] = {}
        else:
            kwargs: Dict[str, Any] = {"region_name": self._settings.AWS_REGION}
            if self._settings.AWS_ENDPOINT_URL:
                kwargs["endpoint_url"] = self._settings.AWS_ENDPOINT_URL
            self._dynamodb = boto3.resource("dynamodb", **kwargs)
            self._runs_table = self._dynamodb.Table(self._settings.DYNAMODB_TABLE_RUNS)
            self._items_table = self._dynamodb.Table(self._settings.DYNAMODB_TABLE_ITEMS)

    # ── Run operations ───────────────────────────────────────────────

    def create_run(self, run_id: str) -> RunRecord:
        """Create a new sync run record."""
        record = RunRecord(run_id=run_id)
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

        if self._use_local:
            self._runs[run_id] = item
        else:
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
            if run_id not in self._runs:
                raise KeyError(f"Run {run_id} not found")
            self._runs[run_id].update(updates)
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
            return self._runs.get(run_id)

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
        for sku in skus:
            item = {
                "run_sku": f"{run_id}#{sku}",
                "run_id": run_id,
                "sku": sku,
                "status": ItemStatus.SENT.value,
                "batch_index": batch_index,
            }
            if self._use_local:
                self._items[f"{run_id}#{sku}"] = item
            else:
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
            if key in self._items:
                self._items[key]["status"] = status.value
                if reason:
                    self._items[key]["reason"] = reason
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
        sent_statuses = {ItemStatus.SENT.value, ItemStatus.ACCEPTED.value}

        if self._use_local:
            return {
                v["sku"]
                for v in self._items.values()
                if v.get("run_id") == run_id and v.get("status") in sent_statuses
            }

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
