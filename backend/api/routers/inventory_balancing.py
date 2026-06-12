"""
IMS 2.0 - Feature #1: Cross-store inventory balancing router
============================================================
Read-only HTTP surface for ``api/services/inventory_balancing.py``. Surfaces
per-product rebalancing PROPOSALS -- move surplus units from a DEAD/OVERSTOCK
donor store to the highest-velocity UNDERSTOCK/STOCKOUT recipient store, over a
configurable (E2) sell-through window. It NEVER mutates stock and NEVER executes
a transfer; a manager acts on a proposal via the existing transfers flow.

Auth: management roles only. Cross-store roles (SUPERADMIN/ADMIN/AREA_MANAGER)
see every proposal; a single-store manager sees only proposals INVOLVING a store
they can access (as donor or recipient) -- the cross-store data is read to
compute the move, but the OUTPUT is store-scoped (no other-store-only leakage).

No emoji (Windows cp1252).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import get_current_user
from ..dependencies import user_store_scope
from ..services import inventory_balancing as svc

router = APIRouter(tags=["inventory-balancing"])

# Management only -- floor/cashier/optometrist roles never see cross-store stock.
_ALLOWED_ROLES = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return {str(r).upper() for r in (user.get("roles", []) or [])}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _policy(key, default, store_id=None):
    try:
        from ..services.policy_engine import get_policy
        val = get_policy(key, {"store_id": store_id} if store_id else None, default=default)
        return int(val)
    except Exception:  # noqa: BLE001
        return default


def _thresholds(store_id: Optional[str]) -> Dict[str, int]:
    return {
        "window_days": _policy(svc.POLICY_WINDOW_DAYS, svc.DEFAULT_WINDOW_DAYS, store_id),
        "overstock_days_cover": _policy(svc.POLICY_OVERSTOCK_DAYS_COVER, svc.DEFAULT_OVERSTOCK_DAYS_COVER, store_id),
        "understock_days_cover": _policy(svc.POLICY_UNDERSTOCK_DAYS_COVER, svc.DEFAULT_UNDERSTOCK_DAYS_COVER, store_id),
        "target_days_cover": _policy(svc.POLICY_TARGET_DAYS_COVER, svc.DEFAULT_TARGET_DAYS_COVER, store_id),
    }


class ProposalsResponse(BaseModel):
    proposals: list
    summary: dict
    thresholds: dict
    generated_at: str
    store_scoped: bool


@router.get("/proposals", response_model=ProposalsResponse)
async def get_proposals(store_id: Optional[str] = None, brand: Optional[str] = None,
                        category: Optional[str] = None,
                        current_user: Dict[str, Any] = Depends(get_current_user)):
    """Cross-store rebalancing proposals. Read-only. Management roles only;
    a single-store manager's view is filtered to proposals touching their store."""
    if not (_roles(current_user) & _ALLOWED_ROLES):
        raise HTTPException(status_code=403, detail="not permitted to view inventory balancing")

    db = _get_db()
    thresholds = _thresholds(store_id)
    stats = svc.gather_stats(db, window_days=thresholds["window_days"], brand=brand, category=category)
    enriched = svc.classify_all(stats, thresholds=thresholds)
    proposals = svc.propose_moves(enriched, thresholds=thresholds)

    # AUTH store-scope on the OUTPUT: cross-store roles see all; a store-level
    # role sees only proposals where their store is the donor or the recipient.
    is_cross, allowed = user_store_scope(current_user)
    if not is_cross:
        proposals = [m for m in proposals
                     if (m.get("from_store") in allowed or m.get("to_store") in allowed)]

    summary = svc.summarize(enriched, proposals)
    return {
        "proposals": proposals,
        "summary": summary,
        "thresholds": thresholds,
        "generated_at": _now_iso(),
        "store_scoped": not is_cross,
    }
