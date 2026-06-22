"""
IMS 2.0 - Feature #29: Skills-based staff rostering + coverage alerts router
============================================================================
HTTP surface for ``api/services/roster_engine.py``, mounted under /api/v1/hr.

Owner decision: all stores are clinical and the optometrist licence never
expires -- so EVERY store/day/shift needs optometrist coverage and there is NO
licence-expiry machinery (only WHO is an optometrist matters). A weekly roster
grid per store + a staff-skills registry feed a coverage check; an uncovered
shift is a BREACH surfaced in-app (the TASKMASTER sweep raises the bell). No
money, no POS. No emoji (Windows cp1252).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..dependencies import validate_store_access
from ..services import roster_engine as svc

router = APIRouter(tags=["roster"])

# Roster + skills edits are management work.
_MANAGE_ROLES = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}
# Reads: management + the staff family.
_READ_ROLES = {
    "SALES_STAFF",
    "SALES_CASHIER",
    "OPTOMETRIST",
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ADMIN",
    "SUPERADMIN",
}


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return {str(r).upper() for r in (user.get("roles", []) or [])}


def _require(user: Dict[str, Any], allowed: set, what: str):
    if not (_roles(user) & allowed):
        raise HTTPException(status_code=403, detail=f"not permitted to {what}")


def _raise(exc: "svc.RosterError"):
    raise HTTPException(status_code=int(getattr(exc, "status", 400)), detail=str(exc))


def _required_optoms(store_id: Optional[str]) -> int:
    try:
        from ..services.policy_engine import get_policy

        val = get_policy(
            svc.POLICY_REQUIRED_OPTOMS,
            {"store_id": store_id} if store_id else None,
            default=svc.DEFAULT_REQUIRED_OPTOMETRISTS,
        )
        return int(val)
    except Exception:  # noqa: BLE001
        return svc.DEFAULT_REQUIRED_OPTOMETRISTS


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class StaffSkillBody(BaseModel):
    is_optometrist: bool = False
    skills: List[str] = Field(default_factory=list)
    store_id: Optional[str] = None


class RosterEntry(BaseModel):
    employee_id: str
    date: str
    shift: str


class RosterBody(BaseModel):
    store_id: str = Field(..., min_length=1)
    week_start: str = Field(..., min_length=1)
    status: Optional[str] = None
    entries: List[RosterEntry] = Field(default_factory=list)


class RosterUpdateBody(BaseModel):
    status: Optional[str] = None
    entries: Optional[List[RosterEntry]] = None


# ---------------------------------------------------------------------------
# Staff-skills registry
# ---------------------------------------------------------------------------


@router.get("/staff-skills")
async def list_staff_skills(
    store_id: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """List the staff-skills registry (optionally per store). Read roles."""
    _require(current_user, _READ_ROLES, "view staff skills")
    if store_id:
        validate_store_access(store_id, current_user)
    return {"staff_skills": svc.list_staff_skills(_get_db(), store_id)}


@router.get("/staff-skills/{employee_id}")
async def get_staff_skill(
    employee_id: str, current_user: Dict[str, Any] = Depends(get_current_user)
):
    _require(current_user, _READ_ROLES, "view staff skills")
    rec = svc.get_staff_skill(_get_db(), employee_id)
    if rec:
        store_id = rec.get("store_id")
        if store_id:
            validate_store_access(store_id, current_user)
    return rec or {"employee_id": employee_id, "is_optometrist": False, "skills": []}


@router.put("/staff-skills/{employee_id}")
async def put_staff_skill(
    employee_id: str,
    body: StaffSkillBody,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Set an employee's optometrist flag + skill tags. Management roles."""
    _require(current_user, _MANAGE_ROLES, "edit staff skills")
    if body.store_id:
        validate_store_access(body.store_id, current_user)
    try:
        return svc.upsert_staff_skill(
            _get_db(), employee_id, body.model_dump(), actor=current_user
        )
    except svc.RosterError as exc:
        _raise(exc)


# ---------------------------------------------------------------------------
# Rosters
# ---------------------------------------------------------------------------


@router.post("/roster")
async def create_roster(
    body: RosterBody, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Create / replace a store-week roster grid. Management + store-scoped."""
    _require(current_user, _MANAGE_ROLES, "manage a roster")
    validate_store_access(body.store_id, current_user)
    try:
        payload = body.model_dump()
        payload["entries"] = [
            e.model_dump() if hasattr(e, "model_dump") else e for e in body.entries
        ]
        return svc.create_or_replace_roster(_get_db(), payload, actor=current_user)
    except svc.RosterError as exc:
        _raise(exc)


@router.get("/roster")
async def get_roster(
    store_id: str,
    week_start: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """The roster for a store-week. Read + store-scoped."""
    _require(current_user, _READ_ROLES, "view a roster")
    validate_store_access(store_id, current_user)
    r = svc.get_roster(_get_db(), store_id=store_id, week_start=week_start)
    if not r:
        raise HTTPException(status_code=404, detail="roster not found")
    return r


@router.put("/roster/{roster_id}")
async def update_roster(
    roster_id: str,
    body: RosterUpdateBody,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Edit entries / publish a roster. Management + store-scoped."""
    _require(current_user, _MANAGE_ROLES, "manage a roster")
    db = _get_db()
    existing = svc.get_roster(db, roster_id=roster_id)
    if not existing:
        raise HTTPException(status_code=404, detail="roster not found")
    validate_store_access(existing.get("store_id"), current_user)
    payload: Dict[str, Any] = {}
    if body.status is not None:
        payload["status"] = body.status
    if body.entries is not None:
        payload["entries"] = [e.model_dump() for e in body.entries]
    try:
        return svc.update_roster(db, roster_id, payload, actor=current_user)
    except svc.RosterError as exc:
        _raise(exc)


@router.get("/roster/{roster_id}/coverage")
async def roster_coverage(
    roster_id: str, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Optometrist-coverage report for a roster (BREACH rows surfaced).
    Read + store-scoped."""
    _require(current_user, _READ_ROLES, "view roster coverage")
    db = _get_db()
    roster = svc.get_roster(db, roster_id=roster_id)
    if not roster:
        raise HTTPException(status_code=404, detail="roster not found")
    validate_store_access(roster.get("store_id"), current_user)
    coverage = svc.coverage_for_roster(
        db, roster, _required_optoms(roster.get("store_id"))
    )
    return {
        "roster_id": roster_id,
        "store_id": roster.get("store_id"),
        "week_start": roster.get("week_start"),
        "required_optometrists": _required_optoms(roster.get("store_id")),
        "coverage": coverage,
        "breaches": svc.coverage_breaches(coverage),
    }
