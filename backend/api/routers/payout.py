"""
IMS 2.0 — Payout Router (Pune Incentive Module iii)
=====================================================
Pure read-model over Modules (i)/(ii) + finance + clinical. Computes
pool sizing, individual payouts, manager bonuses; can persist as an
immutable monthly snapshot once the month is closed.

Mounted at /api/v1/payout.

  GET    /preview       live computation (no persist)
  POST   /lock          SUPERADMIN; persist as LOCKED; 409 if exists
  GET    /snapshots     list all for (store, year)
  GET    /snapshot/{id} fetch one
  PATCH  /snapshot/{id}/mark-paid SUPERADMIN; LOCKED → PAID; audited
  GET    /export/{id}.csv  per-staff CSV export

Settings PATCH endpoints live on the points router (already mounted
under /api/v1/incentive/points/settings/...) since they share the
incentive_settings collection.
"""

from __future__ import annotations

import csv as _csv
import io
import logging
from datetime import date as date_type, datetime
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..dependencies import (
    can_access_store_scoped,
    get_audit_repository,
    get_db,
    get_user_repository,
)

from database.repositories.incentive_settings_repository import (
    IncentiveSettingsRepository,
)
from database.repositories.payout_snapshot_repository import (
    PayoutSnapshotRepository,
)
from database.repositories.points_log_repository import PointsLogRepository
from api.services.points_calculator import aggregate_mtd
from api.services.payout_calculator import assemble_payout
from api.services.csv_safe import safe_writer, BOM

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# RBAC
# ============================================================================
_GLOBAL_ROLES = {"SUPERADMIN", "ADMIN"}
_STORE_ROLES = {"STORE_MANAGER", "AREA_MANAGER"}
_VIEW_ROLES = _GLOBAL_ROLES | _STORE_ROLES | {"ACCOUNTANT"}


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


def _check_view_permission(current_user: dict) -> None:
    if not (_user_role_set(current_user) & _VIEW_ROLES):
        raise HTTPException(
            status_code=403,
            detail="Only managers / admin / accountant can view payouts",
        )


# ============================================================================
# Repository accessors
# ============================================================================


def _settings_repo() -> Optional[IncentiveSettingsRepository]:
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return IncentiveSettingsRepository(db.get_collection("incentive_settings"))
    except Exception:
        return None


def _snapshot_repo() -> Optional[PayoutSnapshotRepository]:
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return PayoutSnapshotRepository(db.get_collection("payout_snapshots"))
    except Exception:
        return None


def _points_repo() -> Optional[PointsLogRepository]:
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return PointsLogRepository(db.get_collection("points_log"))
    except Exception:
        return None


# ============================================================================
# Auto-derive helpers
# ============================================================================


def _month_window(year: int, month: int) -> tuple:
    """Returns (start_dt, next_month_dt, df_str, dt_str). The datetime
    pair drives Mongo `created_at` range matches; the str pair is for
    legacy `date_str` fallbacks if any caller wants them."""
    from datetime import timedelta

    start_dt = datetime(year, month, 1)
    if month == 12:
        next_first = datetime(year + 1, 1, 1)
    else:
        next_first = datetime(year, month + 1, 1)
    df = start_dt.date().isoformat()
    dt = (next_first.date() - timedelta(days=1)).isoformat()
    return start_dt, next_first, df, dt


