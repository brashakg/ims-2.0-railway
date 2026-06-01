"""
IMS 2.0 - Shared IST quiet-hours / promo-window guard
=====================================================
Single source of truth for the India Standard Time (Asia/Kolkata) quiet-hours
window used by EVERY outbound-customer-messaging path:

  - MEGAPHONE (agents.implementations.megaphone) -- the marketing agent's DND.
  - Task escalation WhatsApp (api.services.task_notify) -- so a P2/P3 escalation
    text does not ping a manager at 2 AM.
  - The manual marketing send API (api.routers.marketing) -- promotional sends
    are blocked outside the 9 AM - 9 PM IST window (TRAI/DLT rule).

Why this module exists: the three call sites previously each rolled their own
clock. task_notify used the SERVER-LOCAL clock (datetime.now()), which on a
UTC-hosted Railway box is 5h30m behind IST -- so its "quiet hours" were wrong
and a low-priority escalation could fire at ~02:30 IST. MEGAPHONE already had a
correct IST clock; this module promotes that logic to a shared, importable
helper so all three agree on the SAME window, computed in the SAME timezone.

The window is 21:00-09:00 IST (overnight wrap). Equivalently, promotional sends
are ALLOWED only during [09:00, 21:00) IST.

Design contract (matches the rest of the agents package):
- Pure + IO-free -> unit-tests cleanly with injected `now`.
- Fail soft on tz resolution: prefer zoneinfo Asia/Kolkata; fall back to a fixed
  UTC+5:30 offset (India observes no DST, so the offset is exact); only if BOTH
  fail do we degrade to UTC with a one-time warning.
- `now` may be naive or tz-aware. A NAIVE datetime is interpreted as IST
  wall-clock (callers/tests that pass `datetime(...,23,0)` mean 23:00 IST).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Quiet-hours window in IST hours (overnight wrap): [21:00, 24:00) U [00:00, 09:00).
QUIET_START_HOUR = 21  # 9 PM IST -- promo sending stops
QUIET_END_HOUR = 9  # 9 AM IST -- promo sending resumes

# Resolve IST once at import. zoneinfo is preferred; the fixed +05:30 offset is
# an exact fallback (no DST in India). _IST is None only if BOTH fail, in which
# case we degrade to UTC and warn (sends may not respect the IST window).
_IST: Optional[timezone] = None
try:
    from zoneinfo import ZoneInfo

    _IST = ZoneInfo("Asia/Kolkata")  # type: ignore[assignment]
except Exception as _e:  # pragma: no cover - only on hosts without tzdata
    try:
        _IST = timezone(timedelta(hours=5, minutes=30), name="IST")
        logger.warning(
            "[QUIET_HOURS] zoneinfo Asia/Kolkata unavailable (%s) -- using fixed "
            "UTC+5:30 offset. India does not observe DST so this is exact.",
            _e,
        )
    except Exception:  # pragma: no cover
        _IST = None
        logger.warning(
            "[QUIET_HOURS] Could not resolve IST timezone -- quiet-hours falls "
            "back to UTC. Promotional sends may not respect the IST window."
        )


def now_ist(now: Optional[datetime] = None) -> datetime:
    """Return `now` (or the real current time) expressed in IST.

    `now` may be naive or tz-aware; if naive it is assumed to ALREADY be IST
    wall-clock. Falls back to UTC only if IST could not be resolved at import.
    """
    if now is None:
        tz = _IST or timezone.utc
        return datetime.now(tz)
    if now.tzinfo is None:
        # Naive datetimes from callers/tests are taken to mean IST wall-clock.
        return now if _IST is None else now.replace(tzinfo=_IST)
    return now.astimezone(_IST) if _IST is not None else now.astimezone(timezone.utc)


def in_quiet_hours(now: Optional[datetime] = None) -> bool:
    """True if `now` (default: real IST now) is inside the 21:00-09:00 IST
    quiet window. The window wraps midnight, so the test is
    hour >= QUIET_START_HOUR (>=21) OR hour < QUIET_END_HOUR (<9)."""
    hour = now_ist(now).hour
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


def promo_send_allowed(now: Optional[datetime] = None) -> bool:
    """True if a PROMOTIONAL message may be sent right now (i.e. we are OUTSIDE
    quiet hours -- within [09:00, 21:00) IST). Convenience inverse of
    in_quiet_hours() for the manual-send promo-window guard."""
    return not in_quiet_hours(now)


def next_quiet_end(now: Optional[datetime] = None) -> datetime:
    """Return the next 09:00 IST instant at or after `now`, tz-aware (+05:30).

    Used as a `scheduled_for` so a promo considered inside the quiet window is
    held until the window ends. If it is already past 09:00 today (the evening
    21:00-24:00 leg) the target rolls to 09:00 tomorrow.
    """
    ist_now = now_ist(now)
    target = ist_now.replace(hour=QUIET_END_HOUR, minute=0, second=0, microsecond=0)
    if ist_now.hour >= QUIET_END_HOUR:
        target = target + timedelta(days=1)
    return target


def next_quiet_end_utc_iso(now: Optional[datetime] = None) -> str:
    """`next_quiet_end` as a UTC ISO-8601 string for storage in scheduled_for.

    We store UTC (the same instant as the 09:00 IST target) so a drain query's
    lexicographic `$lte` against a UTC `now_iso` stays valid -- mixing +05:30
    and +00:00 offset strings would compare wrong.
    """
    target = next_quiet_end(now)
    if target.tzinfo is None:  # pragma: no cover - next_quiet_end is tz-aware
        return target.isoformat()
    return target.astimezone(timezone.utc).isoformat()
