"""
IMS 2.0 - Shopify FULFILLMENT reconcile  (BVI-retirement phase 0)
==================================================================
Handles Shopify `fulfillments/create` and `fulfillments/update` webhooks: stamp
the matching IMS online order's tracking (AWB / carrier / URL / shipment status)
and hand the fulfilment's ONE lifecycle fact to the transition table
(online_order_status). BVI reconciled fulfilment today; when BVI is retired IMS
must, or an online order that has actually shipped would stay CONFIRMED forever.

CONTRACT:
  * Match the IMS order by shopify_order_id. NOT found -> log + no-op (fail-soft;
    never crash the NEXUS drain loop).
  * $set the tracking fields + fulfillment_status. Because we only $set (never
    increment), a re-delivered webhook is naturally idempotent.
  * The lifecycle status is the table's (online_order_status.apply_fact): a
    fulfilment states SHIP, or DELIVER when shipment_status is "delivered"
    (owner ruling 2026-09-28: DELIVERED only from the courier), or nothing (a
    cancelled / failed fulfilment). The table never regresses a status, keeps
    a finished one, and withholds SHIPPED / DELIVERED from an order on an active
    Rx / stock hold (owner decision 2026-06-30), re-raising the Rx task --
    tracking / fulfillment_status / shipment_status still land.
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


def _norm(value: Any) -> str:
    return str(value or "").strip()


# The fulfilment's own out-of-order watermark (the payload's updated_at, naive
# UTC) -- the order-level one (shopify_updated_at) is the mapper's, and an order
# body's updated_at is a different clock from a fulfilment's.
FULFILLMENT_WATERMARK = "shopify_fulfillment_updated_at"


def _tracking_fields(payload: Dict[str, Any]) -> Dict[str, str]:
    """The tracking fields a fulfilment payload $sets on the IMS order -- only
    the NON-EMPTY ones: an empty one never clears what an older fulfilment
    wrote. ONE extraction for the reconcile and for the hourly pull sweep's
    "did the fulfilment move" (nexus_providers._fulfilment_moved)."""
    tracking_number = _norm(payload.get("tracking_number")) or _norm(
        (payload.get("tracking_numbers") or [None])[0]
    )
    fields = {
        "awb": tracking_number,
        "tracking_number": tracking_number,
        "tracking_company": _norm(payload.get("tracking_company")),
        "tracking_url": _norm(payload.get("tracking_url"))
        or _norm((payload.get("tracking_urls") or [None])[0]),
        "shipment_status": _norm(payload.get("shipment_status")).lower(),
    }
    return {k: v for k, v in fields.items() if v}


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

        from .online_order_mapper import _shopify_payload_stale
        from .online_order_status import apply_fact, fulfilment_fact
        from .shopify_ingest import _to_naive_utc

        # OUT-OF-ORDER guard: the mapper's stale rule on the fulfilment's own
        # clock. A fulfilment payload STRICTLY older than the one last applied
        # (a retried create landing after a newer update, or the hourly pull's
        # body losing the race with a fulfillments/update) never rewinds the
        # tracking / shipment status. Fail-open without a stamp on either side.
        if _shopify_payload_stale(order, payload, field=FULFILLMENT_WATERMARK):
            return {"status": "skipped", "reason": "stale_fulfillment"}

        ful_status = _FULFILLMENT_STATUS_MAP.get(
            _norm(payload.get("status")).lower(), "FULFILLED"
        )
        tracking = _tracking_fields(payload)
        shipment_status = tracking.get("shipment_status", "")
        tracking_number = tracking.get("tracking_number", "")

        now = datetime.now(timezone.utc).isoformat()
        update: Dict[str, Any] = {
            "fulfillment_status": ful_status,
            "updated_at": now,
            "shopify_fulfillment_id": fulfillment_id,
            **tracking,
        }
        watermark = _to_naive_utc(payload.get("updated_at"))
        if watermark is not None:
            update[FULFILLMENT_WATERMARK] = watermark

        try:
            coll = db.get_collection("orders")
            coll.update_one({"shopify_order_id": shopify_order_id}, {"$set": update})
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHOPIFY_FULFILL] order update failed: %s", exc)
            return {"status": "error", "error": str(exc)}

        # The lifecycle status: this fulfilment's ONE fact through the ONE
        # transition table (the mapper's and the courier's too).
        current_status = _norm(order.get("status")).upper()
        res = apply_fact(
            db,
            {**order, **update},
            fulfilment_fact(ful_status, shipment_status, tracking_number),
            source="SHOPIFY_FULFILL",
        )
        order_status = res["to"] or current_status

        logger.info(
            "[SHOPIFY_FULFILL] order=%s fulfilment=%s -> fulfillment_status=%s "
            "status=%s awb=%s",
            order.get("order_id"),
            fulfillment_id,
            ful_status,
            order_status,
            tracking_number or "-",
        )
        return {
            "status": "reconciled",
            "shopify_order_id": shopify_order_id,
            "order_id": order.get("order_id"),
            "fulfillment_id": fulfillment_id,
            "fulfillment_status": ful_status,
            "order_status": order_status,
            "awb": tracking_number,
            # The table kept a finished status (a fulfilment on an order IMS
            # holds DELIVERED / CANCELLED / ...): the pull sweep reports it as
            # status_skipped_terminal.
            "terminal_withheld": res["terminal_withheld"],
        }
    except Exception as exc:  # noqa: BLE001 -- the drain loop must never die here
        logger.warning("[SHOPIFY_FULFILL] reconcile_fulfillment failed soft: %s", exc)
        return {"status": "skipped", "reason": f"exception:{type(exc).__name__}"}