def _aggregate_sales(store_id: str, year: int, month: int) -> Dict[str, float]:
    """Sum grand_total + total_discount for the month from the orders
    collection. Returns {sales, discount, avg_discount_pct} (zero-safe).

    Filter on `created_at` (datetime range — orders.py stores this via
    BaseRepository._add_timestamps); orders don't have a `date_str`
    field. Sums `grand_total` (the order doc field — orders.py:738) and
    `total_discount` (added in the same Phase 6.15 fix that added the
    per-category GST helper)."""
    out = {"sales": 0.0, "discount": 0.0, "avg_discount_pct": 0.0}
    db = get_db()
    if db is None:
        return out
    try:
        orders = db.get_collection("orders")
    except Exception:
        return out
    start_dt, next_first, df, dt = _month_window(year, month)
    # Two paths: try $aggregate (real Mongo); fall back to find()
    docs: List[Dict] = []
    try:
        docs = list(
            orders.aggregate(
                [
                    {
                        "$match": {
                            "store_id": store_id,
                            "status": {"$nin": ["CANCELLED", "DRAFT"]},
                            "created_at": {"$gte": start_dt, "$lt": next_first},
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "total_revenue": {"$sum": "$grand_total"},
                            "total_discount": {"$sum": "$total_discount"},
                        }
                    },
                ]
            )
        )
        if docs:
            row = docs[0]
            out["sales"] = float(row.get("total_revenue") or 0)
            out["discount"] = float(row.get("total_discount") or 0)
    except Exception:
        try:
            for d in orders.find({"store_id": store_id}):
                created_at = d.get("created_at")
                # Accept both Python datetime and ISO string formats.
                if isinstance(created_at, datetime):
                    if not (start_dt <= created_at < next_first):
                        continue
                elif isinstance(created_at, str):
                    if not (df <= created_at[:10] <= dt):
                        continue
                else:
                    continue
                if d.get("status") in ("CANCELLED", "DRAFT"):
                    continue
                out["sales"] += float(d.get("grand_total") or 0)
                out["discount"] += float(d.get("total_discount") or 0)
        except Exception:
            pass
    if out["sales"] > 0:
        out["avg_discount_pct"] = round(out["discount"] / out["sales"], 4)
    return out


def _last_year_sale(store_id: str, year: int, month: int) -> float:
    """Pull the manually-input last-year-sale figure if it was POSTed,
    else fall back to aggregated last-year actuals from orders."""
    db = get_db()
    if db is None:
        return 0.0
    inputs = None
    try:
        inputs = db.get_collection("incentive_inputs").find_one(
            {
                "store_id": store_id,
                "year": year,
                "month": month,
            }
        )
    except Exception:
        inputs = None
    if inputs and inputs.get("last_year_sale") is not None:
        try:
            return float(inputs["last_year_sale"])
        except (TypeError, ValueError):
            pass
    # Fallback: aggregate last-year same month
    last = _aggregate_sales(store_id, year - 1, month)
    return last.get("sales", 0.0)


def _build_mtd_data(store_id: str, year: int, month: int) -> Dict[str, Dict]:
    """Per-staff MTD slot keyed by staff_id, ready to feed the
    calculator."""
    repo = _points_repo()
    if repo is None:
        return {}
    _, _, df, dt = _month_window(year, month)
    rows = repo.list_for_mtd(store_id, df, dt)
    by_staff = aggregate_mtd(rows)
    out: Dict[str, Dict] = {}
    for sid, slot in by_staff.items():
        avg = slot.get("avg") or {}
        out[sid] = {
            "name": slot.get("staff_name"),
            "mtd_avg_total": avg.get("total"),
            "eligibility": float(slot.get("eligibility_avg") or 0.0),
            "days_logged": slot.get("days_logged"),
        }
    return out


def _name_lookup_for(
    store_id: str, mtd_data: Dict[str, Dict]
) -> Dict[str, Optional[str]]:
    """Resolve user_id → display name via the user repo. Falls back to
    whatever's already on the MTD slot."""
    out: Dict[str, Optional[str]] = {}
    try:
        ur = get_user_repository()
    except Exception:
        ur = None
    for uid, slot in mtd_data.items():
        if ur is not None:
            try:
                u = ur.find_by_id(uid) or ur.find_one({"user_id": uid})
                if u:
                    out[uid] = (
                        u.get("name") or u.get("full_name") or u.get("username") or uid
                    )
                    continue
            except Exception:
                pass
        out[uid] = slot.get("name") or uid
    return out


