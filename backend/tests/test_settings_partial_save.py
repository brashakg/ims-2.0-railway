"""
IMS 2.0 - settings singletons save as a PARTIAL update (audit L1-F05, L1-F13)
=============================================================================
Audit lane1 L1-F05: after a failed read the Tax / Invoice / Printer screens
sent `{}` and the server answered 200 "updated" while writing every model
DEFAULT over the stored record (GST rate 5 -> 18, warranty 90 -> 365, terms ->
the default text, receipt width 58 -> 80, stage auto-print False -> True).

The four singleton saves (business, tax, invoice, printers) now write only the
fields that were sent, fill defaults only when the record is first created, and
refuse a body with nothing in it. These tests drive the REAL handlers over a
strict in-memory collection and assert on the STORED document.

L1-F13: clearing an HSN row's description (sending "") must store "".
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-key-settings-partial")

from api.routers import admin_catalog, settings as settings_router  # noqa: E402
from tests.strict_fakes import StrictCollection  # noqa: E402

ADMIN = {"user_id": "u-admin", "roles": ["ADMIN"]}


@pytest.fixture()
def colls(monkeypatch):
    store = {}

    def _get(name):
        return store.setdefault(name, StrictCollection())

    monkeypatch.setattr(settings_router, "_get_settings_collection", _get)
    return store


def _stored(colls, name):
    doc = colls[name].find_one({"_id": "default"})
    return {k: v for k, v in (doc or {}).items() if k != "_id"}


def _run(coro):
    return asyncio.run(coro)


SAVED_TAX = {
    "gst_enabled": False,
    "company_gstin": "20ABCDE1234F1Z5",
    "default_gst_rate": 5.0,
    "hsn_validation": True,
    "e_invoice_enabled": False,
    "e_invoice_username": None,
    "e_way_bill_enabled": False,
    "e_way_bill_threshold": 50000.0,
    "tds_enabled": False,
    "tds_rate": 0.0,
}
SAVED_INVOICE = {
    "invoice_prefix": "BV",
    "invoice_start_number": 1,
    "current_invoice_number": 41,
    "financial_year": "2026-27",
    "show_logo_on_invoice": False,
    "show_terms_on_invoice": True,
    "default_terms": "AUDIT LANE1 SAVED TERMS",
    "default_warranty_days": 90,
    "show_qr_code": False,
    "digital_signature_enabled": False,
}
SAVED_PRINTER = {
    "receipt_printer_name": "TM-T82",
    "receipt_printer_width": 58,
    "label_printer_name": None,
    "label_size": "50x25",
    "auto_print_receipt": False,
    "auto_print_job_card": True,
    "copies_per_print": 2,
    "qz_enabled": True,
    "auto_print_stage_sticker": False,
}
SAVED_BUSINESS = {
    "company_name": "Saved Optics Pvt Ltd",
    "company_short_name": "SO",
    "tagline": "Saved tagline",
    "logo_url": None,
    "support_email": "owner@example.com",
    "address": "12 Saved Road",
}

CASES = [
    ("tax_settings", SAVED_TAX, settings_router.TaxSettings, settings_router.update_tax_settings),
    ("invoice_settings", SAVED_INVOICE, settings_router.InvoiceSettings, settings_router.update_invoice_settings),
    ("printer_settings", SAVED_PRINTER, settings_router.PrinterSettings, settings_router.update_printer_settings),
    ("business_settings", SAVED_BUSINESS, settings_router.BusinessSettings, settings_router.update_business_settings),
]


@pytest.mark.parametrize("name,saved,model,handler", CASES, ids=[c[0] for c in CASES])
def test_an_empty_save_is_refused_and_the_stored_record_is_untouched(colls, name, saved, model, handler):
    """The audit's exact request: PUT {} after a failed read."""
    settings_router._get_settings_collection(name).insert_one({"_id": "default", **saved})
    with pytest.raises(HTTPException) as exc:
        _run(handler(model.model_validate({}), ADMIN))
    assert exc.value.status_code == 400
    assert "nothing was saved" in exc.value.detail
    assert _stored(colls, name) == saved


def test_a_one_field_tax_save_keeps_every_other_stored_value(colls):
    settings_router._get_settings_collection("tax_settings").insert_one({"_id": "default", **SAVED_TAX})
    body = settings_router.TaxSettings.model_validate({"default_gst_rate": 0})
    out = _run(settings_router.update_tax_settings(body, ADMIN))
    # 0 is a real rate (L1-F06): stored as 0, not swapped for the 18 default.
    assert _stored(colls, "tax_settings") == {**SAVED_TAX, "default_gst_rate": 0.0}
    assert out["settings"]["company_gstin"] == "20ABCDE1234F1Z5"
    assert out["settings"]["gst_enabled"] is False


