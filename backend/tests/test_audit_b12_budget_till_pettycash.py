"""
IMS 2.0 -- Codex audit batch b12 (budget, till, petty cash)
===========================================================
Regression tests for the server side of four audit findings. Every test drives
the REAL service or route handler; only the database handle is an in-memory
fake (the same fakes the till and finance suites already use).

  L3-12  Day-End Report accepted a second counted amount for a till that the
         blind count had already LOCKED, and stored it. Two screens, two
         counts, one drawer. The till count is the close (owner ruling
         2026-08-25): the Day-End record now carries THAT count and says so.
  L3-15  Budgeting saved a plan that the Finance Dashboard could not see: the
         dashboard's reader looked for a document shape nobody writes. One
         writer, one reader now.
  L3-10  A petty-cash float that could not be READ was reported as a float
         that does not exist ("no float is open"). Unreadable is not absent.
  L3-28  Reopening a locked Z-Read while another drawer is open for the same
         shop-day answered 500 with the database's duplicate-key text. It is a
         plain conflict: 409 with a sentence staff can act on.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_audit_b12_budget_till_pettycash.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from fastapi import HTTPException  # noqa: E402
from pymongo.errors import DuplicateKeyError  # noqa: E402

# REUSE the till suite's faithful in-memory Mongo -- never a second copy.
from test_f23_blind_eod_cash_tally import FakeCollection, FakeDB  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

from api.routers import budgets as budgets_router  # noqa: E402
from api.routers import finance as finance_router  # noqa: E402
from api.routers import reports as reports_router  # noqa: E402
from api.routers import till as till_router  # noqa: E402
from api.services import eod_tally as till  # noqa: E402
from api.services import petty_cash_service as pcs  # noqa: E402

STORE = "BV-PUN-01"
OTHER_STORE = "BV-RAN-01"
DATE = "2026-11-04"

MANAGER = {
    "user_id": "u-mgr",
    "username": "mgr",
    "full_name": "Priya",
    "roles": ["STORE_MANAGER"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}
CASHIER = {
    "user_id": "u-cash",
    "full_name": "Ravi",
    "roles": ["CASHIER"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}
ADMIN = {
    "user_id": "u-admin",
    "username": "admin",
    "roles": ["ADMIN"],
    "store_ids": [STORE, OTHER_STORE],
    "active_store_id": STORE,
}


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture(autouse=True)
def _pin_environment(monkeypatch):
    """Pin the reads the till engine makes outside its own collection, at their
    real call sites. The subjects themselves are never patched."""
    monkeypatch.setattr(
        "api.services.policy_engine._resolve_entity_id", lambda store_id: None
    )
    defaults = {
        "till.variance_tolerance_paisa": 0,
        "till.reopen_roles": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    }
    monkeypatch.setattr(
        "api.services.policy_engine.get_policy",
        lambda key, scope=None, *, default=None: defaults.get(key, default),
    )


class _AuditStore:
    """The audit store as the services meet it. A row that names its own
    ``log_id`` is stored once: a second create of that id is refused, as the
    real collection's _id does. ``up`` False = the store cannot be written
    (create answers None, as the real one does); ``stop`` = raise that once
    instead of writing (the process stopping right there)."""

    def __init__(self, rows, up=lambda: True, stop=None):
        self.rows, self.up, self.stop = rows, up, stop
        self.collection = self

    def find_one(self, query, *a, **k):
        key = (query or {}).get("log_id")
        return next((r for r in self.rows if key and r.get("log_id") == key), None)

    def create(self, data):
        if self.stop:
            raise self.stop.pop()
        if not self.up() or self.find_one(data):
            return None
        self.rows.append(dict(data))
        return {"log_id": data.get("log_id") or f"AUD-{len(self.rows)}"}


@pytest.fixture(autouse=True)
def audit_rows(monkeypatch) -> List[Dict[str, Any]]:
    """Capture audit writes at the accessor the services use. Autouse, so no
    test here can write an audit row into a real database."""
    rows: List[Dict[str, Any]] = []
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: _AuditStore(rows))
    monkeypatch.setattr(reports_router, "get_audit_repository", lambda: _AuditStore(rows))
    return rows


# ===========================================================================
# L3-12  Day-End Report vs a till the blind count already closed
# ===========================================================================


@pytest.fixture()
def day_end(monkeypatch, db, audit_rows):
    monkeypatch.setattr(reports_router, "get_db", lambda: db)
    monkeypatch.setattr(
        "api.services.stores_util.is_online_store", lambda _db, _sid: False
    )

    def _close(**body_kw):
        body = reports_router.DayEndCloseBody(date=DATE, store_id=STORE, **body_kw)
        return asyncio.run(
            reports_router.create_day_end_close(body, current_user=MANAGER)
        )

    return _close


def _blind_count(db, *, rupees: int, lock: bool) -> str:
    """The cashier counts the drawer blind; the manager (optionally) locks it.
    This is the audit's starting state: a FULL blind till, counted Rs 1,000."""
    opened = till.open_session(
        db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER
    )
    sid = opened["session"]["session_id"]
    res = till.blind_submit(
        db,
        sid,
        blind_denominations=[{"face": 500, "kind": "note", "pieces": rupees // 500}],
        actor=CASHIER,
    )
    assert res["ok"], res
    if lock:
        locked = till.lock_session(
            db, sid, actor=MANAGER, variance_note="b12 audit fixture - variance explained"
        )
        assert locked["ok"], locked
    return sid


def _stored_close(db) -> Dict[str, Any]:
    rows = list(db.get_collection("day_end_closes").find({}))
    assert len(rows) == 1, rows
    return rows[0]


def _till(db, sid) -> Dict[str, Any]:
    return db.get_collection("till_sessions").find_one({"_id": sid})


class TestL312DayEndCannotRestateALockedTill:
    def test_the_audit_reproduction_locked_1000_then_day_end_3000(self, db, day_end):
        """THE REPRODUCTION. Till LOCKED at Rs 1,000; Day-End Report then sends
        Rs 3,000 for the same shop-day. The stored Day-End record used to hold
        3,000 while the Z-Read held 1,000."""
        sid = _blind_count(db, rupees=1000, lock=True)

        res = day_end(closing_cash=3000.0, system_cash=4000.0)
        close = res["close"]

        # The till count is THE count -- on the reply ...
        assert close["closing_cash"] == 1000.0
        assert close["variance"] == -3000.0  # 1,000 counted - 4,000 system
        # ... and the screen is TOLD its own figure was not used.
        assert close["counted_from_shared_record"] is True
        assert close["till_count_differs"] is True
        assert close["till_already_counted"] is True
        assert close["till_session_id"] == sid

        # ... on the STORED row (what a reload reads) ...
        stored = _stored_close(db)
        assert stored["closing_cash"] == 1000.0
        assert stored["variance"] == -3000.0
        assert stored["counted_from_shared_record"] is True
        assert stored["closing_count"]["amount_paisa"] == 100000
        # The figure that was typed is kept as evidence, not as the count.
        assert stored["entered_closing_cash"] == 3000.0

        # ... and on the read the screen makes after a reload.
        reread = asyncio.run(
            reports_router.get_day_end_close(
                date=DATE, store_id=STORE, current_user=MANAGER
            )
        )
        assert reread["closed"] is True
        assert reread["close"]["closing_cash"] == 1000.0

        # The locked till is untouched: same count, still locked, one drawer.
        session = _till(db, sid)
        assert session["blind_count_paisa"] == 100000
        assert session["status"] == till.STATUS_LOCKED
        assert len(list(db.get_collection("till_sessions").find({}))) == 1

    def test_a_submitted_but_unlocked_till_count_stands_too(self, db, day_end):
        sid = _blind_count(db, rupees=1000, lock=False)
        close = day_end(closing_cash=3000.0, system_cash=1000.0)["close"]
        assert close["closing_cash"] == 1000.0
        assert close["variance"] == 0.0
        assert close["counted_from_shared_record"] is True
        assert _till(db, sid)["blind_count_paisa"] == 100000

    def test_a_day_end_with_no_count_reports_the_till_count_not_uncounted(
        self, db, day_end
    ):
        """The drawer WAS counted (at the till). A Day-End closed without typing
        a figure must not record the day as 'cash was not counted'."""
        _blind_count(db, rupees=1000, lock=True)
        close = day_end(system_cash=1000.0)["close"]
        assert close["closing_cash"] == 1000.0
        assert close["variance"] == 0.0
        assert close["counted_from_shared_record"] is True
        assert close["entered_closing_cash"] is None  # nothing was typed

    def test_the_same_figure_typed_again_is_not_flagged(self, db, day_end):
        _blind_count(db, rupees=1000, lock=True)
        close = day_end(closing_cash=1000.0, system_cash=1000.0)["close"]
        assert close["closing_cash"] == 1000.0
        assert close["counted_from_shared_record"] is False
        assert close["till_count_differs"] is False

    def test_a_day_end_that_counts_first_still_owns_its_count(self, db, day_end):
        """POSITIVE CONTROL: nothing was counted before, so this screen's
        figure IS the count -- on its own record and on the shared till."""
        close = day_end(closing_cash=3000.0, system_cash=3000.0)["close"]
        assert close["closing_cash"] == 3000.0
        assert close["variance"] == 0.0
        assert close["counted_from_shared_record"] is False
        assert close["till_count_differs"] is False
        assert close["entered_closing_cash"] is None
        sessions = list(db.get_collection("till_sessions").find({}))
        assert len(sessions) == 1
        assert sessions[0]["blind_count_paisa"] == 300000

    def test_a_day_end_that_counts_first_with_a_grid_and_a_different_typed_total(
        self, db, day_end
    ):
        """REVIEW MUST-FIX (L3-12, other order of events). Nothing counted yet;
        the Day-End screen sends a typed Rs 3,000 AND a grid of one Rs 500 note
        (the screen sends both when a typed total sits beside a half-filled
        grid). The till's own rule lets the notes rule, so the shared session
        records Rs 500. This record used to keep Rs 3,000 with
        till_count_differs False: Z-Read short Rs 2,500, Day-End 'matches'."""
        close = day_end(
            closing_cash=3000.0,
            system_cash=3000.0,
            closing_count={
                "state": "COUNTED",
                "rows": [{"face": 500, "kind": "note", "pieces": 1}],
            },
        )["close"]
        sessions = list(db.get_collection("till_sessions").find({}))
        assert len(sessions) == 1
        till_paisa = sessions[0]["blind_count_paisa"]
        assert till_paisa == 50000  # the till took the notes

        # ONE counted figure for the drawer: the Day-End record matches the till.
        assert close["closing_cash"] == 500.0
        assert close["variance"] == -2500.0
        assert close["counted_from_shared_record"] is True
        assert close["till_count_differs"] is True
        assert close["till_already_counted"] is False  # this screen counted first
        assert close["entered_closing_cash"] == 3000.0  # kept as evidence
        stored = _stored_close(db)
        assert stored["closing_cash"] * 100 == till_paisa
        assert stored["closing_count"]["amount_paisa"] == till_paisa
        assert stored["till_count_differs"] is True

    def test_the_audit_row_carries_the_count_that_stands(
        self, db, day_end, audit_rows
    ):
        _blind_count(db, rupees=1000, lock=True)
        day_end(closing_cash=3000.0, system_cash=4000.0)
        closed = [r for r in audit_rows if r.get("action") == "DAY_END_CLOSED"]
        assert len(closed) == 1
        assert closed[0]["details"]["closing_cash"] == 1000.0
        assert closed[0]["details"]["variance"] == -3000.0
        assert closed[0]["details"]["counted_from_shared_record"] is True
        assert closed[0]["details"]["entered_closing_cash"] == 3000.0
        assert closed[0]["severity"] == "WARNING"

    def test_a_lost_race_to_count_still_reports_the_count_that_won(
        self, db, day_end, monkeypatch
    ):
        """Both doors read the session OPEN; the other door's count lands first.
        This door's submit is refused (already_submitted) -- it must then report
        the count that won, not its own."""
        opened = till.open_session(
            db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER
        )
        sid = opened["session"]["session_id"]
        real_submit = till.blind_submit

        def _other_door_wins(dbh, session_id, **kw):
            real_submit(
                dbh,
                session_id,
                blind_denominations=[{"face": 500, "kind": "note", "pieces": 2}],
                actor=CASHIER,
            )
            return real_submit(dbh, session_id, **kw)

        monkeypatch.setattr(till, "blind_submit", _other_door_wins)
        close = day_end(closing_cash=3000.0, system_cash=1000.0)["close"]
        assert close["till_already_counted"] is True
        assert close["closing_cash"] == 1000.0
        assert close["counted_from_shared_record"] is True
        assert _till(db, sid)["blind_count_paisa"] == 100000


# ===========================================================================
# L3-28  Reopen while another drawer is open for the same shop-day
# ===========================================================================


class _UniqueActiveDrawer(FakeCollection):
    """The till collection WITH its real constraint: the partial unique index
    ``uniq_active_till_per_store_day`` (ensure_till_indexes) -- at most one
    OPEN / BLIND_SUBMITTED session per (store, date). The plain fake has no
    indexes, so without this the conflict cannot happen in memory at all."""

    _ACTIVE = (till.STATUS_OPEN, till.STATUS_BLIND_SUBMITTED)

    def find_one_and_update(self, query, update, return_document=None, upsert=False, **kw):
        new_status = (update.get("$set") or {}).get("status")
        if new_status in self._ACTIVE:
            target = self.find_one(query)
            if target is not None:
                for other in self.docs:
                    if (
                        other.get("_id") != target.get("_id")
                        and other.get("store_id") == target.get("store_id")
                        and other.get("session_date") == target.get("session_date")
                        and other.get("status") in self._ACTIVE
                    ):
                        raise DuplicateKeyError(
                            "E11000 duplicate key error collection: ims.till_sessions "
                            "index: uniq_active_till_per_store_day dup key: "
                            '{ store_id: "%s", session_date: "%s" }'
                            % (target.get("store_id"), target.get("session_date"))
                        )
        return super().find_one_and_update(
            query, update, return_document=return_document, upsert=upsert, **kw
        )


@pytest.fixture()
def indexed_db() -> FakeDB:
    fake = FakeDB()
    fake._collections["till_sessions"] = _UniqueActiveDrawer(database=fake)
    return fake


def _locked_then_second_drawer(db):
    """The audit's state: the day's first drawer is LOCKED, then a second (PM)
    drawer was opened for the same shop-day and is still active."""
    locked_sid = _blind_count(db, rupees=1000, lock=True)
    second = till.open_session(
        db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
        shift="PM", actor=CASHIER,
    )
    assert second["ok"] and not second.get("already_open"), second
    return locked_sid, second["session"]["session_id"]


_LEAKS = ("E11000", "duplicate key", "dup key", "uniq_active_till", "collection:")


class TestL328ReopenConflictIsAPlainConflict:
    def test_the_service_answers_a_stable_409_not_a_database_500(
        self, indexed_db, audit_rows
    ):
        locked_sid, open_sid = _locked_then_second_drawer(indexed_db)
        before = dict(_till(indexed_db, locked_sid))

        res = till.reopen_session(
            indexed_db, locked_sid, reason="recount the morning drawer", actor=MANAGER
        )

        assert res["ok"] is False
        assert res["http"] == 409
        assert res["error"] == "another_drawer_open"
        # Names the drawer in the way and the next step, in plain words.
        assert res["active_session_id"] == open_sid
        assert "lock" in res["message"].lower()
        for leak in _LEAKS:
            assert leak not in res["message"] and leak not in res["error"]

        # Nothing was reopened, nothing claims it was.
        after = _till(indexed_db, locked_sid)
        assert after["status"] == till.STATUS_LOCKED
        assert after.get("reopen_count") == before.get("reopen_count")
        assert after.get("history") == before.get("history")
        assert after.get("locked_at") == before.get("locked_at")
        assert not [r for r in audit_rows if r.get("action") == "till.reopen"]
        # The single-active-drawer rule held: still exactly one active drawer.
        active = [
            d for d in indexed_db.get_collection("till_sessions").find({})
            if d.get("status") in (till.STATUS_OPEN, till.STATUS_BLIND_SUBMITTED)
        ]
        assert [d["_id"] for d in active] == [open_sid]

    def test_the_route_returns_409_with_a_message_for_staff(
        self, indexed_db, monkeypatch
    ):
        locked_sid, open_sid = _locked_then_second_drawer(indexed_db)
        monkeypatch.setattr(till_router, "_get_db", lambda: indexed_db)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                till_router.reopen_till_session(
                    locked_sid,
                    till_router.ReopenBody(reason="recount"),
                    current_user=MANAGER,
                )
            )
        assert exc.value.status_code == 409
        detail = exc.value.detail
        assert isinstance(detail, dict), detail
        assert detail["code"] == "another_drawer_open"
        assert detail["active_session_id"] == open_sid
        assert isinstance(detail["message"], str) and detail["message"]
        for leak in _LEAKS:
            assert leak not in str(detail)

    def test_reopen_works_once_the_other_drawer_is_locked(self, indexed_db):
        """POSITIVE CONTROL: the conflict is the only thing refused."""
        locked_sid, open_sid = _locked_then_second_drawer(indexed_db)
        till.blind_submit(
            indexed_db, open_sid,
            blind_denominations=[{"face": 500, "kind": "note", "pieces": 1}],
            actor=CASHIER,
        )
        assert till.lock_session(
            indexed_db, open_sid, actor=MANAGER, variance_note="b12 fixture"
        )["ok"]

        res = till.reopen_session(
            indexed_db, locked_sid, reason="recount", actor=MANAGER
        )
        assert res["ok"] is True
        assert res["session"]["status"] == till.STATUS_BLIND_SUBMITTED
        assert res["session"]["reopen_count"] == 1

    def test_an_unknown_database_failure_does_not_leak_its_text(
        self, db, monkeypatch
    ):
        """Any other write failure stays a 500 -- but never with the driver's
        message in the reply."""
        sid = _blind_count(db, rupees=1000, lock=True)
        coll = db.get_collection("till_sessions")

        def _boom(*a, **k):
            raise RuntimeError("connection pool 10.0.3.7:27017 reset by peer")

        monkeypatch.setattr(coll, "find_one_and_update", _boom)
        res = till.reopen_session(db, sid, reason="recount", actor=MANAGER)
        assert res["ok"] is False and res["http"] == 500
        assert res["error"] == "reopen_failed"
        assert "10.0.3.7" not in str(res)

    def test_existing_string_errors_keep_their_shape_on_the_wire(self, db, monkeypatch):
        """The route's other refusals are unchanged: a bare code string."""
        sid = _blind_count(db, rupees=1000, lock=False)  # not LOCKED
        monkeypatch.setattr(till_router, "_get_db", lambda: db)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                till_router.reopen_till_session(
                    sid, till_router.ReopenBody(reason="x"), current_user=MANAGER
                )
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "not_locked"


# ===========================================================================
# D40  Two drawers on one shop-day: both close doors act on the OPEN drawer
# ===========================================================================


def _cr_close_two_drawers(db, monkeypatch, **body_kw):
    """Drive Finance > Cash Register's real close handler for DATE."""
    monkeypatch.setattr(finance_router, "_get_db", lambda: db)
    monkeypatch.setattr(finance_router, "_iso_now", lambda: f"{DATE}T12:00:00")
    db.get_collection("cash_register_sessions").insert_one(
        {
            "_id": "CR-D40",
            "session_id": "CR-D40",
            "store_id": STORE,
            "status": "OPEN",
            "opening_float": 0.0,
            "opening_denominations": [],
            "opened_at": f"{DATE}T04:00:00",
        }
    )
    body = finance_router.CashRegisterClose(session_id="CR-D40", **body_kw)
    return asyncio.run(
        finance_router.close_cash_register(body=body, current_user=MANAGER)
    )


class TestD40TwoDrawersCloseTheOpenOne:
    """The reviewer's sequence: drawer A blind-counted Rs 1,000 and LOCKED,
    then drawer B opened for the same shop-day (allowed -- the open joins only
    an OPEN / BLIND_SUBMITTED drawer). The day is then closed with Rs 3,000.
    The shared lookup used to return locked A: the close said 'already
    counted', B stayed OPEN and uncounted, and the Day-End record stored A's
    Rs 1,000 with variance -2,000 instead of the Rs 3,000 just counted."""

    def test_day_end_counts_the_open_drawer_not_the_locked_one(self, db, day_end):
        locked_sid, open_sid = _locked_then_second_drawer(db)

        close = day_end(closing_cash=3000.0, system_cash=3000.0)["close"]

        assert close["till_session_id"] == open_sid
        assert close["till_already_counted"] is False
        assert close["closing_cash"] == 3000.0
        assert close["variance"] is None  # D53: two drawers, no whole-day variance
        assert close["counted_from_shared_record"] is False
        assert close["till_count_differs"] is False
        assert close["entered_closing_cash"] is None
        stored = _stored_close(db)
        assert (stored["closing_cash"], stored["variance"]) == (3000.0, None)
        assert stored["till_session_id"] == open_sid
        # B is counted; A is untouched; no third drawer.
        b, a = _till(db, open_sid), _till(db, locked_sid)
        assert (b["status"], b["blind_count_paisa"]) == (till.STATUS_BLIND_SUBMITTED, 300000)
        assert (a["status"], a["blind_count_paisa"]) == (till.STATUS_LOCKED, 100000)
        assert len(list(db.get_collection("till_sessions").find({}))) == 2

    def test_an_identical_retry_writes_nothing_new(self, db, day_end):
        locked_sid, open_sid = _locked_then_second_drawer(db)
        day_end(closing_cash=3000.0, system_cash=3000.0)
        before = [dict(d) for d in db.get_collection("till_sessions").find({})]

        with pytest.raises(HTTPException) as exc:
            day_end(closing_cash=3000.0, system_cash=3000.0)
        assert exc.value.status_code == 409
        assert exc.value.detail["close"]["closing_cash"] == 3000.0
        assert _stored_close(db)["closing_cash"] == 3000.0  # still one row
        assert [dict(d) for d in db.get_collection("till_sessions").find({})] == before

    def test_a_lost_acknowledgement_retry_adopts_the_open_drawer_count(
        self, db, day_end, monkeypatch
    ):
        """The till count landed on B but the Day-End row was not written. The
        retry must find B (now BLIND_SUBMITTED), not locked A."""
        locked_sid, open_sid = _locked_then_second_drawer(db)
        closes = db.get_collection("day_end_closes")
        real_insert = closes.insert_one
        calls = {"n": 0}

        def _fail_once(doc, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("write acknowledgement lost")
            return real_insert(doc, *a, **k)

        monkeypatch.setattr(closes, "insert_one", _fail_once)
        with pytest.raises(HTTPException) as exc:
            day_end(closing_cash=3000.0, system_cash=3000.0)
        assert exc.value.status_code == 500
        assert _till(db, open_sid)["blind_count_paisa"] == 300000

        close = day_end(closing_cash=3000.0, system_cash=3000.0)["close"]
        assert close["till_session_id"] == open_sid
        assert close["till_already_counted"] is True
        assert (close["closing_cash"], close["variance"]) == (3000.0, None)
        assert close["counted_from_shared_record"] is False
        assert _till(db, locked_sid)["blind_count_paisa"] == 100000

    def test_cash_register_counts_the_open_drawer_not_the_locked_one(
        self, db, monkeypatch
    ):
        locked_sid, open_sid = _locked_then_second_drawer(db)

        out = _cr_close_two_drawers(
            db, monkeypatch,
            denominations=[{"face": 500, "kind": "note", "pieces": 6}],
            closing_count_state="COUNTED",
            note="d40 fixture - opening float not declared",
        )

        assert out["counted"] == 3000.0
        assert out["counted_from_shared_record"] is False
        stored = db.get_collection("cash_register_sessions").find_one({"_id": "CR-D40"})
        assert stored["till_session_id"] == open_sid
        assert stored["till_already_counted"] is False
        b, a = _till(db, open_sid), _till(db, locked_sid)
        assert (b["status"], b["blind_count_paisa"]) == (till.STATUS_BLIND_SUBMITTED, 300000)
        assert (a["status"], a["blind_count_paisa"]) == (till.STATUS_LOCKED, 100000)

    def test_control_a_day_whose_drawers_are_all_locked_is_still_already_counted(
        self, db
    ):
        """CONTROL: no open drawer -> the lookup still finds a locked one, says
        'already counted', and never opens another drawer."""
        sid = _blind_count(db, rupees=1000, lock=True)
        link = till.record_screen_close(
            db, store_id=STORE, session_date=DATE, counted_paisa=300000, actor=MANAGER
        )
        assert link["ok"] and link["already_counted"] is True
        assert link["session_id"] == sid
        assert till.shared_counted_paisa(link) == 100000
        assert link["drawers_on_day"] == 1
        assert len(list(db.get_collection("till_sessions").find({}))) == 1


def _lock_open_drawer(db, sid, *, rupees: int) -> None:
    """Count drawer ``sid`` blind (unless already counted) and lock it."""
    if _till(db, sid)["status"] == till.STATUS_OPEN:
        assert till.blind_submit(
            db, sid,
            blind_denominations=[{"face": 500, "kind": "note", "pieces": rupees // 500}],
            actor=CASHIER,
        )["ok"]
    assert till.lock_session(db, sid, actor=MANAGER, variance_note="b12 d40 fixture")["ok"]


def _two_locked_drawers(db, *, a_rupees: int, b_rupees: int):
    a = _blind_count(db, rupees=a_rupees, lock=True)
    b = till.open_session(
        db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
        shift="PM", actor=CASHIER,
    )["session"]["session_id"]
    _lock_open_drawer(db, b, rupees=b_rupees)
    return a, b


class TestD40BothDrawersLockedLinksTheLatest:
    """Reviewer round 5: when BOTH drawers of the day are LOCKED, the fallback
    used find_one with no sort and returned the OLDER drawer A, and the Day-End
    record then adopted A's count (a Rs 2,000 shortage that does not exist, or
    'Matches' over a real one). The day's latest drawer is the one acted on."""

    def test_day_end_after_both_locked_records_the_latest_drawer(self, db, day_end):
        a, b = _two_locked_drawers(db, a_rupees=1000, b_rupees=3000)

        close = day_end(closing_cash=3000.0, system_cash=3000.0)["close"]

        assert close["till_session_id"] == b
        assert (close["closing_cash"], close["variance"]) == (3000.0, None)
        assert close["counted_from_shared_record"] is False
        assert close["entered_closing_cash"] is None
        assert close["till_drawers_on_day"] == 2
        assert _till(db, a)["blind_count_paisa"] == 100000
        assert _till(db, b)["blind_count_paisa"] == 300000
        assert len(list(db.get_collection("till_sessions").find({}))) == 2

    def test_mirror_a_real_shortage_on_the_last_drawer_is_not_hidden(self, db, day_end):
        a, b = _two_locked_drawers(db, a_rupees=3000, b_rupees=1000)

        close = day_end(closing_cash=1000.0, system_cash=3000.0)["close"]

        assert close["till_session_id"] == b
        assert (close["closing_cash"], close["variance"]) == (1000.0, None)
        assert close["counted_from_shared_record"] is False
        # D53: the last drawer's shortage is on the record as THAT drawer's
        # own count and variance, never folded into a whole-day figure.
        drawers = {d["session_id"]: d for d in close["till_drawers"]}
        assert drawers[a]["counted_paisa"] == 300000
        assert drawers[b]["counted_paisa"] == 100000
        assert drawers[b]["variance_paisa"] == _till(db, b)["variance_paisa"]

    def test_cash_register_after_both_locked_links_the_latest_drawer(
        self, db, monkeypatch
    ):
        a, b = _two_locked_drawers(db, a_rupees=1000, b_rupees=3000)

        out = _cr_close_two_drawers(
            db, monkeypatch,
            denominations=[{"face": 500, "kind": "note", "pieces": 6}],
            closing_count_state="COUNTED",
            note="d40 fixture - opening float not declared",
        )

        assert out["counted"] == 3000.0
        stored = db.get_collection("cash_register_sessions").find_one({"_id": "CR-D40"})
        assert stored["till_session_id"] == b
        assert stored["till_drawers_on_day"] == 2

    def test_both_doors_agree_when_the_second_closes_after_the_lock(
        self, db, day_end, monkeypatch
    ):
        """The builder's own D40 sequence, then B is locked, then the other door
        closes: both records must name drawer B and Rs 3,000."""
        a, b = _locked_then_second_drawer(db)
        de = day_end(closing_cash=3000.0, system_cash=3000.0)["close"]
        assert de["till_session_id"] == b
        _lock_open_drawer(db, b, rupees=3000)

        out = _cr_close_two_drawers(
            db, monkeypatch,
            denominations=[{"face": 500, "kind": "note", "pieces": 2}],
            closing_count_state="COUNTED",
            note="d40 fixture - opening float not declared",
        )

        assert out["counted"] == 3000.0 == de["closing_cash"]
        stored = db.get_collection("cash_register_sessions").find_one({"_id": "CR-D40"})
        assert stored["till_session_id"] == b

    def test_a_lost_acknowledgement_retry_after_the_lock_keeps_the_latest_drawer(
        self, db, day_end, monkeypatch
    ):
        a, b = _locked_then_second_drawer(db)
        closes = db.get_collection("day_end_closes")
        real_insert = closes.insert_one
        calls = {"n": 0}

        def _fail_once(doc, *args, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("write acknowledgement lost")
            return real_insert(doc, *args, **kw)

        monkeypatch.setattr(closes, "insert_one", _fail_once)
        with pytest.raises(HTTPException):
            day_end(closing_cash=3000.0, system_cash=3000.0)
        _lock_open_drawer(db, b, rupees=3000)  # B locked before the retry

        close = day_end(closing_cash=3000.0, system_cash=3000.0)["close"]
        assert close["till_session_id"] == b
        assert (close["closing_cash"], close["variance"]) == (3000.0, None)
        assert _stored_close(db)["closing_cash"] == 3000.0
        assert _till(db, a)["blind_count_paisa"] == 100000


def _reopen_older_drawer(db, *, a_rupees: int, b_rupees: int):
    """Both drawers counted and locked, then the OLDER drawer A is reopened
    (allowed: no other drawer is active). A is BLIND_SUBMITTED again -- already
    counted, never recountable -- while the day's latest drawer B is LOCKED."""
    a, b = _two_locked_drawers(db, a_rupees=a_rupees, b_rupees=b_rupees)
    res = till.reopen_session(db, a, reason="recount the morning drawer", actor=MANAGER)
    assert res["ok"] and res["session"]["status"] == till.STATUS_BLIND_SUBMITTED, res
    return a, b


class TestD40ReopenedOlderDrawerDoesNotOutrankTheLatest:
    """Reviewer round 6: a REOPENED drawer is not 'the drawer still open'. It
    was counted before its lock and blind_submit refuses to count it again, so
    D40's reason does not apply. Only an uncounted (OPEN) drawer outranks the
    day's latest drawer; otherwise the latest-opened drawer is acted on."""

    def test_day_end_records_the_latest_drawer_not_the_reopened_one(self, db, day_end):
        a, b = _reopen_older_drawer(db, a_rupees=1000, b_rupees=3000)

        close = day_end(closing_cash=3000.0, system_cash=3000.0)["close"]

        assert close["till_session_id"] == b
        assert (close["closing_cash"], close["variance"]) == (3000.0, None)
        assert close["counted_from_shared_record"] is False
        assert close["entered_closing_cash"] is None
        assert close["till_drawers_on_day"] == 2
        stored = _stored_close(db)
        assert (stored["closing_cash"], stored["variance"]) == (3000.0, None)
        # Nothing on the till moved: A still reopened with its count, B locked.
        ra, rb = _till(db, a), _till(db, b)
        assert (ra["status"], ra["blind_count_paisa"]) == (till.STATUS_BLIND_SUBMITTED, 100000)
        assert (rb["status"], rb["blind_count_paisa"]) == (till.STATUS_LOCKED, 300000)
        assert len(list(db.get_collection("till_sessions").find({}))) == 2

    def test_mirror_a_real_shortage_on_the_last_drawer_is_not_hidden(self, db, day_end):
        a, b = _reopen_older_drawer(db, a_rupees=3000, b_rupees=1000)

        close = day_end(closing_cash=1000.0, system_cash=3000.0)["close"]

        assert close["till_session_id"] == b
        assert (close["closing_cash"], close["variance"]) == (1000.0, None)

    def test_cash_register_links_the_latest_drawer_not_the_reopened_one(
        self, db, monkeypatch
    ):
        a, b = _reopen_older_drawer(db, a_rupees=1000, b_rupees=3000)

        out = _cr_close_two_drawers(
            db, monkeypatch,
            denominations=[{"face": 500, "kind": "note", "pieces": 6}],
            closing_count_state="COUNTED",
            note="d40 fixture - opening float not declared",
        )

        assert out["counted"] == 3000.0
        stored = db.get_collection("cash_register_sessions").find_one({"_id": "CR-D40"})
        assert stored["till_session_id"] == b
        assert stored["till_drawers_on_day"] == 2

    def test_both_doors_agree_across_the_reopen(self, db, day_end, monkeypatch):
        """Day-End closes with both locked; A is then reopened; Cash Register
        closes. Both records must name drawer B."""
        a, b = _two_locked_drawers(db, a_rupees=1000, b_rupees=3000)
        de = day_end(closing_cash=3000.0, system_cash=3000.0)["close"]
        assert de["till_session_id"] == b
        assert till.reopen_session(db, a, reason="recount", actor=MANAGER)["ok"]

        out = _cr_close_two_drawers(
            db, monkeypatch,
            denominations=[{"face": 500, "kind": "note", "pieces": 6}],
            closing_count_state="COUNTED",
            note="d40 fixture - opening float not declared",
        )

        assert out["counted"] == 3000.0 == de["closing_cash"]
        stored = db.get_collection("cash_register_sessions").find_one({"_id": "CR-D40"})
        assert stored["till_session_id"] == b

    def test_control_a_single_reopened_drawer_is_still_the_one_linked(self, db):
        """CONTROL: one drawer, reopened -> it is still the day's drawer."""
        sid = _blind_count(db, rupees=1000, lock=True)
        assert till.reopen_session(db, sid, reason="recount", actor=MANAGER)["ok"]
        link = till.record_screen_close(
            db, store_id=STORE, session_date=DATE, counted_paisa=300000, actor=MANAGER
        )
        assert link["ok"] and link["session_id"] == sid
        assert link["already_counted"] is True
        assert till.shared_counted_paisa(link) == 100000
        assert link["drawers_on_day"] == 1


def _real_till_db():
    """A throwaway database on a real MongoDB with the REAL till indexes, or
    skip. CI runs this against its mongo service; a PC without Mongo skips."""
    from pymongo import MongoClient

    host = os.getenv("MONGO_HOST", "127.0.0.1")
    port = int(os.getenv("MONGO_PORT", "27017"))
    try:
        client = MongoClient(host, port, serverSelectionTimeoutMS=1500)
        client.admin.command("ping")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no MongoDB at {host}:{port} ({exc})")
    # A local run on a shared mongod can name its own throwaway prefix.
    prefix = os.getenv("B12_NATIVE_DB_PREFIX", "ims_test_b12")
    assert not prefix.startswith(("ims_codex_", "ims_audit_")), prefix
    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    return client, client[name], name


def test_l3_28_real_index_conflict_is_a_409_on_real_mongo():
    """The same reproduction against the real partial unique index, so the
    in-memory stand-in above cannot be the only thing that agrees."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        locked_sid, open_sid = _locked_then_second_drawer(real_db)

        res = till.reopen_session(real_db, locked_sid, reason="recount", actor=MANAGER)

        assert res["ok"] is False and res["http"] == 409, res
        assert res["error"] == "another_drawer_open"
        assert res["active_session_id"] == open_sid
        for leak in _LEAKS:
            assert leak not in str(res)
        row = real_db.get_collection("till_sessions").find_one({"_id": locked_sid})
        assert row["status"] == till.STATUS_LOCKED
        assert int(row.get("reopen_count") or 0) == 0
        assert not [h for h in (row.get("history") or []) if h.get("action") == "reopen"]
    finally:
        client.drop_database(name)
        client.close()


def test_d40_two_drawers_the_shared_lookup_picks_the_open_one_on_real_mongo():
    """D40 on a native PyMongo Database with the real indexes: whatever order
    Mongo returns the two sessions in, the close lands on the OPEN drawer."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        locked_sid, open_sid = _locked_then_second_drawer(real_db)

        link = till.record_screen_close(
            real_db, store_id=STORE, session_date=DATE,
            counted_paisa=300000, actor=MANAGER,
        )

        assert link["ok"] and link["session_id"] == open_sid, link
        assert link["already_counted"] is False
        assert till.shared_counted_paisa(link) == 300000
        coll = real_db.get_collection("till_sessions")
        b = coll.find_one({"_id": open_sid})
        a = coll.find_one({"_id": locked_sid})
        assert (b["status"], b["blind_count_paisa"]) == (till.STATUS_BLIND_SUBMITTED, 300000)
        assert (a["status"], a["blind_count_paisa"]) == (till.STATUS_LOCKED, 100000)
        assert coll.count_documents({}) == 2
    finally:
        client.drop_database(name)
        client.close()


def test_d40_two_locked_drawers_link_the_latest_on_real_mongo():
    """Both drawers LOCKED on a native PyMongo Database with the real indexes
    (equal index keys come back in insertion order): the close must link the
    LATEST drawer B, never the older A."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        a, b = _two_locked_drawers(real_db, a_rupees=1000, b_rupees=3000)

        link = till.record_screen_close(
            real_db, store_id=STORE, session_date=DATE,
            counted_paisa=300000, actor=MANAGER,
        )

        assert link["ok"] and link["session_id"] == b, link
        assert link["already_counted"] is True
        assert till.shared_counted_paisa(link) == 300000
        assert link["drawers_on_day"] == 2
        assert real_db.get_collection("till_sessions").count_documents({}) == 2
    finally:
        client.drop_database(name)
        client.close()


def test_d40_reopened_older_drawer_does_not_outrank_the_latest_on_real_mongo():
    """The round-6 state on a native PyMongo Database with the real partial
    unique index: older drawer A reopened (BLIND_SUBMITTED), B LOCKED. The
    close links B; A's count is never adopted."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        a, b = _reopen_older_drawer(real_db, a_rupees=1000, b_rupees=3000)

        link = till.record_screen_close(
            real_db, store_id=STORE, session_date=DATE,
            counted_paisa=300000, actor=MANAGER,
        )

        assert link["ok"] and link["session_id"] == b, link
        assert link["already_counted"] is True
        assert till.shared_counted_paisa(link) == 300000
        coll = real_db.get_collection("till_sessions")
        assert coll.find_one({"_id": a})["status"] == till.STATUS_BLIND_SUBMITTED
        assert coll.count_documents({}) == 2
    finally:
        client.drop_database(name)
        client.close()


# ===========================================================================
# L3-15  One budget: what Budgeting saves is what the Finance Dashboard reads
# ===========================================================================

PERIOD = "2026-11"


class _ExpenseRepo:
    """find_many over the fake `expenses` collection -- the one method the
    budget actuals reader uses on the real ExpenseRepository."""

    def __init__(self, coll):
        self._coll = coll

    def find_many(self, flt=None, limit=0, **_kw):
        return list(self._coll.find(flt or {}))


@pytest.fixture()
def budget_world(monkeypatch):
    world = StrictDB()
    monkeypatch.setattr(
        budgets_router, "_budgets_collection", lambda: world.get_collection("budgets")
    )
    monkeypatch.setattr(
        budgets_router,
        "get_expense_repository",
        lambda: _ExpenseRepo(world.get_collection("expenses")),
    )
    # Revenue actuals come from orders; none are seeded here. An EMPTY orders
    # collection, not None: None now means "no database" and answers 503.
    monkeypatch.setattr(
        budgets_router,
        "get_order_repository",
        lambda: _ExpenseRepo(world.get_collection("orders")),
    )
    return world


def _save(head, amount, *, store=STORE, period=PERIOD, user=ADMIN):
    """Through the REAL writer the Budgeting page calls."""
    return asyncio.run(
        budgets_router.upsert_budget(
            budgets_router.BudgetUpsert(
                store_id=store, period=period, head=head, planned_amount=amount
            ),
            current_user=user,
        )
    )


def _dashboard(*, store=STORE, month=11, year=2026, user=ADMIN):
    """The read the Finance Dashboard's Budgets tab makes."""
    return asyncio.run(
        finance_router.get_budget(
            mode="full", month=month, year=year, store_id=store, current_user=user
        )
    )


def _variance(*, store=STORE, period=PERIOD, user=ADMIN):
    return asyncio.run(
        budgets_router.budget_variance(store_id=store, period=period, current_user=user)
    )


class TestL315OneBudgetAcrossBothScreens:
    def test_the_audit_reproduction_revenue_10000_and_rent_1000(self, budget_world):
        """THE REPRODUCTION. Budgeting saves a November revenue target of
        10,000 and rent 1,000. The dashboard said total budget 0, every
        category 0 and `no_budget_set: true`."""
        _save("REVENUE", 10000)
        _save("rent", 1000)

        body = _dashboard()

        assert body.get("no_budget_set") is not True, body
        assert body["categories"]["rent"]["budget"] == 1000
        assert body["revenue"]["budget"] == 10000
        assert body["store_id"] == STORE and body["period"] == PERIOD
        # The dashboard's "Total Budget" card sums the expense heads.
        assert sum(c["budget"] for c in body["categories"].values()) == 1000

    def test_every_head_agrees_with_the_budgeting_page_plan_and_actual(
        self, budget_world
    ):
        _save("REVENUE", 10000)
        _save("rent", 1000)
        _save("marketing", 250)
        budget_world.seed(
            "expenses",
            [
                {"expense_id": "e1", "store_id": STORE, "category": "rent",
                 "amount": 400.0, "status": "APPROVED", "expense_date": "2026-11-05"},
                # An actual with no plan -- shown on both screens, plan 0.
                {"expense_id": "e2", "store_id": STORE, "category": "travel",
                 "amount": 75.0, "status": "APPROVED", "expense_date": "2026-11-06"},
                # Not counted by either screen: pending, last month, other shop.
                {"expense_id": "e3", "store_id": STORE, "category": "rent",
                 "amount": 5000.0, "status": "PENDING", "expense_date": "2026-11-07"},
                {"expense_id": "e4", "store_id": STORE, "category": "rent",
                 "amount": 6000.0, "status": "APPROVED", "expense_date": "2026-10-30"},
                {"expense_id": "e5", "store_id": OTHER_STORE, "category": "rent",
                 "amount": 7000.0, "status": "APPROVED", "expense_date": "2026-11-05"},
            ],
        )

        dashboard = _dashboard()
        lines = {ln["head"]: ln for ln in _variance()["lines"]}

        expense_lines = {h: ln for h, ln in lines.items() if h != "REVENUE"}
        assert set(dashboard["categories"]) == set(expense_lines)
        for head, ln in expense_lines.items():
            assert dashboard["categories"][head]["budget"] == ln["planned"], head
            assert dashboard["categories"][head]["actual"] == ln["actual"], head
        assert dashboard["revenue"]["budget"] == lines["REVENUE"]["planned"]
        assert dashboard["revenue"]["actual"] == lines["REVENUE"]["actual"]
        # Concrete figures, so the two cannot agree by both being wrong.
        assert dashboard["categories"]["rent"] == {"budget": 1000.0, "actual": 400.0}
        assert dashboard["categories"]["travel"] == {"budget": 0.0, "actual": 75.0}

    def test_another_shop_and_another_month_stay_separate(self, budget_world):
        _save("rent", 1000)
        other_shop = _dashboard(store=OTHER_STORE)
        other_month = _dashboard(month=12)
        for body in (other_shop, other_month):
            assert body["no_budget_set"] is True
            assert all(c["budget"] == 0 for c in body["categories"].values())
        # The empty state still names the usual heads (the tab is not blank).
        assert "rent" in other_shop["categories"]

    def test_a_shop_login_reads_its_own_shop_and_no_other(self, budget_world):
        _save("rent", 1000)
        _save("rent", 9000, store=OTHER_STORE)
        own = _dashboard(store=None, user=MANAGER)
        assert own["store_id"] == STORE
        assert own["categories"]["rent"]["budget"] == 1000
        with pytest.raises(HTTPException) as exc:
            _dashboard(store=OTHER_STORE, user=MANAGER)
        assert exc.value.status_code == 403

    def test_an_admin_with_no_shop_picked_reads_every_shop(self, budget_world):
        _save("rent", 1000)
        _save("rent", 9000, store=OTHER_STORE)
        body = _dashboard(store=None)
        assert body["store_id"] is None
        assert body["categories"]["rent"]["budget"] == 10000
        assert body["categories"]["rent"]["budget"] == next(
            ln["planned"] for ln in _variance(store=None)["lines"] if ln["head"] == "rent"
        )

    def test_a_planned_pay_head_is_still_withheld_below_admin(self, budget_world):
        _save("salaries", 41000)
        _save("rent", 1000)
        admin = _dashboard()
        assert admin["categories"]["salaries"]["budget"] == 41000
        manager = _dashboard(user=MANAGER)
        assert "salaries" not in manager["categories"]
        assert manager["categories_partially_restricted"] is True
        assert manager["categories"]["rent"]["budget"] == 1000
        assert "41000" not in str(manager)

    @pytest.mark.parametrize("broken", ["plan", "expenses", "revenue"])
    def test_a_failed_read_is_a_503_never_no_budget_set(
        self, budget_world, monkeypatch, broken
    ):
        """Unreadable is not "no plan" (the L3-10 rule on the budget twin). The
        shared reader used to swallow a failed query into an empty plan or a
        zero actual, so the Budgets tab said `no_budget_set` / total 0 beside a
        saved plan. Every reader of it -- the dashboard, the variance view and
        the plan list -- now answers 503 instead, and the plan is untouched."""
        _save("rent", 1000)

        def boom(*_a, **_k):
            raise RuntimeError("injected read failure")

        if broken == "plan":
            monkeypatch.setattr(budget_world.get_collection("budgets"), "find", boom)
        elif broken == "expenses":
            class _Broken:
                find_many = staticmethod(boom)
            monkeypatch.setattr(budgets_router, "get_expense_repository", lambda: _Broken())
        else:
            class _Orders:
                pass
            monkeypatch.setattr(budgets_router, "get_order_repository", lambda: _Orders())
            monkeypatch.setattr(budgets_router._reports, "_orders_in_window", boom)

        readers = [_dashboard, _variance]
        if broken == "plan":
            readers.append(lambda: asyncio.run(budgets_router.list_budgets(
                store_id=STORE, period=PERIOD, current_user=ADMIN)))
        for read in readers:
            with pytest.raises(HTTPException) as exc:
                read()
            assert exc.value.status_code == 503
            assert "Could not read the budget" in str(exc.value.detail)
            assert "injected" not in str(exc.value.detail)
        monkeypatch.undo()
        # Nothing was lost: the plan reads back once the read works.
        assert budget_world.get_collection("budgets").find_one({"head": "rent"})["planned_amount"] == 1000


    @pytest.mark.parametrize("missing", ["plan", "expenses", "revenue"])
    def test_a_disconnected_database_is_a_503_never_no_budget_set(
        self, budget_world, monkeypatch, missing
    ):
        """Reviewer finding on #1421: with the database disconnected the
        budgets collection and both repositories come back None, and the
        reader used to treat None as "nothing there" -- the Budgets tab
        answered 200 `no_budget_set` with zero actuals (before this PR the
        same outage was a 500). No database is unreadable, not "no plan"."""
        _save("rent", 1000)
        target = {
            "plan": "_budgets_collection",
            "expenses": "get_expense_repository",
            "revenue": "get_order_repository",
        }[missing]
        monkeypatch.setattr(budgets_router, target, lambda: None)
        for read in (_dashboard, _variance):
            with pytest.raises(HTTPException) as exc:
                read()
            assert exc.value.status_code == 503
            assert "Could not read the budget" in str(exc.value.detail)


def test_l3_15_no_reader_still_looks_for_the_shape_nobody_writes():
    """The dashboard reader used to query {month, year, mode}; the only writer
    stores {store_id, period, head, planned_amount}. Guard the source so the
    dead lookup cannot come back beside the live one."""
    import inspect

    from api.routers.finance import budget as budget_module

    src = inspect.getsource(budget_module.get_budget)
    assert '"mode": mode}' not in src and "find_one(" not in src


# ===========================================================================
# L3-10  An unreadable petty-cash float is not "no float"
# ===========================================================================


class _Floats(FakeCollection):
    pass


class TestL310UnreadableFloatIsNotNoFloat:
    def _db_with_float(self) -> FakeDB:
        fake = FakeDB()
        fake.get_collection(pcs.COLLECTION).insert_one(
            {
                "store_id": STORE, "balance": 1200.0, "float_limit": 5000.0,
                "low_balance_threshold": 500.0, "status": "OPEN", "ledger": [],
            }
        )
        return fake

    def test_a_failed_read_is_reported_as_unavailable(self, monkeypatch):
        fake = self._db_with_float()

        def _boom(*a, **k):
            raise RuntimeError("socket timeout")

        monkeypatch.setattr(fake.get_collection(pcs.COLLECTION), "find_one", _boom)
        res = pcs.get_balance(fake, STORE)
        assert res["ok"] is False
        assert res["error"] == "unavailable"
        # Never the "no float" envelope, and never a balance of nothing.
        assert "exists" not in res and "balance" not in res

    def test_no_database_is_unavailable_too(self):
        res = pcs.get_balance(None, STORE)
        assert res["ok"] is False and res["error"] == "unavailable"

    def test_a_float_that_really_is_not_open_still_says_so(self):
        """POSITIVE CONTROL: the honest empty state is unchanged."""
        res = pcs.get_balance(FakeDB(), STORE)
        assert res["ok"] is True and res["exists"] is False

    def test_a_readable_float_is_unchanged(self):
        res = pcs.get_balance(self._db_with_float(), STORE)
        assert res["ok"] is True and res["exists"] is True
        assert res["balance"] == 1200.0

    def test_the_route_answers_503_not_an_empty_float(self, monkeypatch):
        from api.routers import expenses as expenses_router

        fake = self._db_with_float()
        monkeypatch.setattr(
            fake.get_collection(pcs.COLLECTION),
            "find_one",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("socket timeout")),
        )
        monkeypatch.setattr(expenses_router, "get_db", lambda: fake)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                expenses_router.get_petty_cash_balance(
                    store_id=STORE, current_user=MANAGER
                )
            )
        assert exc.value.status_code == 503
        assert "socket" not in str(exc.value.detail)

    def test_the_route_still_serves_a_readable_float(self, monkeypatch):
        from api.routers import expenses as expenses_router

        monkeypatch.setattr(expenses_router, "get_db", self._db_with_float)
        res = asyncio.run(
            expenses_router.get_petty_cash_balance(store_id=STORE, current_user=MANAGER)
        )
        assert res["exists"] is True and res["balance"] == 1200.0


class TestL310DaySettlementTwin:
    """The Day Settlement screen reads the SAME float through its own route.
    A failed read there said "no float is open, nothing to settle"."""

    def _db_with_float(self) -> FakeDB:
        fake = FakeDB()
        fake.get_collection(pcs.COLLECTION).insert_one(
            {
                "store_id": STORE, "balance": 1200.0, "float_limit": 5000.0,
                "low_balance_threshold": 500.0, "status": "OPEN", "ledger": [],
            }
        )
        return fake

    def test_a_failed_position_read_is_unavailable_not_no_float(self, monkeypatch):
        from api.services import petty_cash_settlement_service as pcss

        fake = self._db_with_float()
        monkeypatch.setattr(pcss, "tolerance_for", lambda *a, **k: 0.0)
        monkeypatch.setattr(
            fake.get_collection(pcss.FLOAT_COLLECTION),
            "find_one",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("socket timeout")),
        )
        res = pcss.get_day_position(fake, STORE, DATE)
        assert res["ok"] is False and res["error"] == "unavailable"
        assert "exists" not in res
        assert pcss.get_day_position(None, STORE, DATE)["ok"] is False

    def test_no_float_and_a_readable_float_are_unchanged(self, monkeypatch):
        from api.services import petty_cash_settlement_service as pcss

        monkeypatch.setattr(pcss, "tolerance_for", lambda *a, **k: 0.0)
        none = pcss.get_day_position(FakeDB(), STORE, DATE)
        assert none["ok"] is True and none["exists"] is False
        some = pcss.get_day_position(self._db_with_float(), STORE, DATE)
        assert some["ok"] is True and some["exists"] is True

    def test_the_position_route_answers_503(self, monkeypatch):
        from api.routers import expenses as expenses_router
        from api.services import petty_cash_settlement_service as pcss

        monkeypatch.setattr(pcss, "tolerance_for", lambda *a, **k: 0.0)
        fake = self._db_with_float()
        monkeypatch.setattr(
            fake.get_collection(pcss.FLOAT_COLLECTION),
            "find_one",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("socket timeout")),
        )
        monkeypatch.setattr(expenses_router, "get_db", lambda: fake)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                expenses_router.get_petty_cash_settlement_position(
                    store_id=STORE, settle_date=DATE, current_user=MANAGER
                )
            )
        assert exc.value.status_code == 503
        assert "socket" not in str(exc.value.detail)


