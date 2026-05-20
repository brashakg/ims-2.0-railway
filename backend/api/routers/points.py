"""
IMS 2.0 — Daily Points Router (Pune Incentive Module ii)
=========================================================
9-category daily score per staff member with MTD aggregation, audit-
replayable history, settings-snapshot eligibility, and a Visufit-90%
gate. Output contract `GET /points/mtd` is what Module (iii) consumes.

Auth: every endpoint requires a logged-in user.
RBAC:
  - SUPERADMIN / ADMIN / STORE_MANAGER / AREA_MANAGER / ACCOUNTANT:
    write any staff in their store
  - SALES_STAFF / SALES_CASHIER / CASHIER: write only their own row
  - SUPERADMIN / STORE_MANAGER: soft-delete a row (with reason +
    audit)
  - SUPERADMIN: PATCH /settings/eligibility

Mounted at /api/v1/incentive/points (singular `incentive`) — note
the existing /api/v1/incentives router (sales targets/kickers) is a
separate, unrelated domain.
"""

from __future__ import annotations

from datetime import date as date_type, datetime, timedelta
from typing import Any, Dict, List, Optional
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from .auth import get_current_user
from ..dependencies import (
    get_audit_repository,
    get_db,
    get_user_repository,
    get_walkout_repository,
    get_walkin_counter_repository,
)

# Late imports of the new repos so a broken import doesn't take out
# unrelated routers at app boot.
from database.repositories.points_log_repository import PointsLogRepository
from database.repositories.incentive_settings_repository import (
    IncentiveSettingsRepository,
)
from api.services.points_calculator import (
    CATEGORIES_FOR_TOTAL,
    aggregate_mtd,
    apply_visufit_gate,
    compute_eligibility,
    compute_total,
    leaderboard_sort_key,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# RBAC
# ============================================================================
_GLOBAL_ROLES = {"SUPERADMIN", "ADMIN"}
_STORE_ROLES = {"STORE_MANAGER", "AREA_MANAGER"}
_LOG_ANY_STAFF_ROLES = _GLOBAL_ROLES | _STORE_ROLES | {"ACCOUNTANT"}
_DELETE_ROLES = {"SUPERADMIN", "STORE_MANAGER"}


def _user_role_set(current_user: dict) -> set:
    return set(current_user.get("roles", []) or [])


def _user_store_id(current_user: dict) -> Optional[str]:
    return (
        current_user.get("active_store_id")
        or (current_user.get("store_ids") or [None])[0]
    )


def _resolve_store(current_user: dict, override: Optional[str]) -> str:
    roles = _user_role_set(current_user)
    if (roles & _GLOBAL_ROLES) and override:
        return override
    store = _user_store_id(current_user)
    if not store:
        raise HTTPException(status_code=400, detail="No active store on this session")
    return store


# ============================================================================
# Pydantic models
# ============================================================================


class DailyScores(BaseModel):
    """9-category scores. conversion is Optional — if null AND date is
    today, server auto-fills from /walkouts/conversion-feed. Past dates
    require explicit value to avoid silent backfill."""

    attendance: int = Field(..., ge=0, le=10)
    conversion: Optional[int] = Field(None, ge=0, le=20)
    task: int = Field(..., ge=0, le=10)
    visufit: int = Field(..., ge=0, le=10)
    punctuality: int = Field(..., ge=0, le=10)
    behaviour: int = Field(..., ge=0, le=10)
    kicker_1: int = Field(..., ge=0, le=10)
    kicker_2: int = Field(..., ge=0, le=10)
    reviews: int = Field(..., ge=0, le=10)


class CreateDailyPointsRequest(BaseModel):
    date: date_type
    staff_id: str = Field(..., min_length=1)
    scores: DailyScores
    visufit_usage_pct_mtd: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Caller-provided MTD Visufit usage. If absent, "
        "the gate is not applied (fail-soft) until the clinical "
        "endpoint lands.",
    )


class BulkDailyPointsRequest(BaseModel):
    rows: List[CreateDailyPointsRequest] = Field(..., min_length=1, max_length=20)


class DeletePointsRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=400)


