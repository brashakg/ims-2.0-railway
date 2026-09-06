"""BUG-104 tripwire: no NEW raw calendar derivation off a stored instant.

WHY THIS GUARD EXISTS, AND WHY IT IS THIS NARROW
------------------------------------------------
Four rounds of BUG-104 each fixed the sites they were handed and each left one
behind, because the sites are spread over 40+ routers and nothing connected
them. The recurring shape is always the same:

    <a field holding a STORED INSTANT>  ->  a calendar day / month / FY

and the fix is always one of two rules:

    VALUE  the derived day is DISPLAYED or EXPORTED  -> ist_date_str  (+5:30)
    BOUND  the value is COMPARED against stored instants -> ist_day_start_utc
           / ist_month_window_utc (the bound moves BACKWARD)

A guard that simply grepped for ``.date()`` / ``.strftime("%Y-%m")`` / a 10- or
7-character slice would be USELESS -- backend/ has hundreds of those, almost
all of them on values that are not stored instants at all (``expense_date``,
``bill_date``, ``due_date``, ``session_date`` are operator-typed business-date
STRINGS) or on a day derived from the clock rather than from a row. An
allow-list of that size would be appended to without thought and would be worse
than no guard.

So the pattern is anchored on the FIELD NAME instead: a derivation only trips
this guard when a stored-instant field is named on the same line, AND the line
does not already route through an ist.py helper. Across the whole backend that
leaves the small list below -- every entry a deliberate decision, each with its
reason. The allow-list IS the closing table for BUG-104, kept in code.

If this test goes red you have added the 5th round's site. Do not add a line to
ALLOWED to make it green until you have answered: is this day DISPLAYED (VALUE,
move it forward) or COMPARED (BOUND, move it backward)? If it is genuinely
neither -- an internal key whose other side is the same box clock, or a dedupe
key already persisted on live rows -- add it WITH the reason.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import io
import os
import re

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fields that hold a STORED INSTANT (a naive datetime.now() == the UTC wall
# clock on Railway), as opposed to an operator-typed business-date string.
_INSTANT_FIELDS = (
    "created_at",
    "updated_at",
    "completed_at",
    "accepted_at",
    "result_set_at",
    "detected_at",
    "last_purchase",
    "paid_at",
    "issued_at",
)

# Raw calendar derivations: the calendar day, the month key, the FY test.
_DERIVATIONS = (
    r"\.date\(\)",
    r"\.strftime\(",
    r"\[:10\]",
    r"\[:7\]",
    r"\.month\b",
    r"\.weekday\(\)",
    r"\.year\b",
)

_PATTERN = re.compile(
    "(?:%s)[^\n]{0,90}?(?:%s)"
    % ("|".join(_INSTANT_FIELDS), "|".join(_DERIVATIONS))
)

# A line that already goes through ist.py has made its choice of frame.
_ALREADY_IST = ("ist_date_str(", "ist_day_start_utc(", "ist_month_window_utc(", "now_ist")

_SKIP_FILES = {"seed_data_OLD.py"}
_SKIP_DIRS = {"tests", "__pycache__", ".git", "node_modules", ".venv"}


# ---------------------------------------------------------------------------
# THE ALLOW-LIST. Keyed by (relative path, the exact stripped source line) so
# it survives edits ABOVE the site and breaks loudly when the site itself is
# rewritten. Each entry carries the reason it is TABLED.
# ---------------------------------------------------------------------------

ALLOWED = {
    (
        "api/routers/analytics.py",
        'if to_date_str(c.get("created_at")) >= start_date.date().isoformat()',
    ): (
        "New-vs-returning customer split. `start_date` comes from "
        "analytics.get_date_range() == datetime.now(), the SAME naive box "
        "clock created_at is stored in, so both sides of this comparison are "
        "already in one frame. The same start/end pair is ALSO handed to "
        "_fetch_orders_in_window as Mongo bounds, which must move the OPPOSITE "
        "way -- converting the stored side alone would relocate the error, not "
        "remove it. See the ruling in get_date_range's docstring."
    ),
    (
        "api/routers/analytics.py",
        'if to_date_str(c.get("created_at")) < start_date.date().isoformat()',
    ): (
        "Same comparison, the returning-customer half of the same split. Same "
        "ruling: `start_date` is the naive box clock, so both sides already "
        "share one frame and the Mongo bounds built from the same pair must "
        "move the opposite way."
    ),
    (
        "api/routers/finance/bank_statement.py",
        'pmt_date = (pmt.get("payment_date") or pmt.get("created_at") or "")[:10]',
    ): (
        "Bank-reconciliation fuzzy matcher. The primary source is the "
        "operator-entered `payment_date` business-date string; created_at is "
        "only a fallback for a row missing it, and the comparison (_dt_close) "
        "is a plus/minus-days tolerance against the bank statement's own date, "
        "so 5h30m is well inside it."
    ),
    (
        "api/routers/follow_ups.py",
        'test_date = datetime.fromisoformat(test["created_at"]).date()',
    ): (
        "Eye-test reminder. The day is never displayed; it feeds "
        "`reminder_date`, which is compared against date.today() (the same "
        "box day) AND used as the dedupe key `scheduled_date`, already "
        "persisted on live rows. Shifting it would miss those keys and raise a "
        "SECOND reminder for ~8 percent of tests -- a customer called twice -- "
        "to buy a reminder one day earlier on a 365-day horizon. Exactness "
        "needs a migration that re-keys the stored values."
    ),
    (
        "api/routers/jarvis.py",
        'created = str(o.get("created_at") or "")[:10]',
    ): (
        "Jarvis today/this-month/last-year revenue roll-up. The bounds it is "
        "compared against (`today`, `month_start`, `ly_month_*`) are all built "
        "from the same datetime.now() a few lines above, so both sides are in "
        "the box frame. Converting one side only would move the boundary."
    ),
    (
        "api/routers/jarvis.py",
        '"created_at": {"$gte": datetime.now().strftime("%Y-%m-01")},',
    ): (
        "vendor_returns 'this month' COUNT in the Jarvis context blob. "
        "vendor_returns.created_at is a naive-UTC ISO STRING "
        "(vendor_returns.py writes datetime.now().isoformat(), no repository), "
        "so this string bound and the stored strings are in ONE frame today. "
        "Making it IST means a BOUND moving backward, which for a string "
        "column means emitting a full '...T18:30:00' timestamp rather than a "
        "'%Y-%m-01' prefix -- a different fix shape from every other site, and "
        "it belongs with a decision about that collection's storage frame. The "
        "value here is a count in an AI briefing, not a document."
    ),
    (
        "api/routers/payout.py",
        "if not (df <= created_at[:10] <= dt):",
    ): (
        "Payout fallback branch only reached when the $aggregate above RAISED, "
        "and 0 of 934 production orders store created_at as a string, so it "
        "selects nothing today. Fixing it properly means parsing the string to "
        "its naive-UTC instant and comparing against start_dt/next_first -- NOT "
        "shifting df/dt, which would break points_log MTD."
    ),
    (
        "api/routers/vendors/performance.py",
        'when = str(b.get("bill_date") or b.get("created_at") or "")[:7]',
    ): (
        "Vendor month-to-date spend. The primary source `bill_date` is already "
        "an IST business-date STRING, and the created_at fallback on "
        "vendor_bills is a naive-UTC ISO string that carries no shiftable "
        "frame -- ist_date_str passes both through unchanged by design. The "
        "half that WAS wrong here was the month PREFIX it is compared against, "
        "which now reads the IST clock."
    ),
    (
        "api/services/nba_call_list.py",
        'sub_headlines.append(f"Last purchase {str(last_purchase)[:10]}")',
    ): (
        "`last_purchase_date` / `last_order_date` are pre-computed fields on "
        "the customer document, not raw instants. Their frame is set by "
        "whatever writes them; correcting it belongs with that writer, and "
        "shifting a value of unknown frame here would corrupt it."
    ),
    (
        "api/services/nba_call_list.py",
        '"last_purchase_date": str(last_purchase)[:10] if last_purchase else None,',
    ): (
        "Same pre-computed `last_purchase_date` customer field as the headline "
        "above, emitted verbatim. Same ruling: its frame belongs to whatever "
        "writes it, and shifting a value of unknown frame here would corrupt "
        "it rather than correct it."
    ),
}


def _scan():
    found = []
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            if not name.endswith(".py") or name in _SKIP_FILES:
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, BACKEND).replace(os.sep, "/")
            with io.open(path, encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    code = line.strip()
                    if code.startswith("#") or not code:
                        continue
                    if any(tok in line for tok in _ALREADY_IST):
                        continue
                    if _PATTERN.search(line):
                        found.append((rel, code, lineno))
    return found


def test_no_new_raw_calendar_derivation_off_a_stored_instant():
    """Fails on a NEW site, not on the ones already triaged."""
    unexpected = [
        "%s:%d  %s" % (rel, lineno, code)
        for rel, code, lineno in _scan()
        if (rel, code) not in ALLOWED
    ]
    assert not unexpected, (
        "BUG-104: a calendar day / month / financial year is being derived "
        "from a STORED INSTANT without going through api/utils/ist.py.\n\n"
        + "\n".join(unexpected)
        + "\n\nDecide which of the two rules applies:\n"
        "  VALUE  the day is DISPLAYED or EXPORTED (a chart label, a date on a\n"
        "         document, anything an outside party reads) -> ist_date_str,\n"
        "         which moves it FORWARD +5:30.\n"
        "  BOUND  the value is COMPARED against stored created_at instants\n"
        "         -> ist_day_start_utc / ist_month_window_utc, which move the\n"
        "         bound BACKWARD.\n"
        "BOTH SIDES OF A COMPARISON MUST END UP IN THE SAME FRAME.\n"
        "If the site is genuinely neither (an internal key whose other side is\n"
        "the same box clock, or a dedupe key already persisted on live rows),\n"
        "add it to ALLOWED in this file WITH the reason."
    )


def test_the_allow_list_has_not_gone_stale():
    """Every ALLOWED entry must still exist. Otherwise the list quietly grows
    a graveyard of sites nobody can find, and the next reader cannot tell a
    live exemption from a dead one."""
    live = {(rel, code) for rel, code, _ in _scan()}
    stale = sorted(
        "%s  %s" % (rel, code) for (rel, code) in ALLOWED if (rel, code) not in live
    )
    assert not stale, (
        "these ALLOWED entries no longer match any line -- the site was fixed "
        "or moved, so delete the entry:\n" + "\n".join(stale)
    )


def test_every_allow_list_entry_states_a_reason():
    thin = sorted(
        "%s  %s" % (rel, code)
        for (rel, code), reason in ALLOWED.items()
        if len(reason.strip()) < 60
    )
    assert not thin, (
        "an exemption without a real reason is how round 5 happens:\n"
        + "\n".join(thin)
    )