def test_l3_15_jarvis_context_reads_the_shape_budgeting_writes():
    """The assistant's finance context picked `category` / `allocated` /
    `spent` off budget rows -- fields no budget row has. Guard the source."""
    import inspect

    from api.routers import jarvis

    src = inspect.getsource(jarvis)
    block = src[src.index('get_db_collection("budgets")'):][:1200]
    assert 'b.get("planned_amount")' in block and 'b.get("head")' in block
    assert 'b.get("allocated")' not in block and 'b.get("spent")' not in block


# ===========================================================================
# Round 7 (Codex PR-1421-round1): native read faults, simultaneous closes, D53
# ===========================================================================

from datetime import datetime  # noqa: E402

from pymongo.errors import AutoReconnect  # noqa: E402

from database.repositories.expense_repository import ExpenseRepository  # noqa: E402
from database.repositories.order_repository import OrderRepository  # noqa: E402

BUDGET_ORDER = {"order_id": "R7-ORD", "store_id": STORE, "status": "CONFIRMED",
                "grand_total": 2500.0, "created_at": datetime(2026, 11, 15)}
BUDGET_RENT = {"expense_id": "R7-RENT", "store_id": STORE, "category": "rent",
               "amount": 250.0, "status": "APPROVED", "expense_date": "2026-11-15"}


def _real_repos(monkeypatch, world):
    """The REAL OrderRepository / ExpenseRepository over ``world`` -- their
    find_many is the one that printed a failed read and answered []."""
    monkeypatch.setattr(budgets_router, "_budgets_collection",
                        lambda: world.get_collection("budgets"))
    monkeypatch.setattr(budgets_router, "get_order_repository",
                        lambda: OrderRepository(world.get_collection("orders")))
    monkeypatch.setattr(budgets_router, "get_expense_repository",
                        lambda: ExpenseRepository(world.get_collection("expenses")))


def _assert_budget_reads_503():
    for read in (_dashboard, _variance):
        with pytest.raises(HTTPException) as exc:
            read()
        assert exc.value.status_code == 503
        assert "Could not read the budget" in str(exc.value.detail)
        assert "injected" not in str(exc.value.detail)


class TestR7BudgetActualsFailBelowTheRepository:
    """Codex P2: the budget readers caught a failure only ABOVE the repository,
    but OrderRepository/ExpenseRepository.find_many (BaseRepository) and
    reports._orders_in_window both swallow a failed read into []. A dropped
    connection on `orders` read as revenue 0 (variance -10,000) and on
    `expenses` as rent 0, both 200. The fault is injected at the COLLECTION,
    under the real repositories."""

    @pytest.mark.parametrize("broken", ["orders", "expenses"])
    def test_a_collection_read_fault_is_503_never_a_zero_actual(
        self, monkeypatch, broken
    ):
        world = StrictDB()
        _real_repos(monkeypatch, world)
        world.seed("orders", [dict(BUDGET_ORDER)])
        world.seed("expenses", [dict(BUDGET_RENT)])
        _save("REVENUE", 10000)
        _save("rent", 1000)
        healthy = _dashboard()
        assert healthy["revenue"]["actual"] == 2500.0
        assert healthy["categories"]["rent"]["actual"] == 250.0

        coll = world.get_collection(broken)
        real_find = coll.find

        def _drop(*_a, **_k):
            raise AutoReconnect("injected connection drop")

        monkeypatch.setattr(coll, "find", _drop)
        _assert_budget_reads_503()
        monkeypatch.setattr(coll, "find", real_find)
        assert _dashboard() == healthy

    def test_other_reports_keep_their_fail_soft_contract(self):
        """Only the budget reader is strict: _orders_in_window's default (the
        sales reports) is unchanged."""

        class _Repo:
            def find_many(self, *_a, **_k):
                raise AutoReconnect("x")

        from api.routers.reports import _shared

        kw = dict(store_id=STORE, start_dt=datetime(2026, 11, 1), end_dt=datetime(2026, 12, 1))
        assert _shared._orders_in_window(_Repo(), **kw) == []
        with pytest.raises(AutoReconnect):
            _shared._orders_in_window(_Repo(), strict=True, **kw)


@pytest.mark.parametrize("broken", ["orders", "expenses"])
def test_r7_budget_actuals_native_read_fault_is_503_on_real_mongo(monkeypatch, broken):
    """Codex's reproduction on a native PyMongo Database: Collection.find raises
    AutoReconnect for that collection only. Both readers answered 200 with a
    zero actual; they answer 503, and the true figures return afterwards."""
    from pymongo.collection import Collection

    client, real_db, name = _real_till_db()
    try:
        _real_repos(monkeypatch, real_db)
        real_db.orders.insert_one(dict(BUDGET_ORDER))
        real_db.expenses.insert_one(dict(BUDGET_RENT))
        _save("REVENUE", 10000)
        _save("rent", 1000)
        healthy = _dashboard()
        assert (healthy["revenue"]["actual"], healthy["categories"]["rent"]["actual"]) == (2500.0, 250.0)

        real_find = Collection.find

        def _drop(self, *a, **k):
            if self.database.name == name and self.name == broken:
                raise AutoReconnect("injected connection drop")
            return real_find(self, *a, **k)

        monkeypatch.setattr(Collection, "find", _drop)
        _assert_budget_reads_503()
        monkeypatch.setattr(Collection, "find", real_find)
        assert _dashboard() == healthy
    finally:
        client.drop_database(name)
        client.close()


class TestR7OneCloseRecordPerShopDay:
    """Codex P2: the "already closed?" read and the insert were separate, so
    two closes that both read "not closed" both answered 200 and stored two
    close records for one shop-day. The key is now the record's _id."""

    def test_a_close_whose_precheck_missed_is_refused_409_and_stores_nothing(
        self, db, day_end, monkeypatch
    ):
        """The race, deterministic: the second close's pre-check reads the day
        as not closed (exactly what the loser of a simultaneous pair sees)."""
        sid = _blind_count(db, rupees=1000, lock=False)
        first = day_end(closing_cash=1000.0, system_cash=1000.0)["close"]

        closes = db.get_collection("day_end_closes")
        real_find_one = closes.find_one
        calls = {"n": 0}

        def _stale_precheck(query, *a, **k):
            calls["n"] += 1
            return None if calls["n"] == 1 else real_find_one(query, *a, **k)

        monkeypatch.setattr(closes, "find_one", _stale_precheck)
        with pytest.raises(HTTPException) as exc:
            day_end(closing_cash=2000.0, system_cash=1000.0)
        assert exc.value.status_code == 409
        assert exc.value.detail["close"]["closing_cash"] == first["closing_cash"] == 1000.0
        rows = list(closes.docs)
        assert len(rows) == 1
        assert rows[0]["_id"] == "DAYEND:%s:%s" % (STORE, DATE)
        assert _till(db, sid)["blind_count_paisa"] == 100000

    def test_a_lost_acknowledgement_then_retry_leaves_one_close_and_one_count(
        self, db, day_end, monkeypatch
    ):
        """The insert LANDS but its acknowledgement is lost (500); the retry
        answers 409 with that one stored close. One close row, one till count."""
        sid = _blind_count(db, rupees=1000, lock=False)
        closes = db.get_collection("day_end_closes")
        real_insert = closes.insert_one
        fired = []

        def _lands_then_raises(doc, *a, **k):
            result = real_insert(doc, *a, **k)
            if not fired:
                fired.append(1)
                raise AutoReconnect("acknowledgement lost after commit")
            return result

        monkeypatch.setattr(closes, "insert_one", _lands_then_raises)
        with pytest.raises(HTTPException) as first:
            day_end(closing_cash=1000.0, system_cash=1000.0)
        assert first.value.status_code == 409  # the re-read finds the landed row
        with pytest.raises(HTTPException) as retry:
            day_end(closing_cash=1000.0, system_cash=1000.0)
        assert retry.value.status_code == 409
        assert len(closes.docs) == 1 and closes.docs[0]["closing_cash"] == 1000.0
        assert _till(db, sid)["blind_count_paisa"] == 100000

    def test_control_a_legacy_close_with_a_random_id_still_blocks_a_new_one(
        self, db, day_end
    ):
        db.get_collection("day_end_closes").insert_one(
            {"store_id": STORE, "date": DATE, "closing_cash": 500.0}
        )
        with pytest.raises(HTTPException) as exc:
            day_end(closing_cash=2000.0, system_cash=1000.0)
        assert exc.value.status_code == 409
        assert len(db.get_collection("day_end_closes").docs) == 1


class _NativeDB:
    """A native PyMongo Database behind the app's db handle shape."""

    is_connected = True

    def __init__(self, real_db):
        self._db = real_db

    def get_collection(self, name):
        return self._db.get_collection(name)


def test_r7_two_simultaneous_closes_store_one_record_on_real_mongo(monkeypatch):
    """Codex's concurrent_close.py on a native PyMongo Database: both closes
    pass the "already closed?" read (held at a barrier), then both insert. One
    answers 200, the other 409 naming the same stored close; one record."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from pymongo.collection import Collection

    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        handle = _NativeDB(real_db)
        monkeypatch.setattr(reports_router, "get_db", lambda: handle)
        monkeypatch.setattr("api.services.stores_util.is_online_store", lambda _db, _sid: False)
        till.open_session(handle, store_id=STORE, session_date=DATE,
                          opening_float_paisa=100000, actor=CASHIER)

        real_find_one = Collection.find_one
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        held = []

        def _synced(self, query=None, *a, **k):
            answer = real_find_one(self, query, *a, **k)
            if (self.database.name == name and self.name == "day_end_closes"
                    and query == {"store_id": STORE, "date": DATE} and answer is None):
                with lock:
                    first = len(held) < 2
                    if first:
                        held.append(1)
                if first:
                    barrier.wait(timeout=10)
            return answer

        monkeypatch.setattr(Collection, "find_one", _synced)

        def _close(amount):
            body = reports_router.DayEndCloseBody(
                date=DATE, store_id=STORE, closing_cash=amount, system_cash=1000.0)
            try:
                return 200, asyncio.run(
                    reports_router.create_day_end_close(body, current_user=MANAGER))["close"]
            except HTTPException as exc:
                return exc.status_code, exc.detail

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(_close, [1000.0, 2000.0]))
        monkeypatch.setattr(Collection, "find_one", real_find_one)

        assert sorted(r[0] for r in results) == [200, 409], results
        stored = list(real_db.day_end_closes.find({"store_id": STORE, "date": DATE}))
        assert len(stored) == 1
        won = next(body for code, body in results if code == 200)
        lost = next(body for code, body in results if code == 409)
        assert lost["close"]["closing_cash"] == won["closing_cash"] == stored[0]["closing_cash"]
        session = real_db.till_sessions.find_one({"store_id": STORE, "session_date": DATE})
        assert session["blind_count_paisa"] == int(round(stored[0]["closing_cash"] * 100))
        assert real_db.till_sessions.count_documents({}) == 1
    finally:
        client.drop_database(name)
        client.close()


class TestR7D53SeveralDrawersNoWholeDayVariance:
    """D53: on a day with more than one drawer, drawers are not summed and
    neither close door shows one whole-day variance or a "balanced" verdict;
    each drawer's own count and variance are stored and the day's drawer count
    is said. Codex's replay: drawers Rs 1,000 and Rs 3,000, typed/system
    Rs 4,000, stored Rs 3,000 with variance -1,000."""

    def test_day_end_two_locked_drawers_stores_each_drawer_and_no_day_variance(
        self, db, day_end
    ):
        a, b = _two_locked_drawers(db, a_rupees=1000, b_rupees=3000)

        close = day_end(closing_cash=4000.0, system_cash=4000.0)["close"]

        assert close["variance"] is None
        assert close["variance_withheld_several_drawers"] is True
        assert close["till_drawers_on_day"] == 2
        assert [d["session_id"] for d in close["till_drawers"]] == [a, b]
        assert [d["counted_paisa"] for d in close["till_drawers"]] == [100000, 300000]
        for d in close["till_drawers"]:
            assert d["variance_paisa"] == _till(db, d["session_id"])["variance_paisa"]
            assert d["status"] == till.STATUS_LOCKED
        stored = _stored_close(db)
        assert stored["variance"] is None and len(stored["till_drawers"]) == 2

    def test_a_cashier_reading_the_close_never_sees_a_drawer_variance(self, db, day_end):
        _two_locked_drawers(db, a_rupees=1000, b_rupees=3000)
        day_end(closing_cash=4000.0, system_cash=4000.0)
        read = asyncio.run(reports_router.get_day_end_close(
            date=DATE, store_id=STORE, current_user=CASHIER))["close"]
        assert [d["counted_paisa"] for d in read["till_drawers"]] == [100000, 300000]
        assert all("variance_paisa" not in d for d in read["till_drawers"])
        manager = asyncio.run(reports_router.get_day_end_close(
            date=DATE, store_id=STORE, current_user=MANAGER))["close"]
        assert all("variance_paisa" in d for d in manager["till_drawers"])

    def test_an_unlocked_drawer_variance_stays_hidden_on_the_record(self, db, day_end):
        """Blind rule: a drawer's variance is revealed by its lock, not by a
        close of the day."""
        a, b = _locked_then_second_drawer(db)
        close = day_end(closing_cash=3000.0, system_cash=3000.0)["close"]
        drawers = {d["session_id"]: d for d in close["till_drawers"]}
        assert drawers[b]["status"] == till.STATUS_BLIND_SUBMITTED
        assert drawers[b]["variance_paisa"] is None
        assert drawers[a]["variance_paisa"] is not None

    def test_control_one_drawer_keeps_its_variance_and_no_drawer_list(self, db, day_end):
        _blind_count(db, rupees=1000, lock=True)
        close = day_end(closing_cash=1000.0, system_cash=4000.0)["close"]
        assert close["variance"] == -3000.0
        assert close["till_drawers"] is None
        assert close["variance_withheld_several_drawers"] is False

    def test_cash_register_two_drawers_withholds_the_verdict_and_needs_no_note(
        self, db, monkeypatch
    ):
        _two_locked_drawers(db, a_rupees=1000, b_rupees=3000)

        out = _cr_close_two_drawers(
            db, monkeypatch,
            denominations=[{"face": 500, "kind": "note", "pieces": 8}],
            closing_count_state="COUNTED",
        )

        assert out["variance"] is None
        assert out["variance_status"] == "SEVERAL_DRAWERS"
        assert [d["counted_paisa"] for d in out["till_drawers"]] == [100000, 300000]
        stored = db.get_collection("cash_register_sessions").find_one({"_id": "CR-D40"})
        assert (stored["variance"], stored["variance_status"]) == (None, "SEVERAL_DRAWERS")

        # Cash Reconciliation: drawer B is LOCKED, so this close folds into
        # B's own Z-Read row; no row carries a whole-day variance.
        rows = _recon(db, monkeypatch)
        assert all(r["source"] == "BLIND_EOD" for r in rows), rows

    def test_cash_reconciliation_does_not_rederive_the_withheld_variance(
        self, db, monkeypatch
    ):
        """The second drawer is counted at Cash Register but not yet locked, so
        the cash-register row stands on its own in Cash Reconciliation. It used
        to recompute counted - expected there, bringing back the whole-day
        variance the close withheld."""
        _locked_then_second_drawer(db)
        out = _cr_close_two_drawers(
            db, monkeypatch,
            denominations=[{"face": 500, "kind": "note", "pieces": 6}],
            closing_count_state="COUNTED",
            # Drawer B (float 0, no sales) holds Rs 3,000 over: its OWN
            # variance is out of band, so the close needs the note.
            note="PM drawer float was not entered",
        )
        assert (out["variance"], out["variance_status"]) == (None, "SEVERAL_DRAWERS")
        row = next(r for r in _recon(db, monkeypatch) if r["session_id"] == "CR-D40")
        assert (row["variance"], row["variance_status"]) == (None, "SEVERAL_DRAWERS")


    def test_cash_register_judges_the_unlocked_drawer_it_counted_note_and_alert(
        self, db, monkeypatch
    ):
        """Reviewer finding: on a several-drawer day the close set the variance
        to None BEFORE the band check, so it never asked for the note and never
        raised the manager alert (owner ruling 2026-08-25). The drawer it just
        counted is BLIND_SUBMITTED, so nothing judged it until a lock that may
        never come. Now that drawer is judged on its OWN stored variance."""
        _locked_then_second_drawer(db)
        alerts = []
        monkeypatch.setattr(
            finance_router.cash_drawer.till_service, "raise_variance_task",
            lambda **kw: alerts.append(kw),
        )
        with pytest.raises(HTTPException) as exc:
            _cr_close_two_drawers(
                db, monkeypatch,
                denominations=[{"face": 500, "kind": "note", "pieces": 6}],
                closing_count_state="COUNTED",
            )
        assert exc.value.status_code == 400
        assert "variance_note_required" in str(exc.value.detail)
        assert alerts == []

        # The count already landed on drawer B; the retry with a note adopts
        # it, closes, and alerts on B's own variance -- never a whole-day one.
        out = asyncio.run(finance_router.close_cash_register(
            body=finance_router.CashRegisterClose(
                session_id="CR-D40",
                denominations=[{"face": 500, "kind": "note", "pieces": 6}],
                closing_count_state="COUNTED",
                note="PM drawer float was not entered",
            ),
            current_user=MANAGER,
        ))
        assert (out["variance"], out["variance_status"]) == (None, "SEVERAL_DRAWERS")
        drawer_b = _till(db, out["till_session_id"])
        assert drawer_b["status"] == till.STATUS_BLIND_SUBMITTED
        assert len(alerts) == 1
        assert alerts[0]["variance_paisa"] == drawer_b["variance_paisa"] != 0
        assert alerts[0]["note"] == "PM drawer float was not entered"

    def test_cash_register_a_drawer_already_locked_is_not_judged_again(
        self, db, monkeypatch
    ):
        """Control: when the drawer this close lands on was LOCKED, its lock
        already took the note and raised the alert -- no second ask."""
        _two_locked_drawers(db, a_rupees=1000, b_rupees=3000)
        alerts = []
        monkeypatch.setattr(
            finance_router.cash_drawer.till_service, "raise_variance_task",
            lambda **kw: alerts.append(kw),
        )
        out = _cr_close_two_drawers(
            db, monkeypatch,
            denominations=[{"face": 500, "kind": "note", "pieces": 8}],
            closing_count_state="COUNTED",
        )
        assert out["variance_status"] == "SEVERAL_DRAWERS"
        assert alerts == []


def _recon(db, monkeypatch):
    monkeypatch.setattr(finance_router.cash_recon, "_get_db", lambda: db)
    return asyncio.run(finance_router.cash_recon.cash_reconciliation_summary(
        from_date=DATE, to_date=DATE, store_id=STORE, current_user=MANAGER))["rows"]


class TestR7D53DrawerOrder:
    """D53 lookup order: an uncounted OPEN drawer first; otherwise the
    latest-opened; a legacy row with no opened_at orders by created_at, then by
    id. Legacy rows used to sort as the oldest whatever their created_at."""

    @staticmethod
    def _legacy(db, sid, created_at, rupees):
        db.get_collection("till_sessions").insert_one({
            "_id": sid, "session_id": sid, "store_id": STORE, "session_date": DATE,
            "status": till.STATUS_LOCKED, "created_at": created_at,
            "blind_count_paisa": rupees * 100, "variance_paisa": 0,
        })

    def test_legacy_rows_without_opened_at_order_by_created_at(self, db):
        # The LATER row is inserted FIRST, so insertion order cannot pass this.
        self._legacy(db, "TILL-LEGACY-B", datetime(2026, 11, 4, 15, 0), 3000)
        self._legacy(db, "TILL-LEGACY-A", datetime(2026, 11, 4, 9, 0), 1000)
        link = till.record_screen_close(
            db, store_id=STORE, session_date=DATE, counted_paisa=300000, actor=MANAGER)
        assert link["session_id"] == "TILL-LEGACY-B"
        assert till.shared_counted_paisa(link) == 300000
        assert [d["session_id"] for d in link["drawers"]] == ["TILL-LEGACY-A", "TILL-LEGACY-B"]

    def test_a_created_at_tie_falls_back_to_the_id(self, db):
        same = datetime(2026, 11, 4, 9, 0)
        self._legacy(db, "TILL-LEGACY-2", same, 3000)
        self._legacy(db, "TILL-LEGACY-1", same, 1000)
        link = till.record_screen_close(
            db, store_id=STORE, session_date=DATE, counted_paisa=300000, actor=MANAGER)
        assert link["session_id"] == "TILL-LEGACY-2"


# ===========================================================================
# D60  Each drawer's expected cash is its own float + its own shift's cash
# ===========================================================================


def _cash_sale(db, rupees, at="2026-11-04T05:00:00", *, paid=True):
    """A cash sale on DATE (``at`` is naive UTC; a string for the in-memory
    fake, a datetime on a native database)."""
    oid = f"ORD-D60-{uuid.uuid4().hex[:8]}"
    db.get_collection("orders").insert_one({
        "_id": oid, "order_id": oid, "store_id": STORE, "created_at": at,
        "payments": [{"method": "CASH", "amount": float(rupees)}] if paid else [],
    })
    return oid


def _drawer_a_then_b(
    db, *, a_sales, b_sales=(), between=(),
    at=("2026-11-04T05:00:00", "2026-11-04T10:00:00"),
):
    """Drawer A takes ``a_sales``, is counted at exactly that and LOCKED.
    ``between`` are sales after A's count but before its lock. Drawer B is then
    opened (float 0) and takes ``b_sales``."""
    for r in a_sales:
        _cash_sale(db, r, at[0])
    opened = till.open_session(
        db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER
    )
    a = opened["session"]["session_id"]
    assert till.blind_submit(
        db, a,
        blind_denominations=[{"face": 500, "kind": "note", "pieces": sum(a_sales) // 500}],
        actor=CASHIER,
    )["ok"]
    for r in between:
        _cash_sale(db, r, at[1])
    assert till.lock_session(db, a, actor=MANAGER)["ok"]  # balanced: no note needed
    b = till.open_session(
        db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
        shift="PM", actor=CASHIER,
    )["session"]["session_id"]
    for r in b_sales:
        _cash_sale(db, r, at[1])
    return a, b


def _alerts(monkeypatch):
    alerts = []
    monkeypatch.setattr(
        finance_router.cash_drawer.till_service, "raise_variance_task",
        lambda **kw: alerts.append(kw),
    )
    monkeypatch.setattr(till, "raise_variance_task", lambda **kw: alerts.append(kw))
    return alerts


def _cr_close_b(db, monkeypatch, pieces, note=None):
    return _cr_close_two_drawers(
        db, monkeypatch,
        denominations=[{"face": 500, "kind": "note", "pieces": pieces}],
        closing_count_state="COUNTED",
        **({"note": note} if note else {}),
    )


class TestD60EachDrawerItsOwnExpectedCash:
    """Lead's hold on head c1c4e2fa: blind_submit charged every drawer with the
    WHOLE day's cash, so drawer B's stored variance carried drawer A's sales.
    A Rs 1,000 counted and locked, B Rs 3,000 closed at Rs 3,000: B read
    Rs 1,000 short, Cash Register refused the close for a note and the manager
    was alerted to a shortage on a day that balanced."""

    def test_cash_register_closes_b_at_its_own_cash_no_note_no_alert(self, db, monkeypatch):
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        alerts = _alerts(monkeypatch)

        out = _cr_close_b(db, monkeypatch, 6)  # no note

        assert out["variance_status"] == "SEVERAL_DRAWERS"
        drawer = _till(db, b)
        assert (drawer["blind_count_paisa"], drawer["expected_cash_paisa"]) == (300000, 300000)
        assert (drawer["variance_paisa"], drawer["variance_status"]) == (0, "BALANCED")
        assert drawer["cash_sales_paisa"] == 300000
        assert drawer["by_mode"]["CASH"]["collected"] == 3000.0
        assert drawer["by_mode"]["CASH"]["count"] == 1
        assert drawer["expected_net_of_drawers"] == [a]
        assert alerts == []
        # A is untouched.
        assert (_till(db, a)["expected_cash_paisa"], _till(db, a)["variance_paisa"]) == (100000, 0)

    def test_reverse_a_3000_b_1000(self, db, monkeypatch):
        a, b = _drawer_a_then_b(db, a_sales=[3000], b_sales=[1000])
        alerts = _alerts(monkeypatch)
        _cr_close_b(db, monkeypatch, 2)
        assert (_till(db, b)["expected_cash_paisa"], _till(db, b)["variance_paisa"]) == (100000, 0)
        assert alerts == []

    def test_a_real_shortage_in_b_is_still_refused_without_a_note_and_alerted(
        self, db, monkeypatch
    ):
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        alerts = _alerts(monkeypatch)
        with pytest.raises(HTTPException) as exc:
            _cr_close_b(db, monkeypatch, 4)  # Rs 2,000 in a drawer that took 3,000
        assert exc.value.status_code == 400
        assert "variance_note_required" in str(exc.value.detail)
        assert _till(db, b)["variance_paisa"] == -100000  # B's own shortage only
        asyncio.run(finance_router.close_cash_register(
            body=finance_router.CashRegisterClose(
                session_id="CR-D40",
                denominations=[{"face": 500, "kind": "note", "pieces": 4}],
                closing_count_state="COUNTED",
                note="Rs 1,000 missing from the PM drawer",
            ),
            current_user=MANAGER,
        ))
        assert [x["variance_paisa"] for x in alerts] == [-100000]

    def test_overlap_sale_after_a_counted_before_its_lock_belongs_to_b(self, db, monkeypatch):
        a, b = _drawer_a_then_b(db, a_sales=[1000], between=[500], b_sales=[2000])
        alerts = _alerts(monkeypatch)
        _cr_close_b(db, monkeypatch, 5)  # 2,500
        assert (_till(db, b)["expected_cash_paisa"], _till(db, b)["variance_paisa"]) == (250000, 0)
        assert alerts == []

    def test_overlap_balance_paid_in_b_on_an_order_opened_in_a(self, db, monkeypatch):
        held = _cash_sale(db, 0, paid=False)  # order opened in A's shift, unpaid
        a, b = _drawer_a_then_b(db, a_sales=[1000])
        db.get_collection("orders").update_one(
            {"_id": held}, {"$push": {"payments": {"method": "CASH", "amount": 2000.0}}})
        alerts = _alerts(monkeypatch)
        _cr_close_b(db, monkeypatch, 4)
        assert (_till(db, b)["expected_cash_paisa"], _till(db, b)["variance_paisa"]) == (200000, 0)
        assert alerts == []

    def test_payouts_booked_by_day_are_charged_once(self, db, monkeypatch):
        """Expenses carry a day, not a time: B is charged only payouts its
        predecessors were not already charged with."""
        db.get_collection("expenses").insert_one({
            "store_id": STORE, "expense_date": DATE, "payment_mode": "CASH",
            "status": "APPROVED", "amount": 200.0, "category": "TEA",
        })
        a, b = _drawer_a_then_b(db, a_sales=[1200], b_sales=[3000])
        assert (_till(db, a)["cash_payouts_paisa"], _till(db, a)["expected_cash_paisa"]) == (20000, 100000)
        _cr_close_b(db, monkeypatch, 6)
        assert (_till(db, b)["cash_payouts_paisa"], _till(db, b)["variance_paisa"]) == (0, 0)

    def test_day_end_door_then_lock_b_needs_no_note(self, db, day_end, monkeypatch):
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        alerts = _alerts(monkeypatch)
        day_end(closing_cash=3000.0, system_cash=4000.0)
        locked = till.lock_session(db, b, actor=MANAGER)  # no note
        assert locked["ok"], locked
        assert (locked["session"]["variance_paisa"], locked["session"]["variance_status"]) == (0, "BALANCED")
        assert alerts == []

    def test_control_one_drawer_day_keeps_the_whole_day(self, db):
        """A single drawer is charged with the WHOLE day as before, including a
        sale rung up before it opened."""
        _cash_sale(db, 1000)
        sid = _blind_count(db, rupees=1500, lock=False)
        row = _till(db, sid)
        assert (row["expected_cash_paisa"], row["variance_paisa"]) == (100000, 50000)
        assert row["expected_net_of_drawers"] == []

    def test_unreadable_drawers_refuse_the_count_rather_than_charge_the_day(self, db):
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        coll = db.get_collection("till_sessions")

        def _boom(*a_, **k):
            raise AutoReconnect("till_sessions unreachable")

        coll.find = _boom
        try:
            res = till.blind_submit(
                db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}], actor=CASHIER)
        finally:
            del coll.find
        assert (res["ok"], res["http"], res["error"]) == (False, 503, "drawer_read_failed")
        assert (_till(db, b)["status"], _till(db, b)["variance_paisa"]) == (till.STATUS_OPEN, None)

    def test_zread_face_tally_covers_each_drawer_own_shift(self, db):
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        coll = db.get_collection("till_sessions")
        coll.update_one({"_id": a}, {"$set": {"blind_submitted_at": datetime(2026, 11, 4, 8, 0)}})
        assert till.blind_submit(
            db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}], actor=CASHIER)["ok"]
        coll.update_one({"_id": b}, {"$set": {"blind_submitted_at": datetime(2026, 11, 4, 12, 0)}})
        assert till.build_zread(db, b)["face_ledger"]["coverage"]["cash_sale_legs"] == 1
        assert till.build_zread(db, a)["face_ledger"]["coverage"]["cash_sale_legs"] == 1


def test_d60_second_drawer_own_cash_through_cash_register_on_real_mongo(monkeypatch):
    """The lead's reproduction on a native PyMongo Database with the real till
    indexes, through Finance > Cash Register's real close handler."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        a, b = _drawer_a_then_b(
            real_db, a_sales=[1000], b_sales=[3000],
            at=(datetime(2026, 11, 4, 5, 0), datetime(2026, 11, 4, 10, 0)),
        )
        alerts = _alerts(monkeypatch)

        out = _cr_close_b(_NativeDB(real_db), monkeypatch, 6)  # no note

        assert out["variance_status"] == "SEVERAL_DRAWERS"
        coll = real_db.get_collection("till_sessions")
        drawer = coll.find_one({"_id": b})
        assert (drawer["expected_cash_paisa"], drawer["variance_paisa"]) == (300000, 0)
        assert drawer["expected_net_of_drawers"] == [a]
        assert coll.find_one({"_id": a})["variance_paisa"] == 0
        assert alerts == []
    finally:
        client.drop_database(name)
        client.close()


