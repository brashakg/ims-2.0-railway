"""
IMS 2.0 -- TWO DOORS, ONE RECORD (the owner's second ruling)
============================================================
The owner said: "Keep both, make them agree." Keeping both SCREENS is the
ruling; keeping both RECORDS is how they came to disagree. POS Day-End and
Finance > Cash Register now land the day's counted drawer on the SAME
``till_sessions`` document, so there is only one answer for a store-day and
therefore nothing for the two screens to differ about.

What is proved here, against the REAL services and routers (no stubbed
subject -- ``eod_tally``, ``reports.create_day_end_close`` and
``finance.cash_reconciliation_summary`` are the things under test, driven by
the faithful in-memory Mongo the F23 till tests already use):

  * closing from EITHER door creates or joins ONE session for (store, date)
  * the SECOND door does not restate the FIRST count (a signed-off drawer is
    not quietly overwritten by whoever closed last)
  * a close with NOTHING counted submits NO count -- the session stays OPEN
    and ``blind_count_paisa`` stays None. It is never a submitted zero.
    (This is the live defect: POS Day-End persisted blank as Rs 0.00 and a
    variance of minus the whole day's cash, indistinguishable from a drawer
    someone emptied.)
  * the manager console shows ONE row for ONE counted drawer -- the money is
    not reported twice because two screens touched it
  * a broken link NEVER stops a store closing its day

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_two_doors_one_record.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

# REUSE, do not fork: the faithful fake Mongo already written for the till
# engine. A second in-memory Mongo would be a second set of behaviours for the
# two suites to disagree about -- the very thing this feature is about.
from test_f23_blind_eod_cash_tally import FakeDB  # noqa: E402

from api.routers import finance as finance_router  # noqa: E402
from api.routers import reports as reports_router  # noqa: E402
from api.services import cash_denominations as cd  # noqa: E402
from api.services import eod_tally as till  # noqa: E402

STORE = "BV-PUN-01"
DATE = "2026-08-24"
USER = {
    "user_id": "u-mgr",
    "username": "mgr",
    "name": "Priya",
    "roles": ["STORE_MANAGER"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}


def _rows(*pairs) -> List[Dict[str, Any]]:
    return [{"face": f, "kind": "note", "pieces": p} for f, p in pairs]


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture(autouse=True)
def _pin_environment(monkeypatch):
    """Pin the reads the till engine makes outside its own collection, at their
    REAL call sites, so nothing reaches an absent database. The subject itself
    (eod_tally / the two routers) is NOT patched."""
    monkeypatch.setattr(
        "api.services.policy_engine._resolve_entity_id", lambda store_id: None
    )
    monkeypatch.setattr(
        "api.services.policy_engine.get_policy",
        lambda key, scope=None, *, default=None: (
            0 if key == "till.variance_tolerance_paisa" else default
        ),
    )
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: None)
    monkeypatch.setattr("api.services.eod_tally._audit", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _wire_routers(monkeypatch, db):
    """Both routers reach their database through ``get_db()``. Point that at the
    fake; everything else in the handlers runs for real."""
    monkeypatch.setattr(reports_router, "get_db", lambda: db)
    monkeypatch.setattr(finance_router, "_get_db", lambda: db)
    monkeypatch.setattr(
        "api.services.stores_util.is_online_store", lambda _db, _sid: False
    )
    return db


def _day_end_close(db, **body_kw):
    """Drive POS Day-End's real handler."""
    body = reports_router.DayEndCloseBody(date=DATE, store_id=STORE, **body_kw)
    return asyncio.run(reports_router.create_day_end_close(body, current_user=USER))


def _sessions(db) -> List[Dict[str, Any]]:
    return list(db.get_collection("till_sessions").find({}))


# ===========================================================================
# ONE RECORD
# ===========================================================================


