"""Explicit event instants, distinct from calendar dates and BSON storage.

New UTC event strings use the timezone-aware clock below. Compatibility readers
may opt into the established naive-UTC history of a particular field; this module
must not be used to guess the frame of financial dates or legacy IST ledgers.
"""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any, Optional

from .ist import IST


def utc_now() -> datetime:
    """Current instant independent of the server's local timezone."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """New persisted event timestamp, including its explicit UTC offset."""
    return utc_now().isoformat()


def utc_event_iso(value: datetime) -> str:
    """Encode a new known-UTC sample while retaining existing BSON values."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def ist_event_iso(value: datetime) -> str:
    """Encode a deliberate IST wall-clock sample without changing its day."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    return value.astimezone(IST).isoformat()


def ist_now_iso() -> str:
    """New explicit IST event for fields with deliberate legacy IST history."""
    return utc_now().astimezone(IST).isoformat()


def parse_event_utc(value: Any, *, naive_timezone: tzinfo = timezone.utc) -> Optional[datetime]:
    """Read a known event field as aware UTC, preserving explicit offsets.

    The default is only appropriate for fields with established legacy UTC history.
    Date-only strings are accepted for compatibility with existing event readers;
    calendar-field callers must keep their own deliberate date interpretation.
    Malformed/missing values return None, so each reader retains its failure policy.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    try:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=naive_timezone)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def parse_event_utc_naive(value: Any) -> Optional[datetime]:
    """Compatibility event instant in the existing BSON naive-UTC read frame."""
    parsed = parse_event_utc(value)
    return parsed.replace(tzinfo=None) if parsed is not None else None