# ===========================================================================
# D63  A manager alert per drawer (owner ruling 2026-08-25: alert above band)
# ===========================================================================


def _real_task_repo(monkeypatch, task_db):
    """The REAL TaskRepository / UserRepository behind the real
    raise_variance_task -- nothing on the alert path is monkeypatched except
    the accessor that hands out the repository."""
    import api.dependencies as deps
    from database.repositories.task_repository import TaskRepository
    from database.repositories.user_repository import UserRepository

    task_db.users.insert_one({
        "user_id": "u-mgr", "roles": ["STORE_MANAGER"], "store_ids": [STORE], "is_active": True,
    })
    monkeypatch.setattr(deps, "get_task_repository", lambda: TaskRepository(task_db.tasks))
    monkeypatch.setattr(deps, "get_user_repository", lambda: UserRepository(task_db.users))
    return task_db.tasks


def _a_short_and_locked_then_b(db, at=("2026-11-04T05:00:00", "2026-11-04T10:00:00")):
    """Drawer A takes Rs 1,000, is counted at Rs 500 (out of band) and LOCKED
    with a note, so its alert task is raised and stays OPEN. Drawer B is then
    opened and takes Rs 3,000."""
    _cash_sale(db, 1000, at[0])
    a = till.open_session(
        db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER
    )["session"]["session_id"]
    assert till.blind_submit(
        db, a, blind_denominations=[{"face": 500, "kind": "note", "pieces": 1}], actor=CASHIER
    )["ok"]
    assert till.lock_session(db, a, actor=MANAGER, variance_note="A Rs 500 short")["ok"]
    b = till.open_session(
        db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
        shift="PM", actor=CASHIER,
    )["session"]["session_id"]
    _cash_sale(db, 3000, at[1])
    return a, b


def _alert_rows(tasks):
    return sorted(
        ((t["payload"].get("session_id"), t["payload"]["variance_paisa"], t["source_ref"], t["status"])
         for t in tasks.find({})),
        key=lambda r: r[1],
    )


class TestD63AnAlertPerDrawer:
    """Lead's hold on head af3039c0: raise_variance_task deduped on
    till_variance:{store}:{day}, so while drawer A's alert task was open, drawer
    B's real shortage beyond the band raised no task and the manager saw only
    A's figure."""

    def test_b_short_through_cash_register_alerts_its_own_figure(self, db, monkeypatch):
        import mongomock

        tasks = _real_task_repo(monkeypatch, mongomock.MongoClient().db)
        a, b = _a_short_and_locked_then_b(db)
        assert tasks.count_documents({}) == 1

        _cr_close_b(db, monkeypatch, 4, note="Rs 1,000 missing from the PM drawer")

        rows = _alert_rows(tasks)
        assert [(r[0], r[1]) for r in rows] == [(b, -100000), (a, -50000)]
        assert {r[2] for r in rows} == {
            f"till_variance:{STORE}:{DATE}:{a}", f"till_variance:{STORE}:{DATE}:{b}"}
        assert all(r[3] == "OPEN" for r in rows)
        b_task = tasks.find_one({"payload.session_id": b})
        assert b_task["assigned_to"] == "u-mgr"
        assert "1000.00" in b_task["title"] and b in b_task["description"]

    def test_b_short_through_the_zread_lock_alerts_its_own_figure(self, db, monkeypatch):
        import mongomock

        tasks = _real_task_repo(monkeypatch, mongomock.MongoClient().db)
        a, b = _a_short_and_locked_then_b(db)
        assert till.blind_submit(
            db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 4}], actor=CASHIER
        )["ok"]
        assert till.lock_session(db, b, actor=MANAGER, variance_note="B Rs 1,000 short")["ok"]
        assert [(r[0], r[1]) for r in _alert_rows(tasks)] == [(b, -100000), (a, -50000)]

    def test_control_b_balanced_raises_nothing_more(self, db, monkeypatch):
        import mongomock

        tasks = _real_task_repo(monkeypatch, mongomock.MongoClient().db)
        a, b = _a_short_and_locked_then_b(db)
        assert till.blind_submit(
            db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}], actor=CASHIER
        )["ok"]
        assert till.lock_session(db, b, actor=MANAGER)["ok"]
        assert [r[1] for r in _alert_rows(tasks)] == [-50000]  # A's only

    def test_control_one_drawer_both_doors_and_a_relock_keep_one_alert(self, db, monkeypatch):
        import mongomock

        tasks = _real_task_repo(monkeypatch, mongomock.MongoClient().db)
        a = till.open_session(
            db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER
        )["session"]["session_id"]
        _cash_sale(db, 1000)
        _cr_close_b(db, monkeypatch, 1, note="Rs 500 short")  # links and counts A
        assert tasks.count_documents({}) == 1
        assert till.lock_session(db, a, actor=MANAGER, variance_note="Rs 500 short")["ok"]
        assert till.reopen_session(db, a, reason="recount", actor=MANAGER)["ok"]
        assert till.lock_session(db, a, actor=MANAGER, variance_note="still short")["ok"]
        assert [r[1] for r in _alert_rows(tasks)] == [-50000]  # one drawer, one alert


def test_d63_second_drawer_alert_on_real_mongo(monkeypatch):
    """The lead's reproduction on a native PyMongo Database: the real
    raise_variance_task and the real TaskRepository over a native tasks
    collection, drawer B closed short through Finance > Cash Register while
    drawer A's task is open."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        tasks = _real_task_repo(monkeypatch, real_db)
        a, b = _a_short_and_locked_then_b(
            real_db, at=(datetime(2026, 11, 4, 5, 0), datetime(2026, 11, 4, 10, 0)))
        assert tasks.count_documents({}) == 1

        _cr_close_b(_NativeDB(real_db), monkeypatch, 4, note="Rs 1,000 missing from the PM drawer")

        assert real_db.till_sessions.find_one({"_id": b})["variance_paisa"] == -100000
        assert [(r[0], r[1]) for r in _alert_rows(tasks)] == [(b, -100000), (a, -50000)]
    finally:
        client.drop_database(name)
        client.close()


# ===========================================================================
# Round 8 (Codex PR-1421-round2): F-1421-R2-A/B/C/D and the lead's D76
# ===========================================================================


def _fail_till_reads(coll, *, counted_only):
    """Make the till_sessions READS fail the way Codex injected them: the
    counted-drawers query (it filters on status) or, with counted_only False,
    every read of the shop-day."""
    real_find = coll.find

    def _find(query=None, *a, **k):
        if isinstance(query, dict) and query.get("session_date") == DATE:
            if not counted_only or isinstance(query.get("status"), dict):
                raise AutoReconnect("till_sessions read fault")
        return real_find(query, *a, **k)

    coll.find = _find
    return lambda: delattr(coll, "find")


class TestR8AReadFaultNeverInventsAShortage:
    """F-1421-R2-A: drawer A Rs 1,000 counted and locked, B takes Rs 3,000; the
    counted-drawers read fails while Finance > Cash Register closes B at
    Rs 3,000. The close used to judge B against the WHOLE day (Rs 4,000): a
    note was demanded, a SHORT close was stored and the manager alerted, and
    none of it went away after the read recovered and B balanced."""

    def test_register_close_during_the_fault_withholds_the_comparison(self, db, monkeypatch):
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        alerts = _alerts(monkeypatch)
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=True)
        try:
            out = _cr_close_b(db, monkeypatch, 6)  # no note: none is owed
        finally:
            restore()
        assert out["variance"] is None
        assert out["variance_status"] == "SEVERAL_DRAWERS"
        assert (out["till_link_ok"], out["till_link_error"]) == (False, "drawer_read_failed")
        assert out["till_drawers_on_day"] == 2
        assert alerts == []
        stored = db.get_collection("cash_register_sessions").find_one({"session_id": "CR-D40"})
        assert (stored["variance"], stored["variance_status"]) == (None, "SEVERAL_DRAWERS")
        # Recovery: B is counted on the till and balances; nothing false remains.
        assert till.blind_submit(
            db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}], actor=CASHIER)["ok"]
        assert till.lock_session(db, b, actor=MANAGER)["ok"]
        assert (_till(db, b)["variance_paisa"], _till(db, b)["variance_status"]) == (0, "BALANCED")
        assert alerts == []

    def test_unreadable_drawer_list_is_comparison_unavailable_not_a_shortage(self, db, monkeypatch):
        _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        alerts = _alerts(monkeypatch)
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=False)
        try:
            out = _cr_close_b(db, monkeypatch, 6)
        finally:
            restore()
        assert (out["variance"], out["variance_status"]) == (None, "COMPARISON_UNAVAILABLE")
        assert out["till_link_ok"] is False and alerts == []

    def test_d86_uncompared_register_close_is_left_out_of_recon_cash_sales(self, db, monkeypatch):
        """D86(3): the uncompared close's cash sales cover an unknown set of
        drawers. Once B is counted on the till the day's drawers are A 1,000
        and B 3,000; the close row added its own window's cash on top."""
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=False)
        try:
            out = _cr_close_b(db, monkeypatch, 6)
        finally:
            restore()
        assert out["variance_status"] == "COMPARISON_UNAVAILABLE"
        assert till.blind_submit(
            db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}], actor=CASHIER)["ok"]
        assert till.lock_session(db, b, actor=MANAGER)["ok"]
        monkeypatch.setattr(finance_router.cash_recon, "_get_db", lambda: db)
        res = asyncio.run(finance_router.cash_recon.cash_reconciliation_summary(
            from_date=DATE, to_date=DATE, store_id=STORE, current_user=MANAGER))
        cr = [r for r in res["rows"] if r["source"] == "CASH_REGISTER"]
        assert cr and cr[0]["variance_status"] == "COMPARISON_UNAVAILABLE"
        assert (cr[0]["cash_sales"], cr[0]["expected_cash"]) == (None, None)
        assert res["totals"]["cash_sales"] == 4000.0

    def test_day_end_door_during_the_fault_stores_no_whole_day_variance(self, db, day_end):
        _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=True)
        try:
            day_end(closing_cash=3000.0, system_cash=4000.0)
        finally:
            restore()
        close = _stored_close(db)
        assert close["variance"] is None
        assert close["till_drawers_on_day"] == 2 and close["variance_withheld_several_drawers"]

    def test_day_end_unreadable_drawer_list_stores_no_variance(self, db, day_end):
        _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=False)
        try:
            day_end(closing_cash=3000.0, system_cash=4000.0)
        finally:
            restore()
        close = _stored_close(db)
        assert close["variance"] is None and close["variance_comparison_unavailable"] is True

    def test_control_one_drawer_day_still_judged_when_the_count_read_fails(self, db, monkeypatch):
        """One drawer read before the fault: the whole window IS that drawer,
        so the comparison and the note demand stand as before."""
        till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER)
        _cash_sale(db, 3000)
        alerts = _alerts(monkeypatch)
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=True)
        try:
            with pytest.raises(HTTPException) as exc:
                _cr_close_b(db, monkeypatch, 4)
        finally:
            restore()
        assert exc.value.status_code == 400 and "variance_note_required" in str(exc.value.detail)
        assert alerts == []


def test_r8_a_read_fault_on_real_mongo(monkeypatch):
    """F-1421-R2-A on a native PyMongo Database: the real Collection.find of
    the counted-drawers query raises; the Cash Register close of balanced B
    stores no shortage and raises no alert; after recovery B balances."""
    from pymongo.collection import Collection

    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        a, b = _drawer_a_then_b(
            real_db, a_sales=[1000], b_sales=[3000],
            at=(datetime(2026, 11, 4, 5, 0), datetime(2026, 11, 4, 10, 0)),
        )
        alerts = _alerts(monkeypatch)
        real_find = Collection.find

        def _fault(self, query=None, *a_, **k):
            if (self.database.name == name and self.name == "till_sessions"
                    and isinstance(query, dict) and isinstance(query.get("status"), dict)):
                raise AutoReconnect("native counted-drawers read fault")
            return real_find(self, query, *a_, **k)

        monkeypatch.setattr(Collection, "find", _fault)
        out = _cr_close_b(_NativeDB(real_db), monkeypatch, 6)
        monkeypatch.setattr(Collection, "find", real_find)

        assert (out["variance"], out["variance_status"]) == (None, "SEVERAL_DRAWERS")
        stored = real_db.cash_register_sessions.find_one({"session_id": "CR-D40"})
        assert (stored["variance"], stored["variance_status"]) == (None, "SEVERAL_DRAWERS")
        assert alerts == []
        assert till.blind_submit(
            real_db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}],
            actor=CASHIER)["ok"]
        assert till.lock_session(real_db, b, actor=MANAGER)["ok"]
        assert real_db.till_sessions.find_one({"_id": b})["variance_paisa"] == 0
        assert alerts == []
    finally:
        client.drop_database(name)
        client.close()


def _through_the_app_handler(exc):
    """The app's own HTTPException handler, which turns detail into JSON."""
    import json

    from starlette.requests import Request

    from api.main import http_exception_handler

    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    response = asyncio.run(http_exception_handler(request, exc))
    return response.status_code, json.loads(response.body)


class TestR8BConflictCarriesTheSavedCloseAsJson:
    """F-1421-R2-B: the saved multi-drawer close carries till_drawers[].opened_at
    (a BSON datetime). The 409 for an identical retry or a lost acknowledgement
    put that record in HTTPException.detail and the app's handler could not
    encode it: 500 instead of 409."""

    def test_identical_retry_of_a_two_drawer_close_is_a_json_409(self, db, day_end):
        a, b = _two_locked_drawers(db, a_rupees=1000, b_rupees=3000)
        db.get_collection("till_sessions").update_one(
            {"_id": a}, {"$set": {"opened_at": datetime(2026, 11, 4, 4, 0)}})
        day_end(closing_cash=3000.0, system_cash=4000.0)
        with pytest.raises(HTTPException) as exc:
            day_end(closing_cash=3000.0, system_cash=4000.0)
        code, body = _through_the_app_handler(exc.value)
        assert code == 409
        drawers = body["detail"]["close"]["till_drawers"]
        assert [d["session_id"] for d in drawers] == [a, b]
        assert drawers[0]["opened_at"].startswith("2026-11-04T04:00")

    def test_cashier_conflict_stays_redacted(self, db, monkeypatch):
        monkeypatch.setattr(reports_router, "get_db", lambda: db)
        monkeypatch.setattr("api.services.stores_util.is_online_store", lambda _db, _sid: False)
        _two_locked_drawers(db, a_rupees=1000, b_rupees=3000)
        body = reports_router.DayEndCloseBody(date=DATE, store_id=STORE, closing_cash=3000.0)
        asyncio.run(reports_router.create_day_end_close(body, current_user=MANAGER))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(reports_router.create_day_end_close(body, current_user=CASHIER))
        code, out = _through_the_app_handler(exc.value)
        assert code == 409
        for d in out["detail"]["close"]["till_drawers"]:
            assert "variance_paisa" not in d and "variance_status" not in d


def _face_cash(rupees, at):
    from api.services import cash_denominations as den

    block = den.block_from_input(
        den.CashCountInput(state="COUNTED", rows=[{"face": 500, "pieces": rupees // 500}]),
        rupees * 100)
    return {"method": "CASH", "amount": float(rupees), "received_at": at,
            "cash_tendered_count": block}


def _tally_while_b_is_open(db, ts):
    """D86(1) fixture: ``ts(hour)`` is the stored time form (a string for the
    in-memory fake, a datetime on native Mongo)."""
    orders = db.get_collection("orders")
    orders.insert_one({"_id": "O-A", "store_id": STORE, "created_at": ts(5),
                       "payments": [_face_cash(1000, ts(5))]})
    orders.insert_one({"_id": "O-LATER", "store_id": STORE, "created_at": ts(5), "payments": []})
    coll = db.get_collection("till_sessions")
    a = till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
                          actor=CASHIER)["session"]["session_id"]
    coll.update_one({"_id": a}, {"$set": {"opened_at": datetime(2026, 11, 4, 3, 0)}})
    assert till.blind_submit(db, a, blind_denominations=[{"face": 500, "kind": "note", "pieces": 2}],
                             actor=CASHIER)["ok"]
    coll.update_one({"_id": a}, {"$set": {"blind_submitted_at": datetime(2026, 11, 4, 8, 0)}})
    assert till.lock_session(db, a, actor=MANAGER)["ok"]
    b = till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
                          shift="PM", actor=CASHIER)["session"]["session_id"]
    coll.update_one({"_id": b}, {"$set": {"opened_at": datetime(2026, 11, 4, 9, 0)}})
    orders.update_one({"_id": "O-LATER"}, {"$push": {"payments": _face_cash(2000, ts(10))}})
    assert coll.find_one({"_id": b})["status"] == till.STATUS_OPEN  # B not counted

    fa = till.build_zread(db, a)["face_ledger"]
    assert fa["read_ok"]
    assert fa["coverage"]["cash_sale_legs"] == 1
    assert fa["difference_paisa"] == 0
    fb = till.build_zread(db, b)["face_ledger"]  # B's open-drawer tally has it
    assert fb["coverage"]["cash_sale_legs"] == 1


def test_d86_tally_while_the_next_drawer_is_open_on_real_mongo():
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        _tally_while_b_is_open(real_db, lambda h: datetime(2026, 11, 4, h, 0))
    finally:
        client.drop_database(name)
        client.close()


class TestR8CFaceLedgerFollowsTheCash:
    """F-1421-R2-C: an order opened in drawer A's shift and paid (Rs 2,000 in
    four Rs 500 notes) in drawer B's shift. Both drawers balance in rupees,
    but A's note tally expected the four notes (4 missing) and B's expected
    none (4 extra), both reported as readable."""

    def test_a_later_balance_counts_in_the_later_drawer(self, db):
        orders = db.get_collection("orders")
        orders.insert_one({"_id": "O-A", "store_id": STORE, "created_at": "2026-11-04T05:00:00",
                           "payments": [_face_cash(1000, "2026-11-04T05:00:00")]})
        orders.insert_one({"_id": "O-LATER", "store_id": STORE, "created_at": "2026-11-04T05:00:00",
                           "payments": []})
        coll = db.get_collection("till_sessions")
        a = till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
                              actor=CASHIER)["session"]["session_id"]
        assert till.blind_submit(db, a, blind_denominations=[{"face": 500, "kind": "note", "pieces": 2}],
                                 actor=CASHIER)["ok"]
        coll.update_one({"_id": a}, {"$set": {"blind_submitted_at": datetime(2026, 11, 4, 8, 0)}})
        assert till.lock_session(db, a, actor=MANAGER)["ok"]
        b = till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
                              shift="PM", actor=CASHIER)["session"]["session_id"]
        orders.update_one({"_id": "O-LATER"},
                          {"$push": {"payments": _face_cash(2000, "2026-11-04T10:00:00+00:00")}})
        assert till.blind_submit(db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 4}],
                                 actor=CASHIER)["ok"]
        coll.update_one({"_id": b}, {"$set": {"blind_submitted_at": datetime(2026, 11, 4, 12, 0)}})
        assert till.lock_session(db, b, actor=MANAGER)["ok"]

        fa = till.build_zread(db, a)["face_ledger"]
        fb = till.build_zread(db, b)["face_ledger"]
        assert (fa["difference_paisa"], fb["difference_paisa"]) == (0, 0)
        assert (fa["coverage"]["cash_sale_legs"], fb["coverage"]["cash_sale_legs"]) == (1, 1)
        assert fa["read_ok"] and fb["read_ok"]

    def test_d86_earlier_drawer_tally_ends_where_the_next_drawer_opened(self, db):
        """D86(1): A is counted and printed while B is OPEN and not yet
        counted, and B has taken a Rs 2,000 balance on an order opened in A's
        shift. A's window used to run to the end of the day (only COUNTED
        drawers bounded it), so A's tally expected B's four notes."""
        _tally_while_b_is_open(db, lambda h: f"2026-11-04T{h:02d}:00:00")

    def test_control_one_drawer_day_keeps_every_payment_of_the_day(self, db):
        db.get_collection("orders").insert_one(
            {"_id": "O-1", "store_id": STORE, "created_at": "2026-11-04T05:00:00",
             "payments": [_face_cash(1000, "2026-11-04T05:00:00"),
                          _face_cash(2000, "2026-11-05T03:00:00")]})
        sid = till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
                                actor=CASHIER)["session"]["session_id"]
        assert till.blind_submit(db, sid, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}],
                                 actor=CASHIER)["ok"]
        ledger = till.build_zread(db, sid)["face_ledger"]
        assert ledger["coverage"]["cash_sale_legs"] == 2 and ledger["difference_paisa"] == 0


class TestR8DOneCanonicalBusinessDate:
    """F-1421-R2-D: '2026-7-12', '2026-07-12T00:00:00' and 'not-a-date' each
    stored their own immutable close (and touched the till)."""

    @pytest.mark.parametrize(
        "literal",
        ["2026-7-12", "2026-07-12T00:00:00", "not-a-date", "2026-02-30", "20260712", ""],
    )
    def test_a_non_canonical_date_is_refused(self, literal):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            reports_router.DayEndCloseBody(date=literal, store_id=STORE, closing_cash=1000.0)

    def test_control_a_real_date_is_accepted(self):
        assert reports_router.DayEndCloseBody(date="2026-07-12", store_id=STORE).date == "2026-07-12"


class TestR8D76OlderCodeDrawers:
    """D76: older code stored WHOLE-DAY cash on each drawer. A new-code drawer
    counted after one such snapshot subtracted it as if it were that drawer's
    own share: with old A (Rs 1,000) and old B (whole day Rs 4,000), new C
    taking Rs 500 read expected 0 and a Rs 500 OVERAGE, demanding a note."""

    @staticmethod
    def _old_drawer(db, sid, sales_paisa, counted_paisa, opened):
        db.get_collection("till_sessions").insert_one({
            "_id": sid, "session_id": sid, "store_id": STORE, "session_date": DATE,
            "status": till.STATUS_LOCKED, "opening_float_paisa": 0, "opened_at": opened,
            "blind_count_paisa": counted_paisa, "cash_sales_paisa": sales_paisa,
            "cash_refunds_paisa": 0, "cash_payouts_paisa": 0, "expected_cash_paisa": sales_paisa,
            "variance_paisa": counted_paisa - sales_paisa, "variance_status": "BALANCED",
            "tolerance_paisa": 0, "by_mode": {},
        })

    def test_a_later_drawer_gets_no_comparison_no_note_no_alert(self, db, monkeypatch):
        for rupees, at in ((1000, "2026-11-04T03:00:00"), (3000, "2026-11-04T06:00:00"),
                           (500, "2026-11-04T11:00:00")):
            _cash_sale(db, rupees, at)
        self._old_drawer(db, "OLD-A", 100000, 100000, "2026-11-04T02:00:00")
        self._old_drawer(db, "OLD-B", 400000, 300000, "2026-11-04T05:00:00")
        alerts = _alerts(monkeypatch)
        c = till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
                              actor=CASHIER)["session"]["session_id"]
        assert till.blind_submit(db, c, blind_denominations=[{"face": 500, "kind": "note", "pieces": 1}],
                                 actor=CASHIER)["ok"]
        row = _till(db, c)
        assert (row["expected_cash_paisa"], row["variance_paisa"]) == (None, None)
        assert row["variance_status"] == till.COMPARISON_UNAVAILABLE
        assert row["blind_count_paisa"] == 50000
        locked = till.lock_session(db, c, actor=MANAGER)  # no note owed
        assert locked["ok"] and alerts == []
        z = till.build_zread(db, c)
        assert z["comparison_unavailable_message"].startswith("Comparison not available")
        rows = [r for r in _recon(db, monkeypatch) if r["session_id"] == c]
        assert rows and rows[0]["variance"] is None and rows[0]["expected_cash"] is None
        assert rows[0]["variance_status"] == till.COMPARISON_UNAVAILABLE

    def test_d86_uncompared_drawer_cash_sales_not_available_and_out_of_totals(self, db, monkeypatch):
        """D86(3): the uncompared drawer stored the WHOLE day's cash (Rs 4,500)
        as its cash sales; the Z-Read showed it and the reconciliation totals
        added it to the old drawers' Rs 5,000."""
        for rupees, at in ((1000, "2026-11-04T03:00:00"), (3000, "2026-11-04T06:00:00"),
                           (500, "2026-11-04T11:00:00")):
            _cash_sale(db, rupees, at)
        self._old_drawer(db, "OLD-A", 100000, 100000, "2026-11-04T02:00:00")
        self._old_drawer(db, "OLD-B", 400000, 300000, "2026-11-04T05:00:00")
        c = till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
                              actor=CASHIER)["session"]["session_id"]
        assert till.blind_submit(db, c, blind_denominations=[{"face": 500, "kind": "note", "pieces": 1}],
                                 actor=CASHIER)["ok"]
        assert till.lock_session(db, c, actor=MANAGER)["ok"]
        z = till.build_zread(db, c)
        assert (z["cash_sales_paisa"], z["cash_refunds_paisa"], z["cash_payouts_paisa"]) == (None, None, None)
        assert z["counted_cash_paisa"] == 50000
        monkeypatch.setattr(finance_router.cash_recon, "_get_db", lambda: db)
        res = asyncio.run(finance_router.cash_recon.cash_reconciliation_summary(
            from_date=DATE, to_date=DATE, store_id=STORE, current_user=MANAGER))
        row = [r for r in res["rows"] if r["session_id"] == c][0]
        assert (row["cash_sales"], row["cash_refunds"], row["cash_expenses"]) == (None, None, None)
        assert row["counted_cash"] == 500.0
        assert res["totals"]["cash_sales"] == 5000.0
        assert res["totals"]["counted_cash"] == 4500.0

    def test_an_uncompared_count_gives_the_manager_a_check_by_hand_task(self, db, monkeypatch):
        """No comparison means no automatic alert, so a drawer that may be
        short would go unchecked (owner ruling 2026-08-25). The manager gets
        one task per drawer to check it by hand, on the Blind EOD Tally."""
        import mongomock

        _cash_sale(db, 1000, "2026-11-04T03:00:00")
        self._old_drawer(db, "OLD-A", 100000, 100000, "2026-11-04T02:00:00")
        tasks = _real_task_repo(monkeypatch, mongomock.MongoClient().db)
        c = till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0,
                              actor=CASHIER)["session"]["session_id"]
        assert till.blind_submit(db, c, blind_denominations=[{"face": 500, "kind": "note", "pieces": 1}],
                                 actor=CASHIER)["ok"]
        assert till.lock_session(db, c, actor=MANAGER)["ok"]
        rows = list(tasks.find({}))
        assert len(rows) == 1, rows
        assert rows[0]["source_ref"] == f"till_uncompared:{STORE}:{DATE}:{c}"
        assert rows[0]["link"] == "/finance/blind-eod"
        assert rows[0]["assigned_to"] == "u-mgr"  # the shop's store manager
        assert "Blind EOD Tally (Z-Read)" in rows[0]["description"]

    def test_control_a_compared_count_raises_no_check_task(self, db, monkeypatch):
        import mongomock

        tasks = _real_task_repo(monkeypatch, mongomock.MongoClient().db)
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        assert till.blind_submit(db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}],
                                 actor=CASHIER)["ok"]
        assert list(tasks.find({})) == []

    def test_control_new_code_earlier_drawer_still_takes_its_own_share(self, db):
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        assert till.blind_submit(db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}],
                                 actor=CASHIER)["ok"]
        assert (_till(db, b)["expected_cash_paisa"], _till(db, b)["variance_status"]) == (300000, "BALANCED")

    def test_old_writer_close_landing_beside_ours_wins_and_ours_is_taken_back(self, db, day_end, monkeypatch):
        sid = _blind_count(db, rupees=1000, lock=False)
        closes = db.get_collection("day_end_closes")
        real_insert = closes.insert_one
        raced = []

        def _old_writer_first(doc, *a, **k):
            if not raced:
                raced.append(1)
                real_insert({"store_id": STORE, "date": DATE, "closing_cash": 1000.0,
                             "closed_by": "old-writer"})
            return real_insert(doc, *a, **k)

        monkeypatch.setattr(closes, "insert_one", _old_writer_first)
        with pytest.raises(HTTPException) as exc:
            day_end(closing_cash=1000.0, system_cash=1000.0)
        assert exc.value.status_code == 409
        assert exc.value.detail["close"]["closed_by"] == "old-writer"
        rows = [d for d in closes.docs if d.get("date") == DATE]
        assert len(rows) == 1 and rows[0]["closed_by"] == "old-writer"
        assert _till(db, sid)["blind_count_paisa"] == 100000


