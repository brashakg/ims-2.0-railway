"""
IMS 2.0 - HR Router
====================
Real database queries for attendance, leaves, and payroll
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List
from datetime import date, datetime
from ..utils.ist import now_ist, ist_today, now_ist_naive
from calendar import monthrange
import uuid
from .auth import get_current_user, require_roles
from ..dependencies import (
    get_attendance_repository,
    get_audit_repository,
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
        "CASUAL",
        "SICK",
        "EARNED",
        "PRIVILEGE",
        "MATERNITY",
        "PATERNITY",
        "UNPAID",
        "LWP",
        "LOP",
    }
)

# F26 remote-approval: leave types that are eligible for the short-notice
# PIN-gated remote fast-path. Short-notice CASUAL / SICK leave is the case a
# manager must be able to action from another store / their phone; planned
# leave (EARNED/PRIVILEGE/MATERNITY etc.) rides the standard HR-page flow.
_FAST_PATH_LEAVE_TYPES = frozenset({"CASUAL", "SICK"})

# Fallback notice threshold (calendar days) below which a CASUAL/SICK leave is
# flagged fast_path. The live value is read from the E2 policy key
# "approval.leave_fastpath_days"; this is the no-E2 code default (packet sec 7).
_FAST_PATH_DAYS_DEFAULT = 2


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
        today = ist_today()
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
    def date_not_future(cls, v: "date") -> "date":
        # `v: "date"` is a forward-ref string: the field above is named `date`,
        # which shadows the imported `date` type for static analysis (pylint
        # E0602) -- the string annotation sidesteps the collision. `date.today()`
        # below still resolves to the import at runtime.
        if v > ist_today():
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


class AttendanceEditRequest(BaseModel):
    """Partial correction of an existing attendance row (admin function).

    Every field is optional -- only the keys provided are changed. Used by the
    PUT /attendance/{attendance_id} manager-edit endpoint so SUPERADMIN / ADMIN /
    STORE_MANAGER can fix a wrong status, missing/wrong stamp, or a row recorded
    on the wrong day. Mutating an immutable-by-staff record, so it is audit-logged.

    NOTE: the date field is named ``record_date`` internally (alias ``date`` on
    the wire) to avoid shadowing the imported ``date`` type -- a Python-level
    annotation collision (hr.py has no ``from __future__ import annotations``, so
    a field literally named ``date`` would make ``Optional[date]`` resolve to the
    field, not the type, and reject every value).
    """

    model_config = {"populate_by_name": True}

    status: Optional[str] = None
    record_date: Optional[date] = Field(default=None, alias="date")
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None
    is_late: Optional[bool] = None
    late_minutes: Optional[int] = None
    notes: Optional[str] = None
    # Sentinels so a caller can explicitly CLEAR a stamp (send null) vs. leave it
    # untouched (omit the key). Pydantic can't distinguish those for Optional
    # fields alone, so the endpoint uses model_fields_set.

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        normalised = v.strip().upper()
        if normalised not in _VALID_STATUSES:
            raise ValueError(
                f"status must be one of: {', '.join(sorted(_VALID_STATUSES))}"
            )
        return normalised

    @field_validator("record_date")
    @classmethod
    def date_not_future(cls, v):
        if v is not None and v > ist_today():
            raise ValueError("Attendance date cannot be in the future")
        return v

    @field_validator("late_minutes")
    @classmethod
    def late_minutes_non_negative(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("late_minutes cannot be negative")
        return v

    @model_validator(mode="after")
    def checkout_after_checkin(self) -> "AttendanceEditRequest":
        ci = self.check_in
        co = self.check_out
        if ci is not None and co is not None:
            if co.replace(tzinfo=None) <= ci.replace(tzinfo=None):
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
    today = now_ist()
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


def _pct_present(present: float, total_marked: float) -> float:
    """Percent of marked working-days the employee was present (present +
    half-day*0.5). 0.0 when no day was marked. Rounded to 1 dp."""
    if total_marked <= 0:
        return 0.0
    return round(100.0 * present / total_marked, 1)


def _summarise_grid(grid: dict) -> dict:
    """Roll a built grid into per-employee + per-store summary rollups.

    Pure (no DB): consumes the output of _build_grid so the counting logic stays
    in ONE place. Per employee: counts present/absent/half_day/leave/lwp/
    week_off/late, days_present (present + 0.5*half_day), days_marked, and
    pct_present. Per store: the same counts summed across that store's roster +
    an employee headcount. Also returns flat company-wide totals.
    """
    per_employee = []
    per_store: dict = {}

    for emp in grid.get("employees", []) or []:
        s = emp.get("summary", {}) or {}
        present = float(s.get("present", 0))
        half = float(s.get("half_day", 0))
        days_present = present + 0.5 * half
        days_marked = (
            present
            + half
            + float(s.get("absent", 0))
            + float(s.get("leave", 0))
            + float(s.get("lwp", 0))
            + float(s.get("week_off", 0))
        )
        emp_row = {
            "employee_id": emp.get("employee_id"),
            "name": emp.get("name", ""),
            "store_id": emp.get("store_id", ""),
            "present": int(s.get("present", 0)),
            "absent": int(s.get("absent", 0)),
            "half_day": int(s.get("half_day", 0)),
            "leave": int(s.get("leave", 0)),
            "lwp": int(s.get("lwp", 0)),
            "holiday": int(s.get("week_off", 0)),
            "late": int(s.get("late", 0)),
            "days_present": round(days_present, 1),
            "days_marked": round(days_marked, 1),
            "pct_present": _pct_present(days_present, days_marked),
        }
        per_employee.append(emp_row)

        sid = emp.get("store_id") or ""
        bucket = per_store.setdefault(
            sid,
            {
                "store_id": sid,
                "employees": 0,
                "present": 0,
                "absent": 0,
                "half_day": 0,
                "leave": 0,
                "lwp": 0,
                "holiday": 0,
                "late": 0,
                "days_present": 0.0,
            },
        )
        bucket["employees"] += 1
        for k in ("present", "absent", "half_day", "leave", "lwp", "holiday", "late"):
            bucket[k] += emp_row[k]
        bucket["days_present"] += days_present

    stores = []
    for bucket in per_store.values():
        bucket["days_present"] = round(bucket["days_present"], 1)
        stores.append(bucket)
    stores.sort(key=lambda b: b["store_id"])
    per_employee.sort(key=lambda e: (e.get("name") or "").lower())

    return {
        "month": grid.get("month"),
        "totals": grid.get("totals", {}),
        "stores": stores,
        "employees": per_employee,
    }


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
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

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


@router.get("/attendance/summary")
async def get_attendance_summary(
    month: Optional[str] = Query(None, description="Target month as YYYY-MM"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_HR_READ_ROLES)),
):
    """Monthly attendance SUMMARY rollups for the HR menu summary card.

    Returns per-store and per-employee counts (present / absent / half_day /
    leave / holiday / late), days_present, and % present, plus company-wide
    totals. Built from the SAME roster + records as /attendance/grid (so the two
    can never disagree), then aggregated. Store-scoped + fail-soft: no DB / no
    records => a valid empty summary, never a 500.
    """
    year, mon = _parse_month(month)
    active_store = validate_store_access(store_id, current_user)

    user_repo = get_user_repository()
    attendance_repo = get_attendance_repository()

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

    grid = _build_grid(year, mon, employees, records)
    return _summarise_grid(grid)


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


def _resolve_employee_shift(
    employee_id: str, store_id: Optional[str]
) -> Optional[dict]:
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
            return db.get_collection("shifts").find_one(
                {"shift_id": shift_id}, {"_id": 0}
            )
        # Fall back to the store's lone active shift, if unambiguous.
        if store_id:
            active = list(
                db.get_collection("shifts")
                .find({"store_id": store_id, "is_active": True}, {"_id": 0})
                .limit(2)
            )
            if len(active) == 1:
                return active[0]
    except Exception:
        return None
    return None


def _find_day_row(
    attendance_repo, employee_id: str, store_id: Optional[str], date_iso: str
):
    """Find an employee's single attendance row for a given day.

    The canonical key is (employee_id, date) where `date` is the date-only ISO
    STRING -- the exact shape the unique index, mark_attendance, and the grid all
    use. We deliberately key on (employee_id, date) and NOT on store_id so a row
    written with a missing/old store_id (legacy `mark` rows persisted
    active_store_id, which can be None) is still matched -- otherwise a second
    check-in would fail to see it and mint a duplicate, which is the exact bug
    being fixed. Fail-soft: any repo error -> None (treated as 'no row')."""
    try:
        row = attendance_repo.find_one({"employee_id": employee_id, "date": date_iso})
        if row is not None:
            return row
    except Exception:
        return None
    # Defensive fallback: a legacy row may have stored `date` as a datetime
    # (the repo's mark_check_in helper did). Match on the date prefix so we still
    # attach to it instead of duplicating. Only attempted when the string lookup
    # missed, so it never adds cost on the normal path.
    try:
        from datetime import datetime as _dt

        start = _dt.fromisoformat(f"{date_iso}T00:00:00")
        end = _dt.fromisoformat(f"{date_iso}T23:59:59")
        return attendance_repo.find_one(
            {"employee_id": employee_id, "date": {"$gte": start, "$lte": end}}
        )
    except Exception:
        return None


def _create_day_row_safe(attendance_repo, data: dict):
    """Insert a fresh attendance day-row, race-safe against the unique index.

    If two check-ins for the same (employee_id, date) race, the unique index
    makes the second insert raise DuplicateKeyError; we catch it and UPDATE the
    row the winner just created instead of surfacing a 500 or leaving a dup.
    Fail-soft otherwise (the BaseRepository.create already swallows errors)."""
    try:
        attendance_repo.create(data)
    except Exception:  # noqa: BLE001 - includes pymongo DuplicateKeyError
        existing = _find_day_row(
            attendance_repo,
            data.get("employee_id"),
            data.get("store_id"),
            data.get("date"),
        )
        if existing is not None:
            patch = {
                k: v
                for k, v in data.items()
                if k not in ("attendance_id", "date", "employee_id")
            }
            attendance_repo.update(
                existing.get("attendance_id") or existing.get("_id"), patch
            )


def _half_day_rule(store_id: Optional[str]) -> dict:
    """Resolve the auto half-day attendance rule for a store (settings-system).

    Returns {"auto": bool, "min_hours": float|None, "late_after": str|None}.
    DARK by default: hr.half_day_auto is False unless the owner turns it on, so
    attendance behaves exactly as before. Fail-soft -- any policy error resolves
    to auto=False (never blocks a check-in/out)."""
    try:
        from ..services.policy_engine import get_policy

        scope = {"store_id": store_id} if store_id else None
        if not bool(get_policy("hr.half_day_auto", scope, default=False)):
            return {"auto": False, "min_hours": None, "late_after": None}
        try:
            min_hours = float(get_policy("hr.half_day_min_hours", scope, default=4.0))
        except (TypeError, ValueError):
            min_hours = 4.0
        late_after = get_policy("hr.half_day_late_after", scope, default="13:00")
        if not isinstance(late_after, str) or not late_after.strip():
            late_after = None
        return {"auto": True, "min_hours": min_hours, "late_after": late_after}
    except Exception:  # noqa: BLE001 -- attendance must never 500 on a policy read
        return {"auto": False, "min_hours": None, "late_after": None}


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
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    now = now_ist_naive()

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

    # --- Half-day auto-rule (DARK by default; configured in settings-system) ---
    # At check-in only the late-arrival trigger can fire (no check-out yet); the
    # hours-below-min trigger is re-evaluated at check-out.
    hd_rule = _half_day_rule(active_store)
    checkin_half_day = False
    if hd_rule["auto"]:
        checkin_half_day = attendance_engine.classify_half_day(
            check_in=now,
            check_out=None,
            min_hours=hd_rule["min_hours"],
            late_after=hd_rule["late_after"],
        )["is_half_day"]

    # --- Record (fail-soft, IDEMPOTENT) ---
    # Key the day-row on (employee_id, date) -- the SAME shape mark_attendance
    # and the grid use (date = a date-only ISO STRING), which is exactly the
    # collection's unique index. A second check-in the same day UPDATES the one
    # existing row (refreshes the stamp / late-mark) instead of inserting a
    # duplicate. Previously this raised 409 on re-check-in and, when no unique
    # index existed in prod (see connection.ensure_indexes), a datetime-vs-string
    # `date` mismatch or a race could slip a second row past the constraint --
    # the "same user recorded twice" bug. find-then-update closes that here; the
    # DB unique index is the backstop.
    employee_id = current_user.get("user_id")
    attendance_repo = get_attendance_repository()
    already_checked_in = False
    if attendance_repo is not None and employee_id:
        today_iso = now.date().isoformat()
        existing = _find_day_row(attendance_repo, employee_id, active_store, today_iso)
        already_checked_in = bool(existing and existing.get("check_in"))
        if existing is not None:
            # Preserve an EARLIER check-in time + its late-mark: the first stamp
            # of the day is the system of record. A repeat tap just keeps the row
            # PRESENT and never duplicates.
            base_status = (
                existing.get("status")
                if existing.get("status") in ("PRESENT", "HALF_DAY")
                else "PRESENT"
            )
            # Only ever DOWNGRADE a PRESENT day to HALF_DAY (the late-arrival
            # trigger); never touch a manual HALF_DAY / other status.
            if base_status == "PRESENT" and checkin_half_day:
                base_status = "HALF_DAY"
            update_data = {
                "employee_name": current_user.get("full_name")
                or current_user.get("username"),
                "store_id": existing.get("store_id") or active_store,
                "status": base_status,
                "shift_id": (shift or {}).get("shift_id") or existing.get("shift_id"),
                "geo_verified": geo["reason"] in ("WITHIN_RADIUS", "EXEMPT_ROLE"),
            }
            if not already_checked_in:
                update_data["check_in"] = now.isoformat()
                update_data["is_late"] = late["is_late"]
                update_data["late_minutes"] = late["late_minutes"]
                update_data["marked_by"] = employee_id
                update_data["marked_at"] = now.isoformat()
            attendance_repo.update(
                existing.get("attendance_id") or existing.get("_id"), update_data
            )
        else:
            data = {
                "attendance_id": str(uuid.uuid4()),
                "employee_id": employee_id,
                "employee_name": current_user.get("full_name")
                or current_user.get("username"),
                "store_id": active_store,
                "date": today_iso,
                "status": "HALF_DAY" if checkin_half_day else "PRESENT",
                "check_in": now.isoformat(),
                "is_late": late["is_late"],
                "late_minutes": late["late_minutes"],
                "shift_id": (shift or {}).get("shift_id"),
                "geo_verified": geo["reason"] in ("WITHIN_RADIUS", "EXEMPT_ROLE"),
                "marked_by": employee_id,
                "marked_at": now.isoformat(),
            }
            _create_day_row_safe(attendance_repo, data)

    return {
        "message": (
            "Already checked in today" if already_checked_in else "Check-in recorded"
        ),
        "checkInTime": now.isoformat(),
        "already_checked_in": already_checked_in,
        "is_late": late["is_late"],
        "late_minutes": late["late_minutes"],
        "geo": {
            "verified": geo["reason"] in ("WITHIN_RADIUS", "EXEMPT_ROLE"),
            "reason": geo["reason"],
        },
    }


@router.post("/attendance/check-out")
async def check_out(
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Check the current user OUT of TODAY's attendance row.

    Attaches the check-out stamp to the same (employee_id, store_id, date) day
    row that check-in created -- no second row, no orphan stamp. Previously this
    was a no-op stub that returned a timestamp but persisted nothing, so a
    staff-side "check out" never actually recorded. Fail-soft: with no DB / no
    row yet, returns a stub timestamp so the operator UI still works.
    """
    now = now_ist_naive()
    now_iso = now.isoformat()
    employee_id = current_user.get("user_id")
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    attendance_repo = get_attendance_repository()
    if attendance_repo is None or not employee_id:
        return {"message": "Check-out recorded", "checkOutTime": now_iso}

    today_iso = now.date().isoformat()
    existing = _find_day_row(attendance_repo, employee_id, active_store, today_iso)
    if existing is None:
        raise HTTPException(
            status_code=400,
            detail="No check-in recorded today. Please check in before checking out.",
        )
    if not existing.get("check_in"):
        raise HTTPException(
            status_code=400,
            detail="Cannot check out: no check-in recorded for today.",
        )
    if existing.get("check_out"):
        raise HTTPException(
            status_code=409,
            detail="Already checked out today.",
        )
    update = {"check_out": now_iso, "checked_out_by": employee_id}
    # Half-day auto-rule (DARK by default): now that the day is checked out, the
    # hours-below-min trigger is evaluable. Only DOWNGRADE a PRESENT day -- an
    # explicit ABSENT/LEAVE/manual HALF_DAY is never overridden.
    hd_rule = _half_day_rule(active_store)
    if hd_rule["auto"] and existing.get("status") == "PRESENT":
        hd = attendance_engine.classify_half_day(
            check_in=existing.get("check_in"),
            check_out=now_iso,
            min_hours=hd_rule["min_hours"],
            late_after=hd_rule["late_after"],
        )
        if hd["is_half_day"]:
            update["status"] = "HALF_DAY"
    attendance_repo.update(
        existing.get("attendance_id") or existing.get("_id"),
        update,
    )
    return {
        "attendance_id": existing.get("attendance_id") or existing.get("_id"),
        "message": "Check-out recorded",
        "checkOutTime": now_iso,
    }


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
    validate_store_access(existing.get("store_id"), current_user)

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
    """Mark attendance for an employee (admin function).

    De-dupes on (employee_id, date) via the shared day-row helpers so a re-mark
    UPDATES the one row (also catching a legacy datetime-stored date) and a race
    can't slip a duplicate past the unique index.
    """
    attendance_repo = get_attendance_repository()
    active_store = current_user.get("active_store_id")

    if attendance_repo is not None:
        date_iso = request.date.isoformat()
        existing = _find_day_row(
            attendance_repo, request.employee_id, active_store, date_iso
        )

        data = {
            "employee_id": request.employee_id,
            "store_id": active_store,
            "date": date_iso,
            "status": request.status,
            "check_in": request.check_in.isoformat() if request.check_in else None,
            "check_out": request.check_out.isoformat() if request.check_out else None,
            "marked_by": current_user.get("user_id"),
            "marked_at": datetime.now().isoformat(),
        }

        if existing is not None:
            attendance_repo.update(
                existing.get("attendance_id") or existing.get("_id"), data
            )
        else:
            data["attendance_id"] = str(uuid.uuid4())
            _create_day_row_safe(attendance_repo, data)

    return {"message": "Attendance marked", "date": request.date.isoformat()}


