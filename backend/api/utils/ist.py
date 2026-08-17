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
- ``ist_date_str(value)`` -> the IST calendar DAY of an ALREADY-STORED instant, as
  'YYYY-MM-DD' (for a date an outside party reads: a courier, Tally, an
  accountant). See its own docstring for when NOT to use it.
- ``now_ist_naive()`` -> IST wall-clock as a NAIVE datetime (compare against naive
  IST-wall-clock fields, e.g. a HH:MM shift time or a naive expected_delivery).
- ``ist_day_start_utc(d)`` -> NAIVE-UTC instant of IST-midnight for IST date ``d``
  (the >= bound when range-filtering naive-UTC ``created_at`` by IST day).
- ``fy_start_year_ist(dt)`` -> Indian financial-year start year (FY starts 1 Apr).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta, date as _date
from typing import Any, Optional

from .dates import to_date_str

# Resolve IST once at import: zoneinfo is preferred; the fixed +05:30 offset is an
# exact fallback (India has no DST), so this never degrades to UTC.
try:  # pragma: no cover - trivial import guard
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    IST = timezone(timedelta(hours=5, minutes=30), name="IST")

_UTC = timezone.utc

# India has no DST, so the offset is a constant. Used to shift a NAIVE stored
# instant (UTC wall clock) onto the IST wall clock before taking its day.
_IST_OFFSET = timedelta(hours=5, minutes=30)


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


def ist_date_str(value: Any) -> str:
    """IST calendar day ('YYYY-MM-DD') of an ALREADY-STORED instant.

    WHAT IT IS FOR
    --------------
    A stored ``created_at`` is a NAIVE ``datetime.now()`` == the UTC wall clock
    (Railway runs UTC). Taking ``.date()`` off it therefore yields the UTC
    calendar day, and for any instant in the **00:00-05:30 IST window** that is
    the PREVIOUS day. Use this wherever the resulting day is a BUSINESS DATE
    that leaves the system and is read as a date by someone outside it:

      - the ``order_date`` sent to a courier,
      - the ``<DATE>`` on a Tally sales / receipt voucher (a dated accounting
        document -- a 1-Apr 02:00 IST order otherwise books into the PRIOR
        financial year),
      - the date on a GST / invoice reconciliation row an accountant reads.

    WHEN **NOT** TO USE IT
    ----------------------
    The raw stored instant is still the RIGHT value, and shifting it is a bug,
    whenever the value is only ever compared against OTHER stored instants or
    against a bound built from the same UTC clock:

      - sort keys / ``$gte``-``$lt`` Mongo range bounds on ``created_at``
        (use ``ist_day_start_utc()`` to build an IST-day bound in the stored
        naive-UTC frame -- do NOT shift the stored side instead),
      - dedupe / idempotency keys and any key already persisted in a document,
      - elapsed-time and age-in-days arithmetic,
      - a comparison whose other side is itself derived from ``datetime.now()``
        -- converting only ONE side moves the boundary error, it does not
        remove it.

    Shapes (same tolerance as ``to_date_str``; never raises):
      - naive ``datetime``   -> +05:30, then the calendar day
      - aware ``datetime``   -> converted to IST, then the calendar day
      - ``date``             -> unchanged (a date carries no instant to shift)
      - ``str``              -> first 10 chars, UNSHIFTED. A legacy ISO string
        carries no reliable frame, so guessing would corrupt it; this matches
        the pass-through the reports dashboard has always used.
      - ``None`` / other     -> '' (comparisons fail safe, never crash)
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(IST).date().isoformat()
        return (value + _IST_OFFSET).date().isoformat()
    return to_date_str(value)


def fy_start_year_ist(dt: Optional[datetime] = None) -> int:
    """Indian financial-year START year for an instant (FY starts 1 April, IST).

    1-Apr-2026 IST -> 2026; 31-Mar-2026 IST -> 2025. Pass a tz-aware ``dt`` to tag
    a specific event; default is IST now.
    """
    if dt is None:
        dt = now_ist()
    return dt.year if dt.month >= 4 else dt.year - 1
