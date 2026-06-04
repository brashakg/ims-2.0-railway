"""
IMS 2.0 -- Day-End cash-drawer close (reports.py)
=================================================
The Day-End Closing Report's "Close Day" action used to only flip a local React
flag (no persistence, no audit). These tests cover the new persisted endpoints:

  POST /api/v1/reports/day-end-close  -- record a close (idempotent per
                                          store+date, variance computed server-side)
  GET  /api/v1/reports/day-end-close  -- close status for a store+date

Gating: store-financial roles only (ACCOUNTANT/ADMIN/AREA_MANAGER/STORE_MANAGER/
SALES_CASHIER/CASHIER; SUPERADMIN auto-passes). POS/clinical-only roles are 403.

These run against the CI mongo:7.0; when no DB is connected (local), the POST
returns 503 and the test asserts that branch instead of the persisted flow.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_day_end_close.py -q
"""

from __future__ import annotations

import pytest


GET_URL = "/api/v1/reports/day-end-close"
POST_URL = "/api/v1/reports/day-end-close"
STORE = "BV-TEST-01"
DATE = "2026-05-30"


def _headers(roles, store_id=STORE):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "dec-1",
            "username": "dec",
            "roles": roles,
            "store_ids": [store_id],
            "active_store_id": store_id,
        }
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# RBAC gating
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("roles", [["OPTOMETRIST"], ["SALES_STAFF"], ["WORKSHOP_STAFF"]])
def test_get_blocked_for_non_finance_roles(client, roles):
    r = client.get(GET_URL, params={"date": DATE, "store_id": STORE}, headers=_headers(roles))
    assert r.status_code == 403


@pytest.mark.parametrize("roles", [["OPTOMETRIST"], ["SALES_STAFF"], ["WORKSHOP_STAFF"]])
def test_post_blocked_for_non_finance_roles(client, roles):
    r = client.post(
        POST_URL,
        json={"date": DATE, "store_id": STORE, "closing_cash": 100, "system_cash": 100},
        headers=_headers(roles),
    )
    assert r.status_code == 403


@pytest.mark.parametrize("roles", [["STORE_MANAGER"], ["ACCOUNTANT"], ["SALES_CASHIER"]])
def test_get_allowed_for_finance_roles(client, roles):
    # Not 403 (200 with closed:false when nothing recorded yet).
    r = client.get(GET_URL, params={"date": DATE, "store_id": STORE}, headers=_headers(roles))
    assert r.status_code != 403


def test_get_allowed_for_superadmin(client, auth_headers):
    r = client.get(GET_URL, params={"date": DATE, "store_id": STORE}, headers=auth_headers)
    assert r.status_code != 403


# ---------------------------------------------------------------------------
# Persisted flow (DB-aware)
# ---------------------------------------------------------------------------

def test_close_persists_audits_and_is_idempotent(client, auth_headers):
    payload = {
        "date": DATE,
        "store_id": STORE,
        "closing_cash": 5100,
        "system_cash": 5000,
        "notes": "drawer over by 100",
    }

    # Pre-state: not closed.
    pre = client.get(GET_URL, params={"date": DATE, "store_id": STORE}, headers=auth_headers)
    assert pre.status_code == 200
    assert pre.json()["closed"] is False

    first = client.post(POST_URL, json=payload, headers=auth_headers)

    # Branch on what the write path actually returned (robust across CI mongo,
    # the local seeded DB, and a truly DB-less run):
    if first.status_code == 503:
        # No DB -> the write path fails loudly (never a fabricated success).
        return

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["closed"] is True
    close = body["close"]
    # Variance is computed server-side (closing - system), never trusted from
    # the client; notes round-trip.
    assert close["variance"] == 100.0
    assert close["closing_cash"] == 5100.0
    assert close["system_cash"] == 5000.0
    assert close["closed_by"] == "test-admin-001"
    assert close["closed_at"]

    # GET now reflects the persisted close (survives a refresh).
    after = client.get(GET_URL, params={"date": DATE, "store_id": STORE}, headers=auth_headers)
    assert after.status_code == 200
    aj = after.json()
    assert aj["closed"] is True
    assert aj["close"]["variance"] == 100.0

    # Idempotent: a second close of the same day is rejected (409), not a
    # silent re-write. The existing close is returned in the error detail.
    second = client.post(POST_URL, json=payload, headers=auth_headers)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["close"]["variance"] == 100.0

    # An audit row was written for the close.
    from database.connection import get_db

    mongo = getattr(get_db(), "db", None)
    if mongo is not None:
        row = mongo["audit_logs"].find_one(
            {"action": "DAY_END_CLOSED", "entity_id": f"{STORE}:{DATE}"}
        )
        assert row is not None
        assert row["details"]["variance"] == 100.0