class TestR8RegisterCloseLandsOnce:
    """Two Cash Register closes sent at once both read the session OPEN; the
    second's unguarded update overwrote the first record. The update is now
    guarded on not-yet-CLOSED: the second answers 409 and changes nothing."""

    def test_a_close_whose_read_was_stale_is_refused_409(self, db, monkeypatch):
        till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER)
        _cash_sale(db, 1000)
        first = _cr_close_b(db, monkeypatch, 2)
        assert first["status"] == "CLOSED"
        coll = db.get_collection("cash_register_sessions")
        real_find_one = coll.find_one
        stale = dict(real_find_one({"session_id": "CR-D40"}), status="OPEN")
        monkeypatch.setattr(coll, "find_one", lambda q, *a, **k: dict(stale))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(finance_router.close_cash_register(
                body=finance_router.CashRegisterClose(
                    session_id="CR-D40", counted_override=5000.0, note="second"),
                current_user=MANAGER))
        assert exc.value.status_code == 409
        stored = [d for d in coll.docs if d.get("session_id") == "CR-D40"]
        assert len(stored) == 1
        assert stored[0]["counted"] == first["counted"] == 1000.0
        assert stored[0].get("closing_note") is None


class TestR8ReconReadFaultIsNotAnEmptyConsole:
    """The reconciliation console turned an unreadable close collection into
    an empty list: a range with closes read as a range with none."""

    @pytest.mark.parametrize("name", ["cash_register_sessions", "till_sessions"])
    def test_unreadable_closes_are_503(self, db, monkeypatch, name):
        coll = db.get_collection(name)

        def _boom(*a, **k):
            raise AutoReconnect("closes unreadable")

        coll.find = _boom
        with pytest.raises(HTTPException) as exc:
            _recon(db, monkeypatch)
        assert exc.value.status_code == 503


# ===========================================================================
# Round 9 (review of head 4b470d57): a failed orders / expenses read while a
# drawer is counted is REFUSED, never stored as zero sales or zero payouts
# (D53: read faults shown as zero are corrections; D60 carried that zero onto
# the next drawer as a false shortage with a manager alert).
# ===========================================================================


def _fail_reads(db, name):
    coll = db.get_collection(name)

    def _boom(*a, **k):
        raise AutoReconnect(f"{name} read fault")

    coll.find = _boom
    return lambda: delattr(coll, "find")


def _follow_ups(monkeypatch):
    """Capture the "count it on the Z-Read" manager task at both doors."""
    raised = []

    def _raise(**kw):
        kw.pop("task_id", None)  # the fixed id has its own tests (round 11)
        raised.append(kw)
        return True

    monkeypatch.setattr(till, "raise_count_not_on_till_task", _raise)
    return raised


def _open(db, **kw):
    return till.open_session(
        db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER, **kw
    )["session"]["session_id"]


class TestR9CashReadFaultIsRefusedNotZero:
    """Reviewer's steps: drawer A takes Rs 1,000; A's blind count runs while the
    orders read fails once, and used to be SAVED with cash sales 0 (a +Rs 1,000
    OVERAGE). After recovery drawer B takes Rs 3,000 and is counted at Rs 3,000;
    D60 took A's 0 off the day so B was expected Rs 4,000: SHORT Rs 1,000 and a
    manager alert on a day that balanced."""

    @pytest.mark.parametrize("name", ["orders", "returns", "expenses"])
    def test_a_count_during_the_read_fault_is_refused_and_a_stays_open(self, db, name):
        _cash_sale(db, 1000)
        a = _open(db)
        restore = _fail_reads(db, name)
        try:
            res = till.blind_submit(
                db, a, blind_denominations=[{"face": 500, "kind": "note", "pieces": 2}], actor=CASHIER)
        finally:
            restore()
        assert (res["ok"], res["http"], res["error"]) == (False, 503, "cash_read_failed")
        assert "not saved" in res["message"]
        row = _till(db, a)
        assert row["status"] == till.STATUS_OPEN
        assert (row.get("expected_cash_paisa"), row.get("cash_sales_paisa"), row.get("variance_paisa")) == (None, None, None)

    @pytest.mark.parametrize("name", ["orders", "expenses"])
    def test_after_recovery_both_drawers_balance_and_nobody_is_alerted(self, db, monkeypatch, name):
        db.get_collection("expenses").insert_one({
            "store_id": STORE, "expense_date": DATE, "payment_mode": "CASH",
            "status": "APPROVED", "amount": 200.0, "category": "TEA",
        })
        _cash_sale(db, 1000)
        alerts = _alerts(monkeypatch)
        a = _open(db)
        notes = [{"face": 100, "kind": "note", "pieces": 8}]  # Rs 1,000 less Rs 200 paid out
        restore = _fail_reads(db, name)
        try:
            assert till.blind_submit(db, a, blind_denominations=notes, actor=CASHIER)["http"] == 503
        finally:
            restore()
        assert till.blind_submit(db, a, blind_denominations=notes, actor=CASHIER)["ok"]
        assert till.lock_session(db, a, actor=MANAGER)["ok"]  # no note owed
        b = _open(db, shift="PM")
        _cash_sale(db, 3000, "2026-11-04T10:00:00")
        assert till.blind_submit(
            db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}], actor=CASHIER)["ok"]
        locked = till.lock_session(db, b, actor=MANAGER)
        assert locked["ok"], locked
        assert (_till(db, a)["variance_paisa"], _till(db, b)["variance_paisa"]) == (0, 0)
        assert (_till(db, a)["cash_payouts_paisa"], _till(db, b)["cash_payouts_paisa"]) == (20000, 0)
        assert alerts == []

    def test_through_the_zread_door_the_refusal_is_a_503_with_a_sentence(self, db):
        a = _open(db)
        restore = _fail_reads(db, "orders")
        try:
            res = till.blind_submit(db, a, blind_denominations=[], blind_count_paisa=0, actor=CASHIER)
        finally:
            restore()
        with pytest.raises(HTTPException) as exc:
            till_router._raise(res)
        assert exc.value.status_code == 503
        assert exc.value.detail["code"] == "cash_read_failed"
        assert "Try again" in exc.value.detail["message"]

    @pytest.mark.parametrize("name", ["orders", "returns", "expenses"])
    def test_cash_register_close_during_the_fault_writes_nothing(self, db, monkeypatch, name):
        a = _open(db)
        _cash_sale(db, 1000)
        alerts = _alerts(monkeypatch)
        follow = _follow_ups(monkeypatch)
        restore = _fail_reads(db, name)
        try:
            with pytest.raises(HTTPException) as exc:
                _cr_close_b(db, monkeypatch, 2, note="should not be needed")
        finally:
            restore()
        assert exc.value.status_code == 503
        stored = db.get_collection("cash_register_sessions").find_one({"session_id": "CR-D40"})
        assert (stored["status"], stored.get("variance")) == ("OPEN", None)
        assert _till(db, a)["status"] == till.STATUS_OPEN
        assert alerts == [] and follow == []

    def test_day_end_during_the_fault_leaves_the_till_open_and_tells_the_manager(self, db, day_end, monkeypatch):
        a = _open(db)
        _cash_sale(db, 1000)
        follow = _follow_ups(monkeypatch)
        restore = _fail_reads(db, "orders")
        try:
            day_end(closing_cash=1000.0, system_cash=1000.0)
        finally:
            restore()
        close = _stored_close(db)
        assert (close["till_link_ok"], close["till_link_error"]) == (False, "cash_read_failed")
        row = _till(db, a)
        assert (row["status"], row.get("cash_sales_paisa")) == (till.STATUS_OPEN, None)
        assert follow == [{"store_id": STORE, "session_date": DATE, "source": "Day-End close"}]

    def test_register_close_whose_count_missed_the_till_tells_the_manager(self, db, monkeypatch):
        """F-1421-R2-A's withheld case: the comparison is withheld (no false
        shortage), and the manager gets a task to count the drawer on the
        Z-Read, so a real shortage on it is still checked (ruling 2026-08-25)."""
        _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        _alerts(monkeypatch)
        follow = _follow_ups(monkeypatch)
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=True)
        try:
            out = _cr_close_b(db, monkeypatch, 6)
        finally:
            restore()
        assert (out["till_link_ok"], out["variance_status"]) == (False, "SEVERAL_DRAWERS")
        assert follow == [{"store_id": STORE, "session_date": DATE, "source": "Finance cash-register close"}]

    def test_control_a_close_that_reached_the_till_raises_no_follow_up(self, db, monkeypatch):
        _open(db)
        _cash_sale(db, 1000)
        follow = _follow_ups(monkeypatch)
        out = _cr_close_b(db, monkeypatch, 2)
        assert (out["till_link_ok"], out["variance_status"]) == (True, "BALANCED")
        assert follow == []

    def test_the_follow_up_task_is_one_per_shop_day(self, monkeypatch):
        import mongomock

        tasks = _real_task_repo(monkeypatch, mongomock.MongoClient().db)
        for src in ("Day-End close", "Finance cash-register close"):
            till.raise_count_not_on_till_task(store_id=STORE, session_date=DATE, source=src)
        rows = list(tasks.find({}))
        assert len(rows) == 1
        assert rows[0]["source_ref"] == f"till_uncounted:{STORE}:{DATE}"
        # #1421 review: the button must open the screen that counts and locks
        # a till drawer (Finance > Blind EOD Tally), not Cash Register, whose
        # register is already CLOSED; the text names it as the menu does.
        assert rows[0]["link"] == "/finance/blind-eod"
        assert "Blind EOD Tally" in rows[0]["title"]
        assert "Blind EOD Tally (Z-Read)" in rows[0]["description"]


def test_r9_cash_read_fault_on_real_mongo(monkeypatch):
    """The reviewer's sequence on a native PyMongo Database: the real
    Collection.find of the orders collection raises during A's count; the
    count is refused and A stays OPEN; after recovery A and B both balance."""
    from pymongo.collection import Collection

    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        _cash_sale(real_db, 1000, datetime(2026, 11, 4, 5, 0))
        alerts = _alerts(monkeypatch)
        a = _open(real_db)
        notes = [{"face": 500, "kind": "note", "pieces": 2}]
        real_find = Collection.find

        def _fault(self, *a_, **k):
            if self.database.name == name and self.name == "orders":
                raise AutoReconnect("native orders read fault")
            return real_find(self, *a_, **k)

        monkeypatch.setattr(Collection, "find", _fault)
        res = till.blind_submit(real_db, a, blind_denominations=notes, actor=CASHIER)
        monkeypatch.setattr(Collection, "find", real_find)
        assert (res["ok"], res["http"], res["error"]) == (False, 503, "cash_read_failed")
        assert real_db.till_sessions.find_one({"_id": a})["status"] == till.STATUS_OPEN

        assert till.blind_submit(real_db, a, blind_denominations=notes, actor=CASHIER)["ok"]
        assert till.lock_session(real_db, a, actor=MANAGER)["ok"]
        b = _open(real_db, shift="PM")
        _cash_sale(real_db, 3000, datetime(2026, 11, 4, 10, 0))
        assert till.blind_submit(
            real_db, b, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}], actor=CASHIER)["ok"]
        assert till.lock_session(real_db, b, actor=MANAGER)["ok"]
        rows = {s["_id"]: s for s in real_db.till_sessions.find({})}
        assert (rows[a]["variance_paisa"], rows[b]["variance_paisa"]) == (0, 0)
        assert alerts == []
    finally:
        client.drop_database(name)
        client.close()


def test_r10_uncompared_count_task_on_real_mongo(monkeypatch):
    """#1421 review, on a native PyMongo Database: an older-code drawer A
    (whole-day snapshot) then a new-code drawer C. C's count is kept with no
    comparison, and the REAL task repository stores one check-by-hand task
    for the store manager that opens the Blind EOD Tally. The link-failed
    task on the same day opens the same screen."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        tasks = _real_task_repo(monkeypatch, real_db)
        _cash_sale(real_db, 1000, datetime(2026, 11, 4, 3, 0))
        TestR8D76OlderCodeDrawers._old_drawer(
            real_db, "OLD-A", 100000, 100000, datetime(2026, 11, 4, 2, 0))
        c = _open(real_db)
        notes = [{"face": 500, "kind": "note", "pieces": 1}]
        assert till.blind_submit(real_db, c, blind_denominations=notes, actor=CASHIER)["ok"]
        # Same drawer again: the guarded update refuses it, no second task.
        assert till.blind_submit(real_db, c, blind_denominations=notes, actor=CASHIER)["http"] == 409
        assert till.lock_session(real_db, c, actor=MANAGER)["ok"]
        row = real_db.till_sessions.find_one({"_id": c})
        assert (row["variance_status"], row["blind_count_paisa"]) == (till.COMPARISON_UNAVAILABLE, 50000)
        till.raise_count_not_on_till_task(store_id=STORE, session_date=DATE, source="Day-End close")
        rows = {t["source_ref"]: t for t in tasks.find({})}
        assert set(rows) == {f"till_uncompared:{STORE}:{DATE}:{c}", f"till_uncounted:{STORE}:{DATE}"}
        assert {t["link"] for t in rows.values()} == {"/finance/blind-eod"}
        assert all(t["assigned_to"] == "u-mgr" for t in rows.values())
    finally:
        client.drop_database(name)
        client.close()



# ===========================================================================
# D88(1)  The cash reconciliation totals never count one drawer twice
# ===========================================================================


def _recon_summary(db, monkeypatch, from_date=DATE):
    monkeypatch.setattr(finance_router.cash_recon, "_get_db", lambda: db)
    return asyncio.run(finance_router.cash_recon.cash_reconciliation_summary(
        from_date=from_date, to_date=DATE, store_id=STORE, current_user=MANAGER))


def _count_and_lock(db, sid, pieces):
    assert till.blind_submit(
        db, sid, blind_denominations=[{"face": 500, "kind": "note", "pieces": pieces}],
        actor=CASHIER)["ok"]
    assert till.lock_session(db, sid, actor=MANAGER)["ok"]


def _money_totals(res):
    t = res["totals"]
    return {k: t[k] for k in (
        "opening_float", "cash_sales", "cash_refunds", "cash_expenses",
        "expected_cash", "counted_cash", "variance")}


class TestD88OneDrawerNeverTwiceInTheTotals:
    """D88(1): a Cash Register close whose count did not reach the till is
    later counted on the Z-Read. Both rows used to add their cash: the
    builder probe read counted Rs 7,000 for a Rs 4,000 day (A 1,000 + the
    close 3,000 + B own 3,000)."""

    def test_uncompared_close_then_zread_counts_the_day_once(self, db, monkeypatch):
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=False)
        try:
            out = _cr_close_b(db, monkeypatch, 6)
        finally:
            restore()
        assert out["variance_status"] == "COMPARISON_UNAVAILABLE"
        _count_and_lock(db, b, 6)
        res = _recon_summary(db, monkeypatch)
        assert _money_totals(res) == {
            "opening_float": 0.0, "cash_sales": 4000.0, "cash_refunds": 0.0,
            "cash_expenses": 0.0, "expected_cash": 4000.0, "counted_cash": 4000.0,
            "variance": 0.0,
        }
        cr = [r for r in res["rows"] if r["source"] == "CASH_REGISTER"]
        # The row still shows, with its own count, marked.
        assert len(cr) == 1 and cr[0]["counted_cash"] == 3000.0
        assert cr[0]["also_counted_on_till"] is True
        assert res["totals"]["sessions"] == 2
        assert res["totals"]["also_counted_on_till"] == 1

    def test_several_drawers_failed_link_gives_no_doubled_cash_sales(self, db, monkeypatch):
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=True)
        try:
            out = _cr_close_b(db, monkeypatch, 6)
        finally:
            restore()
        assert (out["variance_status"], out["till_link_ok"]) == ("SEVERAL_DRAWERS", False)
        _count_and_lock(db, b, 6)
        res = _recon_summary(db, monkeypatch)
        money = _money_totals(res)
        assert (money["cash_sales"], money["counted_cash"], money["expected_cash"]) == (
            4000.0, 4000.0, 4000.0)
        cr = [r for r in res["rows"] if r["source"] == "CASH_REGISTER"]
        assert cr[0]["also_counted_on_till"] is True

    def test_one_drawer_failed_link_with_a_comparison_is_not_added_again(self, db, monkeypatch):
        """Same class, one-drawer day: the link failed after the drawers were
        read, so the close kept its comparison (BALANCED) and its count never
        reached the till; the drawer is then counted on the Z-Read."""
        sid = till.open_session(
            db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER,
        )["session"]["session_id"]
        _cash_sale(db, 3000)
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=True)
        try:
            out = _cr_close_b(db, monkeypatch, 6)
        finally:
            restore()
        assert out["till_link_ok"] is False and out["variance_status"] == "BALANCED"
        _count_and_lock(db, sid, 6)
        res = _recon_summary(db, monkeypatch)
        money = _money_totals(res)
        assert (money["counted_cash"], money["cash_sales"]) == (3000.0, 3000.0)
        assert res["totals"]["balanced"] == 1

    def test_control_a_lone_unlinked_close_still_counts(self, db, monkeypatch):
        """No till count that day: the close's counted cash is the only record
        of the drawer and keeps adding to the totals."""
        till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER)
        _cash_sale(db, 3000)
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=False)
        try:
            out = _cr_close_b(db, monkeypatch, 6)
        finally:
            restore()
        assert out["variance_status"] == "COMPARISON_UNAVAILABLE"
        res = _recon_summary(db, monkeypatch)
        assert res["totals"]["counted_cash"] == 3000.0
        cr = [r for r in res["rows"] if r["source"] == "CASH_REGISTER"]
        assert cr[0]["also_counted_on_till"] is False
        assert res["totals"]["also_counted_on_till"] == 0

    def test_control_another_day_does_not_hide_the_close(self, db, monkeypatch):
        """Only a till count of the SAME store and IST day takes the close out."""
        till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER)
        _cash_sale(db, 3000)
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=False)
        try:
            _cr_close_b(db, monkeypatch, 6)
        finally:
            restore()
        db.get_collection("till_sessions").insert_one({
            "_id": "TILL-OTHER-DAY", "session_id": "TILL-OTHER-DAY", "store_id": STORE,
            "session_date": "2026-11-03", "status": "LOCKED", "blind_count_paisa": 50000,
            "expected_cash_paisa": 50000, "variance_paisa": 0, "variance_status": "BALANCED",
        })
        res = _recon_summary(db, monkeypatch, from_date="2026-11-03")
        assert res["totals"]["counted_cash"] == 3500.0
        assert res["totals"]["also_counted_on_till"] == 0

    def test_linked_close_before_the_drawer_locks_is_not_added_beside_a(self, db, monkeypatch):
        """Same failure class, linked close: B counted through Cash Register
        (BLIND_SUBMITTED on the till, not yet a Z-Read row) beside locked A.
        The close's window cash is the whole day (Rs 4,000) on top of A's own
        Rs 1,000 row. Out of the totals until B locks, then folded into B."""
        a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        out = _cr_close_b(db, monkeypatch, 6)
        assert (out["variance_status"], out["till_link_ok"]) == ("SEVERAL_DRAWERS", True)
        res = _recon_summary(db, monkeypatch)
        money = _money_totals(res)
        assert (money["cash_sales"], money["expected_cash"]) == (1000.0, 1000.0)
        assert till.lock_session(db, b, actor=MANAGER)["ok"]
        res = _recon_summary(db, monkeypatch)
        assert [r["source"] for r in res["rows"]] == ["BLIND_EOD", "BLIND_EOD"]
        money = _money_totals(res)
        assert (money["cash_sales"], money["counted_cash"]) == (4000.0, 4000.0)

    def test_control_linked_one_drawer_close_folds_into_its_drawer(self, db, monkeypatch):
        sid = till.open_session(
            db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER,
        )["session"]["session_id"]
        _cash_sale(db, 3000)
        out = _cr_close_b(db, monkeypatch, 6)
        assert out["till_link_ok"] is True
        res = _recon_summary(db, monkeypatch)
        # Not yet locked: the close is the only row and counts.
        assert res["totals"]["counted_cash"] == 3000.0
        assert till.lock_session(db, sid, actor=MANAGER)["ok"]
        res = _recon_summary(db, monkeypatch)
        assert len(res["rows"]) == 1 and res["totals"]["counted_cash"] == 3000.0

    def test_two_register_closes_on_one_unlocked_drawer_count_it_once(self, db, monkeypatch):
        """Review of head 70c94024: one drawer, counted through a Cash Register
        close and not yet locked; a SECOND register session the same day
        closes on the same drawer and holds the same count. Both rows used to
        add it: counted Rs 6,000 for a Rs 3,000 drawer."""
        sid = till.open_session(
            db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER,
        )["session"]["session_id"]
        _cash_sale(db, 3000)
        first = _cr_close_b(db, monkeypatch, 6)
        second = _second_register_close(db, monkeypatch, 7)
        assert (first["till_session_id"], second["till_session_id"]) == (sid, sid)
        assert (first["till_link_ok"], second["till_link_ok"]) == (True, True)
        # D120: the drawer's count was made by the FIRST close, so the second
        # is judged on its own sheet from it (Rs 3,000 + the Rs 500 taken
        # after), with no note. The drawer's count is not replaced, and the
        # totals below still hold the drawer once (D88).
        assert (second["counted"], second["counted_from_shared_record"], second["variance_status"]) == (
            3500.0, False, "BALANCED")
        assert _till(db, sid)["blind_count_paisa"] == 300000

        res = _recon_summary(db, monkeypatch)
        assert _money_totals(res) == {
            "opening_float": 0.0, "cash_sales": 3000.0, "cash_refunds": 0.0,
            "cash_expenses": 0.0, "expected_cash": 3000.0, "counted_cash": 3000.0,
            "variance": 0.0,
        }
        by_id = {r["session_id"]: r for r in res["rows"]}
        # Both rows still show; the later one is marked and adds nothing.
        assert by_id["CR-D40"]["also_counted_on_till"] is False
        assert by_id["CR-D40-2"]["also_counted_on_till"] is True
        assert by_id["CR-D40-2"]["counted_cash"] == 3500.0  # its own sheet, on its own row
        t = res["totals"]
        assert (t["sessions"], t["also_counted_on_till"], t["later_closes"]) == (1, 1, 1)
        assert (t["balanced"], t["overage"], t["shortage"]) == (1, 0, 0)

        # Locked: the first close folds into the drawer's one Z-Read row; the
        # later close keeps its own row, still out of the totals (D121).
        assert till.lock_session(db, sid, actor=MANAGER)["ok"]
        res = _recon_summary(db, monkeypatch)
        assert sorted(r["source"] for r in res["rows"]) == ["BLIND_EOD", "CASH_REGISTER"]
        assert (res["totals"]["counted_cash"], res["totals"]["later_closes"]) == (3000.0, 1)

    def test_control_two_register_closes_with_no_till_link_both_count(self, db, monkeypatch):
        """Two closes that name no till drawer (records from before the shared
        till record) are two counts and keep adding, as before."""
        coll = db.get_collection("cash_register_sessions")
        for n, at in ((1, "09:00:00"), (2, "12:00:00")):
            coll.insert_one({
                "_id": f"CR-OLD-{n}", "session_id": f"CR-OLD-{n}", "store_id": STORE,
                "status": "CLOSED", "opening_float": 0.0, "counted": 1000.0, "expected": 1000.0,
                "cash_sales": 1000.0, "closed_at": f"{DATE}T{at}",
            })
        res = _recon_summary(db, monkeypatch)
        assert res["totals"]["counted_cash"] == 2000.0
        assert res["totals"]["also_counted_on_till"] == 0


def _second_register_close(db, monkeypatch, pieces, note=None):
    """A SECOND Cash Register session of DATE, opened after the first closed
    (carrying its Rs 3,000) and closed through the real handler after one
    more Rs 500 cash sale."""
    monkeypatch.setattr(finance_router, "_get_db", lambda: db)
    monkeypatch.setattr(finance_router, "_iso_now", lambda: f"{DATE}T13:00:00")
    db.get_collection("cash_register_sessions").insert_one({
        "_id": "CR-D40-2", "session_id": "CR-D40-2", "store_id": STORE, "status": "OPEN",
        "opening_float": 3000.0, "opening_denominations": [], "opened_at": f"{DATE}T12:30:00",
    })
    _cash_sale(db, 500, f"{DATE}T12:45:00")
    body = finance_router.CashRegisterClose(
        session_id="CR-D40-2",
        denominations=[{"face": 500, "kind": "note", "pieces": pieces}],
        closing_count_state="COUNTED",
        **({"note": note} if note else {}),
    )
    return asyncio.run(finance_router.close_cash_register(body=body, current_user=MANAGER))


def test_d88_uncompared_close_counted_once_on_real_mongo(monkeypatch):
    """D88(1) on a native PyMongo Database: the drawer-list read of the close
    really fails (Collection.find), B is then counted on the Z-Read, and the
    reconciliation reads the stored rows back: counted 4,000 for a 4,000 day."""
    from pymongo.collection import Collection

    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        a, b = _drawer_a_then_b(
            real_db, a_sales=[1000], b_sales=[3000],
            at=(datetime(2026, 11, 4, 5, 0), datetime(2026, 11, 4, 10, 0)),
        )
        real_find = Collection.find

        def _fault(self, query=None, *a_, **k):
            if (self.database.name == name and self.name == "till_sessions"
                    and isinstance(query, dict) and query.get("session_date") == DATE):
                raise AutoReconnect("native drawer-list read fault")
            return real_find(self, query, *a_, **k)

        monkeypatch.setattr(Collection, "find", _fault)
        out = _cr_close_b(_NativeDB(real_db), monkeypatch, 6)
        monkeypatch.setattr(Collection, "find", real_find)
        assert out["variance_status"] == "COMPARISON_UNAVAILABLE"
        _count_and_lock(real_db, b, 6)
        res = _recon_summary(_NativeDB(real_db), monkeypatch)
        assert res["totals"]["counted_cash"] == 4000.0
        assert res["totals"]["cash_sales"] == 4000.0
        assert res["totals"]["expected_cash"] == 4000.0
        assert res["totals"]["also_counted_on_till"] == 1
    finally:
        client.drop_database(name)
        client.close()


# ===========================================================================
# Round 11 (Codex PR-1421-round3, F-1421-R3-A): a close that COMMITTED but
# lost its acknowledgement keeps its manager follow-up
# ===========================================================================
# Drawer A Rs 1,000 counted and locked, B takes Rs 3,000; the counted-drawer
# read fails so the Cash Register close of B stores till_link_ok False; Mongo
# commits the CLOSED update and loses its acknowledgement. The route answered
# 500, the identical retry a bare 409 "Session already closed", and the "count
# the drawer on the till" task was never raised. The same class sat on the
# sibling writes (the register's variance alert, the Blind EOD lock, the blind
# count, the Day-End close).

_TASK_RAISERS = (
    "raise_variance_task", "raise_count_not_on_till_task", "raise_count_not_compared_task")


def _raised(monkeypatch, store_up=lambda: True):
    """Every manager task the till code raises, as (raiser, fixed task id).
    ``store_up`` False = the task store cannot be written (the raiser answers
    False, as the real one does)."""
    raised = []

    def _capture(name):
        def _raise(**kw):
            if not store_up():
                return False
            raised.append((name, kw.get("task_id")))
            return True
        return _raise

    for name in _TASK_RAISERS:
        monkeypatch.setattr(till, name, _capture(name))
    return raised


def _lose_ack(monkeypatch, coll, method, when=lambda *a, **k: True):
    """Codex's fault: the REAL write runs (it commits), then its
    acknowledgement is lost, once."""
    real = getattr(coll, method)
    fired = []

    def _write(*a, **k):
        out = real(*a, **k)
        if not fired and when(*a, **k):
            fired.append(True)
            raise AutoReconnect(f"{method} committed, acknowledgement lost")
        return out

    monkeypatch.setattr(coll, method, _write)
    return fired


def _fail_read_back(monkeypatch, coll, fired):
    """The one read straight after the lost acknowledgement fails too."""
    real = coll.find_one
    failed = []

    def _find_one(*a, **k):
        if fired and not failed:
            failed.append(True)
            raise AutoReconnect("read-back failed")
        return real(*a, **k)

    monkeypatch.setattr(coll, "find_one", _find_one)


def _sets_status(status):
    return lambda *a, **k: a[1].get("$set", {}).get("status") == status


def _cr_again(db, monkeypatch, **body_kw):
    """The same Cash Register close sent again (no new session inserted)."""
    monkeypatch.setattr(finance_router, "_get_db", lambda: db)
    monkeypatch.setattr(finance_router, "_iso_now", lambda: f"{DATE}T12:05:00")
    body_kw.setdefault("denominations", [{"face": 500, "kind": "note", "pieces": 6}])
    body_kw.setdefault("closing_count_state", "COUNTED")
    return asyncio.run(finance_router.close_cash_register(
        body=finance_router.CashRegisterClose(session_id="CR-D40", **body_kw),
        current_user=MANAGER))


def _cr_stored(db):
    rows = [d for d in db.get_collection("cash_register_sessions").docs
            if d.get("session_id") == "CR-D40"]
    assert len(rows) == 1
    return rows[0]


def _cr_list(db, monkeypatch):
    monkeypatch.setattr(finance_router, "_get_db", lambda: db)
    return asyncio.run(finance_router.list_cash_register_sessions(
        store_id=STORE, status=None, limit=50, current_user=MANAGER))


class TestR11RegisterCloseKeepsItsFollowUp:
    def _failed_link_close(self, db, monkeypatch, *, read_back_fails=False):
        """Codex's sequence up to and including the lost acknowledgement."""
        _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        raised = _raised(monkeypatch)
        cr = db.get_collection("cash_register_sessions")
        fired = _lose_ack(monkeypatch, cr, "update_one", _sets_status("CLOSED"))
        if read_back_fails:
            _fail_read_back(monkeypatch, cr, fired)
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=True)
        try:
            try:
                first = _cr_close_b(db, monkeypatch, 6)
            except HTTPException as exc:
                first = exc
        finally:
            restore()
        assert fired == [True]
        return raised, first

    def test_lost_ack_answers_the_saved_close_and_raises_the_task_once(self, db, monkeypatch):
        raised, first = self._failed_link_close(db, monkeypatch)
        # The write landed and the handler found that out: no error at all.
        assert isinstance(first, dict), first
        assert (first["status"], first["counted"], first["till_link_ok"]) == ("CLOSED", 3000.0, False)
        task = ("raise_count_not_on_till_task", till.followup_task_id("uncounted", "CR-D40"))
        assert raised == [task]
        assert _cr_stored(db)["manager_followup"]["pending"] is False
        # Identical retry, then a retry with a different count: the saved
        # close comes back, nothing is rewritten, no second task.
        for pieces in (6, 2):
            with pytest.raises(HTTPException) as exc:
                _cr_again(db, monkeypatch,
                          denominations=[{"face": 500, "kind": "note", "pieces": pieces}])
            code, body = _through_the_app_handler(exc.value)
            assert code == 409
            assert body["detail"]["message"] == "Session already closed"
            assert body["detail"]["session"]["counted"] == 3000.0
            assert body["detail"]["session"]["till_link_ok"] is False
            assert "_id" not in body["detail"]["session"]
        assert raised == [task]
        assert _cr_stored(db)["counted"] == 3000.0

    def test_codex_sequence_500_then_the_retry_restores_the_task_once(self, db, monkeypatch):
        """The read-back fails as well, so the first answer is still the 500
        Codex saw. The identical retry is the recovery path."""
        raised, first = self._failed_link_close(db, monkeypatch, read_back_fails=True)
        assert isinstance(first, HTTPException) and first.status_code == 500
        stored = _cr_stored(db)
        assert (stored["status"], stored["till_link_ok"]) == ("CLOSED", False)
        assert stored["manager_followup"]["pending"] is True and raised == []

        with pytest.raises(HTTPException) as exc:
            _cr_again(db, monkeypatch)
        code, body = _through_the_app_handler(exc.value)
        assert code == 409 and body["detail"]["session"]["counted"] == 3000.0
        assert body["detail"]["session"]["manager_followup"]["pending"] is False
        task = ("raise_count_not_on_till_task", till.followup_task_id("uncounted", "CR-D40"))
        assert raised == [task]
        assert _cr_stored(db)["manager_followup"]["pending"] is False
        with pytest.raises(HTTPException):
            _cr_again(db, monkeypatch)
        _cr_list(db, monkeypatch)
        assert raised == [task]

    def test_no_retry_at_all_the_next_load_of_the_screen_restores_it(self, db, monkeypatch):
        """A stopped process leaves no retry: once the session shows as closed
        the screen has no Close button. Reading the list finishes it."""
        raised, first = self._failed_link_close(db, monkeypatch, read_back_fails=True)
        assert isinstance(first, HTTPException) and raised == []
        out = _cr_list(db, monkeypatch)
        assert raised == [
            ("raise_count_not_on_till_task", till.followup_task_id("uncounted", "CR-D40"))]
        assert out["sessions"][0]["manager_followup"]["pending"] is False
        _cr_list(db, monkeypatch)
        assert len(raised) == 1

    def test_task_store_down_the_close_stays_owing_until_it_is_back(self, db, monkeypatch):
        _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        up = []
        raised = _raised(monkeypatch, store_up=lambda: bool(up))
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=True)
        try:
            out = _cr_close_b(db, monkeypatch, 6)
        finally:
            restore()
        assert out["status"] == "CLOSED" and out["manager_followup"]["pending"] is True
        _cr_list(db, monkeypatch)
        assert raised == [] and _cr_stored(db)["manager_followup"]["pending"] is True
        up.append(True)
        _cr_list(db, monkeypatch)
        assert len(raised) == 1 and _cr_stored(db)["manager_followup"]["pending"] is False

    def test_variance_alert_survives_the_lost_ack(self, db, monkeypatch):
        """One drawer, Rs 1,000 short with a note: the alert above the band
        (owner ruling 2026-08-25) was lost the same way."""
        till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER)
        _cash_sale(db, 3000)
        raised = _raised(monkeypatch)
        cr = db.get_collection("cash_register_sessions")
        fired = _lose_ack(monkeypatch, cr, "update_one", _sets_status("CLOSED"))
        _fail_read_back(monkeypatch, cr, fired)
        with pytest.raises(HTTPException) as exc:
            _cr_close_b(db, monkeypatch, 4, note="Rs 1,000 missing")
        assert exc.value.status_code == 500 and raised == []
        with pytest.raises(HTTPException) as exc:
            _cr_again(db, monkeypatch, denominations=[{"face": 500, "kind": "note", "pieces": 4}],
                      note="Rs 1,000 missing")
        assert exc.value.status_code == 409
        assert exc.value.detail["session"]["variance"] == -1000.0
        assert raised == [("raise_variance_task", till.followup_task_id("variance", "CR-D40"))]
        owed = _cr_stored(db)["manager_followup"]["tasks"][0]
        assert (owed["variance_paisa"], owed["tolerance_paisa"], owed["note"]) == (
            -100000, 0, "Rs 1,000 missing")

    def test_a_balanced_linked_close_owes_nothing_and_writes_nothing_extra(self, db, monkeypatch):
        till.open_session(db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER)
        _cash_sale(db, 3000)
        raised = _raised(monkeypatch)
        out = _cr_close_b(db, monkeypatch, 6)
        assert out["variance_status"] == "BALANCED"
        assert (out["manager_followup"]["pending"], out["manager_followup"]["tasks"]) == (False, [])
        assert raised == []

    def test_a_count_that_landed_on_the_till_is_not_reported_as_missing(self, db, monkeypatch):
        """The TILL write lost its acknowledgement inside the register close:
        the close said the count never reached the till and raised the task,
        for a drawer the till showed as counted."""
        sid = till.open_session(
            db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER
        )["session"]["session_id"]
        _cash_sale(db, 3000)
        raised = _raised(monkeypatch)
        fired = _lose_ack(monkeypatch, db.get_collection("till_sessions"),
                          "find_one_and_update", _sets_status(till.STATUS_BLIND_SUBMITTED))
        out = _cr_close_b(db, monkeypatch, 6)
        assert fired == [True]
        assert (out["till_link_ok"], out["counted"], out["variance_status"]) == (True, 3000.0, "BALANCED")
        assert _till(db, sid)["blind_count_paisa"] == 300000 and raised == []


class TestR11RegisterReadFaultsAreSaidSo:
    """A read that FAILED was answered as "not found" / "no session open" /
    an empty history."""

    def _broken(self, db, monkeypatch, method):
        monkeypatch.setattr(finance_router, "_get_db", lambda: db)
        coll = db.get_collection("cash_register_sessions")

        def _boom(*a, **k):
            raise AutoReconnect("cash_register_sessions read fault")

        monkeypatch.setattr(coll, method, _boom)

    def test_close_of_an_unreadable_session_is_503_not_404(self, db, monkeypatch):
        self._broken(db, monkeypatch, "find_one")
        with pytest.raises(HTTPException) as exc:
            _cr_again(db, monkeypatch)
        assert exc.value.status_code == 503 and "could not be read" in exc.value.detail

    def test_an_unreadable_history_is_503_not_an_empty_screen(self, db, monkeypatch):
        self._broken(db, monkeypatch, "find")
        with pytest.raises(HTTPException) as exc:
            _cr_list(db, monkeypatch)
        assert exc.value.status_code == 503

    def test_an_unreadable_open_session_is_503_not_no_session_open(self, db, monkeypatch):
        self._broken(db, monkeypatch, "find_one")
        with pytest.raises(HTTPException) as exc:
            _cr_list(db, monkeypatch)
        assert exc.value.status_code == 503

    def test_open_refuses_when_the_one_open_session_guard_cannot_be_read(self, db, monkeypatch):
        self._broken(db, monkeypatch, "find_one")
        monkeypatch.setattr(
            finance_router.cash_drawer, "is_online_store", lambda _db, _sid: False)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(finance_router.open_cash_register(
                body=finance_router.CashRegisterOpen(store_id=STORE, opening_float=500.0),
                current_user=MANAGER))
        assert exc.value.status_code == 503
        assert db.get_collection("cash_register_sessions").docs == []

    def test_preview_read_fault_shows_no_preview_not_one_from_zero(self, db, monkeypatch):
        db.get_collection("cash_register_sessions").insert_one({
            "session_id": "CR-OPEN", "store_id": STORE, "status": "OPEN",
            "opening_float": 0.0, "opened_at": f"{DATE}T04:00:00"})
        monkeypatch.setattr(db.get_collection("orders"), "find", _raise_read_fault)
        out = _cr_list(db, monkeypatch)
        assert out["open_session"]["session_id"] == "CR-OPEN"
        assert out["expected_preview"] is None


