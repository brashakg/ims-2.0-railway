"""
IMS 2.0 - Attendance Engine (pure logic)
========================================
Stateless, side-effect-free helpers for the HR attendance engine. Everything
here is unit-tested with no DB / network (see backend/tests/test_attendance_engine.py).

Scope (product-owner decisions, do NOT change without sign-off):
  - NO OVERTIME. There is intentionally no overtime computation anywhere.
  - RECORD + REPORT ONLY. These helpers compute attendance metrics (late marks,
    LWP days, geo enforcement, swap validation) but never mutate payroll. The
    existing payroll engine still does LWP proration when an accountant manually
    enters LWP days; this engine only surfaces the numbers.

Conventions: no emojis (Windows cp1252); fail-soft inputs; deterministic output.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time
from typing import Iterable, Optional


# Default geo-fence radius in metres when a store has none configured. Mirrors
# the LOGIN geo-fence default in api/routers/auth.py / routers/stores.py.
DEFAULT_GEOFENCE_RADIUS_M = 500

# Roles exempt from geo-fenced check-in (HQ / multi-store). Mirrors the
# "roles 1-3 exempt" rule from the geo-fenced LOGIN flow. Everyone else
# (store manager, optometrist, cashier, sales, workshop = roles 4-7) is fenced.
GEO_EXEMPT_ROLES = frozenset({"SUPERADMIN", "ADMIN", "AREA_MANAGER"})

# Roles allowed to approve a week-off swap (manager hierarchy). SUPERADMIN is
# included for completeness even though the router's require_roles auto-passes it.
SWAP_APPROVER_ROLES = frozenset(
    {"SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"}
)


# ============================================================================
# GEO-FENCE (reused haversine — identical formula to routers/auth.py)
# ============================================================================


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two lat/lng points.

    Same formula as api/routers/auth.py::_haversine_distance so attendance
    geo-enforcement behaves identically to the geo-fenced login.
    """
    radius = 6_371_000  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def is_geo_exempt(roles: Optional[Iterable[str]]) -> bool:
    """True if any of the user's roles is exempt from geo-fenced check-in
    (roles 1-3: SUPERADMIN / ADMIN / AREA_MANAGER)."""
    if not roles:
        return False
    return any(r in GEO_EXEMPT_ROLES for r in roles)


def evaluate_geofence(
    *,
    roles: Optional[Iterable[str]],
    user_lat: Optional[float],
    user_lng: Optional[float],
    store_lat: Optional[float],
    store_lng: Optional[float],
    radius_m: Optional[int] = None,
) -> dict:
    """Decide whether a check-in is allowed by the store geo-fence.

    Returns a dict:
      {
        "exempt": bool,           # role bypasses the fence entirely
        "allowed": bool,          # check-in permitted
        "distance_m": float|None, # measured distance (None when not computed)
        "radius_m": int,          # effective radius used
        "reason": str,            # machine-readable reason code
      }

    Reason codes:
      EXEMPT_ROLE       - role 1-3, no fence
      NO_STORE_COORDS   - store has no coordinates configured -> allow (fail-soft, cannot fence)
      LOCATION_REQUIRED - fenced role didn't send GPS -> block
      WITHIN_RADIUS     - inside fence -> allow
      OUTSIDE_RADIUS    - outside fence -> block

    Pure: no DB. Caller supplies the store coordinates it already loaded.
    """
    effective_radius = int(radius_m) if radius_m else DEFAULT_GEOFENCE_RADIUS_M

    # Roles 1-3 are never fenced.
    if is_geo_exempt(roles):
        return {
            "exempt": True,
            "allowed": True,
            "distance_m": None,
            "radius_m": effective_radius,
            "reason": "EXEMPT_ROLE",
        }

    # Store has no coordinates -> we cannot fence; fail-soft allow (mirrors the
    # login path, where a user with no resolvable fence coords is not blocked).
    if store_lat is None or store_lng is None:
        return {
            "exempt": False,
            "allowed": True,
            "distance_m": None,
            "radius_m": effective_radius,
            "reason": "NO_STORE_COORDS",
        }

    # Fenced role must provide a location.
    if user_lat is None or user_lng is None:
        return {
            "exempt": False,
            "allowed": False,
            "distance_m": None,
            "radius_m": effective_radius,
            "reason": "LOCATION_REQUIRED",
        }

    distance = haversine_distance_m(user_lat, user_lng, store_lat, store_lng)
    within = distance <= effective_radius
    return {
        "exempt": False,
        "allowed": within,
        "distance_m": round(distance, 1),
        "radius_m": effective_radius,
        "reason": "WITHIN_RADIUS" if within else "OUTSIDE_RADIUS",
    }


