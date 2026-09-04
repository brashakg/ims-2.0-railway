"""Date / datetime normalization helpers."""

from datetime import datetime, date, timedelta
from typing import Any, Dict, Optional


def to_date_str(value: Any) -> str:
    """Coerce a `created_at`-style value to a 'YYYY-MM-DD' string.

    Mongo stamps `created_at` as a real BSON **datetime**, but legacy seeds /
    imports may store it as an ISO **string** (or omit it). Blindly slicing
    ``value[:10]`` raised ``TypeError: 'datetime.datetime' object is not
    subscriptable`` and 500'd several analytics / reports endpoints (QA F1).
    This normalizes all shapes and never raises:

      - ``datetime``      -> its ISO date ('YYYY-MM-DD')
      - ``date``          -> its ISO ('YYYY-MM-DD')
      - ``str``           -> first 10 chars (the date part of an ISO string)
      - ``None`` / other  -> '' (so date comparisons fail safely, never crash)
    """
    if value is None:
        return ""
    # NB: datetime is a subclass of date, so test datetime first.
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value[:10]
    return ""


def iso_date_window(
    from_day: Optional[date] = None, to_day: Optional[date] = None
) -> Dict[str, str]:
    """Mongo range bound over a column stored as an ISO-8601 date STRING.

    ONE implementation for every string-dated column -- ``prescription_date``,
    ``orders.expected_delivery``, workshop ``expected_date``. The rule existed
    twice (PrescriptionRepository._clinical_date_filter and inline in
    WorkshopJobRepository.find_overdue) while OrderRepository.find_overdue
    bound a DATETIME against its string column. BSON brackets by type and
    never compares a date with a string, so that filter matched nothing and
    /orders/overdue/list returned [] in production.

    Such a column holds TWO shapes side by side, because different doors write
    it with ``date.isoformat()`` and ``datetime.isoformat()``:

      * bare     ``2026-06-18``
      * stamped  ``2026-06-18T12:05:26.552211``  (or ``T00:00:00`` from a date)

    ISO-8601 sorts lexicographically, so a STRING bound is a correct calendar
    compare for both shapes PROVIDED neither edge carries a time component:

      * lower is the bare from-day (``$gte``). A ``T00:00:00`` lower bound
        would drop the bare rows on that day -- a prefix sorts BEFORE the
        longer string, ``2026-06-18`` < ``2026-06-18T00:00:00``.
      * upper is the NEXT day, exclusive (``$lt``). An inclusive bare to-day
        would drop every stamped row on that day -- ``2026-06-18T12:05`` >
        ``2026-06-18``.

    Both days are INCLUSIVE calendar days. "Strictly before day D" (overdue)
    is ``iso_date_window(to_day=D - 1 day)``, i.e. ``{"$lt": D.isoformat()}``:
    a row due ON day D, in either shape, is not admitted. Only the supplied
    edges are bound; ``iso_date_window()`` is ``{}``. A datetime handed in as
    an edge is reduced to its calendar day, so no time component can leak
    back into the bound.

    A real BSON date in the column would NOT match this bound. Every writer of
    the three columns above writes a string; learn a column's type from its
    WRITER, never from schemas.py (whose ``bsonType: date`` for
    expected_delivery is declared but not enforced).
    """
    out: Dict[str, str] = {}
    if from_day:
        out["$gte"] = to_date_str(from_day)
    if to_day:
        out["$lt"] = to_date_str(to_day + timedelta(days=1))
    return out
