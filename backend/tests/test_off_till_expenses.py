"""IMS 2.0 - a wage bill must not be subtracted from a shop cash drawer.

OWNER RULING 2026-08-14, asked in exactly these words -- are salaries, staff
advances or PF/ESI ever paid out of a shop cash till?

    "NEVER - always bank, cheque or from the office."

So this is an EXPENSE-CLASSIFICATION correction, not a salary redaction, and
these tests are written as money tests first. `expected_cash` is the number a
real person counts real notes against every evening in four shops; if it is
wrong, the count reads as a phantom OVERAGE the size of a month's wages and the
day-end alarm gets tuned out. The security consequence (round 1 of PR #985 made
/finance/pnl payroll-exclusive and left this family payroll-inclusive over the
same store for the same four roles, so one subtraction recovered the wage bill)
is closed by the SAME fix, which is why the fix is deliberately identical for
every role including ADMIN and SUPERADMIN.

WHAT THESE TESTS INSIST ON
--------------------------
1. The drawer arithmetic still holds: opening + cash_sales - cash_refunds -
   cash_expenses = expected. Rent paid in cash is STILL subtracted. Only the
   payroll-shaped head is left out. (A "fix" that drops every expense would
   pass a naive leak test and destroy the drawer -- that is the positive
   control here, and it is not optional.)
2. It behaves IDENTICALLY for an ADMIN. Any role-conditional drawer is a new
   asymmetry to subtract across, so the admin case is a REQUIREMENT, not a
   guard against over-stripping.
3. The exclusion is VISIBLE. A figure a human counts money against must never
   be silently adjusted. The advisory carries a count and a sentence -- never
   the amount, never the head name.

Every test drives the real endpoint through TestClient and asserts on the
RESPONSE BODY. Hermetic fakes, no Mongo. No emoji (Windows cp1252).
"""

from __future__ import annotations

import datetime as _dt
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-off-till")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import finance  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import rbac_policy, salary_visibility  # noqa: E402


# ===========================================================================
# The planted till. Numbers chosen so no two sums collide by accident:
# no subset of the others adds to the wage bill and vice versa.
# ===========================================================================

STORE = "ZZ-TILL"
DAY = "2026-08-14"
OPENED_AT = f"{DAY}T09:00:00+05:30"

# CALENDAR ROT GUARD. `DAY` is a fixed date, which is right for rows this file
# SEEDS with an explicit closed_at. It is wrong for the one test that closes a
# session through the REAL endpoint: that stamps closed_at from the server
# clock, and cash-reconciliation-summary buckets a session by str(closed_at)[:10]
# (finance.py:4801-4802). A hard-coded single-day window therefore passed on the
# day this file was written and failed every day after -- it broke main the next
# morning.
#
# The window below spans the seeded day through TOMORROW, so it holds whatever
# the date is and whichever side of midnight the server's timezone falls on.
# Widening it costs nothing here: this test's subject is the CONTENT of the
# closed row, and date filtering is covered separately by the seeded-row test
# further down, which still pins the exact day.
_TOMORROW = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()

OPENING_FLOAT = 5000.00
CASH_SALES = 41000.00
CASH_REFUNDS = 0.0

RENT_CASH = 2333.33          # an ORDINARY cash expense -- MUST still be deducted
WAGES = 71234.56             # the verifier's planted wage bill, to the paisa

# What the drawer should expect once the wage bill is out of it.
EXPECTED_RIGHT = round(OPENING_FLOAT + CASH_SALES - CASH_REFUNDS - RENT_CASH, 2)
# What it expected BEFORE the fix -- negative, which is how the bug announced
# itself: a "cash-in is missing" verdict on a perfectly ordinary day.
EXPECTED_WRONG = round(EXPECTED_RIGHT - WAGES, 2)


def _expense(expense_id, category, amount, mode=None):
    doc = {
        "expense_id": expense_id,
        "store_id": STORE,
        "category": category,
        "amount": amount,
        "status": "APPROVED",
        "expense_date": DAY,
    }
    if mode is not None:
        doc["payment_mode"] = mode
    return doc


