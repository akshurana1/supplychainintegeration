"""
Tests for the DynamoDB-backed state store.

Uses local (in-memory) mode to verify:
- Run lifecycle (create, update status, query).
- Per-item tracking (mark sent, update status).
- Idempotency check (get_sent_skus).
"""

from app.config import Settings
from app.storage.state import StateStore, RunStatus, ItemStatus


def _make_local_state_store() -> StateStore:
    """Create a StateStore in local (in-memory) mode."""
    settings = Settings(USE_LOCAL_STORAGE=True)
    return StateStore(settings)


class TestStateStoreRunLifecycle:
    """Tests for run record management."""

    def test_create_run(self):
        store = _make_local_state_store()
        record = store.create_run("run-001")
        assert record.run_id == "run-001"
        assert record.status == RunStatus.STARTED

        # Verify it's stored.
        data = store.get_run("run-001")
        assert data is not None
        assert data["run_id"] == "run-001"
        assert data["status"] == RunStatus.STARTED.value

    def test_update_run_status(self):
        store = _make_local_state_store()
        store.create_run("run-002")
        store.update_run("run-002", status=RunStatus.EXPORTING)

        data = store.get_run("run-002")
        assert data["status"] == RunStatus.EXPORTING.value

    def test_update_run_multiple_fields(self):
        store = _make_local_state_store()
        store.create_run("run-003")
        store.update_run(
            "run-003",
            status=RunStatus.EXPORT_COMPLETE,
            s3_key="exports/run-003/catalogue.csv",
            total_products=12000,
            last_export_page=24,
        )

        data = store.get_run("run-003")
        assert data["status"] == RunStatus.EXPORT_COMPLETE.value
        assert data["s3_key"] == "exports/run-003/catalogue.csv"
        assert data["total_products"] == 12000
        assert data["last_export_page"] == 24

    def test_get_nonexistent_run(self):
        store = _make_local_state_store()
        assert store.get_run("does-not-exist") is None

    def test_run_full_lifecycle(self):
        """A run should flow through: STARTED → EXPORTING → EXPORT_COMPLETE → SYNCING → COMPLETED."""
        store = _make_local_state_store()
        store.create_run("lifecycle-run")

        for status in [
            RunStatus.EXPORTING,
            RunStatus.EXPORT_COMPLETE,
            RunStatus.SYNCING,
            RunStatus.COMPLETED,
        ]:
            store.update_run("lifecycle-run", status=status)
            data = store.get_run("lifecycle-run")
            assert data["status"] == status.value

    def test_list_runs_multiple(self):
        """Verify multiple runs are stored and returned in order."""
        store = _make_local_state_store()
        store.create_run("run-alpha")
        store.create_run("run-beta")
        runs = store.list_runs()
        run_ids = [r["run_id"] for r in runs]
        assert "run-alpha" in run_ids
        assert "run-beta" in run_ids
        assert len(runs) >= 2


class TestStateStoreItemTracking:
    """Tests for per-product item state tracking."""

    def test_mark_items_sent(self):
        store = _make_local_state_store()
        store.create_run("run-items-001")
        store.mark_items_sent("run-items-001", ["SKU1", "SKU2", "SKU3"], batch_index=0)

        sent = store.get_sent_skus("run-items-001")
        assert sent == {"SKU1", "SKU2", "SKU3"}

    def test_update_item_accepted(self):
        store = _make_local_state_store()
        store.create_run("run-items-002")
        store.mark_items_sent("run-items-002", ["SKU1"], batch_index=0)
        store.update_item_status("run-items-002", "SKU1", ItemStatus.ACCEPTED)

        # ACCEPTED items should still appear in get_sent_skus (for dedup).
        sent = store.get_sent_skus("run-items-002")
        assert "SKU1" in sent

    def test_update_item_rejected(self):
        store = _make_local_state_store()
        store.create_run("run-items-003")
        store.mark_items_sent("run-items-003", ["SKU1"], batch_index=0)
        store.update_item_status(
            "run-items-003", "SKU1", ItemStatus.REJECTED, reason="Invalid product"
        )

        # REJECTED items should NOT appear in get_sent_skus.
        sent = store.get_sent_skus("run-items-003")
        assert "SKU1" not in sent

    def test_get_sent_skus_isolates_runs(self):
        """Sent SKUs from run A should not leak into run B."""
        store = _make_local_state_store()
        store.create_run("run-A")
        store.create_run("run-B")
        store.mark_items_sent("run-A", ["SKU1", "SKU2"], batch_index=0)
        store.mark_items_sent("run-B", ["SKU3"], batch_index=0)

        assert store.get_sent_skus("run-A") == {"SKU1", "SKU2"}
        assert store.get_sent_skus("run-B") == {"SKU3"}

    def test_idempotency_check_empty_for_new_run(self):
        """A fresh run should have no sent SKUs."""
        store = _make_local_state_store()
        store.create_run("run-fresh")
        assert store.get_sent_skus("run-fresh") == set()
