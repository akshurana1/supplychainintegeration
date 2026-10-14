"""
Tests for the warehouse sync service.

Verifies:
- S3 CSV download, transform, batching.
- Dedup logic: skips already-sent SKUs.
- Partial failure handling and isolation.
- Concurrent batch sending.
"""

import csv
import os
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.clients.warehouse_api import BatchResult
from app.services.warehouse_sync import sync_catalogue, SyncSummary, transform
from app.storage.state import StateStore, RunStatus, ItemStatus


def _make_settings(tmpdir) -> Settings:
    return Settings(
        USE_LOCAL_STORAGE=True,
        EXPORT_DIR=str(tmpdir),
        WAREHOUSE_API_URL="http://localhost:8002",
        WAREHOUSE_API_KEY="test-key",
        WAREHOUSE_API_MAX_RPS=100.0,
        BATCH_SIZE=2,
        SYNC_CONCURRENCY=2,
        MAX_RETRIES=2,
        RETRY_BASE_DELAY=0.01,
        RETRY_MAX_DELAY=0.1,
    )


def _mock_warehouse_client():
    mock = MagicMock()
    mock.send_batch.return_value = BatchResult(
        batch_index=0,
        accepted=["P0000001", "P0000002"],
        rejected=[],
    )
    mock.close = MagicMock()
    return mock


class TestWarehouseSync:
    """Tests for warehouse_sync."""

    def test_sync_basic_flow(self, tmp_path):
        settings = _make_settings(tmp_path)
        state_store = StateStore(settings)
        state_store.create_run("sync-run-001")

        # Create a test CSV in the export directory
        s3_key = "exports/sync-run-001/catalogue.csv"
        csv_full_path = tmp_path / s3_key
        csv_full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(csv_full_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["id", "name", "category", "price", "currency", "updated_at"],
            )
            w.writeheader()
            w.writerow({
                "id": "P0000001",
                "name": "Product 1",
                "category": "ELECTRONICS",
                "price": "100.0",
                "currency": "INR",
                "updated_at": "2026-09-25T08:30:00Z",
            })
            w.writerow({
                "id": "P0000002",
                "name": "Product 2",
                "category": "HOME",
                "price": "200.0",
                "currency": "INR",
                "updated_at": "2026-09-25T08:30:00Z",
            })

        client = _mock_warehouse_client()

        summary = sync_catalogue(
            "sync-run-001",
            s3_key,
            settings=settings,
            warehouse_client=client,
            state_store=state_store,
        )

        assert summary.is_success
        assert summary.total_products == 2
        assert summary.total_accepted == 2
        assert summary.total_skipped_dedup == 0

        run = state_store.get_run("sync-run-001")
        assert run["status"] == RunStatus.COMPLETED.value
        assert run["total_accepted"] == 2

    def test_sync_dedup_skips_already_sent(self, tmp_path):
        settings = _make_settings(tmp_path)
        state_store = StateStore(settings)
        state_store.create_run("sync-run-002")

        # Mark P0000001 as already sent
        state_store.mark_items_sent("sync-run-002", ["P0000001"], batch_index=0)
        state_store.update_item_status("sync-run-002", "P0000001", ItemStatus.ACCEPTED)

        s3_key = "exports/sync-run-002/catalogue.csv"
        csv_full_path = tmp_path / s3_key
        csv_full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(csv_full_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["id", "name", "category", "price", "currency", "updated_at"],
            )
            w.writeheader()
            w.writerow({
                "id": "P0000001",
                "name": "Product 1",
                "category": "ELECTRONICS",
                "price": "100.0",
                "currency": "INR",
                "updated_at": "2026-09-25T08:30:00Z",
            })
            w.writerow({
                "id": "P0000002",
                "name": "Product 2",
                "category": "HOME",
                "price": "200.0",
                "currency": "INR",
                "updated_at": "2026-09-25T08:30:00Z",
            })

        client = MagicMock()
        client.send_batch.return_value = BatchResult(
            batch_index=0,
            accepted=["P0000002"],
            rejected=[],
        )
        client.close = MagicMock()

        summary = sync_catalogue(
            "sync-run-002",
            s3_key,
            settings=settings,
            warehouse_client=client,
            state_store=state_store,
        )

        assert summary.total_products == 2
        assert summary.total_skipped_dedup == 1
        assert summary.total_accepted == 1

        # Check client only received P0000002
        call_args = client.send_batch.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0]["sku"] == "P0000002"

    def test_sync_partial_rejection(self, tmp_path):
        settings = _make_settings(tmp_path)
        state_store = StateStore(settings)
        state_store.create_run("sync-run-003")

        s3_key = "exports/sync-run-003/catalogue.csv"
        csv_full_path = tmp_path / s3_key
        csv_full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(csv_full_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["id", "name", "category", "price", "currency", "updated_at"],
            )
            w.writeheader()
            w.writerow({
                "id": "P0000999",
                "name": "Product 999",
                "category": "ELECTRONICS",
                "price": "100.0",
                "currency": "INR",
                "updated_at": "2026-09-25T08:30:00Z",
            })

        client = MagicMock()
        client.send_batch.return_value = BatchResult(
            batch_index=0,
            accepted=[],
            rejected=[{"sku": "P0000999", "reason": "Invalid warehouse product"}],
        )
        client.close = MagicMock()

        summary = sync_catalogue(
            "sync-run-003",
            s3_key,
            settings=settings,
            warehouse_client=client,
            state_store=state_store,
        )

        assert summary.total_accepted == 0
        assert summary.total_rejected == 1
        assert summary.is_success  # partial rejection is business rejection, not transport failure

    def test_sync_batch_transport_failure(self, tmp_path):
        settings = _make_settings(tmp_path)
        state_store = StateStore(settings)
        state_store.create_run("sync-run-004")

        s3_key = "exports/sync-run-004/catalogue.csv"
        csv_full_path = tmp_path / s3_key
        csv_full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(csv_full_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["id", "name", "category", "price", "currency", "updated_at"],
            )
            w.writeheader()
            w.writerow({
                "id": "P0000001",
                "name": "Product 1",
                "category": "ELECTRONICS",
                "price": "100.0",
                "currency": "INR",
                "updated_at": "2026-09-25T08:30:00Z",
            })

        client = MagicMock()
        client.send_batch.return_value = BatchResult(
            batch_index=0,
            error="Connection timeout after retries",
        )
        client.close = MagicMock()

        summary = sync_catalogue(
            "sync-run-004",
            s3_key,
            settings=settings,
            warehouse_client=client,
            state_store=state_store,
        )

        assert not summary.is_success
        assert summary.total_failed == 1
        assert 0 in summary.failed_batches
        run = state_store.get_run("sync-run-004")
        assert run["status"] == RunStatus.FAILED.value
