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


@pytest.fixture(autouse=True)
def audit_rows(monkeypatch) -> List[Dict[str, Any]]:
    """Capture audit writes at the accessor the services use. Autouse, so no
    test here can write an audit row into a real database."""
    rows: List[Dict[str, Any]] = []

    class _Repo:
        def create(self, data):
            rows.append(dict(data))
            return {"log_id": f"AUD-{len(rows)}"}

    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: _Repo())
    monkeypatch.setattr(reports_router, "get_audit_repository", lambda: _Repo())
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
