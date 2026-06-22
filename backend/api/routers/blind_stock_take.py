"""
IMS 2.0 - Feature #15: Blind stock take router
==============================================
HTTP surface for ``api/services/blind_stock_take.py``. Staff count stock BLIND
(no system on-hand shown); on manager LOCK the per-SKU variance is revealed and
the count is SOFT-LOCKED (transparent, manager-reopenable, audited). A confirmed
variance enqueues a reversible stock-ADJUSTMENT PROPOSAL -- it never auto-mutates
on-hand.

Blind enforcement is at the DATA layer (``redact_for_counter``): a non-manager
never sees the expected on-hand / variance while the session is OPEN. Reveal +
lock + reopen + propose are manager+ only; every route store-scopes.

No emoji (Windows cp1252).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo.errors import PyMongoError

from .auth import get_current_user
from ..dependencies import validate_store_access
from ..services import blind_stock_take as svc
from ..services.non_adapt import rupees_to_paise

router = APIRouter(tags=["blind-stock-take"])

# Who may OPEN a count + SUBMIT blind counted quantities: floor staff + inventory
# + management (the people who physically count).
_COUNT_ROLES = {"SALES_STAFF", "SALES_CASHIER", "CATALOG_MANAGER", "STORE_MANAGER",
                "AREA_MANAGER", "ADMIN", "SUPERADMIN"}
# Who may REVEAL + LOCK + REOPEN + propose an adjustment: managers + (the counter
# never sees the expected figure pre-lock nor approves an adjustment).
_MANAGER_ROLES = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return {str(r).upper() for r in (user.get("roles", []) or [])}


def _require(user: Dict[str, Any], allowed: set, what: str):
    if not (_roles(user) & allowed):
        raise HTTPException(status_code=403, detail=f"not permitted to {what}")


def _engine():
    return svc.BlindStockTakeEngine(_get_db())


def _raise(exc: "svc.BlindStockTakeError"):
    raise HTTPException(status_code=int(getattr(exc, "status", 400)), detail=str(exc))


def _audit(action, *, entity_id, actor, store_id, detail):
    try:
        from ..dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is None:
            return
        repo.create({"action": action, "entity_type": "blind_stock_take", "entity_id": entity_id,
                     "store_id": store_id or actor.get("active_store_id"), "user_id": actor.get("user_id"),
                     "severity": "INFO", "source": "blind_stock_take", "detail": detail or {}})
    except Exception:  # noqa: BLE001
        return


def _tolerance(store_id):
    try:
        from ..services.policy_engine import get_policy
        return int(get_policy(svc.TOLERANCE_KEY, {"store_id": store_id}, default=0))
    except Exception:  # noqa: BLE001
        return 0


def _reopen_roles(store_id):
    try:
        from ..services.policy_engine import get_policy
        val = get_policy(svc.REOPEN_ROLES_KEY, {"store_id": store_id}, default=None)
        return val if isinstance(val, list) and val else None
    except Exception:  # noqa: BLE001
        return None


def _on_hand_resolver(store_id, product_ids):
    """System on-hand per product (reused from inventory). Read-only."""
    try:
        from .inventory import _on_hand_by_product
        return _on_hand_by_product(_get_db(), list(product_ids), store_id) or {}
    except Exception:  # noqa: BLE001
        return {}


def _cost_resolver(product_ids):
    """Per-product cost in integer paise for the variance valuation.

    Only a genuine DB fault degrades to an empty (zero-valuation) map -- a
    programmer error (bad helper, bad shape) must FAIL LOUDLY, never masquerade
    as 'no costs found' and silently zero out a real shrinkage figure.

    Falls back to catalog_products for ids not found in the products spine so
    catalog-only products are included in the shrinkage valuation (not zeroed).
    """
    db = _get_db()
    out: Dict[str, int] = {}
    if db is None:
        return out
    ids = list(product_ids)
    try:
        rows = list(db.get_collection("products").find(
            {"product_id": {"$in": ids}}, {"product_id": 1, "cost_price": 1}))
    except PyMongoError:
        return {}
    # cost_price persists in RUPEES -> integer paise once, at the boundary.
    found_ids = set()
    for p in rows:
        pid = p.get("product_id")
        if pid:
            out[pid] = rupees_to_paise(p.get("cost_price"))
            found_ids.add(pid)
    # Catalog-only fallback for any ids that were not in the products spine.
    missing = [i for i in ids if i not in found_ids]
    if missing:
        try:
            cat_rows = list(db.get_collection("catalog_products").find(
                {"id": {"$in": missing}},
                {"id": 1, "pricing.cost_price": 1},
            ))
            for p in cat_rows:
                pid = p.get("id")
                if pid:
                    cost = (p.get("pricing") or {}).get("cost_price")
                    out[pid] = rupees_to_paise(cost)
        except PyMongoError:
            pass  # fail-soft: catalog miss -> zero paise for those ids
    return out


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class OpenBody(BaseModel):
    store_id: str
    scope: Optional[Dict[str, Any]] = None


class CountLine(BaseModel):
    product_id: Optional[str] = None
    sku: Optional[str] = None
    counted_qty: int = Field(0, ge=0)


class SubmitBody(BaseModel):
    counts: List[CountLine] = Field(default_factory=list)


class ReopenBody(BaseModel):
    reason: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/open")
async def open_count(body: OpenBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Open a blind count session. Floor staff / inventory / manager + store-scoped."""
    _require(current_user, _COUNT_ROLES, "open a stock count")
    validate_store_access(body.store_id, current_user)
    try:
        sess = _engine().open_session(store_id=body.store_id, actor=current_user, scope=body.scope)
    except svc.BlindStockTakeError as exc:
        _raise(exc)
    _audit("blind_count.open", entity_id=sess["session_id"], actor=current_user, store_id=body.store_id, detail={})
    return svc.redact_for_counter(sess, current_user)


