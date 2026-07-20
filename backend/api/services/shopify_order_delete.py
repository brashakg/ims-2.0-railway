"""
IMS 2.0 - Shopify ORDER DELETE -> VOID the IMS online order  (BVI-retirement)
=============================================================================
Handles the Shopify `orders/delete` webhook: when a merchant deletes an order in
Shopify, the matching IMS online order must be VOIDED (a soft, reversible status
change) rather than silently left as a live/confirmed sale. BVI absorbed order
deletions today; when BVI is retired IMS must, or a deleted Shopify order would
keep counting as IMS revenue / an open fulfilment forever.

CONTRACT (mirrors shopify_fulfillment.reconcile_fulfillment exactly):
  * Match the IMS order by shopify_order_id (the `orders/delete` payload is just
    {"id": <order_id>}). NOT found -> log + no-op (fail-soft; never crash the
    NEXUS drain loop).
  * NEVER hard-delete. We $set status=VOID + shopify_deleted_at (and keep the
    prior lifecycle status in status_before_void for the audit trail). Because we
    only $set a marker, a re-delivered webhook is naturally IDEMPOTENT: once
    shopify_deleted_at is present we return "duplicate" and touch nothing.
  * HISTORICAL import orders (bvi_import) are skipped -- they were settled outside
    IMS books and must never be flipped.

PUBLIC API:
    handle_shopify_order_delete(db, payload, *, topic=None) -> dict
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# The IMS lifecycle status a deleted Shopify order is parked in. VOID is already a
# recognised terminal status across the online path (see shopify_fulfillment
# _TERMINAL_STATUSES) so finance / fulfilment aggregations exclude it.
_VOID_STATUS = "VOID"


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _find_ims_order(db, shopify_order_id: str) -> Optional[Dict[str, Any]]:
    """The canonical IMS order minted for this Shopify order (shopify_ingest).
    Fail-soft -> None."""
    if db is None or not shopify_order_id:
        return None
    try:
        coll = db.get_collection("orders")
        if coll is None:
            return None
        return coll.find_one({"shopify_order_id": shopify_order_id})
    except Exception:  # noqa: BLE001
        logger.debug("[SHOPIFY_ORDER_DELETE] order lookup failed", exc_info=True)
        return None


def handle_shopify_order_delete(
    db, payload: Dict[str, Any], *, topic: Optional[str] = None
) -> Dict[str, Any]:
    """VOID the IMS online order that mirrors a deleted Shopify order.

    Idempotent on the shopify_deleted_at marker. NEVER raises (the NEXUS drain
    loop relies on this). Returns a structured result dict:
      {"status": "voided"|"duplicate"|"order_not_found"|"skipped"|"simulated"|
                 "error", "shopify_order_id": <str>, ...}
    """
    try:
        payload = payload if isinstance(payload, dict) else {}
        # The orders/delete payload is just {"id": <order_id>}; there is no
        # separate order_id field (that is a REFUND-payload shape). Fall back to
        # order_id defensively for any non-standard sender.
        shopify_order_id = _norm(payload.get("id")) or _norm(payload.get("order_id"))
        if not shopify_order_id:
            return {"status": "skipped", "reason": "no_order_id"}
        if db is None:
            return {"status": "simulated", "shopify_order_id": shopify_order_id}

        order = _find_ims_order(db, shopify_order_id)
        if not order:
            # Fail-soft: a delete for an order we never ingested. Log, no crash.
            logger.info(
                "[SHOPIFY_ORDER_DELETE] no IMS order for shopify_order_id=%s "
                "-- ignored",
                shopify_order_id,
            )
            return {
                "status": "order_not_found",
                "shopify_order_id": shopify_order_id,
            }

        # HISTORICAL import guard (mirrors online_order_mapper / shopify_refund): a
        # pre-IMS order imported for customer-360 history was settled OUTSIDE IMS
        # books; never flip its status.
        if order.get("historical") or order.get("source") == "bvi_import":
            logger.info(
                "[SHOPIFY_ORDER_DELETE] skip delete for HISTORICAL import order=%s",
                order.get("order_id"),
            )
            return {
                "status": "skipped",
                "reason": "historical_import_order",
                "shopify_order_id": shopify_order_id,
                "order_id": order.get("order_id"),
            }

        # IDEMPOTENCY: a re-delivered orders/delete must not re-void / re-write.
        # The shopify_deleted_at marker being present means we already handled it.
        if order.get("shopify_deleted_at"):
            return {
                "status": "duplicate",
                "shopify_order_id": shopify_order_id,
                "order_id": order.get("order_id"),
            }

        now = datetime.now(timezone.utc).isoformat()
        prior_status = _norm(order.get("status")).upper() or None
        update: Dict[str, Any] = {
            "status": _VOID_STATUS,
            "shopify_deleted_at": now,
            "void_reason": "Shopify orders/delete webhook",
            "updated_at": now,
        }
        # Preserve the lifecycle status the order held before the delete so the
        # void is auditable / reversible (we never overwrite an existing snapshot).
        if prior_status and not order.get("status_before_void"):
            update["status_before_void"] = prior_status

        try:
            coll = db.get_collection("orders")
            coll.update_one(
                {"shopify_order_id": shopify_order_id}, {"$set": update}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHOPIFY_ORDER_DELETE] order update failed: %s", exc)
            return {"status": "error", "error": str(exc)}

        logger.info(
            "[SHOPIFY_ORDER_DELETE] order=%s shopify_order=%s VOIDED "
            "(was status=%s)",
            order.get("order_id"),
            shopify_order_id,
            prior_status or "-",
        )
        return {
            "status": "voided",
            "shopify_order_id": shopify_order_id,
            "order_id": order.get("order_id"),
            "status_before_void": prior_status,
        }
    except Exception as exc:  # noqa: BLE001 -- the drain loop must never die here
        logger.warning(
            "[SHOPIFY_ORDER_DELETE] handle_shopify_order_delete failed soft: %s", exc
        )
        return {"status": "skipped", "reason": f"exception:{type(exc).__name__}"}