# The pay head is booked with the payment-mode box LEFT BLANK, which is the
# real-world shape: ExpenseCreate.payment_mode is Optional and
# _cash_expenses_for_window's own comment says "unknown mode counts as cash
# (conservative)". Rent is booked explicitly CASH so it is unambiguously a
# drawer payout and its survival is a real assertion.
_DEFAULT_EXPENSES = [
    _expense("ZZ-X1", "Rent", RENT_CASH, "CASH"),
    _expense("ZZ-X2", "Staff Salaries", WAGES),
]

_OPEN_SESSION = {
    "session_id": "ZZ-CR-OPEN",
    "store_id": STORE,
    "status": "OPEN",
    "opened_at": OPENED_AT,
    "opening_float": OPENING_FLOAT,
}


def _match(doc, query):
    for key, cond in (query or {}).items():
        val = doc.get(key)
        if isinstance(cond, dict):
            for op, opv in cond.items():
                if op == "$gte" and not (val is not None and val >= opv):
                    return False
                if op == "$lte" and not (val is not None and val <= opv):
                    return False
                if op == "$in" and val not in opv:
                    return False
        elif val != cond:
            return False
    return True


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


class _Cursor(list):
    """`find(...).sort(...).limit(...)` chains on the sessions list route."""

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self


class _TillDB:
    def __init__(self, expenses=None):
        self.cols = {
            "expenses": _Col(expenses if expenses is not None else _DEFAULT_EXPENSES),
            "cash_register_sessions": _Col([_OPEN_SESSION]),
        }

    def get_collection(self, name):
        return self.cols.setdefault(name, _Col([]))


def _session_user(*roles):
    return {
        "user_id": "u-till",
        "username": "tester",
        "full_name": "Test User",
        "active_store_id": STORE,
        "store_ids": [STORE],
        "roles": list(roles),
    }


@pytest.fixture
def till_db(monkeypatch):
    """One store, one open session, one real cash expense, one wage bill.

    `_cash_sales_for_window` is stubbed rather than faked from `orders`: this
    file is about the EXPENSE side of the drawer identity, and a hand-built
    orders fixture would only add a way for the arithmetic assertions below to
    be wrong for a reason that has nothing to do with the thing under test.
    """
    db = _TillDB()
    monkeypatch.setattr(finance, "_get_db", lambda: db)
    monkeypatch.setattr(
        finance,
        "_cash_sales_for_window",
        lambda *a, **k: (CASH_SALES, CASH_REFUNDS),
    )
    monkeypatch.setattr(finance, "_refund_double_entry_advisory", lambda *a, **k: None)
    monkeypatch.setattr(finance, "_store_name_map", lambda db_: {STORE: "Till Store"})
    return db


def _client(*roles):
    app = FastAPI()
    app.include_router(finance.router, prefix="/api/v1/finance")

    async def _u():
        return _session_user(*roles)

    app.dependency_overrides[get_current_user] = _u
    return TestClient(app)


def _preview(role):
    resp = _client(role).get(f"/api/v1/finance/cash-register/sessions?store_id={STORE}")
    assert resp.status_code == 200, resp.text
    prev = resp.json()["expected_preview"]
    assert prev is not None, "fixture broken: no open session, nothing under test"
    return prev


ALL_ROLES = ["SUPERADMIN", "ADMIN", "ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"]


# ===========================================================================
# 1. THE DRAWER ARITHMETIC. The money test, and the reason for the whole change.
# ===========================================================================


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_wage_bill_is_not_subtracted_from_the_drawer(till_db, role):
    """THE REQUIREMENT, stated as money.

    Before the fix cash_expenses was 73567.89 (rent + wages) and `expected` was
    NEGATIVE, so an honest count of the till came back as a phantom overage of
    a month's wages. Assert the figures, not the absence of a key.
    """
    prev = _preview(role)
    assert prev["cash_expenses"] == RENT_CASH, (
        f"{role} sees cash_expenses={prev['cash_expenses']}; expected only the "
        f"rent ({RENT_CASH}). A wage bill has been subtracted from a till."
    )
    assert prev["expected"] == EXPECTED_RIGHT
    assert prev["expected"] != EXPECTED_WRONG