def _raise_read_fault(*a, **k):
    raise AutoReconnect("read fault")


class TestR11TillLockAndCountKeepTheirFollowUp:
    def _short_drawer(self, db):
        _cash_sale(db, 1000)
        return _blind_count(db, rupees=500, lock=False)

    def test_lock_lost_ack_is_a_lock_with_its_audit_and_one_alert(self, db, monkeypatch, audit_rows):
        sid = self._short_drawer(db)
        raised = _raised(monkeypatch)
        fired = _lose_ack(monkeypatch, db.get_collection("till_sessions"),
                          "find_one_and_update", _sets_status(till.STATUS_LOCKED))
        res = till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")
        assert fired == [True] and res["ok"], res
        assert res["session"]["status"] == till.STATUS_LOCKED
        assert len(raised) == 1 and raised[0][0] == "raise_variance_task"
        assert [r["action"] for r in audit_rows].count("till.lock") == 1
        again = till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")
        assert (again["ok"], again["http"], again["error"]) == (False, 409, "already_locked")
        assert len(raised) == 1

    def test_lock_500_then_the_retry_or_a_read_restores_the_alert_once(self, db, monkeypatch):
        sid = self._short_drawer(db)
        raised = _raised(monkeypatch)
        coll = db.get_collection("till_sessions")
        fired = _lose_ack(monkeypatch, coll, "find_one_and_update", _sets_status(till.STATUS_LOCKED))
        _fail_read_back(monkeypatch, coll, fired)
        res = till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")
        assert (res["ok"], res["http"]) == (False, 500) and raised == []
        assert _till(db, sid)["status"] == till.STATUS_LOCKED
        assert _till(db, sid)["manager_followup"]["pending"] is True
        again = till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")
        assert (again["http"], again["error"]) == (409, "already_locked")
        assert len(raised) == 1 and _till(db, sid)["manager_followup"]["pending"] is False
        till.get_session(db, sid)
        till.list_sessions(db, store_id=STORE)
        assert len(raised) == 1

    def test_no_retry_a_read_of_the_drawer_restores_the_alert(self, db, monkeypatch):
        sid = self._short_drawer(db)
        raised = _raised(monkeypatch)
        coll = db.get_collection("till_sessions")
        fired = _lose_ack(monkeypatch, coll, "find_one_and_update", _sets_status(till.STATUS_LOCKED))
        _fail_read_back(monkeypatch, coll, fired)
        assert till.lock_session(db, sid, actor=MANAGER, variance_note="short")["http"] == 500
        till.list_sessions(db, store_id=STORE)
        assert len(raised) == 1
        till.list_sessions(db, store_id=STORE)
        till.get_session(db, sid)
        assert len(raised) == 1

    def test_a_relock_after_a_reopen_is_a_new_lock_and_alerts_again(self, db, monkeypatch):
        sid = self._short_drawer(db)
        raised = _raised(monkeypatch)
        assert till.lock_session(db, sid, actor=MANAGER, variance_note="short")["ok"]
        assert till.reopen_session(db, sid, reason="recount", actor=MANAGER)["ok"]
        time.sleep(0.005)  # a later lock instant, to the millisecond
        assert till.lock_session(db, sid, actor=MANAGER, variance_note="still short")["ok"]
        assert len(raised) == 2 and raised[0][1] != raised[1][1]

    def test_uncompared_count_lost_ack_keeps_the_check_by_hand_task(self, db, monkeypatch):
        _cash_sale(db, 1000, "2026-11-04T03:00:00")
        TestR8D76OlderCodeDrawers._old_drawer(db, "OLD-A", 100000, 100000, "2026-11-04T02:00:00")
        c = _open(db)
        raised = _raised(monkeypatch)
        coll = db.get_collection("till_sessions")
        fired = _lose_ack(
            monkeypatch, coll, "find_one_and_update", _sets_status(till.STATUS_BLIND_SUBMITTED))
        notes = [{"face": 500, "kind": "note", "pieces": 1}]
        res = till.blind_submit(db, c, blind_denominations=notes, idempotency_key="k1", actor=CASHIER)
        assert fired == [True] and res["ok"], res
        assert res["session"]["variance_status"] == till.COMPARISON_UNAVAILABLE
        task = ("raise_count_not_compared_task", till.followup_task_id("uncompared", c))
        assert raised == [task]
        # The cashier's reply never carries the follow-up record.
        assert "manager_followup" not in till.redact_for_cashier(res["session"])
        again = till.blind_submit(db, c, blind_denominations=notes, idempotency_key="k1", actor=CASHIER)
        assert again["ok"] and again["idempotent"] and raised == [task]

    def test_uncompared_count_500_then_the_retry_restores_the_task(self, db, monkeypatch):
        _cash_sale(db, 1000, "2026-11-04T03:00:00")
        TestR8D76OlderCodeDrawers._old_drawer(db, "OLD-A", 100000, 100000, "2026-11-04T02:00:00")
        c = _open(db)
        raised = _raised(monkeypatch)
        coll = db.get_collection("till_sessions")
        fired = _lose_ack(
            monkeypatch, coll, "find_one_and_update", _sets_status(till.STATUS_BLIND_SUBMITTED))
        _fail_read_back(monkeypatch, coll, fired)
        notes = [{"face": 500, "kind": "note", "pieces": 1}]
        res = till.blind_submit(db, c, blind_denominations=notes, actor=CASHIER)
        assert (res["ok"], res["http"]) == (False, 500) and raised == []
        again = till.blind_submit(db, c, blind_denominations=notes, actor=CASHIER)
        assert (again["http"], again["error"]) == (409, "already_submitted")
        assert raised == [("raise_count_not_compared_task", till.followup_task_id("uncompared", c))]

    def test_a_lock_carries_forward_what_the_count_still_owes(self, db, monkeypatch):
        _cash_sale(db, 1000, "2026-11-04T03:00:00")
        TestR8D76OlderCodeDrawers._old_drawer(db, "OLD-A", 100000, 100000, "2026-11-04T02:00:00")
        c = _open(db)
        up = []
        raised = _raised(monkeypatch, store_up=lambda: bool(up))
        notes = [{"face": 500, "kind": "note", "pieces": 1}]
        assert till.blind_submit(db, c, blind_denominations=notes, actor=CASHIER)["ok"]
        assert till.lock_session(db, c, actor=MANAGER)["ok"]
        owed = _till(db, c)["manager_followup"]
        # (beside the audit rows of the count and of the lock)
        assert owed["pending"] is True
        assert [t["kind"] for t in owed["tasks"] if t["kind"] != "audit"] == ["uncompared"]
        up.append(True)
        till.get_session(db, c)
        assert raised == [("raise_count_not_compared_task", till.followup_task_id("uncompared", c))]

    def test_an_unreadable_session_is_503_not_404(self, db, monkeypatch):
        sid = self._short_drawer(db)
        monkeypatch.setattr(db.get_collection("till_sessions"), "find_one", _raise_read_fault)
        for res in (
            till.lock_session(db, sid, actor=MANAGER, variance_note="x"),
            till.blind_submit(db, sid, blind_denominations=[], actor=CASHIER),
            till.reopen_session(db, sid, reason="recount", actor=MANAGER),
        ):
            assert (res["ok"], res["http"], res["error"]) == (False, 503, "session_read_failed")
            assert "could not be read" in res["message"]
        monkeypatch.setattr(till_router, "_get_db", lambda: db)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(till_router.get_till_session(sid, current_user=MANAGER))
        assert exc.value.status_code == 503

    def test_an_unreadable_drawer_list_is_503_not_no_drawer_today(self, db, monkeypatch):
        self._short_drawer(db)
        monkeypatch.setattr(till_router, "_get_db", lambda: db)
        monkeypatch.setattr(db.get_collection("till_sessions"), "find", _raise_read_fault)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(till_router.list_till_sessions(
                store_id=STORE, date=None, status=None, limit=50, current_user=MANAGER))
        assert exc.value.status_code == 503


class TestR11DayEndCloseKeepsItsFollowUp:
    """D88(3) accepted the Day-End lost acknowledgement with no task. The
    shared follow-up record now restores it there too."""

    def test_lost_ack_409_carries_the_saved_close_and_raises_the_task_once(
        self, db, day_end, monkeypatch
    ):
        _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        raised = _raised(monkeypatch)
        fired = _lose_ack(monkeypatch, db.get_collection("day_end_closes"), "insert_one")
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=True)
        try:
            with pytest.raises(HTTPException) as exc:
                day_end(closing_cash=3000.0, system_cash=4000.0)
        finally:
            restore()
        assert fired == [True]
        code, body = _through_the_app_handler(exc.value)
        assert code == 409 and body["detail"]["close"]["closing_cash"] == 3000.0
        assert body["detail"]["close"]["till_link_ok"] is False
        key = reports_router.day_end.day_end_close_key(STORE, DATE)
        task = ("raise_count_not_on_till_task", till.followup_task_id("uncounted", key))
        assert raised == [task]
        with pytest.raises(HTTPException) as exc:
            day_end(closing_cash=3000.0, system_cash=4000.0)
        assert exc.value.status_code == 409 and raised == [task]
        assert _stored_close(db)["manager_followup"]["pending"] is False

    def test_task_store_down_the_next_read_of_the_day_raises_it(self, db, day_end, monkeypatch):
        _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        up = []
        raised = _raised(monkeypatch, store_up=lambda: bool(up))
        restore = _fail_till_reads(db.get_collection("till_sessions"), counted_only=True)
        try:
            out = day_end(closing_cash=3000.0, system_cash=4000.0)
        finally:
            restore()
        assert out["close"]["manager_followup"]["pending"] is True and raised == []
        up.append(True)
        for _ in range(2):
            got = asyncio.run(reports_router.get_day_end_close(
                date=DATE, store_id=STORE, current_user=MANAGER))
        assert len(raised) == 1 and got["close"]["manager_followup"]["pending"] is False

    def test_a_linked_close_owes_nothing(self, db, day_end, monkeypatch):
        raised = _raised(monkeypatch)
        out = day_end(closing_cash=0.0, system_cash=0.0)
        assert out["close"]["till_link_ok"] is True
        assert out["close"]["manager_followup"]["pending"] is False and raised == []


class TestR11OneOwedTaskIsStoredOnce:
    """The fixed id: raising one owed task again stores nothing new, even
    after the manager completed it."""

    class _Repo:
        def __init__(self):
            self.collection = FakeCollection()

        def create(self, data, *, raise_on_duplicate=False):
            self.collection.insert_one({"_id": data["task_id"], **data})
            return data

        def find_by_id(self, task_id):
            return self.collection.find_one({"task_id": task_id})

    def test_same_fixed_id_is_one_task_and_another_event_is_another(self, monkeypatch):
        import api.dependencies as deps

        repo = self._Repo()
        monkeypatch.setattr(deps, "get_task_repository", lambda: repo)
        monkeypatch.setattr(
            "api.services.task_triggers._person_holding", lambda role, store_id: "u-mgr")
        tid = till.followup_task_id("uncounted", "CR-1")
        kw = dict(store_id=STORE, session_date=DATE, source="Finance cash-register close")
        assert till.raise_count_not_on_till_task(task_id=tid, **kw) is True
        assert till.raise_count_not_on_till_task(task_id=tid, **kw) is True
        assert len(repo.collection.docs) == 1
        repo.collection.docs[0]["status"] = "COMPLETED"
        assert till.raise_count_not_on_till_task(task_id=tid, **kw) is True
        assert len(repo.collection.docs) == 1
        other = till.followup_task_id("uncounted", "CR-2")
        assert till.raise_count_not_on_till_task(task_id=other, **kw) is True
        assert len(repo.collection.docs) == 2

    def test_no_task_store_answers_false_so_the_close_keeps_owing(self, monkeypatch):
        import api.dependencies as deps

        monkeypatch.setattr(deps, "get_task_repository", lambda: None)
        assert till.raise_count_not_on_till_task(
            store_id=STORE, session_date=DATE, source="x", task_id="TSK-X") is False
        assert till.raise_variance_task(
            store_id=STORE, session_date=DATE, variance_paisa=-500, tolerance_paisa=0) is False


def _native_lose_ack(monkeypatch, name, collection, method, when):
    """Codex's native fault: the REAL PyMongo write commits, then its
    acknowledgement is lost, once. Class-level, scoped to our database."""
    from pymongo.collection import Collection

    real = getattr(Collection, method)
    fired = []

    def _write(self, *a, **k):
        out = real(self, *a, **k)
        if (self.database.name == name and self.name == collection
                and not fired and when(*a, **k)):
            fired.append(True)
            raise AutoReconnect("native committed write lost its acknowledgement")
        return out

    monkeypatch.setattr(Collection, method, _write)
    return fired


@pytest.mark.parametrize("read_back_fails", [False, True])
def test_r11_register_lost_ack_restores_one_task_on_real_mongo(monkeypatch, read_back_fails):
    """F-1421-R3-A on a native PyMongo Database with the REAL TaskRepository:
    Codex's sequence (counted-drawer read fault, CLOSED update committed,
    acknowledgement lost), then the identical retry twice."""
    from pymongo.collection import Collection

    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        tasks = _real_task_repo(monkeypatch, real_db)
        a, b = _drawer_a_then_b(
            real_db, a_sales=[1000], b_sales=[3000],
            at=(datetime(2026, 11, 4, 5, 0), datetime(2026, 11, 4, 10, 0)),
        )
        real_find, real_find_one = Collection.find, Collection.find_one

        def _fault(self, query=None, *a_, **k):
            if (self.database.name == name and self.name == "till_sessions"
                    and isinstance(query, dict) and isinstance(query.get("status"), dict)):
                raise AutoReconnect("native counted-drawers read fault")
            return real_find(self, query, *a_, **k)

        monkeypatch.setattr(Collection, "find", _fault)
        fired = _native_lose_ack(
            monkeypatch, name, "cash_register_sessions", "update_one", _sets_status("CLOSED"))
        failed = []

        def _read_back(self, *a_, **k):
            if (read_back_fails and fired and not failed
                    and self.database.name == name and self.name == "cash_register_sessions"):
                failed.append(True)
                raise AutoReconnect("native read-back fault")
            return real_find_one(self, *a_, **k)

        monkeypatch.setattr(Collection, "find_one", _read_back)
        try:
            first = _cr_close_b(_NativeDB(real_db), monkeypatch, 6)
        except HTTPException as exc:
            first = exc
        monkeypatch.setattr(Collection, "find", real_find)
        assert fired == [True]
        if read_back_fails:
            assert first.status_code == 500 and tasks.count_documents({}) == 0
        else:
            assert (first["status"], first["till_link_ok"]) == ("CLOSED", False)

        for _ in range(2):
            with pytest.raises(HTTPException) as exc:
                _cr_again(_NativeDB(real_db), monkeypatch)
            code, body = _through_the_app_handler(exc.value)
            assert code == 409 and body["detail"]["session"]["counted"] == 3000.0
        rows = list(tasks.find({}))
        assert [t["source_ref"] for t in rows] == [f"till_uncounted:{STORE}:{DATE}"]
        assert rows[0]["_id"] == till.followup_task_id("uncounted", "CR-D40")
        assert rows[0]["link"] == "/finance/blind-eod" and rows[0]["assigned_to"] == "u-mgr"
        stored = real_db.cash_register_sessions.find_one({"session_id": "CR-D40"})
        assert (stored["status"], stored["counted"], stored["till_link_ok"]) == ("CLOSED", 3000.0, False)
        assert stored["manager_followup"]["pending"] is False
        assert real_db.cash_register_sessions.count_documents({"session_id": "CR-D40"}) == 1
        assert real_db.till_sessions.find_one({"_id": b})["status"] == till.STATUS_OPEN
        # The manager completes the task; the same owed task raised again
        # (a second worker racing the first) is refused by the fixed id.
        tasks.update_one({"_id": rows[0]["_id"]}, {"$set": {"status": "COMPLETED"}})
        assert till.raise_count_not_on_till_task(
            store_id=STORE, session_date=DATE, source="Finance cash-register close",
            task_id=rows[0]["_id"]) is True
        assert tasks.count_documents({}) == 1
    finally:
        client.drop_database(name)
        client.close()


