"""
Tests for the product data transformation.

Verifies:
- Field mapping from PIM format to WMS format.
- Price type conversion (string → float).
- All required WMS fields are present.
"""

from app.services.warehouse_sync import transform


class TestTransform:
    """Tests for the PIM → WMS data transformation."""

    def test_transform_basic(self):
        """Standard row should be correctly mapped."""
        row = {
            "id": "P0000001",
            "name": "Test Product",
            "category": "SPICES",
            "price": "249.50",
            "currency": "INR",
            "updated_at": "2026-09-25T08:30:00Z",
        }
        result = transform(row)
        assert result["sku"] == "P0000001"
        assert result["description"] == "Test Product"
        assert result["selling_price"] == 249.50
        assert isinstance(result["selling_price"], float)
        assert result["currency"] == "INR"
        assert result["category_code"] == "SPICES"
        assert result["source_updated_at"] == "2026-09-25T08:30:00Z"

    def test_transform_all_fields_present(self):
        """The WMS requires exactly these six fields."""
        row = {
            "id": "P0000002",
            "name": "Another Product",
            "category": "ELECTRONICS",
            "price": "1000.00",
            "currency": "INR",
            "updated_at": "2026-10-01T12:00:00Z",
        }
        result = transform(row)
        expected_keys = {"sku", "description", "selling_price", "currency",
                         "category_code", "source_updated_at"}
        assert set(result.keys()) == expected_keys

    def test_transform_price_integer(self):
        """Price without decimals should still become a float."""
        row = {
            "id": "P0000003",
            "name": "Cheap Product",
            "category": "GROCERY",
            "price": "100",
            "currency": "INR",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        result = transform(row)
        assert result["selling_price"] == 100.0
        assert isinstance(result["selling_price"], float)

    def test_transform_preserves_category_codes(self):
        """Category codes should be passed through verbatim."""
        for category in ["ELECTRONICS", "HOME", "SPICES", "GROCERY", "SPORTS"]:
            row = {
                "id": "P0000001",
                "name": "X",
                "category": category,
                "price": "1",
                "currency": "INR",
                "updated_at": "2026-01-01T00:00:00Z",
            }
            assert transform(row)["category_code"] == category
