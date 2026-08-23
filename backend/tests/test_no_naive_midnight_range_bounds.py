"""BUG-104 round-3 tripwire: no naive-midnight window over a stored instant.

THE COMPLETENESS CLAIM, TRUE BY CONSTRUCTION
--------------------------------------------
Rounds 1 and 2 both claimed "every site is fixed" and were both wrong,
because the claim lived in a PR comment instead of the repo. This guard makes
it structural: it greps backend/ (tests excluded) for every line matching

    datetime.combine   |   datetime.min.time   |   datetime.max.time

-- the exact construction every one of the round-3 misses shared (an
operator-typed calendar day expanded to a NAIVE-midnight bound and compared
against a stored naive-UTC instant) -- and requires each hit to be a
deliberate, reasoned entry in ALLOWED below. The claim "no typed date range
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