class TestBothDoorsLandOnOneRecord:
    def test_pos_day_end_lands_its_count_on_the_shared_till_session(self, db):
        res = _day_end_close(
            db,
            closing_cash=13000.0,
            system_cash=13000.0,
            closing_count=cd.CashCountInput(
                rows=_rows((500, 20), (200, 10), (100, 10)), state="COUNTED"
            ),
        )
        close = res["close"]
        sessions = _sessions(db)
        # ONE session for the store-day, and the day-end record names it.
        assert len(sessions) == 1
        assert close["till_session_id"] == sessions[0]["session_id"]
        assert close["till_link_ok"] is True
        assert close["till_counted"] is True
        # The count landed as MONEY on the shared record, paisa-exact.
        assert sessions[0]["blind_count_paisa"] == 1300000
        assert sessions[0]["status"] == till.STATUS_BLIND_SUBMITTED
        # ...and as NOTES. Assert the SET and the COUNT, not just the total.
        counted = {
            (r["kind"], r["face"]): r["pieces"]
            for r in sessions[0]["closing_count"]["rows"]
        }
        assert counted == {("note", 500): 20, ("note", 200): 10, ("note", 100): 10}
        assert sessions[0]["closing_count"]["state"] == "COUNTED"

    def test_the_second_door_joins_the_session_it_does_not_open_a_second_drawer(
        self, db
    ):
        first = till.record_screen_close(
            db,
            store_id=STORE,
            session_date=DATE,
            closing_rows=_rows((500, 20)),
            closing_count_state="COUNTED",
            counted_paisa=1000000,
            actor=USER,
        )
        second = _day_end_close(db, closing_cash=10000.0, system_cash=10000.0)

        assert len(_sessions(db)) == 1
        assert second["close"]["till_session_id"] == first["session_id"]

    def test_the_second_door_does_not_restate_the_first_count(self, db):
        """A drawer counted through one door must not be silently re-counted
        through the other. The first count STANDS."""
        till.record_screen_close(
            db,
            store_id=STORE,
            session_date=DATE,
            closing_rows=_rows((500, 20)),
            closing_count_state="COUNTED",
            counted_paisa=1000000,
            actor=USER,
        )
        res = _day_end_close(db, closing_cash=9999.0, system_cash=10000.0)

        session = _sessions(db)[0]
        assert session["blind_count_paisa"] == 1000000  # NOT 999900
        assert res["close"]["till_already_counted"] is True
        assert res["close"]["till_counted"] is False


# ===========================================================================
# BLANK IS NOT ZERO  (Safety Rule B / C -- the live defect)
# ===========================================================================


class TestNothingCountedIsNotAnEmptyDrawer:
    def test_a_close_with_no_count_records_no_cash_and_no_variance(self, db):
        res = _day_end_close(db, notes="rush, counted in the morning")
        close = res["close"]

        # Not a drawer holding Rs 0.00, and not a variance of minus the day.
        assert close["closing_cash"] is None
        assert close["variance"] is None
        assert close["cash_counted"] is False
        assert close["closing_count"]["state"] == "NOT_CAPTURED"
        # NOT_CAPTURED reconciles against nothing -- null, never False.
        assert close["closing_count"]["matches_amount"] is None

    def test_a_close_with_no_count_submits_no_count_to_the_shared_record(self, db):
        res = _day_end_close(db)

        session = _sessions(db)[0]
        assert session["status"] == till.STATUS_OPEN  # not counted YET
        assert session["blind_count_paisa"] is None  # not zero
        assert session["closing_count"]["state"] == "NOT_CAPTURED"
        assert res["close"]["till_counted"] is False
        assert res["close"]["till_link_ok"] is True

    def test_the_finance_door_with_a_blank_grid_submits_no_count_either(self, db):
        """A blank grid on the cash-register close sums to 0.0. Forwarding that
        would put the same fabricated zero on the shared record through the
        other door."""
        res = till.record_screen_close(
            db,
            store_id=STORE,
            session_date=DATE,
            closing_rows=[],
            closing_count_state=None,
            counted_paisa=None,
            actor=USER,
        )
        assert res["counted"] is False
        assert res["not_captured"] is True
        assert _sessions(db)[0]["blind_count_paisa"] is None

    def test_a_grid_counted_as_genuinely_empty_is_still_a_real_count(self, db):
        """rows=[] with an explicit COUNTED state means 'counted, and there was
        nothing in it' -- a real Rs 0.00 drawer. That must still submit."""
        res = till.record_screen_close(
            db,
            store_id=STORE,
            session_date=DATE,
            closing_rows=[],
            closing_count_state="COUNTED",
            counted_paisa=0,
            actor=USER,
        )
        assert res["counted"] is True
        session = _sessions(db)[0]
        assert session["blind_count_paisa"] == 0
        assert session["closing_count"]["state"] == "COUNTED"

    def test_a_day_nobody_declared_a_float_for_says_so(self, db):
        """Auto-opening a session to close a past date has to assume a zero
        opening float. The flag is how the screen admits that, instead of
        letting the resulting variance pass as a real one."""
        res = _day_end_close(db, closing_cash=500.0, system_cash=500.0)
        assert res["close"]["till_opening_float_not_recorded"] is True
        assert _sessions(db)[0]["opening_float_not_recorded"] is True


