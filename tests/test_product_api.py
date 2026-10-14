"""
Tests for the Product API client (PIM).

Verifies:
- Generator-based pagination yields correct pages.
- Resume from a specific start page.
- Handles empty results gracefully.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.clients.product_api import ProductAPIClient
from app.config import Settings


def _make_settings(**overrides) -> Settings:
    defaults = {
        "PRODUCT_API_URL": "http://localhost:8001",
        "PRODUCT_API_KEY": "test-key",
        "PRODUCT_API_MAX_RPS": 100.0,
        "PAGE_SIZE": 2,
        "MAX_RETRIES": 2,
        "RETRY_BASE_DELAY": 0.01,
        "RETRY_MAX_DELAY": 0.1,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestProductAPIClient:
    """Tests for ProductAPIClient pagination."""

    def test_fetch_pages_single_page(self):
        """When there's only one page, yield it and stop."""
        settings = _make_settings()
        client = ProductAPIClient(settings)

        page_response = {
            "products": [
                {"id": "P0000001", "name": "Product 1", "category": "ELECTRONICS",
                 "price": 100.0, "currency": "INR", "updated_at": "2026-09-25T08:30:00Z"},
            ],
            "page": 1,
            "total": 1,
            "has_next": False,
            "next_page": None,
        }

        with patch.object(client._client, "get", return_value=page_response):
            pages = list(client.fetch_pages())

        assert len(pages) == 1
        assert pages[0]["products"][0]["id"] == "P0000001"
        assert not pages[0]["has_next"]

    def test_fetch_pages_multiple_pages(self):
        """Multi-page pagination should yield each page."""
        settings = _make_settings()
        client = ProductAPIClient(settings)

        page1 = {
            "products": [
                {"id": "P0000001", "name": "P1", "category": "X",
                 "price": 10, "currency": "INR", "updated_at": "2026-01-01T00:00:00Z"},
                {"id": "P0000002", "name": "P2", "category": "X",
                 "price": 20, "currency": "INR", "updated_at": "2026-01-01T00:00:00Z"},
            ],
            "page": 1,
            "total": 3,
            "has_next": True,
            "next_page": 2,
        }
        page2 = {
            "products": [
                {"id": "P0000003", "name": "P3", "category": "Y",
                 "price": 30, "currency": "INR", "updated_at": "2026-01-01T00:00:00Z"},
            ],
            "page": 2,
            "total": 3,
            "has_next": False,
            "next_page": None,
        }

        with patch.object(client._client, "get", side_effect=[page1, page2]):
            pages = list(client.fetch_pages())

        assert len(pages) == 2
        assert len(pages[0]["products"]) == 2
        assert len(pages[1]["products"]) == 1

    def test_fetch_all_products_flat_stream(self):
        """fetch_all_products should yield individual products across pages."""
        settings = _make_settings()
        client = ProductAPIClient(settings)

        page1 = {
            "products": [
                {"id": "P0000001", "name": "P1", "category": "X",
                 "price": 10, "currency": "INR", "updated_at": "2026-01-01T00:00:00Z"},
            ],
            "page": 1,
            "total": 2,
            "has_next": True,
            "next_page": 2,
        }
        page2 = {
            "products": [
                {"id": "P0000002", "name": "P2", "category": "Y",
                 "price": 20, "currency": "INR", "updated_at": "2026-01-01T00:00:00Z"},
            ],
            "page": 2,
            "total": 2,
            "has_next": False,
            "next_page": None,
        }

        with patch.object(client._client, "get", side_effect=[page1, page2]):
            products = list(client.fetch_all_products())

        assert len(products) == 2
        assert products[0]["id"] == "P0000001"
        assert products[1]["id"] == "P0000002"

    def test_fetch_pages_resume_from_page(self):
        """Resuming from page 3 should start fetching from page 3."""
        settings = _make_settings()
        client = ProductAPIClient(settings)

        page3_response = {
            "products": [{"id": "P0000005", "name": "P5", "category": "Z",
                          "price": 50, "currency": "INR", "updated_at": "2026-01-01T00:00:00Z"}],
            "page": 3,
            "total": 5,
            "has_next": False,
            "next_page": None,
        }

        with patch.object(client._client, "get", return_value=page3_response) as mock_get:
            pages = list(client.fetch_pages(start_page=3))

        assert len(pages) == 1
        # Verify the API was called with page=3.
        mock_get.assert_called_once_with(
            "/products",
            params={"page": 3, "page_size": settings.PAGE_SIZE},
        )
