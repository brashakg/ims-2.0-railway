"""
IMS 2.0 - GSTN portal export
============================
Two layers of coverage:

1. The pure shape-mapping functions in api/services/gstn_export.py
   (to_gstr1_json / to_gstr3b_json) — correct top-level keys, the
   "MMYYYY" period format, section assembly, and empty-data fail-soft.

2. Endpoint gating for the new portal-export routes, via the same
   bare-app + get_current_user-override pattern used by
   test_expenses_gating.py — SALES_STAFF gets 403, ADMIN / ACCOUNTANT
   (and SUPERADMIN) do not.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.gstn_export import (  # noqa: E402
    to_gstr1_json,
    to_gstr3b_json,
    _to_fp,
    _state_code,
)
from api.routers import reports, settings  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ============================================================================
# Sample IMS-shape report dicts (matching reports.py output)
# ============================================================================


def _sample_gstr1() -> dict:
    return {
        "period": "2026-04",
        "gstin": "07AABCB0001Q1ZZ",
        "legalName": "BV Delhi",
        "storeState": "Delhi",
        "totalInvoices": 3,
        "totalTaxableValue": 16000.0,
        "totalTax": 2880.0,
        "b2b": [
            {
                "invoiceNumber": "INV-001",
                "invoiceDate": "2026-04-15",
                "customerName": "ACME Ltd",
                "customerGSTIN": "07AAAAA0000A1ZZ",
                "customerState": "Delhi",
                "placeOfSupply": "Delhi",
                "invoiceValue": 11800.0,
                "taxableValue": 10000.0,
                "cgst": 900.0,
                "sgst": 900.0,
                "igst": 0.0,
                "totalTax": 1800.0,
                "hsnCode": "9004",
                "gstRate": 18,
            }
        ],
        "b2cl": [
            {
                "invoiceNumber": "INV-002",
                "invoiceDate": "2026-04-16",
                "customerName": "Big Spender",
                "customerState": "Maharashtra",
                "placeOfSupply": "Maharashtra",
                "invoiceValue": 295000.0,
                "taxableValue": 250000.0,
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 45000.0,
                "totalTax": 45000.0,
                "hsnCode": "9004",
                "gstRate": 18,
            }
        ],
        "b2cs": [
            {
                "placeOfSupply": "Delhi",
                "gstRate": 18,
                "taxableValue": 6000.0,
                "cgst": 540.0,
                "sgst": 540.0,
                "igst": 0.0,
                "totalTax": 1080.0,
            }
        ],
    }


def _sample_cdnr_entry() -> dict:
    """One credit note to a REGISTERED person, shaped exactly as
    reports.py::_compute_gstr1 emits in its `cdnr` list."""
    return {
        "refReference": "RET-260415-ABC123",
        "creditNoteDate": "2026-04-20",
        "customerId": "cust-1",
        "customerName": "ACME Ltd",
        "customerGSTIN": "07AAAAA0000A1ZZ",
        "customerState": "Delhi",
        "placeOfSupply": "Delhi",
        "grossValue": 1180.0,
        "taxableValue": 1000.0,
        "cgst": 90.0,
        "sgst": 90.0,
        "igst": 0.0,
        "taxValue": 180.0,
        "hsnCode": "9004",
        "gstRate": 18,
    }


def _sample_gstr3b() -> dict:
    return {
        "period": "2026-04",
        "gstin": "07AABCB0001Q1ZZ",
        "legalName": "BV Delhi",
        "storeState": "Delhi",
        "outwardTaxableValue": 16000.0,
        "outwardTaxableSupplies": {
            "integratedTax": 45000.0,
            "centralTax": 1440.0,
            "stateTax": 1440.0,
            "cess": 0.0,
        },
        "zeroRatedValue": 0.0,
        "zeroRatedSupplies": {
            "integratedTax": 0.0,
            "centralTax": 0.0,
            "stateTax": 0.0,
            "cess": 0.0,
        },
        "itcAvailable": {
            "integratedTax": 1000.0,
            "centralTax": 500.0,
            "stateTax": 500.0,
            "cess": 0.0,
        },
        "exemptSupplies": 0.0,
        "interest": {
            "integratedTax": 0.0,
            "centralTax": 0.0,
            "stateTax": 0.0,
            "cess": 0.0,
        },
    }


# ============================================================================
# Pure helpers
# ============================================================================


class TestPeriodAndStateHelpers:
    def test_to_fp_from_iso(self):
        assert _to_fp("2026-04") == "042026"

    def test_to_fp_from_iso_full_date(self):
        assert _to_fp("2026-12-31") == "122026"

    def test_to_fp_already_mmyyyy(self):
        assert _to_fp("042026") == "042026"

    def test_to_fp_bad_input_fails_soft(self):
        assert _to_fp("") == ""
        assert _to_fp("garbage") == ""
        assert _to_fp(None) == ""  # type: ignore[arg-type]

    def test_state_code_known_name(self):
        assert _state_code("Maharashtra") == "27"
        assert _state_code("delhi") == "07"
        assert _state_code("Jharkhand") == "20"

    def test_state_code_from_gstin_fallback(self):
        assert _state_code("Unknownland", "27ABCDE1234F1Z5") == "27"

    def test_state_code_unresolvable_is_empty(self):
        assert _state_code("Atlantis") == ""


# ============================================================================
# to_gstr1_json
# ============================================================================


class TestToGstr1Json:
    def test_top_level_keys(self):
        out = to_gstr1_json(_sample_gstr1())
        for key in ("gstin", "fp", "b2b", "b2cl", "b2cs", "cdnr", "hsn"):
            assert key in out

    def test_period_format_mmyyyy(self):
        out = to_gstr1_json(_sample_gstr1())
        assert out["fp"] == "042026"

    def test_gstin_passthrough_from_data(self):
        out = to_gstr1_json(_sample_gstr1())
        assert out["gstin"] == "07AABCB0001Q1ZZ"

    def test_explicit_gstin_and_period_override(self):
        out = to_gstr1_json(_sample_gstr1(), gstin="29XXXXX0000X1ZZ", period="2025-03")
        assert out["gstin"] == "29XXXXX0000X1ZZ"
        assert out["fp"] == "032025"

    def test_b2b_grouped_by_ctin(self):
        out = to_gstr1_json(_sample_gstr1())
        assert len(out["b2b"]) == 1
        grp = out["b2b"][0]
        assert grp["ctin"] == "07AAAAA0000A1ZZ"
        assert len(grp["inv"]) == 1
        inv = grp["inv"][0]
        assert inv["inum"] == "INV-001"
        # ISO date -> DD-MM-YYYY
        assert inv["idt"] == "15-04-2026"
        itm = inv["itms"][0]["itm_det"]
        assert itm["txval"] == 10000.0
        assert itm["camt"] == 900.0
        assert itm["samt"] == 900.0
        assert itm["rt"] == 18.0

    def test_b2b_without_gstin_is_skipped(self):
        data = _sample_gstr1()
        data["b2b"][0].pop("customerGSTIN")
        out = to_gstr1_json(data)
        # A B2B row without a counterparty GSTIN can't be represented —
        # it is dropped rather than emitted with an empty ctin.
        assert out["b2b"] == []

    def test_b2cl_grouped_by_pos_code(self):
        out = to_gstr1_json(_sample_gstr1())
        assert len(out["b2cl"]) == 1
        grp = out["b2cl"][0]
        assert grp["pos"] == "27"  # Maharashtra
        itm = grp["inv"][0]["itms"][0]["itm_det"]
        # B2CL is inter-state -> tax under IGST
        assert itm["iamt"] == 45000.0
        assert itm["camt"] == 0.0

    def test_b2cs_flat_rows(self):
        out = to_gstr1_json(_sample_gstr1())
        assert len(out["b2cs"]) == 1
        row = out["b2cs"][0]
        assert row["sply_ty"] == "INTRA"  # cgst/sgst present, igst 0
        assert row["pos"] == "07"
        assert row["typ"] == "OE"
        assert row["txval"] == 6000.0
        assert row["rt"] == 18.0

    def test_hsn_synthesised_from_totals_when_absent(self):
        out = to_gstr1_json(_sample_gstr1())
        assert "data" in out["hsn"]
        assert len(out["hsn"]["data"]) == 1
        assert out["hsn"]["data"][0]["txval"] == 16000.0

    def test_hsn_mapped_when_present(self):
        data = _sample_gstr1()
        data["hsn"] = [
            {
                "hsnCode": "9003",
                "description": "Frames",
                "quantity": 5,
                "taxableValue": 5000.0,
                "gstRate": 5,
                "cgst": 125.0,
                "sgst": 125.0,
            }
        ]
        out = to_gstr1_json(data)
        rows = out["hsn"]["data"]
        assert len(rows) == 1
        assert rows[0]["hsn_sc"] == "9003"
        assert rows[0]["qty"] == 5.0
        assert rows[0]["camt"] == 125.0

    def test_empty_data_fails_soft(self):
        out = to_gstr1_json({})
        assert out["gstin"] == ""
        assert out["fp"] == ""
        assert out["b2b"] == []
        assert out["b2cl"] == []
        assert out["b2cs"] == []
        # CDNR present but empty (never missing) when there are no credit notes.
        assert out["cdnr"] == []
        assert out["hsn"] == {"data": []}

    def test_none_data_fails_soft(self):
        out = to_gstr1_json(None)
        assert out["b2b"] == []
        assert out["cdnr"] == []
        assert out["hsn"] == {"data": []}

    def test_cdnr_empty_when_no_credit_notes(self):
        # The sample has no `cdnr` key at all -> emit [], not a missing key.
        out = to_gstr1_json(_sample_gstr1())
        assert out["cdnr"] == []


# ============================================================================
# to_gstr1_json -- CDNR (credit/debit notes to registered persons)
# ============================================================================


class TestToGstr1Cdnr:
    def _out_with_cdnr(self, *entries):
        data = _sample_gstr1()
        data["cdnr"] = list(entries) if entries else [_sample_cdnr_entry()]
        return to_gstr1_json(data)

    def test_cdnr_section_present_and_grouped_by_ctin(self):
        out = self._out_with_cdnr()
        assert len(out["cdnr"]) == 1
        grp = out["cdnr"][0]
        # Grouped by counterparty GSTIN.
        assert grp["ctin"] == "07AAAAA0000A1ZZ"
        assert len(grp["nt"]) == 1

    def test_cdnr_note_fields_and_credit_type(self):
        nt = self._out_with_cdnr()["cdnr"][0]["nt"][0]
        assert nt["ntty"] == "C"  # IMS returns/refunds are credit notes
        assert nt["nt_num"] == "RET-260415-ABC123"
        # ISO date -> DD-MM-YYYY portal format
        assert nt["nt_dt"] == "20-04-2026"
        # `val` is the GROSS note value, not the taxable value.
        assert nt["val"] == 1180.0
        assert nt["pos"] == "07"  # Delhi
        assert nt["rchrg"] == "N"
        assert nt["inv_typ"] == "R"

    def test_cdnr_item_tax_mapped(self):
        itm = self._out_with_cdnr()["cdnr"][0]["nt"][0]["itms"][0]["itm_det"]
        assert itm["txval"] == 1000.0
        assert itm["rt"] == 18.0
        assert itm["camt"] == 90.0
        assert itm["samt"] == 90.0
        assert itm["iamt"] == 0.0

    def test_cdnr_inter_state_uses_igst(self):
        entry = _sample_cdnr_entry()
        # Inter-state credit note: tax sits under IGST, pos differs from store.
        entry.update(
            {
                "customerGSTIN": "27BBBBB0000B1ZZ",
                "customerState": "Maharashtra",
                "placeOfSupply": "Maharashtra",
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 180.0,
            }
        )
        out = self._out_with_cdnr(entry)
        grp = out["cdnr"][0]
        assert grp["ctin"] == "27BBBBB0000B1ZZ"
        nt = grp["nt"][0]
        assert nt["pos"] == "27"
        itm = nt["itms"][0]["itm_det"]
        assert itm["iamt"] == 180.0
        assert itm["camt"] == 0.0

    def test_cdnr_debit_note_type_honoured(self):
        entry = _sample_cdnr_entry()
        entry["noteType"] = "D"
        nt = self._out_with_cdnr(entry)["cdnr"][0]["nt"][0]
        assert nt["ntty"] == "D"

    def test_cdnr_multiple_notes_same_ctin_grouped(self):
        e1 = _sample_cdnr_entry()
        e2 = _sample_cdnr_entry()
        e2["refReference"] = "RET-260418-XYZ789"
        out = self._out_with_cdnr(e1, e2)
        assert len(out["cdnr"]) == 1  # same ctin -> one group
        assert len(out["cdnr"][0]["nt"]) == 2

    def test_cdnr_without_gstin_skipped(self):
        # A credit note to an unregistered consumer belongs in CDNUR, not CDNR.
        entry = _sample_cdnr_entry()
        entry.pop("customerGSTIN")
        out = self._out_with_cdnr(entry)
        assert out["cdnr"] == []


# ============================================================================
# to_gstr3b_json
# ============================================================================


class TestToGstr3bJson:
    def test_top_level_keys(self):
        out = to_gstr3b_json(_sample_gstr3b())
        for key in ("gstin", "ret_period", "sup_details", "itc_elg", "inter_sup"):
            assert key in out

    def test_ret_period_format_mmyyyy(self):
        out = to_gstr3b_json(_sample_gstr3b())
        assert out["ret_period"] == "042026"

    def test_section_3_1_outward(self):
        out = to_gstr3b_json(_sample_gstr3b())
        osup = out["sup_details"]["osup_det"]
        assert osup["txval"] == 16000.0
        assert osup["iamt"] == 45000.0
        assert osup["camt"] == 1440.0
        assert osup["samt"] == 1440.0

    def test_table_4_itc(self):
        out = to_gstr3b_json(_sample_gstr3b())
        itc = out["itc_elg"]
        assert itc["itc_net"]["iamt"] == 1000.0
        assert itc["itc_net"]["camt"] == 500.0
        # itc_avl list carries the "all other ITC" row
        assert itc["itc_avl"][0]["ty"] == "OTH"
        assert itc["itc_avl"][0]["samt"] == 500.0

    def test_explicit_overrides(self):
        out = to_gstr3b_json(_sample_gstr3b(), gstin="33ZZZZZ0000Z1ZZ", period="2025-01")
        assert out["gstin"] == "33ZZZZZ0000Z1ZZ"
        assert out["ret_period"] == "012025"

    def test_empty_data_fails_soft(self):
        out = to_gstr3b_json({})
        assert out["gstin"] == ""
        assert out["ret_period"] == ""
        assert out["sup_details"]["osup_det"]["txval"] == 0.0
        assert out["itc_elg"]["itc_net"]["iamt"] == 0.0

    def test_none_data_fails_soft(self):
        out = to_gstr3b_json(None)
        assert out["sup_details"]["osup_det"]["iamt"] == 0.0


# ============================================================================
# Endpoint gating — bare app + get_current_user override
# ============================================================================


def _reports_client_as(roles):
    app = FastAPI()
    app.include_router(reports.router, prefix="/reports")

    async def _fake_user():
        return {
            "user_id": "u1",
            "username": "tester",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "store_ids": ["store-001"],
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


def _settings_client_as(roles):
    app = FastAPI()
    app.include_router(settings.router, prefix="/settings")

    async def _fake_user():
        return {
            "user_id": "u1",
            "username": "tester",
            "full_name": "Test User",
            "active_store_id": "store-001",
            "store_ids": ["store-001"],
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


GSTN_ENDPOINTS = [
    "/reports/gstr1/gstn-json?month=2026-04",
    "/reports/gstr3b/gstn-json?month=2026-04",
]


class TestGstnEndpointGating:
    @pytest.mark.parametrize("path", GSTN_ENDPOINTS)
    def test_sales_staff_blocked(self, path):
        resp = _reports_client_as(["SALES_STAFF"]).get(path)
        assert resp.status_code == 403

    @pytest.mark.parametrize("path", GSTN_ENDPOINTS)
    def test_cashier_blocked(self, path):
        resp = _reports_client_as(["CASHIER"]).get(path)
        assert resp.status_code == 403

    @pytest.mark.parametrize("path", GSTN_ENDPOINTS)
    def test_admin_allowed(self, path):
        resp = _reports_client_as(["ADMIN"]).get(path)
        assert resp.status_code != 403

    @pytest.mark.parametrize("path", GSTN_ENDPOINTS)
    def test_accountant_allowed(self, path):
        resp = _reports_client_as(["ACCOUNTANT"]).get(path)
        assert resp.status_code != 403

    @pytest.mark.parametrize("path", GSTN_ENDPOINTS)
    def test_superadmin_allowed(self, path):
        resp = _reports_client_as(["SUPERADMIN"]).get(path)
        assert resp.status_code != 403

    def test_gstr1_gstn_json_shape_no_db(self):
        """With no DB the endpoint still returns a valid GSTN skeleton."""
        resp = _reports_client_as(["ADMIN"]).get(
            "/reports/gstr1/gstn-json?month=2026-04"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["fp"] == "042026"
        assert "b2b" in body and "hsn" in body

    def test_gstr3b_gstn_json_shape_no_db(self):
        resp = _reports_client_as(["ADMIN"]).get(
            "/reports/gstr3b/gstn-json?month=2026-04"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ret_period"] == "042026"
        assert "sup_details" in body

    def test_gstr1_numeric_month_plus_year(self):
        """month=4&year=2026 normalises to fp 042026."""
        resp = _reports_client_as(["ADMIN"]).get(
            "/reports/gstr1/gstn-json?month=4&year=2026"
        )
        assert resp.status_code == 200
        assert resp.json()["fp"] == "042026"


# ============================================================================
# Marketplace channels — gating + stub behaviour
# ============================================================================


class TestMarketplaceChannels:
    def test_get_open_to_authenticated(self):
        resp = _settings_client_as(["SALES_STAFF"]).get("/settings/marketplace-channels")
        assert resp.status_code == 200
        body = resp.json()
        assert "channels" in body
        assert "amazon" in body["channels"]
        assert "flipkart" in body["channels"]
        # Baseline is all-disabled.
        assert body["channels"]["amazon"]["enabled"] is False

    def test_put_blocked_for_sales_staff(self):
        resp = _settings_client_as(["SALES_STAFF"]).put(
            "/settings/marketplace-channels",
            json={"channels": {"amazon": {"enabled": True, "seller_id": "A1"}}},
        )
        assert resp.status_code == 403

    def test_put_allowed_for_admin(self):
        resp = _settings_client_as(["ADMIN"]).put(
            "/settings/marketplace-channels",
            json={"channels": {"amazon": {"enabled": True, "seller_id": "A1"}}},
        )
        assert resp.status_code != 403

    def test_sync_unconfigured_returns_simulated(self):
        resp = _settings_client_as(["ADMIN"]).post(
            "/settings/marketplace-channels/amazon/sync"
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "SIMULATED"

    def test_sync_unknown_channel_404(self):
        resp = _settings_client_as(["ADMIN"]).post(
            "/settings/marketplace-channels/ebay/sync"
        )
        assert resp.status_code == 404

    def test_sync_blocked_for_sales_staff(self):
        resp = _settings_client_as(["SALES_STAFF"]).post(
            "/settings/marketplace-channels/amazon/sync"
        )
        assert resp.status_code == 403
