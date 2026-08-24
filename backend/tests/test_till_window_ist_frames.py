"""IMS 2.0 - the cash-drawer windows must be read in the IST business frame.

BUG-104 family, two residues the PR #993 delta verifier found by code-read.
The drawer identity is opening + cash_sales - cash_refunds - cash_expenses =
expected, and `expected` is the number a human counts real notes against every
evening in four shops. A window bound read in the wrong calendar frame moves
that number without anyone touching money.

RESIDUE 1 -- the EXPENSE window (_cash_expenses_for_window).
`expense_date` is an operator-typed IST CALENDAR DATE string. The window's
lower bound was `start_iso[:10]` -- the first ten characters of the session's
`opened_at`, which `_iso_now()` writes as a NAIVE-UTC instant. For any till
opened between 00:00 and 05:30 IST those ten characters are YESTERDAY, so a
whole extra day of cash payouts was subtracted from the drawer: expenses
overstated -> `expected` too low -> an honest count reads as a phantom
OVERAGE. The upper bound was worse than wrong, it was INCONSISTENT: it took
the UTC face when a close supplied `end_iso` but the IST face (now_ist) when
the live preview passed None, so the two ends of one window sat in two
different calendar frames. On a till closed after IST midnight the UTC face
lags a day and that day's expenses drop OUT -- expenses understated ->
`expected` too high -> a phantom SHORTAGE.

RESIDUE 2 -- the legacy STRING clause of the SALES window
(_cash_sales_for_window). Orders are matched with an $or over a BSON-datetime
clause and a legacy ISO-STRING clause (Mongo type-brackets a Date range away
from a string field, so both shapes must be asked for separately). The
datetime clause has been correct since #993; the string clause compared the
RAW bound lexically. An offset-suffixed bound ('...T21:00:00+05:30') put
against a stored naive-UTC face ('...T15:30:00') sorts 5h30m late and
silently drops legacy rows -- cash_sales understated -> phantom SHORTAGE.

WHAT THESE TESTS INSIST ON
--------------------------
Every bound is asserted as a CONCRETE day/instant computed by hand, never
re-derived through the helper under test. Clocks are frozen (a real-clock
probe here is the calendar-rot class that broke main this month). The orders
fake TYPE-BRACKETS like Mongo, so no test can pass by the fake being generous
about a string-vs-datetime compare. The sibling suite
test_off_till_expenses.py (41 tests) must stay green -- it owns the
payroll-exclusion half of the same arithmetic.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-till-frames")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import finance  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

STORE = "ZZ-TILLF"

# The IST business day under test and its neighbour, with the naive-UTC
# instants that bracket it. 2026-08-15 IST runs 2026-08-14T18:30 (inclusive)
# to 2026-08-15T18:30 (exclusive) in stored naive-UTC terms.
PREV_IST_DAY = "2026-08-14"
IST_DAY = "2026-08-15"

# 00:30 IST on 15 Aug, as _iso_now() would have stamped it.
OPENED_SMALL_HOURS = "2026-08-14T19:00:00"
# 14:30 IST on 14 Aug -- an ordinary daytime open, same day in both frames.
OPENED_DAYTIME = "2026-08-14T09:00:00"
# 00:45 IST on 15 Aug -- a late close, the hour the upper bound lagged.
CLOSED_AFTER_MIDNIGHT = "2026-08-14T19:15:00"

PREV_DAY_EXPENSE = 900.00
IST_DAY_EXPENSE = 100.00
OPENING_FLOAT = 5000.00
CASH_SALES = 12000.00


def _expense(expense_id, day, amount):
    return {
        "expense_id": expense_id,
        "store_id": STORE,
        "category": "Rent",
        "amount": amount,
        "status": "APPROVED",
        "payment_mode": "CASH",
        "expense_date": day,
    }


_EXPENSES = [
    _expense("ZZ-XP", PREV_IST_DAY, PREV_DAY_EXPENSE),
    _expense("ZZ-XT", IST_DAY, IST_DAY_EXPENSE),
]


# ---------------------------------------------------------------------------
# Fakes. The collection ENFORCES the filter, and range compares TYPE-BRACKET
# exactly as Mongo does: a string bound never matches a datetime field and
# vice versa. That is the whole point of residue 2.
# ---------------------------------------------------------------------------


def _cmp(actual, op, val):
    if isinstance(actual, str) != isinstance(val, str):
        return False  # type bracketing: different BSON types never compare
    if op == "$gte":
        return actual >= val
    if op == "$lte":
        return actual <= val
    if op == "$lt":
        return actual < val
    raise AssertionError("fake does not implement %r" % op)


def _match(doc, query):
    for key, cond in (query or {}).items():
        if key == "$or":
            if not any(_match(doc, arm) for arm in cond):
                return False
            continue
        val = doc.get(key)
        if isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
            for op, opv in cond.items():
                if op == "$in":
                    ok = val in opv
                elif op == "$ne":
                    ok = val != opv
                else:
                    ok = val is not None and _cmp(val, op, opv)
                if not ok:
                    return False
        elif val != cond:
            return False
    return True


class _Cursor(list):
    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self


class _Col:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    def find(self, query=None, projection=None, *a, **k):
        return _Cursor([dict(d) for d in self.docs if _match(d, query)])

    def find_one(self, query=None, *a, **k):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    def update_one(self, query, update, *a, **k):
        for i, d in enumerate(self.docs):
            if _match(d, query):
                merged = dict(d)
                merged.update((update or {}).get("$set") or {})
                self.docs[i] = merged
                return None
        return None

    def aggregate(self, pipeline, *a, **k):
        return []


class _DB:
    def __init__(self, **cols):
        self.cols = {k: _Col(v) for k, v in cols.items()}

    def get_collection(self, name):
        return self.cols.setdefault(name, _Col([]))


def _session(opened_at, status="OPEN"):
    return {
        "session_id": "ZZ-CR-1",
        "store_id": STORE,
        "status": status,
        "opened_at": opened_at,
        "opening_float": OPENING_FLOAT,
    }


def _user():
    return {
        "user_id": "u-till",
        "username": "tester",
        "full_name": "Test User",
        "active_store_id": STORE,
        "store_ids": [STORE],
        "roles": ["STORE_MANAGER"],
    }


def _client():
    app = FastAPI()
    app.include_router(finance.router, prefix="/api/v1/finance")

    async def _u():
        return _user()

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def _freeze(monkeypatch, db, *, iso_now=None, stub_sales=True):
    """One store, one session, two expenses -- and every clock nailed down.

    `stub_sales` replaces the sales side with a constant so the EXPENSE
    assertions cannot fail for an unrelated reason. The two residue-2 tests
    pass stub_sales=False -- they are about the real sales helper."""
    monkeypatch.setattr(finance, "_get_db", lambda: db)
    if stub_sales:
        monkeypatch.setattr(
            finance, "_cash_sales_for_window", lambda *a, **k: (CASH_SALES, 0.0)
        )
    monkeypatch.setattr(finance, "_refund_double_entry_advisory", lambda *a, **k: None)
    monkeypatch.setattr(finance, "_store_name_map", lambda db_: {STORE: "Till Store"})
    # 01:00 IST on 15 Aug: inside the small-hours window, so a UTC-faced bound
    # and an IST-faced one disagree by exactly one day.
    monkeypatch.setattr(finance, "ist_today", lambda: date(2026, 8, 15))
    monkeypatch.setattr(
        finance, "now_ist", lambda: datetime(2026, 8, 15, 1, 0, 0)
    )
    if iso_now is not None:
        monkeypatch.setattr(finance, "_iso_now", lambda: iso_now)


# ===========================================================================
# RESIDUE 1 -- the expense window's lower bound
# ===========================================================================


def test_a_till_opened_after_ist_midnight_does_not_eat_yesterdays_expenses(
    monkeypatch,
):
    """THE REQUIREMENT, as money. A till opened at 00:30 IST belongs to the
    NEW IST day. Its drawer must be charged that day's cash payouts only --
    not the previous day's, which the previous day's till already carried.

    Before the fix the lower bound was the UTC face of `opened_at`
    ('2026-08-14'), so yesterday's Rs 900 was subtracted a second time and
    `expected` came out Rs 900 light: an honest count reads as an overage."""
    db = _DB(
        expenses=_EXPENSES,
        cash_register_sessions=[_session(OPENED_SMALL_HOURS)],
    )
    _freeze(monkeypatch, db)

    resp = _client().get(f"/api/v1/finance/cash-register/sessions?store_id={STORE}")
    assert resp.status_code == 200, resp.text
    prev = resp.json()["expected_preview"]
    assert prev is not None, "fixture broken: no open session, nothing under test"

    assert prev["cash_expenses"] == IST_DAY_EXPENSE, (
        "the drawer of a till opened 00:30 IST on %s was charged "
        "%.2f -- the previous IST day's payout has been subtracted twice"
        % (IST_DAY, prev["cash_expenses"])
    )
    # ...and the drawer identity still holds on the corrected figure.
    assert prev["expected"] == round(OPENING_FLOAT + CASH_SALES - IST_DAY_EXPENSE, 2)


def test_a_daytime_till_is_unchanged(monkeypatch):
    """THE POSITIVE CONTROL. A till opened at 14:30 IST sits on the same
    calendar day in both frames, so the fix must move nothing: its drawer
    still carries that day's payout. A 'fix' that shifted every bound would
    pass the test above and fail this one."""
    db = _DB(
        expenses=_EXPENSES,
        cash_register_sessions=[_session(OPENED_DAYTIME)],
    )
    _freeze(monkeypatch, db)

    resp = _client().get(f"/api/v1/finance/cash-register/sessions?store_id={STORE}")
    prev = resp.json()["expected_preview"]
    # Opened 14 Aug IST, previewed 15 Aug IST: both days are in the window.
    assert prev["cash_expenses"] == round(PREV_DAY_EXPENSE + IST_DAY_EXPENSE, 2)


# ===========================================================================
# RESIDUE 1 -- the expense window's upper bound (the close path)
# ===========================================================================


def test_a_close_after_ist_midnight_still_counts_that_days_expenses(monkeypatch):
    """The other end of the same window. Closing at 00:45 IST stamps
    `closed_at` as a naive-UTC instant whose UTC face is still yesterday, so
    the upper bound lagged a day and the current IST day's payouts fell OUT
    of the drawer: `expected` too high, an honest count reads as a shortage.

    Both bounds are now the IST day, so a till opened in the afternoon and
    closed after midnight carries both days' payouts."""
    db = _DB(
        expenses=_EXPENSES,
        cash_register_sessions=[_session(OPENED_DAYTIME)],
    )
    _freeze(monkeypatch, db, iso_now=CLOSED_AFTER_MIDNIGHT)

    resp = _client().post(
        "/api/v1/finance/cash-register/close",
        json={
            "session_id": "ZZ-CR-1",
            "denominations": [{"face": 500, "pieces": 34}],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cash_expenses"] == round(PREV_DAY_EXPENSE + IST_DAY_EXPENSE, 2), (
        "a close at 00:45 IST dropped that IST day's cash payout out of the "
        "drawer -- expected_cash is too high by that amount"
    )


def test_both_ends_of_the_window_read_the_same_calendar_frame(monkeypatch):
    """The frames-agree requirement, stated directly on the helper.

    The bug was not only that a bound was wrong -- it was that the SAME
    instant produced one day when it arrived as `end_iso` and a different one
    when the preview passed None. Feed one instant as both ends: a window
    whose ends disagree cannot be reasoned about by anyone."""
    db = _DB(expenses=_EXPENSES)
    _freeze(monkeypatch, db)

    # 00:30 IST on 15 Aug as both bounds -> a single-day IST window on the
    # 15th, which contains that day's payout and not the previous day's.
    window = finance._cash_expenses_for_window(
        db, STORE, OPENED_SMALL_HOURS, OPENED_SMALL_HOURS
    )
    assert window.total == IST_DAY_EXPENSE, (
        "one instant used as both bounds produced a window that is not the "
        "single IST day it names (total=%.2f)" % window.total
    )


# ===========================================================================
# RESIDUE 2 -- the legacy string clause of the sales window
# ===========================================================================


def _order(created_at, amount, oid):
    return {
        "order_id": oid,
        "store_id": STORE,
        "created_at": created_at,
        "payments": [{"method": "CASH", "amount": amount}],
    }


def test_legacy_string_orders_match_an_offset_suffixed_bound(monkeypatch):
    """A bound carrying a +05:30 offset must be compared against legacy
    string-typed `created_at` in the frame those strings are STORED in
    (naive-UTC), not at face value.

    '2026-08-14T21:00:00+05:30' IS '2026-08-14T15:30:00' -- but compared as
    text it sorts after 15:30, so a legacy row at 16:00 UTC was dropped from
    the drawer's cash sales. The datetime-typed row proves the other clause
    still works, and the pre-window row is the positive control."""
    bound_aware = "2026-08-14T21:00:00+05:30"  # == 15:30:00 naive UTC
    db = _DB(
        orders=[
            # Legacy STRING-typed created_at, inside the window (16:00 UTC).
            _order("2026-08-14T16:00:00", 500.0, "ZZ-O-STR"),
            # Current DATETIME-typed created_at, inside the window.
            _order(datetime(2026, 8, 14, 17, 0, 0), 700.0, "ZZ-O-DT"),
            # Legacy string BEFORE the window opens (15:00 UTC) -- must stay out.
            _order("2026-08-14T15:00:00", 999.0, "ZZ-O-EARLY"),
        ],
        returns=[],
    )
    _freeze(monkeypatch, db, stub_sales=False)

    cash_sales, cash_refunds = finance._cash_sales_for_window(
        db, STORE, bound_aware, "2026-08-14T23:00:00"
    )

    assert cash_sales == 1200.0, (
        "expected the legacy string row (500) + the datetime row (700); got "
        "%.2f -- an offset-suffixed bound was compared to naive-UTC strings "
        "at face value" % cash_sales
    )
    assert cash_refunds == 0.0


def test_naive_bounds_and_naive_legacy_rows_are_unchanged(monkeypatch):
    """POSITIVE CONTROL for residue 2: production never writes an aware
    stamp, so the ordinary all-naive case must behave exactly as before."""
    db = _DB(
        orders=[
            _order("2026-08-14T16:00:00", 500.0, "ZZ-O-STR"),
            _order(datetime(2026, 8, 14, 17, 0, 0), 700.0, "ZZ-O-DT"),
            _order("2026-08-14T15:00:00", 999.0, "ZZ-O-EARLY"),
        ],
        returns=[],
    )
    _freeze(monkeypatch, db, stub_sales=False)

    cash_sales, _ = finance._cash_sales_for_window(
        db, STORE, "2026-08-14T15:30:00", "2026-08-14T23:00:00"
    )
    assert cash_sales == 1200.0
