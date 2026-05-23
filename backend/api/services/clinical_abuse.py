"""
IMS 2.0 - Clinical abuse / fraud-signal detection (pure logic)
==============================================================
Pure, deterministic detectors for the Clinical "Abuse Detection" fraud-control
view. NO DB, NO IO -- every function here is a function of plain counts / lists
of prescription dicts, so the thresholds are trivially unit-testable and behave
identically across workers.

The router (``backend/api/routers/clinical.py``) does the data fetch + grouping
and feeds these functions; this module owns ONLY the "given the numbers, is this
suspicious and how bad" decision. Keeping that split means a threshold change is
a one-line edit here with a matching unit test, never a router rewrite.

Four detectors, mirroring the frontend ``AbuseAlert`` union:
  1. Excessive redos per optometrist        -> type "high-redo-rate"
  2. Out-of-range / suspicious Rx patterns  -> type "high-redo-rate" (reused;
       the UI union only has 3 types -- this rides the closest existing one)
  3. Repeat tests for one patient, short window -> type "exact-copy"
  4. Rapid / implausibly-fast entries by one optometrist -> type "suspicious-speed"

Every detector is fail-soft at the router boundary: bad/absent inputs yield "no
alert" (None / empty list) rather than raising. ASCII only (Windows cp1252).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


# ============================================================================
# THRESHOLDS (single source of truth -- tune here, tests pin each one)
# ============================================================================

# 1. Redo rate: flag an optometrist whose redos/tests exceeds this, but only
#    once there are enough tests for the rate to mean anything.
REDO_RATE_WARN = 0.15          # 15% -> WARNING
REDO_RATE_CRITICAL = 0.30      # 30% -> CRITICAL
REDO_MIN_SAMPLE = 5            # need >= 5 tests before a rate is trustworthy

# 2. Out-of-range Rx: flag an optometrist who hits the validation bounds (or
#    beyond) on an unusual share of their tests.
OUT_OF_RANGE_RATE_WARN = 0.20      # 20% of tests at/over a bound -> WARNING
OUT_OF_RANGE_RATE_CRITICAL = 0.40  # 40% -> CRITICAL
OUT_OF_RANGE_MIN_SAMPLE = 5
OUT_OF_RANGE_MIN_COUNT = 2         # and at least this many actual hits

# 3. Repeat tests for the same patient inside a short window.
REPEAT_WINDOW_DAYS = 7         # "short window"
REPEAT_MIN_TESTS = 3           # >= 3 tests for one patient in the window
REPEAT_CRITICAL_TESTS = 5      # >= 5 -> CRITICAL

# 4. Rapid entries: many tests entered by one optometrist in too short a span.
#    A genuine refraction takes ~10-15 min; clearing this many in this few
#    minutes implies copy/paste data-entry rather than real exams.
RAPID_MIN_TESTS = 5            # need at least this many close-together tests
RAPID_MAX_MINUTES = 10.0       # ... inside this many minutes (avg gap basis)
RAPID_CRITICAL_MINUTES = 3.0   # avg gap at/under this -> CRITICAL

# Rx validation bounds (IMS 2.0 business rules). A value AT or BEYOND a bound is
# treated as "at the edge" / suspicious. AXIS is valid 1..180 inclusive, so
# out-of-range there means < 1 or > 180.
SPH_ABS_MAX = 20.0
CYL_ABS_MAX = 6.0
ADD_MIN = 0.75
ADD_MAX = 3.50
AXIS_MIN = 1
AXIS_MAX = 180


# ============================================================================
# SMALL HELPERS
# ============================================================================


def _to_float(value: Any) -> Optional[float]:
    """Coerce a stored Rx power (str like "-1.25", int, float) to float.

    Returns None for None / "" / non-numeric junk so callers can skip a blank
    cell rather than crash. Mirrors ``format_rx_value`` tolerance in the router.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # guard: bool is an int subclass
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def parse_dt(value: Any) -> Optional[datetime]:
    """Best-effort parse of a prescription/test timestamp to a naive datetime.

    Prescriptions carry dates in several shapes: ``prescription_date`` /
    ``test_date`` (ISO strings) and ``created_at`` (a real datetime stamped by
    the base repository). Tolerates a trailing 'Z'. Returns None on failure so
    a single bad row never poisons a window comparison.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        # Drop tzinfo so all comparisons are naive (the app writes naive local).
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        s = value.strip()
        if s == "":
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.replace(tzinfo=None)
        except ValueError:
            return None
    return None


def rx_date(rx: Dict[str, Any]) -> Optional[datetime]:
    """Best date for a prescription dict (prescription_date > test_date >
    created_at). Returns the first that parses, else None."""
    for key in ("prescription_date", "test_date", "created_at"):
        dt = parse_dt(rx.get(key))
        if dt is not None:
            return dt
    return None


# ============================================================================
# 1. EXCESSIVE REDOS PER OPTOMETRIST
# ============================================================================


def redo_severity(redo_count: int, test_count: int) -> Optional[str]:
    """Return "critical" / "warning" / None for an optometrist's redo rate.

    None when below the warn threshold OR when the sample is too small to be
    meaningful (< REDO_MIN_SAMPLE tests). Pure: depends only on the two counts.
    """
    if test_count < REDO_MIN_SAMPLE or test_count <= 0:
        return None
    rate = redo_count / test_count
    if rate >= REDO_RATE_CRITICAL:
        return "critical"
    if rate >= REDO_RATE_WARN:
        return "warning"
    return None


def redo_rate_percent(redo_count: int, test_count: int) -> float:
    """Redo rate as a percentage (0.0 when no tests). Rounded to 1 dp."""
    if test_count <= 0:
        return 0.0
    return round(redo_count / test_count * 100.0, 1)


# ============================================================================
# 2. OUT-OF-RANGE / SUSPICIOUS Rx VALUES
# ============================================================================


def is_eye_out_of_range(eye: Any) -> bool:
    """True if any power in a single per-eye block is at/beyond a validation
    bound. Fail-soft: a non-dict or all-blank eye is NOT out of range.

    "At or beyond" by design -- a clean +20.00 SPH is already the extreme edge
    of the valid range and worth surfacing when it shows up repeatedly.
    """
    if not isinstance(eye, dict):
        return False

    sph = _to_float(eye.get("sph", eye.get("sphere")))
    if sph is not None and abs(sph) >= SPH_ABS_MAX:
        return True

    cyl = _to_float(eye.get("cyl", eye.get("cylinder")))
    if cyl is not None and abs(cyl) >= CYL_ABS_MAX:
        return True

    add = _to_float(eye.get("add"))
    # ADD is only meaningful when present and non-zero; flag values that fall
    # outside the [ADD_MIN, ADD_MAX] band (a real ADD can't be 0 < add < 0.75).
    if add is not None and add != 0 and (add < ADD_MIN or add > ADD_MAX):
        return True

    axis = _to_float(eye.get("axis"))
    if axis is not None and (axis < AXIS_MIN or axis > AXIS_MAX):
        return True

    return False


def is_rx_out_of_range(rx: Dict[str, Any]) -> bool:
    """True if either eye of a prescription is at/beyond a validation bound."""
    if not isinstance(rx, dict):
        return False
    return is_eye_out_of_range(rx.get("right_eye")) or is_eye_out_of_range(
        rx.get("left_eye")
    )


def out_of_range_severity(oor_count: int, test_count: int) -> Optional[str]:
    """Severity for an optometrist's share of out-of-range Rx.

    Requires a minimum sample AND a minimum absolute count so one freak high-Rx
    patient in a tiny sample doesn't trip an alert. Pure."""
    if test_count < OUT_OF_RANGE_MIN_SAMPLE or test_count <= 0:
        return None
    if oor_count < OUT_OF_RANGE_MIN_COUNT:
        return None
    rate = oor_count / test_count
    if rate >= OUT_OF_RANGE_RATE_CRITICAL:
        return "critical"
    if rate >= OUT_OF_RANGE_RATE_WARN:
        return "warning"
    return None


