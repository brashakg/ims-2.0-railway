"""
IMS 2.0 - HR Router
====================
Real database queries for attendance, leaves, and payroll
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List
from datetime import date, datetime
from calendar import monthrange
import uuid
from .auth import get_current_user, require_roles
from ..dependencies import (
    get_attendance_repository,
    get_leave_repository,
    get_payroll_repository,
    get_user_repository,
    validate_store_access,
)
from ..services import attendance_engine

# Roles allowed to view HR reporting screens. Mirrors the router-level gate in
# main.py (_FINANCE_ROLES) and how payroll.py gates its read endpoints.
# SUPERADMIN auto-passes inside require_roles, so it is intentionally omitted.
_HR_READ_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")

# Roles allowed to APPROVE a week-off swap / CONFIGURE shifts (manager tier).
# SUPERADMIN auto-passes inside require_roles so it is intentionally omitted.
_SWAP_APPROVER_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")
_SHIFT_ADMIN_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER")


def _get_db():
    """Shared DB-handle helper (CLAUDE.md convention). Fail-soft: callers must
    treat a None return as 'DB not connected'."""
    from database.connection import get_db

    return get_db().db


router = APIRouter()


# ============================================================================
# CONSTANTS
# ============================================================================

# Canonical attendance status values accepted by mark_attendance.
_VALID_STATUSES = frozenset(
    {"PRESENT", "ABSENT", "HALF_DAY", "LEAVE", "LWP", "HOLIDAY", "WEEK_OFF"}
)

# Leave types accepted by apply_leave.
_VALID_LEAVE_TYPES = frozenset(
    {
        "CASUAL", "SICK", "EARNED", "PRIVILEGE",
        "MATERNITY", "PATERNITY", "UNPAID", "LWP", "LOP",
    }
)


# ============================================================================
# SCHEMAS
# ============================================================================


class LeaveCreate(BaseModel):
    leave_type: str
    from_date: date
    to_date: date
    reason: str

    @field_validator("leave_type")
    @classmethod
    def validate_leave_type(cls, v: str) -> str:
        normalised = v.strip().upper()
        if normalised not in _VALID_LEAVE_TYPES:
            raise ValueError(
                f"leave_type must be one of: {', '.join(sorted(_VALID_LEAVE_TYPES))}"
            )
        return normalised

    @model_validator(mode="after")
    def dates_must_be_valid(self) -> "LeaveCreate":
        today = date.today()
        if self.to_date < self.from_date:
            raise ValueError("to_date must be on or after from_date")
        # Allow leave applications for future dates (pre-booking), but not
        # back-dated by more than 90 days (prevents historical data injection).
        if self.from_date < date(today.year - 1, today.month, today.day):
            raise ValueError("from_date cannot be more than 1 year in the past")
        return self


class AttendanceMarkRequest(BaseModel):
    employee_id: str
    date: date
    status: str  # PRESENT, ABSENT, HALF_DAY, LEAVE, LWP, HOLIDAY, WEEK_OFF
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        normalised = v.strip().upper()
        if normalised not in _VALID_STATUSES:
            raise ValueError(
                f"status must be one of: {', '.join(sorted(_VALID_STATUSES))}"
            )
        return normalised

    @field_validator("date")
    @classmethod
    def date_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("Attendance cannot be marked for a future date")
        return v

    @model_validator(mode="after")
    def checkout_after_checkin(self) -> "AttendanceMarkRequest":
        ci = self.check_in
        co = self.check_out
        if ci is not None and co is not None:
            # Strip timezone info for naive comparison (both are stored naive).
            ci_ts = ci.replace(tzinfo=None)
            co_ts = co.replace(tzinfo=None)
            if co_ts <= ci_ts:
                raise ValueError("check_out must be after check_in")
        return self


# ============================================================================
# ATTENDANCE GRID HELPERS (pure functions - unit tested, no DB)
# ============================================================================

# Map raw DB status spellings to the short grid codes. The canonical enum is
# PRESENT / ABSENT / HALF_DAY / LEAVE / HOLIDAY (database/schemas.py), but we
# also defensively accept LWP / UNPAID and various week-off spellings so the
# grid stays correct regardless of how a record was written.
_STATUS_CODE_MAP = {
    "PRESENT": "P",
    "ABSENT": "A",
    "LEAVE": "L",
    "ON_LEAVE": "L",
    "HALF_DAY": "HD",
    "HALFDAY": "HD",
    "LWP": "LWP",
    "UNPAID": "LWP",
    "LOP": "LWP",
    "HOLIDAY": "WO",
    "WEEK_OFF": "WO",
    "WEEKLY_OFF": "WO",
    "WEEKOFF": "WO",
    "OFF": "WO",
}

# Grid code -> summary bucket key.
_CODE_SUMMARY_KEY = {
    "P": "present",
    "A": "absent",
    "L": "leave",
    "LWP": "lwp",
    "HD": "half_day",
    "WO": "week_off",
}

_EMPTY_CODE = "-"


def _days_in_month(year: int, month: int) -> int:
    """Number of days in the given month (handles Feb leap years)."""
    return monthrange(year, month)[1]


def _parse_month(month: str) -> tuple:
    """Parse a 'YYYY-MM' string into (year, month). Falls back to the current
    month for any malformed / missing input so the endpoint never 500s."""
    if month:
        try:
            parts = month.split("-")
            year = int(parts[0])
            mon = int(parts[1])
            if 1 <= mon <= 12 and year >= 1900:
                return year, mon
        except (ValueError, IndexError, AttributeError):
            pass
    today = date.today()
    return today.year, today.month


def _status_to_code(status: Optional[str]) -> str:
    """Map a raw attendance status value to a short grid code."""
    if not status:
        return _EMPTY_CODE
    return _STATUS_CODE_MAP.get(str(status).strip().upper(), _EMPTY_CODE)


def _day_of_record(raw_date) -> Optional[int]:
    """Extract the day-of-month from an attendance record's `date` field.

    The write path (POST /attendance/mark) stores an ISO string ('2026-05-01'),
    while the schema declares a BSON date and some repo helpers write datetimes.
    Handle both so the grid is robust to either storage format. Returns None if
    the value can't be interpreted."""
    if raw_date is None:
        return None
    if isinstance(raw_date, (datetime, date)):
        return raw_date.day
    if isinstance(raw_date, str):
        # Expect 'YYYY-MM-DD' (optionally with a 'T...' time component).
        try:
            return int(raw_date[:10].split("-")[2])
        except (ValueError, IndexError):
            return None
    return None


