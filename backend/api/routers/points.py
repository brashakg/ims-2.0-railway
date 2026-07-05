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
from api.services import scorecard_engine
from api.services.leaderboard_display import (
    build_leaderboard_row,
    leaderboard_config_defaults,
    titles_catalog,
)
from api.utils.ist import ist_today

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


# F33 -- leaderboard scope widening. org/area visibility is a management
# privilege; floor roles stay store-scoped.
_SCOPE_WIDE_ROLES = {"AREA_MANAGER", "ADMIN", "SUPERADMIN"}
_VALID_SCOPES = ("store", "area", "org")


def _resolve_scope_stores(
    current_user: dict, scope: str, store_id: Optional[str]
) -> Optional[List[str]]:
    """Resolve a leaderboard scope into a points_log store filter.

    Returns:
      - ["<store>"]   for scope=store (existing single-store resolution)
      - [ids...]      for scope=area  (the caller's assigned stores)
      - None          for scope=org   (no store filter)

    org/area are allowed ONLY for AREA_MANAGER / ADMIN / SUPERADMIN.
    """
    if scope not in _VALID_SCOPES:
        raise HTTPException(
            status_code=422,
            detail=f"scope must be one of {', '.join(_VALID_SCOPES)}",
        )
    if scope == "store":
        return [_resolve_store(current_user, store_id)]
    roles = _user_role_set(current_user)
    if not (roles & _SCOPE_WIDE_ROLES):
        raise HTTPException(
            status_code=403,
            detail="Only area managers / admin can view area or org leaderboards",
        )
    if scope == "org":
        return None
    stores = [s for s in (current_user.get("store_ids") or []) if s]
    if not stores:
        raise HTTPException(
            status_code=400, detail="No stores assigned to this session"
        )
    return stores


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
    visufit_source: Optional[str] = Field(
        None,
        description="Provenance of the visufit usage input: 'clinical' "
        "(auto from the Visufit clinical feed) or 'manual' (operator "
        "override). Stamped on the row for transparency.",
    )

    @field_validator("visufit_source")
    @classmethod
    def _visufit_source_enum(cls, v):
        if v is None:
            return v
        v = str(v).strip().lower()
        if v not in ("clinical", "manual"):
            raise ValueError("visufit_source must be 'clinical' or 'manual'")
        return v


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
    """Thin HTTP-layer shim: resolve the walkout/walkin repos and hand the
    Module (i) conversion math to scorecard_engine (the importable, tested
    surface). Returns None when the walkout repo is unavailable -- caller
    treats that as "no auto-fill"."""
    return scorecard_engine.conversion_score(
        store_id,
        date_str,
        staff_id,
        walkout_repo=get_walkout_repository(),
        walkin_repo=get_walkin_counter_repository(),
    )


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
    """HTTP-layer shim: delegate the score composition (conversion auto-fill,
    visufit gate, total, eligibility snapshot, visufit_source) to
    scorecard_engine.score_daily, then stamp the persistence fields
    (`date` datetime, staff_name, created_by) the engine deliberately leaves
    to the router."""
    date_str = payload.date.isoformat()

    try:
        row = scorecard_engine.score_daily(
            raw_scores=payload.scores.model_dump(),
            date_str=date_str,
            staff_id=payload.staff_id,
            store_id=store_id,
            settings=settings,
            visufit_usage_pct_mtd=payload.visufit_usage_pct_mtd,
            visufit_source=payload.visufit_source,
            conversion_provider=lambda: _conversion_score_for(
                store_id, date_str, payload.staff_id
            ),
            # N3 / CORRECTIONS.md HARDENING line 92 (binding): block a silent-0
            # conversion auto-fill when today's footfall is missing. The manager
            # can still save by supplying an explicit numeric conversion value.
            block_on_missing_footfall=True,
        )
    except scorecard_engine.FootfallMissingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    row["date"] = datetime.combine(payload.date, datetime.min.time())
    row["staff_name"] = _resolve_staff_name(payload.staff_id)
    row["created_by"] = user_id
    return row


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
        logger.exception("Points log save failed")
        raise HTTPException(
            status_code=500,
            detail="Could not save the points entry - try again or contact support",
        )
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
            "date_str": date or ist_today().isoformat(),
        }
    date_str = date or ist_today().isoformat()
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


def _ranked_items(rows: List[Dict]) -> List[Dict]:
    """aggregate_mtd + canonical leaderboard sort."""
    items = list(aggregate_mtd(rows).values())
    items.sort(key=leaderboard_sort_key)
    return items


def _prev_rank_map(
    repo: PointsLogRepository,
    stores: Optional[List[str]],
    date_from: str,
    date_to: str,
) -> Dict[str, int]:
    """staff_id -> rank in the PREVIOUS period (for rank_delta / top_riser).
    Fail-soft: any error returns {} (deltas just render as new entries)."""
    try:
        rows = repo.list_for_mtd_scoped(stores, date_from, date_to)
        return {
            r["staff_id"]: i + 1 for i, r in enumerate(_ranked_items(rows))
        }
    except Exception:
        return {}