# ============================================================================
# Audit
# ============================================================================


def _audit(
    *,
    action: str,
    snapshot_id: str,
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
                "entity_type": "payout_snapshot",
                "entity_id": snapshot_id,
                "store_id": store_id,
                "severity": "info",
                "detail": detail,
            }
        )
    except Exception as e:
        logger.warning(f"[PAYOUT] audit failed: {e}")


def _serialize(doc: Any) -> Any:
    if isinstance(doc, datetime):
        return doc.isoformat()
    if isinstance(doc, dict):
        return {k: _serialize(v) for k, v in doc.items() if k != "_id"}
    if isinstance(doc, list):
        return [_serialize(v) for v in doc]
    return doc


# ============================================================================
# Pydantic
# ============================================================================


class PreviewOverrides(BaseModel):
    """Optional manual overrides on the preview computation."""

    last_year_sale: Optional[float] = Field(None, ge=0)
    this_year_sale: Optional[float] = Field(None, ge=0)
    avg_discount_pct: Optional[float] = Field(None, ge=0, le=1)
    visufit_usage_pct: Optional[float] = Field(None, ge=0, le=1)


class LockSnapshotRequest(PreviewOverrides):
    year: int = Field(..., ge=2024, le=2100)
    month: int = Field(..., ge=1, le=12)


class MarkPaidRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=400)


# ============================================================================
# Core compute path (shared by /preview + /lock)
# ============================================================================


