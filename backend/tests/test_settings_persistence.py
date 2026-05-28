"""
IMS 2.0 - Settings persistence
==============================
The Tax / Invoice / Business / Printer "Save" handlers must WRITE to Mongo. They
previously returned a success message without persisting, so every reload
silently reverted to defaults (data loss across the Settings panels). These
tests round-trip PUT -> GET against the real settings collections.

Runs against CI's mongo:7.0 (the session `client` fixture connects the DB). When
no DB is connected (local runs) the handler returns a "(no DB)" message, so the
persistence assertions are skipped rather than failing.
"""

import pytest


def _no_db(put_resp) -> bool:
    """True when the PUT ran without a DB (so GET can't round-trip)."""
    try:
        return "(no DB)" in (put_resp.json().get("message", "") or "")
    except Exception:  # noqa: BLE001
        return False


def test_tax_settings_persist(client, auth_headers):
    put = client.put(
        "/api/v1/settings/tax",
        json={"company_gstin": "29ABCDE1234F1Z5", "default_gst_rate": 12.0},
        headers=auth_headers,
    )
    assert put.status_code == 200, put.text
    if _no_db(put):
        pytest.skip("tax_settings collection unavailable (no DB)")
    got = client.get("/api/v1/settings/tax", headers=auth_headers).json()
    assert got.get("company_gstin") == "29ABCDE1234F1Z5"
    assert got.get("default_gst_rate") == 12.0


def test_business_settings_persist(client, auth_headers):
    put = client.put(
        "/api/v1/settings/business",
        json={"company_name": "Persisted Optics Pvt Ltd"},
        headers=auth_headers,
    )
    assert put.status_code == 200, put.text
    if _no_db(put):
        pytest.skip("business_settings collection unavailable (no DB)")
    got = client.get("/api/v1/settings/business", headers=auth_headers).json()
    assert got.get("company_name") == "Persisted Optics Pvt Ltd"


def test_invoice_settings_persist(client, auth_headers):
    put = client.put(
        "/api/v1/settings/invoice",
        json={"invoice_prefix": "TST"},
        headers=auth_headers,
    )
    assert put.status_code == 200, put.text
    if _no_db(put):
        pytest.skip("invoice_settings collection unavailable (no DB)")
    got = client.get("/api/v1/settings/invoice", headers=auth_headers).json()
    assert got.get("invoice_prefix") == "TST"


def test_printer_settings_persist(client, auth_headers):
    put = client.put(
        "/api/v1/settings/printers",
        json={"receipt_printer_width": 58, "copies_per_print": 2},
        headers=auth_headers,
    )
    assert put.status_code == 200, put.text
    if _no_db(put):
        pytest.skip("printer_settings collection unavailable (no DB)")
    got = client.get("/api/v1/settings/printers", headers=auth_headers).json()
    assert got.get("receipt_printer_width") == 58
    assert got.get("copies_per_print") == 2
