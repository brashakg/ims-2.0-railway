"""
IMS 2.0 - Settings validation hardening tests
===============================================
Covers all bugs fixed in the settings-hardening pass:

BUG 1 - discount_rules: update + set_discount_rule never persisted to DB.
BUG 2 - system_settings: PUT never persisted to DB.
BUG 3 - TaxSettings: company_gstin not validated, rates had no bounds.
BUG 4 - InvoiceSettings: invoice_start_number etc. had no range checks.
BUG 5 - feature_toggles: PUT/PATCH populated the cache but never cleared it
         so subsequent GETs returned stale data for up to TTL_LONG (15 min).
BUG 6 - DiscountSettings.max_discount had no bounds (could be negative).
BUG 7 - PT slab upsert accepted arbitrary state_code strings.
BUG 8 - PtSlabUpsert.basis had no enum constraint.
BUG 9 - PrinterSettings.receipt_printer_width accepted arbitrary int.
BUG 10 - PasswordChange.new_password had no max_length (bcrypt truncates at 72).
BUG 11 - Entity: registered_phone / registered_email not validated.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

# ---------------------------------------------------------------------------
# Helpers shared by unit tests that work against the Pydantic models directly
# (no HTTP layer needed for schema-level validation checks).
# ---------------------------------------------------------------------------

from pydantic import ValidationError


def _no_db(put_resp) -> bool:
    """True when the PUT ran without a DB (so round-trip GET can't be checked)."""
    try:
        return "(no DB)" in (put_resp.json().get("message", "") or "")
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# BUG 3 - TaxSettings GSTIN + rate validation (Pydantic layer)
# ---------------------------------------------------------------------------


class TestTaxSettingsValidation:
    def _import(self):
        from api.routers.settings import TaxSettings

        return TaxSettings

    def test_valid_gstin_accepted(self):
        TaxSettings = self._import()
        ts = TaxSettings(company_gstin="29ABCDE1234F1Z5")
        assert ts.company_gstin == "29ABCDE1234F1Z5"

    def test_empty_gstin_accepted(self):
        TaxSettings = self._import()
        ts = TaxSettings(company_gstin="")
        assert ts.company_gstin == ""

    def test_invalid_gstin_rejected(self):
        TaxSettings = self._import()
        with pytest.raises(ValidationError) as exc_info:
            TaxSettings(company_gstin="INVALID")
        assert "company_gstin" in str(exc_info.value)

    def test_gstin_too_short_rejected(self):
        TaxSettings = self._import()
        with pytest.raises(ValidationError):
            TaxSettings(company_gstin="29ABCDE1234")

    def test_gstin_normalised_to_upper(self):
        TaxSettings = self._import()
        # lowercase input -> normalised to upper
        ts = TaxSettings(company_gstin="29abcde1234f1z5")
        assert ts.company_gstin == ts.company_gstin.upper()

    def test_negative_gst_rate_rejected(self):
        TaxSettings = self._import()
        with pytest.raises(ValidationError):
            TaxSettings(default_gst_rate=-1.0)

    def test_over_100_gst_rate_rejected(self):
        TaxSettings = self._import()
        with pytest.raises(ValidationError):
            TaxSettings(default_gst_rate=101.0)

    def test_valid_gst_rates_accepted(self):
        TaxSettings = self._import()
        for r in (0.0, 5.0, 12.0, 18.0, 28.0, 100.0):
            ts = TaxSettings(default_gst_rate=r)
            assert ts.default_gst_rate == r

    def test_negative_tds_rate_rejected(self):
        TaxSettings = self._import()
        with pytest.raises(ValidationError):
            TaxSettings(tds_rate=-0.1)

    def test_over_100_tds_rate_rejected(self):
        TaxSettings = self._import()
        with pytest.raises(ValidationError):
            TaxSettings(tds_rate=100.1)

    def test_negative_eway_threshold_rejected(self):
        TaxSettings = self._import()
        with pytest.raises(ValidationError):
            TaxSettings(e_way_bill_threshold=-1.0)


# ---------------------------------------------------------------------------
# BUG 4 - InvoiceSettings range validation
# ---------------------------------------------------------------------------


class TestInvoiceSettingsValidation:
    def _import(self):
        from api.routers.settings import InvoiceSettings

        return InvoiceSettings

    def test_zero_start_number_rejected(self):
        InvoiceSettings = self._import()
        with pytest.raises(ValidationError):
            InvoiceSettings(invoice_start_number=0)

    def test_negative_start_number_rejected(self):
        InvoiceSettings = self._import()
        with pytest.raises(ValidationError):
            InvoiceSettings(invoice_start_number=-5)

    def test_valid_start_number_accepted(self):
        InvoiceSettings = self._import()
        inv = InvoiceSettings(invoice_start_number=1000)
        assert inv.invoice_start_number == 1000

    def test_negative_warranty_days_rejected(self):
        InvoiceSettings = self._import()
        with pytest.raises(ValidationError):
            InvoiceSettings(default_warranty_days=-1)

    def test_excessive_warranty_days_rejected(self):
        InvoiceSettings = self._import()
        with pytest.raises(ValidationError):
            InvoiceSettings(default_warranty_days=99999)

    def test_valid_warranty_days_accepted(self):
        InvoiceSettings = self._import()
        inv = InvoiceSettings(default_warranty_days=365)
        assert inv.default_warranty_days == 365

    def test_empty_prefix_rejected(self):
        InvoiceSettings = self._import()
        with pytest.raises(ValidationError):
            InvoiceSettings(invoice_prefix="")

    def test_long_prefix_rejected(self):
        InvoiceSettings = self._import()
        with pytest.raises(ValidationError):
            InvoiceSettings(invoice_prefix="AVERYLONGPREFIX123")

    def test_prefix_normalised_to_upper(self):
        InvoiceSettings = self._import()
        inv = InvoiceSettings(invoice_prefix="inv")
        assert inv.invoice_prefix == "INV"

    def test_valid_financial_year_accepted(self):
        InvoiceSettings = self._import()
        inv = InvoiceSettings(financial_year="2025-26")
        assert inv.financial_year == "2025-26"

    def test_invalid_financial_year_rejected(self):
        InvoiceSettings = self._import()
        with pytest.raises(ValidationError):
            InvoiceSettings(financial_year="FY2025")


# ---------------------------------------------------------------------------
# BUG 6 - DiscountSettings range validation
# ---------------------------------------------------------------------------


class TestDiscountSettingsValidation:
    def _import(self):
        from api.routers.settings import DiscountSettings

        return DiscountSettings

    def test_negative_max_discount_rejected(self):
        DiscountSettings = self._import()
        with pytest.raises(ValidationError):
            DiscountSettings(role="SALES_STAFF", category="MASS", max_discount=-1.0)

    def test_over_100_max_discount_rejected(self):
        DiscountSettings = self._import()
        with pytest.raises(ValidationError):
            DiscountSettings(
                role="SALES_STAFF", category="MASS", max_discount=100.5
            )

    def test_zero_max_discount_accepted(self):
        DiscountSettings = self._import()
        ds = DiscountSettings(
            role="SALES_STAFF", category="NON_DISCOUNTABLE", max_discount=0.0
        )
        assert ds.max_discount == 0.0

    def test_100_max_discount_accepted(self):
        DiscountSettings = self._import()
        ds = DiscountSettings(role="SUPERADMIN", category="MASS", max_discount=100.0)
        assert ds.max_discount == 100.0


# ---------------------------------------------------------------------------
# BUG 9 - PrinterSettings width + label_size validation
# ---------------------------------------------------------------------------


class TestPrinterSettingsValidation:
    def _import(self):
        from api.routers.settings import PrinterSettings

        return PrinterSettings

    def test_invalid_receipt_width_rejected(self):
        PrinterSettings = self._import()
        with pytest.raises(ValidationError):
            PrinterSettings(receipt_printer_width=100)

    def test_valid_receipt_widths_accepted(self):
        PrinterSettings = self._import()
        for w in (58, 80):
            ps = PrinterSettings(receipt_printer_width=w)
            assert ps.receipt_printer_width == w

    def test_invalid_label_size_rejected(self):
        PrinterSettings = self._import()
        with pytest.raises(ValidationError):
            PrinterSettings(label_size="99x99")

    def test_valid_label_sizes_accepted(self):
        PrinterSettings = self._import()
        for sz in ("50x25", "50x30", "100x50"):
            ps = PrinterSettings(label_size=sz)
            assert ps.label_size == sz

    def test_zero_copies_rejected(self):
        PrinterSettings = self._import()
        with pytest.raises(ValidationError):
            PrinterSettings(copies_per_print=0)

    def test_negative_copies_rejected(self):
        PrinterSettings = self._import()
        with pytest.raises(ValidationError):
            PrinterSettings(copies_per_print=-1)

    def test_over_10_copies_rejected(self):
        PrinterSettings = self._import()
        with pytest.raises(ValidationError):
            PrinterSettings(copies_per_print=11)


# ---------------------------------------------------------------------------
# BUG 10 - PasswordChange max_length
# ---------------------------------------------------------------------------


class TestPasswordChangeValidation:
    def _import(self):
        from api.routers.settings import PasswordChange

        return PasswordChange

    def test_too_short_rejected(self):
        PasswordChange = self._import()
        with pytest.raises(ValidationError):
            PasswordChange(current_password="old", new_password="short")

    def test_too_long_rejected(self):
        PasswordChange = self._import()
        with pytest.raises(ValidationError):
            # 73 chars exceeds bcrypt's 72-byte limit
            PasswordChange(current_password="old", new_password="a" * 73)

    def test_valid_password_accepted(self):
        PasswordChange = self._import()
        pc = PasswordChange(current_password="old", new_password="ValidPass1!")
        assert pc.new_password == "ValidPass1!"


# ---------------------------------------------------------------------------
# BUG 7+8 - PT slab upsert: state_code validation + basis enum
# ---------------------------------------------------------------------------


class TestPtSlabUpsert:
    def _import(self):
        from api.routers.payroll import PtSlabUpsert

        return PtSlabUpsert

    def test_invalid_basis_rejected(self):
        PtSlabUpsert = self._import()
        with pytest.raises(ValidationError):
            PtSlabUpsert(basis="WEEKLY")

    def test_monthly_basis_accepted(self):
        PtSlabUpsert = self._import()
        p = PtSlabUpsert(basis="MONTHLY")
        assert p.basis == "MONTHLY"

    def test_annual_basis_accepted(self):
        PtSlabUpsert = self._import()
        p = PtSlabUpsert(basis="ANNUAL")
        assert p.basis == "ANNUAL"

    def test_basis_case_insensitive(self):
        PtSlabUpsert = self._import()
        p = PtSlabUpsert(basis="monthly")
        assert p.basis == "MONTHLY"

    def test_negative_slab_tax_rejected(self):
        PtSlabUpsert = self._import()
        with pytest.raises(ValidationError):
            PtSlabUpsert(slabs=[{"from": 0, "to": 15000, "tax": -100}])

    def test_valid_slabs_accepted(self):
        PtSlabUpsert = self._import()
        p = PtSlabUpsert(slabs=[{"from": 0, "to": 15000, "tax": 0}, {"from": 15001, "tax": 200}])
        assert len(p.slabs) == 2


# ---------------------------------------------------------------------------
# BUG 7 - PT slab state_code HTTP validation (needs client fixture)
# ---------------------------------------------------------------------------


class TestPtSlabStateCodeHttp:
    def test_invalid_state_code_returns_400(self, client, auth_headers):
        resp = client.put(
            "/api/v1/payroll/pt-slabs/ZZ",
            json={"basis": "MONTHLY", "slabs": []},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "state_code" in resp.json().get("detail", "").lower() or \
               "unknown" in resp.json().get("detail", "").lower()

    def test_valid_state_code_not_rejected_as_400(self, client, auth_headers):
        # 27 = Maharashtra, always in INDIAN_STATE_CODES; should pass the state_code
        # check and proceed. Without a DB it will 503 / 500; 400 would be wrong.
        resp = client.put(
            "/api/v1/payroll/pt-slabs/27",
            json={"basis": "MONTHLY", "slabs": []},
            headers=auth_headers,
        )
        # A 400 here means we wrongly rejected a valid state code.
        assert resp.status_code != 400, resp.text


# ---------------------------------------------------------------------------
# BUG 1 - Discount rules persist (HTTP round-trip)
# ---------------------------------------------------------------------------


class TestDiscountRulesPersistence:
    def test_update_discount_rules_persists(self, client, auth_headers):
        rules = {"SALES_STAFF": {"MASS": 10, "PREMIUM": 15}}
        put = client.put(
            "/api/v1/settings/discount-rules",
            json=rules,
            headers=auth_headers,
        )
        assert put.status_code == 200, put.text
        if _no_db(put):
            pytest.skip("discount_rules collection unavailable (no DB)")
        get = client.get("/api/v1/settings/discount-rules", headers=auth_headers)
        assert get.status_code == 200
        body = get.json()
        assert "rules" in body
        assert body["rules"].get("SALES_STAFF", {}).get("MASS") == 10

    def test_set_individual_discount_rule_persists(self, client, auth_headers):
        payload = {"role": "STORE_MANAGER", "category": "PREMIUM", "max_discount": 20.0}
        put = client.post(
            "/api/v1/settings/discount-rules",
            json=payload,
            headers=auth_headers,
        )
        assert put.status_code == 200, put.text
        if _no_db(put):
            pytest.skip("discount_rules collection unavailable (no DB)")
        get = client.get("/api/v1/settings/discount-rules", headers=auth_headers)
        assert get.status_code == 200
        body = get.json()
        assert "rules" in body

    def test_discount_max_over_100_rejected(self, client, auth_headers):
        payload = {"role": "STORE_MANAGER", "category": "MASS", "max_discount": 150.0}
        put = client.post(
            "/api/v1/settings/discount-rules",
            json=payload,
            headers=auth_headers,
        )
        assert put.status_code == 422

    def test_discount_negative_rejected(self, client, auth_headers):
        payload = {"role": "STORE_MANAGER", "category": "MASS", "max_discount": -5.0}
        put = client.post(
            "/api/v1/settings/discount-rules",
            json=payload,
            headers=auth_headers,
        )
        assert put.status_code == 422


# ---------------------------------------------------------------------------
# BUG 2 - System settings persist (HTTP round-trip)
# ---------------------------------------------------------------------------


class TestSystemSettingsPersistence:
    def test_system_settings_persist(self, client, auth_headers):
        payload = {"maintenance_mode": True, "session_timeout_minutes": 120}
        put = client.put(
            "/api/v1/settings/system",
            json=payload,
            headers=auth_headers,
        )
        assert put.status_code == 200, put.text
        if _no_db(put):
            pytest.skip("system_settings collection unavailable (no DB)")
        get = client.get("/api/v1/settings/system", headers=auth_headers)
        assert get.status_code == 200
        body = get.json()
        assert body.get("maintenance_mode") is True
        assert body.get("session_timeout_minutes") == 120


# ---------------------------------------------------------------------------
# BUG 5 - Feature toggle cache invalidation
# ---------------------------------------------------------------------------


class TestFeatureToggleCacheInvalidation:
    def test_put_then_get_reflects_change_immediately(self, client, auth_headers):
        store_id = "BV-TEST-01"

        # First, write a known state
        put = client.put(
            f"/api/v1/settings/feature-toggles/{store_id}",
            json={"features": {"pos-quick-sale": False, "loyalty-points": False}},
            headers=auth_headers,
        )
        assert put.status_code == 200, put.text
        if _no_db(put):
            pytest.skip("feature_toggles collection unavailable (no DB)")

        # Immediately GET — must reflect the update (not a stale cache value)
        get = client.get(
            f"/api/v1/settings/feature-toggles/{store_id}",
            headers=auth_headers,
        )
        assert get.status_code == 200
        assert get.json().get("features", {}).get("pos-quick-sale") is False

        # Update again
        put2 = client.put(
            f"/api/v1/settings/feature-toggles/{store_id}",
            json={"features": {"pos-quick-sale": True, "loyalty-points": True}},
            headers=auth_headers,
        )
        assert put2.status_code == 200

        get2 = client.get(
            f"/api/v1/settings/feature-toggles/{store_id}",
            headers=auth_headers,
        )
        assert get2.json().get("features", {}).get("pos-quick-sale") is True
        assert get2.json().get("features", {}).get("loyalty-points") is True

    def test_patch_then_get_reflects_change_immediately(self, client, auth_headers):
        store_id = "BV-TEST-01"

        put = client.patch(
            f"/api/v1/settings/feature-toggles/{store_id}",
            json={"features": {"workshop-module": False}},
            headers=auth_headers,
        )
        assert put.status_code == 200, put.text
        if _no_db(put):
            pytest.skip("feature_toggles collection unavailable (no DB)")

        get = client.get(
            f"/api/v1/settings/feature-toggles/{store_id}",
            headers=auth_headers,
        )
        assert get.status_code == 200
        assert get.json().get("features", {}).get("workshop-module") is False


# ---------------------------------------------------------------------------
# BUG 3 - Tax settings GSTIN via HTTP
# ---------------------------------------------------------------------------


class TestTaxSettingsHttp:
    def test_invalid_gstin_returns_422(self, client, auth_headers):
        resp = client.put(
            "/api/v1/settings/tax",
            json={"company_gstin": "INVALID123"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_valid_gstin_accepted(self, client, auth_headers):
        resp = client.put(
            "/api/v1/settings/tax",
            json={"company_gstin": "27ABCDE1234F1Z5"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_negative_gst_rate_returns_422(self, client, auth_headers):
        resp = client.put(
            "/api/v1/settings/tax",
            json={"default_gst_rate": -1.0},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_over_100_gst_rate_returns_422(self, client, auth_headers):
        resp = client.put(
            "/api/v1/settings/tax",
            json={"default_gst_rate": 150.0},
            headers=auth_headers,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# BUG 4 + 9 - Invoice + printer settings via HTTP
# ---------------------------------------------------------------------------


class TestInvoiceSettingsHttp:
    def test_zero_invoice_start_returns_422(self, client, auth_headers):
        resp = client.put(
            "/api/v1/settings/invoice",
            json={"invoice_start_number": 0},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_invalid_prefix_returns_422(self, client, auth_headers):
        resp = client.put(
            "/api/v1/settings/invoice",
            json={"invoice_prefix": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestPrinterSettingsHttp:
    def test_invalid_width_returns_422(self, client, auth_headers):
        resp = client.put(
            "/api/v1/settings/printers",
            json={"receipt_printer_width": 100},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_invalid_label_size_returns_422(self, client, auth_headers):
        resp = client.put(
            "/api/v1/settings/printers",
            json={"label_size": "99x99"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# BUG 11 - Entity phone/email validation via HTTP
# ---------------------------------------------------------------------------


class TestEntityValidation:
    def test_invalid_phone_returns_400(self, client, auth_headers):
        resp = client.post(
            "/api/v1/entities",
            json={
                "name": "Test Entity",
                "registered_phone": "123",  # too short / invalid
            },
            headers=auth_headers,
        )
        # Could be 400 (validation) or 503 (no DB); should NOT be 201/200
        assert resp.status_code in (400, 503), resp.text

    def test_invalid_email_returns_400(self, client, auth_headers):
        resp = client.post(
            "/api/v1/entities",
            json={
                "name": "Test Entity",
                "registered_email": "not-an-email",
            },
            headers=auth_headers,
        )
        assert resp.status_code in (400, 503), resp.text

    def test_valid_phone_and_email_accepted(self, client, auth_headers):
        resp = client.post(
            "/api/v1/entities",
            json={
                "name": "Valid Entity",
                "registered_phone": "9876543210",
                "registered_email": "info@example.com",
            },
            headers=auth_headers,
        )
        # 201 = created, 503/500 = no DB — any of these is fine; 400 would be a regression
        # (400 means we wrongly rejected a valid phone/email).
        assert resp.status_code != 400, resp.text
