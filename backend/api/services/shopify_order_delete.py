"""
IMS 2.0 - Shopify ORDER DELETE -> VOID the IMS online order  (BVI-retirement)
=============================================================================
Handles the Shopify `orders/delete` webhook: when a merchant deletes an order in
Shopify, the matching IMS online order must be VOIDED (a soft, reversible status
change) rather than silently left as a live/confirmed sale. BVI absorbed order
deletions today; when BVI is retired IMS must, or a deleted Shopify order would
keep counting as IMS revenue / an open fulfilment forever.

The status move is the ONE transition table's (online_order_status, fact
DELETE): an open order (CONFIRMED / PROCESSING / READY / SHIPPED) is VOIDED; a
DELIVERED one stays DELIVERED with ONE task for a person (owner ruling
2026-09-28); a finished one (CANCELLED / REFUNDED) stays as it is -- finance
leaves VOID in revenue, so voiding a cancelled order would count it again.

CONTRACT (mirrors shopify_fulfillment.reconcile_fulfillment exactly):
  * Match the IMS order by shopify_order_id (the `orders/delete` payload is just
    {"id": <order_id>}). NOT found -> log + no-op (fail-soft; never crash the
    NEXUS drain loop).
  * NEVER hard-delete. shopify_deleted_at rides the table's VOID claim (with
    the prior lifecycle status in status_before_void for the audit trail), or
    lands alone when the table keeps the status. Because we only $set a
    marker, a re-delivered webhook is naturally IDEMPOTENT: once
    shopify_deleted_at is present we return "duplicate" and touch nothing; a
    write that failed left no marker, so the re-delivery retries it.
  * HISTORICAL import orders (bvi_import) are skipped -- they were settled outside
    IMS books and must never be flipped.

PUBLIC API:
    handle_shopify_order_delete(db, payload, *, topic=None) -> dict
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .shopify_delete_shape import (
    KIND_ORDER,
    delete_payload_refusal,
    unexpected_delete_keys,
)

logger = logging.getLogger(__name__)

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
      {"status": "voided"|"kept"|"duplicate"|"order_not_found"|"skipped"|
                 "simulated"|"error", "shopify_order_id": <str>, ...}
    "kept": the table kept the status (DELIVERED -> conflict_task, or a
    finished order -> terminal_withheld).
    """
    try:
        payload = payload if isinstance(payload, dict) else {}

        # SHAPE GUARD -- the SAME classifier shopify_customer_delete uses, so the
        # destructive pair can never drift apart. The Shopify topic comes from the
        # UNSIGNED X-Shopify-Topic header, so a captured, validly-signed
        # orders/create body -- whose top-level `id` IS a live order id -- can be
        # replayed as orders/delete and would void that live order. A genuine
        # orders/delete body is just {"id": <order_id>}; anything carrying order
        # CONTENT is a different resource wearing a delete label. We refuse loudly
        # (a deleted Shopify order left live in IMS is visible and recoverable; a
        # silently voided live order is not).
        refusal = delete_payload_refusal(payload, kind=KIND_ORDER)
        if refusal:
            logger.warning(
                "[SHOPIFY_ORDER_DELETE] refusing to void on a non-delete payload: "
                "reason=%s id=%s unexpected_keys=%s",
                refusal,
                payload.get("id"),
                ",".join(unexpected_delete_keys(payload, kind=KIND_ORDER)) or "-",
            )
            return {
                "status": "skipped",
                "reason": refusal,
                "shopify_order_id": _norm(payload.get("id")),
            }

        # The orders/delete payload is just {"id": <order_id>}. The old
        # `or payload.get("order_id")` fallback is GONE, deliberately: a top-level
        # order_id means this is a CHILD resource (refund / fulfillment), which the
        # guard above now refuses outright -- so the fallback could only ever have
        # voided a real order off a child payload's parent pointer.
        shopify_order_id = _norm(payload.get("id"))
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

        from .online_order_status import DELETE, apply_fact

        extra: Dict[str, Any] = {"void_reason": "Shopify orders/delete webhook"}
        # Preserve the lifecycle status the order held before the delete so the
        # void is auditable / reversible (we never overwrite an existing snapshot).
        if prior_status and not order.get("status_before_void"):
            extra["status_before_void"] = prior_status
        # The marker rides the status claim (or lands alone when the table
        # keeps the status): a failed write leaves no marker, so a re-delivered
        # orders/delete retries instead of returning "duplicate".
        res = apply_fact(
            db, order, DELETE, source="SHOPIFY_ORDER_DELETE", extra=extra,
            marks={"shopify_deleted_at": now, "updated_at": now},
        )
        if res["failed"]:
            return {"status": "error", "error": "status write failed",
                    "shopify_order_id": shopify_order_id, "order_id": order.get("order_id")}

        logger.info(
            "[SHOPIFY_ORDER_DELETE] order=%s shopify_order=%s -> %s (was status=%s)",
            order.get("order_id"),
            shopify_order_id,
            res["to"] or f"kept ({res['why'] or 'no move'})",
            prior_status or "-",
        )
        if res["to"] == "VOID":
            return {
                "status": "voided",
                "shopify_order_id": shopify_order_id,
                "order_id": order.get("order_id"),
                "status_before_void": prior_status,
            }
        return {
            "status": "kept",
            "shopify_order_id": shopify_order_id,
            "order_id": order.get("order_id"),
            "order_status": prior_status,
            "terminal_withheld": res["terminal_withheld"],
            "conflict_task": res["why"] == "conflict",
        }
    except Exception as exc:  # noqa: BLE001 -- the drain loop must never die here
        logger.warning(
            "[SHOPIFY_ORDER_DELETE] handle_shopify_order_delete failed soft: %s", exc
        )
        return {"status": "skipped", "reason": f"exception:{type(exc).__name__}"}
