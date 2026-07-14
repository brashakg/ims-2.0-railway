"""
IMS 2.0 - Shopify FULFILLMENT reconcile  (BVI-retirement phase 0)
==================================================================
Handles Shopify `fulfillments/create` and `fulfillments/update` webhooks: mark
the matching IMS online order SHIPPED and stamp its tracking (AWB / carrier /
URL / shipment status). BVI reconciled fulfilment today; when BVI is retired IMS
must, or an online order that has actually shipped would stay CONFIRMED forever.

CONTRACT:
  * Match the IMS order by shopify_order_id. NOT found -> log + no-op (fail-soft;
    never crash the NEXUS drain loop).
  * $set the tracking fields + advance the fulfilment/lifecycle status. Because we
    only $set (never increment), a re-delivered webhook is naturally idempotent.
  * NEVER regress a terminal status: a DELIVERED / CANCELLED / REFUNDED order is
    not knocked back to SHIPPED by a late fulfilment event.
  * HISTORICAL import orders (bvi_import) are skipped (settled outside IMS books).

PUBLIC API:
    reconcile_fulfillment(db, payload, *, topic=None) -> dict
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Shopify fulfillment.status -> canonical IMS fulfillment_status.
# (Shopify: pending | open | success | cancelled | error | failure.)
_FULFILLMENT_STATUS_MAP = {
    "success": "FULFILLED",
    "open": "PARTIAL",
    "pending": "PARTIAL",
    "cancelled": "CANCELLED",
    "error": "ERROR",
    "failure": "ERROR",
}

# Shopify shipment_status values that mean the parcel reached the buyer.
_DELIVERED_SHIPMENT = {"delivered"}

# IMS lifecycle statuses we must never regress a shipped/delivered event over.
_TERMINAL_STATUSES = {"DELIVERED", "CANCELLED", "REFUNDED", "VOID", "VOIDED"}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _find_ims_order(db, shopify_order_id: str) -> Optional[Dict[str, Any]]:
    if db is None or not shopify_order_id:
        return None
    try:
        coll = db.get_collection("orders")
        if coll is None:
            return None
        return coll.find_one({"shopify_order_id": shopify_order_id})
    except Exception:  # noqa: BLE001
        logger.debug("[SHOPIFY_FULFILL] order lookup failed", exc_info=True)
        return None


def reconcile_fulfillment(
    db, payload: Dict[str, Any], *, topic: Optional[str] = None
) -> Dict[str, Any]:
    """Reconcile a Shopify Fulfillment onto the matching IMS online order.

    Returns a structured result; NEVER raises."""
    try:
        payload = payload if isinstance(payload, dict) else {}
        shopify_order_id = _norm(payload.get("order_id"))
        fulfillment_id = _norm(payload.get("id"))
        if not shopify_order_id:
            return {"status": "skipped", "reason": "no_order_id"}
        if db is None:
            return {"status": "simulated", "fulfillment_id": fulfillment_id}

        order = _find_ims_order(db, shopify_order_id)
        if not order:
            # Fail-soft: a fulfilment for an order we never ingested. Log, no crash.
            logger.info(
                "[SHOPIFY_FULFILL] no IMS order for shopify_order_id=%s "
                "(fulfilment=%s) -- ignored",
                shopify_order_id,
                fulfillment_id,
            )
            return {
                "status": "order_not_found",
                "shopify_order_id": shopify_order_id,
                "fulfillment_id": fulfillment_id,
            }

        if order.get("historical") or order.get("source") == "bvi_import":
            return {"status": "skipped", "reason": "historical_import_order"}

        ful_status = _FULFILLMENT_STATUS_MAP.get(
            _norm(payload.get("status")).lower(), "FULFILLED"
        )
        shipment_status = _norm(payload.get("shipment_status")).lower()
        tracking_number = (
            _norm(payload.get("tracking_number"))
            or _norm((payload.get("tracking_numbers") or [None])[0])
        )
        tracking_company = _norm(payload.get("tracking_company"))
        tracking_url = (
            _norm(payload.get("tracking_url"))
            or _norm((payload.get("tracking_urls") or [None])[0])
        )

        now = datetime.now(timezone.utc).isoformat()
        update: Dict[str, Any] = {
            "fulfillment_status": ful_status,
            "updated_at": now,
            "shopify_fulfillment_id": fulfillment_id,
        }
        if tracking_number:
            update["awb"] = tracking_number
            update["tracking_number"] = tracking_number
        if tracking_company:
            update["tracking_company"] = tracking_company
        if tracking_url:
            update["tracking_url"] = tracking_url
        if shipment_status:
            update["shipment_status"] = shipment_status

        # Advance the lifecycle status -- but NEVER regress a terminal one.
        current_status = _norm(order.get("status")).upper()
        if current_status not in _TERMINAL_STATUSES:
            if shipment_status in _DELIVERED_SHIPMENT:
                update["status"] = "DELIVERED"
            elif ful_status == "FULFILLED" or tracking_number:
                update["status"] = "SHIPPED"

        try:
            coll = db.get_collection("orders")
            coll.update_one({"shopify_order_id": shopify_order_id}, {"$set": update})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHOPIFY_FULFILL] order update failed: %s", exc)
            return {"status": "error", "error": str(exc)}

        logger.info(
            "[SHOPIFY_FULFILL] order=%s fulfilment=%s -> fulfillment_status=%s "
            "status=%s awb=%s",
            order.get("order_id"),
            fulfillment_id,
            ful_status,
            update.get("status", current_status),
            tracking_number or "-",
        )
        return {
            "status": "reconciled",
            "shopify_order_id": shopify_order_id,
            "order_id": order.get("order_id"),
            "fulfillment_id": fulfillment_id,
            "fulfillment_status": ful_status,
            "order_status": update.get("status", current_status),
            "awb": tracking_number,
        }
    except Exception as exc:  # noqa: BLE001 -- the drain loop must never die here
        logger.warning("[SHOPIFY_FULFILL] reconcile_fulfillment failed soft: %s", exc)
        return {"status": "skipped", "reason": f"exception:{type(exc).__name__}"}
