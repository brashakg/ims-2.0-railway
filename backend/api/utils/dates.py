"""Date / datetime normalization helpers."""

from datetime import datetime, date
from typing import Any


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
