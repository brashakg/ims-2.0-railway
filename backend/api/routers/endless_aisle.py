"""
IMS 2.0 - Feature #38: Manager-restricted Endless Aisle router
==============================================================
HTTP surface for ``api/services/endless_aisle.py``. When a selling store is out
of stock of a SKU, a STORE_MANAGER+ can fulfill it from another branch: see
cross-store availability, open a request, the SOURCE store ACCEPTS (two-step,
prevents selling ghost stock), then a transfer is created (source -> selling)
and tracked to delivery. The COMPANY bears shipping (booked to the selling
store's P&L; the customer pays nothing extra). Payment is always taken at the
selling store -- this module NEVER touches POS pricing/discount/tender/GST.

Everything is behind ``endless_aisle.enabled`` (E2, default False): when off,
every route returns 403 feature_disabled and nothing about POS changes. Every
route store-scopes. No emoji (Windows cp1252).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..dependencies import validate_store_access
from ..services import endless_aisle as svc

router = APIRouter(tags=["endless-aisle"])

_MANAGER_ROLES = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return {str(r).upper() for r in (user.get("roles", []) or [])}


def _require_manager(user: Dict[str, Any], what: str):
    if not (_roles(user) & _MANAGER_ROLES):
        raise HTTPException(status_code=403, detail=f"not permitted to {what}")


def _require_enabled():
    """Flag gate: when endless_aisle.enabled is off (the default) every route
    is 403 feature_disabled -- the feature is inert on a fresh deploy."""
    try:
        from ..services.policy_engine import get_policy

        if not bool(get_policy(svc.POLICY_ENABLED, None, default=False)):
            raise HTTPException(status_code=403, detail="endless_aisle_disabled")
    except HTTPException:
        raise
    except Exception:
        # Fail CLOSED: if the flag can't be read, treat as disabled.
        raise HTTPException(status_code=403, detail="endless_aisle_disabled")


def _eligible_stores() -> Optional[List[str]]:
    try:
        from ..services.policy_engine import get_policy

        val = get_policy(svc.POLICY_ELIGIBLE_STORES, None, default=None)
        return val if isinstance(val, list) and val else None
    except Exception:  # noqa: BLE001
        return None


def _on_hand_resolver(product_id, store_id) -> int:
    """Live on-hand for one product at one store (reused from inventory)."""
    try:
        from .inventory import _on_hand_by_product

        m = _on_hand_by_product(_get_db(), [product_id], store_id) or {}
        return int(m.get(product_id, 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _raise(exc: "svc.EndlessAisleError"):
    raise HTTPException(status_code=int(getattr(exc, "status", 400)), detail=str(exc))


def _audit(action, *, entity_id, actor, store_id, detail):
    try:
        from ..dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "action": action,
                "entity_type": "endless_aisle_request",
                "entity_id": entity_id,
                "store_id": store_id,
                "user_id": actor.get("user_id"),
                "severity": "INFO",
                "source": "endless_aisle",
                "detail": detail or {},
            }
        )
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RequestBody(BaseModel):
    product_id: str = Field(..., min_length=1)
    qty: int = Field(..., gt=0)
    selling_store_id: str = Field(..., min_length=1)
    source_store_id: str = Field(..., min_length=1)
    order_id: Optional[str] = None
    delivery_address: Optional[Dict[str, Any]] = None


class RejectBody(BaseModel):
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/availability")
async def availability(
    product_id: str,
    qty: int,
    selling_store_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Cross-store source candidates for an OOS SKU. Manager + store-scoped."""
    _require_enabled()
    _require_manager(current_user, "use endless aisle")
    validate_store_access(selling_store_id, current_user)
    try:
        from .inventory import _on_hand_by_product

        on_hand = _on_hand_by_product(_get_db(), [product_id], None) or {}
    except Exception:  # noqa: BLE001
        on_hand = {}
    # _on_hand_by_product keyed by product when store=None won't split by store;
    # build a per-store map via the resolver across known stores instead.
    stores: Dict[str, int] = {}
    try:
        from database.connection import get_db

        db = get_db().db
        if db is not None:
            for s in db.get_collection("stores").find({}, {"store_id": 1}):
                sid = s.get("store_id")
                if sid:
                    stores[sid] = _on_hand_resolver(product_id, sid)
    except Exception:  # noqa: BLE001
        stores = {}
    sources = svc.find_fulfillment_sources(
        stores, selling_store_id, _eligible_stores(), qty
    )
    return {
        "product_id": product_id,
        "qty": qty,
        "selling_store_id": selling_store_id,
        "sources": sources,
    }


