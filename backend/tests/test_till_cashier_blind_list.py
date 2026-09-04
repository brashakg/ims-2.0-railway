"""
IMS 2.0 - blind EOD: the CASHIER can reach the count, and the wire stays blind
==============================================================================
Owner rulings, both binding:
  * 2026-08-25: a BLIND count IS the day-end -- staff must not see the expected
    figure while counting.
  * 2026-09-03: cashiers COUNT AND SUBMIT; the manager reviews the variance
    AFTER submission ("only managers submit" explicitly rejected).

The defect these tests pin: GET /till/sessions was gated to manager/finance
roles only, so a cashier could never FIND the shared drawer session -- the
Submit Count panel keyed on it never rendered and the blind day-end was
unreachable by the very people who count. The fix opens the list door to the
operate roles and blind-redacts every cashier-only row AT THE DATA LAYER
(redact_for_cashier): the expected figure is off the WIRE, not merely off the
screen (a hidden column is defeated by a devtools tab).

Exercises the REAL eod_tally service + the REAL till router against the shared
strict in-memory Mongo double (strict_fakes.StrictDB -- raises on any operator
it does not faithfully implement, and honours query filters, so a window bug
cannot hide behind a lenient fake). Policy reads + the audit repo are pinned at
their real call sites, same seams as test_f23_blind_eod_cash_tally.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import timedelta
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from strict_fakes import StrictDB  # noqa: E402
from api.services import eod_tally as till  # noqa: E402
from api.utils.ist import ist_day_start_utc, ist_today  # noqa: E402


# Every field the expected-cash computation derives, spelled OUT here (never
# read from the implementation's own list -- a test that asks the code what it
# hides would go green the day the list was gutted).
WIRE_BLIND_FIELDS = (
    "expected_cash_paisa",
    "variance_paisa",
    "variance_status",
    "cash_sales_paisa",
    "cash_refunds_paisa",
    "cash_payouts_paisa",
    "cash_payouts_source",
    "by_mode",
    "tolerance_paisa",
    "negative_expected_advisory",
    "refund_double_entry_advisory",
    "off_till_expense_advisory",
    "variance_note",
)


# ---------------------------------------------------------------------------
# Fixtures -- same real-call-site pins as test_f23_blind_eod_cash_tally
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> StrictDB:
    return StrictDB()


@pytest.fixture(autouse=True)
def _pin_entity_resolver(monkeypatch):
    monkeypatch.setattr(
        "api.services.policy_engine._resolve_entity_id", lambda store_id: None
    )


@pytest.fixture(autouse=True)
def _pin_policy(monkeypatch):
    defaults = {
        "till.variance_tolerance_paisa": 0,
        "till.reopen_roles": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    }

    def _fake_get_policy(key, scope=None, *, default=None):
        return defaults.get(key, default)

    monkeypatch.setattr("api.services.policy_engine.get_policy", _fake_get_policy)


@pytest.fixture(autouse=True)
def _capture_audit(monkeypatch):
    rows: List[Dict[str, Any]] = []

    class _Repo:
        def create(self, data):
            rows.append(dict(data))
            return {"log_id": f"AUD-{len(rows)}"}

    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: _Repo())
    return rows


@pytest.fixture()
def route(db, monkeypatch):
    """The REAL router module wired to the strict fake DB + a store-scope that
    honours the actor's own store (cross-store still refused elsewhere)."""
    from api.routers import till as tillroute

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    monkeypatch.setattr(
        tillroute, "validate_store_access", lambda sid, u: sid or u.get("active_store_id")
    )
    return tillroute


def _cashier(uid="C1", store="BV-1"):
    return {
        "user_id": uid,
        "full_name": "Cashier One",
        "roles": ["SALES_CASHIER"],
        "store_ids": [store],
        "active_store_id": store,
    }


def _manager(uid="M1", store="BV-1"):
    return {
        "user_id": uid,
        "full_name": "Manager One",
        "roles": ["STORE_MANAGER"],
        "store_ids": [store],
        "active_store_id": store,
    }


TODAY = ist_today().isoformat()