# ===========================================================================
# NOTHING BLOCKS THE CLOSE
# ===========================================================================


class TestLinkingNeverStopsTheDayClosing:
    def test_a_broken_link_is_reported_not_raised(self, db, monkeypatch):
        def _explode(*a, **k):
            raise RuntimeError("mongo went away")

        monkeypatch.setattr(till, "_record_screen_close", _explode)

        res = _day_end_close(db, closing_cash=1000.0, system_cash=1000.0)
        close = res["close"]
        # The day still closed, with its own figures intact.
        assert res["closed"] is True
        assert close["closing_cash"] == 1000.0
        assert close["variance"] == 0.0
        # ...and the failure is on the record, not swallowed into a fake success.
        assert close["till_link_ok"] is False
        assert "mongo went away" in str(close["till_link_error"])


# ===========================================================================
# ONE COUNTED DRAWER, ONE ROW IN THE MANAGER CONSOLE
# ===========================================================================


def _seed_linked_pair(db, till_session_id="TILL-1"):
    """One physical drawer, counted once, touched by both screens: a Finance
    cash-register close that linked to the till session, and that same till
    session locked as a Z-Read."""
    db.get_collection("till_sessions").insert_one(
        {
            "_id": till_session_id,
            "session_id": till_session_id,
            "store_id": STORE,
            "session_date": DATE,
            "status": till.STATUS_LOCKED,
            "opening_float_paisa": 200000,
            "cash_sales_paisa": 500000,
            "cash_refunds_paisa": 0,
            "cash_payouts_paisa": 0,
            "expected_cash_paisa": 700000,
            "blind_count_paisa": 700000,
            "variance_paisa": 0,
            "variance_status": "BALANCED",
            "tolerance_paisa": 0,
            "locked_by": "u-mgr",
            "locked_at": f"{DATE}T12:00:00",
            "zread_number": 7,
        }
    )
    db.get_collection("cash_register_sessions").insert_one(
        {
            "_id": "CR-1",
            "session_id": "CR-1",
            "store_id": STORE,
            "status": "CLOSED",
            "opened_at": f"{DATE}T03:30:00",
            "closed_at": f"{DATE}T12:00:00",
            "opening_float": 2000.0,
            "cash_sales": 5000.0,
            "cash_refunds": 0.0,
            "cash_expenses": 0.0,
            "bank_deposit": 0.0,
            "expected_cash": 7000.0,
            "counted_cash": 7000.0,
            "variance": 0.0,
            "closed_by": "u-mgr",
            "till_session_id": till_session_id,
        }
    )


def _summary(db, day=DATE):
    return asyncio.run(
        finance_router.cash_reconciliation_summary(
            from_date=day, to_date=day, store_id=STORE, current_user=USER
        )
    )