# Roles allowed to EDIT/correct an existing attendance row. Stricter than the
# read tier: a correction overrides what staff recorded, so it is limited to
# SUPERADMIN (auto via require_roles) + ADMIN + STORE_MANAGER and audit-logged.
_ATTENDANCE_EDIT_ROLES = ("ADMIN", "STORE_MANAGER")


def _audit_attendance_edit(current_user, attendance_id, before, after, changed):
    """Write an immutable audit row for a manager attendance correction.

    Fail-soft (SYSTEM_INTENT 'Audit Everything', but the correction must not 500
    because the audit write hiccuped). Records before/after states + the set of
    changed fields, keyed entity ATTENDANCE."""
    try:
        audit_repo = get_audit_repository()
        if audit_repo is None:
            return
        audit_repo.create(
            {
                "action": "ATTENDANCE_EDIT",
                "entity_type": "ATTENDANCE",
                "entity_id": attendance_id,
                "store_id": (after or {}).get("store_id")
                or (before or {}).get("store_id"),
                "user_id": current_user.get("user_id"),
                "user_name": current_user.get("full_name")
                or current_user.get("username"),
                "timestamp": datetime.utcnow(),
                "severity": "INFO",
                "source": "domain",
                "detail": {"changed_fields": changed},
                "before_state": before,
                "after_state": after,
            }
        )
    except Exception:  # noqa: BLE001 - audit must never break the correction
        pass