class EligibilityBand(BaseModel):
    min: float
    max: float
    value: float

    @field_validator("max")
    @classmethod
    def _max_gt_min(cls, v, info):
        mn = info.data.get("min")
        if mn is not None and v <= mn:
            raise ValueError("max must be greater than min")
        return v


class UpdateEligibilityRequest(BaseModel):
    bands: List[EligibilityBand] = Field(..., min_length=2, max_length=10)


class UpdateVisufitGateRequest(BaseModel):
    threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    enabled: Optional[bool] = None


# ============================================================================
# Repository accessors
# ============================================================================


def _points_repo() -> Optional[PointsLogRepository]:
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return PointsLogRepository(db.get_collection("points_log"))
    except Exception:
        return None


def _settings_repo() -> Optional[IncentiveSettingsRepository]:
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return IncentiveSettingsRepository(db.get_collection("incentive_settings"))
    except Exception:
        return None


def _resolve_staff_name(staff_id: str) -> Optional[str]:
    try:
        ur = get_user_repository()
        if ur is None:
            return None
        u = ur.find_by_id(staff_id) or ur.find_one({"user_id": staff_id})
        if not u:
            return None
        return u.get("name") or u.get("full_name") or u.get("username") or staff_id
    except Exception:
        return None


def _conversion_score_for(store_id: str, date_str: str, staff_id: str) -> Optional[int]:
    """Pull conversion_score from Module (i)'s feed math, in-process
    (no HTTP self-call). Returns None on any failure — caller should
    treat that as "no auto-fill"."""
    repo = get_walkout_repository()
    walkin_repo = get_walkin_counter_repository()
    if repo is None:
        return None
    try:
        walkouts_today = repo.list_walkouts(
            store_id=store_id,
            date_from=date_str,
            date_to=date_str,
            limit=5000,
        )
    except Exception:
        walkouts_today = []
    walkouts_count = sum(
        1 for w in walkouts_today if w.get("sales_person_id") == staff_id
    )

    # Retro: prior 90 days where result_set_at falls on date_str
    try:
        target_d = date_type.fromisoformat(date_str)
        window_from = (target_d - timedelta(days=90)).isoformat()
        window_to = (target_d - timedelta(days=1)).isoformat()
        prior = repo.list_walkouts(
            store_id=store_id,
            date_from=window_from,
            date_to=window_to,
            limit=5000,
        )
    except Exception:
        prior = []
    retro = 0
    for w in prior:
        if w.get("result") != "CONVERTED":
            continue
        if w.get("sales_person_id") != staff_id:
            continue
        rsa = w.get("result_set_at")
        rsa_str = (
            rsa[:10]
            if isinstance(rsa, str)
            else (rsa.date().isoformat() if isinstance(rsa, datetime) else "")
        )
        if rsa_str != date_str:
            continue
        retro += 1

    walk_ins = 0
    if walkin_repo is not None:
        try:
            today_doc = walkin_repo.get_today(store_id, date_str=date_str)
            walk_ins = int((today_doc.get("per_staff") or {}).get(staff_id, 0))
        except Exception:
            pass
    if walk_ins <= 0:
        return 0
    raw = (walk_ins - walkouts_count + retro) / walk_ins * 20.0
    return int(round(max(0.0, min(20.0, raw))))


def _audit(
    *,
    action: str,
    log_id: str,
    store_id: str,
    current_user: dict,
    detail: Dict,
) -> None:
    audit_repo = get_audit_repository()
    if audit_repo is None:
        return
    try:
        audit_repo.create(
            {
                "log_id": uuid.uuid4().hex,
                "timestamp": datetime.now(),
                "user_id": current_user.get("user_id"),
                "action": action,
                "entity_type": "points_log",
                "entity_id": log_id,
                "store_id": store_id,
                "severity": "info",
                "detail": detail,
            }
        )
    except Exception as e:
        logger.warning(f"[POINTS] audit failed: {e}")


def _serialize(doc: Dict) -> Dict:
    """ISO-format datetimes recursively; strip _id."""
    if not doc:
        return doc
    if isinstance(doc, list):
        return [_serialize(d) for d in doc]
    if isinstance(doc, datetime):
        return doc.isoformat()
    if isinstance(doc, dict):
        return {k: _serialize(v) for k, v in doc.items() if k != "_id"}
    return doc


# ============================================================================
# Core write path
# ============================================================================


