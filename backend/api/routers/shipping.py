"""
IMS 2.0 - Shipping (Shiprocket) Router
======================================
Book + track customer shipments via Shiprocket (India's logistics aggregator)
for orders that ship to the customer.

Endpoints (prefix /api/v1/shipping):
  POST /shipments                       - book a shipment for an order_id. Records
                                          a shipment doc and returns awb/label/status.
                                          SIMULATED (no network) unless DISPATCH_MODE
                                          == 'live' AND Shiprocket creds are set.
  GET  /shipments?order_id=&store_id=   - list shipments (store-scoped).
  GET  /shipments/{id}/track            - live track via Shiprocket, falling back
                                          to the last-known status on the doc.

Everything FAILS SOFT: no DB / no creds / wrong mode never 500s. The actual
booking is gated exactly like MEGAPHONE/NEXUS sends - see services/shiprocket.py.

Conventions mirrored from returns.py:
  - get_current_user / require_roles for auth + RBAC (writes gated).
  - _get_db() raw-handle pattern; fail-soft when the DB is absent.
  - persists to a `shipments` collection; list is store-scoped for non-HQ roles.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import get_customer_repository, get_order_repository
from ..services import shiprocket

logger = logging.getLogger(__name__)
router = APIRouter()

# Roles allowed to BOOK a shipment - fulfilment-capable roles, mirrors the
# returns router gate (SUPERADMIN always passes via require_roles).
# SALES_CASHIER merged into SALES_STAFF (backlog #12): granted SALES_CASHIER but
# not SALES_STAFF, so the access moves to the survivor.
_FULFILMENT_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "CASHIER",
    "SALES_STAFF",
)

_HQ_ROLES = ("SUPERADMIN", "ADMIN", "AREA_MANAGER")


# ============================================================================
# SCHEMAS
# ============================================================================


class ShipAddress(BaseModel):
    """Ship-to override fields. Anything omitted is filled from the customer doc
    / order at book time. Pincode + address are what Shiprocket actually needs."""

    name: Optional[str] = None
    last_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    country: Optional[str] = "India"
    phone: Optional[str] = None
    email: Optional[str] = None
    payment_method: Optional[str] = None  # Prepaid | COD
    # Parcel dims (optional; service defaults to a small optical parcel)
    weight: Optional[float] = Field(None, ge=0)
    length: Optional[float] = Field(None, ge=0)
    breadth: Optional[float] = Field(None, ge=0)
    height: Optional[float] = Field(None, ge=0)


class ShipmentCreate(BaseModel):
    order_id: str
    store_id: Optional[str] = None
    pickup_location: Optional[str] = None
    address: ShipAddress = Field(default_factory=ShipAddress)


# ============================================================================
# DB HELPERS (fail-soft - no DB must never 500)
# ============================================================================


def _get_db():
    """Raw MongoDB handle, or None when unavailable (mock / no-DB mode)."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _shipments_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("shipments")
    except Exception:  # noqa: BLE001
        return None


def generate_shipment_id() -> str:
    """Short, human-friendly shipment id, e.g. SHP-250523-AB12CD."""
    stamp = datetime.now().strftime("%y%m%d")
    return f"SHP-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def _resolve_order(order_id: str) -> Optional[Dict[str, Any]]:
    """Look up the IMS order by id (or order_number). Fail-soft -> None."""
    repo = get_order_repository()
    if repo is None:
        return None
    try:
        found = repo.find_by_id(order_id)
        if found:
            return found
        finder = getattr(repo, "find_by_order_number", None)
        if callable(finder):
            return finder(order_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHIPROCKET] order lookup failed: %s", exc)
    return None