@pytest.mark.parametrize("role", ALL_ROLES)
def test_an_ordinary_cash_expense_is_still_deducted(till_db, role):
    """POSITIVE CONTROL, and the one that stops the lazy fix.

    Excluding EVERY expense would pass the test above and quietly break the
    drawer for rent, chai, courier and every other real payout. Rent paid in
    cash must still come out.
    """
    prev = _preview(role)
    assert prev["cash_expenses"] == RENT_CASH
    assert prev["cash_expenses"] > 0, "every cash payout has vanished from the drawer"


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_drawer_identity_still_holds(till_db, role):
    """opening + sales - refunds - expenses = expected, recomputed from the
    body's own fields. If a future edit changes one figure without the others
    the panel stops tying out and this fails."""
    prev = _preview(role)
    recomputed = round(
        prev["opening_float"]
        + prev["cash_sales"]
        - prev["cash_refunds"]
        - prev["cash_expenses"]
        - prev["bank_deposit"],
        2,
    )
    assert recomputed == prev["expected"]


def test_a_drawer_with_no_pay_head_is_completely_untouched(monkeypatch):
    """The innocent day. Nothing about this change may move a store that never
    booked a pay head -- no advisory, no arithmetic drift."""
    db = _TillDB(expenses=[_expense("ZZ-X1", "Rent", RENT_CASH, "CASH")])
    monkeypatch.setattr(finance, "_get_db", lambda: db)
    monkeypatch.setattr(
        finance, "_cash_sales_for_window", lambda *a, **k: (CASH_SALES, CASH_REFUNDS)
    )
    monkeypatch.setattr(finance, "_refund_double_entry_advisory", lambda *a, **k: None)

    prev = _preview("STORE_MANAGER")
    assert prev["cash_expenses"] == RENT_CASH
    assert prev["expected"] == EXPECTED_RIGHT
    assert prev["off_till_expense_advisory"] is False
    assert prev["off_till_expense_message"] is None


def test_salary_advance_recovery_is_not_mistaken_for_a_pay_head(monkeypatch):
    """"Salary advance recovery" is money coming BACK from a customer/employee
    and is an ordinary head. The shared matcher is exact-match for precisely
    this reason; assert it through the drawer, not through the matcher."""
    db = _TillDB(
        expenses=[_expense("ZZ-X9", "Salary advance recovery", RENT_CASH, "CASH")]
    )
    monkeypatch.setattr(finance, "_get_db", lambda: db)
    monkeypatch.setattr(
        finance, "_cash_sales_for_window", lambda *a, **k: (CASH_SALES, CASH_REFUNDS)
    )
    monkeypatch.setattr(finance, "_refund_double_entry_advisory", lambda *a, **k: None)

    prev = _preview("STORE_MANAGER")
    assert prev["cash_expenses"] == RENT_CASH
    assert prev["off_till_expense_advisory"] is False


# ===========================================================================
# 2. NO ROLE-CONDITIONAL DRAWER. The admin case is a REQUIREMENT here.
# ===========================================================================


def test_every_role_is_shown_the_same_drawer(till_db):
    """The whole point of doing this as a classification fix rather than a
    redaction: if ADMIN saw a payroll-INCLUSIVE drawer and a manager saw a
    payroll-EXCLUSIVE one, that difference IS the wage bill and we would have
    built a third asymmetry instead of removing one."""
    seen = {}
    for role in ALL_ROLES:
        prev = _preview(role)
        seen[role] = (prev["cash_expenses"], prev["expected"])
    assert len(set(seen.values())) == 1, f"the drawer differs by role: {seen}"


