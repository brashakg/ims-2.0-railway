"""
IMS 2.0 - Hub "Buy Desk" router (the owner's one-screen landing).

GET /api/v1/buy-desk/rows  -- read-only, per-product: catalog readiness +
honest online-store state + on-hand + on-order + a netted buy signal. Powers the
Buy Desk table. NO writes (the multi-select -> DRAFT-PO action reuses the existing
create_po and is a separate follow-up).

Catalog/purchase roles only (SUPERADMIN auto-passes). Fail-soft: a sub-signal
read error degrades that field, never 500s the screen. No emoji.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from .auth import require_roles
from ..dependencies import get_product_repository, resolve_store_scope
from ..services import buy_desk as _bd
from ..services import product_master as _pm
from ..services import shopify_push as _sp

router = APIRouter()
logger = logging.getLogger("ims.buy_desk_router")

# View roles: catalog owners + the PO raisers (they need to see what to buy).
_VIEW_ROLES = (
    "ADMIN",
    "CATALOG_MANAGER",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "ACCOUNTANT",
)


def _get_db():
    from database.connection import get_db

    return get_db().db


def _on_hand_map(db, product_ids: List[str], store_id: Optional[str]) -> Dict[str, int]:
    """AVAILABLE stock_units count per product (optionally scoped to a store)."""
    if db is None or not product_ids:
        return {}
    try:
        match: Dict[str, Any] = {
            "product_id": {"$in": product_ids},
            "status": "AVAILABLE",
        }
        if store_id:
            match["store_id"] = store_id
        cur = db.get_collection("stock_units").aggregate(
            [{"$match": match}, {"$group": {"_id": "$product_id", "n": {"$sum": 1}}}]
        )
        return {d["_id"]: int(d.get("n") or 0) for d in cur}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BUYDESK] on_hand map failed: %s", exc)
        return {}


def _on_order_map(db, product_ids: List[str]) -> Dict[str, int]:
    """Open-PO qty per product = ordered - already-received, across POs not
    CANCELLED/RECEIVED. Drafts count (they are intended orders)."""
    if db is None or not product_ids:
        return {}
    pid_set = set(product_ids)
    out: Dict[str, int] = {}
    try:
        cur = db.get_collection("purchase_orders").find(
            {"status": {"$nin": ["CANCELLED", "RECEIVED"]}},
            {"items": 1, "received_qty_by_product": 1, "_id": 0},
        )
        for po in cur:
            received = po.get("received_qty_by_product") or {}
            for it in po.get("items") or []:
                pid = it.get("product_id")
                if pid not in pid_set:
                    continue
                try:
                    ordered = int(it.get("quantity") or 0)
                except (TypeError, ValueError):
                    ordered = 0
                got = 0
                try:
                    got = int(received.get(pid) or 0)
                except (TypeError, ValueError):
                    got = 0
                open_qty = ordered - got
                if open_qty > 0:
                    out[pid] = out.get(pid, 0) + open_qty
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BUYDESK] on_order map failed: %s", exc)
        return {}
    return out


def _velocity_map(db, product_ids: List[str]) -> Dict[str, float]:
    """Units sold per day over the last 30 days, per product (from confirmed
    orders). Best-effort: any failure -> empty -> buy_signal reads None."""
    if db is None or not product_ids:
        return {}
    pid_set = set(product_ids)
    sold: Dict[str, int] = {}
    try:
        # NAIVE-UTC DATETIME bound -- created_at is a BSON datetime (POS always;
        # online post-#935 + backfill). The old .isoformat() STRING bound never
        # matched a Date field (Mongo type bracketing) -> zero velocity for all.
        cutoff = datetime.utcnow() - timedelta(days=30)
        cur = db.get_collection("orders").find(
            {
                "created_at": {"$gte": cutoff},
                "status": {"$nin": ["CANCELLED", "DRAFT"]},
            },
            {"items": 1, "_id": 0},
        )
        for o in cur:
            for it in o.get("items") or []:
                pid = it.get("product_id")
                if pid not in pid_set:
                    continue
                try:
                    sold[pid] = sold.get(pid, 0) + int(it.get("quantity") or 0)
                except (TypeError, ValueError):
                    continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BUYDESK] velocity map failed: %s", exc)
        return {}
    return {pid: n / 30.0 for pid, n in sold.items() if n > 0}


@router.get("/rows")
async def buy_desk_rows(
    store_id: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(require_roles(*_VIEW_ROLES)),
):
    """Per-product Buy Desk rows: readiness + ecom state + on-hand + on-order +
    netted buy signal. Read-only."""
    # Store-scope the on-hand read (same canonical guard as the LIST endpoints):
    # an explicit ?store_id is validated against the caller's access (403 on a
    # foreign store); omitted -> the caller's own active store for store-level
    # roles, or None (all stores) for SUPERADMIN/ADMIN. Area/admin reach is kept.
    store_id = resolve_store_scope(store_id, current_user)
    repo = get_product_repository()
    db = _get_db()
    if repo is None:
        return {"rows": [], "total": 0, "store_id": store_id}

    try:
        products = repo.find_many({}, skip=skip, limit=limit) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BUYDESK] product load failed: %s", exc)
        products = []

    product_ids = [p.get("product_id") for p in products if p.get("product_id")]
    on_hand = _on_hand_map(db, product_ids, store_id)
    on_order = _on_order_map(db, product_ids)
    velocity = _velocity_map(db, product_ids)

    rows: List[Dict[str, Any]] = []
    for p in products:
        pid = p.get("product_id")
        try:
            readiness = _pm.catalog_readiness(p)
        except Exception:  # noqa: BLE001
            readiness = {
                "complete": False,
                "missing": [],
                "blockers": [],
                "purchasable": False,
            }
        try:
            locked = bool(_sp.push_lock_reason(db, "product", p))
        except Exception:  # noqa: BLE001
            locked = False
        rows.append(
            _bd.build_row(
                p,
                readiness=readiness,
                push_locked=locked,
                on_hand=on_hand.get(pid, 0),
                on_order=on_order.get(pid, 0),
                velocity_per_day=velocity.get(pid),
            )
        )

    return {"rows": rows, "total": len(rows), "store_id": store_id}
