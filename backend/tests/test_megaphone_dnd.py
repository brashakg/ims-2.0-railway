"""
IMS 2.0 - MEGAPHONE DND window tests
=====================================
Verifies BUG-1 fix: the DND quiet window is computed in Asia/Kolkata (IST),
NOT UTC. The window is 21:00-09:00 IST with an overnight wrap.

Why this matters: TRAI/DLT rules forbid promotional WhatsApp/SMS during the
night quiet hours, expressed in India Standard Time. The old code read
`datetime.now(timezone.utc).hour`, which is 5h30m behind IST -- so at ~01:00
IST (deep inside the quiet window) the UTC hour was ~19:30 and the system
would happily send, a compliance breach.

All tests inject fixed datetimes; no network, no DB, no real clock.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.implementations.megaphone import MegaphoneAgent, _IST, _now_ist


def _ist(hour: int, minute: int = 0, day: int = 15) -> datetime:
    """Build a tz-aware datetime at the given IST wall-clock time.

    Uses the module's resolved IST zone so the test matches production exactly
    (zoneinfo on normal hosts, fixed +05:30 fallback otherwise).
    """
    tz = _IST or timezone(timedelta(hours=5, minutes=30))
    return datetime(2026, 5, day, hour, minute, 0, tzinfo=tz)


@pytest.fixture()
def agent() -> MegaphoneAgent:
    # db=None -> no Mongo; we only exercise the pure DND helpers.
    return MegaphoneAgent(db=None)


# ---------------------------------------------------------------------------
# Inside the DND window (21:00-09:00 IST)
# ---------------------------------------------------------------------------


def test_2200_ist_is_inside_dnd(agent):
    assert agent._in_dnd_window(_ist(22, 0)) is True


def test_0200_ist_is_inside_dnd(agent):
    assert agent._in_dnd_window(_ist(2, 0)) is True


def test_2100_ist_boundary_is_inside_dnd(agent):
    """21:00 is the start of the window -> inside."""
    assert agent._in_dnd_window(_ist(21, 0)) is True


def test_0859_ist_is_inside_dnd(agent):
    """One minute before 09:00 is still quiet hours."""
    assert agent._in_dnd_window(_ist(8, 59)) is True


# ---------------------------------------------------------------------------
# Outside the DND window
# ---------------------------------------------------------------------------


def test_1000_ist_is_outside_dnd(agent):
    assert agent._in_dnd_window(_ist(10, 0)) is False


def test_1400_ist_is_outside_dnd(agent):
    assert agent._in_dnd_window(_ist(14, 0)) is False


def test_0900_ist_boundary_is_outside_dnd(agent):
    """09:00 is the end of the window -> sending resumes (not inside)."""
    assert agent._in_dnd_window(_ist(9, 0)) is False


# ---------------------------------------------------------------------------
# The UTC trap: a UTC-naive read would mis-classify these.
# ---------------------------------------------------------------------------


def test_old_utc_logic_would_have_been_wrong_at_0100_ist(agent):
    """At 01:00 IST the equivalent UTC hour is 19:30 (previous day). The old
    UTC-based check (>=21 or <9) on hour 19 would have returned False and SENT
    a promo at 1 AM. The IST-correct check must say True (quiet hours)."""
    one_am_ist = _ist(1, 0)
    # Sanity: confirm the corresponding UTC hour is indeed outside 21..9.
    assert one_am_ist.astimezone(timezone.utc).hour == 19
    # IST-correct answer:
    assert agent._in_dnd_window(one_am_ist) is True


def test_utc_instant_daytime_utc_but_night_ist_is_dnd(agent):
    """A tz-aware UTC instant that reads as DAYTIME in UTC but NIGHT in IST must
    be treated as DND (the core regression: the window is IST, not UTC).

    20:00 UTC == 01:30 IST (next day). UTC hour 20 is NOT inside the naive
    21..9 window, so the old UTC-based check would have ALLOWED a 1:30 AM IST
    promo. The IST-correct check must block it."""
    utc_2000 = datetime(2026, 5, 15, 20, 0, 0, tzinfo=timezone.utc)
    # The UTC hour (20) is daytime-ish and outside a naive 21..9 read.
    assert utc_2000.hour == 20
    # In IST it is 01:30 the next day -- deep inside quiet hours.
    assert _now_ist(utc_2000).hour == 1
    assert agent._in_dnd_window(utc_2000) is True


def test_utc_instant_night_utc_but_daytime_ist_is_allowed(agent):
    """The inverse: a tz-aware UTC instant that reads as NIGHT in UTC but
    DAYTIME in IST must be ALLOWED (not DND).

    23:00 UTC == 04:30 IST -- still quiet hours -- so use 05:00 UTC == 10:30
    IST instead. UTC hour 5 IS inside a naive 21..9 window (<9), so the old
    UTC-based check would have WRONGLY blocked a legit 10:30 AM IST promo. The
    IST-correct check must allow it."""
    utc_0500 = datetime(2026, 5, 15, 5, 0, 0, tzinfo=timezone.utc)
    # The UTC hour (5) is night-ish and inside a naive <9 read.
    assert utc_0500.hour == 5
    # In IST it is 10:30 -- well within the 09:00-21:00 allowed send window.
    assert _now_ist(utc_0500).hour == 10
    assert agent._in_dnd_window(utc_0500) is False


# ---------------------------------------------------------------------------
# scheduled_for == next 09:00 IST
# ---------------------------------------------------------------------------


def test_scheduled_for_evening_rolls_to_next_morning(agent):
    """A message considered at 22:00 IST should be scheduled for 09:00 IST the
    NEXT day (we're past 09:00 today, in the evening leg of the window)."""
    queued_at = _ist(22, 0, day=15)
    target = agent._next_dnd_end(queued_at)
    target_ist = _now_ist(target)
    assert target_ist.hour == 9
    assert target_ist.minute == 0
    assert target_ist.day == 16  # next day


def test_scheduled_for_after_midnight_is_same_morning(agent):
    """A message considered at 02:00 IST should be scheduled for 09:00 IST the
    SAME calendar day (quiet hours end this morning)."""
    queued_at = _ist(2, 0, day=15)
    target = agent._next_dnd_end(queued_at)
    target_ist = _now_ist(target)
    assert target_ist.hour == 9
    assert target_ist.day == 15  # same day


def test_scheduled_for_utc_iso_is_the_next_0900_ist_instant(agent):
    """The stored scheduled_for string is UTC but represents 09:00 IST.
    09:00 IST == 03:30 UTC. Verify the stored instant round-trips back to
    09:00 IST."""
    queued_at = _ist(23, 30, day=15)
    iso = agent._next_dnd_end_utc_iso(queued_at)
    parsed = datetime.fromisoformat(iso)
    # Stored as UTC
    assert parsed.utcoffset() == timedelta(0)
    # Same instant is 09:00 IST the next day
    back_to_ist = _now_ist(parsed)
    assert (back_to_ist.hour, back_to_ist.minute) == (9, 0)
    assert back_to_ist.day == 16
    # And the UTC clock reads 03:30
    assert (parsed.hour, parsed.minute) == (3, 30)


def test_scheduled_for_only_set_when_in_dnd(agent):
    """Outside DND there is nothing to defer."""
    assert agent._in_dnd_window(_ist(14, 0)) is False
    # The queue path only computes scheduled_for when _in_dnd_window() is True,
    # so just assert the guard itself here.


# ---------------------------------------------------------------------------
# Injection: naive datetimes are treated as IST wall-clock
# ---------------------------------------------------------------------------


def test_naive_datetime_treated_as_ist(agent):
    """A naive 23:00 (no tzinfo) is interpreted as 23:00 IST -> inside DND."""
    assert agent._in_dnd_window(datetime(2026, 5, 15, 23, 0, 0)) is True


def test_default_now_uses_real_ist_clock(agent):
    """With no argument, _in_dnd_window resolves against the real IST clock and
    must return a bool without raising."""
    result = agent._in_dnd_window()
    assert isinstance(result, bool)
