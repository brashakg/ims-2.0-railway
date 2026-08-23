"""BUG-104 round-3 tripwire: no naive-midnight window over a stored instant.

THE COMPLETENESS CLAIM, TRUE BY CONSTRUCTION
--------------------------------------------
Rounds 1 and 2 both claimed "every site is fixed" and were both wrong,
because the claim lived in a PR comment instead of the repo. This guard makes
it structural: it greps backend/ (tests excluded) for every line matching

    datetime.combine   |   datetime.min.time   |   datetime.max.time
    datetime(<...>, 1) |   23, 59, 59          |   .replace(day=1)

-- the constructions every one of the round-3 AND round-4 misses shared (an
operator-typed calendar day or month expanded to a NAIVE-midnight bound and
compared against a stored naive-UTC instant; the commission ledger's
month-literal window and the MoM denominator's first-of-month derivation on
an already-shifted instant were the round-4 finds) -- and requires each hit
to be a deliberate, reasoned entry in ALLOWED below. The claim "no typed date range
still bounds at naive midnight against a stored instant" IS the empty result
of (scan minus ALLOWED), re-executed on every CI run.

WHAT BELONGS IN ALLOWED (and nothing else):
  * WRITES of an operator-typed business DATE stored, by convention, as the
    midnight datetime of that IST calendar day (points/kicker daily sheets,
    walkout dates, order delivery dates). Both writer and every reader treat
    the value as a calendar day; shifting these would CREATE the bug.
  * BOUNDS over a column that itself holds such a business-date value
    (expense_date, attendance date, prescription_date) -- calendar-day
    bounds over calendar-day values are one frame already.
  * Same-value derivations (attendance lateness: minutes-of-day within the
    check-in's own frame) and windows already expressed in IST before the
    combine (collection_insights).
  * Dead helpers with no caller, kept only until their own cleanup.

A window over orders.created_at / audit timestamp / any BaseRepository
_add_timestamps column must NEVER appear here: that is the BOUND rule --
ist_day_start_utc(from_date) .. ist_day_start_utc(to_date + 1 day) - 1us.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import io
import os
import re

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PATTERN = re.compile(
    r"datetime\.combine|datetime\.min\.time|datetime\.max\.time"
    # Round-4 widening (the commission-ledger window's shapes): a month
    # literal `datetime(<y>, <m>, 1)`, a `... 23, 59, 59` end bound, and
    # `.replace(day=1)` -- which is CORRECT on an IST calendar date fed to
    # ist_day_start_utc (the BOUND rule itself) but the bug when applied to
    # an already-shifted naive-UTC instant (the MoM denominator defect).
    r"|datetime\([^)]*,\s*1\s*\)"
    r"|23,\s*59,\s*59"
    r"|\.replace\(day=1\)"
)

_SKIP_DIRS = {"tests", "__pycache__", ".git", "node_modules", ".venv"}

# ---------------------------------------------------------------------------
# THE ALLOW-LIST. Keyed by (relative path, exact stripped source line); every
# entry carries the reason its calendar frame is CORRECT. If this test goes
# red you have either added a new naive-midnight window (fix it with the
# BOUND rule, do not add it here) or moved one of these lines (update it).
# ---------------------------------------------------------------------------

ALLOWED = {
    (
        "api/routers/kicker.py",
        '"date": datetime.combine(payload.date, datetime.min.time()),',
    ): (
        "WRITE of the operator-typed kicker sheet day: stored by convention "
        "as the midnight datetime of that IST calendar day. Writer and "
        "readers share the calendar frame; shifting would corrupt the day."
    ),
    (
        "api/routers/points.py",
        'row["date"] = datetime.combine(payload.date, datetime.min.time())',
    ): (
        "WRITE of the operator-typed daily points-sheet day (points_log.date "
        "feeds date_str MTD scoring, a calendar label by design -- see "
        "payout._month_window's docstring). Calendar frame is correct."
    ),
    (
        "api/routers/orders.py",
        "expected_delivery = datetime.combine(",
    ): (
        "WRITE of the operator-promised delivery DATE (a business day the "
        "staff told the customer, not an instant). ist.py's module docstring "
        "names this exact site as the business-date frame example."
    ),
    (
        "api/routers/orders.py",
        "order.delivery_date, datetime.min.time()",
    ): (
        "Continuation line of the expected_delivery write above -- same "
        "operator-typed business-date frame."
    ),
    (
        "api/routers/walkouts.py",
        '"date": datetime.combine(target_date, datetime.min.time()),',
    ): (
        "WRITE of the walkout's business day (operator-chosen calendar "
        "date). Calendar frame by design."
    ),
    (
        "api/routers/walkouts.py",
        '"scheduled_date": datetime.combine(payload.scheduled_date, datetime.min.time()),',
    ): (
        "WRITE of the operator-typed follow-up date -- a promise for an IST "
        "calendar day, stored as its midnight datetime by convention."
    ),
    (
        "api/routers/walkouts.py",
        "new_val = datetime.combine(val, datetime.min.time())",
    ): (
        "Normalises an edited walkout date field back to the same "
        "midnight-datetime storage convention as the two writes above."
    ),
    (
        "api/services/attendance_engine.py",
        "shift_start_dt = datetime.combine(ci.date(), start)",
    ): (
        "Lateness = minutes-of-day between the check-in and the shift's "
        "HH:MM on the CHECK-IN'S OWN calendar date -- both operands derive "
        "from the same value's frame, so no cross-frame comparison exists."
    ),
    (
        "api/services/attendance_engine.py",
        "cutoff_dt = datetime.combine(ci.date(), cutoff)",
    ): (
        "Same minutes-of-day construction as the shift-start line: the "
        "half-day cutoff HH:MM on the check-in's own calendar date."
    ),
    (
        "api/services/collection_insights.py",
        "window_start_ist = datetime.combine(ist_today - timedelta(days=days - 1), time.min)",
    ): (
        "Already the IST frame: the midnight combined here is an IST day "
        "start and the very next line subtracts _IST_OFFSET to express it "
        "as the naive-UTC bound. This is the BOUND rule, hand-rolled."
    ),
    (
        "api/utils/ist.py",
        "a ``datetime.combine(date, min.time())`` expected_delivery, etc.).",
    ): (
        "Docstring text in the IST helper module itself, naming the "
        "business-date storage convention. Not executable code."
    ),
    (
        "database/repositories/audit_repository.py",
        'filter["timestamp"] = {"$gte": datetime.combine(from_date, datetime.min.time())}',
    ): (
        "find_by_store: DEAD helper -- no caller anywhere in backend/ (the "
        "live Activity Log path is settings._audit_time_filter, IST-shifted "
        "in round 3). Kept until the repo helper cleanup; a future caller "
        "must go through an IST-shifted bound instead."
    ),
    (
        "database/repositories/audit_repository.py",
        'filter.setdefault("timestamp", {})["$lte"] = datetime.combine(to_date, datetime.max.time())',
    ): (
        "Second half of the dead find_by_store helper above. Same ruling."
    ),
    (
        "database/repositories/audit_repository.py",
        "start = datetime.combine(dt, datetime.min.time())",
    ): (
        "get_activity_summary: DEAD helper -- no caller anywhere in "
        "backend/. Same ruling as find_by_store."
    ),
    (
        "database/repositories/audit_repository.py",
        "end = datetime.combine(dt, datetime.max.time())",
    ): (
        "Second half of the dead get_activity_summary helper. Same ruling."
    ),
    (
        "database/repositories/expense_repository.py",
        '"$gte": datetime.combine(from_date, datetime.min.time()),',
    ): (
        "Bounds over expense_date -- an operator-typed business DATE, not a "
        "stored instant. Calendar bounds over a calendar value are one frame."
    ),
    (
        "database/repositories/expense_repository.py",
        '"$lte": datetime.combine(to_date, datetime.max.time())',
    ): (
        "Upper half of the expense_date calendar bound above. Same ruling."
    ),
    (
        "database/repositories/hr_repository.py",
        '"date": datetime.combine(dt, datetime.min.time())',
    ): (
        "Attendance `date` is the IST business day of the attendance row "
        "(calendar value); midnight-datetime is its storage convention. "
        "Appears three times in this file (exact-line key matches all)."
    ),
    (
        "database/repositories/hr_repository.py",
        '"$gte": datetime.combine(from_date, datetime.min.time()),',
    ): (
        "Range bound over the attendance business-day column above -- "
        "calendar bounds over a calendar value."
    ),
    (
        "database/repositories/hr_repository.py",
        '"$lte": datetime.combine(to_date, datetime.max.time())',
    ): (
        "Upper half of the attendance calendar bound over the business-day "
        "`date` column above -- calendar bounds over a calendar value."
    ),
    (
        "database/repositories/hr_repository.py",
        '"date": datetime.combine(dt, datetime.min.time()),',
    ): (
        "Same attendance business-day write convention, trailing-comma "
        "variant."
    ),
    (
        "database/repositories/prescription_repository.py",
        'filter["prescription_date"] = {"$gte": datetime.combine(from_date, datetime.min.time())}',
    ): (
        "prescription_date is the operator-typed clinical business DATE "
        "(the day of the eye test as written on the Rx), not a stored "
        "instant -- calendar bounds are the correct frame. Appears twice "
        "(find_by_optometrist + find_by_store); exact-line key matches both."
    ),
    (
        "database/repositories/prescription_repository.py",
        'filter.setdefault("prescription_date", {})["$lte"] = datetime.combine(to_date, datetime.max.time())',
    ): (
        "Upper half of the prescription_date calendar bound; two "
        "occurrences, same ruling."
    ),
    (
        "database/repositories/prescription_repository.py",
        '"$gte": datetime.combine(from_date, datetime.min.time()),',
    ): (
        "get_optometrist_stats: same prescription_date calendar bound "
        "inside the aggregation $match."
    ),
    (
        "database/repositories/prescription_repository.py",
        '"$lte": datetime.combine(to_date, datetime.max.time())',
    ): (
        "Upper half of the get_optometrist_stats calendar bound over the "
        "prescription_date business-date column -- same ruling as above."
    ),
    # -----------------------------------------------------------------------
    # Round-4 widening survivors: .replace(day=1). The rule of thumb -- on an
    # IST calendar DATE feeding ist_day_start_utc it IS the BOUND rule; on a
    # date feeding a date-string filter it is one calendar frame; only on an
    # already-shifted naive-UTC instant is it the MoM-denominator bug.
    # -----------------------------------------------------------------------
    (
        "api/routers/analytics_v2.py",
        "from_date = ist_day_start_utc(today.replace(day=1))",
    ): (
        "today is ist_today() (an IST calendar date); first-of-month then "
        "ist_day_start_utc IS the BOUND rule, applied correctly."
    ),
    (
        "api/routers/dashboard_widgets.py",
        "return ist_day_start_utc(ist_today().replace(day=1))",
    ): (
        "IST calendar date -> first of IST month -> shifted to the naive-UTC "
        "bound. The BOUND rule itself."
    ),
    (
        "api/routers/dashboard_widgets.py",
        "month_start = ist_day_start_utc(t.replace(day=1))",
    ): (
        "t is an IST calendar date; same BOUND-rule construction as the "
        "helper above it in this file."
    ),
    (
        "api/routers/expenses.py",
        "month_start = on_date.replace(day=1)",
    ): (
        "on_date is the expense's operator-typed business DATE; the monthly "
        "spend cap window filters expense_date (a calendar value) with it -- "
        "one calendar frame on both sides."
    ),
    (
        "api/routers/finance.py",
        "start = ist_day_start_utc(today.replace(day=1))",
    ): (
        "today is ist_today(); BOUND rule applied correctly (occurs in "
        "get_revenue and the cash-flow month window; exact-line key matches "
        "both)."
    ),
    (
        "api/routers/finance.py",
        "prev_first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)",
    ): (
        "The round-4 MoM FIX: previous month derived in the CALENDAR frame "
        "(today is ist_today(), a date) and only then converted via "
        "ist_day_start_utc. Pinned by "
        "test_revenue_mom_previous_window_tiles_with_the_current_month."
    ),
    (
        "api/routers/finance.py",
        "start = ist_day_start_utc(now.replace(day=1).date())",
    ): (
        "now is now_ist_naive() -- IST WALL-CLOCK, not a shifted UTC "
        "instant; .replace(day=1).date() lands on the 1st of the IST month, "
        "then the BOUND rule converts. Correct frame (AR/AP + P&L monthly)."
    ),
    (
        "api/routers/finance.py",
        'start_day = (from_date or today.replace(day=1).isoformat())[:10]',
    ): (
        "Default for a DATE-STRING filter over date_str columns (day-book): "
        "today is ist_today(), the string stays in the calendar frame "
        "end-to-end."
    ),
    (
        "api/routers/jarvis.py",
        "month_start = ist_day_start_utc(ist_today().replace(day=1))",
    ): (
        "IST calendar date -> first of month -> BOUND rule. Three "
        "occurrences in this file (MTD revenue probes); exact-line key "
        "matches all."
    ),
    (
        "api/routers/payroll.py",
        "from_dt = ist_day_start_utc(today.replace(day=1))",
    ): (
        "today is ist_today(); the commission LEADERBOARD month window, "
        "fixed in round 2 with the BOUND rule -- this is the fix, not the "
        "bug."
    ),
    (
        "api/routers/points.py",
        "prev_from = prev_last.replace(day=1).isoformat()",
    ): (
        "prev_last is a datetime.date; produces a DATE-STRING bound for "
        "points_log.date_str, which is an IST business-day string -- one "
        "calendar frame."
    ),
    (
        "api/routers/points.py",
        "df = date_from or today.replace(day=1).isoformat()",
    ): (
        "Same date-string frame as above: today is ist_today(), the default "
        "MTD lower bound for the date_str column."
    ),
    (
        "api/routers/reports.py",
        "start = (now.replace(day=1) - timedelta(days=1)).replace(day=1)",
    ): (
        "now is now_ist_naive() -- IST wall-clock. Used for date_str "
        "filters and month labels (calendar frame), and the created_at "
        "bound derived from it is shifted through the BOUND rule five lines "
        "below (see the BUG-104 comment at the site)."
    ),
    (
        "api/routers/reports.py",
        "start = (start - timedelta(days=1)).replace(day=1)",
    ): (
        "Walks further months back within the SAME IST wall-clock frame as "
        "the line above; same ruling."
    ),
    (
        "api/routers/walkouts.py",
        "mtd_from = now.replace(day=1).isoformat()",
    ): (
        "now is ist_today() (see the BUG-104 comment beside it); date-string "
        "MTD bound over walkout date columns -- calendar frame end-to-end."
    ),
    (
        "api/services/ticker_service.py",
        "month_start = ist_day_start_utc(today.replace(day=1))",
    ): (
        "today is ist_today(); BOUND rule applied correctly for the ticker "
        "MTD window."
    ),
    (
        "api/services/ticker_service.py",
        "days_in_month = (next_month_first - today.replace(day=1)).days",
    ): (
        "Pure calendar arithmetic between two IST calendar dates (days in "
        "the current IST month); never a query bound."
    ),
    # -----------------------------------------------------------------------
    # Round-4 widening survivors: month literals datetime(<y>, <m>, 1) and
    # 23:59:59 end bounds. Each is a calendar-frame value bounding a
    # calendar-frame column (or a label), never a stored naive-UTC instant.
    # -----------------------------------------------------------------------
    (
        "api/routers/crm.py",
        "window = (datetime.now() - datetime(2020, 1, 1)).days // 30",
    ): (
        "Coarse month-count since a fixed epoch anchor for cohort bucketing "
        "-- both operands share one local frame and the result is a "
        "duration, never a query bound."
    ),
    (
        "api/routers/finance.py",
        "bill_start = datetime(y, m, 1)",
    ): (
        "ITC register: bill_date is an operator-typed CALENDAR date (the "
        "date printed on the vendor bill), not a stored instant -- the "
        "comment at the site rules the frame. Calendar bounds are correct."
    ),
    (
        "api/routers/finance.py",
        "bill_end = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)",
    ): (
        "Upper half of the bill_date calendar month bound above -- same "
        "ruling."
    ),
    (
        "api/routers/finance.py",
        "event_date = datetime(y, m, 1)",
    ): (
        "Cash-flow FORECAST: a projected future first-of-month used only as "
        "a display label (.date().isoformat()) for recurring outflows -- "
        "never compared against a stored instant."
    ),
    (
        "api/routers/finance.py",
        "start = datetime(y, m, 1)",
    ): (
        "Budget actuals: expenses are dated on expense_date, a date-only "
        "'YYYY-MM-DD' STRING; these datetimes only seed date-string month "
        "bounds in the $match below (see the comment at the site). Calendar "
        "frame on both sides."
    ),
    (
        "api/routers/finance.py",
        "end = datetime(y, m + 1 if m < 12 else 1, 1) if m < 12 else datetime(y + 1, 1, 1)",
    ): (
        "Upper half of the budget-actuals expense_date calendar bound above "
        "-- same ruling."
    ),
    (
        "api/routers/finance.py",
        "fy_start = datetime(fy_year, 4, 1)",
    ): (
        "Journal-entry FY guard: compared against _je_cal_day(s), which is "
        "BY DESIGN a calendar-frame datetime (its docstring rules the "
        "frame). One frame on both sides of the comparison."
    ),
    (
        "api/routers/reports.py",
        "start_date = datetime(year, month, 1)",
    ): (
        "Attendance month report: attendance rows are keyed on the "
        "business-day `date` column (calendar-midnight storage convention, "
        "see the hr_repository entries above) -- calendar month bounds over "
        "a calendar column."
    ),
    (
        "api/routers/reports.py",
        "end_date = datetime(year + 1, 1, 1)",
    ): (
        "December arm of the attendance calendar month bound above -- same "
        "ruling."
    ),
    (
        "api/routers/reports.py",
        "end_date = datetime(year, month + 1, 1)",
    ): (
        "Non-December arm of the attendance calendar month bound above -- "
        "same ruling."
    ),
    (
        "api/routers/vendors.py",
        "fy_start = datetime(target_fy, 4, 1)",
    ): (
        "26Q TDS export: payment_date is a date-only ISO STRING; the bound "
        "is used date-only precisely so '2026-04-01' is not dropped (the "
        "comment at the site records the defect this fixed). Calendar frame."
    ),
    (
        "api/routers/vendors.py",
        "fy_end = datetime(target_fy + 1, 3, 31, 23, 59, 59)",
    ): (
        "Upper half of the 26Q payment_date calendar FY bound above -- a "
        "date-string comparison, never a stored-instant window."
    ),
    (
        "api/utils/ist.py",
        "``datetime(year, month, 1)`` bound silently excludes every order placed",
    ): (
        "Docstring text in the IST helper module describing the round-4 "
        "commission-ledger defect. Not executable code."
    ),
    (
        "database/repositories/hr_repository.py",
        '"$gte": datetime(year, 1, 1),',
    ): (
        "Leave from_date is an operator-typed business DATE stored by the "
        "calendar-midnight convention; year bounds over it are one calendar "
        "frame. Two occurrences; exact-line key matches both."
    ),
    (
        "database/repositories/hr_repository.py",
        '"$lt": datetime(year + 1, 1, 1)',
    ): (
        "Upper half of the leave from_date calendar year bound above -- "
        "same ruling, two occurrences."
    ),
}


def _scan():
    found = []
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, BACKEND).replace(os.sep, "/")
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    code = line.strip()
                    if code.startswith("#") or not code:
                        continue
                    if _PATTERN.search(line):
                        found.append((rel, code, lineno))
    return found


def test_no_new_naive_midnight_window():
    """The round-3 completeness claim: (scan minus ALLOWED) is EMPTY."""
    unexpected = [
        "%s:%d  %s" % (rel, lineno, code)
        for rel, code, lineno in _scan()
        if (rel, code) not in ALLOWED
    ]
    assert not unexpected, (
        "BUG-104: a datetime.combine / min.time / max.time appeared outside "
        "the reasoned allow-list. If it bounds a STORED INSTANT column "
        "(orders.created_at, audit timestamp, anything _add_timestamps "
        "writes) from an operator-typed calendar day, apply the BOUND rule:\n"
        "    from_dt = ist_day_start_utc(from_date)\n"
        "    to_dt = ist_day_start_utc(to_date + timedelta(days=1)) "
        "- timedelta(microseconds=1)\n"
        "Only a genuine calendar-frame site (business-date write or bound "
        "over a business-date column) may be added to ALLOWED, with the "
        "reason.\n\n" + "\n".join(unexpected)
    )


def test_the_allow_list_has_not_gone_stale():
    """Every ALLOWED entry must still match a live line, or the list grows a
    graveyard nobody can audit."""
    live = {(rel, code) for rel, code, _ in _scan()}
    stale = sorted(
        "%s  %s" % (rel, code) for (rel, code) in ALLOWED if (rel, code) not in live
    )
    assert not stale, (
        "these ALLOWED entries no longer match any line -- the site was "
        "fixed or moved, so delete or update the entry:\n" + "\n".join(stale)
    )


def test_every_allow_list_entry_states_a_reason():
    thin = sorted(
        "%s  %s" % (rel, code)
        for (rel, code), reason in ALLOWED.items()
        if len(reason.strip()) < 60
    )
    assert not thin, (
        "an exemption without a real reason is how round 4 happens:\n"
        + "\n".join(thin)
    )