def test_r11_lock_and_count_lost_ack_on_real_mongo(monkeypatch):
    """The sibling writes on a native PyMongo Database with the REAL
    TaskRepository: the Blind EOD lock of a short drawer and the count of an
    uncompared drawer each commit, lose the acknowledgement, and still store
    exactly one manager task; the retry adds none."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        tasks = _real_task_repo(monkeypatch, real_db)
        _cash_sale(real_db, 1000, datetime(2026, 11, 4, 3, 0))
        TestR8D76OlderCodeDrawers._old_drawer(
            real_db, "OLD-A", 100000, 100000, datetime(2026, 11, 4, 2, 0))
        c = _open(real_db)
        fired = _native_lose_ack(
            monkeypatch, name, "till_sessions", "find_one_and_update",
            _sets_status(till.STATUS_BLIND_SUBMITTED))
        notes = [{"face": 500, "kind": "note", "pieces": 1}]
        res = till.blind_submit(real_db, c, blind_denominations=notes, actor=CASHIER)
        assert fired == [True] and res["ok"], res
        assert till.blind_submit(real_db, c, blind_denominations=notes, actor=CASHIER)["http"] == 409
        assert [t["source_ref"] for t in tasks.find({})] == [f"till_uncompared:{STORE}:{DATE}:{c}"]

        # A short drawer on another day: lock commits, acknowledgement lost.
        day2 = "2026-11-05"
        _cash_sale(real_db, 1000, datetime(2026, 11, 5, 5, 0))
        s = till.open_session(
            real_db, store_id=STORE, session_date=day2, opening_float_paisa=0, actor=CASHIER
        )["session"]["session_id"]
        assert till.blind_submit(real_db, s, blind_denominations=notes, actor=CASHIER)["ok"]
        fired = _native_lose_ack(
            monkeypatch, name, "till_sessions", "find_one_and_update",
            _sets_status(till.STATUS_LOCKED))
        locked = till.lock_session(real_db, s, actor=MANAGER, variance_note="Rs 500 short")
        assert fired == [True] and locked["ok"], locked
        again = till.lock_session(real_db, s, actor=MANAGER, variance_note="Rs 500 short")
        assert (again["http"], again["error"]) == (409, "already_locked")
        alerts = list(tasks.find({"source_ref": f"till_variance:{STORE}:{day2}:{s}"}))
        assert len(alerts) == 1 and alerts[0]["payload"]["variance_paisa"] == -50000
        row = real_db.till_sessions.find_one({"_id": s})
        assert row["status"] == till.STATUS_LOCKED and row["manager_followup"]["pending"] is False
    finally:
        client.drop_database(name)
        client.close()


# ===========================================================================
# Round 12 (review must-fix on head b83560fa)  The AUDIT ROW of a till write
# that committed but did not carry on (lead decision D84)
# ===========================================================================
# Round 11 kept the manager TASK of a till write whose reply was lost. The
# audit row was still written only when the request itself carried on: after
# a 500 (the read-back failed too) or a stopped process the identical retry
# and the read repair restored the task and never the till.lock /
# till.blind_submit row. And the reopen had no read-back at all: 500, then a
# bare 409 not_locked, and no till.reopen row for a drawer that WAS reopened.

_REOPENS = lambda *a, **k: "$inc" in a[1]  # noqa: E731  the reopen's guarded write
_AUDIT_OF = {
    "open": "till.open", "count": "till.blind_submit", "lock": "till.lock", "reopen": "till.reopen"}


class _Stop(BaseException):
    """The process stopping: nothing after it in the request runs."""


def _rows(audit_rows, action, sid=None):
    return [r for r in audit_rows
            if r.get("action") == action and (sid is None or r.get("entity_id") == sid)]


class TestR12TillWritesKeepTheirAuditRow:
    def _locked(self, db):
        _cash_sale(db, 1000)
        return _blind_count(db, rupees=1000, lock=True)

    def _short(self, db):
        _cash_sale(db, 1000)
        return _blind_count(db, rupees=500, lock=False)

    # -- reopen (had no read-back at all) -----------------------------------

    def test_reopen_lost_ack_is_a_reopen_with_its_audit_row(self, db, monkeypatch, audit_rows):
        sid = self._locked(db)
        fired = _lose_ack(
            monkeypatch, db.get_collection("till_sessions"), "find_one_and_update", _REOPENS)
        res = till.reopen_session(db, sid, reason="recount", actor=MANAGER)
        assert fired == [True] and res["ok"], res
        assert res["session"]["status"] == till.STATUS_BLIND_SUBMITTED
        rows = _rows(audit_rows, "till.reopen", sid)
        assert len(rows) == 1 and rows[0]["user_id"] == "u-mgr" and rows[0]["severity"] == "WARNING"
        assert rows[0]["before_state"] == {"status": till.STATUS_LOCKED}
        assert rows[0]["after_state"] == {"status": till.STATUS_BLIND_SUBMITTED, "reason": "recount"}
        assert rows[0]["detail"] == {"session_date": DATE, "reopen_count": 1}
        again = till.reopen_session(db, sid, reason="recount", actor=MANAGER)
        assert again["ok"] and again["idempotent"], again
        stored = _till(db, sid)
        assert (stored["reopen_count"], len(stored["history"])) == (1, 1)
        assert len(_rows(audit_rows, "till.reopen", sid)) == 1

    def test_reopen_500_then_the_retry_writes_the_audit_row_once(self, db, monkeypatch, audit_rows):
        sid = self._locked(db)
        coll = db.get_collection("till_sessions")
        fired = _lose_ack(monkeypatch, coll, "find_one_and_update", _REOPENS)
        _fail_read_back(monkeypatch, coll, fired)
        res = till.reopen_session(db, sid, reason="recount", actor=MANAGER)
        assert (res["ok"], res["http"], res["error"]) == (False, 500, "reopen_failed")
        assert _rows(audit_rows, "till.reopen", sid) == []
        assert _till(db, sid)["status"] == till.STATUS_BLIND_SUBMITTED
        for _ in range(2):
            again = till.reopen_session(db, sid, reason="recount", actor=MANAGER)
            assert again["ok"] and again["idempotent"], again
            assert again["session"]["status"] == till.STATUS_BLIND_SUBMITTED
        # Another reason, or another person, is not that retry.
        other = till.reopen_session(db, sid, reason="something else", actor=MANAGER)
        assert (other["http"], other["error"]) == (409, "not_locked")
        other = till.reopen_session(db, sid, reason="recount", actor=ADMIN)
        assert (other["http"], other["error"]) == (409, "not_locked")
        till.get_session(db, sid)
        till.list_sessions(db, store_id=STORE)
        assert len(_rows(audit_rows, "till.reopen", sid)) == 1
        stored = _till(db, sid)
        assert (stored["reopen_count"], len(stored["history"])) == (1, 1)
        assert stored["manager_followup"]["pending"] is False

    def test_reopen_500_no_retry_a_read_of_the_drawer_writes_the_row(self, db, monkeypatch, audit_rows):
        sid = self._locked(db)
        coll = db.get_collection("till_sessions")
        fired = _lose_ack(monkeypatch, coll, "find_one_and_update", _REOPENS)
        _fail_read_back(monkeypatch, coll, fired)
        assert till.reopen_session(db, sid, reason="recount", actor=MANAGER)["http"] == 500
        assert _rows(audit_rows, "till.reopen", sid) == []
        till.list_sessions(db, store_id=STORE)
        assert len(_rows(audit_rows, "till.reopen", sid)) == 1
        till.list_sessions(db, store_id=STORE)
        till.get_session(db, sid)
        assert len(_rows(audit_rows, "till.reopen", sid)) == 1

    def test_a_never_locked_count_is_not_an_earlier_reopen(self, db):
        sid = self._short(db)
        res = till.reopen_session(db, sid, reason="recount", actor={**MANAGER, "user_id": None})
        assert (res["ok"], res["http"], res["error"]) == (False, 409, "not_locked")

    # -- lock and blind count (the 500 / stopped-process half) ---------------

    def test_lock_500_then_the_retry_writes_the_audit_row_once(self, db, monkeypatch, audit_rows):
        sid = self._short(db)
        _raised(monkeypatch)
        coll = db.get_collection("till_sessions")
        fired = _lose_ack(monkeypatch, coll, "find_one_and_update", _sets_status(till.STATUS_LOCKED))
        _fail_read_back(monkeypatch, coll, fired)
        res = till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")
        # The bare code: the driver's text is for the log, not the reply.
        assert (res["ok"], res["http"], res["error"]) == (False, 500, "lock_failed")
        assert _rows(audit_rows, "till.lock", sid) == []
        again = till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")
        assert (again["http"], again["error"]) == (409, "already_locked")
        rows = _rows(audit_rows, "till.lock", sid)
        assert len(rows) == 1 and rows[0]["user_id"] == "u-mgr" and rows[0]["severity"] == "WARNING"
        assert rows[0]["before_state"] == {"status": till.STATUS_BLIND_SUBMITTED}
        assert rows[0]["after_state"] == {
            "status": till.STATUS_LOCKED,
            "zread_number": _till(db, sid)["zread_number"],
            "expected_cash_paisa": 100000,
            "variance_paisa": -50000,
            "variance_status": "SHORTAGE",
            "variance_note": "Rs 500 short",
        }
        till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")
        till.get_session(db, sid)
        till.list_sessions(db, store_id=STORE)
        assert len(_rows(audit_rows, "till.lock", sid)) == 1

    def test_lock_500_no_retry_a_read_of_the_drawer_writes_the_row(self, db, monkeypatch, audit_rows):
        sid = self._short(db)
        _raised(monkeypatch)
        coll = db.get_collection("till_sessions")
        fired = _lose_ack(monkeypatch, coll, "find_one_and_update", _sets_status(till.STATUS_LOCKED))
        _fail_read_back(monkeypatch, coll, fired)
        assert till.lock_session(db, sid, actor=MANAGER, variance_note="short")["http"] == 500
        assert _rows(audit_rows, "till.lock", sid) == []
        till.list_sessions(db, store_id=STORE)
        assert len(_rows(audit_rows, "till.lock", sid)) == 1
        till.get_session(db, sid)
        till.list_sessions(db, store_id=STORE)
        assert len(_rows(audit_rows, "till.lock", sid)) == 1

    def test_another_persons_read_writes_the_row_in_the_lockers_name(self, db, monkeypatch, audit_rows):
        """The repair is not tied to the person who pressed: the cashier's own
        list of the day, or an admin's retry, writes the manager's row."""
        sid = self._short(db)
        _raised(monkeypatch)
        coll = db.get_collection("till_sessions")
        fired = _lose_ack(monkeypatch, coll, "find_one_and_update", _sets_status(till.STATUS_LOCKED))
        _fail_read_back(monkeypatch, coll, fired)
        assert till.lock_session(db, sid, actor=MANAGER, variance_note="short")["http"] == 500
        monkeypatch.setattr(till_router, "_get_db", lambda: db)
        listed = asyncio.run(till_router.list_till_sessions(
            store_id=STORE, date=None, status=None, limit=50, current_user=CASHIER))
        rows = _rows(audit_rows, "till.lock", sid)
        assert len(rows) == 1 and rows[0]["user_id"] == "u-mgr" and rows[0]["user_name"] == "Priya"
        # The cashier's list never carries the owed row (it names the variance).
        assert listed["sessions"] and all("manager_followup" not in r for r in listed["sessions"])
        again = till.lock_session(db, sid, actor=ADMIN, variance_note="short")
        assert (again["http"], again["error"]) == (409, "already_locked")
        assert len(_rows(audit_rows, "till.lock", sid)) == 1

    def test_count_500_then_the_retry_writes_the_audit_row_once(self, db, monkeypatch, audit_rows):
        _cash_sale(db, 1000)
        c = _open(db)
        coll = db.get_collection("till_sessions")
        fired = _lose_ack(
            monkeypatch, coll, "find_one_and_update", _sets_status(till.STATUS_BLIND_SUBMITTED))
        _fail_read_back(monkeypatch, coll, fired)
        notes = [{"face": 500, "kind": "note", "pieces": 1}]
        res = till.blind_submit(db, c, blind_denominations=notes, idempotency_key="k1", actor=CASHIER)
        assert (res["ok"], res["http"], res["error"]) == (False, 500, "submit_failed")
        assert _rows(audit_rows, "till.blind_submit", c) == []
        again = till.blind_submit(db, c, blind_denominations=notes, idempotency_key="k1", actor=CASHIER)
        assert again["ok"] and again["idempotent"], again
        # The cashier's reply never carries the owed row (it names the variance).
        assert "manager_followup" not in till.redact_for_cashier(again["session"])
        rows = _rows(audit_rows, "till.blind_submit", c)
        assert len(rows) == 1 and rows[0]["user_id"] == "u-cash"
        assert rows[0]["after_state"] == {
            "status": till.STATUS_BLIND_SUBMITTED,
            "blind_count_paisa": 50000,
            "expected_cash_paisa": 100000,
            "variance_paisa": -50000,
        }
        till.blind_submit(db, c, blind_denominations=notes, idempotency_key="k1", actor=CASHIER)
        till.get_session(db, c)
        assert len(_rows(audit_rows, "till.blind_submit", c)) == 1

    # -- the open (same class, found on the way) -----------------------------

    @pytest.mark.parametrize("read_back_fails", [False, True])
    def test_open_lost_ack_keeps_its_audit_row(self, db, monkeypatch, audit_rows, read_back_fails):
        coll = db.get_collection("till_sessions")
        fired = _lose_ack(monkeypatch, coll, "insert_one")
        if read_back_fails:
            _fail_read_back(monkeypatch, coll, fired)
        kw = dict(store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER)
        res = till.open_session(db, **kw)
        assert fired == [True]
        if read_back_fails:
            assert (res["ok"], res["http"], res["error"]) == (False, 500, "write_failed")
            assert _rows(audit_rows, "till.open") == []
        else:
            assert res["ok"] and not res.get("already_open"), res
        again = till.open_session(db, **kw)
        assert again["ok"] and again["already_open"], again
        rows = _rows(audit_rows, "till.open")
        assert len(rows) == 1 and rows[0]["entity_id"] == again["session"]["session_id"]
        assert rows[0]["user_id"] == "u-cash" and len(coll.docs) == 1
        till.open_session(db, **kw)
        assert len(_rows(audit_rows, "till.open")) == 1

    # -- a stopped process, an audit store that is down ----------------------

    @pytest.mark.parametrize("write", ["open", "count", "lock", "reopen"])
    def test_a_process_stopped_before_the_audit_row_a_read_writes_it(
        self, db, monkeypatch, audit_rows, write
    ):
        _cash_sale(db, 1000)
        notes = [{"face": 500, "kind": "note", "pieces": 2}]
        steps = {
            "open": lambda sid: till.open_session(
                db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER),
            "count": lambda sid: till.blind_submit(db, sid, blind_denominations=notes, actor=CASHIER),
            "lock": lambda sid: till.lock_session(db, sid, actor=MANAGER),
            "reopen": lambda sid: till.reopen_session(db, sid, reason="recount", actor=MANAGER),
        }
        sid = None
        for name in ("open", "count", "lock", "reopen"):
            if name == write:
                store = _AuditStore(audit_rows, stop=[_Stop()])
                monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: store)
                with pytest.raises(_Stop):
                    steps[name](sid)
                break
            out = steps[name](sid)
            assert out["ok"], out
            sid = out["session"]["session_id"]
        sid = sid or db.get_collection("till_sessions").docs[0]["_id"]
        assert _rows(audit_rows, _AUDIT_OF[write], sid) == []
        till.list_sessions(db, store_id=STORE)  # the Blind EOD Tally loads
        assert len(_rows(audit_rows, _AUDIT_OF[write], sid)) == 1
        till.get_session(db, sid)
        till.list_sessions(db, store_id=STORE)
        assert len(_rows(audit_rows, _AUDIT_OF[write], sid)) == 1
        assert _till(db, sid)["manager_followup"]["pending"] is False

    def test_audit_store_down_the_lock_stands_and_the_next_read_writes_the_row(
        self, db, monkeypatch, audit_rows
    ):
        sid = self._short(db)
        raised = _raised(monkeypatch)
        up = []
        store = _AuditStore(audit_rows, up=lambda: bool(up))
        monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: store)
        res = till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")
        assert res["ok"] and res["session"]["status"] == till.STATUS_LOCKED
        assert _rows(audit_rows, "till.lock", sid) == []
        assert _till(db, sid)["manager_followup"]["pending"] is True
        up.append(True)
        till.get_session(db, sid)
        assert len(_rows(audit_rows, "till.lock", sid)) == 1
        assert _till(db, sid)["manager_followup"]["pending"] is False
        till.get_session(db, sid)
        till.list_sessions(db, store_id=STORE)
        assert len(_rows(audit_rows, "till.lock", sid)) == 1
        # The alert itself has a fixed id: raised again while the row was
        # owed, stored once (TestR11OneOwedTaskIsStoredOnce).
        assert {r[1] for r in raised} == {raised[0][1]}

    # -- a count on a drawer whose OPEN still owes its row -------------------
    # The count restates the owed record. It used to store only its own row,
    # so a ``till.open`` row that could not be written at open time was gone
    # for good (review must-fix on head 70c94024).

    @pytest.mark.parametrize("door", ["till", "screen_close", "cash_register", "day_end"])
    def test_a_count_carries_forward_the_row_the_open_still_owes(
        self, db, monkeypatch, audit_rows, day_end, door
    ):
        _cash_sale(db, 1000)
        up = []
        store = _AuditStore(audit_rows, up=lambda: bool(up))
        monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: store)
        monkeypatch.setattr(reports_router, "get_audit_repository", lambda: store)
        notes = [{"face": 500, "kind": "note", "pieces": 2}]
        if door == "till":
            # Two requests, the audit store down across both.
            sid = till.open_session(
                db, store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER,
            )["session"]["session_id"]
            assert till.blind_submit(db, sid, blind_denominations=notes, actor=CASHIER)["ok"]
        elif door == "screen_close":
            # ONE request opens and counts (a close on a day with no drawer).
            link = till.record_screen_close(
                db, store_id=STORE, session_date=DATE, counted_paisa=100000, actor=MANAGER)
            assert link["ok"] and link["created"], link
            sid = link["session_id"]
        elif door == "cash_register":
            out = _cr_close_two_drawers(
                db, monkeypatch, denominations=notes, closing_count_state="COUNTED")
            assert out["till_link_ok"] is True
            sid = out["till_session_id"]
        else:
            sid = day_end(closing_cash=1000.0, system_cash=1000.0)["close"]["till_session_id"]
        drawer = _till(db, sid)
        assert drawer["status"] == till.STATUS_BLIND_SUBMITTED
        assert drawer["manager_followup"]["pending"] is True
        assert [r for r in audit_rows if r.get("entity_id") == sid] == []
        up.append(True)
        till.list_sessions(db, store_id=STORE)  # the Blind EOD Tally loads
        mine = [r for r in audit_rows if r.get("entity_id") == sid]
        assert [r["action"] for r in mine] == ["till.open", "till.blind_submit"]
        assert mine[0]["after_state"] == {"status": till.STATUS_OPEN, "opening_float_paisa": 0}
        assert _till(db, sid)["manager_followup"]["pending"] is False
        till.get_session(db, sid)
        till.list_sessions(db, store_id=STORE)
        assert len([r for r in audit_rows if r.get("entity_id") == sid]) == 2

    def test_open_count_and_lock_with_the_audit_store_down_keep_all_three_rows(
        self, db, monkeypatch, audit_rows
    ):
        up = []
        store = _AuditStore(audit_rows, up=lambda: bool(up))
        monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: store)
        sid = self._short(db)
        assert till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")["ok"]
        assert audit_rows == []
        up.append(True)
        till.get_session(db, sid)
        assert [r["action"] for r in audit_rows] == ["till.open", "till.blind_submit", "till.lock"]
        assert [r["user_id"] for r in audit_rows] == ["u-cash", "u-cash", "u-mgr"]
        assert _till(db, sid)["manager_followup"]["pending"] is False

    def test_a_count_on_a_drawer_whose_open_row_is_paid_owes_only_its_own(self, db, audit_rows):
        """Control: nothing owed by the open, nothing carried."""
        sid = self._short(db)
        owed = _till(db, sid)["manager_followup"]
        assert [t["row"]["action"] for t in owed["tasks"]] == ["till.blind_submit"]
        assert [r["action"] for r in audit_rows] == ["till.open", "till.blind_submit"]

    def test_every_till_write_has_one_row_and_a_relock_is_a_new_row(self, db, audit_rows):
        sid = self._locked(db)
        assert till.reopen_session(db, sid, reason="recount", actor=MANAGER)["ok"]
        time.sleep(0.005)  # a later lock instant, to the millisecond
        assert till.lock_session(db, sid, actor=MANAGER)["ok"]
        mine = [r for r in audit_rows if r.get("entity_id") == sid]
        assert [r["action"] for r in mine] == [
            "till.open", "till.blind_submit", "till.lock", "till.reopen", "till.lock"]
        assert len({r["log_id"] for r in mine}) == 5
        assert all(r["entity_type"] == "till_session" and r["source"] == "eod_tally" for r in mine)
        assert _till(db, sid)["manager_followup"]["pending"] is False


def test_r12_till_audit_rows_survive_a_lost_ack_on_real_mongo(monkeypatch):
    """Native PyMongo, the REAL AuditRepository (hash chain) and the real
    TaskRepository. Each till write commits, loses its acknowledgement AND
    the read-back fails (500); the identical retry writes the one audit row."""
    from pymongo.collection import Collection

    from database.repositories.audit_chain import verify_chain
    from database.repositories.audit_repository import AuditRepository

    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        tasks = _real_task_repo(monkeypatch, real_db)
        monkeypatch.setattr(
            "api.dependencies.get_audit_repository", lambda: AuditRepository(real_db.audit_logs))
        _cash_sale(real_db, 1000, datetime(2026, 11, 4, 5, 0))
        real_find_one = Collection.find_one
        state = {"fired": [], "failed": []}

        def _read_back(self, *a, **k):
            if (self.database.name == name and self.name == "till_sessions"
                    and state["fired"] and not state["failed"]):
                state["failed"].append(True)
                raise AutoReconnect("native read-back fault")
            return real_find_one(self, *a, **k)

        monkeypatch.setattr(Collection, "find_one", _read_back)

        def _arm(method, when):
            state["failed"] = []
            state["fired"] = _native_lose_ack(monkeypatch, name, "till_sessions", method, when)

        def _count(action):
            return real_db.audit_logs.count_documents({"action": action})

        kw = dict(store_id=STORE, session_date=DATE, opening_float_paisa=0, actor=CASHIER)
        _arm("insert_one", lambda *a, **k: True)
        assert till.open_session(real_db, **kw)["http"] == 500 and _count("till.open") == 0
        opened = till.open_session(real_db, **kw)
        assert opened["ok"] and opened["already_open"] and _count("till.open") == 1
        sid = opened["session"]["session_id"]

        notes = [{"face": 500, "kind": "note", "pieces": 1}]
        _arm("find_one_and_update", _sets_status(till.STATUS_BLIND_SUBMITTED))
        count = lambda: till.blind_submit(  # noqa: E731
            real_db, sid, blind_denominations=notes, idempotency_key="k1", actor=CASHIER)
        assert count()["http"] == 500 and _count("till.blind_submit") == 0
        assert count()["idempotent"] and _count("till.blind_submit") == 1

        _arm("find_one_and_update", _sets_status(till.STATUS_LOCKED))
        lock = lambda: till.lock_session(  # noqa: E731
            real_db, sid, actor=MANAGER, variance_note="Rs 500 short")
        assert lock()["http"] == 500 and _count("till.lock") == 0
        assert lock()["error"] == "already_locked" and _count("till.lock") == 1
        assert tasks.count_documents({"source_ref": f"till_variance:{STORE}:{DATE}:{sid}"}) == 1

        _arm("find_one_and_update", _REOPENS)
        reopen = lambda: till.reopen_session(real_db, sid, reason="recount", actor=MANAGER)  # noqa: E731
        assert reopen()["http"] == 500 and _count("till.reopen") == 0
        again = reopen()
        assert again["ok"] and again["idempotent"] and _count("till.reopen") == 1
        assert state["fired"] == [True] and state["failed"] == [True]

        # Retries and reads after that add nothing.
        count(), reopen()
        till.list_sessions(real_db, store_id=STORE)
        till.get_session(real_db, sid)
        rows = list(real_db.audit_logs.find({"entity_id": sid}).sort("seq", 1))
        assert [r["action"] for r in rows] == [
            "till.open", "till.blind_submit", "till.lock", "till.reopen"]
        assert [r["user_id"] for r in rows] == ["u-cash", "u-cash", "u-mgr", "u-mgr"]
        assert rows[2]["after_state"]["variance_paisa"] == -50000
        assert all(r["_id"] == r["log_id"] and r.get("entry_hash") for r in rows)
        chain = verify_chain(real_db.audit_logs)
        assert chain["intact"] and chain["entries_checked"] == 4, chain
        stored = real_db.till_sessions.find_one({"_id": sid})
        assert stored["status"] == till.STATUS_BLIND_SUBMITTED
        assert (stored["reopen_count"], len(stored["history"])) == (1, 1)
        assert stored["manager_followup"]["pending"] is False
        assert tasks.count_documents({}) == 1
    finally:
        client.drop_database(name)
        client.close()


def test_r13_open_and_count_with_the_audit_store_down_on_real_mongo(monkeypatch):
    """Native PyMongo, the REAL AuditRepository (hash chain). One close on a
    day with no drawer opens AND counts it in one request while ``audit_logs``
    cannot be read or written; the drawer is then locked, still down. When
    the store is back, one read writes ``till.open``, ``till.blind_submit``
    and ``till.lock`` -- the count used to drop the row the open owed."""
    from pymongo.collection import Collection

    from database.repositories.audit_chain import verify_chain
    from database.repositories.audit_repository import AuditRepository

    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        _real_task_repo(monkeypatch, real_db)
        monkeypatch.setattr(
            "api.dependencies.get_audit_repository", lambda: AuditRepository(real_db.audit_logs))
        _cash_sale(real_db, 1000, datetime(2026, 11, 4, 5, 0))
        down = [True]
        for method in ("find_one", "insert_one"):
            real = getattr(Collection, method)

            def _fault(self, *a, _real=real, **k):
                if down and self.database.name == name and self.name == "audit_logs":
                    raise AutoReconnect("native audit store fault")
                return _real(self, *a, **k)

            monkeypatch.setattr(Collection, method, _fault)

        link = till.record_screen_close(
            real_db, store_id=STORE, session_date=DATE, counted_paisa=50000, actor=CASHIER)
        assert link["ok"] and link["created"] and link["counted"], link
        sid = link["session_id"]
        assert till.lock_session(real_db, sid, actor=MANAGER, variance_note="Rs 500 short")["ok"]
        stored = real_db.till_sessions.find_one({"_id": sid})
        assert stored["status"] == till.STATUS_LOCKED
        assert stored["manager_followup"]["pending"] is True
        assert real_db.audit_logs.count_documents({}) == 0

        down.clear()
        till.list_sessions(real_db, store_id=STORE)
        till.get_session(real_db, sid)
        rows = list(real_db.audit_logs.find({"entity_id": sid}).sort("seq", 1))
        assert [r["action"] for r in rows] == ["till.open", "till.blind_submit", "till.lock"]
        assert [r["user_id"] for r in rows] == ["u-cash", "u-cash", "u-mgr"]
        chain = verify_chain(real_db.audit_logs)
        assert chain["intact"] and chain["entries_checked"] == 3, chain
        assert real_db.till_sessions.find_one({"_id": sid})["manager_followup"]["pending"] is False
    finally:
        client.drop_database(name)
        client.close()


# ===========================================================================
# Round 17 (review of head d4429202; lead decisions D110, D111)
# ===========================================================================
# MUST-FIX: "of several closes on one drawer the earliest adds" let a close
# with NO count stand in for the later close that counted the drawer, so the
# counted cash left the range totals.
# D111: a Cash Register session opened on a drawer already counted is judged
# on its own sheet against that count plus the cash moved since it.
# D110: every reader and retry that meets a till drawer pays the audit rows
# it still owes, not only the Blind EOD screens.


def _register(db, monkeypatch, sid, *, opened, closed, opening=0.0, pieces=None, note=None,
              insert=True, bank=0.0):
    """One Cash Register session of DATE through the real close handler.
    ``pieces`` None = the grid left blank (closed without a count)."""
    monkeypatch.setattr(finance_router, "_get_db", lambda: db)
    monkeypatch.setattr(finance_router, "_iso_now", lambda: f"{DATE}T{closed}")
    if insert:
        db.get_collection("cash_register_sessions").insert_one({
            "_id": sid, "session_id": sid, "store_id": STORE, "status": "OPEN",
            "opening_float": opening, "opening_denominations": [], "opened_at": f"{DATE}T{opened}",
        })
    body = finance_router.CashRegisterClose(
        session_id=sid,
        denominations=[] if pieces is None else [{"face": 500, "kind": "note", "pieces": pieces}],
        closing_count_state="NOT_CAPTURED" if pieces is None else "COUNTED",
        bank_deposit=bank,
        **({"note": note} if note else {}),
    )
    return asyncio.run(finance_router.close_cash_register(body=body, current_user=MANAGER))


def _counted_at(db, sid, clock):
    """Put the drawer's count instant on DATE's clock. The register sessions
    of these tests run on DATE (their clock is patched) while the till stamps
    the real now; in real use both are the same clock."""
    db.get_collection("till_sessions").update_one(
        {"_id": sid}, {"$set": {"blind_submitted_at": datetime.fromisoformat(f"{DATE}T{clock}")}})


def _one_drawer_counted_by_a_register(db, monkeypatch, *, pieces=6, note=None):
    """Drawer opened, Rs 3,000 cash taken, counted through a first Cash
    Register close at 12:00 and not locked."""
    sid = _open(db)
    _cash_sale(db, 3000)
    first = _register(db, monkeypatch, "CR-R17-1", opened="04:00:00", closed="12:00:00",
                      pieces=pieces, note=note)
    assert (first["till_session_id"], first["till_link_ok"]) == (sid, True)
    _counted_at(db, sid, "12:00:00")
    return sid, first


def _recon_row(res, sid):
    return next(r for r in res["rows"] if r["session_id"] == sid)


class TestR17TheEarliestCloseThatHoldsACountAdds:
    def test_first_close_without_a_count_does_not_displace_the_one_that_counted(self, db, monkeypatch):
        """The reviewers' steps: register closed with a blank grid (the drawer
        stays OPEN), a second session counts the drawer. Until the Z-Read
        locked, the totals read counted Rs 0 and called the only count a
        duplicate."""
        first = _register(db, monkeypatch, "CR-R17-1", opened="04:00:00", closed="09:00:00")
        sid = first["till_session_id"]
        assert (first["variance_status"], first["counted"], first["till_link_ok"]) == (
            "NOT_COUNTED", None, True)
        assert _till(db, sid)["status"] == till.STATUS_OPEN
        _cash_sale(db, 3000, f"{DATE}T10:00:00")
        second = _register(db, monkeypatch, "CR-R17-2", opened="09:30:00", closed="12:00:00", pieces=6)
        assert (second["till_session_id"], second["counted"], second["variance_status"]) == (
            sid, 3000.0, "BALANCED")

        res = _recon_summary(db, monkeypatch)
        t = res["totals"]
        assert (t["counted_cash"], t["cash_sales"], t["expected_cash"]) == (3000.0, 3000.0, 3000.0)
        assert (t["sessions"], t["also_counted_on_till"], t["balanced"]) == (2, 0, 1)
        assert _recon_row(res, "CR-R17-2")["also_counted_on_till"] is False
        assert _recon_row(res, "CR-R17-1")["variance_status"] == "NOT_COUNTED"

        assert till.lock_session(db, sid, actor=MANAGER)["ok"]
        res = _recon_summary(db, monkeypatch)
        assert [r["source"] for r in res["rows"]] == ["BLIND_EOD"]
        assert res["totals"]["counted_cash"] == 3000.0


class TestR17D111ASessionOnACountedDrawerIsJudgedOnItsOwnSheet:
    """Builder probe of head d4429202: Rs 3,000 counted at the first close,
    Rs 500 taken after, Rs 3,500 in hand at the second close: stored Rs 500
    SHORT, a note demanded, the manager alerted."""

    def test_its_own_sheet_balances_with_no_note_and_no_alert(self, db, monkeypatch):
        alerts = _alerts(monkeypatch)
        sid, _first = _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           opening=3000.0, pieces=7)
        assert {k: second[k] for k in ("counted", "expected", "variance", "variance_status")} == {
            "counted": 3500.0, "expected": 3500.0, "variance": 0.0, "variance_status": "BALANCED"}
        assert (second["counted_from_shared_record"], second["till_count_differs"]) == (False, False)
        assert (second["till_counted_earlier"], second["till_standing_count"]) == (True, 3000.0)
        assert (second["cash_sales"], second["till_session_id"]) == (500.0, sid)
        assert alerts == [] and second["manager_followup"]["tasks"] == []
        # The drawer's blind count is not replaced (L3-12).
        assert _till(db, sid)["blind_count_paisa"] == 300000

        # D88 totals unchanged: the earliest counted close adds, this one is marked.
        res = _recon_summary(db, monkeypatch)
        assert _money_totals(res) == {
            "opening_float": 0.0, "cash_sales": 3000.0, "cash_refunds": 0.0,
            "cash_expenses": 0.0, "expected_cash": 3000.0, "counted_cash": 3000.0, "variance": 0.0}
        row = _recon_row(res, "CR-R17-2")
        assert (row["also_counted_on_till"], row["counted_cash"], row["expected_cash"],
                row["opening_float"], row["variance"], row["variance_status"]) == (
            True, 3500.0, 3500.0, 3000.0, 0.0, "BALANCED")

        # Locked: the first close folds into the drawer's Z-Read row; this
        # session's own figures still show, marked and not added.
        assert till.lock_session(db, sid, actor=MANAGER)["ok"]
        res = _recon_summary(db, monkeypatch)
        assert sorted((r["source"], r["also_counted_on_till"]) for r in res["rows"]) == [
            ("BLIND_EOD", False), ("CASH_REGISTER", True)]
        assert _recon_row(res, "CR-R17-2")["counted_cash"] == 3500.0
        t = res["totals"]
        assert (t["counted_cash"], t["cash_sales"], t["sessions"], t["also_counted_on_till"]) == (
            3000.0, 3000.0, 1, 1)

    def test_expected_starts_from_the_standing_count_and_covers_cash_since_it(self, db, monkeypatch):
        """Rs 500 is taken after the count and BEFORE the next session opens,
        and the float typed at opening is wrong: neither moves the figure."""
        alerts = _alerts(monkeypatch)
        _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 500, f"{DATE}T12:10:00")
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           opening=2000.0, pieces=8)
        assert (second["counted"], second["expected"], second["variance_status"]) == (
            4000.0, 4000.0, "BALANCED")
        assert (second["cash_sales"], second["opening_float"], second["till_standing_count"]) == (
            1000.0, 2000.0, 3000.0)
        assert alerts == []

    def test_a_real_difference_on_its_own_sheet_needs_the_note_and_alerts(self, db, monkeypatch):
        """Owner ruling 2026-08-25 stands. The first close was Rs 500 short
        too and its alert is still open: the second session's own shortage
        raises its own."""
        alerts = _alerts(monkeypatch)
        sid, first = _one_drawer_counted_by_a_register(
            db, monkeypatch, pieces=5, note="Rs 500 short at the first count")
        assert first["variance"] == -500.0
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                      opening=2500.0, pieces=5)
        assert exc.value.status_code == 400 and "variance_note_required" in exc.value.detail
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           pieces=5, note="another Rs 500 short", insert=False)
        assert {k: second[k] for k in ("counted", "expected", "variance", "variance_status")} == {
            "counted": 2500.0, "expected": 3000.0, "variance": -500.0, "variance_status": "SHORT"}
        assert second["counted_from_shared_record"] is False
        assert [(a["session_id"], a["variance_paisa"]) for a in alerts] == [
            (sid, -50000), ("CR-R17-2", -50000)]
        # The shortage stays on the console after the Z-Read locks (the row
        # is marked and adds nothing: the totals are the drawer's own).
        assert till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")["ok"]
        res = _recon_summary(db, monkeypatch)
        row = _recon_row(res, "CR-R17-2")
        assert (row["variance"], row["variance_status"], row["also_counted_on_till"]) == (
            -500.0, "SHORTAGE", True)
        assert (res["totals"]["counted_cash"], res["totals"]["shortage"]) == (2500.0, 1)

    def test_no_count_of_its_own_stores_no_comparison(self, db, monkeypatch):
        alerts = _alerts(monkeypatch)
        _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           opening=3000.0)
        assert (second["variance"], second["variance_status"]) == (None, "COUNTED_EARLIER")
        # The count shown is the one standing on the till, flagged as such;
        # the cash taken after that count is on the row.
        assert (second["counted"], second["counted_from_shared_record"], second["cash_sales"]) == (
            3000.0, True, 500.0)
        assert alerts == [] and second["manager_followup"]["tasks"] == []
        res = _recon_summary(db, monkeypatch)
        row = _recon_row(res, "CR-R17-2")
        assert (row["variance"], row["variance_status"], row["also_counted_on_till"]) == (
            None, "COUNTED_EARLIER", True)
        t = res["totals"]
        assert (t["counted_cash"], t["shortage"], t["sessions"]) == (3000.0, 0, 1)

    def test_counted_on_the_blind_eod_tally_then_a_register_session(self, db, monkeypatch):
        alerts = _alerts(monkeypatch)
        _cash_sale(db, 1000)
        sid = _blind_count(db, rupees=1000, lock=False)
        _counted_at(db, sid, "06:00:00")
        _cash_sale(db, 500, f"{DATE}T07:00:00")
        out = _register(db, monkeypatch, "CR-R17-1", opened="06:30:00", closed="08:00:00",
                        opening=1000.0, pieces=3)
        assert (out["counted"], out["expected"], out["variance_status"]) == (1500.0, 1500.0, "BALANCED")
        assert alerts == [] and _till(db, sid)["blind_count_paisa"] == 100000
        # D121 (round 20): a later close on an already counted drawer is
        # never in the totals, also while that count is not locked yet. It
        # used to add until the lock (Rs 1,500) and leave at it.
        res = _recon_summary(db, monkeypatch)
        t = res["totals"]
        assert (t["counted_cash"], t["sessions"], t["also_counted_on_till"], t["later_closes"]) == (
            0, 0, 1, 1)
        assert _recon_row(res, "CR-R17-1")["counted_cash"] == 1500.0  # the row still shows
        assert till.lock_session(db, sid, actor=MANAGER)["ok"]
        res = _recon_summary(db, monkeypatch)
        assert sorted((r["source"], r["also_counted_on_till"]) for r in res["rows"]) == [
            ("BLIND_EOD", False), ("CASH_REGISTER", True)]
        assert (res["totals"]["counted_cash"], res["totals"]["also_counted_on_till"]) == (1000.0, 1)

    def test_on_a_day_with_several_drawers_it_is_still_its_own_sheet(self, db, monkeypatch):
        alerts = _alerts(monkeypatch)
        _a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        first = _cr_close_b(db, monkeypatch, 6)
        assert (first["variance_status"], first["till_session_id"]) == ("SEVERAL_DRAWERS", b)
        _counted_at(db, b, "12:00:00")
        second = _second_register_close(db, monkeypatch, 7)
        assert (second["counted"], second["expected"], second["variance"], second["variance_status"]) == (
            3500.0, 3500.0, 0.0, "BALANCED")
        assert second["till_drawers_on_day"] == 2 and alerts == []

    def test_a_count_made_while_the_session_is_open_is_where_the_close_starts(self, db, monkeypatch):
        """D120 (it replaced this round-17 control, which kept the till's
        count against the whole window: Rs 1,000 "counted", Rs 2,000 SHORT).
        The till is counted through the other door WHILE this register
        session is open (Rs 1,000 of Rs 3,000, the loss is on that count).
        The close is judged on its own sheet from that count: Rs 3,000 in
        hand is Rs 2,000 MORE than it, a real difference between two counts,
        stored with its note. The till's count is not replaced (L3-12)."""
        sid = _open(db)
        _cash_sale(db, 3000)
        assert till.blind_submit(
            db, sid, blind_denominations=[{"face": 500, "kind": "note", "pieces": 2}],
            actor=CASHIER)["ok"]
        _counted_at(db, sid, "10:00:00")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R17-1", opened="04:00:00", closed="12:00:00", pieces=6)
        assert exc.value.status_code == 400
        out = _register(db, monkeypatch, "CR-R17-1", opened="04:00:00", closed="12:00:00",
                        pieces=6, note="Rs 2,000 found after the till count", insert=False)
        assert (out["counted"], out["counted_from_shared_record"], out["expected"], out["variance"],
                out["variance_status"]) == (3000.0, False, 1000.0, 2000.0, "OVER")
        assert (out["till_counted_earlier"], out["till_standing_count"]) == (True, 1000.0)
        assert _till(db, sid)["blind_count_paisa"] == 100000
        # Round 20: the tally's own Rs 2,000 shortage was never explained
        # (nobody locked it). The same note covers it and the close owes its
        # alert under the drawer's key, beside the alert for its own sheet.
        assert out["till_count_variance"] == -2000.0
        assert [(t["session_id"], t["variance_paisa"]) for t in out["manager_followup"]["tasks"]] == [
            ("CR-R17-1", 200000), (sid, -200000)]


class TestR17D110EveryReaderAndRetryPaysTheOwedTillRows:
    """A till write whose audit row could not be stored answers saved and
    owes the row. It used to be paid only by the Blind EOD screens."""

    @staticmethod
    def _audit_down(monkeypatch, audit_rows):
        up = []
        store = _AuditStore(audit_rows, up=lambda: bool(up))
        monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: store)
        monkeypatch.setattr(reports_router, "get_audit_repository", lambda: store)
        return up

    @staticmethod
    def _actions(audit_rows, sid):
        return [r["action"] for r in audit_rows if r.get("entity_id") == sid]

    def test_the_cash_reconciliation_read_pays_a_locked_drawer(self, db, monkeypatch, audit_rows):
        up = self._audit_down(monkeypatch, audit_rows)
        _cash_sale(db, 1000)
        sid = _blind_count(db, rupees=1000, lock=True)
        assert audit_rows == [] and _till(db, sid)["manager_followup"]["pending"] is True
        up.append(True)
        for _ in range(2):
            _recon_summary(db, monkeypatch)
        assert self._actions(audit_rows, sid) == ["till.open", "till.blind_submit", "till.lock"]
        assert [r["user_id"] for r in audit_rows] == ["u-cash", "u-cash", "u-mgr"]
        assert _till(db, sid)["manager_followup"]["pending"] is False

    @pytest.mark.parametrize("repair", ["recon", "register_retry", "register_list"])
    def test_a_register_close_that_opened_and_counted_the_drawer(
        self, db, monkeypatch, audit_rows, repair
    ):
        up = self._audit_down(monkeypatch, audit_rows)
        _cash_sale(db, 1000)
        out = _cr_close_two_drawers(
            db, monkeypatch, denominations=[{"face": 500, "kind": "note", "pieces": 2}],
            closing_count_state="COUNTED")
        sid = out["till_session_id"]
        assert out["till_link_ok"] is True and audit_rows == []
        assert _till(db, sid)["status"] == till.STATUS_BLIND_SUBMITTED
        up.append(True)
        for _ in range(2):
            if repair == "recon":
                _recon_summary(db, monkeypatch)
            elif repair == "register_list":
                _cr_list(db, monkeypatch)
            else:
                with pytest.raises(HTTPException) as exc:
                    _cr_again(db, monkeypatch,
                              denominations=[{"face": 500, "kind": "note", "pieces": 2}])
                assert exc.value.status_code == 409
        assert self._actions(audit_rows, sid) == ["till.open", "till.blind_submit"]
        assert _till(db, sid)["manager_followup"]["pending"] is False

    @pytest.mark.parametrize("repair", ["retry", "status_read"])
    def test_a_day_end_close_that_opened_and_counted_the_drawer(
        self, db, monkeypatch, audit_rows, day_end, repair
    ):
        up = self._audit_down(monkeypatch, audit_rows)
        _cash_sale(db, 1000)
        sid = day_end(closing_cash=1000.0, system_cash=1000.0)["close"]["till_session_id"]
        assert self._actions(audit_rows, sid) == []
        up.append(True)
        for _ in range(2):
            if repair == "retry":
                with pytest.raises(HTTPException) as exc:
                    day_end(closing_cash=1000.0, system_cash=1000.0)
                assert exc.value.status_code == 409
            else:
                asyncio.run(reports_router.get_day_end_close(
                    date=DATE, store_id=STORE, current_user=MANAGER))
        assert self._actions(audit_rows, sid) == ["till.open", "till.blind_submit"]
        assert _till(db, sid)["manager_followup"]["pending"] is False

    def test_a_close_that_joins_a_counted_drawer_pays_what_it_owes(self, db, monkeypatch, audit_rows):
        up = self._audit_down(monkeypatch, audit_rows)
        _cash_sale(db, 1000)
        sid = _blind_count(db, rupees=1000, lock=False)
        assert audit_rows == []
        up.append(True)
        link = till.record_screen_close(
            db, store_id=STORE, session_date=DATE, counted_paisa=100000, actor=MANAGER)
        assert link["ok"] and link["already_counted"]
        assert self._actions(audit_rows, sid) == ["till.open", "till.blind_submit"]

    def test_an_unreadable_drawer_leaves_the_rows_owed_and_the_retry_answers(
        self, db, monkeypatch, audit_rows
    ):
        """The repair read fails: the retry still answers the saved close and
        the rows stay owed for the next read."""
        up = self._audit_down(monkeypatch, audit_rows)
        _cash_sale(db, 1000)
        out = _cr_close_two_drawers(
            db, monkeypatch, denominations=[{"face": 500, "kind": "note", "pieces": 2}],
            closing_count_state="COUNTED")
        sid = out["till_session_id"]
        up.append(True)
        restore = _fail_reads(db, "till_sessions")
        try:
            with pytest.raises(HTTPException) as exc:
                _cr_again(db, monkeypatch, denominations=[{"face": 500, "kind": "note", "pieces": 2}])
        finally:
            restore()
        assert exc.value.status_code == 409 and self._actions(audit_rows, sid) == []
        till.list_sessions(db, store_id=STORE)
        assert self._actions(audit_rows, sid) == ["till.open", "till.blind_submit"]


class TestR17D111CloseThroughFaults:
    """The failure classes of the earlier rounds, on the D111 close."""

    def test_lost_ack_then_retries_keep_one_close_and_its_one_alert(self, db, monkeypatch):
        """The session is really Rs 500 short on its own sheet. Its close
        commits, the acknowledgement is lost and the read-back fails: 500.
        The identical retry and a retry with another count answer 409 with
        the saved close; the alert is raised once, under the session's key."""
        _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        raised = _raised(monkeypatch)
        cr = db.get_collection("cash_register_sessions")
        fired = _lose_ack(monkeypatch, cr, "update_one", _sets_status("CLOSED"))
        _fail_read_back(monkeypatch, cr, fired)
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                      opening=3000.0, pieces=6, note="Rs 500 short")
        assert exc.value.status_code == 500 and fired == [True] and raised == []
        for pieces in (6, 7):
            with pytest.raises(HTTPException) as exc:
                _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:05:00",
                          pieces=pieces, note="Rs 500 short", insert=False)
            code, body = _through_the_app_handler(exc.value)
            saved = body["detail"]["session"]
            assert code == 409
            assert (saved["counted"], saved["expected"], saved["variance"], saved["variance_status"]) == (
                3000.0, 3500.0, -500.0, "SHORT")
        assert raised == [("raise_variance_task", till.followup_task_id("variance", "CR-R17-2"))]
        stored = [d for d in cr.docs if d["session_id"] == "CR-R17-2"]
        assert len(stored) == 1 and stored[0]["manager_followup"]["pending"] is False
        assert stored[0]["manager_followup"]["tasks"][0]["session_id"] == "CR-R17-2"

    def test_a_failed_read_of_the_cash_since_the_count_refuses_the_close(self, db, monkeypatch):
        """The read of the cash moved since the standing count fails: 503,
        nothing is written anywhere, and the same close then goes through.
        A zero there would have stored Rs 500 OVER."""
        sid, _first = _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        before = dict(_till(db, sid))
        real = till.compute_expected
        calls = []

        def _day_read_fails(*a, **k):
            calls.append(a[4])
            if len(calls) == 1:
                raise AutoReconnect("orders read fault")
            return real(*a, **k)

        monkeypatch.setattr(till, "compute_expected", _day_read_fails)
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                      opening=3000.0, pieces=7)
        assert exc.value.status_code == 503
        cr = db.get_collection("cash_register_sessions").find_one({"session_id": "CR-R17-2"})
        assert cr["status"] == "OPEN" and _till(db, sid) == before
        out = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                        pieces=7, insert=False)
        assert (out["counted"], out["expected"], out["variance_status"]) == (3500.0, 3500.0, "BALANCED")
        # The day read starts from the standing count, both times.
        assert calls == [300000, 300000]


def test_r17_first_counted_close_adds_and_d111_on_real_mongo(monkeypatch):
    """Native PyMongo with the real till indexes, the REAL AuditRepository
    (hash chain) and TaskRepository. The must-fix sequence with the audit
    store down for both closes, then a third session that is really short on
    its own sheet; the reconciliation is read as stored."""
    from pymongo.collection import Collection

    from database.repositories.audit_chain import verify_chain
    from database.repositories.audit_repository import AuditRepository

    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        tasks = _real_task_repo(monkeypatch, real_db)
        monkeypatch.setattr(
            "api.dependencies.get_audit_repository", lambda: AuditRepository(real_db.audit_logs))
        ndb = _NativeDB(real_db)
        down = [True]
        for method in ("find_one", "insert_one"):
            real = getattr(Collection, method)

            def _fault(self, *a, _real=real, **k):
                if down and self.database.name == name and self.name == "audit_logs":
                    raise AutoReconnect("native audit store fault")
                return _real(self, *a, **k)

            monkeypatch.setattr(Collection, method, _fault)

        # A close without a count, then the close that counts the drawer.
        first = _register(ndb, monkeypatch, "CR-R17-1", opened="04:00:00", closed="09:00:00")
        sid = first["till_session_id"]
        assert (first["variance_status"], first["till_link_ok"]) == ("NOT_COUNTED", True)
        _cash_sale(real_db, 3000, datetime(2026, 11, 4, 10, 0))
        second = _register(ndb, monkeypatch, "CR-R17-2", opened="09:30:00", closed="12:00:00", pieces=6)
        assert (second["till_session_id"], second["counted"]) == (sid, 3000.0)
        assert real_db.audit_logs.count_documents({}) == 0
        assert real_db.till_sessions.find_one({"_id": sid})["manager_followup"]["pending"] is True

        # D110: the reconciliation read pays the rows; D88: the count is in the totals.
        down.clear()
        for _ in range(2):
            res = _recon_summary(ndb, monkeypatch)
        t = res["totals"]
        assert (t["counted_cash"], t["cash_sales"], t["sessions"], t["also_counted_on_till"]) == (
            3000.0, 3000.0, 2, 0)
        rows = list(real_db.audit_logs.find({"entity_id": sid}).sort("seq", 1))
        assert [r["action"] for r in rows] == ["till.open", "till.blind_submit"]
        chain = verify_chain(real_db.audit_logs)
        assert chain["intact"] and chain["entries_checked"] == 2, chain
        assert real_db.till_sessions.find_one({"_id": sid})["manager_followup"]["pending"] is False

        # D111: a third session opened after that count, Rs 500 short on its own sheet.
        _counted_at(real_db, sid, "12:00:00")
        _cash_sale(real_db, 500, datetime(2026, 11, 4, 12, 45))
        with pytest.raises(HTTPException) as exc:
            _register(ndb, monkeypatch, "CR-R17-3", opened="12:30:00", closed="13:00:00",
                      opening=3000.0, pieces=6)
        assert exc.value.status_code == 400
        assert real_db.cash_register_sessions.find_one({"session_id": "CR-R17-3"})["status"] == "OPEN"
        third = _register(ndb, monkeypatch, "CR-R17-3", opened="12:30:00", closed="13:00:00",
                          pieces=6, note="Rs 500 short", insert=False)
        assert (third["counted"], third["expected"], third["variance"], third["variance_status"]) == (
            3000.0, 3500.0, -500.0, "SHORT")
        assert tasks.count_documents({"source_ref": f"till_variance:{STORE}:{DATE}:CR-R17-3"}) == 1
        assert tasks.count_documents({}) == 1
        assert real_db.till_sessions.find_one({"_id": sid})["blind_count_paisa"] == 300000
        # The identical retry: the saved close, no second task.
        with pytest.raises(HTTPException) as exc:
            _register(ndb, monkeypatch, "CR-R17-3", opened="12:30:00", closed="13:00:00",
                      pieces=6, note="Rs 500 short", insert=False)
        assert exc.value.status_code == 409 and tasks.count_documents({}) == 1
        # Totals unchanged: the earliest counted close adds, the later is marked.
        res = _recon_summary(ndb, monkeypatch)
        t = res["totals"]
        assert (t["counted_cash"], t["shortage"], t["also_counted_on_till"]) == (3000.0, 0, 1)
        assert _recon_row(res, "CR-R17-3")["variance_status"] == "SHORTAGE"
    finally:
        client.drop_database(name)
        client.close()


# ===========================================================================
# Round 18 (review of head 9d3241b0): D111 was not closed
# ===========================================================================
# MUST-FIX: the D111 expected cash re-read "cash since the count's instant".
# Payouts are booked by DAY, so the day's payouts already out of the standing
# count were taken off again (a false excess, a note demanded, the manager
# alerted); orders are read by created_at, so a balance paid after the count
# on an order opened before it was in no figure. It is now the till's own D60
# rule: the day so far minus what the day's counted drawers were charged with.
# Also: D88(1) a close whose count did not reach the till, beside a later close
# that holds the drawer's count; D110 the three readers left out.


def _payout(db, rupees):
    db.get_collection("expenses").insert_one({
        "store_id": STORE, "expense_date": DATE, "payment_mode": "CASH",
        "status": "APPROVED", "amount": float(rupees), "category": "TEA",
    })


def _judged(out):
    return {k: out[k] for k in ("counted", "expected", "variance", "variance_status")}


def _balanced(rupees):
    return {"counted": rupees, "expected": rupees, "variance": 0.0, "variance_status": "BALANCED"}


def _count_misses_the_till(db, monkeypatch, sid):
    """A Cash Register close whose count does not reach the till (the guarded
    count write fails after the day's drawers were read)."""
    with monkeypatch.context() as m:
        m.setattr(till, "blind_submit", lambda *a, **k: {"ok": False, "error": "submit_failed"})
        return _register(db, m, sid, opened="04:00:00", closed="09:00:00", pieces=6)


