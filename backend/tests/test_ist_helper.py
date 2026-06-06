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
