"""IST helper (BUG-104). Verifies the naive-UTC<->IST conversions + the
financial-year boundary that the GST invoice serial depends on."""
import os
import sys
from datetime import datetime, date

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.utils.ist import (  # noqa: E402
    IST,
    now_ist,
    ist_today,
    now_ist_naive,
    to_utc_naive,
    ist_day_start_utc,
    fy_start_year_ist,
)


def test_now_ist_is_tz_aware_and_offset():
    n = now_ist()
    assert n.tzinfo is not None
    assert n.utcoffset().total_seconds() == 5.5 * 3600


def test_now_ist_naive_strips_tz():
    assert now_ist_naive().tzinfo is None


def test_ist_today_is_a_date():
    assert isinstance(ist_today(), date)


def test_ist_day_start_utc_is_1830_prev_day():
    # IST-midnight of 2026-06-06 == 2026-06-05 18:30:00 in UTC, returned NAIVE
    got = ist_day_start_utc(date(2026, 6, 6))
    assert got == datetime(2026, 6, 5, 18, 30, 0)
    assert got.tzinfo is None


def test_to_utc_naive_roundtrips_ist_midnight():
    ist_midnight = datetime(2026, 6, 6, 0, 0, tzinfo=IST)
    assert to_utc_naive(ist_midnight) == datetime(2026, 6, 5, 18, 30, 0)


def test_fy_start_year_ist_april_boundary():
    # FY starts 1 Apr IST.
    assert fy_start_year_ist(datetime(2026, 4, 1, 0, 0, tzinfo=IST)) == 2026
    assert fy_start_year_ist(datetime(2026, 3, 31, 23, 59, tzinfo=IST)) == 2025
    assert fy_start_year_ist(datetime(2026, 12, 31, tzinfo=IST)) == 2026
    assert fy_start_year_ist(datetime(2027, 1, 1, tzinfo=IST)) == 2026


def test_fy_serial_early_1apr_ist_lands_in_new_fy():
    # The BUG-104 GST-serial bug: a sale at 1-Apr 02:00 IST (= 31-Mar 20:30 UTC)
    # must serialise into FY starting 2026, NOT the prior FY 2025.
    dt = datetime(2026, 4, 1, 2, 0, tzinfo=IST)
    assert fy_start_year_ist(dt) == 2026


# ---------------------------------------------------------------------------
# ist_date_str_from_stored -- the shape-agnostic IST day.
#
# This is the helper the Tally voucher <DATE> goes through, so its answer IS a
# date on a dated accounting document. It was shipped exercised only through
# ONE naive-string shape via that path; every other branch (the Z-strip, an
# offset, date-only, the unparseable fallback) was untested. The rows below
# are hand-computed IST days, never re-derived through the helper.
#
# THE SAFETY PROPERTY, stated as a test: for a string input this helper is
# either RIGHT or exactly as wrong as the ist_date_str it replaced -- never
# wronger. Python 3.10 (CI runs 3.10 and 3.11) rejects some ISO forms the
# newer interpreters accept; those rows fall back to the first ten characters,
# which is precisely the old behaviour, so the property holds on every
# interpreter.
# ---------------------------------------------------------------------------

import pytest  # noqa: E402
from api.utils.ist import (  # noqa: E402
    ist_date_str,
    ist_date_str_from_stored,
)


# 2026-05-07T18:30:00 naive-UTC IS IST midnight on the 8th: the seam.
_SHAPES_THAT_MUST_GIVE_8_MAY = [
    "2026-05-07T19:00:00",          # 00:30 IST 8 May -- the whole point
    "2026-05-07T18:30:00",          # IST midnight exactly, inclusive
    "2026-05-08T04:30:00",          # 10:00 IST, an ordinary afternoon
    "2026-05-07 19:00:00",          # space separator (BSON str() shape)
    "2026-05-07T19:00:00.123456",   # microseconds
    "2026-05-07T19:00:00+00:00",    # aware UTC
    "2026-05-08T00:30:00+05:30",    # already IST, aware
    "2026-05-07T19:00:00Z",         # Z-suffixed
    "  2026-05-07T19:00:00  ",      # whitespace-padded
    datetime(2026, 5, 7, 19, 0),    # naive datetime
    datetime(2026, 5, 8, 0, 30, tzinfo=IST),  # aware IST datetime
]


@pytest.mark.parametrize("value", _SHAPES_THAT_MUST_GIVE_8_MAY)
def test_stored_shapes_all_resolve_to_the_same_ist_day(value):
    """The SHAPE a row happens to be stored in must never change the business
    day it is reported under. Every value above is the same instant (or the
    same IST day) written differently."""
    assert ist_date_str_from_stored(value) == "2026-05-08", value


def test_the_second_before_ist_midnight_is_the_previous_day():
    """Positive control on the seam: one second earlier is 7 May, not 8."""
    assert ist_date_str_from_stored("2026-05-07T18:29:59") == "2026-05-07"


def test_blank_and_missing_stay_blank():
    assert ist_date_str_from_stored(None) == ""
    assert ist_date_str_from_stored("") == ""


def test_unparseable_keeps_the_old_first_ten_characters():
    """Fail-soft, and identical to what ist_date_str returned before -- so an
    interpreter that rejects an exotic ISO form degrades to the OLD answer
    rather than inventing a new one."""
    assert ist_date_str_from_stored("not-a-date") == "not-a-date"
    assert ist_date_str_from_stored("2026-05-07Twobble") == "2026-05-07"


@pytest.mark.parametrize(
    "value",
    ["2026-05-07T19:00:00", "2026-05-07T19:00:00Z", "2026-05-08", "not-a-date"],
)
def test_it_is_never_wronger_than_the_helper_it_replaced(value):
    """THE SAFETY PROPERTY. For any string, the answer is either the correct
    IST day or exactly what ist_date_str would have said. It can improve on
    the old helper; it can never be a third, novel answer."""
    new = ist_date_str_from_stored(value)
    old = ist_date_str(value)
    assert new == "2026-05-08" or new == old, (value, new, old)


def test_a_non_string_non_datetime_gets_no_fabricated_date():
    """An int epoch or a stray list has no ISO face. It must come back blank
    (an empty <DATE> fails a Tally import loudly) rather than coerced to
    '1746646200', which would look like data."""
    assert ist_date_str_from_stored(1746646200) == ""
    assert ist_date_str_from_stored([]) == ""


def test_a_date_object_is_unchanged():
    """A date carries no instant to shift."""
    assert ist_date_str_from_stored(date(2026, 5, 8)) == "2026-05-08"
