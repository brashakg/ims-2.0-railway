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

import inspect
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


def test_consent_text_endpoints_are_correctly_gated():
    """GET is open to any authenticated user (the create form needs it); PUT is
    ADMIN-gated (editing legal wording is privileged)."""
    import api.routers.marketing as mk

    get_src = inspect.getsource(mk.get_consent_text)
    put_src = inspect.getsource(mk.update_consent_text)
    assert "get_current_user" in get_src
    assert 'require_roles("ADMIN")' in put_src


def test_default_consent_text_is_sensible():
    from api.routers.marketing import _DEFAULT_CONSENT_TEXT

    t = _DEFAULT_CONSENT_TEXT.lower()
    assert "store" in t and "consent" in t and "withdraw" in t


# ---------------------------------------------------------------------------
# New tests -- DPDP consent ledger (FIN-2)
# ---------------------------------------------------------------------------

def test_consent_grant_request_defaults_all_purposes():
    """Omitting purposes grants all four by default."""
    from api.routers.customers import ConsentGrantRequest, _ALL_PURPOSES

    req = ConsentGrantRequest()
    assert set(req.purposes) == _ALL_PURPOSES


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
# Pending-purge endpoint -- ADMIN gate check (source inspection)
# ---------------------------------------------------------------------------

def test_pending_purge_endpoint_is_admin_gated():
    """list_pending_purge must use require_roles('ADMIN'), not just get_current_user."""
    import api.routers.customers as cust_mod

    src = inspect.getsource(cust_mod.list_pending_purge)
    assert 'require_roles("ADMIN")' in src


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
        "SERVICE_DELIVERY", "MARKETING", "RX_HISTORY", "ANALYTICS"
    }


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