def _seed_cash_order(db, rupees: float, order_id="O1", store_id="BV-1"):
    """A CASH order INSIDE today's IST day window (naive-UTC created_at, the
    frame the window matcher brackets)."""
    created = ist_day_start_utc(ist_today()) + timedelta(hours=6)
    db.get_collection("orders").insert_one(
        {
            "order_id": order_id,
            "store_id": store_id,
            "created_at": created,
            "payments": [
                {
                    "payment_id": f"PAY-{order_id}",
                    "method": "CASH",
                    "amount": rupees,
                    "received_by": "U1",
                    "received_at": created,
                    "idempotency_key": f"IK-{order_id}",
                }
            ],
        }
    )


def _open_as(route, actor, opening_paisa=10000):
    body = route.OpenSession(
        store_id="BV-1",
        session_date=TODAY,
        opening_denominations=[],
        opening_float_paisa=opening_paisa,
    )
    out = asyncio.run(route.open_till_session(body, current_user=actor))
    assert out["ok"] is True
    return out["session"]["session_id"]


def _list_as(route, actor):
    return asyncio.run(
        route.list_till_sessions(
            store_id="BV-1", date=None, status=None, limit=30, current_user=actor
        )
    )


# ---------------------------------------------------------------------------
# 1. The cashier can FIND the drawer (the defect itself)
# ---------------------------------------------------------------------------


def test_cashier_can_list_and_find_the_shared_drawer(db, route):
    """A SALES_CASHIER's session list is NON-EMPTY: the open drawer they are
    meant to count comes back through GET /till/sessions (it used to 403, which
    made the blind day-end unreachable by the people who count)."""
    sid = _open_as(route, _cashier())
    out = _list_as(route, _cashier())
    rows = out["sessions"]
    assert rows, "cashier got an EMPTY session list -- the blind count is unreachable again"
    assert rows[0]["session_id"] == sid
    assert rows[0]["status"] == "OPEN"


# ---------------------------------------------------------------------------
# 2. ...but the wire stays BLIND for them (response BODY, not just the screen)
# ---------------------------------------------------------------------------


def test_cashier_list_row_is_blind_on_the_wire_while_submitted(db, route):
    """After the count is submitted (expected/variance COMPUTED AND STORED),
    the cashier's list row must not carry a single derived figure -- absent
    keys, not nulled columns. The stored doc keeps the full truth for the
    manager's review."""
    _seed_cash_order(db, 800.0)
    sid = _open_as(route, _cashier(), opening_paisa=10000)
    body = route.BlindSubmit(
        blind_denominations=[{"face": 500, "pieces": 1}, {"face": 100, "pieces": 4}],
        blind_count_paisa=90000,
    )
    asyncio.run(route.submit_blind_count(sid, body, current_user=_cashier()))

    # The stored doc HAS the truth (guards against passing via not-computing).
    stored = db.get_collection("till_sessions").find_one({"_id": sid})
    assert stored["expected_cash_paisa"] == 90000  # 10000 float + 80000 sales
    assert stored["variance_paisa"] == 0

    row = next(
        r for r in _list_as(route, _cashier())["sessions"] if r["session_id"] == sid
    )
    assert row["status"] == "BLIND_SUBMITTED"
    for field in WIRE_BLIND_FIELDS:
        assert field not in row, f"cashier list row leaks {field} on the wire"
    assert row.get("expected_hidden") is True
    # Their OWN count and the float they declared still come back (the UI's
    # waiting panel shows the figure they submitted).
    assert row["blind_count_paisa"] == 90000
    assert row["opening_float_paisa"] == 10000


def test_advisory_tells_stripped_from_cashier_submit_response(db, route):
    """negative_expected_advisory literally discloses the SIGN of the expected
    figure. It (and the other derived advisories/payout leg) must not reach a
    cashier's blind-submit response or list row pre-lock."""
    created = ist_day_start_utc(ist_today()) + timedelta(hours=6)
    # A lone CASH refund and no sales: expected = 0 + 0 - 20000 = -20000.
    db.get_collection("orders").insert_one(
        {
            "order_id": "O-REF",
            "store_id": "BV-1",
            "created_at": created,
            "payments": [
                {
                    "payment_id": "PAY-REF",
                    "method": "CASH",
                    "amount": -200.0,
                    "received_by": "U1",
                    "received_at": created,
                    "idempotency_key": "IK-REF",
                }
            ],
        }
    )
    sid = _open_as(route, _cashier(), opening_paisa=0)
    body = route.BlindSubmit(
        blind_denominations=[{"face": 100, "pieces": 1}], blind_count_paisa=10000
    )
    out = asyncio.run(route.submit_blind_count(sid, body, current_user=_cashier()))

    stored = db.get_collection("till_sessions").find_one({"_id": sid})
    assert stored["negative_expected_advisory"] is True
    assert stored["variance_status"] == "NEGATIVE_EXPECTED"

    for payload in (out["session"], _list_as(route, _cashier())["sessions"][0]):
        for field in WIRE_BLIND_FIELDS:
            assert field not in payload, f"cashier payload leaks {field}"


