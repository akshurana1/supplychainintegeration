"""
Tests for the Warehouse API client (WMS).

Verifies:
- Successful batch sending with all accepted.
- Partial success (some accepted, some rejected).
- Batch failure after retries.
- Batch size validation.
"""

from unittest.mock import patch

import pytest

from app.clients.warehouse_api import WarehouseAPIClient, BatchResult
from app.config import Settings


def _make_settings(**overrides) -> Settings:
    defaults = {
        "WAREHOUSE_API_URL": "http://localhost:8002",
        "WAREHOUSE_API_KEY": "test-key",
        "WAREHOUSE_API_MAX_RPS": 100.0,
        "BATCH_SIZE": 100,
        "MAX_RETRIES": 2,
        "RETRY_BASE_DELAY": 0.01,
        "RETRY_MAX_DELAY": 0.1,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestWarehouseAPIClient:
    """Tests for WarehouseAPIClient."""

    def test_send_batch_all_accepted(self):
        """A fully accepted batch should return all SKUs in accepted list."""
        settings = _make_settings()
        client = WarehouseAPIClient(settings)

        response = {
            "accepted": ["P0000001", "P0000002"],
            "rejected": [],
            "message": "Processed",
        }

        with patch.object(client._client, "post", return_value=response):
            result = client.send_batch(
                [
                    {"sku": "P0000001", "description": "P1", "selling_price": 10.0,
                     "currency": "INR", "category_code": "ELECTRONICS", "source_updated_at": "2026-01-01T00:00:00Z"},
                    {"sku": "P0000002", "description": "P2", "selling_price": 20.0,
                     "currency": "INR", "category_code": "HOME", "source_updated_at": "2026-01-01T00:00:00Z"},
                ],
                batch_index=0,
            )

        assert result.is_success
        assert result.accepted == ["P0000001", "P0000002"]
        assert result.rejected == []

    def test_send_batch_partial_rejection(self):
        """A batch with some rejected products should report both lists."""
        settings = _make_settings()
        client = WarehouseAPIClient(settings)

        response = {
            "accepted": ["P0000001"],
            "rejected": [{"sku": "P0000999", "reason": "Invalid warehouse product"}],
            "message": "Processed",
        }

        with patch.object(client._client, "post", return_value=response):
            result = client.send_batch(
                [
                    {"sku": "P0000001", "description": "P1", "selling_price": 10.0,
                     "currency": "INR", "category_code": "X", "source_updated_at": "2026-01-01T00:00:00Z"},
                    {"sku": "P0000999", "description": "P2", "selling_price": 20.0,
                     "currency": "INR", "category_code": "Y", "source_updated_at": "2026-01-01T00:00:00Z"},
                ],
                batch_index=1,
            )

        assert result.is_success  # Partial rejection is still a "success" at the batch level.
        assert result.accepted == ["P0000001"]
        assert len(result.rejected) == 1
        assert result.rejected[0]["reason"] == "Invalid warehouse product"

    def test_send_batch_api_failure(self):
        """When the API call fails after retries, result.error should be set."""
        settings = _make_settings()
        client = WarehouseAPIClient(settings)

        with patch.object(
            client._client,
            "post",
            side_effect=Exception("Connection refused"),
        ):
            result = client.send_batch(
                [{"sku": "P0000001", "description": "P1", "selling_price": 10.0,
                  "currency": "INR", "category_code": "X", "source_updated_at": "2026-01-01T00:00:00Z"}],
                batch_index=0,
            )

        assert not result.is_success
        assert "Connection refused" in result.error

    def test_batch_size_validation(self):
        """Sending more than BATCH_SIZE products should raise ValueError."""
        settings = _make_settings(BATCH_SIZE=2)
        client = WarehouseAPIClient(settings)

        products = [
            {"sku": f"P{i:07d}", "description": f"P{i}", "selling_price": 10.0,
             "currency": "INR", "category_code": "X", "source_updated_at": "2026-01-01T00:00:00Z"}
            for i in range(1, 5)  # 4 products, max is 2
        ]

        with pytest.raises(ValueError, match="exceeds WMS limit"):
            client.send_batch(products, batch_index=0)
