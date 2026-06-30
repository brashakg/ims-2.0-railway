"""
IMS 2.0 — TechCherry migration endpoint tests
==============================================
Locks the contract for `POST /api/v1/admin/techcherry/import`:

  1. SUPERADMIN-only (everyone else gets 404, hiding endpoint).
  2. Mapper helpers: phone normalisation, float parsing, date parsing.
  3. Product mapper handles barcode='NA' as missing, splits Prod Grp
     "RAYBAN FRAME" into brand="RAYBAN", category="FRAME".
  4. Empty/malformed rows are skipped, not 500'd.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ----- helper functions ---------------------------------------------------


class TestHelpers:
    def test_normalise_phone_strips_country_code(self):
        from api.routers.techcherry_import import _normalise_phone
        assert _normalise_phone("+91 98765 43210") == "9876543210"
        assert _normalise_phone("919876543210") == "9876543210"
        assert _normalise_phone("09876543210") == "9876543210"
        assert _normalise_phone("98765-43210") == "9876543210"
        assert _normalise_phone("9876543210") == "9876543210"

    def test_normalise_phone_handles_missing(self):
        from api.routers.techcherry_import import _normalise_phone
        assert _normalise_phone(None) == ""
        assert _normalise_phone("") == ""
        assert _normalise_phone("--") == ""

    def test_safe_float_handles_commas(self):
        from api.routers.techcherry_import import _safe_float
        assert _safe_float("1,250.50") == 1250.5
        assert _safe_float("0") == 0.0
        assert _safe_float(None) == 0.0
        assert _safe_float("garbage") == 0.0
        assert _safe_float(42) == 42.0

    def test_parse_date_multiple_formats(self):
        from api.routers.techcherry_import import _parse_date
        # TechCherry exports usually DD-MM-YYYY
        d = _parse_date("21-05-2026")
        assert d is not None and d.day == 21 and d.month == 5 and d.year == 2026
        # ISO also works
        d2 = _parse_date("2026-05-21")
        assert d2 is not None and d2.day == 21 and d2.month == 5
        # Garbage returns None, never raises
        assert _parse_date("not a date") is None
        assert _parse_date(None) is None


# ----- mapper outputs -----------------------------------------------------


class TestMappers:
    def test_product_mapper_splits_brand_category_from_prod_grp(self):
        from api.routers.techcherry_import import _map_product
        row = {
            "Prod Name": "-RAYBAN-RB6266-2509-53-RIMLESS",
            "Prod Grp": "RAYBAN FRAME",
            "Barcode": "2510647",
            "Sale Prc": "4790.00",
            "Pur Prc": "2500.00",
            "Stock In_Hand": "1.00",
            "HSN": "90031100",
            "Unit": "PCS-PIECES",
        }
        doc = _map_product(row, "BV-PUN-01", "techcherry")
        assert doc is not None
        assert doc["brand"] == "RAYBAN"
        assert doc["category"] == "FRAME"
        assert doc["barcode"] == "2510647"
        assert doc["mrp"] == 4790.0
        assert doc["cost_price"] == 2500.0
        assert doc["stock_quantity"] == 1
        assert doc["store_id"] == "BV-PUN-01"
        assert doc["source"] == "techcherry"
        assert "techcherry_imported_at" in doc

    def test_product_mapper_treats_barcode_NA_as_empty(self):
        from api.routers.techcherry_import import _map_product
        row = {"Prod Name": "Generic frame", "Barcode": "NA", "Sale Prc": "500"}
        doc = _map_product(row, "BV-PUN-01", "techcherry")
        assert doc is not None
        assert doc["barcode"] == ""
        # sku falls back to name when no barcode
        assert doc["sku"] == "Generic frame"

    def test_product_mapper_skips_when_no_name_or_barcode(self):
        from api.routers.techcherry_import import _map_product
        assert _map_product({}, "BV-PUN-01", "techcherry") is None
        assert _map_product({"Sale Prc": "100"}, "BV-PUN-01", "techcherry") is None

    def test_customer_mapper_normalises_phone(self):
        from api.routers.techcherry_import import _map_customer
        row = {"Name": "Rakesh Kumar", "Mobile": "+91 98765-43210"}
        doc = _map_customer(row, "BV-PUN-01", "techcherry")
        assert doc is not None
        assert doc["phone"] == "9876543210"
        assert doc["name"] == "Rakesh Kumar"
        assert doc["preferred_store_id"] == "BV-PUN-01"

    def test_customer_mapper_skips_blank_rows(self):
        from api.routers.techcherry_import import _map_customer
        assert _map_customer({}, "BV-PUN-01", "techcherry") is None
        assert _map_customer({"name": "", "phone": ""}, "BV-PUN-01", "techcherry") is None

    def test_order_mapper_uses_invoice_no(self):
        from api.routers.techcherry_import import _map_order
        row = {
            "InvoiceNo": "INV-2026-001",
            "Date": "15-05-2026",
            "CustomerName": "Anita",
            "Mobile": "9876543210",
            "GrandTotal": "5,250.00",
            "TaxAmount": "250.00",
        }
        doc = _map_order(row, "BV-PUN-01", "techcherry")
        assert doc is not None
        assert doc["order_number"] == "INV-2026-001"
        assert doc["grand_total"] == 5250.0
        assert doc["tax_amount"] == 250.0
        assert doc["status"] == "DELIVERED"
        assert doc["customer_phone"] == "9876543210"

    def test_order_mapper_skips_no_invoice(self):
        from api.routers.techcherry_import import _map_order
        assert _map_order({"GrandTotal": "1000"}, "BV-PUN-01", "techcherry") is None


# ----- legacy import: POWER-VALIDATE ONLY (never blocks / never skips) -----


class TestPowerQuality:
    """A historical row with out-of-range Rx powers is imported anyway; the
    importer only RECORDS a data-quality note. It must NEVER reject/skip the row.
    Reuses the canonical clinical validators (no duplicated ranges)."""

    def test_in_range_powers_no_issue(self):
        from api.routers.techcherry_import import _power_quality_issues
        row = {
            "InvoiceNo": "INV-1",
            "items": [{"sph": "-2.25", "cyl": "-1.00", "axis": 90}],
        }
        assert _power_quality_issues(row) == []

    def test_out_of_range_sph_recorded(self):
        from api.routers.techcherry_import import _power_quality_issues
        # +99.00 is far outside -20..+20
        issues = _power_quality_issues({"items": [{"sph": "99.00"}]})
        assert issues
        assert any("99" in s for s in issues)

    def test_off_grid_power_recorded(self):
        from api.routers.techcherry_import import _power_quality_issues
        # +1.30 is not on the 0.25 grid
        issues = _power_quality_issues({"items": [{"sph": "1.30"}]})
        assert issues

    def test_cyl_without_axis_recorded(self):
        from api.routers.techcherry_import import _power_quality_issues
        # non-zero cyl requires an axis
        issues = _power_quality_issues({"items": [{"cyl": "-1.00"}]})
        assert issues

    def test_row_level_powers_checked(self):
        from api.routers.techcherry_import import _power_quality_issues
        # legacy export that flattens the Rx onto the order row (no items[])
        issues = _power_quality_issues({"sph": "50.00"})
        assert issues

    def test_no_powers_no_issue(self):
        from api.routers.techcherry_import import _power_quality_issues
        # a plain historical sale row with no Rx fields -> nothing to validate
        assert _power_quality_issues({"InvoiceNo": "INV-2", "GrandTotal": "999"}) == []

    def test_import_records_note_but_imports_row(self, client, auth_headers, monkeypatch):
        """End-to-end via the endpoint: an out-of-range-power order row is still
        imported (inserted) and surfaces in data_quality_notes + the counter."""
        import api.routers.techcherry_import as tc

        inserted = []

        class _Col:
            def find_one(self, *a, **k):
                return None

            def insert_one(self, doc):
                inserted.append(doc)

            def update_one(self, *a, **k):
                pass

        class _DB:
            def get_collection(self, name):
                return _Col()

        monkeypatch.setattr(tc, "_get_db", lambda: _DB())

        r = client.post(
            "/api/v1/admin/techcherry/import",
            json={
                "type": "orders",
                "store_id": "BV-PUN-01",
                "rows": [
                    {"InvoiceNo": "INV-OK", "GrandTotal": "999",
                     "items": [{"sph": "-2.00"}]},
                    {"InvoiceNo": "INV-BAD", "GrandTotal": "1500",
                     "items": [{"sph": "99.00"}]},
                ],
            },
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # BOTH rows imported -- the bad-power row is NOT skipped.
        assert body["inserted"] == 2
        assert body["skipped"] == 0
        assert body["out_of_range_power_rows"] == 1
        assert any("INV-BAD" in n for n in body["data_quality_notes"])
        assert len(inserted) == 2


# ----- endpoint auth ------------------------------------------------------


class TestEndpointAuth:
    def test_import_requires_superadmin(self, client):
        # No auth → 401 or 403 from auth dependency
        r = client.post("/api/v1/admin/techcherry/import", json={
            "type": "products", "store_id": "BV-PUN-01", "rows": [],
        })
        assert r.status_code in (401, 403, 404)

    def test_status_requires_superadmin(self, client):
        r = client.get("/api/v1/admin/techcherry/status")
        assert r.status_code in (401, 403, 404)

    def test_invalid_type_rejected(self, client, auth_headers):
        r = client.post(
            "/api/v1/admin/techcherry/import",
            json={"type": "shenanigans", "store_id": "BV-PUN-01", "rows": []},
            headers=auth_headers,
        )
        assert r.status_code == 422  # Pydantic literal validation