def test_no_pair_of_roles_can_difference_out_the_wage_bill(till_db):
    """The attacker's question rather than the reviewer's: take every figure
    every role can see on this route and confirm no signed difference lands on
    the wage bill."""
    pools = {}
    for role in ALL_ROLES:
        prev = _preview(role)
        pools[role] = [
            float(v) for v in prev.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
    for a in ALL_ROLES:
        for b in ALL_ROLES:
            for x in pools[a]:
                for y in pools[b]:
                    assert abs(abs(x - y) - WAGES) > 0.005, (
                        f"{a} figure {x} minus {b} figure {y} is the wage bill"
                    )


# ===========================================================================
# 3. THE EXCLUSION IS VISIBLE, AND CARRIES NO NUMBER.
# ===========================================================================


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_counter_is_told_something_was_left_out(till_db, role):
    """A number a human counts money against must never be adjusted behind
    their back -- same class as the false 'coming online' screens PR #960
    killed."""
    prev = _preview(role)
    assert prev["off_till_expense_advisory"] is True
    assert prev["off_till_expense_message"] == finance.OFF_TILL_EXPENSE_MESSAGE
    assert "till" in prev["off_till_expense_message"].lower()


@pytest.mark.parametrize("role", ["ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"])
def test_the_note_never_names_the_head_or_its_size(till_db, role):
    """The note says THAT something is out, never WHAT or HOW MUCH. If a future
    edit helpfully appends the amount, this fails."""
    prev = _preview(role)
    note = prev["off_till_expense_message"]
    for word in ("salary", "salaries", "wage", "wages", "payroll", "pf", "esi"):
        assert word not in note.lower(), f"the note names the withheld head: {word}"
    for figure in (WAGES, round(WAGES, 0), int(WAGES)):
        assert str(figure) not in note
    assert "71234" not in note and "71,234" not in note


# ===========================================================================
# 4. THE CLOSE HANDLER -- the figure that gets PERSISTED and signed off.
# ===========================================================================


def _close(role="STORE_MANAGER"):
    return _client(role).post(
        "/api/v1/finance/cash-register/close",
        json={
            "session_id": "ZZ-CR-OPEN",
            "denominations": [{"face": 500, "pieces": 87, "kind": "note"}],
            "tolerance": 0,
        },
    )


@pytest.mark.parametrize("role", ALL_ROLES)
def test_the_close_persists_a_payroll_free_expected_figure(till_db, role):
    """The close record is what the reconciliation console reads back and what
    a manager signs off, so the corrected figure has to be the one STORED, not
    merely the one previewed."""
    resp = _close(role)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cash_expenses"] == RENT_CASH
    assert body["expected"] == EXPECTED_RIGHT
    assert body["off_till_expense_advisory"] is True
    assert body["off_till_expense_message"] == finance.OFF_TILL_EXPENSE_MESSAGE

    stored = till_db.cols["cash_register_sessions"].find_one(
        {"session_id": "ZZ-CR-OPEN"}
    )
    assert stored["cash_expenses"] == RENT_CASH
    assert stored["expected"] == EXPECTED_RIGHT
    assert stored["off_till_expense_advisory"] is True


def test_the_close_no_longer_reports_a_phantom_verdict(till_db):
    """Before the fix `expected` was negative (EXPECTED_WRONG), which tripped
    the NEGATIVE_EXPECTED withholding path -- a cash-in-missing warning on an
    ordinary day. The corrected drawer produces an ordinary verdict."""
    body = _close("STORE_MANAGER").json()
    assert body["expected"] > 0
    assert body["negative_expected_advisory"] is False
    # 87 x 500 = 43500 counted against 43666.67 expected -> a real, small short.
    assert body["variance"] == round(43500.0 - EXPECTED_RIGHT, 2)


def test_the_reconciliation_console_carries_the_note_on_the_closed_row(till_db):
    """End to end: close through the endpoint, then read the manager console,
    and confirm the row is payroll-free AND says something was left out."""
    assert _close("STORE_MANAGER").status_code == 200
    resp = _client("STORE_MANAGER").get(
        f"/api/v1/finance/cash-reconciliation-summary?from={DAY}&to={_TOMORROW}"
        f"&store_id={STORE}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows = [r for r in body["rows"] if r["session_id"] == "ZZ-CR-OPEN"]
    assert rows, f"the closed session is missing from the console: {body}"
    row = rows[0]
    assert row["cash_expenses"] == RENT_CASH
    assert row["off_till_expense_advisory"] is True
    assert body["totals"]["cash_expenses"] == RENT_CASH


# ===========================================================================
# 5. THE DELIBERATE CHOICE: an EXPLICIT cash payment mode does NOT re-admit it.
# ===========================================================================


def test_an_explicitly_cash_pay_head_is_still_excluded(monkeypatch):
    """Option (b), chosen on purpose and recorded here so the next reviewer
    inherits the decision instead of re-deriving it.

    Honouring an explicit CASH mode would keep the drawer right in the case
    where it genuinely happened, but it would put the wage bill back in a
    figure the manager tier reads -- and it would make that leak switchable by
    whoever types the expense. The owner's ruling is unconditional, so an
    explicit CASH mode on a pay head is a mis-booking. Its failure mode (that
    drawer reads SHORT) is loud, and the advisory above says why.
    """
    db = _TillDB(
        expenses=[
            _expense("ZZ-X1", "Rent", RENT_CASH, "CASH"),
            _expense("ZZ-X2", "Staff Salaries", WAGES, "CASH"),
        ]
    )
    monkeypatch.setattr(finance, "_get_db", lambda: db)
    monkeypatch.setattr(
        finance, "_cash_sales_for_window", lambda *a, **k: (CASH_SALES, CASH_REFUNDS)
    )
    monkeypatch.setattr(finance, "_refund_double_entry_advisory", lambda *a, **k: None)

    prev = _preview("STORE_MANAGER")
    assert prev["cash_expenses"] == RENT_CASH
    assert prev["expected"] == EXPECTED_RIGHT
    assert prev["off_till_expense_advisory"] is True


def test_a_non_cash_ordinary_expense_is_still_ignored(monkeypatch):
    """Unchanged behaviour, asserted because the projection grew a field: a
    BANK-mode rent never touched the drawer and still must not."""
    db = _TillDB(expenses=[_expense("ZZ-X1", "Rent", RENT_CASH, "BANK")])
    monkeypatch.setattr(finance, "_get_db", lambda: db)
    monkeypatch.setattr(
        finance, "_cash_sales_for_window", lambda *a, **k: (CASH_SALES, CASH_REFUNDS)
    )
    monkeypatch.setattr(finance, "_refund_double_entry_advisory", lambda *a, **k: None)

    prev = _preview("STORE_MANAGER")
    assert prev["cash_expenses"] == 0.0


# ===========================================================================
# 5b. *** THIS TEST PINS A GAP ON PURPOSE. *** The one thing the manager tier
#     can still read, why it is deliberate, and why it is empty in production.
# ===========================================================================


def test_a_session_closed_before_this_change_keeps_its_old_figure(monkeypatch):
    """THE RESIDUAL, asserted rather than hidden -- same standard as
    test_the_accountants_seal_is_deliberately_incomplete_and_here_is_where.

    /finance/cash-reconciliation-summary READS the close record; it does not
    recompute it. So a session CLOSED BEFORE this change, in a window that
    contained a payroll-shaped cash expense, still reports the payroll-
    INCLUSIVE `cash_expenses` it was signed off with, and a manager could
    difference that against today's /finance/pnl.

    WHY IT IS LEFT ALONE, deliberately:
      * The row has to tie out. `expected_cash` on that row is the figure a
        human counted real notes against on that day, and the row's own
        arithmetic is opening + sales - refunds - expenses = expected.
        Restating `cash_expenses` under today's rules without restating
        `expected` breaks the row visibly; restating `expected` rewrites a
        signed-off drawer. Both are worse than the leak.
      * The leak is EMPTY in production. It needs a payroll-shaped expense
        booked AND a till closed over it BEFORE this ships. Production has 0
        expense documents and 0 payroll documents today, so no such row can
        exist. Every close from here on is payroll-free at source.

    If a row like this ever does appear, the fix is a one-off data correction
    with the owner in the room, not a read-time rewrite. Delete this test then.
    """
    legacy = {
        "session_id": "ZZ-CR-LEGACY",
        "store_id": STORE,
        "status": "CLOSED",
        "opened_at": OPENED_AT,
        "closed_at": f"{DAY}T21:00:00+05:30",
        "opening_float": OPENING_FLOAT,
        "cash_sales": CASH_SALES,
        "cash_refunds": 0.0,
        # The payroll-INCLUSIVE figure a pre-fix close would have written.
        "cash_expenses": round(RENT_CASH + WAGES, 2),
        "expected": EXPECTED_WRONG,
        "counted": EXPECTED_WRONG,
        "tolerance": 0.0,
    }
    db = _TillDB()
    db.cols["cash_register_sessions"] = _Col([legacy])
    monkeypatch.setattr(finance, "_get_db", lambda: db)
    monkeypatch.setattr(finance, "_store_name_map", lambda db_: {STORE: "Till Store"})

    resp = _client("STORE_MANAGER").get(
        f"/api/v1/finance/cash-reconciliation-summary?from={DAY}&to={DAY}"
        f"&store_id={STORE}"
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()["rows"][0]
    assert row["cash_expenses"] == round(RENT_CASH + WAGES, 2), (
        "a pre-existing close record is now being restated at read time. If "
        "that was deliberate, check `expected_cash` on the same row still ties "
        "out to it -- otherwise the console now shows a drawer that never "
        "existed. Read this test's docstring before changing it."
    )
    # And the row still ties out to the figure it was signed off with, which is
    # the property that makes leaving it alone the right call.
    assert round(
        row["opening_float"] + row["cash_sales"] - row["cash_refunds"]
        - row["cash_expenses"] - row["bank_deposit"], 2
    ) == row["expected_cash"]


# ===========================================================================
# 6. ONE MATCHER, and a TRIPWIRE on the two policy rows.
# ===========================================================================


def test_the_till_route_uses_the_shared_matcher_by_identity():
    """Not "a matcher that behaves the same" -- THE matcher. A fifth private
    copy is how /budgets kept emitting the head after /finance stopped."""
    assert (
        finance.is_payroll_shaped_expense
        is salary_visibility.is_payroll_shaped_expense
    )


def _policy_row(method, path):
    rows = [
        r
        for r in rbac_policy.POLICY
        if r.get("method") == method and r.get("path") == path
    ]
    assert len(rows) == 1, f"expected exactly one policy row for {method} {path}"
    return rows[0]


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/finance/cash-register/close"),
        ("GET", "/api/v1/finance/cash-register/sessions"),
    ],
)
def test_the_till_policy_rows_are_frozen_at_the_manager_tier(method, path):
    """TRIPWIRE, so the next reviewer inherits the decision.

    These two rows admit the SAME four roles as /finance/pnl over the SAME
    store. That is exactly why the round-1 asymmetry was exploitable, and it is
    also why the fix had to be a classification correction rather than a role
    gate: this route family legitimately belongs to the people who count the
    drawer. If somebody widens these rows, the sealing argument in PR #985
    changes and has to be re-made. If somebody NARROWS them, a store manager
    can no longer close their own till -- a live-ops outage. Either way, read
    the PR before changing this list.
    """
    row = _policy_row(method, path)
    assert sorted(row["allowed"]) == [
        "ACCOUNTANT",
        "ADMIN",
        "AREA_MANAGER",
        "STORE_MANAGER",
    ], f"{method} {path} role list changed: {row['allowed']}"
    assert row.get("store_scoped") is True


def test_the_reconciliation_console_row_is_frozen_too():
    row = _policy_row("GET", "/api/v1/finance/cash-reconciliation-summary")
    assert sorted(row["allowed"]) == [
        "ACCOUNTANT",
        "ADMIN",
        "AREA_MANAGER",
        "STORE_MANAGER",
    ]
    assert row.get("store_scoped") is True