class TestR18D111CashSinceTheCountIsTheDaySoFarMinusTheCountedDrawers:
    def test_a_payout_of_the_day_paid_before_the_standing_count_is_not_charged_again(self, db, monkeypatch):
        """The reviewers' sequence: Rs 3,000 taken, Rs 500 paid out, Rs 2,500
        counted at the first close (balanced). Rs 500 more taken: Rs 3,000 in
        hand read Rs 500 OVER, the close was refused for a note and the
        manager alerted."""
        alerts = _alerts(monkeypatch)
        _payout(db, 500)
        sid, first = _one_drawer_counted_by_a_register(db, monkeypatch, pieces=5)
        assert (first["expected"], first["cash_expenses"], first["variance_status"]) == (
            2500.0, 500.0, "BALANCED")
        assert _till(db, sid)["cash_payouts_paisa"] == 50000
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           opening=2500.0, pieces=6)
        assert _judged(second) == _balanced(3000.0)
        assert (second["cash_sales"], second["cash_expenses"], second["till_standing_count"]) == (
            500.0, 0.0, 2500.0)
        assert alerts == [] and second["manager_followup"]["tasks"] == []
        # The row ties out on the console: opening + cash - payouts = expected.
        row = _recon_row(_recon_summary(db, monkeypatch), "CR-R17-2")
        assert (row["opening_float"], row["cash_sales"], row["cash_expenses"], row["expected_cash"],
                row["variance_status"]) == (2500.0, 500.0, 0.0, 3000.0, "BALANCED")

    def test_a_payout_booked_after_the_count_is_charged_once_to_the_later_session(self, db, monkeypatch):
        """One payout before the count, one after: the second session carries
        the later one only. A third session starts from the second's sheet
        (D120), so it carries neither; it is really Rs 500 short."""
        alerts = _alerts(monkeypatch)
        _payout(db, 500)
        sid, _first = _one_drawer_counted_by_a_register(db, monkeypatch, pieces=5)
        _cash_sale(db, 1000, f"{DATE}T12:45:00")
        _payout(db, 500)
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           opening=2500.0, pieces=6)
        assert _judged(second) == _balanced(3000.0)
        assert (second["cash_sales"], second["cash_expenses"]) == (1000.0, 500.0)
        _cash_sale(db, 1000, f"{DATE}T13:30:00")
        third = _register(db, monkeypatch, "CR-R17-3", opened="13:10:00", closed="14:00:00",
                          opening=3000.0, pieces=7, note="Rs 500 short")
        assert _judged(third) == {
            "counted": 3500.0, "expected": 4000.0, "variance": -500.0, "variance_status": "SHORT"}
        assert (third["cash_sales"], third["cash_expenses"], third["till_standing_count"]) == (
            1000.0, 0.0, 3000.0)
        assert _till(db, sid)["blind_count_paisa"] == 250000
        assert [a["session_id"] for a in alerts] == ["CR-R17-3"]

    def test_a_balance_paid_after_the_count_on_an_order_opened_before_it(self, db, monkeypatch):
        """The reviewers' sequence 2: Rs 500 advance before the count, the
        Rs 2,000 balance in cash after it. The order's created_at is before
        the count, so the balance was in no figure: Rs 2,000 OVER."""
        alerts = _alerts(monkeypatch)
        held = _cash_sale(db, 500, f"{DATE}T05:00:00")
        sid = _open(db)
        first = _register(db, monkeypatch, "CR-R17-1", opened="04:00:00", closed="12:00:00", pieces=1)
        assert first["variance_status"] == "BALANCED"
        _counted_at(db, sid, "12:00:00")
        db.get_collection("orders").update_one(
            {"_id": held}, {"$push": {"payments": {"method": "CASH", "amount": 2000.0}}})
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           opening=500.0, pieces=5)
        assert _judged(second) == _balanced(2500.0)
        assert second["cash_sales"] == 2000.0 and alerts == []

    def test_another_drawers_day_booked_payout_is_not_charged_to_the_session(self, db, monkeypatch):
        """Several drawers: drawer A was charged with the day's Rs 200 payout
        at its count. A D111 session on drawer B used to take it off again."""
        alerts = _alerts(monkeypatch)
        _payout(db, 200)
        a, b = _drawer_a_then_b(db, a_sales=[1200], b_sales=[3000])
        assert _till(db, a)["cash_payouts_paisa"] == 20000
        _cr_close_b(db, monkeypatch, 6)
        _counted_at(db, b, "12:00:00")
        second = _second_register_close(db, monkeypatch, 7)
        assert _judged(second) == _balanced(3500.0)
        assert (second["cash_sales"], second["cash_expenses"]) == (500.0, 0.0)
        assert alerts == []

    def test_an_older_code_snapshot_beside_another_drawer_gives_no_comparison(self, db, monkeypatch):
        """D76: a counted drawer of the day holds whole-day cash from older
        code, so the cash since the count is not known. Nothing is compared,
        no note is asked, and the sheet goes to the manager to check by hand."""
        raised = _raised(monkeypatch)
        _cash_sale(db, 1000, f"{DATE}T03:00:00")
        TestR8D76OlderCodeDrawers._old_drawer(db, "OLD-A", 100000, 100000, f"{DATE}T02:00:00")
        TestR8D76OlderCodeDrawers._old_drawer(db, "OLD-B", 400000, 300000, f"{DATE}T05:00:00")
        db.get_collection("till_sessions").update_one(
            {"_id": "OLD-B"}, {"$set": {"blind_submitted_at": datetime.fromisoformat(f"{DATE}T06:00:00")}})
        out = _register(db, monkeypatch, "CR-R18-1", opened="06:30:00", closed="08:00:00",
                        opening=3000.0, pieces=7)
        assert (out["till_session_id"], out["till_counted_earlier"]) == ("OLD-B", True)
        assert (out["counted"], out["variance"], out["variance_status"]) == (
            3500.0, None, "COMPARISON_UNAVAILABLE")
        assert raised == [("raise_count_not_compared_task", till.followup_task_id("uncompared", "CR-R18-1"))]
        row = _recon_row(_recon_summary(db, monkeypatch), "CR-R18-1")
        assert (row["cash_sales"], row["expected_cash"], row["variance"], row["counted_cash"]) == (
            None, None, None, 3500.0)

    def test_control_one_older_code_drawer_alone_is_its_own_share(self, db, monkeypatch):
        """A single counted drawer written by older code holds its own cash
        (the whole day at its count), so the session is still judged."""
        _cash_sale(db, 1000, f"{DATE}T03:00:00")
        TestR8D76OlderCodeDrawers._old_drawer(db, "OLD-A", 100000, 100000, f"{DATE}T02:00:00")
        db.get_collection("till_sessions").update_one(
            {"_id": "OLD-A"}, {"$set": {"blind_submitted_at": datetime.fromisoformat(f"{DATE}T06:00:00")}})
        _cash_sale(db, 500, f"{DATE}T07:00:00")
        out = _register(db, monkeypatch, "CR-R18-1", opened="06:30:00", closed="08:00:00",
                        opening=1000.0, pieces=3)
        assert _judged(out) == _balanced(1500.0)


class TestR18ACountOffTheTillBesideOneOnItIsNotAddedTwice:
    def test_failed_link_close_then_blind_count_then_a_second_register_close(self, db, monkeypatch):
        """D88(1). Close 1 counts Rs 3,000 but its count does not reach the
        till; the drawer is counted on the Blind EOD Tally (not locked); a
        second register session takes Rs 500 and counts Rs 3,500. Both closes
        added: Rs 6,500 for a drawer holding Rs 3,500, until the Z-Read locked."""
        _follow_ups(monkeypatch)
        sid = _open(db)
        _cash_sale(db, 3000)
        first = _count_misses_the_till(db, monkeypatch, "CR-R18-1")
        assert (first["till_link_ok"], first["counted"], first["till_drawers_on_day"]) == (False, 3000.0, 1)
        assert till.blind_submit(
            db, sid, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}], actor=CASHIER)["ok"]
        _counted_at(db, sid, "10:00:00")
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        second = _register(db, monkeypatch, "CR-R18-2", opened="12:30:00", closed="13:00:00",
                           opening=3000.0, pieces=7)
        assert (second["till_link_ok"], second["till_counted_earlier"]) == (True, True)
        assert _judged(second) == _balanced(3500.0)

        res = _recon_summary(db, monkeypatch)
        t = res["totals"]
        # D121 (round 20): the later close is never in the totals, so the
        # close whose count missed the till is the drawer's one row in them
        # until the Z-Read locks: Rs 3,000 before the lock and after it,
        # never Rs 6,500.
        assert (t["counted_cash"], t["expected_cash"], t["sessions"], t["also_counted_on_till"]) == (
            3000.0, 3000.0, 1, 1)
        assert _recon_row(res, "CR-R18-1")["also_counted_on_till"] is False
        assert _recon_row(res, "CR-R18-2")["also_counted_on_till"] is True
        assert _recon_row(res, "CR-R18-2")["counted_cash"] == 3500.0  # the row still shows
        assert (t["later_closes"], t["later_closes_short"], t["later_closes_over"]) == (1, 0.0, 0.0)
        assert till.lock_session(db, sid, actor=MANAGER)["ok"]
        t = _recon_summary(db, monkeypatch)["totals"]
        assert (t["counted_cash"], t["sessions"], t["also_counted_on_till"]) == (3000.0, 1, 2)
        assert (t["later_closes"], t["later_closes_short"], t["later_closes_over"]) == (1, 0.0, 0.0)

    def test_control_a_count_off_the_till_alone_still_adds(self, db, monkeypatch):
        """No close of the day holds the drawer's count and no Z-Read row
        exists: the failed-link close keeps adding, as before."""
        _follow_ups(monkeypatch)
        _open(db)
        _cash_sale(db, 3000)
        _count_misses_the_till(db, monkeypatch, "CR-R18-1")
        t = _recon_summary(db, monkeypatch)["totals"]
        assert (t["counted_cash"], t["also_counted_on_till"]) == (3000.0, 0)


class TestR18D110TheRemainingReadersPayTheOwedTillRows:
    """D110 says EVERY reader of till sessions. Three were left out: the
    refund door's "is today's drawer counted" check, the bank reconciliation's
    expected-deposit read and the Cash Reconciliation sign-off."""

    @staticmethod
    def _owed(db, monkeypatch, audit_rows, *, lock):
        up = TestR17D110EveryReaderAndRetryPaysTheOwedTillRows._audit_down(monkeypatch, audit_rows)
        _cash_sale(db, 1000)
        sid = _blind_count(db, rupees=1000, lock=lock)
        assert audit_rows == [] and _till(db, sid)["manager_followup"]["pending"] is True
        up.append(True)
        return sid

    @staticmethod
    def _paid(db, audit_rows, sid, want):
        assert [r["action"] for r in audit_rows if r["action"].startswith("till.")] == want
        assert {r["entity_id"] for r in audit_rows if r["action"].startswith("till.")} == {sid}
        assert _till(db, sid)["manager_followup"]["pending"] is False

    def test_the_refund_doors_drawer_check(self, db, monkeypatch, audit_rows):
        from datetime import date

        from api.routers import returns as returns_router

        sid = self._owed(db, monkeypatch, audit_rows, lock=False)
        monkeypatch.setattr(returns_router, "_get_db", lambda: db)
        monkeypatch.setattr("api.utils.ist.ist_today", lambda: date.fromisoformat(DATE))
        for _ in range(2):
            assert returns_router._drawer_closed_today(STORE) is True
        self._paid(db, audit_rows, sid, ["till.open", "till.blind_submit"])
        # Control: a shop with no counted drawer answers as before.
        assert returns_router._drawer_closed_today(OTHER_STORE) is False

    def test_the_bank_reconciliations_expected_deposit_read(self, db, monkeypatch, audit_rows):
        from api.services.bank_reconciliation import BankReconciliationEngine

        sid = self._owed(db, monkeypatch, audit_rows, lock=False)
        for _ in range(2):
            trail = BankReconciliationEngine(db).build_cash_expected(STORE, DATE, DATE)
            assert [(t["expected_paise"], t["source_id"]) for t in trail] == [(100000, sid)]
        self._paid(db, audit_rows, sid, ["till.open", "till.blind_submit"])

    def test_the_cash_reconciliation_sign_off(self, db, monkeypatch, audit_rows):
        sid = self._owed(db, monkeypatch, audit_rows, lock=True)
        monkeypatch.setattr(finance_router.cash_recon, "_get_db", lambda: db)
        for _ in range(2):
            asyncio.run(finance_router.cash_recon.cash_reconciliation_signoff(
                body=finance_router.cash_recon.CashReconSignoff(session_id=sid, source="BLIND_EOD"),
                current_user=MANAGER))
        self._paid(db, audit_rows, sid, ["till.open", "till.blind_submit", "till.lock"])


def test_r18_d111_payout_and_later_balance_on_real_mongo(monkeypatch):
    """Native PyMongo with the real till indexes and TaskRepository: a payout
    of the day and a cash advance before the first close's count; the balance
    of that order and a second payout after it. The second session is charged
    with the later payout only and holds the balance; no note, no alert."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        tasks = _real_task_repo(monkeypatch, real_db)
        ndb = _NativeDB(real_db)
        held = _cash_sale(real_db, 500, datetime(2026, 11, 4, 5, 0))
        _cash_sale(real_db, 2500, datetime(2026, 11, 4, 6, 0))
        _payout(real_db, 500)
        first = _register(ndb, monkeypatch, "CR-R18-1", opened="04:00:00", closed="12:00:00", pieces=5)
        sid = first["till_session_id"]
        assert (first["expected"], first["variance_status"], first["till_link_ok"]) == (
            2500.0, "BALANCED", True)
        _counted_at(real_db, sid, "12:00:00")
        real_db.orders.update_one(
            {"_id": held}, {"$push": {"payments": {"method": "CASH", "amount": 2000.0}}})
        _payout(real_db, 500)
        second = _register(ndb, monkeypatch, "CR-R18-2", opened="12:30:00", closed="13:00:00",
                           opening=2500.0, pieces=8)
        assert _judged(second) == _balanced(4000.0)
        assert (second["cash_sales"], second["cash_expenses"], second["till_standing_count"]) == (
            2000.0, 500.0, 2500.0)
        stored = real_db.cash_register_sessions.find_one({"session_id": "CR-R18-2"})
        assert (stored["expected"], stored["variance"], stored["manager_followup"]["tasks"]) == (
            4000.0, 0.0, [])
        assert tasks.count_documents({}) == 0
        assert real_db.till_sessions.find_one({"_id": sid})["blind_count_paisa"] == 250000
    finally:
        client.drop_database(name)
        client.close()


# ===========================================================================
# Round 19 (review of head 1ae62d8d): the lead's rulings D120 and D121
# ===========================================================================
# D111's formula (opening = the drawer's standing count) could not be made
# right. D120 replaces it: a Cash Register close is judged on its own sheet
# against the most recent count of that drawer made by anything other than
# this close itself -- the till's count (the Blind EOD Tally, Day-End or the
# register close that first counted it, standing at opening OR made while the
# session was open) or an earlier register session's own sheet, whichever is
# later -- plus the cash moved since it, less this close's own bank deposit.
# The session's own refused-and-resent count is told apart by who made the
# till's count (blind_count_origin). D121: such closes stay out of the range
# totals (D88) and are named beside them with their own differences.


def _third(db, monkeypatch, sid="CR-R17-3", *, opened="13:10:00", closed="14:00:00", **kw):
    return _register(db, monkeypatch, sid, opened=opened, closed=closed, **kw)


class TestR19D120TheNextSessionStartsFromTheLatestOwnSheet:
    def test_a_bank_deposit_on_the_second_session_is_not_charged_to_the_third(self, db, monkeypatch):
        """The reviewers' sequence A: Rs 3,000 counted; session 2 takes
        Rs 1,000, banks Rs 2,000, counts Rs 2,000 (balanced); session 3 takes
        Rs 500 and counts Rs 2,500. It read Rs 2,000 SHORT."""
        alerts = _alerts(monkeypatch)
        sid, _first = _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 1000, f"{DATE}T12:45:00")
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           opening=3000.0, pieces=4, bank=2000.0)
        assert _judged(second) == _balanced(2000.0) and second["bank_deposit"] == 2000.0
        _cash_sale(db, 500, f"{DATE}T13:30:00")
        third = _third(db, monkeypatch, opening=2000.0, pieces=5)
        assert _judged(third) == _balanced(2500.0)
        assert (third["till_counted_earlier"], third["till_standing_count"], third["cash_sales"],
                third["till_standing_counted_at"]) == (True, 2000.0, 500.0, f"{DATE}T13:00:00")
        assert third["by_mode_breakdown"]["CASH"]["collected"] == 500.0
        assert alerts == [] and third["manager_followup"]["tasks"] == []
        # A fourth session starts from the third's sheet, and so on.
        _cash_sale(db, 500, f"{DATE}T14:30:00")
        fourth = _register(db, monkeypatch, "CR-R17-4", opened="14:10:00", closed="15:00:00",
                           opening=2500.0, pieces=6)
        assert _judged(fourth) == _balanced(3000.0)
        assert (fourth["till_standing_count"], fourth["cash_sales"]) == (2500.0, 500.0)
        assert alerts == []
        # The drawer's blind count is not replaced (L3-12); D88 totals unchanged.
        assert _till(db, sid)["blind_count_paisa"] == 300000
        res = _recon_summary(db, monkeypatch)
        t = res["totals"]
        assert (t["counted_cash"], t["cash_sales"], t["shortage"], t["also_counted_on_till"]) == (
            3000.0, 3000.0, 0, 3)
        row = _recon_row(res, "CR-R17-3")
        assert (row["opening_float"], row["cash_sales"], row["expected_cash"], row["variance_status"]) == (
            2000.0, 500.0, 2500.0, "BALANCED")

    def test_a_shortage_the_second_session_noted_is_not_demanded_again(self, db, monkeypatch):
        """Sequence B: session 2 is really Rs 500 short (noted, alerted).
        Session 3 takes Rs 500 and counts everything present: it read Rs 500
        SHORT again. A real shortage on a later sheet still needs its note."""
        alerts = _alerts(monkeypatch)
        _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           opening=3000.0, pieces=6, note="Rs 500 short")
        assert (second["variance"], second["variance_status"]) == (-500.0, "SHORT")
        _cash_sale(db, 500, f"{DATE}T13:30:00")
        third = _third(db, monkeypatch, opening=3000.0, pieces=7)
        assert _judged(third) == _balanced(3500.0)
        assert (third["till_standing_count"], third["cash_sales"]) == (3000.0, 500.0)
        assert [a["session_id"] for a in alerts] == ["CR-R17-2"]
        assert third["manager_followup"]["tasks"] == []

        _cash_sale(db, 500, f"{DATE}T14:30:00")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R17-4", opened="14:10:00", closed="15:00:00",
                      opening=3500.0, pieces=7)
        assert exc.value.status_code == 400 and "variance_note_required" in exc.value.detail
        fourth = _register(db, monkeypatch, "CR-R17-4", opened="14:10:00", closed="15:00:00",
                           pieces=7, note="Rs 500 short again", insert=False)
        assert _judged(fourth) == {
            "counted": 3500.0, "expected": 4000.0, "variance": -500.0, "variance_status": "SHORT"}
        assert [a["session_id"] for a in alerts] == ["CR-R17-2", "CR-R17-4"]

    def test_a_session_with_no_sheet_of_its_own_is_not_a_starting_point(self, db, monkeypatch):
        """Session 2 closes without a count (Rs 1,000 taken after the till's
        count): it counted nothing, so session 3 still starts from the till's
        count and carries that Rs 1,000."""
        alerts = _alerts(monkeypatch)
        _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 1000, f"{DATE}T12:45:00")
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           opening=3000.0)
        assert (second["variance_status"], second["cash_sales"]) == ("COUNTED_EARLIER", 1000.0)
        _cash_sale(db, 500, f"{DATE}T13:30:00")
        third = _third(db, monkeypatch, opening=4000.0, pieces=9)
        assert _judged(third) == _balanced(4500.0)
        assert (third["till_standing_count"], third["cash_sales"]) == (3000.0, 1500.0)
        assert alerts == []

    def test_an_unreadable_register_refuses_the_close(self, db, monkeypatch):
        """The read of the earlier sessions fails: 503, the session stays
        OPEN, and the same close then goes through. Read as "none", session 3
        would have stored the Rs 2,000 banked by session 2 as a shortage."""
        _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 1000, f"{DATE}T12:45:00")
        _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                  opening=3000.0, pieces=4, bank=2000.0)
        _cash_sale(db, 500, f"{DATE}T13:30:00")
        cr = db.get_collection("cash_register_sessions")
        real_find = cr.find
        down = [True]

        def _find(*a, **k):
            if down:
                raise AutoReconnect("register read fault")
            return real_find(*a, **k)

        monkeypatch.setattr(cr, "find", _find)
        with pytest.raises(HTTPException) as exc:
            _third(db, monkeypatch, opening=2000.0, pieces=5)
        assert exc.value.status_code == 503
        assert cr.find_one({"session_id": "CR-R17-3"})["status"] == "OPEN"
        down.clear()
        third = _third(db, monkeypatch, pieces=5, insert=False)
        assert _judged(third) == _balanced(2500.0)


    def test_a_deposit_banked_on_a_close_with_no_sheet_is_not_charged_to_the_next(self, db, monkeypatch):
        """Session 2 takes Rs 1,000, banks Rs 2,000 and closes WITHOUT a
        count: it is no starting point, but the cash it recorded as banked
        left the drawer. Session 3 takes Rs 500 and counts Rs 2,500: it read
        Rs 2,000 SHORT. A fourth starts from the third's sheet, and the
        deposit is not taken off a second time."""
        alerts = _alerts(monkeypatch)
        _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 1000, f"{DATE}T12:45:00")
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           opening=3000.0, bank=2000.0)
        assert (second["variance_status"], second["variance"], second["bank_deposit"]) == (
            "COUNTED_EARLIER", None, 2000.0)
        _cash_sale(db, 500, f"{DATE}T13:30:00")
        third = _third(db, monkeypatch, opening=2000.0, pieces=5)
        assert _judged(third) == _balanced(2500.0)
        assert (third["till_standing_count"], third["till_banked_since_count"], third["cash_sales"],
                third["bank_deposit"]) == (1000.0, 2000.0, 1500.0, 0.0)
        _cash_sale(db, 500, f"{DATE}T14:30:00")
        fourth = _register(db, monkeypatch, "CR-R17-4", opened="14:10:00", closed="15:00:00",
                           opening=2500.0, pieces=6)
        assert _judged(fourth) == _balanced(3000.0)
        assert (fourth["till_standing_count"], fourth["till_banked_since_count"]) == (2500.0, 0.0)
        assert alerts == []

    def test_the_third_close_lands_and_its_reply_is_lost(self, db, monkeypatch):
        """D110 on sequence A: the third close commits, its acknowledgement
        is lost and the read-back fails (500). The identical retry, twice,
        answers 409 with the saved balanced close; one row, no task."""
        raised = _raised(monkeypatch)
        _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 1000, f"{DATE}T12:45:00")
        _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                  opening=3000.0, pieces=4, bank=2000.0)
        _cash_sale(db, 500, f"{DATE}T13:30:00")
        cr = db.get_collection("cash_register_sessions")
        fired = _lose_ack(monkeypatch, cr, "update_one", _sets_status("CLOSED"))
        _fail_read_back(monkeypatch, cr, fired)
        with pytest.raises(HTTPException) as exc:
            _third(db, monkeypatch, opening=2000.0, pieces=5)
        assert exc.value.status_code == 500 and fired == [True]
        for _ in range(2):
            with pytest.raises(HTTPException) as exc:
                _third(db, monkeypatch, pieces=5, insert=False)
            code, body = _through_the_app_handler(exc.value)
            assert code == 409 and _judged(body["detail"]["session"]) == _balanced(2500.0)
        stored = [d for d in cr.docs if d["session_id"] == "CR-R17-3"]
        assert len(stored) == 1 and stored[0]["till_standing_count"] == 2000.0
        assert raised == []

    def test_the_noted_shortage_lands_its_reply_is_lost_and_it_is_alerted_once(self, db, monkeypatch):
        """D110 on sequence B: session 2's close (Rs 500 short, noted)
        commits and its reply is lost: 500, then 409 on the identical retry,
        which raises the one alert. Session 3 is then balanced, with no note
        and no second alert."""
        raised = _raised(monkeypatch)
        _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        cr = db.get_collection("cash_register_sessions")
        with monkeypatch.context() as m:
            fired = _lose_ack(m, cr, "update_one", _sets_status("CLOSED"))
            _fail_read_back(m, cr, fired)
            with pytest.raises(HTTPException) as exc:
                _register(db, m, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                          opening=3000.0, pieces=6, note="Rs 500 short")
            assert exc.value.status_code == 500 and fired == [True] and raised == []
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                      pieces=6, note="Rs 500 short", insert=False)
        assert exc.value.status_code == 409
        alert = [("raise_variance_task", till.followup_task_id("variance", "CR-R17-2"))]
        assert raised == alert
        _cash_sale(db, 500, f"{DATE}T13:30:00")
        third = _third(db, monkeypatch, opening=3000.0, pieces=7)
        assert _judged(third) == _balanced(3500.0) and third["manager_followup"]["tasks"] == []
        assert raised == alert


class TestR19D121LaterClosesAreNamedBesideTheTotals:
    """D88 stands: a later Cash Register close on an already counted drawer
    is never in the range totals. D121: the totals name those closes and
    carry their own differences as a separate figure, short and over apart,
    before and after the Z-Read locks."""

    def test_their_own_differences_are_a_separate_figure(self, db, monkeypatch):
        _alerts(monkeypatch)
        sid, _first = _one_drawer_counted_by_a_register(db, monkeypatch)
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                  opening=3000.0, pieces=6, note="Rs 500 short")
        _cash_sale(db, 500, f"{DATE}T13:30:00")
        third = _third(db, monkeypatch, opening=3000.0, pieces=8, note="Rs 500 over")
        assert (third["variance"], third["variance_status"]) == (500.0, "OVER")
        for locked in (False, True):
            if locked:
                assert till.lock_session(db, sid, actor=MANAGER)["ok"]
            res = _recon_summary(db, monkeypatch)
            t = res["totals"]
            assert (t["later_closes"], t["later_closes_short"], t["later_closes_over"]) == (
                2, 500.0, 500.0), locked
            # D88 unchanged: the drawer once, at its first count of the day.
            assert (t["counted_cash"], t["shortage"], t["overage"], t["variance"],
                    t["also_counted_on_till"]) == (3000.0, 0, 0, 0.0, 2), locked
            assert _recon_row(res, "CR-R17-2")["variance"] == -500.0  # the rows still show

    def test_control_a_range_with_no_later_close_has_no_such_figure(self, db, monkeypatch):
        _one_drawer_counted_by_a_register(db, monkeypatch)
        t = _recon_summary(db, monkeypatch)["totals"]
        assert (t["later_closes"], t["later_closes_short"], t["later_closes_over"]) == (0, 0.0, 0.0)


class TestR19D120ADrawerCountedThroughAnotherDoorWhileTheSessionIsOpen:
    """Builder probe X1 of head 1ae62d8d: the register opens at 04:00, the
    drawer is counted on the Blind EOD Tally at 10:00 (Rs 3,000, balanced),
    Rs 500 more is taken, the register closes with Rs 3,500 in hand: stored
    Rs 500 SHORT, a note demanded, the manager alerted."""

    @staticmethod
    def _counted_on_the_tally(db):
        sid = _open(db)
        _cash_sale(db, 3000)
        assert till.blind_submit(
            db, sid, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}],
            actor=CASHIER)["ok"]
        _counted_at(db, sid, "10:00:00")
        return sid

    def test_counted_on_the_blind_eod_tally_mid_session(self, db, monkeypatch):
        alerts = _alerts(monkeypatch)
        sid = self._counted_on_the_tally(db)
        assert _till(db, sid)["blind_count_origin"] == "TILL"
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        out = _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00", pieces=7)
        assert _judged(out) == _balanced(3500.0)
        assert (out["till_counted_earlier"], out["till_standing_count"], out["cash_sales"],
                out["counted_from_shared_record"]) == (True, 3000.0, 500.0, False)
        assert alerts == [] and out["manager_followup"]["tasks"] == []
        assert _till(db, sid)["blind_count_paisa"] == 300000

    def test_counted_through_day_end_mid_session(self, db, day_end, monkeypatch):
        alerts = _alerts(monkeypatch)
        sid = _open(db)
        _cash_sale(db, 3000)
        day_end(closing_cash=3000.0, system_cash=3000.0)
        assert (_till(db, sid)["blind_count_paisa"], _till(db, sid)["blind_count_origin"]) == (
            300000, "DAY_END")
        _counted_at(db, sid, "10:00:00")
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        out = _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00", pieces=7)
        assert _judged(out) == _balanced(3500.0) and alerts == []

    def test_no_sheet_of_its_own_stores_no_comparison(self, db, monkeypatch):
        alerts = _alerts(monkeypatch)
        self._counted_on_the_tally(db)
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        out = _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00")
        assert (out["variance"], out["variance_status"], out["counted"], out["cash_sales"]) == (
            None, "COUNTED_EARLIER", 3000.0, 500.0)
        assert alerts == [] and out["manager_followup"]["tasks"] == []

    def test_a_real_difference_on_its_own_sheet_needs_the_note_and_alerts(self, db, monkeypatch):
        alerts = _alerts(monkeypatch)
        self._counted_on_the_tally(db)
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00", pieces=6)
        assert exc.value.status_code == 400 and "variance_note_required" in exc.value.detail
        out = _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                        pieces=6, note="Rs 500 short", insert=False)
        assert _judged(out) == {
            "counted": 3000.0, "expected": 3500.0, "variance": -500.0, "variance_status": "SHORT"}
        assert [a["session_id"] for a in alerts] == ["CR-R19-1"]

    def test_the_mid_shift_count_was_short_and_noted_at_its_lock(self, db, monkeypatch):
        """The Blind EOD count mid-shift is Rs 500 short and the manager
        locks it with a note (the till's one alert). Rs 500 more is taken;
        the register closes with all of it in hand: balanced, no note, no
        second alert. The Rs 500 is on the till's row only."""
        raised = _raised(monkeypatch)
        sid = _open(db)
        _cash_sale(db, 3000)
        assert till.blind_submit(
            db, sid, blind_denominations=[{"face": 500, "kind": "note", "pieces": 5}],
            actor=CASHIER)["ok"]
        _counted_at(db, sid, "10:00:00")
        assert till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")["ok"]
        assert [r[0] for r in raised] == ["raise_variance_task"]
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        out = _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00", pieces=6)
        assert _judged(out) == _balanced(3000.0)
        assert (out["till_standing_count"], out["cash_sales"]) == (2500.0, 500.0)
        assert len(raised) == 1 and out["manager_followup"]["tasks"] == []
        res = _recon_summary(db, monkeypatch)
        t = res["totals"]
        assert (t["shortage"], t["shortage_amount"], t["later_closes"], t["later_closes_short"]) == (
            1, 500.0, 1, 0.0)

    def test_the_close_lands_and_its_reply_is_lost(self, db, monkeypatch):
        """D110: the close after a mid-shift count commits, its reply and
        the read-back are lost (500); the identical retry, twice, answers 409
        with the saved balanced close. One row, no task."""
        raised = _raised(monkeypatch)
        self._counted_on_the_tally(db)
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        cr = db.get_collection("cash_register_sessions")
        fired = _lose_ack(monkeypatch, cr, "update_one", _sets_status("CLOSED"))
        _fail_read_back(monkeypatch, cr, fired)
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00", pieces=7)
        assert exc.value.status_code == 500 and fired == [True]
        for _ in range(2):
            with pytest.raises(HTTPException) as exc:
                _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                          pieces=7, insert=False)
            code, body = _through_the_app_handler(exc.value)
            assert code == 409 and _judged(body["detail"]["session"]) == _balanced(3500.0)
        assert len([d for d in cr.docs if d["session_id"] == "CR-R19-1"]) == 1 and raised == []

    def test_the_register_loses_the_count_race_to_the_tally(self, db, monkeypatch):
        """Two counts at once: the Blind EOD Tally's count (Rs 2,500) lands
        between this close's read of the drawer and its own count. The
        till's count is the tally's; the close is judged on its own sheet
        (Rs 3,000) from it, Rs 500 over, with its note. It used to store the
        tally's count against the whole window: Rs 500 SHORT."""
        sid = _open(db)
        _cash_sale(db, 3000)
        real = till.blind_submit
        raced = []

        def _tally_first(db_, session_id, **kw):
            if not raced:
                raced.append(True)
                assert real(db_, session_id, blind_denominations=[
                    {"face": 500, "kind": "note", "pieces": 5}], actor=CASHIER)["ok"]
            return real(db_, session_id, **kw)

        monkeypatch.setattr(till, "blind_submit", _tally_first)
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00", pieces=6)
        assert exc.value.status_code == 400
        out = _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                        pieces=6, note="Rs 500 more than the tally's count", insert=False)
        assert (_till(db, sid)["blind_count_paisa"], _till(db, sid)["blind_count_origin"]) == (250000, "TILL")
        assert _judged(out) == {
            "counted": 3000.0, "expected": 2500.0, "variance": 500.0, "variance_status": "OVER"}
        assert (out["till_counted_earlier"], out["counted_from_shared_record"]) == (True, False)

    def test_a_first_count_refused_for_its_note_and_sent_again_unchanged(self, db, monkeypatch):
        """D120's guard. The close is the drawer's FIRST count, Rs 500 short,
        sent with no note: 400. Sent again unchanged: the same Rs 500 and
        still 400 (its own count on the till is not an independent count).
        Sent with the note: stored once, one alert; then 409."""
        raised = _raised(monkeypatch)
        sid = _open(db)
        _cash_sale(db, 3000)
        cr = db.get_collection("cash_register_sessions")
        for attempt in range(2):
            with pytest.raises(HTTPException) as exc:
                _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                          pieces=5, insert=attempt == 0)
            assert exc.value.status_code == 400 and "variance_note_required" in exc.value.detail
            assert cr.find_one({"session_id": "CR-R19-1"})["status"] == "OPEN"
            assert (_till(db, sid)["blind_count_paisa"], _till(db, sid)["blind_count_origin"]) == (
                250000, "CR-R19-1")
        assert raised == []
        out = _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                        pieces=5, note="Rs 500 short", insert=False)
        assert _judged(out) == {
            "counted": 2500.0, "expected": 3000.0, "variance": -500.0, "variance_status": "SHORT"}
        assert not out.get("till_counted_earlier")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                      pieces=5, note="Rs 500 short", insert=False)
        assert exc.value.status_code == 409
        assert raised == [("raise_variance_task", till.followup_task_id("variance", "CR-R19-1"))]
        assert len([d for d in cr.docs if d["session_id"] == "CR-R19-1"]) == 1

    def test_its_count_lands_on_the_till_and_that_reply_is_lost(self, db, monkeypatch):
        """D110 on the count write: the close's count commits on the till,
        its acknowledgement and the read-back are lost. The close is refused
        for its note all the same (Rs 500 short on its own window); sent
        again it finds its OWN count on the till and is still Rs 500 short;
        with the note it is stored once."""
        raised = _raised(monkeypatch)
        sid = _open(db)
        _cash_sale(db, 3000)
        drawers = db.get_collection("till_sessions")
        with monkeypatch.context() as m:
            fired = _lose_ack(m, drawers, "find_one_and_update", _sets_status("BLIND_SUBMITTED"))
            _fail_read_back(m, drawers, fired)
            with pytest.raises(HTTPException) as exc:
                _register(db, m, "CR-R19-1", opened="04:00:00", closed="12:00:00", pieces=5)
            assert exc.value.status_code == 400 and fired == [True]
        assert (_till(db, sid)["blind_count_paisa"], _till(db, sid)["blind_count_origin"]) == (
            250000, "CR-R19-1")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                      pieces=5, insert=False)
        assert exc.value.status_code == 400
        out = _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                        pieces=5, note="Rs 500 short", insert=False)
        assert _judged(out) == {
            "counted": 2500.0, "expected": 3000.0, "variance": -500.0, "variance_status": "SHORT"}
        assert not out.get("till_counted_earlier") and out["till_link_ok"] is True
        assert [r for r in raised if r[0] == "raise_variance_task"] == [
            ("raise_variance_task", till.followup_task_id("variance", "CR-R19-1"))]

    def test_control_this_sessions_own_refused_count_keeps_its_shortage(self, db, monkeypatch):
        """The close counts the drawer (Rs 500 short), is refused for its
        note and sent again: the till's count is THIS session's own, made
        while it was open. Judged from that count it would read balanced and
        the shortage would be gone. Same sheet or another, it stays short."""
        alerts = _alerts(monkeypatch)
        sid = _open(db)
        _cash_sale(db, 3000)
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00", pieces=5)
        assert exc.value.status_code == 400
        drawer = _till(db, sid)
        assert (drawer["blind_count_paisa"], drawer["blind_count_origin"]) == (250000, "CR-R19-1")
        assert drawer["blind_submitted_at"] > datetime.fromisoformat(f"{DATE}T04:00:00")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                      pieces=6, insert=False)
        assert exc.value.status_code == 400
        out = _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                        pieces=5, note="Rs 500 short", insert=False)
        assert _judged(out) == {
            "counted": 2500.0, "expected": 3000.0, "variance": -500.0, "variance_status": "SHORT"}
        assert not out.get("till_counted_earlier")
        assert [(a["session_id"], a["variance_paisa"]) for a in alerts] == [(sid, -50000)]

    def test_control_a_count_stored_by_the_previous_version_stands_as_before(self, db, monkeypatch):
        """A till count with no record of who made it (written before this
        version) cannot be told from the session's own: it is read as on
        main, so a real shortage is never turned into "balanced"."""
        sid = self._counted_on_the_tally(db)
        db.get_collection("till_sessions").update_one(
            {"_id": sid}, {"$set": {"blind_count_origin": None}})
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00", pieces=7)
        assert exc.value.status_code == 400
        out = _register(db, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                        pieces=7, note="till count stands", insert=False)
        assert (out["counted"], out["counted_from_shared_record"], out["variance_status"]) == (
            3000.0, True, "SHORT")
        assert not out.get("till_counted_earlier")