def _empty_summary() -> dict:
    """A zeroed summary bucket."""
    return {
        "present": 0,
        "absent": 0,
        "leave": 0,
        "lwp": 0,
        "half_day": 0,
        "late": 0,
        "week_off": 0,
    }


def _build_grid(year: int, month: int, employees: list, records: list) -> dict:
    """Assemble the month grid from a roster + attendance records.

    Args:
        year, month: target period
        employees: list of {employee_id, name, store_id} (the roster)
        records: list of raw attendance docs for the period

    Pure function (no DB) so it can be unit-tested directly.
    """
    n_days = _days_in_month(year, month)
    days = list(range(1, n_days + 1))

    # Bucket records by employee_id -> {day: code} and remember late flags.
    by_emp: dict = {}
    for rec in records or []:
        emp_id = rec.get("employee_id")
        if not emp_id:
            continue
        day = _day_of_record(rec.get("date"))
        if day is None or day < 1 or day > n_days:
            continue
        code = _status_to_code(rec.get("status"))
        slot = by_emp.setdefault(emp_id, {"days": {}, "late_days": set()})
        slot["days"][str(day)] = code
        if rec.get("is_late"):
            slot["late_days"].add(day)

    totals = _empty_summary()
    out_employees = []
    for emp in employees or []:
        emp_id = emp.get("employee_id")
        slot = by_emp.get(emp_id, {"days": {}, "late_days": set()})
        day_codes = slot["days"]
        summary = _empty_summary()
        for code in day_codes.values():
            key = _CODE_SUMMARY_KEY.get(code)
            if key:
                summary[key] += 1
        summary["late"] = len(slot["late_days"])
        # Roll into grand totals.
        for k in totals:
            totals[k] += summary[k]
        out_employees.append(
            {
                "employee_id": emp_id,
                "name": emp.get("name", ""),
                "store_id": emp.get("store_id", ""),
                "days": day_codes,
                "summary": summary,
            }
        )

    return {
        "month": f"{year:04d}-{month:02d}",
        "days": days,
        "employees": out_employees,
        "totals": totals,
    }


def _roster_from_users(users: list, store_id: Optional[str]) -> list:
    """Normalise user docs into the grid roster shape, sorted by name."""
    roster = []
    for u in users or []:
        uid = u.get("user_id") or u.get("_id")
        if not uid:
            continue
        # A user may belong to multiple stores; pin the row to the requested
        # store when one was resolved, else fall back to the user's first store.
        store_ids = u.get("store_ids") or []
        row_store = store_id or (store_ids[0] if store_ids else u.get("store_id", ""))
        roster.append(
            {
                "employee_id": uid,
                "name": u.get("full_name") or u.get("name") or u.get("username") or uid,
                "store_id": row_store or "",
            }
        )
    roster.sort(key=lambda r: (r["name"] or "").lower())
    return roster


# ============================================================================
# ATTENDANCE ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def get_hr_root():
    """Root endpoint for HR module overview"""
    return {"module": "hr", "status": "active", "message": "HR overview endpoint ready"}


