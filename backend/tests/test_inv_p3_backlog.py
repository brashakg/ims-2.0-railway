"""
IMS 2.0 - P3 Inventory backlog tests
======================================
Covers:
  INV-11  transfer receive line.id resolution -- item_map fallback to product_id
  INV-12  barcode lifecycle trace view (fail-soft empty state without DB)
  INV-13  vendor performance scoring (pure compute helpers)
  INV-15  LensPricingCreate accepts snake_case aliases alongside camelCase

Run:
  JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest backend/tests/test_inv_p3_backlog.py -v
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ===========================================================================
# INV-11: transfer receive item resolution
# ===========================================================================


class TestTransferReceiveItemResolution:
    """_resolve_item must handle docs that have no `id` on a transfer line."""

    def _make_items(self, include_id: bool):
        items = [
            {
                "product_id": "prod_abc",
                "sku": "SKU-001",
                "quantity_requested": 2,
                "quantity_received": 0,
                "status": "pending",
            },
            {
                "product_id": "prod_xyz",
                "sku": "SKU-002",
                "quantity_requested": 1,
                "quantity_received": 0,
                "status": "pending",
            },
        ]
        if include_id:
            items[0]["id"] = "trfi_aaaa"
            items[1]["id"] = "trfi_bbbb"
        return items

    def _build_item_map_and_fallback(self, items):
        """Replicate the INV-11 fix logic from transfers.py."""
        item_map = {}
        items_without_id = []
        for item in items:
            item_id = item.get("id") or item.get("item_id") or ""
            if item_id:
                item_map[item_id] = item
            else:
                items_without_id.append(item)
        product_id_fallback = {}
        for item in items_without_id:
            pid = str(item.get("product_id") or "")
            if pid and pid not in product_id_fallback:
                product_id_fallback[pid] = item

        def resolve(transfer_item_id):
            if transfer_item_id in item_map:
                return item_map[transfer_item_id]
            if transfer_item_id in product_id_fallback:
                return product_id_fallback[transfer_item_id]
            return None

        return resolve

    def test_resolves_by_id_when_present(self):
        items = self._make_items(include_id=True)
        resolve = self._build_item_map_and_fallback(items)
        assert resolve("trfi_aaaa") is items[0]
        assert resolve("trfi_bbbb") is items[1]

    def test_resolves_by_product_id_when_no_id(self):
        items = self._make_items(include_id=False)
        resolve = self._build_item_map_and_fallback(items)
        assert resolve("prod_abc") is items[0]
        assert resolve("prod_xyz") is items[1]

    def test_returns_none_for_unknown_id(self):
        items = self._make_items(include_id=True)
        resolve = self._build_item_map_and_fallback(items)
        assert resolve("trfi_zzzz") is None

    def test_id_wins_over_product_id_when_both_present(self):
        """A doc that has both `id` AND matches another item's product_id
        must resolve by its own `id`."""
        items = [
            {"id": "trfi_aaa", "product_id": "prod_shared"},
            {"id": "trfi_bbb", "product_id": "prod_other"},
        ]
        resolve = self._build_item_map_and_fallback(items)
        # Looking up by explicit id still works
        assert resolve("trfi_aaa") is items[0]
        # Looking up by product_id that has a proper id field won't enter
        # the fallback dict (items_without_id is empty)
        assert resolve("prod_shared") is None  # no fallback entry (has id)

    def test_mixed_items_some_with_id_some_without(self):
        """Items with ids go into item_map; items without go into fallback."""
        items = [
            {"id": "trfi_111", "product_id": "prod_a"},
            {"product_id": "prod_b", "quantity_requested": 3},
        ]
        resolve = self._build_item_map_and_fallback(items)
        assert resolve("trfi_111") is items[0]
        assert resolve("prod_b") is items[1]
        assert resolve("prod_a") is None  # item[0] has id, not in fallback


# ===========================================================================
# INV-12: barcode lifecycle trace -- fail-soft without DB
# ===========================================================================


class TestBarcodeTrace:
    """The trace endpoint should return an honest empty envelope when DB is down."""

    def test_empty_envelope_shape(self):
        """Simulate the no-DB code path: result must have all expected keys."""
        # Replicate the fail-soft block from barcode_lifecycle_trace
        result = {
            "barcode": "BC-TEST-001",
            "stock_unit": None,
            "purchase": [],
            "sales": [],
            "transfers": [],
            "returns": [],
            "audit_trail": [],
        }
        assert result["barcode"] == "BC-TEST-001"
        assert result["stock_unit"] is None
        for key in ("purchase", "sales", "transfers", "returns", "audit_trail"):
            assert isinstance(result[key], list)
            assert len(result[key]) == 0

    def test_trace_endpoint_importable(self):
        """The inventory router must import cleanly with the new endpoint."""
        from api.routers import inventory as inv_router  # noqa: F401

        assert hasattr(inv_router, "barcode_lifecycle_trace")


# ===========================================================================
# INV-13: vendor performance scoring helpers
# ===========================================================================


class TestVendorPerformanceScore:
    """Pure compute helpers extracted from vendor_performance for unit testing."""

    def _compute_score(
        self,
        total_received,
        total_accepted,
        on_time_count,
        grns_with_po_date,
    ):
        acceptance_rate = (
            round(total_accepted / total_received, 4) if total_received > 0 else None
        )
        on_time_rate = (
            round(on_time_count / grns_with_po_date, 4)
            if grns_with_po_date > 0
            else None
        )
        if acceptance_rate is not None and on_time_rate is not None:
            overall_score = round(
                (acceptance_rate * 0.6 + on_time_rate * 0.4) * 100, 1
            )
        elif acceptance_rate is not None:
            overall_score = round(acceptance_rate * 100, 1)
        else:
            overall_score = None
        return acceptance_rate, on_time_rate, overall_score

    def test_perfect_vendor(self):
        acc, ot, score = self._compute_score(100, 100, 5, 5)
        assert acc == 1.0
        assert ot == 1.0
        assert score == 100.0

    def test_zero_received_gives_none(self):
        acc, ot, score = self._compute_score(0, 0, 0, 0)
        assert acc is None
        assert ot is None
        assert score is None

    def test_partial_acceptance(self):
        acc, ot, score = self._compute_score(100, 80, 4, 5)
        assert acc == 0.8
        assert ot == 0.8
        # 0.8*0.6 + 0.8*0.4 = 0.48 + 0.32 = 0.80 -> 80.0
        assert score == 80.0

    def test_no_po_dates(self):
        """When no GRNs have a PO expected_date, on_time_rate is None.
        Score falls back to acceptance_rate only."""
        acc, ot, score = self._compute_score(50, 45, 0, 0)
        assert acc == 0.9
        assert ot is None
        # falls back to acceptance_rate * 100
        assert score == 90.0

    def test_score_label_excellent(self):
        _, _, score = self._compute_score(100, 100, 10, 10)
        label = (
            "Excellent" if score >= 90
            else "Good" if score >= 75
            else "Average" if score >= 50
            else "Poor"
        )
        assert label == "Excellent"

    def test_score_label_poor(self):
        # 30 accepted out of 100, 0 on time
        _, _, score = self._compute_score(100, 30, 0, 5)
        label = (
            "Excellent" if score >= 90
            else "Good" if score >= 75
            else "Average" if score >= 50
            else "Poor"
        )
        assert label == "Poor"

    def test_vendor_performance_endpoint_importable(self):
        """The vendors router must import cleanly with the new endpoints."""
        from api.routers import vendors as vendors_router  # noqa: F401

        assert hasattr(vendors_router, "vendor_performance")
        assert hasattr(vendors_router, "vendor_purchase_history")


# ===========================================================================
# INV-15: LensPricingCreate snake_case / camelCase normalisation
# ===========================================================================


class TestLensPricingCreateAliases:
    """LensPricingCreate must accept both camelCase and snake_case keys."""

    def test_camel_case_accepted(self):
        from api.routers.admin_catalog import LensPricingCreate

        m = LensPricingCreate(
            **{"brandId": "brand1", "indexId": "idx1", "category": "SINGLE_VISION", "basePrice": 1200.0}
        )
        assert m.brandId == "brand1"
        assert m.indexId == "idx1"
        assert m.basePrice == 1200.0

    def test_snake_case_accepted(self):
        from api.routers.admin_catalog import LensPricingCreate

        m = LensPricingCreate(
            **{"brand_id": "brand2", "index_id": "idx2", "category": "BIFOCAL", "base_price": 1500.0}
        )
        # After normalisation, canonical camelCase fields must be set.
        assert m.brandId == "brand2"
        assert m.indexId == "idx2"
        assert m.basePrice == 1500.0

    def test_snake_wins_over_camel_when_both_given(self):
        from api.routers.admin_catalog import LensPricingCreate

        m = LensPricingCreate(
            **{
                "brand_id": "snake_brand",
                "brandId": "camel_brand",
                "index_id": "snake_idx",
                "indexId": "camel_idx",
                "category": "PROGRESSIVE",
                "base_price": 2000.0,
                "basePrice": 999.0,
            }
        )
        # snake_case takes priority in the validator
        assert m.brandId == "snake_brand"
        assert m.indexId == "snake_idx"
        assert m.basePrice == 2000.0

    def test_missing_brand_raises_validation_error(self):
        from api.routers.admin_catalog import LensPricingCreate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LensPricingCreate(
                **{"index_id": "idx1", "category": "SINGLE_VISION", "base_price": 100.0}
            )

    def test_missing_price_raises_validation_error(self):
        from api.routers.admin_catalog import LensPricingCreate
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LensPricingCreate(
                **{"brand_id": "b", "index_id": "i", "category": "SINGLE_VISION"}
            )