def _compute_payout(
    *,
    store_id: str,
    year: int,
    month: int,
    overrides: PreviewOverrides,
) -> Dict[str, Any]:
    settings_repo = _settings_repo()
    settings = (
        settings_repo.get_for_store(store_id)
        if settings_repo
        else IncentiveSettingsRepository.__new__(IncentiveSettingsRepository)._defaults(
            store_id
        )
    )  # type: ignore

    # Inputs
    if overrides.this_year_sale is not None:
        sales = float(overrides.this_year_sale)
        avg_disc = (
            overrides.avg_discount_pct
            if overrides.avg_discount_pct is not None
            else _aggregate_sales(store_id, year, month).get("avg_discount_pct", 0.0)
        )
    else:
        agg = _aggregate_sales(store_id, year, month)
        sales = agg["sales"]
        avg_disc = (
            overrides.avg_discount_pct
            if overrides.avg_discount_pct is not None
            else agg["avg_discount_pct"]
        )
    last_year = (
        overrides.last_year_sale
        if overrides.last_year_sale is not None
        else _last_year_sale(store_id, year, month)
    )
    visufit = (
        overrides.visufit_usage_pct if overrides.visufit_usage_pct is not None else 0.0
    )

    inputs = {
        "last_year_sale": last_year,
        "this_year_sale": sales,
        "avg_discount_pct": avg_disc,
        "visufit_usage_pct": visufit,
    }

    mtd_data = _build_mtd_data(store_id, year, month)
    name_lookup = _name_lookup_for(store_id, mtd_data)

    envelope = assemble_payout(
        inputs=inputs,
        settings=settings,
        mtd_data=mtd_data,
        name_lookup=name_lookup,
    )
    envelope["store_id"] = store_id
    envelope["year"] = year
    envelope["month"] = month
    return envelope


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/preview")
async def preview(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
    year: Optional[int] = Query(None, ge=2024, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    last_year_sale: Optional[float] = Query(None, ge=0),
    this_year_sale: Optional[float] = Query(None, ge=0),
    avg_discount_pct: Optional[float] = Query(None, ge=0, le=1),
    visufit_usage_pct: Optional[float] = Query(None, ge=0, le=1),
):
    """Live computation. No DB write. Defaults year+month to today;
    auto-derives sales/discount from orders aggregation when overrides
    aren't passed."""
    _check_view_permission(current_user)
    store = _resolve_store(current_user, store_id)
    now = datetime.now().date()
    yr = year or now.year
    mo = month or now.month
    overrides = PreviewOverrides(
        last_year_sale=last_year_sale,
        this_year_sale=this_year_sale,
        avg_discount_pct=avg_discount_pct,
        visufit_usage_pct=visufit_usage_pct,
    )
    return _serialize(
        _compute_payout(
            store_id=store,
            year=yr,
            month=mo,
            overrides=overrides,
        )
    )


@router.post("/lock", status_code=201)
async def lock(
    payload: LockSnapshotRequest,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """SUPERADMIN-only. Compute + persist as an immutable LOCKED
    snapshot. 409 if a LOCKED row already exists for (store, year, month)."""
    if "SUPERADMIN" not in _user_role_set(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only SUPERADMIN can lock a payout snapshot",
        )
    store = _resolve_store(current_user, store_id)
    repo = _snapshot_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    envelope = _compute_payout(
        store_id=store,
        year=payload.year,
        month=payload.month,
        overrides=payload,
    )
    doc = {
        **envelope,
        "store_id": store,
        "year": payload.year,
        "month": payload.month,
        "created_by": current_user.get("user_id"),
        "locked_by": current_user.get("user_id"),
    }
    try:
        saved = repo.create_snapshot(doc, status="LOCKED")
    except Exception as e:
        cls = type(e).__name__
        if "DuplicateKeyError" in cls or "duplicate key" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail=f"Already locked for {payload.year}-{payload.month:02d}. Mark as paid or delete first.",
            )
        raise HTTPException(status_code=500, detail=f"Lock failed: {e}")
    if saved is None:
        raise HTTPException(status_code=500, detail="Lock returned None")
    _audit(
        action="payout.lock",
        snapshot_id=saved["snapshot_id"],
        store_id=store,
        current_user=current_user,
        detail={
            "year": payload.year,
            "month": payload.month,
            "grand_total": saved.get("grand_total"),
            "best_level": saved.get("best_level_achieved"),
        },
    )
    return _serialize(saved)


@router.get("/snapshots")
async def list_snapshots(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
    year: Optional[int] = Query(None, ge=2024, le=2100),
):
    _check_view_permission(current_user)
    store = _resolve_store(current_user, store_id)
    repo = _snapshot_repo()
    if repo is None:
        return {"items": [], "store_id": store, "year": year}
    yr = year or datetime.now().year
    items = repo.list_for_store_year(store, yr)
    return {
        "items": [_serialize(i) for i in items],
        "store_id": store,
        "year": yr,
    }


@router.get("/snapshot/{snapshot_id}")
async def get_snapshot(
    snapshot_id: str,
    current_user: dict = Depends(get_current_user),
):
    _check_view_permission(current_user)
    repo = _snapshot_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = repo.find_by_id(snapshot_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    # NEW-IDOR-by-id: existence-hide snapshots whose store the caller can't access.
    if not can_access_store_scoped(doc.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return _serialize(doc)


@router.patch("/snapshot/{snapshot_id}/mark-paid")
async def mark_paid(
    snapshot_id: str,
    payload: MarkPaidRequest,
    current_user: dict = Depends(get_current_user),
):
    """Flip LOCKED → PAID. SUPERADMIN-only."""
    if "SUPERADMIN" not in _user_role_set(current_user):
        raise HTTPException(
            status_code=403,
            detail="Only SUPERADMIN can mark a payout as paid",
        )
    repo = _snapshot_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    existing = repo.find_by_id(snapshot_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    # NEW-IDOR-by-id: existence-hide snapshots whose store the caller can't access.
    if not can_access_store_scoped(existing.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Snapshot not found")
    if existing.get("status") != "LOCKED":
        raise HTTPException(
            status_code=409,
            detail=f"Snapshot is {existing.get('status')}, not LOCKED",
        )
    updated = repo.mark_paid(
        snapshot_id,
        paid_by=current_user.get("user_id") or "",
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="mark_paid failed")
    _audit(
        action="payout.mark_paid",
        snapshot_id=snapshot_id,
        store_id=existing.get("store_id", ""),
        current_user=current_user,
        detail={"note": payload.note or ""},
    )
    return _serialize(updated)


@router.get("/export/{snapshot_id}.csv")
async def export_csv(
    snapshot_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Per-staff CSV export of a snapshot. Includes grand totals
    summary in the trailing rows."""
    _check_view_permission(current_user)
    repo = _snapshot_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = repo.find_by_id(snapshot_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    # NEW-IDOR-by-id: existence-hide snapshots whose store the caller can't access.
    if not can_access_store_scoped(doc.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Snapshot not found")

    buf = io.StringIO()
    # BUG-139: neutralize formula-injection in user-controlled cells (staff /
    # manager names) so an exported name like `=cmd|'/C calc'!A0` can't execute
    # when the file is opened in Excel.
    w = safe_writer(buf)
    w.writerow(["IMS 2.0 Pune Incentive Payout"])
    w.writerow(
        [
            f"Store: {doc.get('store_id')}",
            f"Period: {doc.get('year')}-{doc.get('month'):02d}",
            f"Status: {doc.get('status')}",
        ]
    )
    w.writerow([])
    w.writerow(["Inputs"])
    inp = doc.get("inputs") or {}
    w.writerow(["Last year sale", inp.get("last_year_sale")])
    w.writerow(["This year sale", inp.get("this_year_sale")])
    w.writerow(["Avg discount %", f"{(inp.get('avg_discount_pct') or 0) * 100:.2f}"])
    w.writerow(["Visufit usage %", f"{(inp.get('visufit_usage_pct') or 0) * 100:.2f}"])
    w.writerow([])
    w.writerow(["Pool sizing"])
    w.writerow(["Best level", doc.get("best_level_achieved")])
    w.writerow(["Multiplier", doc.get("multiplier")])
    w.writerow(["Discount-kill active", doc.get("discount_kill_active")])
    w.writerow(["Total team pool", doc.get("total_team_pool")])
    w.writerow([])
    w.writerow(["Per-staff payouts"])
    w.writerow(
        [
            "User ID",
            "Name",
            "Weightage",
            "MTD avg total",
            "Eligibility",
            "L1",
            "L2",
            "L3",
            "Total payout",
        ]
    )
    for s in doc.get("staff_payouts") or []:
        plv = s.get("payout_by_level") or {}
        w.writerow(
            [
                s.get("user_id"),
                s.get("name"),
                s.get("weightage"),
                s.get("mtd_avg_total"),
                s.get("eligibility"),
                plv.get("L1"),
                plv.get("L2"),
                plv.get("L3"),
                s.get("total_payout"),
            ]
        )
    w.writerow([])
    w.writerow(["Manager bonuses"])
    w.writerow(
        [
            "User ID",
            "Name",
            "Role",
            "Eligibility",
            "L1 bonus",
            "L2 bonus",
            "L3 bonus",
            "Total bonus",
        ]
    )
    for m in doc.get("manager_bonuses") or []:
        blv = m.get("bonus_by_level") or {}
        w.writerow(
            [
                m.get("user_id"),
                m.get("name"),
                m.get("role"),
                m.get("eligibility"),
                blv.get("L1"),
                blv.get("L2"),
                blv.get("L3"),
                m.get("total_bonus"),
            ]
        )
    w.writerow([])
    g = doc.get("grand_total") or {}
    w.writerow(
        [
            "Grand totals",
            "Staff",
            g.get("staff"),
            "Manager",
            g.get("manager"),
            "All",
            g.get("all"),
        ]
    )
    buf.seek(0)
    return StreamingResponse(
        iter([BOM + buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={snapshot_id}.csv"},
    )
