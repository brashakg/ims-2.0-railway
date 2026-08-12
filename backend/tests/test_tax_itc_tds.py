"""
IMS 2.0 — ITC eligibility (received + not-blocked) + editable TDS rates
========================================================================
Owner decisions:
  * ITC: a vendor bill's GST counts toward input credit only when ELIGIBLE --
    excluded if 17(5)-blocked OR not-yet-received; a bill with NO flags
    default-INCLUDES (historical data never silently drops).
  * TDS: a SUPERADMIN-editable national rate map overrides the code defaults.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-tax")


# --- ITC eligibility -------------------------------------------------------
def test_unflagged_bill_is_eligible():
    from api.routers.finance import _itc_eligible_bill

    assert _itc_eligible_bill({"tax_amount": 100}) is True  # default-include


def test_itc_blocked_bill_excluded():
    from api.routers.finance import _itc_eligible_bill

    assert _itc_eligible_bill({"tax_amount": 100, "itc_blocked": True}) is False


def test_not_received_bill_excluded():
    from api.routers.finance import _itc_eligible_bill

    assert _itc_eligible_bill({"tax_amount": 100, "received": False}) is False
    for st in ("DRAFT", "PENDING", "CANCELLED", "rejected"):
        assert _itc_eligible_bill({"tax_amount": 100, "status": st}) is False


def test_received_paid_bill_eligible():
    from api.routers.finance import _itc_eligible_bill

    assert _itc_eligible_bill({"tax_amount": 100, "status": "PAID", "received": True}) is True
    assert _itc_eligible_bill({"tax_amount": 100, "status": "BOOKED"}) is True


def test_explicit_itc_eligible_false_excluded():
    from api.routers.finance import _itc_eligible_bill

    assert _itc_eligible_bill({"tax_amount": 100, "itc_eligible": False}) is False


# --- editable TDS rates ----------------------------------------------------
def test_compute_tds_uses_override_when_present():
    from api.services.ap_engine import compute_tds

    # 194H default is 2.0; an override to 5.0 must win.
    out = compute_tds(100000, "194H", overrides={"194H": 5.0})
    assert out["rate"] == 5.0 and out["tds_amount"] == 5000.0


def test_compute_tds_falls_back_to_code_default():
    from api.services.ap_engine import compute_tds

    out = compute_tds(100000, "194H")  # no override
    assert out["rate"] == 2.0  # the merged Budget-2024 default


def test_resolve_tds_rate_ignores_bad_override():
    from api.services.ap_engine import resolve_tds_rate

    # garbage override -> fall back to default, never crash
    assert resolve_tds_rate("194J", overrides={"194J": "abc"}) == 10.0


def test_tds_rates_update_model_validates():
    from pydantic import ValidationError

    from api.routers.settings import TdsRatesUpdate

    ok = TdsRatesUpdate(rates={"194h": 2.0, "194C_IND": 1.0})  # normalizes case
    assert ok.rates == {"194H": 2.0, "194C_IND": 1.0}
    with pytest.raises(ValidationError):
        TdsRatesUpdate(rates={"NOPE": 2.0})        # unknown section
    with pytest.raises(ValidationError):
        TdsRatesUpdate(rates={"194H": 99.0})       # out of 0-30 band


def test_tds_rates_are_readable_by_any_authenticated_user(client, staff_headers):
    """BEHAVIOURAL: the AP/payment screens show the rates, so a SALES_STAFF read
    must succeed -- and an unauthenticated one must not.

    Replaces a grep for "get_current_user" in the handler source, which appears
    throughout settings.py regardless of how THIS route is wired.
    """
    assert client.get("/api/v1/settings/tds-rates").status_code == 401
    resp = client.get("/api/v1/settings/tds-rates", headers=staff_headers)
    assert resp.status_code == 200, resp.text


def test_tds_rate_edit_is_superadmin_only(client, staff_headers, auth_headers):
    """BEHAVIOURAL: editing a statutory deduction rate is SUPERADMIN-only.

    A wrong TDS rate silently under-deducts on every vendor payment, so prove
    the server refuses the write rather than grepping for the decorator text.
    """
    body = {"rates": {"194H": 2.0}}

    assert client.put("/api/v1/settings/tds-rates", json=body).status_code == 401

    denied = client.put(
        "/api/v1/settings/tds-rates", json=body, headers=staff_headers
    )
    assert denied.status_code == 403, denied.text

    # The SUPERADMIN token from conftest must clear the gate (a 5xx from the
    # absent DB is fine -- what matters is that it is NOT refused by RBAC).
    allowed = client.put(
        "/api/v1/settings/tds-rates", json=body, headers=auth_headers
    )
    assert allowed.status_code != 403, allowed.text