@router.put("/attendance/{attendance_id}")
async def edit_attendance(
    attendance_id: str,
    request: AttendanceEditRequest,
    current_user: dict = Depends(require_roles(*_ATTENDANCE_EDIT_ROLES)),
):
    """Correct an existing attendance row (SUPERADMIN / ADMIN / STORE_MANAGER).

    Owner-reported gap: managers had no way to fix a wrong attendance entry. This
    edits status / check_in / check_out / date / is_late / late_minutes / notes
    on one row (partial -- only the provided fields change; send a field as null
    to CLEAR it). Re-dating is guarded so it cannot collide with the employee's
    existing row on the target day (which the unique index would reject anyway).
    Every edit writes an immutable audit_logs entry with before/after state.
    """
    attendance_repo = get_attendance_repository()
    if attendance_repo is None:
        raise HTTPException(status_code=503, detail="Attendance store unavailable")

    existing = attendance_repo.find_one(
        {"$or": [{"attendance_id": attendance_id}, {"_id": attendance_id}]}
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    validate_store_access(existing.get("store_id"), current_user)

    # Only the fields the caller actually sent (so null != absent).
    sent = request.model_fields_set
    if not sent:
        raise HTTPException(status_code=422, detail="No fields provided to update")

    before = {k: v for k, v in existing.items() if k != "_id"}
    updates: dict = {}

    if "status" in sent:
        updates["status"] = request.status
    if "is_late" in sent:
        updates["is_late"] = request.is_late
    if "late_minutes" in sent:
        updates["late_minutes"] = request.late_minutes
    if "notes" in sent:
        updates["notes"] = (request.notes or "").strip() or None
    if "check_in" in sent:
        updates["check_in"] = request.check_in.isoformat() if request.check_in else None
    if "check_out" in sent:
        updates["check_out"] = (
            request.check_out.isoformat() if request.check_out else None
        )

    # Cross-field guard: the resulting check_out must still be after check_in,
    # accounting for the side NOT being edited (the model only validated the two
    # when both were sent in the same request).
    eff_ci = (
        updates.get("check_in", existing.get("check_in"))
        if ("check_in" in sent or existing.get("check_in"))
        else None
    )
    eff_co = (
        updates.get("check_out", existing.get("check_out"))
        if ("check_out" in sent or existing.get("check_out"))
        else None
    )
    if eff_ci and eff_co:
        ci_dt = attendance_engine._coerce_check_in(eff_ci)
        co_dt = attendance_engine._coerce_check_in(eff_co)
        if ci_dt and co_dt and co_dt <= ci_dt:
            raise HTTPException(
                status_code=422, detail="check_out must be after check_in"
            )

    if "record_date" in sent and request.record_date is not None:
        new_date_iso = request.record_date.isoformat()
        if new_date_iso != existing.get("date"):
            # Don't let a re-date duplicate the employee's day-row.
            clash = _find_day_row(
                attendance_repo,
                existing.get("employee_id"),
                existing.get("store_id"),
                new_date_iso,
            )
            if clash is not None and (
                clash.get("attendance_id") or clash.get("_id")
            ) != (existing.get("attendance_id") or existing.get("_id")):
                raise HTTPException(
                    status_code=409,
                    detail="Employee already has an attendance row on that date.",
                )
            updates["date"] = new_date_iso

    if not updates:
        raise HTTPException(status_code=422, detail="No effective changes to apply")

    updates["edited_by"] = current_user.get("user_id")
    updates["edited_at"] = datetime.now().isoformat()

    row_id = existing.get("attendance_id") or existing.get("_id")
    attendance_repo.update(row_id, updates)

    after = {**before, **updates}
    changed = [k for k in updates if k not in ("edited_by", "edited_at")]
    _audit_attendance_edit(current_user, row_id, before, after, changed)

    return {
        "message": "Attendance updated",
        "attendance_id": row_id,
        "changed_fields": changed,
        "record": {
            "attendanceId": row_id,
            "employeeId": after.get("employee_id", ""),
            "date": after.get("date", ""),
            "status": after.get("status", ""),
            "checkIn": after.get("check_in"),
            "checkOut": after.get("check_out"),
            "isLate": after.get("is_late"),
            "lateMinutes": after.get("late_minutes"),
            "storeId": after.get("store_id", ""),
        },
    }


# ============================================================================
# LEAVE remote-approval helpers (F26)
# ============================================================================
#
# F26 wires leave applications through the merged E4 ApprovalEngine so a manager
# can action a pending leave from a different store / device with their PIN. The
# E4 engine owns the PIN gate, the atomic single-use token, the store-binding at
# approve-time, and the audit chain -- this router only opens the request on
# apply, blocks self-approval, and stamps the leave on consume. No fork of E4.


def _fast_path_days_threshold(store_id: Optional[str], entity_id: Optional[str] = None) -> int:
    """Short-notice threshold (calendar days) from E2 policy with a code fallback.
    Never raises -- a missing key / no-DB returns the locked default of 2."""
    scope: dict = {}
    if store_id:
        scope["store_id"] = store_id
    if entity_id:
        scope["entity_id"] = entity_id
    try:
        from ..services.policy_engine import get_policy

        return int(get_policy("approval.leave_fastpath_days", scope or None,
                              default=_FAST_PATH_DAYS_DEFAULT))
    except Exception:  # noqa: BLE001 - fail-soft to the locked default
        return _FAST_PATH_DAYS_DEFAULT


def _is_fast_path_leave(leave_type: str, from_date: date, store_id: Optional[str]) -> bool:
    """A leave is fast-path when BOTH hold: the type is short-notice-eligible
    (CASUAL / SICK) AND the days-until-start is strictly below the threshold.
    Planned types or sufficient notice -> standard flow (False)."""
    if (leave_type or "").strip().upper() not in _FAST_PATH_LEAVE_TYPES:
        return False
    threshold = _fast_path_days_threshold(store_id)
    days_until = (from_date - ist_today()).days
    return days_until < threshold


def _notify_store_managers(store_id: Optional[str], leave_doc: dict, *, fast_path: bool) -> int:
    """Best-effort in-app bell to every active manager of the leave's store, so a
    manager who is not currently on the HR page (different store / their phone)
    still sees a pending leave. Fail-soft: a missing DB / collection is a silent
    no-op; never blocks the leave application. Returns the count written.

    Outbound WhatsApp is MEGAPHONE's job (DISPATCH_MODE-gated) and is intentionally
    not done synchronously here; the in-app bell has no external dependency, so a
    manager can always poll the approvals inbox even if MSG91 is down."""
    db = _get_db()
    if db is None:
        return 0
    # Managers of this store + HQ approvers. find_by_role is_active-filtered.
    user_repo = get_user_repository()
    recipients: List[str] = []
    if user_repo is not None and store_id:
        try:
            for role in ("STORE_MANAGER", "AREA_MANAGER", "ADMIN"):
                for u in (user_repo.find_by_role(role, store_id) or []):
                    uid = u.get("user_id")
                    if uid and uid not in recipients:
                        recipients.append(uid)
        except Exception:  # noqa: BLE001
            recipients = []
    try:
        ncoll = db.get_collection("notifications")
    except Exception:  # noqa: BLE001
        return 0
    if ncoll is None:
        return 0
    now = now_ist().isoformat()
    written = 0
    base = {
        "kind": "leave_request",
        "title": "Leave approval required" if fast_path else "Leave request filed",
        "message": (
            f"{leave_doc.get('leave_type')} leave "
            f"{leave_doc.get('from_date')} to {leave_doc.get('to_date')}"
        ),
        "for_roles": ["STORE_MANAGER", "AREA_MANAGER", "ADMIN"],
        "store_id": store_id,
        "leave_id": leave_doc.get("leave_id"),
        "approval_request_id": leave_doc.get("approval_request_id"),
        "urgent": bool(fast_path),
        "status": "PENDING",
        "source": "HR_LEAVE",
        "created_at": now,
    }
    targets = recipients or [None]  # store-less fallback: one role-addressed bell
    for uid in targets:
        try:
            row = dict(base)
            row["notification_id"] = f"NTF-LV-{uuid.uuid4().hex[:10]}"
            row["for_user"] = uid
            ncoll.insert_one(row)
            written += 1
        except Exception:  # noqa: BLE001
            continue
    return written


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
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if leave_repo is None:
        return {"leaves": [], "total": 0}

    filter_dict = {}
    if active_store:
        filter_dict["store_id"] = active_store
    if employee_id:
        filter_dict["employee_id"] = employee_id
    if status:
        filter_dict["status"] = status

    leaves = leave_repo.find_many(filter_dict, limit=0)

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
        existing_leaves = (
            leave_repo.find_many(
                {
                    "employee_id": employee_id,
                    "status": {"$in": ["APPROVED", "PENDING"]},
                }
            )
            or []
        )
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

    fast_path = _is_fast_path_leave(leave.leave_type, leave.from_date, store_id)

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
        # F26 remote-approval fields (additive; absence on legacy rows = standard).
        "fast_path": fast_path,
        "approval_request_id": None,
        "approved_via": None,
    }

    # F26: a short-notice CASUAL/SICK leave opens an E4 leave_approval request so it
    # surfaces in the manager's approvals inbox and can be actioned remotely with a
    # PIN. amount=None -> the E4 "auto" tier (STORE_MANAGER+). Fail-soft: if no DB /
    # the engine can't record it, the leave is still filed and the standard HR-page
    # approve path remains. dedupe_key keeps a re-submit from spawning a duplicate.
    if fast_path:
        try:
            from ..services.approvals import request_approval

            ar = request_approval(
                _get_db(),
                action_type="leave_approval",
                requested_by=employee_id,
                requested_by_roles=list(current_user.get("roles", []) or []),
                store_id=store_id,
                amount=None,
                context={
                    "leave_id": doc["leave_id"],
                    "employee_id": employee_id,
                    "leave_type": leave.leave_type,
                    "from_date": doc["from_date"],
                    "to_date": doc["to_date"],
                    "fast_path": True,
                },
                reason=(leave.reason or "").strip(),
                dedupe_key=f"leave:{doc['leave_id']}",
            )
            if ar and ar.get("request_id"):
                doc["approval_request_id"] = ar["request_id"]
        except Exception:  # noqa: BLE001 - approval is best-effort; leave still files
            pass

    if leave_repo is not None:
        leave_repo.create(doc)

    # In-app bell to store managers on every submission (fail-soft).
    _notify_store_managers(store_id, doc, fast_path=fast_path)

    return {
        "leaveId": doc["leave_id"],
        "message": "Leave application submitted",
        "status": "PENDING",
        "fast_path": fast_path,
        "approval_request_id": doc["approval_request_id"],
    }


