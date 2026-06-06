"""Unit tests for the category split normalization backfill script."""
import sys
from unittest.mock import MagicMock, patch
from datetime import datetime
import pytest

# Import the script module (adjust import path as needed)
sys.path.insert(0, "backend")
from scripts.normalize_category_split import ALIAS_MAPPING, CANONICAL_CATEGORIES


class TestAliasMappingBuild:
    """Verify that ALIAS_MAPPING is built correctly from GST_CATEGORY_TABLE."""

    def test_alias_mapping_exists(self):
        """ALIAS_MAPPING should not be empty."""
        assert len(ALIAS_MAPPING) > 0, "ALIAS_MAPPING should have entries"

    def test_frames_maps_to_frame(self):
        """FRAMES alias should map to FRAME."""
        assert ALIAS_MAPPING.get("FRAMES") == "FRAME"

    def test_contact_lenses_maps_to_contact_lens(self):
        """CONTACT_LENSES alias should map to CONTACT_LENS."""
        assert ALIAS_MAPPING.get("CONTACT_LENSES") == "CONTACT_LENS"

    def test_rx_lenses_maps_to_optical_lens(self):
        """RX_LENSES alias should map to OPTICAL_LENS."""
        assert ALIAS_MAPPING.get("RX_LENSES") == "OPTICAL_LENS"

    def test_wrist_watches_maps_to_watch(self):
        """WRIST_WATCHES alias should map to WATCH."""
        assert ALIAS_MAPPING.get("WRIST_WATCHES") == "WATCH"

    def test_smartwatches_maps_to_smartwatch(self):
        """SMARTWATCHES alias should map to SMARTWATCH."""
        assert ALIAS_MAPPING.get("SMARTWATCHES") == "SMARTWATCH"

    def test_wall_clocks_maps_to_wall_clock(self):
        """WALL_CLOCKS alias should map to WALL_CLOCK."""
        assert ALIAS_MAPPING.get("WALL_CLOCKS") == "WALL_CLOCK"

    def test_colour_contacts_maps_to_colored_contact_lens(self):
        """COLOUR_CONTACTS alias should map to COLORED_CONTACT_LENS."""
        assert ALIAS_MAPPING.get("COLOUR_CONTACTS") == "COLORED_CONTACT_LENS"

    def test_all_mappings_are_to_canonical(self):
        """All mapped-to values should be in CANONICAL_CATEGORIES."""
        for alias, canonical in ALIAS_MAPPING.items():
            assert canonical in CANONICAL_CATEGORIES, \
                f"Mapping {alias} -> {canonical}, but {canonical} is not canonical"

    def test_no_canonical_forms_in_alias_mapping(self):
        """No key in ALIAS_MAPPING should be a canonical form."""
        for alias in ALIAS_MAPPING.keys():
            assert alias not in CANONICAL_CATEGORIES, \
                f"Alias key {alias} is also a canonical form"


class TestDryRunVsApply:
    """Test that DRY-RUN mode doesn't write and APPLY mode does."""

    @patch('scripts.normalize_category_split._connect')
    def test_dry_run_returns_zero_on_no_matches(self, mock_connect):
        """DRY-RUN with no matching products should return 0."""
        from scripts.normalize_category_split import run

        mock_db = MagicMock()
        mock_db.is_connected = True
        mock_db.get_collection.return_value.find.return_value = []
        mock_connect.return_value = mock_db

        # DRY-RUN mode
        result = run(apply=False)
        assert result == 0

    @patch('scripts.normalize_category_split._connect')
    def test_apply_returns_zero_on_no_matches(self, mock_connect):
        """APPLY with no matching products should return 0."""
        from scripts.normalize_category_split import run

        mock_db = MagicMock()
        mock_db.is_connected = True
        mock_db.get_collection.return_value.find.return_value = []
        mock_connect.return_value = mock_db

        # APPLY mode
        result = run(apply=True)
        assert result == 0

    @patch('scripts.normalize_category_split._connect')
    def test_dry_run_does_not_call_update(self, mock_connect):
        """DRY-RUN should not call update_one."""
        from scripts.normalize_category_split import run

        mock_db = MagicMock()
        mock_db.is_connected = True
        mock_products = MagicMock()
        mock_db.get_collection.side_effect = lambda x: mock_products
        mock_products.find.return_value = [
            {
                "_id": "test_id",
                "product_id": "p1",
                "sku": "SKU1",
                "category": "FRAMES",
                "brand": "Test",
                "model": "Model",
            }
        ]
        mock_connect.return_value = mock_db

        # DRY-RUN mode
        result = run(apply=False)
        assert result == 0
        # update_one should NOT have been called
        mock_products.update_one.assert_not_called()

    @patch('scripts.normalize_category_split._connect')
    def test_apply_calls_update(self, mock_connect):
        """APPLY should call update_one."""
        from scripts.normalize_category_split import run

        mock_db = MagicMock()
        mock_db.is_connected = True
        mock_products = MagicMock()
        mock_audit = MagicMock()
        mock_db.get_collection.side_effect = lambda x: mock_products if x == "products" else mock_audit
        mock_products.find.return_value = [
            {
                "_id": "test_id",
                "product_id": "p1",
                "sku": "SKU1",
                "category": "FRAMES",
                "brand": "Test",
                "model": "Model",
            }
        ]
        mock_result = MagicMock()
        mock_result.modified_count = 1
        mock_products.update_one.return_value = mock_result
        mock_connect.return_value = mock_db

        # APPLY mode
        result = run(apply=True)
        assert result == 0
        # update_one should have been called
        mock_products.update_one.assert_called_once()
        # Verify the call: should normalize FRAMES -> FRAME
        call_args = mock_products.update_one.call_args
        assert call_args[0][1]["$set"]["category"] == "FRAME"


class TestIdempotency:
    """Test that the script is idempotent."""

    @patch('scripts.normalize_category_split._connect')
    def test_second_run_finds_zero_products(self, mock_connect):
        """Running APPLY twice should find 0 products on second run."""
        from scripts.normalize_category_split import run

        mock_db = MagicMock()
        mock_db.is_connected = True
        mock_products = MagicMock()
        mock_audit = MagicMock()
        mock_db.get_collection.side_effect = lambda x: mock_products if x == "products" else mock_audit

        # First run: find FRAMES product
        mock_products.find.return_value = [
            {
                "_id": "test_id",
                "product_id": "p1",
                "sku": "SKU1",
                "category": "FRAMES",
                "brand": "Test",
                "model": "Model",
            }
        ]
        mock_result = MagicMock()
        mock_result.modified_count = 1
        mock_products.update_one.return_value = mock_result
        mock_connect.return_value = mock_db

        result = run(apply=True)
        assert result == 0

        # Second run: should find no products (they're all FRAME now)
        mock_products.find.return_value = []
        result = run(apply=True)
        assert result == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