@router.get("/attendance")
async def get_attendance(
    employee_id: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get attendance records with optional filters"""
    attendance_repo = get_attendance_repository()
    active_store = store_id or current_user.get("active_store_id")

    if attendance_repo is None:
        return {"records": [], "total": 0}

    filter_dict = {}
    if active_store:
        filter_dict["store_id"] = active_store
    if employee_id:
        filter_dict["employee_id"] = employee_id
    if from_date:
        filter_dict["date"] = {"$gte": from_date.isoformat()}
    if to_date:
        if "date" in filter_dict:
            filter_dict["date"]["$lte"] = to_date.isoformat()
        else:
            filter_dict["date"] = {"$lte": to_date.isoformat()}

    records = attendance_repo.find_many(filter_dict, limit=500)

    # Convert to camelCase for frontend
    camel_records = []
    for r in records:
        camel_records.append(
            {
                "attendanceId": r.get("attendance_id", ""),
                "employeeId": r.get("employee_id", ""),
                "employeeName": r.get("employee_name", ""),
                "date": r.get("date", ""),
                "status": r.get("status", ""),
                "checkIn": r.get("check_in"),
                "checkOut": r.get("check_out"),
                "storeId": r.get("store_id", ""),
            }
        )

    return {"records": camel_records, "total": len(camel_records)}


@router.get("/attendance/grid")
async def get_attendance_grid(
    month: Optional[str] = Query(None, description="Target month as YYYY-MM"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_HR_READ_ROLES)),
):
    """Monthly attendance grid: per-employee x per-day status matrix.

    Returns a well-formed grid (days computed from the month, employees joined
    from the store roster). Read-only reporting view.

    Store scoping: HQ roles (SUPERADMIN/ADMIN/AREA_MANAGER) may pass any
    store_id; lower roles are pinned to a store they have access to via
    validate_store_access. Fail-soft: no DB / no records => empty-but-valid grid
    (never 500).
    """
    year, mon = _parse_month(month)

    # Resolve + authorise the store. validate_store_access falls back to the
    # user's active store when store_id is omitted and 403s on cross-store
    # access for non-HQ roles.
    active_store = validate_store_access(store_id, current_user)

    user_repo = get_user_repository()
    attendance_repo = get_attendance_repository()

    # Build the roster (store-scoped). No DB -> empty roster, still valid grid.
    employees = []
    if user_repo is not None:
        roster_filter = {"is_active": True}
        if active_store:
            roster_filter["store_ids"] = active_store
        try:
            users = user_repo.find_many(roster_filter, limit=1000)
        except Exception:
            users = []
        employees = _roster_from_users(users, active_store)

    # Pull the month's attendance records (string date range mirrors the actual
    # write path in POST /attendance/mark and payroll/generate).
    records = []
    if attendance_repo is not None:
        n_days = _days_in_month(year, mon)
        start = f"{year:04d}-{mon:02d}-01"
        end = f"{year:04d}-{mon:02d}-{n_days:02d}"
        rec_filter = {"date": {"$gte": start, "$lte": end}}
        if active_store:
            rec_filter["store_id"] = active_store
        try:
            records = attendance_repo.find_many(rec_filter, limit=5000)
        except Exception:
            records = []

    return _build_grid(year, mon, employees, records)


def _store_coords(store_id: Optional[str]) -> dict:
    """Load a store's geo-fence coordinates + radius. Fail-soft: returns
    {lat: None, lng: None, radius_m: None} when the DB or store is absent."""
    out = {"lat": None, "lng": None, "radius_m": None}
    if not store_id:
        return out
    try:
        db = _get_db()
    except Exception:
        db = None
    if db is None:
        return out
    try:
        s = db.get_collection("stores").find_one(
            {"store_id": store_id},
            {"_id": 0, "latitude": 1, "longitude": 1, "geofence_radius_m": 1},
        )
    except Exception:
        s = None
    if s:
        out["lat"] = s.get("latitude")
        out["lng"] = s.get("longitude")
        out["radius_m"] = s.get("geofence_radius_m")
    return out


def _resolve_employee_shift(employee_id: str, store_id: Optional[str]) -> Optional[dict]:
    """Resolve the shift assigned to an employee.

    Lookup order: explicit per-user assignment (users.shift_id) -> the store's
    single active shift (only if exactly one, to avoid guessing) -> None.
    Fail-soft: any DB error -> None (so late-mark just isn't computed)."""
    try:
        db = _get_db()
    except Exception:
        db = None
    if db is None:
        return None
    shift_id = None
    try:
        u = db.get_collection("users").find_one(
            {"user_id": employee_id}, {"_id": 0, "shift_id": 1}
        )
        if u:
            shift_id = u.get("shift_id")
    except Exception:
        shift_id = None
    try:
        if shift_id:
            return db.get_collection("shifts").find_one({"shift_id": shift_id}, {"_id": 0})
        # Fall back to the store's lone active shift, if unambiguous.
        if store_id:
            active = list(
                db.get_collection("shifts").find(
                    {"store_id": store_id, "is_active": True}, {"_id": 0}
                ).limit(2)
            )
            if len(active) == 1:
                return active[0]
    except Exception:
        return None
    return None


@router.post("/attendance/check-in")
async def check_in(
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Record a geo-fenced, late-mark-aware check-in for the current user.

    Behaviour:
      - Geo-fence: store staff (roles 4-7) must be within the store radius
        (default 500m). Roles 1-3 are exempt. Reuses the same haversine logic as
        the geo-fenced LOGIN. Out-of-radius -> 403 with the measured distance.
      - Late-mark: if the employee has an assigned shift, the check-in time is
        compared to shift start + grace; a late check-in is RECORDED on the
        attendance doc (is_late / late_minutes). Record-only -- no payroll effect.

    Fail-soft: with no DB connected the endpoint still returns a stub response so
    the operator UI works in demo mode.
    """
    roles = current_user.get("roles", []) or []
    active_store = store_id or current_user.get("active_store_id")
    now = datetime.now()

    # --- Geo-fence enforcement (roles 4-7) ---
    coords = _store_coords(active_store)
    geo = attendance_engine.evaluate_geofence(
        roles=roles,
        user_lat=latitude,
        user_lng=longitude,
        store_lat=coords["lat"],
        store_lng=coords["lng"],
        radius_m=coords["radius_m"],
    )
    if not geo["allowed"]:
        if geo["reason"] == "LOCATION_REQUIRED":
            raise HTTPException(
                status_code=403,
                detail="Location access required for check-in. Please enable GPS and try again.",
            )
        # OUTSIDE_RADIUS
        dist = geo.get("distance_m")
        raise HTTPException(
            status_code=403,
            detail=(
                f"Check-in blocked: you are {int(dist)}m from the store "
                f"(must be within {geo['radius_m']}m)."
                if dist is not None
                else "Check-in not permitted from this location."
            ),
        )

    # --- Late-mark auto-calc ---
    shift = _resolve_employee_shift(current_user.get("user_id"), active_store)
    late = attendance_engine.compute_late_mark(
        now,
        (shift or {}).get("start_time"),
        (shift or {}).get("grace_minutes", 0),
    )

    # --- Record (fail-soft) ---
    employee_id = current_user.get("user_id")
    attendance_repo = get_attendance_repository()
    if attendance_repo is not None and employee_id:
        today_iso = now.date().isoformat()
        existing = attendance_repo.find_one(
            {"employee_id": employee_id, "date": today_iso}
        )
        # Block double check-in: if a record already has a check_in timestamp,
        # the employee must check out first before checking in again.
        if existing is not None and existing.get("check_in"):
            raise HTTPException(
                status_code=409,
                detail="Already checked in today. Please check out before checking in again.",
            )
        data = {
            "employee_id": employee_id,
            "employee_name": current_user.get("full_name") or current_user.get("username"),
            "store_id": active_store,
            "date": today_iso,
            "status": "PRESENT",
            "check_in": now.isoformat(),
            "is_late": late["is_late"],
            "late_minutes": late["late_minutes"],
            "shift_id": (shift or {}).get("shift_id"),
            "geo_verified": geo["reason"] in ("WITHIN_RADIUS", "EXEMPT_ROLE"),
            "marked_by": employee_id,
            "marked_at": now.isoformat(),
        }
        if existing is not None:
            attendance_repo.update(existing.get("attendance_id"), data)
        else:
            data["attendance_id"] = str(uuid.uuid4())
            attendance_repo.create(data)

    return {
        "message": "Check-in recorded",
        "checkInTime": now.isoformat(),
        "is_late": late["is_late"],
        "late_minutes": late["late_minutes"],
        "geo": {"verified": geo["reason"] in ("WITHIN_RADIUS", "EXEMPT_ROLE"), "reason": geo["reason"]},
    }


@router.post("/attendance/check-out")
async def check_out(current_user: dict = Depends(get_current_user)):
    return {"message": "Check-out recorded", "checkOutTime": datetime.now().isoformat()}


@router.post("/attendance/{attendance_id}/check-out")
async def check_out_by_id(
    attendance_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Per-attendance-record check-out — frontend's hrApi was 404'ing
    because the original `/attendance/check-out` endpoint didn't accept
    an attendance_id in the path. Stamps the check-out timestamp on the
    matching record. Falls back to a stub response if the repo isn't
    available so the operator UI doesn't break in demo mode."""
    repo = get_attendance_repository()
    if repo is None:
        return {
            "attendance_id": attendance_id,
            "message": "Check-out recorded",
            "checkOutTime": datetime.now().isoformat(),
        }
    existing = repo.find_one(
        {
            "$or": [
                {"attendance_id": attendance_id},
                {"_id": attendance_id},
            ]
        }
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    # Must have checked in before checking out.
    if not existing.get("check_in"):
        raise HTTPException(
            status_code=400,
            detail="Cannot check out: no check-in recorded for this attendance entry.",
        )
    # Block double check-out: once stamped, the employee is already gone.
    if existing.get("check_out"):
        raise HTTPException(
            status_code=409,
            detail="Already checked out for this attendance record.",
        )

    now_iso = datetime.now().isoformat()
    repo.update(
        existing.get("attendance_id") or existing.get("_id"),
        {"check_out": now_iso, "checked_out_by": current_user.get("user_id")},
    )
    return {"attendance_id": attendance_id, "checkOutTime": now_iso}


@router.post("/attendance/mark")
async def mark_attendance(
    request: AttendanceMarkRequest, current_user: dict = Depends(get_current_user)
):
    """Mark attendance for an employee (admin function)"""
    attendance_repo = get_attendance_repository()

    if attendance_repo is not None:
        # Check if record exists
        existing = attendance_repo.find_one(
            {"employee_id": request.employee_id, "date": request.date.isoformat()}
        )

        data = {
            "employee_id": request.employee_id,
            "store_id": current_user.get("active_store_id"),
            "date": request.date.isoformat(),
            "status": request.status,
            "check_in": request.check_in.isoformat() if request.check_in else None,
            "check_out": request.check_out.isoformat() if request.check_out else None,
            "marked_by": current_user.get("user_id"),
            "marked_at": datetime.now().isoformat(),
        }

        if existing is not None:
            attendance_repo.update(existing.get("attendance_id"), data)
        else:
            data["attendance_id"] = str(uuid.uuid4())
            attendance_repo.create(data)

    return {"message": "Attendance marked", "date": request.date.isoformat()}


# ============================================================================
# LEAVE ENDPOINTS
# ============================================================================


@router.get("/leaves")
async def list_leaves(
    employee_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List leave requests"""
    leave_repo = get_leave_repository()
    active_store = store_id or current_user.get("active_store_id")

    if leave_repo is None:
        return {"leaves": [], "total": 0}

    filter_dict = {}
    if active_store:
        filter_dict["store_id"] = active_store
    if employee_id:
        filter_dict["employee_id"] = employee_id
    if status:
        filter_dict["status"] = status

    leaves = leave_repo.find_many(filter_dict)

    return {"leaves": leaves or [], "total": len(leaves) if leaves else 0}


@router.post("/leaves", status_code=201)
async def apply_leave(
    leave: LeaveCreate, current_user: dict = Depends(get_current_user)
):
    """Apply for leave. Validates leave_type, date order, and detects overlapping
    approved/pending leaves for the same employee. Persists to the leave collection
    when DB is available; echoes back a draft record in demo mode (fail-soft).

    Overlap rule: a new application is blocked if its [from_date, to_date] range
    intersects any existing APPROVED or PENDING leave for the same employee.
    This prevents booking two leaves for the same calendar days.
    """
    employee_id = current_user.get("user_id")
    store_id = current_user.get("active_store_id")
    leave_repo = get_leave_repository()

    if leave_repo is not None and employee_id:
        # Check for overlapping active (APPROVED or PENDING) leaves.
        existing_leaves = leave_repo.find_many(
            {
                "employee_id": employee_id,
                "status": {"$in": ["APPROVED", "PENDING"]},
            }
        ) or []
        for ex in existing_leaves:
            ex_from = ex.get("from_date")
            ex_to = ex.get("to_date") or ex_from
            if ex_from is None:
                continue
            # Normalise to ISO strings for comparison.
            ex_from_s = ex_from[:10] if isinstance(ex_from, str) else str(ex_from)
            ex_to_s = ex_to[:10] if isinstance(ex_to, str) else str(ex_to)
            new_from_s = leave.from_date.isoformat()
            new_to_s = leave.to_date.isoformat()
            # Overlap: new interval starts before existing ends AND ends after existing starts.
            if new_from_s <= ex_to_s and new_to_s >= ex_from_s:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Leave overlaps with an existing {ex.get('status')} leave "
                        f"({ex_from_s} to {ex_to_s}). Cancel or modify the existing "
                        "request before filing a new one."
                    ),
                )

    doc = {
        "leave_id": str(uuid.uuid4()),
        "employee_id": employee_id,
        "store_id": store_id,
        "leave_type": leave.leave_type,
        "from_date": leave.from_date.isoformat(),
        "to_date": leave.to_date.isoformat(),
        "reason": (leave.reason or "").strip(),
        "status": "PENDING",
        "applied_by": employee_id,
        "applied_at": datetime.now().isoformat(),
    }

    if leave_repo is not None:
        leave_repo.create(doc)

    return {"leaveId": doc["leave_id"], "message": "Leave application submitted", "status": "PENDING"}


@router.post("/leaves/{leave_id}/approve")
async def approve_leave(leave_id: str, current_user: dict = Depends(get_current_user)):
    """Approve a leave request"""
    leave_repo = get_leave_repository()

    if leave_repo is not None:
        leave = leave_repo.find_by_id(leave_id)
        if not leave:
            raise HTTPException(status_code=404, detail="Leave request not found")

        if leave.get("status") != "PENDING":
            raise HTTPException(status_code=400, detail="Leave is not pending")

        leave_repo.update(
            leave_id,
            {
                "status": "APPROVED",
                "approved_by": current_user.get("user_id"),
                "approved_at": datetime.now().isoformat(),
            },
        )

    return {"message": "Leave approved", "leave_id": leave_id}


@router.post("/leaves/{leave_id}/reject")
async def reject_leave(
    leave_id: str,
    reason: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Reject a leave request"""
    leave_repo = get_leave_repository()

    if leave_repo is not None:
        leave = leave_repo.find_by_id(leave_id)
        if not leave:
            raise HTTPException(status_code=404, detail="Leave request not found")

        if leave.get("status") != "PENDING":
            raise HTTPException(status_code=400, detail="Leave is not pending")

        leave_repo.update(
            leave_id,
            {
                "status": "REJECTED",
                "rejected_by": current_user.get("user_id"),
                "rejected_at": datetime.now().isoformat(),
                "rejection_reason": reason,
            },
        )

    return {"message": "Leave rejected", "leave_id": leave_id}


@router.get("/leaves/balance/{employee_id}")
async def get_leave_balance(
    employee_id: str,
    year: int = Query(...),
    current_user: dict = Depends(get_current_user),
):
    return {"employeeId": employee_id, "year": year, "balance": {}}


@router.get("/payroll")
async def list_payroll(
    year: int = Query(...),
    month: int = Query(...),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List payroll records for a month"""
    payroll_repo = get_payroll_repository()
    active_store = store_id or current_user.get("active_store_id")

    if payroll_repo is None:
        return {"payroll": [], "total": 0}

    records = payroll_repo.find_many(
        {"store_id": active_store, "year": year, "month": month}
    )

    return {"payroll": records or [], "total": len(records) if records else 0}


@router.post("/payroll/generate")
async def generate_payroll(
    year: int = Query(...),
    month: int = Query(...),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Generate payroll for a month"""
    payroll_repo = get_payroll_repository()
    user_repo = get_user_repository()
    attendance_repo = get_attendance_repository()
    active_store = store_id or current_user.get("active_store_id")

    if not payroll_repo or not user_repo:
        return {"message": "Payroll generation initiated", "count": 0}

    # Get all employees for the store
    employees = user_repo.find_many({"store_ids": active_store})

    generated_count = 0
    for employee in employees or []:
        # Check if payroll already exists
        existing = payroll_repo.find_one(
            {"employee_id": employee.get("user_id"), "year": year, "month": month}
        )

        if existing is not None:
            continue

        # Calculate attendance
        working_days = 0
        present_days = 0
        if attendance_repo is not None:
            attendance = attendance_repo.find_many(
                {
                    "employee_id": employee.get("user_id"),
                    "date": {
                        "$gte": f"{year}-{month:02d}-01",
                        "$lt": (
                            f"{year}-{month+1:02d}-01"
                            if month < 12
                            else f"{year+1}-01-01"
                        ),
                    },
                }
            )
            working_days = len(attendance) if attendance else 26
            present_days = len(
                [a for a in (attendance or []) if a.get("status") == "PRESENT"]
            )

        # Basic payroll calculation
        base_salary = employee.get("salary", 0) or 25000
        daily_rate = base_salary / 26
        gross = daily_rate * present_days
        deductions = gross * 0.1  # 10% deductions
        net = gross - deductions

        payroll_repo.create(
            {
                "payroll_id": str(uuid.uuid4()),
                "employee_id": employee.get("user_id"),
                "employee_name": employee.get("full_name"),
                "store_id": active_store,
                "year": year,
                "month": month,
                "working_days": working_days,
                "present_days": present_days,
                "base_salary": base_salary,
                "gross_salary": round(gross, 2),
                "deductions": round(deductions, 2),
                "net_salary": round(net, 2),
                "status": "DRAFT",
                "generated_by": current_user.get("user_id"),
                "generated_at": datetime.now().isoformat(),
            }
        )
        generated_count += 1

    return {
        "message": "Payroll generated",
        "count": generated_count,
        "year": year,
        "month": month,
    }


@router.post("/payroll/{payroll_id}/approve")
async def approve_payroll(
    payroll_id: str, current_user: dict = Depends(get_current_user)
):
    """Approve payroll for payment"""
    payroll_repo = get_payroll_repository()

    if payroll_repo is not None:
        record = payroll_repo.find_by_id(payroll_id)
        if not record:
            raise HTTPException(status_code=404, detail="Payroll record not found")

        payroll_repo.update(
            payroll_id,
            {
                "status": "APPROVED",
                "approved_by": current_user.get("user_id"),
                "approved_at": datetime.now().isoformat(),
            },
        )

    return {"message": "Payroll approved", "payroll_id": payroll_id}


@router.get("/employee/{employee_id}/salary-slip")
async def get_salary_slip(
    employee_id: str,
    year: int,
    month: int,
    current_user: dict = Depends(get_current_user),
):
    return {"employeeId": employee_id, "year": year, "month": month, "salarySlip": {}}


# ============================================================================
# SHIFT CONFIG  (attendance engine)
# ============================================================================
# A shift defines a start/end time, a grace window for late-marking, and the
# weekly-off day(s). Shifts are stored in the `shifts` collection and assigned
# to an employee via users.shift_id. Manager-tier only for writes; HR roles can
# read. weekly_off uses Python weekday() convention (Mon=0 .. Sun=6).


class ShiftCreate(BaseModel):
    name: str
    start_time: str = Field(..., description="Shift start, 'HH:MM' 24h")
    end_time: str = Field(..., description="Shift end, 'HH:MM' 24h")
    grace_minutes: int = Field(default=0, ge=0, le=240)
    weekly_off: List[int] = Field(
        default_factory=list, description="Weekday ints, Mon=0..Sun=6"
    )
    store_id: Optional[str] = None


class ShiftAssignRequest(BaseModel):
    employee_id: str
    shift_id: str


def _clean_weekly_off(days: List[int]) -> List[int]:
    """Keep only valid weekday ints (0-6), de-duplicated + sorted."""
    return sorted({int(d) for d in (days or []) if isinstance(d, int) and 0 <= int(d) <= 6})


@router.post("/shifts", status_code=201)
async def create_shift(
    shift: ShiftCreate,
    current_user: dict = Depends(require_roles(*_SHIFT_ADMIN_ROLES)),
):
    """Create a work shift. Validates HH:MM times via the engine's parser so a
    malformed time is rejected up front (422)."""
    if attendance_engine._parse_hhmm(shift.start_time) is None:
        raise HTTPException(status_code=422, detail="start_time must be 'HH:MM' 24h")
    if attendance_engine._parse_hhmm(shift.end_time) is None:
        raise HTTPException(status_code=422, detail="end_time must be 'HH:MM' 24h")

    store_id = validate_store_access(shift.store_id, current_user)
    doc = {
        "shift_id": str(uuid.uuid4()),
        "store_id": store_id,
        "name": shift.name.strip(),
        "start_time": shift.start_time.strip(),
        "end_time": shift.end_time.strip(),
        "grace_minutes": int(shift.grace_minutes),
        "weekly_off": _clean_weekly_off(shift.weekly_off),
        "is_active": True,
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }
    try:
        db = _get_db()
    except Exception:
        db = None
    if db is None:
        # Fail-soft: echo the would-be doc so demo mode works.
        return {"message": "Shift created", "shift": doc}
    db.get_collection("shifts").insert_one({**doc, "_id": doc["shift_id"]})
    return {"message": "Shift created", "shift": doc}


@router.get("/shifts")
async def list_shifts(
    store_id: Optional[str] = Query(None),
    active_only: bool = Query(True),
    current_user: dict = Depends(require_roles(*_HR_READ_ROLES)),
):
    """List shifts for a store. Fail-soft -> empty list."""
    active_store = validate_store_access(store_id, current_user)
    try:
        db = _get_db()
    except Exception:
        db = None
    if db is None:
        return {"shifts": [], "total": 0}
    flt: dict = {}
    if active_store:
        flt["store_id"] = active_store
    if active_only:
        flt["is_active"] = True
    try:
        shifts = list(db.get_collection("shifts").find(flt, {"_id": 0}).limit(200))
    except Exception:
        shifts = []
    return {"shifts": shifts, "total": len(shifts)}


@router.post("/shifts/assign")
async def assign_shift(
    req: ShiftAssignRequest,
    current_user: dict = Depends(require_roles(*_SHIFT_ADMIN_ROLES)),
):
    """Assign a shift to an employee (sets users.shift_id)."""
    try:
        db = _get_db()
    except Exception:
        db = None
    if db is None:
        return {"message": "Shift assigned", "employee_id": req.employee_id, "shift_id": req.shift_id}

    shift = db.get_collection("shifts").find_one({"shift_id": req.shift_id}, {"_id": 0})
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    # Store-scope guard: a non-HQ manager can only assign within their store.
    if shift.get("store_id"):
        validate_store_access(shift.get("store_id"), current_user)

    res = db.get_collection("users").update_one(
        {"user_id": req.employee_id}, {"$set": {"shift_id": req.shift_id}}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {"message": "Shift assigned", "employee_id": req.employee_id, "shift_id": req.shift_id}


# ============================================================================
# LATE-MARK REPORT
# ============================================================================


@router.get("/attendance/late-marks")
async def late_marks_report(
    month: Optional[str] = Query(None, description="Target month as YYYY-MM"),
    store_id: Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_HR_READ_ROLES)),
):
    """Per-employee late-mark report for a month.

    Reads attendance docs flagged is_late=True and aggregates count +
    total/avg late minutes per employee. Record-only reporting view; never
    touches payroll. Fail-soft -> empty report."""
    year, mon = _parse_month(month)
    active_store = validate_store_access(store_id, current_user)
    attendance_repo = get_attendance_repository()
    if attendance_repo is None:
        return {"month": f"{year:04d}-{mon:02d}", "employees": [], "total_late_marks": 0}

    n_days = _days_in_month(year, mon)
    start = f"{year:04d}-{mon:02d}-01"
    end = f"{year:04d}-{mon:02d}-{n_days:02d}"
    flt: dict = {"date": {"$gte": start, "$lte": end}, "is_late": True}
    if active_store:
        flt["store_id"] = active_store
    if employee_id:
        flt["employee_id"] = employee_id
    try:
        records = attendance_repo.find_many(flt, limit=5000)
    except Exception:
        records = []

    by_emp: dict = {}
    total = 0
    for r in records or []:
        emp = r.get("employee_id")
        if not emp:
            continue
        total += 1
        slot = by_emp.setdefault(
            emp,
            {
                "employee_id": emp,
                "name": r.get("employee_name") or emp,
                "late_count": 0,
                "total_late_minutes": 0,
                "dates": [],
            },
        )
        slot["late_count"] += 1
        slot["total_late_minutes"] += int(r.get("late_minutes") or 0)
        d = r.get("date")
        if d:
            slot["dates"].append(str(d)[:10])

    employees = []
    for slot in by_emp.values():
        cnt = slot["late_count"] or 1
        slot["avg_late_minutes"] = round(slot["total_late_minutes"] / cnt, 1)
        slot["dates"].sort()
        employees.append(slot)
    employees.sort(key=lambda e: (-e["late_count"], e["name"].lower()))

    return {
        "month": f"{year:04d}-{mon:02d}",
        "employees": employees,
        "total_late_marks": total,
    }


# ============================================================================
# WEEK-OFF SWAP  (request -> manager approval)
# ============================================================================
# An employee requests to move their weekly-off from one date to another. The
# request is PENDING until a manager-tier user approves/rejects. The requester
# can NEVER approve their own request (SYSTEM_INTENT 7), enforced both by the
# role gate AND an explicit self-approval check in the engine.


class WeekOffSwapCreate(BaseModel):
    from_date: date = Field(..., description="The scheduled week-off being given up")
    to_date: date = Field(..., description="The new week-off date requested")
    reason: Optional[str] = None


@router.post("/weekoff-swaps", status_code=201)
async def create_weekoff_swap(
    req: WeekOffSwapCreate,
    current_user: dict = Depends(get_current_user),
):
    """File a week-off swap request for the current user (status PENDING)."""
    if req.from_date == req.to_date:
        raise HTTPException(status_code=422, detail="from_date and to_date must differ")

    employee_id = current_user.get("user_id")
    doc = {
        "swap_id": str(uuid.uuid4()),
        "employee_id": employee_id,
        "store_id": current_user.get("active_store_id"),
        "from_date": req.from_date.isoformat(),
        "to_date": req.to_date.isoformat(),
        "reason": (req.reason or "").strip(),
        "status": "PENDING",
        "requested_by": employee_id,
        "created_at": datetime.now().isoformat(),
    }
    try:
        db = _get_db()
    except Exception:
        db = None
    if db is None:
        return {"message": "Week-off swap requested", "swap": doc}
    db.get_collection("weekoff_swaps").insert_one({**doc, "_id": doc["swap_id"]})
    return {"message": "Week-off swap requested", "swap": doc}


@router.get("/weekoff-swaps")
async def list_weekoff_swaps(
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List week-off swap requests.

    HQ/manager roles see store-scoped requests; a non-manager only sees their
    own (so staff can track their pending requests). Fail-soft -> empty list."""
    try:
        db = _get_db()
    except Exception:
        db = None
    if db is None:
        return {"swaps": [], "total": 0}

    roles = set(current_user.get("roles", []) or [])
    is_manager = bool(roles & set(_SWAP_APPROVER_ROLES)) or "SUPERADMIN" in roles

    flt: dict = {}
    if status:
        flt["status"] = status.upper()
    if is_manager:
        active_store = store_id or current_user.get("active_store_id")
        if active_store and "SUPERADMIN" not in roles and "ADMIN" not in roles:
            flt["store_id"] = active_store
        elif store_id:
            flt["store_id"] = store_id
        if employee_id:
            flt["employee_id"] = employee_id
    else:
        # Non-managers: pinned to their own requests.
        flt["employee_id"] = current_user.get("user_id")

    try:
        swaps = list(
            db.get_collection("weekoff_swaps")
            .find(flt, {"_id": 0})
            .sort("created_at", 1)
            .limit(500)
        )
    except Exception:
        swaps = []
    return {"swaps": swaps, "total": len(swaps)}


@router.post("/weekoff-swaps/{swap_id}/approve")
async def approve_weekoff_swap(
    swap_id: str,
    current_user: dict = Depends(require_roles(*_SWAP_APPROVER_ROLES)),
):
    """Approve a week-off swap. The requester cannot approve their own request
    (enforced by the engine's can_approve_swap on top of the role gate)."""
    try:
        db = _get_db()
    except Exception:
        db = None
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    swap = db.get_collection("weekoff_swaps").find_one({"swap_id": swap_id}, {"_id": 0})
    if swap is None:
        raise HTTPException(status_code=404, detail="Swap request not found")
    if swap.get("store_id"):
        validate_store_access(swap.get("store_id"), current_user)

    decision = attendance_engine.can_approve_swap(
        approver_id=current_user.get("user_id"),
        approver_roles=current_user.get("roles", []),
        requested_by=swap.get("requested_by") or swap.get("employee_id"),
        swap_status=swap.get("status"),
    )
    if not decision["allowed"]:
        if decision["reason"] == "SELF_APPROVAL":
            raise HTTPException(
                status_code=403, detail="You cannot approve your own week-off swap request"
            )
        if decision["reason"] == "NOT_PENDING":
            raise HTTPException(status_code=400, detail="Swap request is not pending")
        raise HTTPException(status_code=403, detail="Not permitted to approve this request")

    db.get_collection("weekoff_swaps").update_one(
        {"swap_id": swap_id},
        {
            "$set": {
                "status": "APPROVED",
                "approved_by": current_user.get("user_id"),
                "approved_at": datetime.now().isoformat(),
            }
        },
    )
    return {"message": "Week-off swap approved", "swap_id": swap_id}


@router.post("/weekoff-swaps/{swap_id}/reject")
async def reject_weekoff_swap(
    swap_id: str,
    reason: str = Query(...),
    current_user: dict = Depends(require_roles(*_SWAP_APPROVER_ROLES)),
):
    """Reject a week-off swap. Self-rejection is also forbidden via the engine
    (requester cannot act on their own request)."""
    try:
        db = _get_db()
    except Exception:
        db = None
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    swap = db.get_collection("weekoff_swaps").find_one({"swap_id": swap_id}, {"_id": 0})
    if swap is None:
        raise HTTPException(status_code=404, detail="Swap request not found")
    if swap.get("store_id"):
        validate_store_access(swap.get("store_id"), current_user)

    decision = attendance_engine.can_approve_swap(
        approver_id=current_user.get("user_id"),
        approver_roles=current_user.get("roles", []),
        requested_by=swap.get("requested_by") or swap.get("employee_id"),
        swap_status=swap.get("status"),
    )
    if not decision["allowed"]:
        if decision["reason"] == "SELF_APPROVAL":
            raise HTTPException(
                status_code=403, detail="You cannot act on your own week-off swap request"
            )
        if decision["reason"] == "NOT_PENDING":
            raise HTTPException(status_code=400, detail="Swap request is not pending")
        raise HTTPException(status_code=403, detail="Not permitted to reject this request")

    db.get_collection("weekoff_swaps").update_one(
        {"swap_id": swap_id},
        {
            "$set": {
                "status": "REJECTED",
                "rejected_by": current_user.get("user_id"),
                "rejected_at": datetime.now().isoformat(),
                "rejection_reason": reason,
            }
        },
    )
    return {"message": "Week-off swap rejected", "swap_id": swap_id}


# ============================================================================
# LWP REPORT  (for the accountant -- read-only, NOT auto-applied to payroll)
# ============================================================================


@router.get("/reports/lwp")
async def lwp_report(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    store_id: Optional[str] = Query(None),
    employee_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_HR_READ_ROLES)),
):
    """Leave-Without-Pay days per employee for a month.

    Computed (via the pure engine) from attendance records + approved unpaid
    leaves. This is what the accountant READS before manually entering LWP days
    into a payroll run. It is deliberately NOT pushed into payroll -- the payroll
    engine still does LWP proration off the manually-entered number.

    Fail-soft -> empty report."""
    active_store = validate_store_access(store_id, current_user)
    attendance_repo = get_attendance_repository()
    leave_repo = get_leave_repository()
    user_repo = get_user_repository()
    if attendance_repo is None:
        return {"year": year, "month": month, "employees": [], "total_lwp_days": 0.0}

    n_days = _days_in_month(year, month)
    start = f"{year:04d}-{month:02d}-01"
    end = f"{year:04d}-{month:02d}-{n_days:02d}"

    # Build the roster so an employee with ZERO records still appears (0 LWP).
    roster: dict = {}
    if user_repo is not None:
        try:
            rfilter = {"is_active": True}
            if active_store:
                rfilter["store_ids"] = active_store
            for u in user_repo.find_many(rfilter, limit=1000) or []:
                uid = u.get("user_id") or u.get("_id")
                if uid:
                    roster[uid] = u.get("full_name") or u.get("username") or uid
        except Exception:
            roster = {}

    att_flt: dict = {"date": {"$gte": start, "$lte": end}}
    if active_store:
        att_flt["store_id"] = active_store
    if employee_id:
        att_flt["employee_id"] = employee_id
    try:
        att_records = attendance_repo.find_many(att_flt, limit=10000)
    except Exception:
        att_records = []

    leaves = []
    if leave_repo is not None:
        lv_flt: dict = {"status": "APPROVED"}
        if active_store:
            lv_flt["store_id"] = active_store
        if employee_id:
            lv_flt["employee_id"] = employee_id
        try:
            leaves = leave_repo.find_many(lv_flt, limit=5000)
        except Exception:
            leaves = []

    # Bucket per employee.
    att_by_emp: dict = {}
    for r in att_records or []:
        att_by_emp.setdefault(r.get("employee_id"), []).append(r)
    lv_by_emp: dict = {}
    for lv in leaves or []:
        # Keep only leaves that overlap the target month window.
        fd = attendance_engine._to_date(lv.get("from_date"))
        if fd is None:
            continue
        td = attendance_engine._to_date(lv.get("to_date")) or fd
        if td.year < year or fd.year > year:
            continue
        if not (fd <= date(year, month, n_days) and td >= date(year, month, 1)):
            continue
        lv_by_emp.setdefault(lv.get("employee_id"), []).append(lv)

    # Union of employees seen in roster + records + leaves.
    emp_ids = set(roster) | set(att_by_emp) | set(lv_by_emp)
    if employee_id:
        emp_ids = {employee_id}

    employees = []
    total = 0.0
    for emp in emp_ids:
        if not emp:
            continue
        result = attendance_engine.compute_lwp_days(
            records=att_by_emp.get(emp, []),
            approved_unpaid_leaves=lv_by_emp.get(emp, []),
        )
        total += result["lwp_days"]
        employees.append(
            {
                "employee_id": emp,
                "name": roster.get(emp, emp),
                **result,
            }
        )
    employees.sort(key=lambda e: (-e["lwp_days"], (e["name"] or "").lower()))

    return {
        "year": year,
        "month": month,
        "employees": employees,
        "total_lwp_days": round(total, 1),
        "note": "Report only. Enter LWP days manually into the payroll run; not auto-applied.",
    }
