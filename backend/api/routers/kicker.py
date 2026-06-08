"""
IMS 2.0 - Product-Incentive (Kicker) Router (SC)
=================================================
Rupee incentive for qualifying premium product sales (e.g. ZEISS PAL),
SPIFFs (category="SPIFF"), and clawbacks (negative amount). One collection,
one monthly rollup -- NO parallel commission feed (CORRECTIONS P0-3).

The kicker is folded into the payout snapshot (scorecard_engine.compute_payout)
and reaches payroll through the LOCKED snapshot ONLY (P0-4). This router does
NOT write to payroll directly.

Mounted at /api/v1/incentive/kicker.

  POST /product-sale     log a kicker (manual by manager / sales staff own;
                         POS auto-attach is feature-flagged, off by default)
  GET  /{ym}             monthly rollup per staff (own-only for sales staff)

No emoji (Windows cp1252).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date as date_type, datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..dependencies import get_audit_repository, get_db, get_user_repository

from database.repositories.product_incentive_log_repository import (
    ProductIncentiveLogRepository,
)
from api.services import scorecard_engine

logger = logging.getLogger(__name__)
router = APIRouter()

_GLOBAL_ROLES = {"SUPERADMIN", "ADMIN"}
_STORE_ROLES = {"STORE_MANAGER", "AREA_MANAGER"}
_MANAGER_ROLES = _GLOBAL_ROLES | _STORE_ROLES | {"ACCOUNTANT"}
_SALES_ROLES = {"SALES_STAFF", "SALES_CASHIER", "CASHIER"}
_YM_RE = re.compile(r"^\d{4}-\d{2}$")


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


def _kicker_repo() -> Optional[ProductIncentiveLogRepository]:
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return ProductIncentiveLogRepository(
            db.get_collection("product_incentive_log")
        )
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


def _audit(*, action, entry_id, store_id, current_user, detail) -> None:
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
                "entity_type": "product_incentive_log",
                "entity_id": entry_id,
                "store_id": store_id,
                "severity": "info",
                "detail": detail,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[KICKER] audit failed: %s", e)


def _serialize(doc):
    if isinstance(doc, datetime):
        return doc.isoformat()
    if isinstance(doc, dict):
        return {k: _serialize(v) for k, v in doc.items() if k != "_id"}
    if isinstance(doc, list):
        return [_serialize(v) for v in doc]
    return doc


class ProductSaleKicker(BaseModel):
    staff_id: str = Field(..., min_length=1)
    date: date_type
    sku: str = Field(..., min_length=1)
    brand: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    description: Optional[str] = None
    order_id: Optional[str] = None
    product_id: Optional[str] = None
    incentive_amount: float = Field(
        ..., ge=-100000, le=100000,
        description="Rupees. Negative = clawback (DECISIONS.md s4). Server-bounded "
                    "+/-1L so a self-loggable kicker can't mint an unbounded "
                    "payroll-feeding amount (money-integrity: never trust a client amount).",
    )


@router.post("/product-sale", status_code=201)
async def log_product_sale(
    payload: ProductSaleKicker,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """Log a product-incentive kicker. Sales staff may log only their own;
    managers/admin/accountant may log for any staff in their store. 409 on a
    duplicate (order_id, sku) -- the POS line can't be double-logged."""
    roles = _user_role_set(current_user)
    store = _resolve_store(current_user, store_id)

    if not (roles & _MANAGER_ROLES):
        # Sales staff: own entries only.
        if current_user.get("user_id") != payload.staff_id:
            raise HTTPException(
                status_code=403,
                detail="You can only log product incentive for yourself",
            )

    repo = _kicker_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    date_str = payload.date.isoformat()
    doc = {
        "store_id": store,
        "date": datetime.combine(payload.date, datetime.min.time()),
        "date_str": date_str,
        "ym": date_str[:7],
        "staff_id": payload.staff_id,
        "staff_name": _resolve_staff_name(payload.staff_id),
        "sku": payload.sku,
        "product_id": payload.product_id,
        "brand": payload.brand,
        "category": payload.category,
        "description": payload.description,
        "order_id": payload.order_id,
        "incentive_amount": round(float(payload.incentive_amount), 2),
        "created_by": current_user.get("user_id"),
    }
    try:
        saved = repo.log_entry(doc)
    except Exception as e:  # noqa: BLE001
        cls = type(e).__name__
        if "DuplicateKeyError" in cls or "duplicate key" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail="This order line already has a product-incentive entry.",
            )
        raise HTTPException(status_code=500, detail=f"Save failed: {e}")
    if saved is None:
        raise HTTPException(status_code=500, detail="Save returned None")
    _audit(
        action="incentive.kicker.create",
        entry_id=saved["entry_id"],
        store_id=store,
        current_user=current_user,
        detail={
            "staff_id": payload.staff_id,
            "sku": payload.sku,
            "amount": doc["incentive_amount"],
            "order_id": payload.order_id,
        },
    )
    return _serialize(saved)


@router.get("/{ym}")
async def kicker_rollup(
    ym: str,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
    staff_id: Optional[str] = Query(None),
):
    """Monthly product-incentive rollup per staff for 'YYYY-MM'. Sales staff
    see only their own; managers/admin/accountant see all."""
    if not _YM_RE.match(ym or ""):
        raise HTTPException(status_code=400, detail="ym must be YYYY-MM")
    roles = _user_role_set(current_user)
    store = _resolve_store(current_user, store_id)

    if not (roles & _MANAGER_ROLES):
        # Sales staff: force own-only.
        staff_id = current_user.get("user_id")

    repo = _kicker_repo()
    if repo is None:
        return {"store_id": store, "ym": ym, "items": [], "total": 0.0}

    entries = repo.list_for_ym(store, ym, staff_id=staff_id)
    by_staff: Dict[str, Dict] = {}
    for e in entries:
        sid = e.get("staff_id") or ""
        slot = by_staff.setdefault(
            sid,
            {
                "staff_id": sid,
                "staff_name": e.get("staff_name"),
                "total_rupees": 0.0,
                "sale_count": 0,
                "entries": [],
            },
        )
        slot["total_rupees"] = round(
            slot["total_rupees"] + float(e.get("incentive_amount") or 0.0), 2
        )
        slot["sale_count"] += 1
        slot["entries"].append(_serialize(e))

    items: List[Dict] = list(by_staff.values())
    items.sort(key=lambda s: -float(s.get("total_rupees") or 0.0))
    grand = round(sum(float(s["total_rupees"]) for s in items), 2)
    return {"store_id": store, "ym": ym, "items": items, "total": grand}
