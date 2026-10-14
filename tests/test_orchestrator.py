"""
Tests for the main orchestrator pipeline.

Verifies:
- End-to-end execution: export -> sync -> reporting.
- Resume capability from interrupted runs.
- Run status transitions in state store.
- Failure handling and logging.
"""

from unittest.mock import MagicMock, patch
import pytest

from app.config import Settings
from app.services.warehouse_sync import SyncSummary
from app.storage.state import StateStore, RunStatus
from app.main import run, generate_run_id


def _make_settings(tmpdir) -> Settings:
    return Settings(
        USE_LOCAL_STORAGE=True,
        EXPORT_DIR=str(tmpdir),
        PRODUCT_API_URL="http://localhost:8001",
        WAREHOUSE_API_URL="http://localhost:8002",
        PAGE_SIZE=2,
        BATCH_SIZE=2,
        SYNC_CONCURRENCY=2,
    )


class TestOrchestrator:
    """Tests for orchestrator run and resume."""

    def test_generate_run_id(self):
        run_id_1 = generate_run_id()
        run_id_2 = generate_run_id()
        assert run_id_1 != run_id_2
        assert len(run_id_1.split("-")) >= 3

    def test_run_end_to_end_success(self, tmp_path):
        settings = _make_settings(tmp_path)

        mock_summary = SyncSummary(
            run_id="test-run",
            total_products=10,
            total_batches=5,
            total_accepted=10,
            total_rejected=0,
            total_failed=0,
            total_skipped_dedup=0,
        )

        with patch("app.main.get_settings", return_value=settings), \
             patch("app.main.export_catalogue", return_value="exports/test-run/catalogue.csv") as mock_export, \
             patch("app.main.sync_catalogue", return_value=mock_summary) as mock_sync:

            summary = run()

            assert summary.is_success
            assert summary.total_accepted == 10
            mock_export.assert_called_once()
            mock_sync.assert_called_once()

    def test_resume_nonexistent_run_raises(self, tmp_path):
        settings = _make_settings(tmp_path)
        with patch("app.main.get_settings", return_value=settings):
            with pytest.raises(ValueError, match="not found"):
                run(resume_run_id="non-existent-run-id")

    def test_resume_export_complete_skips_export(self, tmp_path):
        settings = _make_settings(tmp_path)
        state_store = StateStore(settings)
        run_id = "resumed-run-001"
        state_store.create_run(run_id)
        state_store.update_run(
            run_id,
            status=RunStatus.EXPORT_COMPLETE,
            s3_key=f"exports/{run_id}/catalogue.csv",
            total_products=50,
        )

        mock_summary = SyncSummary(
            run_id=run_id,
            total_products=50,
            total_batches=25,
            total_accepted=50,
            total_rejected=0,
            total_failed=0,
            total_skipped_dedup=0,
        )

        with patch("app.main.get_settings", return_value=settings), \
             patch("app.main.StateStore", return_value=state_store), \
             patch("app.main.export_catalogue") as mock_export, \
             patch("app.main.sync_catalogue", return_value=mock_summary) as mock_sync:

            summary = run(resume_run_id=run_id)

            assert summary.is_success
            # Export was already completed, so export_catalogue should NOT be called
            mock_export.assert_not_called()
            # Sync should be called with the existing s3_key
            mock_sync.assert_called_once()
            assert mock_sync.call_args[0][1] == f"exports/{run_id}/catalogue.csv"