class TestOneCountedDrawerIsOneRow:
    def test_a_drawer_closed_through_both_doors_is_reported_once(self, db):
        _seed_linked_pair(db)
        out = _summary(db)

        assert len(out["rows"]) == 1
        assert out["totals"]["sessions"] == 1
        # Rs 7,000 was counted ONCE. Reported twice it would read as Rs 14,000
        # of cash across the range -- two days' takings from one drawer.
        assert out["totals"]["counted_cash"] == 7000.0
        assert out["totals"]["expected_cash"] == 7000.0
        assert out["totals"]["cash_sales"] == 5000.0

    def test_the_surviving_row_is_the_shared_record_and_keeps_the_other_door(
        self, db
    ):
        _seed_linked_pair(db)
        row = _summary(db)["rows"][0]

        assert row["source"] == "BLIND_EOD"  # the shared record survives
        assert row["session_id"] == "TILL-1"
        assert row["zread_number"] == 7
        # Nothing about who closed it through the other door is lost.
        assert row["also_closed_from"] == "CR-1"
        assert row["also_closed_from_source"] == "CASH_REGISTER"

    def test_an_unlinked_cash_register_close_is_still_its_own_row(self, db):
        """Historical closes carry no till_session_id. They must keep appearing
        -- folding is for a drawer counted once, not for every legacy row."""
        _seed_linked_pair(db)
        db.get_collection("cash_register_sessions").insert_one(
            {
                "_id": "CR-OLD",
                "session_id": "CR-OLD",
                "store_id": STORE,
                "status": "CLOSED",
                "opened_at": f"{DATE}T03:00:00",
                "closed_at": f"{DATE}T08:00:00",
                "opening_float": 1000.0,
                "cash_sales": 1000.0,
                "expected_cash": 2000.0,
                "counted_cash": 2000.0,
                "variance": 0.0,
            }
        )
        out = _summary(db)

        assert {r["session_id"] for r in out["rows"]} == {"TILL-1", "CR-OLD"}
        assert out["totals"]["sessions"] == 2


# ===========================================================================
# THE FINANCE DOOR, END TO END
# ===========================================================================


def _open_cr_session(db, session_id="CR-OPEN", opening_float=0.0):
    db.get_collection("cash_register_sessions").insert_one(
        {
            "_id": session_id,
            "session_id": session_id,
            "store_id": STORE,
            "status": "OPEN",
            "opening_float": opening_float,
            "opening_denominations": [],
            "opened_at": "2026-08-24T04:00:00",
        }
    )
    return session_id


def _cr_close(db, opening_float=0.0, **body_kw):
    body = finance_router.CashRegisterClose(
        session_id=_open_cr_session(db, opening_float=opening_float), **body_kw
    )
    return asyncio.run(
        finance_router.close_cash_register(body=body, current_user=USER)
    )


def _cr_stored(db, session_id="CR-OPEN"):
    return db.get_collection("cash_register_sessions").find_one(
        {"session_id": session_id}
    )