@router.post("/leaves/{leave_id}/approve")
async def approve_leave(
    leave_id: str,
    current_user: dict = Depends(require_roles(*_SWAP_APPROVER_ROLES)),
):
    """Approve a leave request (ADMIN/AREA_MANAGER/STORE_MANAGER; SUPERADMIN auto).

    Was gated only by get_current_user -- so ANY authenticated user, including
    the applicant themselves, could approve their own leave. Leave approval is a
    manager action; gate it with the same approver roles the shift-swap approval
    flow uses (_SWAP_APPROVER_ROLES) for a consistent workforce-control policy.
    """
    leave_repo = get_leave_repository()

    if leave_repo is not None:
        leave = leave_repo.find_by_id(leave_id)
        if not leave:
            raise HTTPException(status_code=404, detail="Leave request not found")
        validate_store_access(leave.get("store_id"), current_user)

        # F26: self-approval is blocked. A manager applying for their own leave
        # cannot approve it -- a different eligible manager must. SUPERADMIN is no
        # exception: separation of duties holds for every actor.
        if leave.get("employee_id") == current_user.get("user_id"):
            raise HTTPException(status_code=403, detail="cannot_approve_own")

        if leave.get("status") != "PENDING":
            raise HTTPException(status_code=400, detail="Leave is not pending")

        leave_repo.update(
            leave_id,
            {
                "status": "APPROVED",
                "approved_by": current_user.get("user_id"),
                "approved_at": datetime.now().isoformat(),
                "approved_via": "standard",
            },
        )

    return {"message": "Leave approved", "leave_id": leave_id}


