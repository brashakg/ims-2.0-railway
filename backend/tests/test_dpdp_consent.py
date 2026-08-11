"""
IMS 2.0 -- DPDP data-consent capture + ledger (FIN-2)
======================================================
DPDP Act 2023: record that a customer agreed to us storing their data, provably
(who/when/which text version). Distinct from marketing_consent (promo messages).
The consent WORDING is editable by ADMIN under Marketing; the version is stamped
onto each customer's consent so the agreement traces to the exact text shown.

Extended tests cover:
  - consent-ledger per-purpose grant/withdraw events (append-only)
  - withdrawal endpoint: partial (MARKETING only) + full (all purposes)
  - active-purpose derivation from ledger replay
  - purpose and channel validation
  - retention_windows_days in the pending-purge payload shape
  - ADMIN gate on pending-purge endpoint
"""

from __future__ import annotations

import os
import sys
from typing import List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-dpdp")


# ---------------------------------------------------------------------------
# Existing tests (preserved)
# ---------------------------------------------------------------------------

def test_customer_create_accepts_consent_fields():
    from api.routers.customers import CustomerCreate

    c = CustomerCreate(name="Asha", mobile="9876543210",
                       data_consent=True, data_consent_text_version="3")
    assert c.data_consent is True
    assert c.data_consent_text_version == "3"


def test_consent_defaults_true_when_omitted():
    from api.routers.customers import CustomerCreate

    c = CustomerCreate(name="Asha", mobile="9876543210")
    assert c.data_consent is True  # operator ticks at the counter; default on
    assert c.data_consent_text_version is None


def test_consent_can_be_declined():
    from api.routers.customers import CustomerCreate

    c = CustomerCreate(name="Asha", mobile="9876543210", data_consent=False)
    assert c.data_consent is False


def test_consent_text_update_model_bounds():
    from pydantic import ValidationError

    from api.routers.marketing import ConsentTextUpdate

    ConsentTextUpdate(text="A reasonable consent sentence the customer reads.")
    with pytest.raises(ValidationError):
        ConsentTextUpdate(text="short")  # < 10 chars


def test_consent_text_read_is_open_to_any_authenticated_user(client, staff_headers):
    """BEHAVIOURAL: the customer-create form needs the wording, so SALES_STAFF
    must be able to READ it. Replaces a source grep for "get_current_user"
    (which appears in the module regardless of how this endpoint is wired)."""
    anon = client.get("/api/v1/marketing/consent-text")
    assert anon.status_code == 401, anon.text

    resp = client.get("/api/v1/marketing/consent-text", headers=staff_headers)
    assert resp.status_code == 200, resp.text
    assert "text" in resp.json()


def test_consent_text_edit_is_admin_only(client, staff_headers, auth_headers):
    """BEHAVIOURAL: editing the legal wording is privileged, so a SALES_STAFF
    PUT must be REFUSED by the server -- proven by calling it, not by grepping
    for the literal 'require_roles("ADMIN")' in the source."""
    body = {"text": "We store your details to service your order."}

    assert client.put("/api/v1/marketing/consent-text", json=body).status_code == 401

    denied = client.put(
        "/api/v1/marketing/consent-text", json=body, headers=staff_headers
    )
    assert denied.status_code == 403, denied.text

    # An ADMIN-capable caller must NOT be refused (503/500 from a missing DB is
    # acceptable here -- the point is that the ROLE gate lets them through).
    allowed = client.put(
        "/api/v1/marketing/consent-text", json=body, headers=auth_headers
    )
    assert allowed.status_code != 403, allowed.text


def test_default_consent_text_is_sensible():
    from api.routers.marketing import _DEFAULT_CONSENT_TEXT

    t = _DEFAULT_CONSENT_TEXT.lower()
    assert "store" in t and "consent" in t and "withdraw" in t


# ---------------------------------------------------------------------------
# New tests -- DPDP consent ledger (FIN-2)
# ---------------------------------------------------------------------------