@router.post("/requests")
async def open_request(
    body: RequestBody, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Open a PENDING fulfillment request (re-validates source on-hand)."""
    _require_enabled()
    _require_manager(current_user, "open an endless-aisle request")
    validate_store_access(body.selling_store_id, current_user)
    try:
        req = svc.open_request(
            _get_db(),
            body.dict(),
            actor=current_user,
            on_hand_resolver=_on_hand_resolver,
        )
    except svc.EndlessAisleError as exc:
        _raise(exc)
    _audit(
        "endless_aisle.open",
        entity_id=req["request_id"],
        actor=current_user,
        store_id=body.selling_store_id,
        detail={"source": body.source_store_id, "product": body.product_id},
    )
    return req


@router.post("/requests/{request_id}/accept")
async def accept_request(
    request_id: str, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """SOURCE store confirms the unit + accepts. Requires SOURCE-store access."""
    _require_enabled()
    _require_manager(current_user, "accept an endless-aisle request")
    db = _get_db()
    req = svc.get_request(db, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="request not found")
    validate_store_access(
        req.get("source_store_id"), current_user
    )  # the SOURCE accepts
    try:
        updated = svc.accept_request(
            db, request_id, actor=current_user, on_hand_resolver=_on_hand_resolver
        )
    except svc.EndlessAisleError as exc:
        _raise(exc)
    _audit(
        "endless_aisle.accept",
        entity_id=request_id,
        actor=current_user,
        store_id=req.get("source_store_id"),
        detail={},
    )
    return updated


@router.post("/requests/{request_id}/reject")
async def reject_request(
    request_id: str,
    body: RejectBody,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """SOURCE store declines. Requires SOURCE-store access."""
    _require_enabled()
    _require_manager(current_user, "reject an endless-aisle request")
    db = _get_db()
    req = svc.get_request(db, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="request not found")
    validate_store_access(req.get("source_store_id"), current_user)
    try:
        return svc.reject_request(
            db, request_id, actor=current_user, reason=body.reason
        )
    except svc.EndlessAisleError as exc:
        _raise(exc)


@router.post("/requests/{request_id}/create-transfer")
async def create_transfer(
    request_id: str, current_user: Dict[str, Any] = Depends(get_current_user)
):
    """Create the source->selling transfer (company-borne shipping). Requires
    SELLING-store access (the selling store drives fulfillment)."""
    _require_enabled()
    _require_manager(current_user, "create an endless-aisle transfer")
    db = _get_db()
    req = svc.get_request(db, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="request not found")
    validate_store_access(req.get("selling_store_id"), current_user)
    try:
        updated = svc.create_transfer(
            db, request_id, actor=current_user, on_hand_resolver=_on_hand_resolver
        )
    except svc.EndlessAisleError as exc:
        _raise(exc)
    _audit(
        "endless_aisle.create_transfer",
        entity_id=request_id,
        actor=current_user,
        store_id=req.get("selling_store_id"),
        detail={"transfer_id": updated.get("transfer_id")},
    )
    return updated


@router.post("/requests/{request_id}/ship")
async def ship_request(
    request_id: str, current_user: Dict[str, Any] = Depends(get_current_user)
):
    _require_enabled()
    _require_manager(current_user, "ship an endless-aisle request")
    db = _get_db()
    req = svc.get_request(db, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="request not found")
    validate_store_access(req.get("selling_store_id"), current_user)
    try:
        return svc.advance(db, request_id, svc.STATUS_SHIPPED, actor=current_user)
    except svc.EndlessAisleError as exc:
        _raise(exc)


@router.post("/requests/{request_id}/deliver")
async def deliver_request(
    request_id: str, current_user: Dict[str, Any] = Depends(get_current_user)
):
    _require_enabled()
    _require_manager(current_user, "deliver an endless-aisle request")
    db = _get_db()
    req = svc.get_request(db, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="request not found")
    validate_store_access(req.get("selling_store_id"), current_user)
    try:
        return svc.advance(db, request_id, svc.STATUS_DELIVERED, actor=current_user)
    except svc.EndlessAisleError as exc:
        _raise(exc)


@router.get("/requests")
async def list_requests(
    store_id: str,
    status: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    _require_enabled()
    _require_manager(current_user, "view endless-aisle requests")
    validate_store_access(store_id, current_user)
    return {"requests": svc.list_requests(_get_db(), store_id=store_id, status=status)}


@router.get("/requests/{request_id}")
async def get_request(
    request_id: str, current_user: Dict[str, Any] = Depends(get_current_user)
):
    _require_enabled()
    _require_manager(current_user, "view an endless-aisle request")
    req = svc.get_request(_get_db(), request_id)
    if not req:
        raise HTTPException(status_code=404, detail="request not found")
    # either side of the transfer may view it
    roles = _roles(current_user)
    if not ({"ADMIN", "SUPERADMIN", "AREA_MANAGER"} & roles):
        allowed = set(current_user.get("store_ids") or [])
        if (
            req.get("selling_store_id") not in allowed
            and req.get("source_store_id") not in allowed
        ):
            raise HTTPException(status_code=403, detail="not permitted for this store")
    return req
