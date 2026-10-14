"""
Warehouse sync service — CSV from S3 → transform → WMS batches.

Reads the exported CSV, transforms each row into the WMS format,
chunks into batches of ≤100, and sends each batch to the WMS.

Key design decisions:

**Idempotency / "never send twice"**
    Before processing, we load the set of already-sent SKUs from the
    state store.  Any product that was already sent (in a previous
    attempt of the same run) is skipped.  This guarantees at-most-once
    delivery *within a run*.

    Across runs, we send the full catalogue each day as required by the
    business.  The WMS is expected to handle upserts for products that
    haven't changed.  See ARCHITECTURE.md §Assumptions for discussion.

**Partial failure isolation**
    Each batch is independent.  If batch N fails after exhausting
    retries, we log the failure, mark the affected SKUs as FAILED in
    the state store, and continue with batch N+1.  This prevents a
    small number of failures from blocking the entire sync.

**Concurrent batch sending**
    Uses a ThreadPoolExecutor to send multiple batches in parallel
    (bounded by ``SYNC_CONCURRENCY`` and the WMS rate limit).
"""

from __future__ import annotations

import csv
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

from app.config import Settings, get_settings
from app.clients.warehouse_api import WarehouseAPIClient, BatchResult
from app.storage.s3 import S3Storage
from app.storage.state import StateStore, RunStatus, ItemStatus
from app.observability import get_logger

logger = get_logger(__name__)


@dataclass
class SyncSummary:
    """Summary of a complete warehouse sync operation."""

    run_id: str
    total_products: int = 0
    total_batches: int = 0
    total_accepted: int = 0
    total_rejected: int = 0
    total_failed: int = 0
    total_skipped_dedup: int = 0
    failed_batches: List[int] = field(default_factory=list)
    rejected_details: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        return self.total_failed == 0