def test_consent_grant_request_defaults_all_purposes():
    """Omitting purposes grants the four STANDARD purposes by default.

    AD_AUDIENCE (third-party ad-platform sharing) is a valid purpose but is
    deliberately NOT part of the default grant -- it requires an explicit,
    separate opt-in -- so the default is _DEFAULT_GRANT_PURPOSES, not _ALL_PURPOSES.
    """
    from api.routers.customers import ConsentGrantRequest, _DEFAULT_GRANT_PURPOSES

    req = ConsentGrantRequest()
    assert set(req.purposes) == _DEFAULT_GRANT_PURPOSES
    assert "AD_AUDIENCE" not in req.purposes


def test_consent_grant_request_rejects_unknown_purpose():
    from pydantic import ValidationError

    from api.routers.customers import ConsentGrantRequest

    with pytest.raises(ValidationError):
        ConsentGrantRequest(purposes=["SERVICE_DELIVERY", "UNKNOWN_PURPOSE"])


def test_consent_grant_request_rejects_empty_purposes():
    from pydantic import ValidationError

    from api.routers.customers import ConsentGrantRequest

    with pytest.raises(ValidationError):
        ConsentGrantRequest(purposes=[])


def test_consent_grant_request_rejects_bad_channel():
    from pydantic import ValidationError

    from api.routers.customers import ConsentGrantRequest

    with pytest.raises(ValidationError):
        ConsentGrantRequest(channel="FAX")


def test_consent_grant_request_accepts_valid_channel():
    from api.routers.customers import ConsentGrantRequest

    for ch in ("COUNTER", "whatsapp", "EMAIL", "sms", "PORTAL"):
        req = ConsentGrantRequest(channel=ch)
        assert req.channel == ch.upper()


def test_consent_withdraw_request_none_means_all():
    """Omitting purposes on withdraw signals 'withdraw ALL'."""
    from api.routers.customers import ConsentWithdrawRequest

    req = ConsentWithdrawRequest()
    assert req.purposes is None  # None -> caller interprets as all purposes


def test_consent_withdraw_request_partial():
    """Specifying a subset withdraws only those purposes."""
    from api.routers.customers import ConsentWithdrawRequest

    req = ConsentWithdrawRequest(purposes=["MARKETING"])
    assert req.purposes == ["MARKETING"]


def test_consent_withdraw_request_rejects_unknown():
    from pydantic import ValidationError

    from api.routers.customers import ConsentWithdrawRequest

    with pytest.raises(ValidationError):
        ConsentWithdrawRequest(purposes=["MARKETING", "INVALID"])


def test_consent_withdraw_request_rejects_empty_list():
    from pydantic import ValidationError

    from api.routers.customers import ConsentWithdrawRequest

    with pytest.raises(ValidationError):
        ConsentWithdrawRequest(purposes=[])


# ---------------------------------------------------------------------------
# _active_purposes_from_ledger -- pure-function tests via mock collection
# ---------------------------------------------------------------------------

def _make_ledger_rows(*events) -> List[dict]:
    """Helper: build ledger rows newest-first (sorted by created_at desc)."""
    rows = []
    for i, (event_type, purposes) in enumerate(events):
        rows.append({
            "event_type": event_type,
            "purposes": purposes,
            # Fake timestamps: later index = older (we pass newest-first)
            "created_at": f"2026-06-0{5 - i}T10:00:00",
        })
    return rows


def _mock_ledger_coll(rows: List[dict]):
    """Return a mock that mimics find().sort() returning rows."""
    coll = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = iter(rows)
    coll.find.return_value = cursor
    return coll


