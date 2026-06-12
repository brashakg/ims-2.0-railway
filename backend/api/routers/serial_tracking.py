"""
IMS 2.0 - Feature #6: Luxury / per-unit SERIAL tracking router
==============================================================
HTTP surface for ``api/services/serial_tracking.py``. A unique serial is captured
at STOCK-IN for serialized / high-value units (hearing aids, luxury frames/watches
-- per owner decision, the serial lives on the UNIT, not the catalogue), tracked
through the SALE (atomic IN_STOCK -> SOLD so a serial can NEVER be double-sold),
and looked up for WARRANTY / RECALL.

INVENTORY writes only -- the at-sale transition stamps order_id/customer/sold_at on
the stock_unit; it does NOT touch the order total or any payment (POS money capture
is unchanged). Every route store-scopes; a cashier can never mint / relabel / recall
a serial (only read a warranty).

No emoji (Windows cp1252).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..dependencies import validate_store_access
from ..services import serial_tracking as svc

router = APIRouter(tags=["serials"])

# Who may CAPTURE a serial at stock-in / mark it sold / recall / return: store
# management + catalog/inventory. A cashier/sales role can NEVER mint or relabel
# a serial -> 403. The at-sale mark-sold is a system/manager action (driven by the
# order-finalize path), not a cashier action that could be abused.
_STOCK_ROLES = {"CATALOG_MANAGER", "STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}
_RECALL_ROLES = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}
# Warranty lookup + single-serial read: any authenticated store staff (a cashier
# CAN look up a warranty; it is read-only).
_READ_ROLES = {"SALES_CASHIER", "CASHIER", "SALES_STAFF", "OPTOMETRIST",
               "CATALOG_MANAGER", "STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT",
               "ADMIN", "SUPERADMIN"}


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return {str(r).upper() for r in (user.get("roles", []) or [])}


def _require(user: Dict[str, Any], allowed: set, what: str):
    if not (_roles(user) & allowed):
        raise HTTPException(status_code=403, detail=f"not permitted to {what}")


def _units():
    return _get_db().get_collection("stock_units")


def _raise(exc: "svc.SerialError"):
    raise HTTPException(status_code=int(getattr(exc, "status", 400)), detail=str(exc))


def _get_policy(key, scope=None, *, default=None):
    try:
        from ..services.policy_engine import get_policy

        return get_policy(key, scope, default=default)
    except Exception:  # noqa: BLE001
        return default


def _audit(action, *, entity_id, actor, store_id, detail):
    try:
        from ..dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is None:
            return
        repo.create({"action": action, "entity_type": "stock_unit_serial",
                     "entity_id": entity_id, "store_id": store_id or actor.get("active_store_id"),
                     "user_id": actor.get("user_id"), "severity": "INFO",
                     "source": "serial_tracking", "detail": detail or {}})
    except Exception:  # noqa: BLE001
        return


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CaptureBody(BaseModel):
    serial: str = Field(..., description="Manufacturer serial of the physical unit")
    product_id: str
    store_id: str
    category: Optional[str] = Field(None, description="Product category (for the serialized-category gate)")
    grn_id: Optional[str] = None
    barcode: Optional[str] = None
    warranty_months: Optional[int] = None
    warranty_expiry_date: Optional[str] = None


class MarkSoldBody(BaseModel):
    order_id: str
    customer_id: Optional[str] = None


class RecallBody(BaseModel):
    reason: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/capture")
async def capture(body: CaptureBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    """STOCK-IN: mint a unique IN_STOCK serial for one physical unit. Inventory/
    manager only + store-scoped. A non-serialized category is a no-op (skipped)."""
    _require(current_user, _STOCK_ROLES, "capture a serial")
    validate_store_access(body.store_id, current_user)
    # A non-serialized category does not force a serial (no-op) -- fail soft so a
    # normal receive of a non-tracked item is never blocked.
    if body.category and not svc.is_serialized_category(body.category, _get_policy, body.store_id):
        return {"serialized": False, "skipped": True}
    try:
        unit = svc.capture_serial(
            _units(), serial=body.serial, product_id=body.product_id, store_id=body.store_id,
            grn_id=body.grn_id, barcode=body.barcode, warranty_months=body.warranty_months,
            warranty_expiry_date=body.warranty_expiry_date, captured_by=current_user.get("user_id"),
        )
    except svc.SerialError as exc:
        _raise(exc)
    _audit("serial.capture", entity_id=unit.get("serial"), actor=current_user,
           store_id=body.store_id, detail={"product_id": body.product_id})
    return unit


@router.post("/{serial}/mark-sold")
async def mark_sold(serial: str, body: MarkSoldBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    """AT-SALE: atomically transition a serial IN_STOCK -> SOLD (the double-sell
    guard). System/manager action -- a cashier cannot drive this directly."""
    _require(current_user, _RECALL_ROLES, "mark a serial sold")
    unit = svc.find_serial(_units(), serial, None)
    if unit is None:
        raise HTTPException(status_code=404, detail="serial not found")
    validate_store_access(unit.get("store_id"), current_user)
    updated = svc.mark_serial_sold(
        _units(), serial=serial, order_id=body.order_id,
        store_id=unit.get("store_id"), customer_id=body.customer_id,
    )
    if updated is None:
        # Not IN_STOCK -> already sold / recalled. The guard refused a double-sell.
        raise HTTPException(status_code=409, detail="serial is not in stock (already sold or recalled)")
    _audit("serial.sold", entity_id=updated.get("serial"), actor=current_user,
           store_id=unit.get("store_id"), detail={"order_id": body.order_id})
    return updated


@router.post("/{serial}/recall")
async def recall(serial: str, body: RecallBody, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Mark a serial RECALLED (from any pre-recall state). Manager/admin only."""
    _require(current_user, _RECALL_ROLES, "recall a serial")
    unit = svc.find_serial(_units(), serial, None)
    if unit is None:
        raise HTTPException(status_code=404, detail="serial not found")
    validate_store_access(unit.get("store_id"), current_user)
    try:
        updated = svc.transition_serial(_units(), serial=serial, to_status=svc.STATUS_RECALLED,
                                        store_id=unit.get("store_id"),
                                        actor=current_user.get("user_id"), reason=body.reason)
    except svc.SerialError as exc:
        _raise(exc)
    _audit("serial.recall", entity_id=serial, actor=current_user,
           store_id=unit.get("store_id"), detail={"reason": body.reason})
    return updated