def test_r19_d120_later_sessions_and_a_mid_session_count_on_real_mongo(monkeypatch):
    """Native PyMongo with the real till indexes and TaskRepository. Day 1:
    sequences A and B in one run (a bank deposit on session 2, a noted
    shortage on session 3, session 4 balanced with no note and no task).
    Then, on a fresh drawer, a count through the Blind EOD Tally while the
    register is open, and the session's own refused-and-resent count."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        tasks = _real_task_repo(monkeypatch, real_db)
        ndb = _NativeDB(real_db)
        _cash_sale(real_db, 3000, datetime(2026, 11, 4, 5, 0))
        first = _register(ndb, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00", pieces=6)
        sid = first["till_session_id"]
        assert (first["variance_status"], first["till_link_ok"]) == ("BALANCED", True)
        _counted_at(real_db, sid, "12:00:00")

        _cash_sale(real_db, 1000, datetime(2026, 11, 4, 12, 45))
        second = _register(ndb, monkeypatch, "CR-R19-2", opened="12:30:00", closed="13:00:00",
                           opening=3000.0, pieces=4, bank=2000.0)
        assert _judged(second) == _balanced(2000.0)

        _cash_sale(real_db, 500, datetime(2026, 11, 4, 13, 30))
        with pytest.raises(HTTPException) as exc:
            _register(ndb, monkeypatch, "CR-R19-3", opened="13:10:00", closed="14:00:00",
                      opening=2000.0, pieces=4)
        assert exc.value.status_code == 400
        third = _register(ndb, monkeypatch, "CR-R19-3", opened="13:10:00", closed="14:00:00",
                          pieces=4, note="Rs 500 short", insert=False)
        assert _judged(third) == {
            "counted": 2000.0, "expected": 2500.0, "variance": -500.0, "variance_status": "SHORT"}
        assert (third["till_standing_count"], third["cash_sales"]) == (2000.0, 500.0)
        assert tasks.count_documents({"source_ref": f"till_variance:{STORE}:{DATE}:CR-R19-3"}) == 1

        _cash_sale(real_db, 500, datetime(2026, 11, 4, 14, 30))
        fourth = _register(ndb, monkeypatch, "CR-R19-4", opened="14:10:00", closed="15:00:00",
                           opening=2000.0, pieces=5)
        assert _judged(fourth) == _balanced(2500.0)
        assert (fourth["till_standing_count"], fourth["cash_sales"]) == (2000.0, 500.0)
        stored = real_db.cash_register_sessions.find_one({"session_id": "CR-R19-4"})
        assert (stored["expected"], stored["variance"], stored["manager_followup"]["tasks"]) == (
            2500.0, 0.0, [])
        assert tasks.count_documents({}) == 1
        drawer_row = real_db.till_sessions.find_one({"_id": sid})
        assert (drawer_row["blind_count_paisa"], drawer_row["blind_count_origin"]) == (300000, "CR-R19-1")
        t = _recon_summary(ndb, monkeypatch)["totals"]
        assert (t["counted_cash"], t["cash_sales"], t["shortage"]) == (3000.0, 3000.0, 0)
        assert (t["later_closes"], t["later_closes_short"], t["later_closes_over"]) == (3, 500.0, 0.0)

        # A fresh drawer: the day's rows are cleared, the task store is kept.
        for coll in ("till_sessions", "cash_register_sessions", "orders"):
            real_db[coll].delete_many({})
        _cash_sale(real_db, 3000, datetime(2026, 11, 4, 5, 0))
        drawer = _open(real_db)
        assert till.blind_submit(
            real_db, drawer, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}],
            actor=CASHIER)["ok"]
        _counted_at(real_db, drawer, "10:00:00")
        _cash_sale(real_db, 500, datetime(2026, 11, 4, 11, 0))
        mid = _register(ndb, monkeypatch, "CR-R19-5", opened="04:00:00", closed="12:00:00", pieces=7)
        assert _judged(mid) == _balanced(3500.0)
        assert (mid["till_counted_earlier"], mid["till_standing_count"]) == (True, 3000.0)
        assert tasks.count_documents({}) == 1

        # Its own count, refused for the note and sent again: still short.
        for coll in ("till_sessions", "cash_register_sessions", "orders"):
            real_db[coll].delete_many({})
        _cash_sale(real_db, 3000, datetime(2026, 11, 4, 5, 0))
        with pytest.raises(HTTPException) as exc:
            _register(ndb, monkeypatch, "CR-R19-6", opened="04:00:00", closed="12:00:00", pieces=5)
        assert exc.value.status_code == 400
        own = _register(ndb, monkeypatch, "CR-R19-6", opened="04:00:00", closed="12:00:00",
                        pieces=5, note="Rs 500 short", insert=False)
        assert _judged(own) == {
            "counted": 2500.0, "expected": 3000.0, "variance": -500.0, "variance_status": "SHORT"}
        assert not own.get("till_counted_earlier")
        assert tasks.count_documents({}) == 2
    finally:
        client.drop_database(name)
        client.close()


def _native_fail_read_back(monkeypatch, name, collection, fired):
    """Native: the one find_one on ``collection`` straight after the lost
    acknowledgement fails too. Class-level, scoped to our database."""
    from pymongo.collection import Collection

    real = Collection.find_one
    failed = []

    def _find_one(self, *a, **k):
        if self.database.name == name and self.name == collection and fired and not failed:
            failed.append(True)
            raise AutoReconnect("native read-back fault")
        return real(self, *a, **k)

    monkeypatch.setattr(Collection, "find_one", _find_one)


def test_r19_d120_lost_replies_and_a_deposit_with_no_sheet_on_real_mongo(monkeypatch):
    """Native PyMongo with the real till indexes and TaskRepository (D110 on
    the D120 closes). (1) A close after a mid-shift count: its CLOSED write
    commits, the reply and the read-back are lost; the identical retry twice.
    (2) On the same drawer, a close with a bank deposit and no sheet, then a
    counted close. (3) A fresh drawer: the close's COUNT write on the till
    commits and its reply is lost; the same close again; then with the note,
    whose CLOSED write also loses its reply."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        tasks = _real_task_repo(monkeypatch, real_db)
        ndb = _NativeDB(real_db)
        registers = real_db.cash_register_sessions

        _cash_sale(real_db, 3000, datetime(2026, 11, 4, 5, 0))
        drawer = _open(real_db)
        assert till.blind_submit(
            real_db, drawer, blind_denominations=[{"face": 500, "kind": "note", "pieces": 6}],
            actor=CASHIER)["ok"]
        _counted_at(real_db, drawer, "10:00:00")
        _cash_sale(real_db, 500, datetime(2026, 11, 4, 11, 0))
        with monkeypatch.context() as m:
            fired = _native_lose_ack(m, name, "cash_register_sessions", "update_one", _sets_status("CLOSED"))
            _native_fail_read_back(m, name, "cash_register_sessions", fired)
            with pytest.raises(HTTPException) as exc:
                _register(ndb, m, "CR-R19-1", opened="04:00:00", closed="12:00:00", pieces=7)
            assert exc.value.status_code == 500 and fired == [True]
        for _ in range(2):
            with pytest.raises(HTTPException) as exc:
                _register(ndb, monkeypatch, "CR-R19-1", opened="04:00:00", closed="12:00:00",
                          pieces=7, insert=False)
            code, body = _through_the_app_handler(exc.value)
            assert code == 409 and _judged(body["detail"]["session"]) == _balanced(3500.0)
        stored = registers.find_one({"session_id": "CR-R19-1"})
        assert (stored["status"], stored["till_standing_count"], stored["cash_sales"],
                stored["manager_followup"]["pending"]) == ("CLOSED", 3000.0, 500.0, False)
        assert registers.count_documents({}) == 1 and tasks.count_documents({}) == 0
        assert real_db.till_sessions.find_one({"_id": drawer})["blind_count_paisa"] == 300000

        _cash_sale(real_db, 1000, datetime(2026, 11, 4, 12, 45))
        second = _register(ndb, monkeypatch, "CR-R19-2", opened="12:30:00", closed="13:00:00",
                           opening=3500.0, bank=2000.0)
        assert (second["variance_status"], second["variance"]) == ("COUNTED_EARLIER", None)
        _cash_sale(real_db, 500, datetime(2026, 11, 4, 13, 30))
        third = _register(ndb, monkeypatch, "CR-R19-3", opened="13:10:00", closed="14:00:00",
                          opening=2500.0, pieces=6)
        assert _judged(third) == _balanced(3000.0)
        assert (third["till_standing_count"], third["till_banked_since_count"], third["cash_sales"]) == (
            1500.0, 2000.0, 1500.0)
        assert tasks.count_documents({}) == 0

        for coll in ("till_sessions", "cash_register_sessions", "orders"):
            real_db[coll].delete_many({})
        _cash_sale(real_db, 3000, datetime(2026, 11, 4, 5, 0))
        with monkeypatch.context() as m:
            fired = _native_lose_ack(m, name, "till_sessions", "find_one_and_update",
                                     _sets_status("BLIND_SUBMITTED"))
            _native_fail_read_back(m, name, "till_sessions", fired)
            with pytest.raises(HTTPException) as exc:
                _register(ndb, m, "CR-R19-4", opened="04:00:00", closed="12:00:00", pieces=5)
            assert exc.value.status_code == 400 and fired == [True]
        counted = real_db.till_sessions.find_one({})
        assert (counted["blind_count_paisa"], counted["blind_count_origin"]) == (250000, "CR-R19-4")
        with pytest.raises(HTTPException) as exc:
            _register(ndb, monkeypatch, "CR-R19-4", opened="04:00:00", closed="12:00:00",
                      pieces=5, insert=False)
        assert exc.value.status_code == 400 and registers.find_one({})["status"] == "OPEN"
        with monkeypatch.context() as m:
            fired = _native_lose_ack(m, name, "cash_register_sessions", "update_one", _sets_status("CLOSED"))
            _native_fail_read_back(m, name, "cash_register_sessions", fired)
            with pytest.raises(HTTPException) as exc:
                _register(ndb, m, "CR-R19-4", opened="04:00:00", closed="12:00:00",
                          pieces=5, note="Rs 500 short", insert=False)
            assert exc.value.status_code == 500 and fired == [True] and tasks.count_documents({}) == 0
        with pytest.raises(HTTPException) as exc:
            _register(ndb, monkeypatch, "CR-R19-4", opened="04:00:00", closed="12:00:00",
                      pieces=5, note="Rs 500 short", insert=False)
        assert exc.value.status_code == 409
        stored = registers.find_one({"session_id": "CR-R19-4"})
        assert _judged(stored) == {
            "counted": 2500.0, "expected": 3000.0, "variance": -500.0, "variance_status": "SHORT"}
        assert not stored.get("till_counted_earlier") and stored["manager_followup"]["pending"] is False
        assert registers.count_documents({}) == 1
        assert [t["_id"] for t in tasks.find({})] == [till.followup_task_id("variance", "CR-R19-4")]
    finally:
        client.drop_database(name)
        client.close()


# ---------------------------------------------------------------------------
# Round 20 (head 7b386834): review must-fix. A drawer counted through another
# door (Blind EOD Tally, Day-End), short beyond the band and NOT locked: the
# Cash Register close that starts from that count (D120) read balanced, asked
# no note and alerted nobody (owner ruling 2026-08-25: note + manager alert).
# X3: a close's own refused count against a longer window after a NEW sheet.
# D121: a later close is never in the range totals, also before the lock.
# ---------------------------------------------------------------------------


def _short_tally_count(db, *, denominations=None, at="10:00:00"):
    """Drawer opened, Rs 3,000 cash taken, counted on the Blind EOD Tally at
    Rs 2,500 (Rs 500 short) and NOT locked."""
    sid = _open(db)
    _cash_sale(db, 3000)
    assert till.blind_submit(
        db, sid,
        blind_denominations=denominations or [{"face": 500, "kind": "note", "pieces": 5}],
        actor=CASHIER)["ok"]
    _counted_at(db, sid, at)
    return sid


def _owed(out):
    return [(t["session_id"], t["variance_paisa"]) for t in out["manager_followup"]["tasks"]
            if t["kind"] == "variance"]


def _refused_for_the_note(exc):
    return exc.value.status_code == 400 and "variance_note_required" in exc.value.detail


class TestR20AShortCountNobodyLockedIsExplainedAtTheRegisterClose:
    """The reviewer's steps: the register opens, Rs 3,000 cash, the cashier
    counts Rs 2,500 on the Blind EOD Tally, nobody locks the Z-Read, the
    register closes with the cash in hand and no note: 200, balanced, no
    task anywhere."""

    def test_blind_eod_count_short_and_unlocked_mid_session(self, db, monkeypatch):
        alerts = _alerts(monkeypatch)
        sid = _short_tally_count(db)
        drawer = _till(db, sid)
        assert (drawer["variance_paisa"], drawer["status"], drawer["blind_count_origin"]) == (
            -50000, till.STATUS_BLIND_SUBMITTED, "TILL")
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00", pieces=6)
        assert _refused_for_the_note(exc) and "earlier count" in exc.value.detail
        cr = db.get_collection("cash_register_sessions")
        assert cr.find_one({"session_id": "CR-R20-1"})["status"] == "OPEN" and alerts == []

        out = _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                        pieces=6, note="Rs 500 short at the tally count", insert=False)
        # Its own figures stay as D120 computes them.
        assert _judged(out) == _balanced(3000.0)
        assert (out["till_standing_count"], out["cash_sales"], out["closing_note"]) == (
            2500.0, 500.0, "Rs 500 short at the tally count")
        assert out["till_count_variance"] == -500.0
        # ONE alert, under the DRAWER's key and day (the Z-Read lock's key).
        assert [(a["session_id"], a["variance_paisa"], a["session_date"], a["note"]) for a in alerts] == [
            (sid, -50000, DATE, "Rs 500 short at the tally count")]
        assert _owed(out) == [(sid, -50000)]
        # The till's count and its own shortage are not replaced (L3-12).
        assert (_till(db, sid)["blind_count_paisa"], _till(db, sid)["variance_paisa"]) == (250000, -50000)

        # The Z-Read lock still takes its own note and raises under the SAME
        # key (one task: the native test counts the stored rows).
        assert till.lock_session(db, sid, actor=MANAGER)["error"] == "variance_note_required"
        assert till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")["ok"]
        assert [a["session_id"] for a in alerts] == [sid, sid]

    def test_day_end_count_short_and_unlocked(self, db, day_end, monkeypatch):
        alerts = _alerts(monkeypatch)
        sid = _open(db)
        _cash_sale(db, 3000)
        day_end(closing_cash=2500.0, system_cash=3000.0)
        drawer = _till(db, sid)
        assert (drawer["variance_paisa"], drawer["status"], drawer["blind_count_origin"]) == (
            -50000, till.STATUS_BLIND_SUBMITTED, "DAY_END")
        assert alerts == []  # Day-End takes no note and raises no alert
        _counted_at(db, sid, "10:00:00")
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00", pieces=6)
        assert _refused_for_the_note(exc)
        out = _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                        pieces=6, note="Rs 500 short at Day-End", insert=False)
        assert _judged(out) == _balanced(3000.0) and out["till_count_variance"] == -500.0
        assert [(a["session_id"], a["variance_paisa"]) for a in alerts] == [(sid, -50000)]

    def test_the_short_count_stood_before_the_session_opened(self, db, monkeypatch):
        alerts = _alerts(monkeypatch)
        sid = _short_tally_count(db, at="06:00:00")
        _cash_sale(db, 500, f"{DATE}T07:00:00")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R20-1", opened="06:30:00", closed="08:00:00",
                      opening=2500.0, pieces=6)
        assert _refused_for_the_note(exc)
        out = _register(db, monkeypatch, "CR-R20-1", opened="06:30:00", closed="08:00:00",
                        pieces=6, note="Rs 500 short at the tally count", insert=False)
        assert _judged(out) == _balanced(3000.0)
        assert [(a["session_id"], a["variance_paisa"]) for a in alerts] == [(sid, -50000)]

    def test_a_close_with_no_sheet_of_its_own_takes_the_note_too(self, db, monkeypatch):
        """On main a close with no count adopted the till's and was refused
        for the note. Nothing is compared for it (D111), but the drawer's
        unexplained count is still asked for and alerted."""
        alerts = _alerts(monkeypatch)
        sid = _short_tally_count(db)
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00")
        assert _refused_for_the_note(exc)
        out = _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                        note="Rs 500 short at the tally count", insert=False)
        assert (out["variance"], out["variance_status"], out["till_count_variance"]) == (
            None, "COUNTED_EARLIER", -500.0)
        assert [(a["session_id"], a["variance_paisa"]) for a in alerts] == [(sid, -50000)]

    @pytest.mark.parametrize("first_pieces", [6, None])
    def test_it_is_asked_once_not_on_the_next_sessions(self, db, monkeypatch, first_pieces):
        """D120: a loss already explained is not demanded a second time. The
        first close (with a sheet, or with none) took the note; the next
        sessions on the same unlocked drawer are not asked and not alerted."""
        alerts = _alerts(monkeypatch)
        sid = _short_tally_count(db)
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        first = _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                          pieces=first_pieces, note="Rs 500 short at the tally count")
        assert first["till_count_variance"] == -500.0 and len(alerts) == 1
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        second = _register(db, monkeypatch, "CR-R20-2", opened="12:30:00", closed="13:00:00",
                           opening=3000.0, pieces=7)
        assert _judged(second) == _balanced(3500.0)
        assert (second["till_count_variance"], second["manager_followup"]["tasks"]) == (None, [])
        third = _register(db, monkeypatch, "CR-R20-3", opened="13:10:00", closed="14:00:00",
                          opening=3500.0)
        assert (third["variance_status"], third["till_count_variance"]) == ("COUNTED_EARLIER", None)
        assert [a["session_id"] for a in alerts] == [sid]

    def test_the_close_lands_and_its_reply_is_lost(self, db, monkeypatch):
        """D110: the close with the note commits, its reply and the read-back
        are lost (500); the identical retry, twice, answers 409 with the
        saved close and raises the drawer's alert once."""
        raised = _raised(monkeypatch)
        _short_tally_count(db)
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        cr = db.get_collection("cash_register_sessions")
        fired = _lose_ack(monkeypatch, cr, "update_one", _sets_status("CLOSED"))
        _fail_read_back(monkeypatch, cr, fired)
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                      pieces=6, note="Rs 500 short at the tally count")
        assert exc.value.status_code == 500 and fired == [True] and raised == []
        for _ in range(2):
            with pytest.raises(HTTPException) as exc:
                _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                          pieces=6, note="Rs 500 short at the tally count", insert=False)
            code, body = _through_the_app_handler(exc.value)
            assert code == 409 and body["detail"]["session"]["till_count_variance"] == -500.0
        assert raised == [
            ("raise_variance_task", till.followup_task_id("variance", "CR-R20-1", "count"))]
        stored = cr.find_one({"session_id": "CR-R20-1"})
        assert stored["manager_followup"]["pending"] is False
        assert len([d for d in cr.docs if d["session_id"] == "CR-R20-1"]) == 1

    def test_the_alert_stays_owed_while_the_task_store_is_down(self, db, monkeypatch):
        up = []
        raised = _raised(monkeypatch, store_up=lambda: bool(up))
        _short_tally_count(db)
        out = _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                        pieces=5, note="Rs 500 short at the tally count")
        assert out["manager_followup"]["pending"] is True and raised == []
        up.append(True)
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                      pieces=5, note="Rs 500 short at the tally count", insert=False)
        assert exc.value.status_code == 409
        assert raised == [
            ("raise_variance_task", till.followup_task_id("variance", "CR-R20-1", "count"))]

    def test_its_own_difference_and_the_drawers_are_two_alerts(self, db, monkeypatch):
        """The tally count is Rs 500 short and unlocked AND the register's
        own sheet is Rs 500 short of that count plus the cash since: one
        note, the drawer's alert under the drawer's key and the sheet's
        under the session's."""
        alerts = _alerts(monkeypatch)
        sid = _short_tally_count(db)
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        out = _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                        pieces=5, note="Rs 500 short twice")
        assert _judged(out) == {
            "counted": 2500.0, "expected": 3000.0, "variance": -500.0, "variance_status": "SHORT"}
        assert sorted((a["session_id"], a["variance_paisa"]) for a in alerts) == sorted([
            ("CR-R20-1", -50000), (sid, -50000)])

    # --- controls: nothing is asked where the count is already explained ---

    def test_control_a_count_inside_the_band_asks_nothing(self, db, monkeypatch):
        alerts = _alerts(monkeypatch)
        monkeypatch.setattr(till, "get_variance_tolerance_paisa", lambda **_kw: 10000)
        notes = [{"face": 500, "kind": "note", "pieces": 5}, {"face": 100, "kind": "note", "pieces": 4}]
        sid = _short_tally_count(db, denominations=notes)
        assert _till(db, sid)["variance_paisa"] == -10000  # Rs 100 short: on the band, not beyond
        db.get_collection("cash_register_sessions").insert_one({
            "_id": "CR-R20-1", "session_id": "CR-R20-1", "store_id": STORE, "status": "OPEN",
            "opening_float": 0.0, "opening_denominations": [], "opened_at": f"{DATE}T04:00:00"})
        out = _register_with(db, monkeypatch, "CR-R20-1", closed="12:00:00", denominations=notes)
        assert _judged(out) == _balanced(2900.0)
        assert (out["till_count_variance"], alerts) == (None, [])

    def test_control_a_count_locked_with_its_note_asks_nothing(self, db, monkeypatch):
        raised = _raised(monkeypatch)
        sid = _short_tally_count(db)
        assert till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")["ok"]
        assert [r[0] for r in raised] == ["raise_variance_task"]
        out = _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00", pieces=5)
        assert _judged(out) == _balanced(2500.0)
        assert (out["till_count_variance"], out["manager_followup"]["tasks"], len(raised)) == (None, [], 1)

    def test_control_a_count_made_and_noted_by_a_register_close_asks_nothing(self, db, monkeypatch):
        """The drawer's first count was a register close, Rs 500 short,
        noted and alerted there. The next session balances: no note, no
        second alert (D120)."""
        alerts = _alerts(monkeypatch)
        sid, first = _one_drawer_counted_by_a_register(
            db, monkeypatch, pieces=5, note="Rs 500 short at the first count")
        assert (first["variance"], _till(db, sid)["variance_paisa"]) == (-500.0, -50000)
        assert _till(db, sid)["blind_count_origin"] == "CR-R17-1"
        _cash_sale(db, 500, f"{DATE}T12:45:00")
        second = _register(db, monkeypatch, "CR-R17-2", opened="12:30:00", closed="13:00:00",
                           opening=2500.0, pieces=6)
        assert _judged(second) == _balanced(3000.0)
        assert (second["till_count_variance"], second["manager_followup"]["tasks"]) == (None, [])
        assert [(a["session_id"], a["variance_paisa"]) for a in alerts] == [(sid, -50000)]

    def test_a_count_with_no_recorded_door_standing_at_opening_is_asked(self, db, monkeypatch):
        """Stored by the previous version (no ``blind_count_origin``): it
        cannot be told who took its note, so it is read as awaiting it."""
        alerts = _alerts(monkeypatch)
        sid = _short_tally_count(db, at="06:00:00")
        db.get_collection("till_sessions").update_one(
            {"_id": sid}, {"$set": {"blind_count_origin": None}})
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R20-1", opened="06:30:00", closed="08:00:00",
                      opening=2500.0, pieces=5)
        assert _refused_for_the_note(exc) and alerts == []


def _register_with(db, monkeypatch, sid, *, closed, denominations, **body_kw):
    """A register session (already inserted) closed with an explicit body."""
    monkeypatch.setattr(finance_router, "_get_db", lambda: db)
    monkeypatch.setattr(finance_router, "_iso_now", lambda: f"{DATE}T{closed}")
    body = finance_router.CashRegisterClose(
        session_id=sid, denominations=denominations, closing_count_state="COUNTED", **body_kw)
    return asyncio.run(finance_router.close_cash_register(body=body, current_user=MANAGER))


class TestR20ANewSheetAfterARefusedOrFailedClose:
    """X3 (D120: "Counted = this close's own sheet"). A close that is the
    drawer's first count leaves that count on the till when it is refused for
    its note or its closing write fails. Sent again with ANOTHER sheet, the
    first count was adopted and compared with the longer window."""

    def test_cash_taken_after_the_refusal_then_a_new_sheet(self, db, monkeypatch):
        """Rs 2,500 of Rs 3,000 counted, refused; Rs 500 more is taken; the
        drawer is recounted at Rs 3,000. It read Rs 1,000 short; it is the
        same Rs 500."""
        alerts = _alerts(monkeypatch)
        sid = _open(db)
        _cash_sale(db, 3000)
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00", pieces=5)
        assert _refused_for_the_note(exc)
        _cash_sale(db, 500, f"{DATE}T12:10:00")
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:30:00",
                      pieces=6, insert=False)
        assert _refused_for_the_note(exc)
        out = _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:30:00",
                        pieces=6, note="Rs 500 short", insert=False)
        assert _judged(out) == {
            "counted": 3000.0, "expected": 3500.0, "variance": -500.0, "variance_status": "SHORT"}
        assert (out["counted_from_shared_record"], out["till_count_differs"],
                out.get("till_counted_earlier")) == (False, True, False)
        # The till's first count is not replaced (L3-12); one alert, Rs 500.
        assert (_till(db, sid)["blind_count_paisa"], _till(db, sid)["blind_count_origin"]) == (
            250000, "CR-R20-1")
        assert [(a["session_id"], a["variance_paisa"]) for a in alerts] == [(sid, -50000)]
        assert _owed(out) == [(sid, -50000)]

    def test_a_sheet_retyped_to_balance_does_not_escape_the_note(self, db, monkeypatch):
        """Refused for a Rs 500 shortage, the same close is sent with a sheet
        that balances. Its first count stands on the till, short and
        unexplained: still refused; with the note the drawer's Rs 500 is
        alerted."""
        alerts = _alerts(monkeypatch)
        sid = _open(db)
        _cash_sale(db, 3000)
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00", pieces=5)
        assert _refused_for_the_note(exc)
        with pytest.raises(HTTPException) as exc:
            _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                      pieces=6, insert=False)
        assert _refused_for_the_note(exc) and "earlier count" in exc.value.detail
        out = _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                        pieces=6, note="Rs 500 found after the first count", insert=False)
        assert _judged(out) == _balanced(3000.0)
        assert (out["till_count_variance"], out["till_count_differs"]) == (-500.0, True)
        assert _till(db, sid)["blind_count_paisa"] == 250000
        assert [(a["session_id"], a["variance_paisa"]) for a in alerts] == [(sid, -50000)]

    def test_a_balanced_close_whose_write_failed_then_an_honest_recount(self, db, monkeypatch):
        """The close balanced, its count landed on the till, its closing
        write truly failed (500). Rs 500 more is taken and the drawer is
        recounted: it was refused for a Rs 500 shortage that does not exist."""
        alerts = _alerts(monkeypatch)
        sid = _open(db)
        _cash_sale(db, 3000)
        cr = db.get_collection("cash_register_sessions")
        with monkeypatch.context() as m:
            real, failed = cr.update_one, []

            def _fails(*a, **k):
                if not failed and _sets_status("CLOSED")(*a, **k):
                    failed.append(True)
                    raise AutoReconnect("nothing was written")
                return real(*a, **k)

            m.setattr(cr, "update_one", _fails)
            with pytest.raises(HTTPException) as exc:
                _register(db, m, "CR-R20-1", opened="04:00:00", closed="12:00:00", pieces=6)
            assert exc.value.status_code == 500 and failed == [True]
        assert cr.find_one({"session_id": "CR-R20-1"})["status"] == "OPEN"
        assert (_till(db, sid)["blind_count_paisa"], _till(db, sid)["variance_paisa"]) == (300000, 0)
        _cash_sale(db, 500, f"{DATE}T12:10:00")
        out = _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:30:00",
                        pieces=7, insert=False)
        assert _judged(out) == _balanced(3500.0)
        assert (out["till_count_variance"], out["till_count_differs"], alerts) == (None, True, [])

    def test_control_the_same_sheet_sent_again_is_judged_as_before(self, db, monkeypatch):
        """An override that disagrees with its own notes: the notes are the
        count on the till. The identical retry is the same sheet, so it is
        judged on the notes both times."""
        sid = _open(db)
        _cash_sale(db, 3000)
        db.get_collection("cash_register_sessions").insert_one({
            "_id": "CR-R20-1", "session_id": "CR-R20-1", "store_id": STORE, "status": "OPEN",
            "opening_float": 0.0, "opening_denominations": [], "opened_at": f"{DATE}T04:00:00"})
        five = [{"face": 500, "kind": "note", "pieces": 5}]
        for _ in range(2):
            with pytest.raises(HTTPException) as exc:
                _register_with(db, monkeypatch, "CR-R20-1", closed="12:00:00",
                               denominations=five, counted_override=3000.0)
            assert _refused_for_the_note(exc)
        out = _register_with(db, monkeypatch, "CR-R20-1", closed="12:00:00",
                             denominations=five, counted_override=3000.0, note="Rs 500 short")
        assert _judged(out) == {
            "counted": 2500.0, "expected": 3000.0, "variance": -500.0, "variance_status": "SHORT"}
        assert (out["counted_from_shared_record"], _till(db, sid)["blind_count_paisa"]) == (True, 250000)

    def test_control_several_drawers_on_the_day_compare_nothing_as_before(self, db, monkeypatch):
        """D53 stands: on a several-drawer day the close is judged on the
        drawer's own till variance and stores the drawer's count; a new
        sheet changes neither."""
        _alerts(monkeypatch)
        _a, b = _drawer_a_then_b(db, a_sales=[1000], b_sales=[3000])
        with pytest.raises(HTTPException) as exc:
            _cr_close_b(db, monkeypatch, 5)
        assert _refused_for_the_note(exc)
        six = [{"face": 500, "kind": "note", "pieces": 6}]
        with pytest.raises(HTTPException) as exc:
            _register_with(db, monkeypatch, "CR-D40", closed="12:00:00", denominations=six)
        assert _refused_for_the_note(exc)
        out = _register_with(db, monkeypatch, "CR-D40", closed="12:00:00", denominations=six,
                             note="B Rs 500 short")
        assert (out["variance_status"], out["counted"], out["counted_from_shared_record"],
                out["till_session_id"]) == ("SEVERAL_DRAWERS", 2500.0, True, b)


class TestR20D121ALaterCloseIsNeverInTheTotals:
    def test_before_the_lock_and_after_it(self, db, monkeypatch):
        """D121. The drawer's count was made on the Blind EOD Tally (Rs 500
        short) and is not locked: the register close on it was the drawer's
        only row and WAS in the totals (balanced 1, shortage 0), then left
        them at the lock. It stays out; the totals read low until the lock
        and then show the drawer at its first count."""
        _alerts(monkeypatch)
        sid = _short_tally_count(db)
        _cash_sale(db, 500, f"{DATE}T11:00:00")
        _register(db, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                  pieces=6, note="Rs 500 short at the tally count")
        res = _recon_summary(db, monkeypatch)
        t = res["totals"]
        assert (t["sessions"], t["counted_cash"], t["cash_sales"], t["balanced"], t["shortage"]) == (
            0, 0, 0, 0, 0)
        assert (t["also_counted_on_till"], t["later_closes"], t["later_closes_short"]) == (1, 1, 0.0)
        row = _recon_row(res, "CR-R20-1")
        assert (row["also_counted_on_till"], row["counted_cash"], row["variance_status"]) == (
            True, 3000.0, "BALANCED")

        assert till.lock_session(db, sid, actor=MANAGER, variance_note="Rs 500 short")["ok"]
        t = _recon_summary(db, monkeypatch)["totals"]
        assert (t["sessions"], t["counted_cash"], t["cash_sales"], t["shortage"], t["shortage_amount"]) == (
            1, 2500.0, 3000.0, 1, 500.0)
        assert (t["also_counted_on_till"], t["later_closes"]) == (1, 1)


def test_r20_an_unlocked_short_count_and_a_new_sheet_on_real_mongo(monkeypatch):
    """Native PyMongo with the real till indexes and TaskRepository.
    (1) A Blind EOD count Rs 500 short and unlocked while the register is
    open: no note 400; with the note stored, exactly one task keyed to the
    drawer; the next session is not asked; locking the Z-Read adds no second
    task. (2) The same close with its reply and read-back lost, then the
    identical retry twice (D110). (3) Day-End's short count. (4) A first
    count refused for its note, Rs 500 more cash, a new sheet."""
    client, real_db, name = _real_till_db()
    try:
        till.ensure_till_indexes(real_db)
        tasks = _real_task_repo(monkeypatch, real_db)
        ndb = _NativeDB(real_db)
        registers = real_db.cash_register_sessions
        day_rows = ("till_sessions", "cash_register_sessions", "orders", "tasks", "day_end_closes")

        def _fresh_short_count():
            for coll in day_rows:
                real_db[coll].delete_many({})
            _cash_sale(real_db, 3000, datetime(2026, 11, 4, 5, 0))
            drawer = _open(real_db)
            assert till.blind_submit(
                real_db, drawer, blind_denominations=[{"face": 500, "kind": "note", "pieces": 5}],
                actor=CASHIER)["ok"]
            _counted_at(real_db, drawer, "10:00:00")
            _cash_sale(real_db, 500, datetime(2026, 11, 4, 11, 0))
            return drawer

        # (1)
        drawer = _fresh_short_count()
        key = f"till_variance:{STORE}:{DATE}:{drawer}"
        with pytest.raises(HTTPException) as exc:
            _register(ndb, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00", pieces=6)
        assert _refused_for_the_note(exc)
        assert registers.find_one({"session_id": "CR-R20-1"})["status"] == "OPEN"
        assert tasks.count_documents({}) == 0
        out = _register(ndb, monkeypatch, "CR-R20-1", opened="04:00:00", closed="12:00:00",
                        pieces=6, note="Rs 500 short at the tally count", insert=False)
        assert _judged(out) == _balanced(3000.0)
        stored = registers.find_one({"session_id": "CR-R20-1"})
        assert (stored["till_count_variance"], stored["closing_note"], stored["variance"],
                stored["manager_followup"]["pending"]) == (
            -500.0, "Rs 500 short at the tally count", 0.0, False)
        assert [(t["source_ref"], t["payload"]["variance_paisa"]) for t in tasks.find({})] == [(key, -50000)]
        _cash_sale(real_db, 500, datetime(2026, 11, 4, 12, 45))
        second = _register(ndb, monkeypatch, "CR-R20-2", opened="12:30:00", closed="13:00:00",
                           opening=3000.0, pieces=7)
        assert _judged(second) == _balanced(3500.0) and second["till_count_variance"] is None
        assert till.lock_session(real_db, drawer, actor=MANAGER)["error"] == "variance_note_required"
        assert till.lock_session(real_db, drawer, actor=MANAGER, variance_note="Rs 500 short")["ok"]
        assert tasks.count_documents({}) == 1
        row = real_db.till_sessions.find_one({"_id": drawer})
        assert (row["blind_count_paisa"], row["variance_paisa"], row["status"]) == (
            250000, -50000, till.STATUS_LOCKED)

        # (2)
        drawer = _fresh_short_count()
        with monkeypatch.context() as m:
            fired = _native_lose_ack(m, name, "cash_register_sessions", "update_one", _sets_status("CLOSED"))
            _native_fail_read_back(m, name, "cash_register_sessions", fired)
            with pytest.raises(HTTPException) as exc:
                _register(ndb, m, "CR-R20-3", opened="04:00:00", closed="12:00:00",
                          pieces=6, note="Rs 500 short at the tally count")
            assert exc.value.status_code == 500 and fired == [True] and tasks.count_documents({}) == 0
        for _ in range(2):
            with pytest.raises(HTTPException) as exc:
                _register(ndb, monkeypatch, "CR-R20-3", opened="04:00:00", closed="12:00:00",
                          pieces=6, note="Rs 500 short at the tally count", insert=False)
            code, body = _through_the_app_handler(exc.value)
            assert code == 409 and body["detail"]["session"]["till_count_variance"] == -500.0
        assert registers.count_documents({}) == 1
        assert registers.find_one({})["manager_followup"]["pending"] is False
        assert [(t["_id"], t["source_ref"]) for t in tasks.find({})] == [
            (till.followup_task_id("variance", "CR-R20-3", "count"),
             f"till_variance:{STORE}:{DATE}:{drawer}")]

        # (3)
        for coll in day_rows:
            real_db[coll].delete_many({})
        _cash_sale(real_db, 3000, datetime(2026, 11, 4, 5, 0))
        drawer = _open(real_db)
        monkeypatch.setattr(reports_router, "get_db", lambda: ndb)
        monkeypatch.setattr("api.services.stores_util.is_online_store", lambda _db, _sid: False)
        asyncio.run(reports_router.create_day_end_close(
            reports_router.DayEndCloseBody(date=DATE, store_id=STORE, closing_cash=2500.0, system_cash=3000.0),
            current_user=MANAGER))
        row = real_db.till_sessions.find_one({"_id": drawer})
        assert (row["variance_paisa"], row["blind_count_origin"], row["status"]) == (
            -50000, "DAY_END", till.STATUS_BLIND_SUBMITTED)
        assert tasks.count_documents({}) == 0
        _counted_at(real_db, drawer, "10:00:00")
        _cash_sale(real_db, 500, datetime(2026, 11, 4, 11, 0))
        with pytest.raises(HTTPException) as exc:
            _register(ndb, monkeypatch, "CR-R20-4", opened="04:00:00", closed="12:00:00", pieces=6)
        assert _refused_for_the_note(exc)
        out = _register(ndb, monkeypatch, "CR-R20-4", opened="04:00:00", closed="12:00:00",
                        pieces=6, note="Rs 500 short at Day-End", insert=False)
        assert _judged(out) == _balanced(3000.0)
        assert [t["source_ref"] for t in tasks.find({})] == [f"till_variance:{STORE}:{DATE}:{drawer}"]

        # (4)
        for coll in day_rows:
            real_db[coll].delete_many({})
        _cash_sale(real_db, 3000, datetime(2026, 11, 4, 5, 0))
        with pytest.raises(HTTPException) as exc:
            _register(ndb, monkeypatch, "CR-R20-5", opened="04:00:00", closed="12:00:00", pieces=5)
        assert _refused_for_the_note(exc)
        _cash_sale(real_db, 500, datetime(2026, 11, 4, 12, 10))
        out = _register(ndb, monkeypatch, "CR-R20-5", opened="04:00:00", closed="12:30:00",
                        pieces=6, note="Rs 500 short", insert=False)
        assert _judged(out) == {
            "counted": 3000.0, "expected": 3500.0, "variance": -500.0, "variance_status": "SHORT"}
        drawer_row = real_db.till_sessions.find_one({})
        assert (drawer_row["blind_count_paisa"], drawer_row["blind_count_origin"]) == (250000, "CR-R20-5")
        assert [(t["source_ref"], t["payload"]["variance_paisa"]) for t in tasks.find({})] == [
            (f"till_variance:{STORE}:{DATE}:{drawer_row['_id']}", -50000)]
    finally:
        client.drop_database(name)
        client.close()