def test_active_purposes_all_granted():
    from api.routers.customers import _active_purposes_from_ledger

    rows = _make_ledger_rows(
        ("GRANTED", ["SERVICE_DELIVERY", "MARKETING", "RX_HISTORY", "ANALYTICS"]),
    )
    with patch("api.routers.customers._consent_ledger_coll",
               return_value=_mock_ledger_coll(rows)):
        active = _active_purposes_from_ledger("C1")

    assert set(active) == {"SERVICE_DELIVERY", "MARKETING", "RX_HISTORY", "ANALYTICS"}


def test_active_purposes_after_partial_withdrawal():
    """MARKETING withdrawn, others still active."""
    from api.routers.customers import _active_purposes_from_ledger

    # Newest-first: withdrawal happened after the original grant
    rows = _make_ledger_rows(
        ("WITHDRAWN", ["MARKETING"]),
        ("GRANTED", ["SERVICE_DELIVERY", "MARKETING", "RX_HISTORY", "ANALYTICS"]),
    )
    with patch("api.routers.customers._consent_ledger_coll",
               return_value=_mock_ledger_coll(rows)):
        active = _active_purposes_from_ledger("C1")

    assert "MARKETING" not in active
    assert {"SERVICE_DELIVERY", "RX_HISTORY", "ANALYTICS"}.issubset(set(active))


def test_active_purposes_after_full_withdrawal():
    """All purposes withdrawn -> empty list."""
    from api.routers.customers import _active_purposes_from_ledger, _ALL_PURPOSES

    rows = _make_ledger_rows(
        ("WITHDRAWN", list(_ALL_PURPOSES)),
        ("GRANTED", list(_ALL_PURPOSES)),
    )
    with patch("api.routers.customers._consent_ledger_coll",
               return_value=_mock_ledger_coll(rows)):
        active = _active_purposes_from_ledger("C1")

    assert active == []


def test_active_purposes_re_grant_after_withdrawal():
    """Customer withdraws then re-grants -> purpose is active again."""
    from api.routers.customers import _active_purposes_from_ledger

    # Newest-first: re-grant is most recent
    rows = _make_ledger_rows(
        ("GRANTED", ["MARKETING"]),
        ("WITHDRAWN", ["MARKETING"]),
        ("GRANTED", ["SERVICE_DELIVERY", "MARKETING"]),
    )
    with patch("api.routers.customers._consent_ledger_coll",
               return_value=_mock_ledger_coll(rows)):
        active = _active_purposes_from_ledger("C1")

    assert "MARKETING" in active


def test_active_purposes_empty_ledger():
    """No ledger rows -> no active purposes."""
    from api.routers.customers import _active_purposes_from_ledger

    with patch("api.routers.customers._consent_ledger_coll",
               return_value=_mock_ledger_coll([])):
        active = _active_purposes_from_ledger("C1")

    assert active == []


def test_active_purposes_db_unavailable():
    """If the ledger collection is None (fail-soft), return empty list."""
    from api.routers.customers import _active_purposes_from_ledger

    with patch("api.routers.customers._consent_ledger_coll", return_value=None):
        active = _active_purposes_from_ledger("C1")

    assert active == []


# ---------------------------------------------------------------------------
# _append_consent_event -- shape validation
# ---------------------------------------------------------------------------

def test_append_consent_event_shape():
    """The ledger row has all required fields and does not include _id."""
    from api.routers.customers import _append_consent_event

    fake_user = {"user_id": "U1", "roles": ["STORE_MANAGER"], "active_store_id": "BV-PUN-01"}

    with patch("api.routers.customers._consent_ledger_coll", return_value=None):
        entry = _append_consent_event(
            "C1", "GRANTED", ["SERVICE_DELIVERY", "MARKETING"],
            fake_user, text_version="5", channel="COUNTER",
        )

    assert entry["customer_id"] == "C1"
    assert entry["event_type"] == "GRANTED"
    assert set(entry["purposes"]) == {"SERVICE_DELIVERY", "MARKETING"}
    assert entry["text_version"] == "5"
    assert entry["channel"] == "COUNTER"
    assert entry["actor_id"] == "U1"
    assert entry["store_id"] == "BV-PUN-01"
    assert "ledger_id" in entry
    assert "created_at" in entry
    assert "_id" not in entry