# ---------------------------------------------------------------------------
# 3. End to end: count, submit, and no second bite at the cherry
# ---------------------------------------------------------------------------


def test_cashier_submit_end_to_end_then_double_submit_409(db, route):
    """The full cashier path: open -> list -> find the drawer -> submit. Then:
      * a SECOND submit (different figures, no idempotency key) is refused 409,
      * a RETRY with the same idempotency key returns the existing state and
        does NOT rewrite the count.
    A submitted count is immutable until a manager reopen -- otherwise the
    control proves nothing."""
    from fastapi import HTTPException

    _seed_cash_order(db, 500.0)
    _open_as(route, _cashier())
    # Discover the drawer the way the SCREEN does: via the list.
    row = _list_as(route, _cashier())["sessions"][0]
    sid = row["session_id"]

    first = route.BlindSubmit(
        blind_denominations=[{"face": 500, "pieces": 1}],
        blind_count_paisa=50000,
        idempotency_key=f"{sid}:blind",
    )
    out = asyncio.run(route.submit_blind_count(sid, first, current_user=_cashier()))
    assert out["ok"] is True

    # Second submit, different count, no key: refused.
    second = route.BlindSubmit(
        blind_denominations=[{"face": 100, "pieces": 1}], blind_count_paisa=10000
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.submit_blind_count(sid, second, current_user=_cashier()))
    assert exc.value.status_code == 409

    # Retry with the SAME key: idempotent echo, count unchanged.
    replay = route.BlindSubmit(
        blind_denominations=[{"face": 100, "pieces": 1}],
        blind_count_paisa=10000,
        idempotency_key=f"{sid}:blind",
    )
    echoed = asyncio.run(route.submit_blind_count(sid, replay, current_user=_cashier()))
    assert echoed.get("idempotent") is True

    stored = db.get_collection("till_sessions").find_one({"_id": sid})
    assert stored["blind_count_paisa"] == 50000, "a replay rewrote the submitted count"
    assert stored["status"] == "BLIND_SUBMITTED"


# ---------------------------------------------------------------------------
# 4. The manager's review AFTER submission (owner ruling 2026-09-03)
# ---------------------------------------------------------------------------


def test_manager_list_reveals_variance_after_submission(db, route):
    """The manager reviews expected vs counted AFTER the cashier submits: their
    list row carries the full figures. This is the deliberate divergence from
    the inventory stock count's role-blind withhold -- HERE the expected figure
    does not exist server-side until the moment of submission, and the reviewer
    is a different person from the counter by ruling, so redacting the manager
    would only blind the review the owner asked for."""
    _seed_cash_order(db, 700.0)
    sid = _open_as(route, _cashier(), opening_paisa=20000)
    body = route.BlindSubmit(
        blind_denominations=[{"face": 500, "pieces": 1}, {"face": 200, "pieces": 2}],
        blind_count_paisa=90000,
    )
    asyncio.run(route.submit_blind_count(sid, body, current_user=_cashier()))

    row = next(
        r for r in _list_as(route, _manager())["sessions"] if r["session_id"] == sid
    )
    # expected = 20000 float + 70000 sales = 90000; counted 90000 -> balanced.
    assert row["expected_cash_paisa"] == 90000
    assert row["blind_count_paisa"] == 90000
    assert row["variance_paisa"] == 0
    assert row["variance_status"] == "BALANCED"
    assert "expected_hidden" not in row


def test_open_session_carries_no_expected_to_anyone(db, route):
    """Pre-submission there is nothing to reveal: an OPEN session's stored doc
    has no computed expected (blind by construction), so even the manager's
    list shows None -- the manager's review genuinely starts AFTER submission."""
    _seed_cash_order(db, 999.0)
    sid = _open_as(route, _cashier())
    row = next(
        r for r in _list_as(route, _manager())["sessions"] if r["session_id"] == sid
    )
    assert row["status"] == "OPEN"
    assert row["expected_cash_paisa"] is None
    assert row["variance_paisa"] is None