class TestTheFinanceDoorNeverFabricatesACount:
    def test_a_blank_grid_closes_the_till_without_counting_the_drawer(self, db):
        """A blank grid sums to 0.00 -- the sum of nothing. Forwarding that as
        a count would tell the shared record the drawer held nothing, which is
        the same lie POS Day-End used to tell."""
        _cr_close(db, denominations=[], bank_deposit=0.0)

        session = _sessions(db)[0]
        assert session["status"] == till.STATUS_OPEN
        assert session["blind_count_paisa"] is None  # never a fabricated zero
        assert session["closing_count"]["state"] == "NOT_CAPTURED"

    def test_a_blank_finance_close_records_not_counted_never_a_zero_drawer(
        self, db
    ):
        """THE OWNER'S OWN BUG, on the second door. Open with 4 x Rs 500, close
        with an UNTOUCHED grid: the screen used to persist counted 0.00 against
        an expected Rs 2,000 -- variance -Rs 2,000, verdict SHORT -- while the
        very same document's count block said NOT_CAPTURED. One record, two
        answers, and the honest one lost. A blank grid means NOT COUNTED."""
        out = _cr_close(db, opening_float=2000.0, denominations=[], bank_deposit=0.0)

        for doc in (out, _cr_stored(db)):
            # Never (0.0, -2000.0, "SHORT") -- the whole day, called missing.
            assert (doc["counted"], doc["variance"], doc["variance_status"]) == (
                None,
                None,
                "NOT_COUNTED",
            )
            # The expected drawer is still reported -- the figure is real, it
            # is the VERDICT that is withheld.
            assert doc["expected"] == 2000.0
            assert doc["closing_count"]["state"] == "NOT_CAPTURED"
            assert doc["closing_count"]["matches_amount"] is None

    def test_an_uncounted_drawer_is_not_a_shortfall_on_the_manager_console(
        self, db
    ):
        """The console is where a manager actually reads this. Re-deriving
        `float(counted or 0)` there would rebuild the fabricated zero -- and
        its full-day shortfall -- one screen further along."""
        _cr_close(db, opening_float=2000.0, denominations=[], bank_deposit=0.0)
        stored = _cr_stored(db)
        day = finance_router._ist_day_face(stored["closed_at"])

        out = _summary(db, day=day)
        row = [r for r in out["rows"] if r["session_id"] == "CR-OPEN"][0]

        assert (row["counted_cash"], row["variance"], row["variance_status"]) == (
            None,
            None,
            "NOT_COUNTED",
        )
        # ...and it is counted as neither balanced nor short in the totals.
        assert (out["totals"]["shortage"], out["totals"]["shortage_amount"]) == (0, 0)
        assert out["totals"]["balanced"] == 0

    def test_a_manager_who_counts_an_empty_drawer_still_gets_a_real_zero(self, db):
        """POSITIVE CONTROL. Blank is not zero -- but zero is still possible.
        A typed override of 0 is a human saying "I counted, it was empty", and
        that must still produce a real count and a real shortfall."""
        out = _cr_close(
            db, opening_float=2000.0, denominations=[], counted_override=0.0,
            # This suite pins the band to 0, and a Rs 2,000 short is beyond any
            # band: the 2026-08-25 ruling demands the explanation to close.
            note="drawer emptied for banking before the count - slip attached",
        )

        assert out["counted"] == 0.0
        assert out["variance"] == -2000.0
        assert out["variance_status"] == "SHORT"
        assert _sessions(db)[0]["blind_count_paisa"] == 0

    def test_a_counted_grid_does_land_on_the_shared_record(self, db):
        """The positive control: the guard above must not swallow a real count."""
        _cr_close(
            db,
            denominations=[{"face": 500, "kind": "note", "pieces": 4}],
            bank_deposit=0.0,
            closing_count_state="COUNTED",
            # Band pinned to 0 by this suite; the Rs 2,000 overage (opening
            # float 0) is out of band, so the close needs its explanation.
            note="opening float was never declared - counted Rs 2,000 stands",
        )

        session = _sessions(db)[0]
        assert session["status"] == till.STATUS_BLIND_SUBMITTED
        assert session["blind_count_paisa"] == 200000
        assert [
            (r["kind"], r["face"], r["pieces"]) for r in session["closing_count"]["rows"]
        ] == [("note", 500, 4)]

    def test_a_till_closed_after_ist_midnight_links_to_the_day_it_is_filed_under(
        self, db, monkeypatch
    ):
        """BUG-104 class. ``closed_at`` is a NAIVE-UTC instant. 19:00 UTC is
        00:30 IST the next day, and the manager console files that row under
        the NEXT IST day -- so the shared session it links to must be that same
        day, or the two disagree about which business day was closed."""
        monkeypatch.setattr(finance_router, "_iso_now", lambda: "2026-08-24T19:00:00")

        _cr_close(
            db,
            denominations=[{"face": 500, "kind": "note", "pieces": 1}],
            closing_count_state="COUNTED",
            # Band pinned to 0; the Rs 500 variance is out of band -> note.
            note="ZZ test close - variance explained",
        )
        assert _sessions(db)[0]["session_date"] == "2026-08-25"


# ===========================================================================
# ONE DRAWER, ONE ANSWER -- the Finance screen reports the shared count
# ===========================================================================