@router.post("/{serial}/return")
async def return_unit(serial: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Return a SOLD serial back to stock (SOLD -> RETURNED). Inventory/manager only."""
    _require(current_user, _STOCK_ROLES, "return a serial")
    unit = svc.find_serial(_units(), serial, None)
    if unit is None:
        raise HTTPException(status_code=404, detail="serial not found")
    validate_store_access(unit.get("store_id"), current_user)
    try:
        updated = svc.transition_serial(_units(), serial=serial, to_status=svc.STATUS_RETURNED,
                                        store_id=unit.get("store_id"), actor=current_user.get("user_id"))
    except svc.SerialError as exc:
        _raise(exc)
    return updated


@router.get("/warranty/{serial}")
async def warranty(serial: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """Warranty / recall LOOKUP by serial: unit -> sale -> customer + window. Staff read."""
    _require(current_user, _READ_ROLES, "look up a warranty")
    db = _get_db()
    try:
        res = svc.lookup_warranty(db.get_collection("stock_units"), db.get_collection("orders"),
                                  db.get_collection("customers"), serial=serial)
    except svc.SerialError as exc:
        _raise(exc)
    validate_store_access((res.get("unit") or {}).get("store_id"), current_user)
    return res


@router.get("/{serial}")
async def get_serial(serial: str, current_user: Dict[str, Any] = Depends(get_current_user)):
    """One serialized stock_unit. Staff read + store-scoped."""
    _require(current_user, _READ_ROLES, "view a serial")
    unit = svc.find_serial(_units(), serial, None)
    if unit is None:
        raise HTTPException(status_code=404, detail="serial not found")
    validate_store_access(unit.get("store_id"), current_user)
    return unit
