"""
Tests for the catalogue export service.

Verifies:
- CSV is generated with correct columns and data.
- S3 key follows the expected pattern.
- Run state is updated correctly through the export lifecycle.
- Progress tracking (last_export_page) works.
"""

import csv
import os
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.services.catalogue_export import export_catalogue, CSV_FIELDNAMES
from app.storage.state import StateStore, RunStatus


def _make_settings(tmpdir) -> Settings:
    return Settings(
        USE_LOCAL_STORAGE=True,
        EXPORT_DIR=str(tmpdir),
        PRODUCT_API_URL="http://localhost:8001",
        PRODUCT_API_KEY="test-key",
        PRODUCT_API_MAX_RPS=100.0,
        PAGE_SIZE=2,
        MAX_RETRIES=2,
        RETRY_BASE_DELAY=0.01,
        RETRY_MAX_DELAY=0.1,
    )


def _mock_product_client(pages):
    """Create a mock ProductAPIClient that yields the given pages."""
    mock = MagicMock()
    mock.fetch_pages.return_value = iter(pages)
    mock.close = MagicMock()
    return mock


class TestCatalogueExport:
    """Tests for the catalogue export service."""

    def test_export_creates_csv_with_correct_columns(self, tmp_path):
        settings = _make_settings(tmp_path)
        state_store = StateStore(settings)
        state_store.create_run("test-run-001")

        pages = [
            {
                "products": [
                    {"id": "P0000001", "name": "Product 1", "category": "ELECTRONICS",
                     "price": 100.0, "currency": "INR", "updated_at": "2026-09-25T08:30:00Z"},
                    {"id": "P0000002", "name": "Product 2", "category": "HOME",
                     "price": 200.0, "currency": "INR", "updated_at": "2026-09-25T08:30:00Z"},
                ],
                "page": 1,
                "total": 2,
                "has_next": False,
                "next_page": None,
            }
        ]

        s3_key = export_catalogue(
            "test-run-001",
            settings=settings,
            product_client=_mock_product_client(pages),
            state_store=state_store,
        )

        assert s3_key == "exports/test-run-001/catalogue.csv"

        # Verify the CSV was written locally.
        csv_path = os.path.join(str(tmp_path), s3_key)
        assert os.path.exists(csv_path)

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        assert set(reader.fieldnames) == set(CSV_FIELDNAMES)
        assert rows[0]["id"] == "P0000001"
        assert rows[1]["id"] == "P0000002"

    def test_export_updates_run_status(self, tmp_path):
        settings = _make_settings(tmp_path)
        state_store = StateStore(settings)
        state_store.create_run("test-run-002")

        pages = [
            {
                "products": [
                    {"id": "P0000001", "name": "P1", "category": "X",
                     "price": 10, "currency": "INR", "updated_at": "2026-01-01T00:00:00Z"},
                ],
                "page": 1,
                "total": 1,
                "has_next": False,
                "next_page": None,
            }
        ]

        export_catalogue(
            "test-run-002",
            settings=settings,
            product_client=_mock_product_client(pages),
            state_store=state_store,
        )

        run = state_store.get_run("test-run-002")
        assert run["status"] == RunStatus.EXPORT_COMPLETE.value
        assert run["total_products"] == 1
        assert run["s3_key"] == "exports/test-run-002/catalogue.csv"

    def test_export_tracks_page_progress(self, tmp_path):
        settings = _make_settings(tmp_path)
        state_store = StateStore(settings)
        state_store.create_run("test-run-003")

        pages = [
            {
                "products": [
                    {"id": "P0000001", "name": "P1", "category": "X",
                     "price": 10, "currency": "INR", "updated_at": "2026-01-01T00:00:00Z"},
                ],
                "page": 1,
                "total": 3,
                "has_next": True,
                "next_page": 2,
            },
            {
                "products": [
                    {"id": "P0000002", "name": "P2", "category": "X",
                     "price": 20, "currency": "INR", "updated_at": "2026-01-01T00:00:00Z"},
                ],
                "page": 2,
                "total": 3,
                "has_next": True,
                "next_page": 3,
            },
            {
                "products": [
                    {"id": "P0000003", "name": "P3", "category": "Y",
                     "price": 30, "currency": "INR", "updated_at": "2026-01-01T00:00:00Z"},
                ],
                "page": 3,
                "total": 3,
                "has_next": False,
                "next_page": None,
            },
        ]

        export_catalogue(
            "test-run-003",
            settings=settings,
            product_client=_mock_product_client(pages),
            state_store=state_store,
        )

        run = state_store.get_run("test-run-003")
        assert run["last_export_page"] == 3
        assert run["total_products"] == 3