def _merge_address(req: ShipAddress, order: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the ship-to address dict: request fields win, then the customer doc,
    then the order. Always returns a dict (never raises)."""
    data: Dict[str, Any] = req.model_dump(exclude_none=True)

    customer_doc: Optional[Dict[str, Any]] = None
    cust_id = (order or {}).get("customer_id")
    if cust_id:
        repo = get_customer_repository()
        if repo is not None:
            try:
                customer_doc = repo.find_by_id(cust_id)
            except Exception:  # noqa: BLE001
                customer_doc = None

    if customer_doc:
        data.setdefault("name", customer_doc.get("name"))
        data.setdefault(
            "phone", customer_doc.get("phone") or customer_doc.get("mobile")
        )
        data.setdefault("email", customer_doc.get("email"))
        data.setdefault("address", customer_doc.get("address"))
        data.setdefault("city", customer_doc.get("city"))
        data.setdefault("state", customer_doc.get("state"))
        data.setdefault("pincode", customer_doc.get("pincode"))

    if order:
        data.setdefault("name", order.get("customer_name"))
        data.setdefault("phone", order.get("customer_phone"))

    return data


def _shipment_to_response(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Drop Mongo's _id; otherwise return the shipment doc as-is."""
    out = dict(doc)
    out.pop("_id", None)
    return out


def _write_fulfillment_push_audit(result: Dict[str, Any], current_user: dict) -> None:
    """Chained ONLINE_STORE_PUSH audit row for a fulfilment push-back attempt
    (live OR dry-run), mirroring online_store_push._write_audit. Fail-soft: an
    audit error can NEVER undo/block the booking."""
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is None:
            return
        ok = result.get("ok")
        audit.create(
            {
                "action": "ONLINE_STORE_PUSH",
                "entity_type": result.get("entity"),
                "entity_id": result.get("target_id"),
                "user_id": (current_user or {}).get("user_id"),
                "severity": "INFO" if ok else "WARNING",
                "details": {
                    "mode": result.get("mode"),
                    "push_action": result.get("action"),
                    "ok": ok,
                    "shopify_id": result.get("shopify_id"),
                    "error": result.get("error"),
                    "reason": result.get("reason"),
                },
            }
        )
    except Exception:  # noqa: BLE001 -- audit must never break the booking
        pass


async def _maybe_push_online_fulfillment(
    db, order: Dict[str, Any], ship_result, current_user: dict
) -> None:
    """FIRE-AND-FORGET: when an ONLINE (Shopify) order is dispatched via
    Shiprocket, push the fulfilment + tracking to Shopify so the buyer gets
    Shopify's shipping notification and the order shows Fulfilled.

    Fail-soft + gated: the push is SIMULATED unless the Shopify writer gates are
    armed, and it NEVER raises -- ANY error here is swallowed so it can never
    block the booking that triggered it. A non-online order (or a FAILED booking
    with no AWB) is a clean silent no-op."""
    try:
        if not order or not order.get("shopify_order_id"):
            return
        if str(order.get("source") or "").lower() != "shopify":
            return
        # A failed booking never really dispatched -- do not tell Shopify it did.
        if getattr(ship_result, "status", None) == "FAILED":
            return
        from ..services.shopify_fulfillment_push import push_fulfillment

        tracking = {
            "number": getattr(ship_result, "awb", None),
            "company": getattr(ship_result, "courier", None),
            "url": getattr(ship_result, "tracking_url", None),
        }
        result = await push_fulfillment(db, order, tracking=tracking)
        _write_fulfillment_push_audit(result.to_dict(), current_user)
    except Exception as exc:  # noqa: BLE001 -- must never block the booking
        logger.warning(
            "[SHIPROCKET] online fulfilment push-back failed soft: %s", exc
        )


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.post("/shipments", status_code=201)
async def book_shipment(
    body: ShipmentCreate = Body(...),
    current_user: dict = Depends(require_roles(*_FULFILMENT_ROLES)),
):
    """Book a Shiprocket shipment for an order.

    Returns the new shipment_id + awb/label/status. When DISPATCH_MODE != 'live'
    (or creds unset) the booking is SIMULATED - a fake AWB is recorded and no
    network call is made - so the flow is always exercisable safely.
    """
    order = _resolve_order(body.order_id)
    if order is None:
        # Allow booking even if the order can't be loaded (mock/no-DB), but warn.
        logger.info(
            "[SHIPROCKET] order %s not found - booking with request data only",
            body.order_id,
        )
        order = {"order_id": body.order_id}

    store_id = (
        body.store_id or order.get("store_id") or current_user.get("active_store_id")
    )
    address = _merge_address(body.address, order)

    db = _get_db()
    result = await shiprocket.create_shipment(
        order, address, db=db, pickup_location=body.pickup_location
    )

    shipment_id = generate_shipment_id()
    now = datetime.now().isoformat()
    simulated = result.status == "SIMULATED"

    doc: Dict[str, Any] = {
        "shipment_id": shipment_id,
        "order_id": order.get("order_id") or body.order_id,
        "order_number": order.get("order_number"),
        "customer_id": order.get("customer_id"),
        "customer_name": address.get("name") or order.get("customer_name"),
        "store_id": store_id,
        "provider": "shiprocket",
        "awb": result.awb,
        "courier": result.courier,
        "label_url": result.label_url,
        "sr_order_id": result.sr_order_id,
        "sr_shipment_id": result.shipment_id,
        "tracking_status": result.tracking_status,
        "tracking_url": result.tracking_url,
        "status": result.status,  # BOOKED | SIMULATED | FAILED
        "simulated": simulated,
        "ship_to": {
            "address": address.get("address"),
            "city": address.get("city"),
            "state": address.get("state"),
            "pincode": address.get("pincode"),
            "phone": address.get("phone"),
        },
        "error": result.error,
        "created_by": current_user.get("user_id"),
        "created_at": now,
        "updated_at": now,
    }

    coll = _shipments_coll()
    if coll is not None:
        try:
            coll.insert_one(dict(doc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHIPROCKET] persist failed: %s", exc)

    # Close the order loop: an ONLINE (Shopify) order just dispatched via
    # Shiprocket must be told to Shopify (fulfilment + tracking) so the buyer
    # gets Shopify's shipping mail and the order shows Fulfilled. Fire-and-forget
    # + fail-soft -- gated like every other Shopify write and it never raises, so
    # it can never block this booking. A non-online order is a silent no-op.
    await _maybe_push_online_fulfillment(db, order, result, current_user)

    if result.status == "FAILED":
        # Booking attempted live but failed - record kept, surface a soft message.
        message = f"Shipment booking failed: {result.error}"
    elif simulated:
        message = "Shipment simulated (not dispatched live) - " + (
            result.error or "DISPATCH_MODE not live"
        )
    else:
        message = "Shipment booked"

    return {
        "shipment_id": shipment_id,
        "order_id": doc["order_id"],
        "status": result.status,
        "simulated": simulated,
        "awb": result.awb,
        "courier": result.courier,
        "label_url": result.label_url,
        "tracking_status": result.tracking_status,
        "tracking_url": result.tracking_url,
        "message": message,
    }


@router.get("/shipments")
async def list_shipments(
    order_id: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """List shipments, store-scoped, newest first.

    HQ roles (SUPERADMIN/ADMIN/AREA_MANAGER) may pass any store_id; lower roles
    are pinned to their active store. Optional order_id filter.
    """
    coll = _shipments_coll()
    if coll is None:
        return {"shipments": [], "total": 0}

    roles = current_user.get("roles", []) or []
    is_hq = any(r in roles for r in _HQ_ROLES)
    effective_store = store_id if is_hq else current_user.get("active_store_id")

    query: Dict[str, Any] = {}
    if order_id:
        query["order_id"] = order_id
    if effective_store:
        query["store_id"] = effective_store

    try:
        total = coll.count_documents(query)
        cursor = (
            coll.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
        )
        shipments: List[Dict[str, Any]] = [_shipment_to_response(d) for d in cursor]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SHIPROCKET] list failed: %s", exc)
        return {"shipments": [], "total": 0}

    return {"shipments": shipments, "total": total}


@router.get("/shipments/{shipment_id}/track")
async def track_shipment(
    shipment_id: str = Path(..., description="IMS shipment id"),
    current_user: dict = Depends(get_current_user),
):
    """Live-track a shipment via Shiprocket; fall back to the last-known status.

    Never 500s: missing creds / no DB / track failure all degrade to the
    persisted last-known status on the shipment doc.
    """
    coll = _shipments_coll()
    doc: Optional[Dict[str, Any]] = None
    if coll is not None:
        try:
            doc = coll.find_one({"shipment_id": shipment_id}, {"_id": 0})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHIPROCKET] track lookup failed: %s", exc)
    if not doc:
        raise HTTPException(status_code=404, detail="Shipment not found")

    awb = doc.get("awb")
    sr_shipment_id = doc.get("sr_shipment_id")

    db = _get_db()
    result = await shiprocket.track(awb=awb, shipment_id=sr_shipment_id, db=db)

    # Update last-known status when we got a real (non-simulated) tracking value.
    if result.ok and result.status == "TRACKED" and result.tracking_status:
        new_status = result.tracking_status
        if coll is not None and new_status != doc.get("tracking_status"):
            try:
                coll.update_one(
                    {"shipment_id": shipment_id},
                    {
                        "$set": {
                            "tracking_status": new_status,
                            "tracking_url": result.tracking_url
                            or doc.get("tracking_url"),
                            "courier": result.courier or doc.get("courier"),
                            "updated_at": datetime.now().isoformat(),
                        }
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SHIPROCKET] track update failed: %s", exc)
        doc["tracking_status"] = new_status

    live = result.status == "TRACKED"
    return {
        "shipment_id": shipment_id,
        "order_id": doc.get("order_id"),
        "awb": awb,
        "courier": result.courier or doc.get("courier"),
        "tracking_status": result.tracking_status or doc.get("tracking_status"),
        "tracking_url": result.tracking_url or doc.get("tracking_url"),
        "live": live,
        "source": "shiprocket" if live else "last_known",
        "message": None if live else (result.error or "Showing last-known status"),
    }