def _cr_sessions(db) -> List[Dict[str, Any]]:
    return asyncio.run(
        # Explicit args: a direct call leaves FastAPI Query(...) defaults in place.
        finance_router.list_cash_register_sessions(
            store_id=STORE, status=None, limit=50, current_user=USER
        )
    )["sessions"]


class TestTheTwoScreensCannotShowTwoFigures:
    @pytest.fixture(autouse=True)
    def _fixed_clock(self, monkeypatch):
        """Both doors must land on the SAME business day for this to be one
        drawer. 12:00 UTC is 17:30 IST on the same date."""
        monkeypatch.setattr(finance_router, "_iso_now", lambda: f"{DATE}T12:00:00")

    def test_the_finance_list_reports_the_count_the_day_end_already_made(self, db):
        """THE REPRODUCTION. POS Day-End closes the drawer at Rs 3,000. The
        Finance screen then closes the SAME day with a 4 x Rs 500 grid. The
        first count STANDS on the shared record, so the Finance list must
        report Rs 3,000 too -- it used to report Rs 2,000 and a Rs 1,000
        OVERAGE for a drawer the Z-Read said held Rs 3,000."""
        _day_end_close(
            db,
            closing_cash=3000.0,
            system_cash=3000.0,
            closing_count=cd.CashCountInput(rows=_rows((500, 6)), state="COUNTED"),
        )
        shared = _sessions(db)[0]
        assert shared["blind_count_paisa"] == 300000

        closed = _cr_close(
            db,
            denominations=[{"face": 500, "kind": "note", "pieces": 4}],
            closing_count_state="COUNTED",
            # Band pinned to 0; the adopted Rs 3,000 count vs expected 0 is out
            # of band -> the mandatory explanation (2026-08-25 ruling).
            note="day-end already counted this drawer - see Z-Read",
        )

        # ONE drawer, ONE counted figure -- on the close response, on the
        # stored document, and on what the Finance screen reads back.
        assert closed["counted"] == 3000.0
        listed = _cr_sessions(db)
        assert len(listed) == 1
        assert listed[0]["counted"] == cd.paisa_to_rupees(shared["blind_count_paisa"])
        assert listed[0]["counted"] == 3000.0
        # ...and the screen is TOLD, rather than silently shown someone else's
        # number.
        assert listed[0]["counted_from_shared_record"] is True
        assert listed[0]["till_count_differs"] is True
        assert listed[0]["till_already_counted"] is True
        # The variance is re-derived from the count that stands.
        assert listed[0]["variance"] == round(3000.0 - listed[0]["expected"], 2)
        # The shared record is NOT restated by the second door.
        assert _sessions(db)[0]["blind_count_paisa"] == 300000

    def test_the_finance_door_closing_first_still_owns_its_own_count(self, db):
        """POSITIVE CONTROL. When this screen is the one that counts the
        drawer, its own figure IS the shared figure -- nothing is adopted and
        nothing is flagged."""
        closed = _cr_close(
            db,
            denominations=[{"face": 500, "kind": "note", "pieces": 4}],
            closing_count_state="COUNTED",
            # Band pinned to 0; Rs 2,000 counted vs expected 0 -> note needed.
            note="opening float was never declared - counted Rs 2,000 stands",
        )
        shared = _sessions(db)[0]

        assert closed["counted"] == 2000.0
        assert shared["blind_count_paisa"] == 200000
        listed = _cr_sessions(db)[0]
        assert listed["counted"] == cd.paisa_to_rupees(shared["blind_count_paisa"])
        assert listed["counted_from_shared_record"] is False
        assert listed["till_count_differs"] is False


# ===========================================================================
# THE OPENING FLOAT DECLARED AT THE FINANCE DOOR REACHES THE SHARED RECORD
# ===========================================================================


def _cr_open(db, **body_kw):
    body = finance_router.CashRegisterOpen(store_id=STORE, **body_kw)
    return asyncio.run(finance_router.open_cash_register(body=body, current_user=USER))