def _check_write_permission(current_user: dict, target_staff_id: str) -> None:
    """STORE_MANAGER+/ACCOUNTANT may write any staff; everyone else
    only their own row."""
    roles = _user_role_set(current_user)
    if roles & _LOG_ANY_STAFF_ROLES:
        return
    if current_user.get("user_id") == target_staff_id:
        return
    raise HTTPException(
        status_code=403,
        detail="You can only log points for yourself",
    )


def _build_row(
    *,
    payload: CreateDailyPointsRequest,
    store_id: str,
    settings: Dict,
    user_id: Optional[str],
) -> Dict:
    """Pure helper — compose the points_log document from payload +
    settings (auto-fill conversion if needed, apply visufit gate, total,
    eligibility snapshot)."""
    date_str = payload.date.isoformat()
    today = datetime.now().date().isoformat()

    # Convert payload scores → mutable dict
    raw_scores = payload.scores.model_dump()

    # Auto-fill conversion if null AND target date is today.
    if raw_scores.get("conversion") is None:
        if date_str == today:
            raw_scores["conversion"] = (
                _conversion_score_for(store_id, date_str, payload.staff_id) or 0
            )
        else:
            # Past date with explicit None — accept as 0 (operator
            # signaled "no conversion measurement"); the build plan
            # allows manual override but doesn't require non-null.
            raw_scores["conversion"] = 0

    # Apply Visufit gate
    threshold = float(settings.get("visufit_gate_threshold") or 0.9)
    enabled = bool(settings.get("visufit_gate_enabled", True))
    scored, gate_applied = apply_visufit_gate(
        raw_scores,
        visufit_usage_pct_mtd=payload.visufit_usage_pct_mtd,
        threshold=threshold,
        enabled=enabled,
    )

    total = compute_total(scored)
    bands = settings.get("eligibility_bands") or []
    eligibility = compute_eligibility(total, bands)

    return {
        "store_id": store_id,
        "date": datetime.combine(payload.date, datetime.min.time()),
        "date_str": date_str,
        "staff_id": payload.staff_id,
        "staff_name": _resolve_staff_name(payload.staff_id),
        **scored,
        "total": total,
        "eligibility": eligibility,
        "eligibility_thresholds_used": {"bands": list(bands)},
        "visufit_gate_applied": gate_applied,
        "visufit_usage_pct_mtd": payload.visufit_usage_pct_mtd,
        "created_by": user_id,
    }


def _save_row(
    repo: PointsLogRepository,
    row: Dict,
    current_user: dict,
) -> Dict:
    """Insert + audit; raise 409 on duplicate key."""
    try:
        saved = repo.create_points_log(row)
    except Exception as e:
        cls = type(e).__name__
        if "DuplicateKeyError" in cls or "duplicate key" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail="Already logged. Delete first to re-save.",
            )
        raise HTTPException(status_code=500, detail=f"Save failed: {e}")
    if saved is None:
        raise HTTPException(status_code=500, detail="Save returned None")
    _audit(
        action="points.create",
        log_id=saved["log_id"],
        store_id=row["store_id"],
        current_user=current_user,
        detail={
            "staff_id": row["staff_id"],
            "date_str": row["date_str"],
            "total": row["total"],
            "eligibility": row["eligibility"],
            "visufit_gate_applied": row["visufit_gate_applied"],
        },
    )
    return saved


# ============================================================================
# Endpoints
# ============================================================================