def test_a_one_field_invoice_save_keeps_terms_and_warranty(colls):
    settings_router._get_settings_collection("invoice_settings").insert_one({"_id": "default", **SAVED_INVOICE})
    body = settings_router.InvoiceSettings.model_validate({"show_qr_code": True})
    _run(settings_router.update_invoice_settings(body, ADMIN))
    stored = _stored(colls, "invoice_settings")
    assert stored == {**SAVED_INVOICE, "show_qr_code": True}
    assert stored["default_warranty_days"] == 90
    assert stored["default_terms"] == "AUDIT LANE1 SAVED TERMS"


def test_a_one_field_printer_save_keeps_width_and_auto_print(colls):
    settings_router._get_settings_collection("printer_settings").insert_one({"_id": "default", **SAVED_PRINTER})
    body = settings_router.PrinterSettings.model_validate({"qz_enabled": False})
    _run(settings_router.update_printer_settings(body, ADMIN))
    assert _stored(colls, "printer_settings") == {**SAVED_PRINTER, "qz_enabled": False}


def test_the_logo_only_save_keeps_the_company_profile(colls):
    """The Business Profile logo upload sends only logo_url."""
    settings_router._get_settings_collection("business_settings").insert_one({"_id": "default", **SAVED_BUSINESS})
    body = settings_router.BusinessSettings.model_validate({"logo_url": "/api/v1/settings/business/logo/abc"})
    _run(settings_router.update_business_settings(body, ADMIN))
    assert _stored(colls, "business_settings") == {
        **SAVED_BUSINESS,
        "logo_url": "/api/v1/settings/business/logo/abc",
    }


def test_the_first_ever_save_still_creates_a_complete_record(colls):
    """A clean first run stays supported: defaults fill what was not sent."""
    body = settings_router.InvoiceSettings.model_validate({"default_warranty_days": 0})
    out = _run(settings_router.update_invoice_settings(body, ADMIN))
    stored = _stored(colls, "invoice_settings")
    assert stored == {**settings_router.InvoiceSettings().model_dump(), "default_warranty_days": 0}
    assert out["settings"] == stored
    assert _run(settings_router.get_invoice_settings(ADMIN)) == stored


def test_validation_still_applies_to_the_fields_that_are_sent():
    with pytest.raises(ValueError):
        settings_router.TaxSettings.model_validate({"default_gst_rate": -1})
    with pytest.raises(ValueError):
        settings_router.InvoiceSettings.model_validate({"default_warranty_days": 3651})
    with pytest.raises(ValueError):
        settings_router.PrinterSettings.model_validate({"receipt_printer_width": 100})


def test_role_gates_are_unchanged(colls):
    for handler, model in (
        (settings_router.update_tax_settings, settings_router.TaxSettings),
        (settings_router.update_invoice_settings, settings_router.InvoiceSettings),
        (settings_router.update_printer_settings, settings_router.PrinterSettings),
        (settings_router.update_business_settings, settings_router.BusinessSettings),
    ):
        with pytest.raises(HTTPException) as exc:
            _run(handler(model.model_validate({}), {"user_id": "u", "roles": ["SALES_STAFF"]}))
        assert exc.value.status_code == 403


# --- L1-F13: an HSN description can be cleared --------------------------------


def test_clearing_an_hsn_description_stores_the_empty_text(monkeypatch):
    coll = StrictCollection()
    coll.insert_one(
        {"hsn_id": "h1", "hsn_code": "991199", "description": "AUDIT L1 TAX", "category_hint": "SERVICE", "gst_rate": 18.0}
    )
    monkeypatch.setattr(admin_catalog, "_coll", lambda name: coll)
    monkeypatch.setattr(admin_catalog, "invalidate_gst_cache", lambda: None)

    body = admin_catalog.HsnRateUpdate.model_validate({"gst_rate": 12, "description": "", "category_hint": ""})
    out = _run(admin_catalog.update_hsn_rate("h1", body))

    stored = coll.find_one({"hsn_id": "h1"})
    assert stored["description"] == ""
    assert stored["category_hint"] == ""
    assert stored["gst_rate"] == 12
    assert stored["hsn_code"] == "991199"
    assert out["description"] == ""

    # Leaving a field out still means "unchanged".
    _run(admin_catalog.update_hsn_rate("h1", admin_catalog.HsnRateUpdate.model_validate({"description": "Back"})))
    stored = coll.find_one({"hsn_id": "h1"})
    assert stored["description"] == "Back" and stored["gst_rate"] == 12