@router.post("/leaves/{leave_id}/reject")
async def reject_leave(
    leave_id: str,
    reason: str = Query(...),
    current_user: dict = Depends(require_roles(*_SWAP_APPROVER_ROLES)),
):
    """Reject a leave request (ADMIN/AREA_MANAGER/STORE_MANAGER; SUPERADMIN auto).

    Same manager gate as approve_leave -- rejecting a colleague's (or one's own)
    leave was previously open to any authenticated user.
    """
    leave_repo = get_leave_repository()

    if leave_repo is not None:
        leave = leave_repo.find_by_id(leave_id)
        if not leave:
            raise HTTPException(status_code=404, detail="Leave request not found")
        validate_store_access(leave.get("store_id"), current_user)

        # F26: a manager cannot reject their own leave either -- same separation
        # of duties as approve. (Cancelling one's own leave is a different flow.)
        if leave.get("employee_id") == current_user.get("user_id"):
            raise HTTPException(status_code=403, detail="cannot_approve_own")

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


class LeaveRemoteApprove(BaseModel):
    """Body for the remote fast-path leave approval. The approver has already
    approved the E4 leave_approval request from their approvals inbox (PIN-gated,
    store-bound, atomic) and holds the one-time approval_token it minted."""

    approval_token: str = Field(..., min_length=8)