def _decorate_items(
    items: List[Dict],
    current_user: dict,
    prev_ranks: Dict[str, int],
    period_days: Optional[int],
) -> List[Dict]:
    """F33: pipe every raw aggregate row through the display layer
    (tier/title/badges/rank_delta + the junior-role rupee strip)."""
    total = len(items)
    viewer_roles = _user_role_set(current_user)
    return [
        build_leaderboard_row(
            row,
            rank=idx + 1,
            total=total,
            viewer_roles=viewer_roles,
            prev_rank=prev_ranks.get(row.get("staff_id") or ""),
            period_days=period_days,
        )
        for idx, row in enumerate(items)
    ]


@router.get("/mtd")
async def get_mtd(
    current_user: dict = Depends(get_current_user),
    year: Optional[int] = Query(None, ge=2024, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    store_id: Optional[str] = Query(None),
    scope: str = Query("store"),
):
    """The Module (iii) contract — per-staff MTD aggregation.

    F33: rows are decorated with tier/title/badges/rank_delta and the
    junior-role rupee strip. `scope=area|org` (managers only) widens the
    board beyond the caller's store."""
    stores = _resolve_scope_stores(current_user, scope, store_id)
    store = stores[0] if scope == "store" and stores else None
    repo = _points_repo()
    if repo is None:
        return {"store_id": store, "scope": scope, "items": []}
    now = ist_today()
    yr = year or now.year
    mo = month or now.month
    date_from = f"{yr:04d}-{mo:02d}-01"
    # Last day of month: ride to next month, subtract a day
    if mo == 12:
        next_d = date_type(yr + 1, 1, 1)
    else:
        next_d = date_type(yr, mo + 1, 1)
    date_to = (next_d - timedelta(days=1)).isoformat()

    rows = repo.list_for_mtd_scoped(stores, date_from, date_to)
    items = _ranked_items(rows)

    # Previous month window (for rank_delta / top_riser)
    prev_last = date_type(yr, mo, 1) - timedelta(days=1)
    prev_from = prev_last.replace(day=1).isoformat()
    prev_ranks = _prev_rank_map(repo, stores, prev_from, prev_last.isoformat())

    # Days elapsed in the requested month (for logged_every_day)
    window_end = min(date_type.fromisoformat(date_to), now)
    elapsed = (window_end - date_type.fromisoformat(date_from)).days + 1
    period_days = elapsed if elapsed > 0 else None

    items = _decorate_items(items, current_user, prev_ranks, period_days)
    return {
        "store_id": store,
        "scope": scope,
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
    scope: str = Query("store"),
):
    """Sorted by avg.total DESC, tie-broken by days_logged DESC.

    F33: rows are decorated with tier/title/badges/rank_delta and the
    junior-role rupee strip. `scope=area|org` (managers only) widens the
    board beyond the caller's store."""
    stores = _resolve_scope_stores(current_user, scope, store_id)
    store = stores[0] if scope == "store" and stores else None
    repo = _points_repo()
    if repo is None:
        return {"store_id": store, "scope": scope, "items": []}
    today = ist_today()
    date_from = (today - timedelta(days=days - 1)).isoformat()
    date_to = today.isoformat()
    rows = repo.list_for_mtd_scoped(stores, date_from, date_to)
    items = _ranked_items(rows)

    # Previous same-length window (for rank_delta / top_riser)
    prev_to = (today - timedelta(days=days)).isoformat()
    prev_from = (today - timedelta(days=2 * days - 1)).isoformat()
    prev_ranks = _prev_rank_map(repo, stores, prev_from, prev_to)

    items = _decorate_items(items, current_user, prev_ranks, days)
    return {
        "store_id": store,
        "scope": scope,
        "days": days,
        "date_from": date_from,
        "date_to": date_to,
        "items": items,
    }


@router.get("/leaderboard/titles")
async def get_leaderboard_titles(
    current_user: dict = Depends(get_current_user),
):
    """F33: the titles + badges catalog (FE legend). Any authenticated
    user — contains no staff or rupee data."""
    items = titles_catalog()
    return {
        "titles": [i for i in items if i["kind"] == "title"],
        "badges": [i for i in items if i["kind"] == "badge"],
        "tiers": ["PODIUM", "CONTENDER", "BUILDING"],
    }


class LeaderboardSettingsRequest(BaseModel):
    """F33 — per-store leaderboard display config (sub-doc on the
    existing incentive_settings doc; no new collection)."""

    enabled: Optional[bool] = None
    scope_default: Optional[str] = None
    show_titles: Optional[bool] = None
    show_badges: Optional[bool] = None

    @field_validator("scope_default")
    @classmethod
    def _scope_default_enum(cls, v):
        if v is None:
            return v
        v = str(v).strip().lower()
        if v not in _VALID_SCOPES:
            raise ValueError(
                f"scope_default must be one of {', '.join(_VALID_SCOPES)}"
            )
        return v


@router.post("/leaderboard/settings")
async def update_leaderboard_settings(
    payload: LeaderboardSettingsRequest,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """F33: SUPERADMIN/ADMIN — upsert the leaderboard_config sub-doc on
    the store's incentive_settings doc (defaults merged underneath)."""
    if not (_user_role_set(current_user) & {"SUPERADMIN", "ADMIN"}):
        raise HTTPException(
            status_code=403,
            detail="Only admin / superadmin can update leaderboard settings",
        )
    store = _resolve_store(current_user, store_id)
    settings_repo = _settings_repo()
    if settings_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    incoming = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not incoming:
        raise HTTPException(
            status_code=400, detail="At least one field must be provided"
        )

    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    coll = db.get_collection("incentive_settings")
    existing = coll.find_one({"store_id": store}) or {}
    config = {
        **leaderboard_config_defaults(),
        **(existing.get("leaderboard_config") or {}),
        **incoming,
    }
    now = datetime.now()
    update_doc = {
        "leaderboard_config": config,
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
        detail={"field": "leaderboard_config", "patch": list(incoming.keys())},
    )
    return {"store_id": store, "leaderboard_config": config}


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
    today = ist_today()
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


@router.get("/settings/effective")
async def get_effective_settings(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
):
    """E2-resolved settings for a store: global -> entity -> store merged.

    Reports which scope each calculator-input key resolved from
    (`_resolution_sources`) so the UI can show "inherited from entity" vs
    "store override". VIEW_ROLES only."""
    if not (_user_role_set(current_user) & _LOG_ANY_STAFF_ROLES):
        raise HTTPException(
            status_code=403,
            detail="Only managers / admin / accountant can view effective settings",
        )
    store = _resolve_store(current_user, store_id)
    settings_repo = _settings_repo()
    if settings_repo is None:
        return scorecard_engine.resolve_settings(
            store, entity_id, settings_repo=None
        )
    resolved = scorecard_engine.resolve_settings(
        store, entity_id, settings_repo=settings_repo
    )
    return _serialize(resolved)


class UpdateScopeSettingsRequest(BaseModel):
    """SUPERADMIN-only -- set calculator-input defaults at entity / global
    scope (E2 hierarchy). At least one field must be supplied."""

    scope: str = Field(..., description="'global' or 'entity'")
    entity_id: Optional[str] = None
    eligibility_bands: Optional[List[EligibilityBand]] = None
    growth_targets: Optional[Dict[str, float]] = None
    base_rates: Optional[Dict[str, float]] = None
    discount_kill_threshold: Optional[float] = Field(None, ge=0, le=1)
    discount_multipliers: Optional[List[Dict[str, float]]] = None
    staff_weightages: Optional[Dict[str, float]] = None
    supervisor_bonuses: Optional[List[Dict]] = None
    visufit_gate_threshold: Optional[float] = Field(None, ge=0, le=1)
    visufit_gate_enabled: Optional[bool] = None

    @field_validator("scope")
    @classmethod
    def _scope_enum(cls, v):
        v = str(v).strip().lower()
        if v not in ("global", "entity"):
            raise ValueError("scope must be 'global' or 'entity'")
        return v


@router.patch("/settings/scope")
async def update_scope_settings(
    payload: UpdateScopeSettingsRequest,
    current_user: dict = Depends(get_current_user),
):
    """SUPERADMIN-only. Set chain-wide calculator defaults at entity or
    global scope (E2 hierarchy) without per-store duplication. Store rows
    still override via PATCH /settings/payout."""
    if "SUPERADMIN" not in _user_role_set(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only SUPERADMIN can set entity/global settings",
        )
    settings_repo = _settings_repo()
    if settings_repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    if payload.scope == "entity" and not payload.entity_id:
        raise HTTPException(
            status_code=400, detail="entity_id required for entity scope"
        )

    patch = payload.model_dump(
        exclude_unset=True, exclude={"scope", "entity_id"}
    )
    if payload.eligibility_bands is not None:
        patch["eligibility_bands"] = [b.model_dump() for b in payload.eligibility_bands]
    if not patch:
        raise HTTPException(
            status_code=400, detail="At least one settings field must be provided"
        )

    if payload.scope == "global":
        scope_key = scorecard_engine.GLOBAL_SCOPE_ID
        entity_id = None
    else:
        scope_key = scorecard_engine._entity_scope_id(payload.entity_id)
        entity_id = payload.entity_id

    updated = settings_repo.upsert_scope(
        scope_key,
        patch,
        scope=payload.scope,
        entity_id=entity_id,
        updated_by=current_user.get("user_id") or "",
    )
    _audit(
        action="incentive.settings.update",
        log_id=f"settings:{scope_key}",
        store_id=scope_key,
        current_user=current_user,
        detail={"scope": payload.scope, "entity_id": entity_id, "keys": list(patch.keys())},
    )
    return _serialize(updated)


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
