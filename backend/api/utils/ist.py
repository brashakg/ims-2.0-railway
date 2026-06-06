"""IST (Asia/Kolkata) clock helpers for reporting / period / financial-year math.

BUG-104. Railway runs in UTC; the business calendar is IST (UTC+5:30). Two facts
make naive ``datetime.now()`` wrong for any business-day / period / FY boundary:

1. ``datetime.now()`` on the box returns the UTC wall-clock, so "today" /
   "this month" / "1-Apr financial-year" computed from it are 5h30m behind IST.
   Between 00:00-05:30 IST this lands on the PREVIOUS IST day/month, and a sale
   at 1-Apr 02:00 IST gets a PRIOR-FY GST invoice serial (Rule 46(b) violation).

2. ``created_at`` / ``updated_at`` are stored as NAIVE ``datetime.now()`` == UTC
   wall-clock. So an IST day boundary used to FILTER created_at must be the
   equivalent NAIVE-UTC instant (IST-midnight is 18:30 UTC the previous day), or
   the comparison silently mixes frames.

Use:
- ``now_ist()`` -> tz-aware IST "now" (for month/year ints, %Y-%m labels, FY test,
  scheduler/agent hour-of-day decisions).
- ``ist_today()`` -> IST calendar date.
- ``now_ist_naive()`` -> IST wall-clock as a NAIVE datetime (compare against naive
  IST-wall-clock fields, e.g. a HH:MM shift time or a naive expected_delivery).
- ``ist_day_start_utc(d)`` -> NAIVE-UTC instant of IST-midnight for IST date ``d``
  (the >= bound when range-filtering naive-UTC ``created_at`` by IST day).
- ``fy_start_year_ist(dt)`` -> Indian financial-year start year (FY starts 1 Apr).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta, date as _date
from typing import Optional

# Resolve IST once at import: zoneinfo is preferred; the fixed +05:30 offset is an
# exact fallback (India has no DST), so this never degrades to UTC.
try:  # pragma: no cover - trivial import guard
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    IST = timezone(timedelta(hours=5, minutes=30), name="IST")

_UTC = timezone.utc


def now_ist() -> datetime:
    """Current instant as a tz-aware IST datetime."""
    return datetime.now(IST)


def ist_today() -> _date:
    """Current IST calendar date."""
    return now_ist().date()


def now_ist_naive() -> datetime:
    """Current IST wall-clock as a NAIVE datetime.

    For comparing against fields stored as naive IST wall-clock (shift HH:MM,
    a ``datetime.combine(date, min.time())`` expected_delivery, etc.).
    """
    return now_ist().replace(tzinfo=None)


def to_utc_naive(dt_aware: datetime) -> datetime:
    """Convert a tz-aware datetime to the equivalent NAIVE-UTC instant.

    Naive-UTC is the frame ``created_at`` is stored in.
    """
    return dt_aware.astimezone(_UTC).replace(tzinfo=None)


def ist_day_start_utc(d: Optional[_date] = None) -> datetime:
    """NAIVE-UTC instant of IST-midnight for IST date ``d`` (default: IST today).

    Use as the ``$gte`` bound when filtering naive-UTC ``created_at`` by IST day:
    ``{"created_at": {"$gte": ist_day_start_utc()}}`` selects today's IST orders.
    """
    if d is None:
        d = ist_today()
    return to_utc_naive(datetime(d.year, d.month, d.day, tzinfo=IST))


def fy_start_year_ist(dt: Optional[datetime] = None) -> int:
    """Indian financial-year START year for an instant (FY starts 1 April, IST).

    1-Apr-2026 IST -> 2026; 31-Mar-2026 IST -> 2025. Pass a tz-aware ``dt`` to tag
    a specific event; default is IST now.
    """
    if dt is None:
        dt = now_ist()
    return dt.year if dt.month >= 4 else dt.year - 1