@router.post("/{session_id}/submit")
async def submit_count(session_id: str, body: SubmitBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Submit blind counted quantities. The response is redacted (no expected
    figure) for a non-manager. Floor staff / manager + store-scoped."""
    _require(current_user, _COUNT_ROLES, "submit a stock count")
    sess = _engine().get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="count session not found")
    validate_store_access(sess.get("store_id"), current_user)
    try:
        updated = _engine().submit_count(session_id, [c.model_dump() for c in body.counts],
                                         store_id=sess.get("store_id"), actor=current_user)
    except svc.BlindStockTakeError as exc:
        _raise(exc)
    return svc.redact_for_counter(updated, current_user)


@router.post("/{session_id}/lock")
async def lock_count(session_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Reveal variance + soft-lock (atomic). Manager+ only + store-scoped."""
    _require(current_user, _MANAGER_ROLES, "lock a stock count")
    sess = _engine().get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="count session not found")
    store_id = sess.get("store_id")
    validate_store_access(store_id, current_user)
    try:
        updated = _engine().lock_and_reveal(
            session_id, store_id=store_id, actor=current_user,
            on_hand_resolver=_on_hand_resolver, cost_resolver=_cost_resolver,
            tolerance=_tolerance(store_id))
    except svc.BlindStockTakeError as exc:
        _raise(exc)
    _audit("blind_count.lock", entity_id=session_id, actor=current_user, store_id=store_id,
           detail={"summary": updated.get("summary")})
    return updated


@router.post("/{session_id}/reopen")
async def reopen_count(session_id: str, body: ReopenBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Reopen a locked count (mandatory reason). Manager+ (or E2 reopen roles) + store-scoped."""
    sess = _engine().get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="count session not found")
    store_id = sess.get("store_id")
    validate_store_access(store_id, current_user)
    reopen_roles = _reopen_roles(store_id)
    allowed = ({str(r).upper() for r in reopen_roles} | {"ADMIN", "SUPERADMIN"}) if reopen_roles else _MANAGER_ROLES
    _require(current_user, allowed, "reopen a stock count")
    try:
        updated = _engine().reopen(session_id, store_id=store_id, actor=current_user,
                                   reason=body.reason, reopen_roles=reopen_roles)
    except svc.BlindStockTakeError as exc:
        _raise(exc)
    _audit("blind_count.reopen", entity_id=session_id, actor=current_user, store_id=store_id,
           detail={"reason": body.reason})
    return updated


@router.post("/{session_id}/propose-adjustment")
async def propose_adjustment(session_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Enqueue a reversible stock-adjustment PROPOSAL from the variances. Manager+
    only. Does NOT mutate on-hand."""
    _require(current_user, _MANAGER_ROLES, "propose a stock adjustment")
    sess = _engine().get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="count session not found")
    validate_store_access(sess.get("store_id"), current_user)
    try:
        proposal = _engine().propose_adjustment(session_id, store_id=sess.get("store_id"), actor=current_user)
    except svc.BlindStockTakeError as exc:
        _raise(exc)
    _audit("blind_count.propose_adjustment", entity_id=proposal["proposal_id"], actor=current_user,
           store_id=sess.get("store_id"), detail={"lines": len(proposal.get("lines") or [])})
    return proposal


@router.get("/{session_id}")
async def get_count(session_id: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """One count session. Counter sees a BLIND-redacted view pre-lock; manager
    sees the full figures. Store-scoped."""
    _require(current_user, _COUNT_ROLES, "view a stock count")
    sess = _engine().get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="count session not found")
    validate_store_access(sess.get("store_id"), current_user)
    return svc.redact_for_counter(sess, current_user, reopen_roles=_reopen_roles(sess.get("store_id")))
