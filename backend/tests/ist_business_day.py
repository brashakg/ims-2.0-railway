"""IST business-day arithmetic for TEST FIXTURES. Hand-rolled on purpose.

THE TRAP THIS EXISTS TO CLOSE
=============================
The box (laptop, Railway, the CI runner) runs on UTC. Almost everything IMS
STORES is stamped in that frame -- ``datetime.now()`` / ``datetime.utcnow()``,
naive, UTC wall clock. Almost everything IMS ASKS FOR is an IST BUSINESS DAY:
a till session's expense window, a /sales report's from_date/to_date, the date
a courier is told an order was placed, a vendor's month-to-date spend.

Between 00:00 and 05:30 IST -- 18:30 to 24:00 UTC the previous day -- those two
are DIFFERENT CALENDAR DAYS. A fixture that seeds a row at ``datetime.now()``
and then reuses that same value's ``.date()`` as the business day it expects
back is asking for a day its own row is not in. It passes all day and fails
every night, which is exactly how CI went red from 00:00 to 05:30 IST while the
production code was correct.

Seed with ``datetime.now()`` -- that IS the stored frame, and changing it would
make the fixture lie about production. Then convert with the helpers here to
say which BUSINESS day you mean.

WHY THIS DOES NOT IMPORT ``api.utils.ist``
==========================================
Deliberately. A fixture that asks the code under test what the right answer is
can never catch that code being wrong: it would agree with a broken helper and
report green. The +05:30 is spelled out here, once, independently. India has no
DST, so the offset is a constant and this arithmetic is exact.

No emoji (Windows cp1252).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST_OFFSET = timedelta(hours=5, minutes=30)


def business_now() -> datetime:
    """The IST business wall clock, as a NAIVE datetime.

    Use for the year/month a report windows on (``?year=&month=``), or any
    "what day is it, in the shop" question. NOT for seeding ``created_at`` --
    that is stored in the naive-UTC frame, so seed it with ``datetime.now()``.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None) + IST_OFFSET


def business_day(stored=None) -> str:
    """The IST business day ('YYYY-MM-DD') of a stored instant.

    ``stored`` may be a naive-UTC datetime (the frame IMS writes), an aware
    datetime, an ISO string in either shape, or ``None`` for "right now".
    """
    if stored is None:
        return business_now().date().isoformat()
    if isinstance(stored, str):
        stored = datetime.fromisoformat(stored)
    if stored.tzinfo is not None:
        stored = stored.astimezone(timezone.utc).replace(tzinfo=None)
    return (stored + IST_OFFSET).date().isoformat()


def demo() -> None:
    """Self-check: the 18:30 UTC seam is the IST day boundary."""
    assert business_day(datetime(2026, 8, 20, 18, 29, 59)) == "2026-08-20"
    assert business_day(datetime(2026, 8, 20, 18, 30, 0)) == "2026-08-21"
    assert business_day("2026-08-20T18:30:00") == "2026-08-21"
    assert business_day("2026-08-20T22:30:00+00:00") == "2026-08-21"
    # An IST-aware stamp lands on its own day, no double shift.
    assert business_day("2026-08-21T00:30:00+05:30") == "2026-08-21"
    print("ist_business_day: ok")


if __name__ == "__main__":
    demo()