# ============================================================================
# LATE-MARK CALC
# ============================================================================


def _parse_hhmm(value: str) -> Optional[time]:
    """Parse a 'HH:MM' (or 'HH:MM:SS') 24h string into a time. None if invalid."""
    if not value or not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def _coerce_check_in(check_in) -> Optional[datetime]:
    """Coerce a check-in value (datetime or ISO string) into a datetime. None on failure."""
    if check_in is None:
        return None
    if isinstance(check_in, datetime):
        return check_in
    if isinstance(check_in, str):
        try:
            # fromisoformat handles 'YYYY-MM-DDTHH:MM[:SS]' and with offset.
            return datetime.fromisoformat(check_in.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def compute_late_mark(
    check_in,
    shift_start: Optional[str],
    grace_minutes: int = 0,
) -> dict:
    """Compute whether a check-in is late versus the shift start + grace window.

    Args:
        check_in: datetime or ISO 'YYYY-MM-DDTHH:MM:SS' string of the check-in.
        shift_start: 'HH:MM' string for the shift start. None/invalid -> no shift,
            so never late (we can't judge lateness without a configured shift).
        grace_minutes: minutes past shift start that are still on-time.

    Returns:
        {"is_late": bool, "late_minutes": int}

    late_minutes is measured from the shift start (NOT from the grace boundary),
    so a 10:00 shift with 15m grace and a 10:20 check-in is is_late=True,
    late_minutes=20. A 10:10 check-in is is_late=False, late_minutes=0.

    Record-only: this never touches payroll. Pure / deterministic.
    """
    result = {"is_late": False, "late_minutes": 0}

    ci = _coerce_check_in(check_in)
    start = _parse_hhmm(shift_start) if shift_start else None
    if ci is None or start is None:
        return result

    grace = max(0, int(grace_minutes or 0))

    # Compare on the check-in's own calendar date so we measure minutes-of-day,
    # independent of which date the shift template nominally lives on.
    shift_start_dt = datetime.combine(ci.date(), start)
    grace_boundary = shift_start_dt.timestamp() + grace * 60

    if ci.timestamp() > grace_boundary:
        delta_seconds = ci.timestamp() - shift_start_dt.timestamp()
        # Minutes late from the actual shift start (whole minutes, floored).
        late_min = int(delta_seconds // 60)
        result["is_late"] = True
        result["late_minutes"] = max(0, late_min)

    return result


# ============================================================================
# WEEK-OFF SWAP VALIDATION
# ============================================================================


def can_approve_swap(
    *,
    approver_id: Optional[str],
    approver_roles: Optional[Iterable[str]],
    requested_by: Optional[str],
    swap_status: Optional[str],
) -> dict:
    """Decide whether `approver` may approve/reject a week-off swap request.

    Enforces SYSTEM_INTENT 7: the requester cannot approve their own request,
    and only manager-tier roles may approve. Pure (no DB).

    Returns {"allowed": bool, "reason": str}. Reason codes:
      OK                 - approval permitted
      NOT_PENDING        - the request is already decided
      SELF_APPROVAL      - approver is the requester (forbidden)
      INSUFFICIENT_ROLE  - approver lacks a manager role
    """
    if swap_status is not None and str(swap_status).upper() != "PENDING":
        return {"allowed": False, "reason": "NOT_PENDING"}

    # Self-approval is forbidden even for a manager who filed their own request.
    if approver_id is not None and approver_id == requested_by:
        return {"allowed": False, "reason": "SELF_APPROVAL"}

    roles = set(approver_roles or [])
    if not (roles & SWAP_APPROVER_ROLES):
        return {"allowed": False, "reason": "INSUFFICIENT_ROLE"}

    return {"allowed": True, "reason": "OK"}


# ============================================================================
# LWP (Leave Without Pay) COMPUTATION
# ============================================================================

# Status spellings that count as LWP (unpaid). Mirrors the grid's defensive
# mapping in routers/hr.py (_STATUS_CODE_MAP).
_LWP_STATUSES = frozenset({"LWP", "UNPAID", "LOP"})
_ABSENT_STATUSES = frozenset({"ABSENT", "A"})
_PAID_LEAVE_STATUSES = frozenset({"LEAVE", "ON_LEAVE"})
_HALF_DAY_STATUSES = frozenset({"HALF_DAY", "HALFDAY", "HD"})


def _record_day(raw_date) -> Optional[int]:
    """Day-of-month from an attendance record's date (datetime/date/ISO str)."""
    if raw_date is None:
        return None
    if isinstance(raw_date, (datetime, date)):
        return raw_date.day
    if isinstance(raw_date, str):
        try:
            return int(raw_date[:10].split("-")[2])
        except (ValueError, IndexError):
            return None
    return None


def compute_lwp_days(
    *,
    records: Iterable[dict],
    approved_unpaid_leaves: Optional[Iterable[dict]] = None,
    half_day_as_half: bool = True,
) -> dict:
    """Compute Leave-Without-Pay days for a month from attendance + leaves.

    LWP is the count of days an employee was NOT paid-present, i.e. the union of:
      - attendance records explicitly marked LWP / UNPAID / LOP
      - attendance records marked ABSENT (unauthorised absence = unpaid)
      - approved leave rows of an unpaid leave_type (UNPAID/LWP/LOP), expanded
        across their date range within the records' implied month

    Paid leaves (CASUAL/SICK/EARNED), week-offs and holidays do NOT count as LWP.
    A HALF_DAY counts as 0.5 LWP when half_day_as_half is True.

    Args:
        records: attendance docs ({"date", "status"}).
        approved_unpaid_leaves: leave docs ({"from_date","to_date","leave_type",
            "status"}) -- only APPROVED + unpaid types contribute. Optional.
        half_day_as_half: count HALF_DAY as 0.5 instead of 0.

    Returns:
        {"lwp_days": float, "absent_days": int, "marked_lwp_days": int,
         "half_days": int, "unpaid_leave_days": int}

    Report-only: this is what the accountant reads. It is NEVER auto-applied to
    a payroll run. Pure / deterministic. Days are de-duplicated so an ABSENT
    record and an overlapping unpaid-leave row count once.
    """
    lwp_day_set: set = set()       # day-of-month flagged unpaid (from any source)
    half_day_set: set = set()
    absent_days = 0
    marked_lwp_days = 0
    half_days = 0

    for rec in records or []:
        status = str(rec.get("status") or "").strip().upper()
        day = _record_day(rec.get("date"))
        if day is None:
            continue
        if status in _LWP_STATUSES:
            marked_lwp_days += 1
            lwp_day_set.add(day)
        elif status in _ABSENT_STATUSES:
            absent_days += 1
            lwp_day_set.add(day)
        elif status in _HALF_DAY_STATUSES:
            half_days += 1
            half_day_set.add(day)

    # Expand approved unpaid-leave rows into individual days.
    unpaid_leave_days = 0
    for lv in approved_unpaid_leaves or []:
        if str(lv.get("status") or "").strip().upper() != "APPROVED":
            continue
        if str(lv.get("leave_type") or "").strip().upper() not in _LWP_STATUSES | {
            "UNPAID"
        }:
            continue
        for day in _expand_leave_days(lv.get("from_date"), lv.get("to_date")):
            unpaid_leave_days += 1
            lwp_day_set.add(day)

    # A full-LWP day overrides a half-day flag for the same date.
    effective_half_days = {d for d in half_day_set if d not in lwp_day_set}

    lwp_days = float(len(lwp_day_set))
    if half_day_as_half:
        lwp_days += 0.5 * len(effective_half_days)

    return {
        "lwp_days": round(lwp_days, 1),
        "absent_days": absent_days,
        "marked_lwp_days": marked_lwp_days,
        "half_days": half_days,
        "unpaid_leave_days": unpaid_leave_days,
    }


def _expand_leave_days(from_date, to_date) -> list:
    """Day-of-month list spanned by a leave row (inclusive). Handles ISO strings
    and date/datetime. Returns [] on bad input. Caps at 31 to stay bounded."""
    fd = _to_date(from_date)
    td = _to_date(to_date) or fd
    if fd is None:
        return []
    if td < fd:
        fd, td = td, fd
    days = []
    cur = fd
    guard = 0
    while cur <= td and guard < 366:
        days.append(cur.day)
        cur = date.fromordinal(cur.toordinal() + 1)
        guard += 1
    return days


def _to_date(value) -> Optional[date]:
    """Coerce a value into a date (date/datetime/ISO 'YYYY-MM-DD' str)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