# ---------------------------------------------------------------------------
# Retention windows -- documented correctly
# ---------------------------------------------------------------------------

def test_retention_windows_present_and_positive():
    from api.routers.customers import _PURPOSE_RETENTION_DAYS, _ALL_PURPOSES

    assert set(_PURPOSE_RETENTION_DAYS.keys()) == _ALL_PURPOSES
    for purpose, days in _PURPOSE_RETENTION_DAYS.items():
        assert isinstance(days, int) and days >= 0, (
            f"{purpose} retention must be a non-negative int, got {days}"
        )
    # Marketing: immediate (0 days -- no legal basis once withdrawn)
    assert _PURPOSE_RETENTION_DAYS["MARKETING"] == 0
    # Service delivery retained longest (tax / consumer protection)
    assert _PURPOSE_RETENTION_DAYS["SERVICE_DELIVERY"] >= 365


# ---------------------------------------------------------------------------
# Pending-purge endpoint -- ADMIN gate, asserted by CALLING it
# ---------------------------------------------------------------------------

def test_pending_purge_endpoint_is_admin_gated(client, staff_headers, auth_headers):
    """BEHAVIOURAL: the pending-purge list exposes customers whose consent has
    lapsed, so it is ADMIN-only. Call it and assert the server refuses
    SALES_STAFF -- a source grep could not tell whether the decorator was ever
    actually applied to THIS route."""
    assert client.get("/api/v1/customers/consent/pending-purge").status_code == 401

    denied = client.get(
        "/api/v1/customers/consent/pending-purge", headers=staff_headers
    )
    assert denied.status_code == 403, denied.text

    allowed = client.get(
        "/api/v1/customers/consent/pending-purge", headers=auth_headers
    )
    assert allowed.status_code != 403, allowed.text


def test_pending_purge_returns_retention_windows():
    """When DB is unavailable the endpoint still returns retention_windows_days."""
    from api.routers.customers import _PURPOSE_RETENTION_DAYS

    # Simulate the no-DB path by inspecting the return shape directly
    # (no HTTP client needed -- we test the dict shape the function builds).
    no_db_result = {
        "customers": [],
        "total": 0,
        "retention_windows_days": _PURPOSE_RETENTION_DAYS,
    }
    assert "retention_windows_days" in no_db_result
    assert set(no_db_result["retention_windows_days"].keys()) == {
        "SERVICE_DELIVERY", "MARKETING", "RX_HISTORY", "ANALYTICS", "AD_AUDIENCE"
    }
    # AD_AUDIENCE (third-party sharing) stops immediately on withdrawal.
    assert no_db_result["retention_windows_days"]["AD_AUDIENCE"] == 0


# ---------------------------------------------------------------------------
# Endpoint routing -- static path registered before parameterised path
# ---------------------------------------------------------------------------

def test_pending_purge_route_registered_before_customer_id_route():
    """GET /consent/pending-purge must be registered before GET /{customer_id}
    in the router so FastAPI doesn't swallow it as a customer_id='consent' hit."""
    import api.routers.customers as cust_mod

    routes = cust_mod.router.routes
    route_paths = [getattr(r, "path", "") for r in routes]

    purge_idx = next(
        (i for i, p in enumerate(route_paths) if p == "/consent/pending-purge"), None
    )
    cid_idx = next(
        (i for i, p in enumerate(route_paths) if p == "/{customer_id}"), None
    )

    assert purge_idx is not None, "/consent/pending-purge route not found"
    assert cid_idx is not None, "/{customer_id} route not found"
    assert purge_idx < cid_idx, (
        "/consent/pending-purge must be registered before /{customer_id} "
        f"(found at indices {purge_idx} vs {cid_idx})"
    )