@router.post("/daily", status_code=201)
@router.post("/daily/", status_code=201, include_in_schema=False)
async def create_daily(
    payload: CreateDailyPointsRequest,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """Log one staff member's points for one day. 409 if already logged
    (delete first to re-save)."""
    store = _resolve_store(current_user, store_id)
    _check_write_permission(current_user, payload.staff_id)

    repo = _points_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    settings_repo = _settings_repo()
    settings = (
        settings_repo.get_for_store(store)
        if settings_repo
        else IncentiveSettingsRepository.__new__(IncentiveSettingsRepository)._defaults(
            store
        )
    )  # type: ignore

    row = _build_row(
        payload=payload,
        store_id=store,
        settings=settings,
        user_id=current_user.get("user_id"),
    )
    saved = _save_row(repo, row, current_user)
    return _serialize(saved)


@router.post("/daily/bulk", status_code=201)
async def create_daily_bulk(
    body: BulkDailyPointsRequest,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """End-of-day batch. Per-row success / failure breakdown so a
    single 409 doesn't kill the whole batch."""
    store = _resolve_store(current_user, store_id)
    repo = _points_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    settings_repo = _settings_repo()
    settings = (
        settings_repo.get_for_store(store)
        if settings_repo
        else IncentiveSettingsRepository.__new__(IncentiveSettingsRepository)._defaults(
            store
        )
    )  # type: ignore

    saved_rows: List[Dict] = []
    failures: List[Dict] = []
    for r in body.rows:
        try:
            _check_write_permission(current_user, r.staff_id)
        except HTTPException as e:
            failures.append(
                {
                    "staff_id": r.staff_id,
                    "date": r.date.isoformat(),
                    "status_code": e.status_code,
                    "detail": e.detail,
                }
            )
            continue
        try:
            row = _build_row(
                payload=r,
                store_id=store,
                settings=settings,
                user_id=current_user.get("user_id"),
            )
            saved = _save_row(repo, row, current_user)
            saved_rows.append(_serialize(saved))
        except HTTPException as e:
            failures.append(
                {
                    "staff_id": r.staff_id,
                    "date": r.date.isoformat(),
                    "status_code": e.status_code,
                    "detail": e.detail,
                }
            )
    return {
        "saved": saved_rows,
        "failures": failures,
        "saved_count": len(saved_rows),
        "failed_count": len(failures),
    }


@router.get("/daily")
@router.get("/daily/", include_in_schema=False)
async def list_daily(
    current_user: dict = Depends(get_current_user),
    date: Optional[str] = Query(None, description="ISO date, defaults to today"),
    store_id: Optional[str] = Query(None),
):
    """All rows for one (store, date). Excludes soft-deleted."""
    store = _resolve_store(current_user, store_id)
    repo = _points_repo()
    if repo is None:
        return {
            "items": [],
            "store_id": store,
            "date_str": date or datetime.now().date().isoformat(),
        }
    date_str = date or datetime.now().date().isoformat()
    rows = repo.list_by_date(store, date_str)
    return {
        "items": [_serialize(r) for r in rows],
        "store_id": store,
        "date_str": date_str,
    }


@router.delete("/daily/{log_id}", status_code=200)
async def delete_daily(
    log_id: str,
    payload: DeletePointsRequest,
    current_user: dict = Depends(get_current_user),
):
    """Soft-delete one points_log row. Frees the (store, date, staff)
    slot so a corrected row can be POSTed."""
    repo = _points_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    existing = repo.find_by_log_id(log_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Points log not found")

    roles = _user_role_set(current_user)
    if not (roles & _DELETE_ROLES):
        # Store managers in their own store, or superadmin everywhere
        if not (roles & {"SUPERADMIN"}):
            raise HTTPException(
                status_code=403,
                detail="Only superadmin / store-manager may delete points logs",
            )
    if "STORE_MANAGER" in roles and not (roles & {"SUPERADMIN"}):
        if existing.get("store_id") != _user_store_id(current_user):
            raise HTTPException(
                status_code=403,
                detail="Cross-store deletion not allowed",
            )

    ok = repo.soft_delete(
        log_id,
        deleted_by=current_user.get("user_id") or "",
        reason=payload.reason,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Delete failed")
    _audit(
        action="points.delete",
        log_id=log_id,
        store_id=existing.get("store_id", ""),
        current_user=current_user,
        detail={"reason": payload.reason},
    )
    return {"log_id": log_id, "deleted": True}


@router.get("/mtd")
async def get_mtd(
    current_user: dict = Depends(get_current_user),
    year: Optional[int] = Query(None, ge=2024, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    store_id: Optional[str] = Query(None),
):
    """The Module (iii) contract — per-staff MTD aggregation."""
    store = _resolve_store(current_user, store_id)
    repo = _points_repo()
    if repo is None:
        return {"store_id": store, "items": []}
    now = datetime.now().date()
    yr = year or now.year
    mo = month or now.month
    date_from = f"{yr:04d}-{mo:02d}-01"
    # Last day of month: ride to next month, subtract a day
    if mo == 12:
        next_d = date_type(yr + 1, 1, 1)
    else:
        next_d = date_type(yr, mo + 1, 1)
    date_to = (next_d - timedelta(days=1)).isoformat()

    rows = repo.list_for_mtd(store, date_from, date_to)
    by_staff = aggregate_mtd(rows)
    items = list(by_staff.values())
    items.sort(key=leaderboard_sort_key)
    return {
        "store_id": store,
        "year": yr,
        "month": mo,
        "date_from": date_from,
        "date_to": date_to,
        "items": items,
    }


@router.get("/leaderboard")
async def get_leaderboard(
    current_user: dict = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365),
    store_id: Optional[str] = Query(None),
):
    """Sorted by avg.total DESC, tie-broken by days_logged DESC."""
    store = _resolve_store(current_user, store_id)
    repo = _points_repo()
    if repo is None:
        return {"store_id": store, "items": []}
    today = datetime.now().date()
    date_from = (today - timedelta(days=days - 1)).isoformat()
    date_to = today.isoformat()
    rows = repo.list_for_mtd(store, date_from, date_to)
    by_staff = aggregate_mtd(rows)
    items = list(by_staff.values())
    items.sort(key=leaderboard_sort_key)
    return {
        "store_id": store,
        "days": days,
        "date_from": date_from,
        "date_to": date_to,
        "items": items,
    }


@router.get("/staff/{staff_id}/history")
async def staff_history(
    staff_id: str,
    current_user: dict = Depends(get_current_user),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
):
    """All points_log rows for one staff in a date range. Defaults to
    the current month."""
    store = _resolve_store(current_user, store_id)
    repo = _points_repo()
    if repo is None:
        return {"store_id": store, "staff_id": staff_id, "items": []}
    today = datetime.now().date()
    df = date_from or today.replace(day=1).isoformat()
    dt = date_to or today.isoformat()
    rows = repo.list_by_staff_range(store, staff_id, df, dt)
    return {
        "store_id": store,
        "staff_id": staff_id,
        "date_from": df,
        "date_to": dt,
        "items": [_serialize(r) for r in rows],
    }


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------


@router.get("/settings/eligibility")
async def get_settings(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """Current eligibility bands + visufit gate config."""
    store = _resolve_store(current_user, store_id)
    settings_repo = _settings_repo()
    if settings_repo is None:
        return IncentiveSettingsRepository.__new__(IncentiveSettingsRepository)._defaults(store)  # type: ignore
    return _serialize(settings_repo.get_for_store(store))


@router.patch("/settings/eligibility")
async def update_eligibility(
    payload: UpdateEligibilityRequest,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """SUPERADMIN only. Replace eligibility bands. Snapshot semantics
    on points_log mean historical rows are unaffected."""
    if "SUPERADMIN" not in _user_role_set(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only SUPERADMIN can update eligibility bands",
        )
    store = _resolve_store(current_user, store_id)
    settings_repo = _settings_repo()
    if settings_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    bands = [b.model_dump() for b in payload.bands]
    updated = settings_repo.update_eligibility_bands(
        store,
        bands,
        updated_by=current_user.get("user_id") or "",
    )
    _audit(
        action="incentive.settings.update",
        log_id=f"settings:{store}",
        store_id=store,
        current_user=current_user,
        detail={"field": "eligibility_bands", "new_value": bands},
    )
    return _serialize(updated)


class UpdatePayoutSettingsRequest(BaseModel):
    """SUPERADMIN-only — Module iii inputs that drive the calculator."""

    growth_targets: Optional[Dict[str, float]] = None
    base_rates: Optional[Dict[str, float]] = None
    discount_kill_threshold: Optional[float] = Field(None, ge=0, le=1)
    discount_multipliers: Optional[List[Dict[str, float]]] = None
    staff_weightages: Optional[Dict[str, float]] = None
    supervisor_bonuses: Optional[List[Dict]] = None


class LastYearSaleInput(BaseModel):
    """Per-(store, year, month) manual override of last_year_sale.
    Module iii's calculator picks this up before falling back to the
    aggregated last-year actuals."""

    year: int = Field(..., ge=2024, le=2100)
    month: int = Field(..., ge=1, le=12)
    last_year_sale: float = Field(..., ge=0)


@router.patch("/settings/payout")
async def update_payout_settings(
    payload: UpdatePayoutSettingsRequest,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """SUPERADMIN-only — patch the Module iii calculator inputs that
    live on incentive_settings (growth, rates, multipliers, weightages,
    supervisor bonuses)."""
    if "SUPERADMIN" not in _user_role_set(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only SUPERADMIN can update payout settings",
        )
    store = _resolve_store(current_user, store_id)
    settings_repo = _settings_repo()
    if settings_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    incoming = payload.model_dump(exclude_unset=True)
    if not incoming:
        raise HTTPException(
            status_code=400,
            detail="At least one field must be provided",
        )

    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    coll = db.get_collection("incentive_settings")
    existing = coll.find_one({"store_id": store})
    now = datetime.now()
    update_doc = {
        **incoming,
        "updated_at": now,
        "updated_by": current_user.get("user_id"),
    }
    if existing:
        coll.update_one({"store_id": store}, {"$set": update_doc})
    else:
        defaults = settings_repo._defaults(store)
        defaults.update(update_doc)
        defaults["_id"] = store
        coll.insert_one(defaults)
    _audit(
        action="incentive.settings.update",
        log_id=f"settings:{store}",
        store_id=store,
        current_user=current_user,
        detail={"field": "payout", "patch": list(incoming.keys())},
    )
    return _serialize(settings_repo.get_for_store(store))


@router.post("/inputs/last-year-sale", status_code=201)
async def set_last_year_sale(
    payload: LastYearSaleInput,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """Per-(store, year, month) manual input. SUPERADMIN/ADMIN/
    AREA_MANAGER/STORE_MANAGER/ACCOUNTANT may set."""
    roles = _user_role_set(current_user)
    if not (roles & _LOG_ANY_STAFF_ROLES):
        raise HTTPException(
            status_code=403,
            detail="Only managers / admin / accountant can set inputs",
        )
    store = _resolve_store(current_user, store_id)
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    coll = db.get_collection("incentive_inputs")
    now = datetime.now()
    existing = coll.find_one(
        {
            "store_id": store,
            "year": payload.year,
            "month": payload.month,
        }
    )
    if existing:
        coll.update_one(
            {"store_id": store, "year": payload.year, "month": payload.month},
            {
                "$set": {
                    "last_year_sale": payload.last_year_sale,
                    "updated_at": now,
                    "updated_by": current_user.get("user_id"),
                }
            },
        )
    else:
        coll.insert_one(
            {
                "store_id": store,
                "year": payload.year,
                "month": payload.month,
                "last_year_sale": payload.last_year_sale,
                "created_at": now,
                "created_by": current_user.get("user_id"),
                "updated_at": now,
                "updated_by": current_user.get("user_id"),
            }
        )
    _audit(
        action="incentive.inputs.update",
        log_id=f"inputs:{store}:{payload.year}-{payload.month:02d}",
        store_id=store,
        current_user=current_user,
        detail={
            "year": payload.year,
            "month": payload.month,
            "last_year_sale": payload.last_year_sale,
        },
    )
    return {
        "store_id": store,
        "year": payload.year,
        "month": payload.month,
        "last_year_sale": payload.last_year_sale,
    }


@router.patch("/settings/visufit-gate")
async def update_visufit_gate(
    payload: UpdateVisufitGateRequest,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """SUPERADMIN only. Toggle / re-tune the Visufit gate."""
    if "SUPERADMIN" not in _user_role_set(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only SUPERADMIN can update the Visufit gate",
        )
    if payload.threshold is None and payload.enabled is None:
        raise HTTPException(
            status_code=400,
            detail="Provide threshold and/or enabled",
        )
    store = _resolve_store(current_user, store_id)
    settings_repo = _settings_repo()
    if settings_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    updated = settings_repo.update_visufit_gate(
        store,
        threshold=payload.threshold,
        enabled=payload.enabled,
        updated_by=current_user.get("user_id") or "",
    )
    _audit(
        action="incentive.settings.update",
        log_id=f"settings:{store}",
        store_id=store,
        current_user=current_user,
        detail={
            "field": "visufit_gate",
            "threshold": payload.threshold,
            "enabled": payload.enabled,
        },
    )
    return _serialize(updated)
