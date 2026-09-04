"""BUG-104 tripwire, TEST side: no business date seeded from a UTC face.

WHY THIS FILE EXISTS
====================
The production side of BUG-104 is guarded by
``test_no_naive_midnight_range_bounds.py`` and
``test_no_raw_calendar_derivations.py``. Neither looks at the tests
themselves -- and the tests are where the class bit next.

On 2026-08-26 the backend suite was red every night between 00:00 and 05:30
IST and green all day. Fifteen tests, three files, one cause: the FIXTURES
seeded a business date from the UTC FACE of a clock read, while the code --
correctly, since PR #999 -- windowed on the IST BUSINESS DAY. The box runs UTC,
so between 00:00 and 05:30 IST (18:30-24:00 UTC the day before) the seed said
one day and the query asked for the next. Nothing was wrong with production.
CI simply could not merge anything overnight, which is when the owner works.

Two more of the same shape were found by sweeping the clock across a MONTH
boundary rather than only across tonight's hour: they were not failing today,
they were waiting for the 1st.

WHAT THIS SCANS FOR
===================
Inside ``backend/tests/`` only, on CODE ONLY (every string and comment token is
blanked before matching, so prose about the trap does not trip it)::

    .now().date()      .utcnow().date()
    .now().strftime(   .utcnow().strftime(
    .today()
    [:10]

Those are the ways a UTC-framed instant gets frozen into a day FACE. Each hit
must be a deliberate, reasoned entry in ALLOWED below, and the claim "no test
seeds a business date off the UTC clock" IS the empty result of
(scan minus ALLOWED), re-executed on every CI run.

HOW TO FIX A NEW HIT (do not just add it to ALLOWED)
====================================================
Keep seeding stored columns with ``datetime.now()`` -- that IS the frame
``created_at`` is written in, and changing it would make the fixture lie about
production. Convert only where you mean the BUSINESS day::

    from tests.ist_business_day import business_day, business_now
    ...
    "expense_date": business_day(session["opened_at"])   # the till's IST day
    f"?from_date={business_day(now)}"                    # the report's IST day

``ist_business_day`` hand-rolls the +05:30 and deliberately does NOT import
``api.utils.ist``: a fixture that asks the code under test what the right
answer is can never catch that code being wrong (feedback_hollow_tests calls
this "assertions true by construction").

WHAT THIS GUARD HONESTLY CANNOT DO
==================================
1. It cannot tell a day face that is COMPARED against an IST window from one
   that is only a relative offset (``date.today() + timedelta(days=7)`` fed to
   a "not in the future" validator). That judgement is what ALLOWED is for,
   and why every entry has to state it.
2. It cannot see a HARD-CODED late-UTC literal -- ``datetime(2026, 7, 21, 19, 0)``
   asserted to be the 21st is the same bug with no clock read to grep for.
   Those are found by RUNNING, not by grepping: shift the process clock into
   the 00:00-05:30 IST band (and onto the 1st of a month) and watch. The
   ``IMS_TEST_IST_CLOCK`` hook in ``tests/conftest.py`` does exactly that::

       IMS_TEST_IST_CLOCK=01:40              pytest tests/...
       IMS_TEST_IST_CLOCK="2026-09-01 01:40" pytest tests/...

   A CI job that runs the suite once at a shifted clock would close (2)
   properly; this guard is the cheap tripwire, not a substitute for it.
3. ``[:10]`` is a blunt arm: it also matches a uuid-hex slice, which is why
   four such lines sit in ALLOWED. Narrowing it would lose ``opened_at[:10]``
   -- the exact line that started this.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import io
import os
import re
import tokenize

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

_PATTERN = re.compile(
    r"\.now\(\)\.date\(\)"
    r"|\.utcnow\(\)\.date\(\)"
    r"|\.now\(\)\.strftime\("
    r"|\.utcnow\(\)\.strftime\("
    r"|\.today\(\)"
    r"|\[:10\]"
)

# This guard's own scanner names the patterns it hunts for, and the helper
# module is the sanctioned way OUT of the trap -- neither is a seed.
#
# The two sibling guards keep PRODUCTION source lines as allow-list keys. Those
# keys are string literals, so the token-blanking below already hides them; the
# files are named here as well so a later refactor that moves such a key out of
# a string cannot silently start tripping this guard on another guard's data.
_SKIP_FILES = {
    os.path.basename(__file__),
    "ist_business_day.py",
    "test_no_naive_midnight_range_bounds.py",
    "test_no_raw_calendar_derivations.py",
}

# ---------------------------------------------------------------------------
# THE ALLOW-LIST. Keyed by (test file, exact stripped source line); every entry
# says why a UTC-vs-IST day shift cannot change that assertion. Verified
# empirically: every file below passes with the process clock at 01:40, 05:29,
# 05:31 and 12:00 IST, and at 2026-09-01 01:40 IST (a month start inside the
# band).
#
# "Same frame as production" entries are NOT an endorsement of production's
# frame -- two of them record a live BUG-104 residue in shipped code, named in
# the reason. When that code moves to the IST day, its test moves with it and
# the entry goes away.
# ---------------------------------------------------------------------------

_REL = (
    "RELATIVE OFFSET, not a business day. The value goes to a validator that "
    "compares it against ITS OWN clock read, and the assertion is about the "
    "SIGN of the difference (past / today / future), not about which calendar "
    "day it is. A whole-day frame shift moves both sides together."
)

_HEX = (
    "NOT A DATE. A uuid4 hex slice used to build a unique test id. The [:10] "
    "arm of the pattern exists for `stamp[:10]` day faces -- the exact line "
    "that started this -- and cannot tell a hex slice apart. KEEP THESE FOUR: "
    "they are the only f-string hits in the scan, so they double as the "
    "cross-version canary for the PEP 701 tokenizer split described in "
    "_code_only. If the scanner ever stops looking inside f-strings on some "
    "Python, `test_the_allow_list_has_not_gone_stale` says so immediately -- "
    "which is how that drift was caught the first time, on CI's 3.10/3.11."
)

ALLOWED = {
    # -- uuid hex slices ----------------------------------------------------
    (
        "test_cataloguer_attribution.py",
        '"store_id": f"S-attrib-{uuid.uuid4().hex[:10]}",',
    ): _HEX,
    (
        "test_inventory_correctness.py",
        '"barcode": f"BC-{uuid.uuid4().hex[:10]}",',
    ): _HEX,
    ("test_inventory_quantity.py", '"barcode": f"BC-{uuid.uuid4().hex[:10]}",'): _HEX,
    (
        "test_stock_count_lifecycle.py",
        'bc = f"BC-{uuid.uuid4().hex[:10]}"',
    ): _HEX,
    ("test_stock_count_blind.py", 'bc = f"BC-{uuid.uuid4().hex[:10]}"'): _HEX,
    # -- validators that only care about past / today / future --------------
    (
        "test_customer_validation.py",
        "_YESTERDAY = date.today() - timedelta(days=1)",
    ): _REL,
    ("test_customer_validation.py", "_TODAY = date.today()"): (
        _REL + " The 'today is accepted' case is the boundary itself: the rule "
        "is `dob <= the validator's own today`, so both sides shift as one."
    ),
    (
        "test_customer_validation.py",
        "_TOMORROW = date.today() + timedelta(days=1)",
    ): _REL,
    (
        "test_order_delivery_and_cart_discount.py",
        "future = date.today() + timedelta(days=7)",
    ): _REL,
    ("test_orders_hardening.py", "today = date.today()"): _REL,
    ("test_orders_hardening.py", "future = date.today() + timedelta(days=30)"): _REL,
    ("test_orders_hardening.py", "past = date.today() - timedelta(days=1)"): _REL,
    ("test_pos_workshop_autolink.py", "far = date.today() + timedelta(days=400)"): _REL,
    ("test_pos_workshop_autolink.py", "soon = date.today() + timedelta(days=30)"): _REL,
    (
        "test_pos_workshop_autolink.py",
        "delivery_date=date.today() - timedelta(days=1),",
    ): _REL,
    (
        "test_unification_5_ensure_customer.py",
        "future = date.today() + timedelta(days=10)",
    ): _REL,
    (
        "test_unification_customer_guards.py",
        "CustomerUpdate(dob=date.today() + timedelta(days=1))",
    ): _REL,
    (
        "test_marketing_correctness.py",
        "yesterday = (date.today() - timedelta(days=1)).isoformat()",
    ): _REL,
    ("test_marketing_correctness.py", "today = date.today().isoformat()"): _REL,
    (
        "test_marketing_correctness.py",
        "future_date = (date.today() + timedelta(days=7)).isoformat()",
    ): _REL,
    # -- leave notice: a DIFFERENCE in days, both ends off one clock ---------
    (
        "test_f26_remote_approval.py",
        "self.from_date = date.today() + timedelta(days=from_days_ahead)",
    ): (
        "NOTICE PERIOD, a difference. `_is_fast_path_leave` measures from_date "
        "minus its own today against the policy threshold, so both ends move "
        "together under a frame shift."
    ),
    ("test_f26_remote_approval.py", "self.to_date = date.today() + timedelta("): (
        "Continuation of the leave-window construction above; same difference."
    ),
    (
        "test_f26_remote_approval.py",
        'assert hr_router._is_fast_path_leave("CASUAL", date.today() + timedelta(days=1), "S1") is True',
    ): ("1 day of notice against a 2-day threshold -- a difference, not a day."),
    (
        "test_f26_remote_approval.py",
        'assert hr_router._is_fast_path_leave("SICK", date.today(), "S1") is True',
    ): ("Zero notice against a 2-day threshold -- a difference, not a day."),
    (
        "test_f26_remote_approval.py",
        'assert hr_router._is_fast_path_leave("EARNED", date.today() + timedelta(days=1), "S1") is False',
    ): (
        "Asserts the leave TYPE gate, not the date; the date is 1 day of "
        "notice either way."
    ),
    (
        "test_f26_remote_approval.py",
        'assert hr_router._is_fast_path_leave("CASUAL", date.today() + timedelta(days=5), "S1") is False',
    ): ("5 days of notice against a 2-day threshold -- a difference, not a day."),
    (
        "test_f26_remote_approval.py",
        'assert hr_router._is_fast_path_leave("CASUAL", date.today() + timedelta(days=5), "S1") is True',
    ): (
        "The same 5-day notice re-asserted under a monkeypatched 7-day "
        "threshold; still a difference."
    ),
    # -- deliberate widening -------------------------------------------------
    (
        "test_off_till_expenses.py",
        "_TOMORROW = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()",
    ): (
        "DELIBERATE WIDENING, explained in a 12-line comment at the site. One "
        "test closes a session through the REAL endpoint, so closed_at comes "
        "off the server clock; the summary window is stretched to TOMORROW so "
        "the row is inside it whichever side of midnight the box is on. "
        "Widening cannot make the assertion pass falsely -- that test's "
        "subject is the CONTENT of the closed row, and date filtering is "
        "pinned separately by the seeded-row test in the same file."
    ),
    # -- the day is the payload, not the query -------------------------------
    ("test_points.py", "return _d.today().isoformat()"): (
        "The points-sheet date is SUPPLIED IN THE PAYLOAD, and points.py:525 "
        "honours a supplied `date` (`date or ist_today()`), so the value is "
        "just an arbitrary business-date string the test also asserts on. The "
        "router's own IST default is covered by the frozen-clock test in this "
        "same file."
    ),
    ("test_points.py", "return (_d.today() - timedelta(days=1)).isoformat()"): (
        "Yesterday's sheet, supplied in the payload for the same reason -- a "
        "second distinct day, not a query against an IST window."
    ),
    # -- walkouts: the router's IST clock is frozen to match ------------------
    ("test_walkouts.py", "return _d.today().isoformat()"): (
        "This file installs `frozen_walkouts_now`, which freezes the walkouts "
        "router's IST clock precisely so UTC-seeded today/yesterday rows line "
        "up; the trap is written out at that fixture."
    ),
    ("test_walkouts.py", "return (_d.today() - timedelta(days=1)).isoformat()"): (
        "Yesterday counterpart of the line above, under the same frozen router "
        "clock."
    ),
    ("test_walkouts.py", "now = _d.today()"): (
        "Feeds `?year=&month=` for the walk-ins MTD roll-up, and the rows it "
        "counts are seeded off the SAME clock read, so both land in whichever "
        "month that is. Verified at 2026-09-01 01:40 IST."
    ),
    # -- same frame as production --------------------------------------------
    (
        "test_prescription_backdate.py",
        'today_prefix = datetime.now().strftime("%Y-%m-%d")',
    ): (
        "SAME FRAME AS PRODUCTION: prescriptions.py stamps the default "
        "`prescription_date` with a naive `datetime.now()`, so the assertion "
        "reads back exactly what the writer wrote. Whether a clinical Rx date "
        "ought to be the IST business day is a production question, not a "
        "fixture one."
    ),
    # -- neither a seed nor a query ------------------------------------------
    (
        "test_rpt5_rpt6_analytics.py",
        'stub.to_date_str = lambda v: str(v)[:10] if v else ""',
    ): (
        "A STUB standing in for `api.utils.dates.to_date_str`, reproducing "
        "that helper's documented string pass-through. It seeds nothing."
    ),
    ("test_workshop_productivity.py", "today = datetime.now().date()"): (
        "Builds a window entirely in the PAST (today-10 .. today-5) to assert "
        "that ZERO jobs are counted. The seeded completed_at values come off "
        "the same clock read, and the assertion is emptiness, which a one-day "
        "shift of both sides cannot break."
    ),
}


_FSTRING = re.compile(r"^[a-zA-Z]*[fF]['\"]")


def _code_only(path):
    """(raw lines, code-only lines) -- string and comment tokens blanked.

    Blanking them is what lets this guard scan test files that TALK about the
    trap, and sibling guards that carry production source as data, without
    tripping on the prose.

    F-STRINGS ARE LEFT ALONE, and that is a VERSION-COMPATIBILITY fix, not a
    preference. PEP 701 changed the tokenizer in 3.12: from then on an f-string
    arrives as FSTRING_START / FSTRING_MIDDLE plus real code tokens for each
    ``{...}``, but on 3.10 and 3.11 the whole thing is ONE STRING token. Blank
    STRING blindly and this guard sees different files on the CI matrix
    (3.10/3.11) than on a 3.12+ laptop -- which is exactly how it first went
    red: three `f"...{uuid.uuid4().hex[:10]}"` lines were hits locally and
    invisible on CI, so their allow-list entries read as stale. Skipping
    f-strings on every version makes the scan identical everywhere.

    The cost is that the LITERAL half of an f-string is also scanned, so an
    f-string whose text happens to contain e.g. ".today()" is a false positive.
    That is the safe direction: it asks for a reasoned allow-list entry rather
    than quietly missing a seed.
    """
    with io.open(path, "rb") as fh:
        tokens = list(tokenize.tokenize(fh.readline))
    raw = io.open(path, encoding="utf-8", errors="replace").read().splitlines()
    grid = {i + 1: list(line) for i, line in enumerate(raw)}
    for tok in tokens:
        if tok.type not in (tokenize.STRING, tokenize.COMMENT):
            continue
        if tok.type == tokenize.STRING and _FSTRING.match(tok.string):
            continue
        (r1, c1), (r2, c2) = tok.start, tok.end
        for row in range(r1, r2 + 1):
            if row not in grid:
                continue
            lo = c1 if row == r1 else 0
            hi = c2 if row == r2 else len(grid[row])
            for col in range(lo, min(hi, len(grid[row]))):
                grid[row][col] = " "
    return raw, {k: "".join(v) for k, v in grid.items()}


def _scan():
    found = []
    for name in sorted(os.listdir(TESTS_DIR)):
        if not name.endswith(".py") or name in _SKIP_FILES:
            continue
        raw, code = _code_only(os.path.join(TESTS_DIR, name))
        for lineno in sorted(code):
            if _PATTERN.search(code[lineno]):
                found.append((name, raw[lineno - 1].strip(), lineno))
    return found


def test_no_test_seeds_a_business_date_from_a_utc_face():
    """The completeness claim: (scan minus ALLOWED) is EMPTY."""
    unexpected = [
        "%s:%d  %s" % (name, lineno, code)
        for name, code, lineno in _scan()
        if (name, code) not in ALLOWED
    ]
    assert not unexpected, (
        "BUG-104: a test derives a day FACE from the box clock (UTC) outside "
        "the reasoned allow-list.\n"
        "If that day is compared against an IST BUSINESS-DAY window -- a till "
        "session's expenses, a /sales report's from_date/to_date, a courier "
        "date, a month-to-date total -- the test passes all day and fails "
        "every night between 00:00 and 05:30 IST. Keep seeding stored columns "
        "with datetime.now() (that IS the stored frame) and convert only the "
        "business day:\n"
        "    from tests.ist_business_day import business_day, business_now\n"
        "Only a genuinely frame-free site (a relative offset, a uuid slice, a "
        "value that shares production's own frame) may be added to ALLOWED, "
        "with the reason spelled out.\n\n" + "\n".join(unexpected)
    )


def test_the_allow_list_has_not_gone_stale():
    """Every ALLOWED entry must still match a live line, or the list becomes a
    graveyard nobody can audit."""
    live = {(name, code) for name, code, _ in _scan()}
    stale = sorted(
        "%s  %s" % (name, code) for (name, code) in ALLOWED if (name, code) not in live
    )
    assert not stale, (
        "these ALLOWED entries no longer match any line -- the site was fixed "
        "or moved, so delete or update the entry:\n" + "\n".join(stale)
    )


def test_every_allow_list_entry_states_a_reason():
    thin = sorted(
        "%s  %s" % (name, code)
        for (name, code), reason in ALLOWED.items()
        if len(reason.strip()) < 60
    )
    assert not thin, (
        "an exemption without a real reason is how this class comes back:\n"
        + "\n".join(thin)
    )