def transform(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transform a PIM product row into the WMS format.

    Args:
        row: Dict from CSV DictReader with PIM field names.

    Returns:
        Dict with WMS field names.
    """
    return {
        "sku": row["id"],
        "description": row["name"],
        "selling_price": float(row["price"]),
        "currency": row["currency"],
        "category_code": row["category"],
        "source_updated_at": row["updated_at"],
    }


def _chunk(items: list, size: int):
    """Yield successive chunks of *size* from *items*."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def sync_catalogue(
    run_id: str,
    s3_key: str,
    *,
    settings: Optional[Settings] = None,
    warehouse_client: Optional[WarehouseAPIClient] = None,
    s3_storage: Optional[S3Storage] = None,
    state_store: Optional[StateStore] = None,
) -> SyncSummary:
    """Read CSV from S3, transform, and send to the WMS in batches.

    Args:
        run_id: Unique identifier for this sync run.
        s3_key: S3 key of the catalogue CSV.
        settings: Application settings (injected for testing).
        warehouse_client: WMS client (injected for testing).
        s3_storage: S3 storage (injected for testing).
        state_store: State store (injected for testing).

    Returns:
        A ``SyncSummary`` with counts and details.
    """
    settings = settings or get_settings()
    warehouse_client = warehouse_client or WarehouseAPIClient(settings)
    s3_storage = s3_storage or S3Storage(settings)
    state_store = state_store or StateStore(settings)

    summary = SyncSummary(run_id=run_id)

    state_store.update_run(run_id, status=RunStatus.SYNCING)

    try:
        # ── Step 1: Load already-sent SKUs (idempotency) ─────────────
        already_sent = state_store.get_sent_skus(run_id)
        if already_sent:
            logger.info(
                "sync_dedup_loaded",
                run_id=run_id,
                already_sent_count=len(already_sent),
            )

        # ── Step 2: Read CSV and transform ───────────────────────────
        # Download CSV from S3 to a local temp file for reading.
        if settings.USE_LOCAL_STORAGE:
            csv_path = str(s3_storage.get_file_path(s3_key))
        else:
            csv_path = os.path.join(settings.EXPORT_DIR, f"{run_id}_sync.csv")
            s3_storage.download_file(s3_key, csv_path)

        products_to_send: List[Dict[str, Any]] = []

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                summary.total_products += 1
                sku = row["id"]

                # Dedup: skip if already processed in a prior attempt.
                if sku in already_sent:
                    summary.total_skipped_dedup += 1
                    continue

                products_to_send.append(transform(row))

        logger.info(
            "sync_products_loaded",
            run_id=run_id,
            total_products=summary.total_products,
            to_send=len(products_to_send),
            skipped_dedup=summary.total_skipped_dedup,
        )

        if not products_to_send:
            logger.info("sync_nothing_to_send", run_id=run_id)
            state_store.update_run(run_id, status=RunStatus.COMPLETED)
            return summary

        # ── Step 3: Chunk into batches and send concurrently ─────────
        batches = list(_chunk(products_to_send, settings.BATCH_SIZE))
        summary.total_batches = len(batches)

        logger.info(
            "sync_sending_batches",
            run_id=run_id,
            total_batches=len(batches),
            concurrency=settings.SYNC_CONCURRENCY,
        )

        def _process_batch(batch_index: int, batch: list) -> BatchResult:
            """Send one batch and update state store."""
            skus = [p["sku"] for p in batch]

            # Mark as sent BEFORE calling WMS (at-most-once guarantee).
            state_store.mark_items_sent(run_id, skus, batch_index)

            result = warehouse_client.send_batch(batch, batch_index=batch_index)

            # Update per-item status based on WMS response.
            if result.is_success:
                for sku in result.accepted:
                    state_store.update_item_status(
                        run_id, sku, ItemStatus.ACCEPTED
                    )
                for rej in result.rejected:
                    state_store.update_item_status(
                        run_id,
                        rej.get("sku", "unknown"),
                        ItemStatus.REJECTED,
                        reason=rej.get("reason"),
                    )
            else:
                # Entire batch failed after retries.
                for sku in skus:
                    state_store.update_item_status(
                        run_id, sku, ItemStatus.FAILED, reason=result.error
                    )

            return result

        # Use a thread pool for concurrent batch sending.
        with ThreadPoolExecutor(max_workers=settings.SYNC_CONCURRENCY) as pool:
            futures = {
                pool.submit(_process_batch, idx, batch): idx
                for idx, batch in enumerate(batches)
            }

            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    result = future.result()
                    if result.is_success:
                        summary.total_accepted += len(result.accepted)
                        summary.total_rejected += len(result.rejected)
                        summary.rejected_details.extend(result.rejected)
                    else:
                        summary.total_failed += len(
                            batches[batch_idx]
                        )
                        summary.failed_batches.append(batch_idx)
                except Exception as exc:
                    logger.error(
                        "sync_batch_exception",
                        run_id=run_id,
                        batch_index=batch_idx,
                        error=str(exc),
                    )
                    summary.total_failed += len(batches[batch_idx])
                    summary.failed_batches.append(batch_idx)

        # ── Step 4: Finalise run ─────────────────────────────────────
        import time

        final_status = RunStatus.COMPLETED if summary.is_success else RunStatus.FAILED
        state_store.update_run(
            run_id,
            status=final_status,
            total_accepted=summary.total_accepted,
            total_rejected=summary.total_rejected,
            total_failed=summary.total_failed,
            completed_at=time.time(),
        )

        logger.info(
            "sync_complete",
            run_id=run_id,
            status=final_status.value,
            total_accepted=summary.total_accepted,
            total_rejected=summary.total_rejected,
            total_failed=summary.total_failed,
            skipped_dedup=summary.total_skipped_dedup,
        )

        return summary

    except Exception as exc:
        state_store.update_run(
            run_id,
            status=RunStatus.FAILED,
            error=str(exc),
        )
        logger.error("sync_failed", run_id=run_id, error=str(exc))
        raise
    finally:
        warehouse_client.close()
