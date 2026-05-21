"""
IMS 2.0 — serialized-inventory tests
====================================
Covers the pure helpers behind GET/POST/PATCH /api/v1/inventory/serials,
which feed SerialNumberTracker.tsx (it used to render a hardcoded mock list
- Phonak Audeo P90-R "sold to Mr. Rajesh Kumar", Apple Watch, etc. - removed
in #146 and now backed by the serial_numbers collection).

`_compute_warranty_status` and `_serial_to_frontend` are pure (no DB), so
the warranty derivation and the snake->camel + product enrichment mapping
are tested directly.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.inventory import (  # noqa: E402
    _compute_warranty_status,
    _serial_to_frontend,
    _SERIAL_STATUSES,
)

NOW = datetime(2026, 5, 21, 12, 0, 0)


# --------------------------------------------------------------------------
# Warranty status derivation
# --------------------------------------------------------------------------


class TestWarrantyStatus:
    def test_future_expiry_is_active(self):
        assert _compute_warranty_status("2027-01-01", NOW) == "ACTIVE"

    def test_past_expiry_is_expired(self):
        assert _compute_warranty_status("2025-01-01", NOW) == "EXPIRED"

    def test_missing_expiry_is_none(self):
        assert _compute_warranty_status(None, NOW) == "NONE"
        assert _compute_warranty_status("", NOW) == "NONE"

    def test_garbage_expiry_is_none(self):
        assert _compute_warranty_status("not-a-date", NOW) == "NONE"

    def test_tz_aware_expiry_handled(self):
        # trailing Z (UTC) must not crash the naive comparison
        assert _compute_warranty_status("2027-01-01T00:00:00Z", NOW) == "ACTIVE"
        assert _compute_warranty_status("2025-01-01T00:00:00Z", NOW) == "EXPIRED"


# --------------------------------------------------------------------------
# Serial doc -> frontend SerializedItem mapping
# --------------------------------------------------------------------------


class TestSerialToFrontend:
    def _doc(self, **overrides) -> dict:
        base = {
            "serial_id": "ser-001",
            "serial_number": "SN-ABC-123",
            "product_id": "BC-9001",
            "store_id": "BV-PUN-01",
            "status": "IN_STOCK",
            "location_code": "A1-05",
            "purchase_date": "2026-01-10",
            "warranty_months": 24,
            "warranty_expiry_date": "2028-01-10",
            "supplier_batch": "BATCH-2026-001",
            "notes": "demo unit",
            "sold_to": None,
            "sold_date": None,
        }
        base.update(overrides)
        return base

    def test_maps_snake_to_camel(self):
        item = _serial_to_frontend(self._doc(), None, NOW)
        assert item["id"] == "ser-001"
        assert item["serialNumber"] == "SN-ABC-123"
        assert item["productId"] == "BC-9001"
        assert item["locationCode"] == "A1-05"
        assert item["warrantyMonths"] == 24
        assert item["warrantyExpiryDate"] == "2028-01-10"
        assert item["supplierBatch"] == "BATCH-2026-001"
        # no _id leaks through
        assert "_id" not in item

    def test_enriches_with_product(self):
        product = {
            "name": "Phonak Audeo P90-R",
            "sku": "BC-9001",
            "barcode": "BC-9001",
            "brand": "Phonak",
            "category": "HEARING_AID",
        }
        item = _serial_to_frontend(self._doc(), product, NOW)
        assert item["productName"] == "Phonak Audeo P90-R"
        assert item["productBrand"] == "Phonak"
        assert item["productCategory"] == "HEARING_AID"
        assert item["productSku"] == "BC-9001"

    def test_missing_product_degrades_gracefully(self):
        item = _serial_to_frontend(self._doc(), None, NOW)
        assert item["productName"] == ""
        assert item["productBrand"] == ""
        assert item["productSku"] == ""

    def test_warranty_status_computed_into_item(self):
        active = _serial_to_frontend(
            self._doc(warranty_expiry_date="2028-01-10"), None, NOW
        )
        expired = _serial_to_frontend(
            self._doc(warranty_expiry_date="2024-01-10"), None, NOW
        )
        none = _serial_to_frontend(
            self._doc(warranty_expiry_date=None), None, NOW
        )
        assert active["warrantyStatus"] == "ACTIVE"
        assert expired["warrantyStatus"] == "EXPIRED"
        assert none["warrantyStatus"] == "NONE"

    def test_sold_unit_surfaces_customer(self):
        item = _serial_to_frontend(
            self._doc(status="SOLD", sold_to="Anita Desai", sold_date="2026-03-01"),
            None,
            NOW,
        )
        assert item["status"] == "SOLD"
        assert item["soldTo"] == "Anita Desai"
        assert item["soldToCustomer"] == "Anita Desai"
        assert item["soldDate"] == "2026-03-01"


# --------------------------------------------------------------------------
# Status vocabulary guard
# --------------------------------------------------------------------------


class TestStatusVocabulary:
    def test_expected_statuses(self):
        assert _SERIAL_STATUSES == {
            "IN_STOCK",
            "SOLD",
            "WARRANTY_CLAIM",
            "DAMAGED",
            "LOST_STOLEN",
        }