# ============================================================================
# 3. REPEAT TESTS FOR ONE PATIENT IN A SHORT WINDOW
# ============================================================================


def max_tests_in_window(dates: List[datetime], window_days: int) -> int:
    """Max number of tests falling inside any rolling ``window_days`` window.

    Sliding-window over a sorted copy of the dates. Pure; ignores nothing --
    callers pre-filter to one patient. An empty / single list returns its len.
    """
    valid = sorted(d for d in dates if isinstance(d, datetime))
    n = len(valid)
    if n <= 1:
        return n
    window_seconds = window_days * 86400
    best = 1
    start = 0
    for end in range(n):
        while (valid[end] - valid[start]).total_seconds() > window_seconds:
            start += 1
        best = max(best, end - start + 1)
    return best


def repeat_severity(test_count_in_window: int) -> Optional[str]:
    """Severity for N tests on one patient inside the short window."""
    if test_count_in_window >= REPEAT_CRITICAL_TESTS:
        return "critical"
    if test_count_in_window >= REPEAT_MIN_TESTS:
        return "warning"
    return None


# ============================================================================
# 4. RAPID / IMPLAUSIBLY-FAST ENTRIES BY ONE OPTOMETRIST
# ============================================================================


def find_rapid_burst(
    dates: List[datetime],
    min_tests: int = RAPID_MIN_TESTS,
    max_minutes: float = RAPID_MAX_MINUTES,
) -> Optional[Dict[str, Any]]:
    """Detect the tightest burst of >= ``min_tests`` entries within
    ``max_minutes`` for one optometrist.

    Returns ``{"count": int, "span_minutes": float, "avg_gap_minutes": float}``
    for the densest qualifying window, or None if no such burst exists. Pure.

    "avg_gap" = span / (count - 1): the mean minutes between consecutive
    entries. A burst of 6 tests spanning 5 minutes => 1.0 min/test, which no
    real refraction can sustain, so it scores CRITICAL via ``rapid_severity``.
    """
    valid = sorted(d for d in dates if isinstance(d, datetime))
    n = len(valid)
    if n < min_tests:
        return None

    window_seconds = max_minutes * 60.0
    # Track the best burst as scalars (not a possibly-None dict) so the
    # comparison never subscripts an unset value. found stays False until a
    # qualifying window appears.
    found = False
    best_count = 0
    best_span = 0.0
    start = 0
    for end in range(n):
        while (valid[end] - valid[start]).total_seconds() > window_seconds:
            start += 1
        count = end - start + 1
        if count >= min_tests:
            span_minutes = round(
                (valid[end] - valid[start]).total_seconds() / 60.0, 2
            )
            # Prefer the densest burst: more tests, or same tests in less time.
            if (not found) or count > best_count or (
                count == best_count and span_minutes < best_span
            ):
                found = True
                best_count = count
                best_span = span_minutes

    if not found:
        return None
    avg_gap = round(best_span / (best_count - 1), 2) if best_count > 1 else 0.0
    return {
        "count": best_count,
        "span_minutes": best_span,
        "avg_gap_minutes": avg_gap,
    }


def rapid_severity(avg_gap_minutes: float) -> str:
    """Severity for a rapid burst given its average inter-entry gap.

    Always returns a level (callers only invoke this once ``find_rapid_burst``
    has already confirmed a qualifying burst exists)."""
    if avg_gap_minutes <= RAPID_CRITICAL_MINUTES:
        return "critical"
    return "warning"
