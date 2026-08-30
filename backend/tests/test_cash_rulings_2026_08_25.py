"""
IMS 2.0 - Owner cash rulings 2026-08-25: the deciding tests
===========================================================
Five rulings, each with a named test that dies ON THE REQUIREMENT:

  ONE Rs 100 BAND    till.variance_tolerance_paisa defaults to 10000 paisa,
                     store-scopable, and it is the ONLY band: the Finance
                     close reads it too (the closer-typed tolerance is gone
                     from the wire model outright -- delete the copy).
  MANDATORY NOTE     |variance| beyond the band refuses the blind lock (and
                     the Finance close, tested in test_cash_register.py) until
                     a written explanation is supplied; the note is stored.
  MANAGER ALERT      an out-of-band lock/close raises ONE SYSTEM task on the
                     store manager's worklist, deduped per (store, day) so the
                     two doors cannot double-alert one drawer-day.
  AUTO PAYOUTS       blind_submit pulls the payouts leg from the EXPENSES
                     BOOK (the same read the Finance close charges the drawer
                     with); a client-sent payouts figure is ignored, and a
                     payroll-shaped expense stays out and flags the session.

Runs the REAL eod_tally service + till router on the faithful fake Mongo from
the F23 suite (imported, not copied). Policy reads are pinned to EMPTY scope
docs so the REGISTRY DEFAULT is what is under test -- a fresh DB is exactly
the state the stores run in today.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import eod_tally as till  # noqa: E402
from api.services import policy_registry as reg  # noqa: E402

# The faithful fake Mongo + fixtures the F23 suite already proved out.
from tests.test_f23_blind_eod_cash_tally import (  # noqa: E402
    FakeDB,
    _cashier,
    _manager,
    _pay,
    _seed_order,
)


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture(autouse=True)
def _empty_policy_docs(monkeypatch):
    """No stored policy rows anywhere -> get_policy resolves to the REGISTRY
    default, which is precisely what these tests are about. (Prod has no
    override for this key either -- verified read-only 2026-08-30.)"""
    monkeypatch.setattr("api.services.policy_engine._scope_doc_values", lambda addr: {})
    monkeypatch.setattr("api.services.policy_engine._resolve_entity_id", lambda sid: None)


@pytest.fixture(autouse=True)
def _capture_audit(monkeypatch):
    rows: List[Dict[str, Any]] = []

    class _Repo:
        def create(self, data):
            rows.append(dict(data))
            return {"log_id": f"AUD-{len(rows)}"}

    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: _Repo())
    return rows


class _TaskRepo:
    """Just enough of TaskRepository for create_system_task: create + the
    source_ref dedupe read."""

    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def find_many(self, query):
        return [
            t for t in self.docs if all(t.get(k) == v for k, v in (query or {}).items())
        ]

    def create(self, task):
        self.docs.append(dict(task))
        return dict(task)


@pytest.fixture()
def task_repo(monkeypatch) -> _TaskRepo:
    repo = _TaskRepo()
    monkeypatch.setattr("api.dependencies.get_task_repository", lambda: repo)
    return repo


def _submitted_session(db, *, counted_paisa: int, sales_rupees: float = 1000.0):
    """Open + blind-submit one BV-1 session for 2026-06-09 with a CASH sale of
    ``sales_rupees`` and a count of ``counted_paisa``. Opening float 0, no
    expenses -> expected == sales, variance == counted - expected."""
    _seed_order(db, order_id="O-R1", store_id="BV-1", payments=[_pay("CASH", sales_rupees)])
    opened = till.open_session(
        db, store_id="BV-1", session_date="2026-06-09", opening_float_paisa=0, actor=_cashier()
    )
    sid = opened["session"]["session_id"]
    res = till.blind_submit(
        db, sid, blind_count_paisa=counted_paisa,
        blind_denominations=[{"face": 1, "kind": "coin", "pieces": counted_paisa // 100}],
        actor=_cashier())
    assert res["ok"] is True, res
    return sid


# ===========================================================================
# Ruling: ONE Rs 100 band
# ===========================================================================


def test_the_band_default_is_rs_100_and_store_scopable():
    spec = reg.REGISTRY.get("till.variance_tolerance_paisa")
    assert spec is not None
    # Rs 100 = 10000 paisa (owner ruling 2026-08-25). Reverting the registry
    # default to 0 (exact-match) kills this line.
    assert spec.default == 10000
    # The owner can still change it per store in Settings > Cash Register.
    assert "store" in spec.scopes and "global" in spec.scopes
    # And the layered read lands on that default on a fresh DB.
    assert till.get_variance_tolerance_paisa(store_id="BV-1") == 10000


def test_a_rs_50_gap_is_balanced_inside_the_default_band(db, task_repo):
    """The exact reproduction from the launch audit (probe P2e): a Rs 50 gap
    used to flag SHORTAGE because the shipped band was 0. Inside the owner's
    Rs 100 band it is BALANCED, locks with no note, and alerts nobody."""
    sid = _submitted_session(db, counted_paisa=100000 - 5000)  # Rs 50 short
    stored = db.get_collection("till_sessions").find_one({"_id": sid})
    assert stored["variance_paisa"] == -5000
    assert stored["variance_status"] == "BALANCED"
    assert stored["tolerance_paisa"] == 10000
    res = till.lock_session(db, sid, actor=_manager())  # no note needed
    assert res["ok"] is True
    assert task_repo.docs == []  # in-band day alerts nobody


def test_the_finance_close_wire_model_has_no_tolerance_field():
    """DELETE THE COPY: the closer-typed band is gone from the wire outright.
    Re-adding the field (even defaulted) resurrects the second implementation
    the 2026-08-25 ruling banned -- see test_cash_register.py for the
    behavioural half (a client-sent tolerance is ignored by the route)."""
    from api.routers.finance import CashRegisterClose

    # pylint: disable-next=unsupported-membership-test  # model_fields IS a dict
    assert "tolerance" not in CashRegisterClose.model_fields


# ===========================================================================
# Ruling: MANDATORY NOTE above the band (blind lock leg; the Finance-close
# leg lives in test_cash_register.py::test_close_detects_short_and_over)
# ===========================================================================


def test_an_out_of_band_lock_without_a_note_is_refused(db, task_repo):
    sid = _submitted_session(db, counted_paisa=100000 - 20000)  # Rs 200 short
    res = till.lock_session(db, sid, actor=_manager())
    assert res["ok"] is False
    assert res["error"] == "variance_note_required"
    assert res["http"] == 400
    # The day is NOT locked and nobody was alerted for a lock that never was.
    stored = db.get_collection("till_sessions").find_one({"_id": sid})
    assert stored["status"] == "BLIND_SUBMITTED"
    assert task_repo.docs == []
    # A blank-string note is the same as no note.
    res2 = till.lock_session(db, sid, actor=_manager(), variance_note="   ")
    assert res2["ok"] is False and res2["error"] == "variance_note_required"


def test_an_out_of_band_lock_with_a_note_locks_and_stores_it(db, task_repo, _capture_audit):
    sid = _submitted_session(db, counted_paisa=100000 - 20000)
    res = till.lock_session(
        db, sid, actor=_manager(), variance_note="Rs 200 change lent to the counter"
    )
    assert res["ok"] is True
    assert res["session"]["variance_note"] == "Rs 200 change lent to the counter"
    # The immutable trail carries the explanation too.
    lock_rows = [r for r in _capture_audit if r["action"] == "till.lock"]
    assert len(lock_rows) == 1
    assert lock_rows[0]["after_state"]["variance_note"] == "Rs 200 change lent to the counter"
    # And the Z-Read prints it.
    z = till.build_zread(db, sid)
    assert z["variance_note"] == "Rs 200 change lent to the counter"


def test_the_lock_route_translates_the_refusal_to_a_400(db, monkeypatch):
    """Through the REAL router: an out-of-band lock with an empty body is a
    400, not a 500 and not a silent lock."""
    import asyncio

    from fastapi import HTTPException

    from api.routers import till as tillroute

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    monkeypatch.setattr(
        tillroute, "validate_store_access", lambda sid, u: sid or u.get("active_store_id")
    )
    sid = _submitted_session(db, counted_paisa=100000 - 20000)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tillroute.lock_till_session(sid, body=None, current_user=_manager()))
    assert exc.value.status_code == 400
    assert "variance_note_required" in str(exc.value.detail)
    # With the note in the body the same route locks.
    out = asyncio.run(
        tillroute.lock_till_session(
            sid,
            body=tillroute.LockBody(variance_note="counted twice, Rs 200 short stands"),
            current_user=_manager(),
        )
    )
    assert out["ok"] is True
    assert out["session"]["status"] == "LOCKED"


# ===========================================================================
# Ruling: MANAGER ALERT above the band
# ===========================================================================


def test_an_out_of_band_lock_raises_one_store_manager_task(db, task_repo):
    sid = _submitted_session(db, counted_paisa=100000 - 20000)
    res = till.lock_session(db, sid, actor=_manager(), variance_note="till short, note attached")
    assert res["ok"] is True
    assert len(task_repo.docs) == 1
    task = task_repo.docs[0]
    assert task["source"] == "SYSTEM"
    assert task["assigned_to"] == "STORE_MANAGER"
    assert task["store_id"] == "BV-1"
    assert task["source_ref"] == "till_variance:BV-1:2026-06-09"
    assert "SHORT" in task["title"]
    assert "200.00" in task["title"]
    assert "till short, note attached" in task["description"]


def test_reopen_and_relock_does_not_double_alert_the_same_day(db, task_repo):
    sid = _submitted_session(db, counted_paisa=100000 - 20000)
    till.lock_session(db, sid, actor=_manager(), variance_note="first lock")
    assert len(task_repo.docs) == 1
    r = till.reopen_session(db, sid, reason="recount requested", actor=_manager())
    assert r["ok"] is True
    till.lock_session(db, sid, actor=_manager(), variance_note="second lock, same short")
    # Same (store, day) -> the ACTIVE task dedupes; one drawer-day, one alert.
    assert len(task_repo.docs) == 1


# The FINANCE-door legs of the note + alert rulings are behavioural tests in
# test_cash_register.py::TestCashRegisterEndpoints::test_close_detects_short_and_over
# (400 variance_note_required without a note; the STORE_MANAGER task after the
# noted close) -- they drive the real /finance/cash-register/close route.


# ===========================================================================
# Ruling: AUTO PAYOUTS (blind is THE close -- no hand-typed payouts box)
# ===========================================================================


def test_blind_submit_ignores_a_client_sent_payouts_figure(db, monkeypatch):
    """Through the REAL router: the wire model no longer carries
    cash_payouts_paisa, so a legacy client sending one changes NOTHING -- the
    stored payouts leg is the expenses-book figure."""
    import asyncio

    from api.routers import till as tillroute

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    monkeypatch.setattr(
        tillroute, "validate_store_access", lambda sid, u: sid or u.get("active_store_id")
    )
    _seed_order(db, order_id="O-R2", store_id="BV-1", payments=[_pay("CASH", 1000.0)])
    db.get_collection("expenses").insert_one({
        "expense_id": "EXP-R1", "store_id": "BV-1", "amount": 40.0,
        "payment_mode": "CASH", "status": "PAID",
        "expense_date": "2026-06-09", "category": "Courier",
    })
    opened = till.open_session(
        db, store_id="BV-1", session_date="2026-06-09", opening_float_paisa=0, actor=_cashier()
    )
    sid = opened["session"]["session_id"]
    body = tillroute.BlindSubmit(**{
        "blind_denominations": [{"face": 1, "kind": "coin", "pieces": 960}],
        "blind_count_paisa": 96000,
        "cash_payouts_paisa": 999999,  # legacy client field: silently dropped
    })
    asyncio.run(tillroute.submit_blind_count(sid, body, current_user=_cashier()))
    stored = db.get_collection("till_sessions").find_one({"_id": sid})
    # Payouts = Rs 40 from the expenses book, NOT the hand-typed 999999.
    assert stored["cash_payouts_paisa"] == 4000
    assert stored["cash_payouts_source"] == "AUTO_EXPENSES"
    # expected = 0 + 100000 - 4000 = 96000 -> the count of 96000 balances.
    assert stored["expected_cash_paisa"] == 96000
    assert stored["variance_paisa"] == 0


def test_a_payroll_shaped_expense_stays_out_and_flags_the_session(db):
    """Salaries never leave a shop till (owner 2026-08-14): the auto-pull must
    exclude them AND say so on the session, or an expected figure that quietly
    leaves something out gets 'corrected' by an honest counter."""
    _seed_order(db, order_id="O-R3", store_id="BV-1", payments=[_pay("CASH", 1000.0)])
    coll = db.get_collection("expenses")
    coll.insert_one({
        "expense_id": "EXP-R2", "store_id": "BV-1", "amount": 30.0,
        "payment_mode": "CASH", "status": "APPROVED",
        "expense_date": "2026-06-09", "category": "Stationery",
    })
    coll.insert_one({
        "expense_id": "EXP-R3", "store_id": "BV-1", "amount": 5000.0,
        "payment_mode": "CASH", "status": "APPROVED",
        "expense_date": "2026-06-09", "category": "Staff Salary",
    })
    opened = till.open_session(
        db, store_id="BV-1", session_date="2026-06-09", opening_float_paisa=0, actor=_cashier()
    )
    sid = opened["session"]["session_id"]
    till.blind_submit(db, sid, blind_count_paisa=97000,
                      blind_denominations=[{"face": 1, "kind": "coin", "pieces": 970}],
                      actor=_cashier())
    stored = db.get_collection("till_sessions").find_one({"_id": sid})
    assert stored["cash_payouts_paisa"] == 3000  # only the Rs 30 stationery
    assert stored["off_till_expense_advisory"] is True
    # expected = 100000 - 3000; the Rs 5000 salary never touched the drawer.
    assert stored["expected_cash_paisa"] == 97000


def test_the_blind_submit_wire_model_has_no_payouts_field():
    """DELETE THE COPY: the hand-typed payouts box's wire field is gone."""
    from api.routers.till import BlindSubmit

    # pylint: disable-next=unsupported-membership-test  # model_fields IS a dict
    assert "cash_payouts_paisa" not in BlindSubmit.model_fields
