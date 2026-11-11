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
        sessions = list(db.get_collection("till_sessions").find({}))
        assert len(sessions) == 1
        assert sessions[0]["blind_count_paisa"] == 300000

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
    name = f"ims_test_b12_{uuid.uuid4().hex[:8]}"
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
    # Revenue actuals come from orders; none are seeded here.
    monkeypatch.setattr(budgets_router, "get_order_repository", lambda: None)
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