@router.post("/leaves/{leave_id}/approve-remote")
async def approve_leave_remote(
    leave_id: str,
    body: LeaveRemoteApprove,
    current_user: dict = Depends(require_roles(*_SWAP_APPROVER_ROLES)),
):
    """Remote fast-path leave approval (F26).

    An eligible manager -- possibly at a different store, on their phone -- has
    approved the E4 ``leave_approval`` request in their approvals inbox with their
    PIN and received a single-use ``approval_token``. This endpoint spends that
    token EXACTLY ONCE via the merged ApprovalEngine (the atomic single-use guard,
    PIN gate, and store-binding all live there -- no fork) and, only on a clean
    consume, stamps the leave APPROVED with ``approved_via='fast_path'``.

    Self-approval is blocked: a manager cannot fast-path their own leave. The
    token's E4 context must reference THIS leave, so a token minted for one leave
    cannot be replayed against another.
    """
    leave_repo = get_leave_repository()
    if leave_repo is None:
        raise HTTPException(status_code=503, detail="Leave store unavailable")

    leave = leave_repo.find_by_id(leave_id)
    if not leave:
        raise HTTPException(status_code=404, detail="Leave request not found")

    # Self-approval block (defense-in-depth; the engine does not know employee_id).
    if leave.get("employee_id") == current_user.get("user_id"):
        raise HTTPException(status_code=403, detail="cannot_approve_own")

    if leave.get("status") != "PENDING":
        raise HTTPException(status_code=400, detail="Leave is not pending")

    from ..services.approvals import ApprovalEngine

    engine = ApprovalEngine(db=_get_db())

    # Bind the token to THIS leave BEFORE consuming so a token minted for a
    # different leave can never be spent here (the consume is single-use; we must
    # not burn it on the wrong leave). The request the token belongs to is fetched
    # by token via the engine's get-on-request_id path is not available, so we
    # look it up through the request_id recorded on the leave when it was opened.
    req_id = leave.get("approval_request_id")
    # A leave with NO recorded approval request was never sent for approval (e.g.
    # E4 was unavailable at apply time, fail-soft -> approval_request_id=None).
    # REFUSE remote-approve outright -- do NOT fall through to a token-only consume,
    # which would atomically BURN a token minted for a DIFFERENT leave (a griefing
    # token-burn). Such a leave must be re-filed / standard-approved instead.
    if not req_id:
        raise HTTPException(status_code=409, detail="no_approval_request")
    # Bind the token to THIS leave's request BEFORE consuming, so a token minted
    # for another leave can never reach (and burn) the single-use consume.
    req = engine.get(req_id)
    if not req or req.get("approval_token") != body.approval_token:
        raise HTTPException(status_code=400, detail="token_mismatch")
    if (req.get("context") or {}).get("leave_id") != leave_id:
        raise HTTPException(status_code=400, detail="token_mismatch")

    # Atomic single-use spend. The engine flips APPROVED -> CONSUMED in one op;
    # a replay returns already_consumed; an expired/rejected token is refused.
    res = engine.consume_approval(
        consumed_by=current_user.get("user_id"),
        action_type="leave_approval",
        approval_token=body.approval_token,
    )
    if not res.get("ok"):
        err = res.get("error")
        code_map = {
            "already_consumed": 409,
            "expired": 410,
            "action_mismatch": 400,
            "not_approved": 409,
            "not_found": 404,
            "no_db": 503,
        }
        raise HTTPException(status_code=code_map.get(err, 400), detail=err or "consume_failed")

    consumed_req = res.get("request") or {}
    # The consumed request must reference this leave (guards a token whose leave
    # link was not recorded on the leave doc, e.g. legacy / repaired rows).
    if (consumed_req.get("context") or {}).get("leave_id") not in (None, leave_id):
        raise HTTPException(status_code=400, detail="token_mismatch")

    leave_repo.update(
        leave_id,
        {
            "status": "APPROVED",
            "approved_by": current_user.get("user_id"),
            "approved_at": datetime.now().isoformat(),
            "approved_via": "fast_path",
        },
    )
    return {
        "message": "Leave approved",
        "leave_id": leave_id,
        "approved_via": "fast_path",
    }


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
    current_user: dict = Depends(require_roles(*_HR_READ_ROLES)),
):
    """List payroll records for a month"""
    payroll_repo = get_payroll_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

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
    current_user: dict = Depends(require_roles(*_HR_READ_ROLES)),
):
    """Generate payroll for a month"""
    payroll_repo = get_payroll_repository()
    user_repo = get_user_repository()
    attendance_repo = get_attendance_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if not payroll_repo or not user_repo:
        return {"message": "Payroll generation initiated", "count": 0}

    # Get all employees for the store
    employees = user_repo.find_many({"store_ids": active_store}, limit=0)

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
    payroll_id: str,
    current_user: dict = Depends(require_roles(*_HR_READ_ROLES)),
):
    """Approve payroll for payment"""
    payroll_repo = get_payroll_repository()

    if payroll_repo is not None:
        record = payroll_repo.find_by_id(payroll_id)
        if not record:
            raise HTTPException(status_code=404, detail="Payroll record not found")
        validate_store_access(record.get("store_id"), current_user)

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
    current_user: dict = Depends(require_roles(*_HR_READ_ROLES)),
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
    return sorted(
        {int(d) for d in (days or []) if isinstance(d, int) and 0 <= int(d) <= 6}
    )


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
        return {
            "message": "Shift assigned",
            "employee_id": req.employee_id,
            "shift_id": req.shift_id,
        }

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
    return {
        "message": "Shift assigned",
        "employee_id": req.employee_id,
        "shift_id": req.shift_id,
    }


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
        return {
            "month": f"{year:04d}-{mon:02d}",
            "employees": [],
            "total_late_marks": 0,
        }

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
        active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
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
                status_code=403,
                detail="You cannot approve your own week-off swap request",
            )
        if decision["reason"] == "NOT_PENDING":
            raise HTTPException(status_code=400, detail="Swap request is not pending")
        raise HTTPException(
            status_code=403, detail="Not permitted to approve this request"
        )

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
                status_code=403,
                detail="You cannot act on your own week-off swap request",
            )
        if decision["reason"] == "NOT_PENDING":
            raise HTTPException(status_code=400, detail="Swap request is not pending")
        raise HTTPException(
            status_code=403, detail="Not permitted to reject this request"
        )

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