class TestTheOpeningFloatIsShared:
    @pytest.fixture(autouse=True)
    def _fixed_clock(self, monkeypatch):
        monkeypatch.setattr(finance_router, "_iso_now", lambda: f"{DATE}T12:00:00")
        monkeypatch.setattr(finance_router, "is_online_store", lambda *_a, **_k: False)

    def test_a_float_declared_here_lands_on_the_shared_record(self, db):
        """Rs 2,000 declared as 2 x Rs 500 + 10 x Rs 100. Without this the
        shared record held opening_float_paisa 0 with
        opening_float_not_recorded set, so expected cash and EVERY per-face
        expected row were computed from nothing and the note-by-note verdict
        was withheld for the whole store-day."""
        opened = _cr_open(
            db,
            denominations=[
                {"face": 500, "kind": "note", "pieces": 2},
                {"face": 100, "kind": "note", "pieces": 10},
            ],
            opening_count_state="COUNTED",
        )

        session = _sessions(db)[0]
        assert opened["till_link_ok"] is True
        assert opened["till_session_id"] == session["session_id"]
        assert session["opening_float_paisa"] == 200000
        assert session["opening_float_not_recorded"] is False
        # The NOTES, not just the total -- SET and COUNT.
        assert {
            (r["kind"], r["face"]): r["pieces"]
            for r in session["opening_count"]["rows"]
        } == {("note", 500): 2, ("note", 100): 10}
        assert session["opening_count"]["state"] == "COUNTED"

    def test_the_shared_float_survives_a_day_end_close_through_the_other_door(self, db):
        _cr_open(
            db,
            denominations=[{"face": 500, "kind": "note", "pieces": 4}],
            opening_count_state="COUNTED",
        )
        res = _day_end_close(db, closing_cash=5000.0, system_cash=5000.0)

        session = _sessions(db)[0]
        assert session["opening_float_paisa"] == 200000
        assert res["close"]["till_opening_float_not_recorded"] is False

    def test_a_blank_opening_grid_declares_nothing_rather_than_zero(self, db):
        """Blank is not zero at the open door either: an untouched grid must not
        put "the drawer opened with Rs 0.00" on the record all three screens
        read."""
        _cr_open(db, denominations=[])

        session = _sessions(db)[0]
        assert session["opening_float_not_recorded"] is True
        assert session["opening_count"]["state"] == "NOT_CAPTURED"

    def test_a_float_already_declared_is_not_restated_by_this_screen(self, db):
        """First answer wins, the same rule the closing count follows."""
        till.open_session(
            db,
            store_id=STORE,
            session_date=DATE,
            opening_denominations=_rows((500, 10)),
            opening_count_state="COUNTED",
            actor=USER,
        )
        opened = _cr_open(
            db,
            denominations=[{"face": 500, "kind": "note", "pieces": 1}],
            opening_count_state="COUNTED",
        )

        session = _sessions(db)[0]
        assert session["opening_float_paisa"] == 500000  # NOT 50000
        assert opened["till_float_already_declared"] is True

    def test_a_float_declared_after_an_auto_opened_session_fills_the_gap(self, db):
        """A close screen auto-opens a session with NO float on it. The first
        DECLARED float fills that gap -- otherwise the per-face verdict stays
        withheld for a store that opens on this screen."""
        till.record_screen_close(db, store_id=STORE, session_date=DATE, actor=USER)
        assert _sessions(db)[0]["opening_float_not_recorded"] is True

        _cr_open(
            db,
            denominations=[{"face": 500, "kind": "note", "pieces": 3}],
            opening_count_state="COUNTED",
        )
        session = _sessions(db)[0]
        assert session["opening_float_paisa"] == 150000
        assert session["opening_float_not_recorded"] is False

    def test_a_broken_link_never_stops_the_drawer_opening(self, db, monkeypatch):
        def _explode(*a, **k):
            raise RuntimeError("mongo went away")

        monkeypatch.setattr(till, "open_session", _explode)
        opened = _cr_open(db, denominations=[{"face": 500, "kind": "note", "pieces": 1}])

        assert opened["status"] == "OPEN"
        assert opened["opening_float"] == 500.0
        assert opened["till_link_ok"] is False
        assert "mongo went away" in str(opened["till_link_error"])
