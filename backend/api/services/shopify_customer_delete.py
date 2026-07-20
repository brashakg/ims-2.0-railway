"""
IMS 2.0 - Shopify CUSTOMER DELETE -> data-erasure flag  (BVI-retirement)
=========================================================================
Handles the Shopify `customers/delete` webhook (a merchant deleted the customer,
or a GDPR/DPDP erasure request). IMS marks the matching customer as erasure-
requested so the buyer's data can be scrubbed by the downstream erasure job --
WITHOUT hard-deleting the record or the PII-linked sales/GST history, which IMS
is legally required to retain for tax + returns.

CONTRACT:
  * Match the IMS customer by shopify_customer_id (the payload is essentially
    {"id": <customer_id>, ...}). NOT found -> log + no-op (nothing to erase;
    fail-soft, never crash the NEXUS drain loop).
  * $set shopify_erasure_requested=True + shopify_erasure_requested_at. We do NOT
    null out PII here and NEVER delete the customer doc or its order history --
    the flag records erasure INTENT for the scrub job / the compliance surface.
  * IDEMPOTENT: once the flag is set a re-delivered webhook returns "duplicate"
    and touches nothing (only $set of a marker; no destructive step).

PUBLIC API:
    handle_shopify_customer_delete(db, payload, *, topic=None) -> dict
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _find_customer_by_shopify_id(db, shopify_customer_id: str) -> Optional[Dict[str, Any]]:
    """The IMS customer previously synced from this Shopify customer
    (shopify_customer_sync stamps shopify_customer_id). Fail-soft -> None."""
    if db is None or not shopify_customer_id:
        return None
    try:
        coll = db.get_collection("customers")
        if coll is None:
            return None
        return coll.find_one({"shopify_customer_id": shopify_customer_id})
    except Exception:  # noqa: BLE001
        logger.debug("[SHOPIFY_CUSTOMER_DELETE] customer lookup failed", exc_info=True)
        return None


def handle_shopify_customer_delete(
    db, payload: Dict[str, Any], *, topic: Optional[str] = None
) -> Dict[str, Any]:
    """Flag the IMS customer mirroring a deleted Shopify customer for data
    erasure. NEVER hard-deletes PII-linked history. Idempotent on the
    shopify_erasure_requested flag. NEVER raises.

    Returns a structured result dict:
      {"status": "erasure_flagged"|"duplicate"|"customer_not_found"|"skipped"|
                 "simulated"|"error", "shopify_customer_id": <str>, ...}
    """
    try:
        payload = payload if isinstance(payload, dict) else {}
        shopify_customer_id = _norm(payload.get("id")) or _norm(
            payload.get("customer_id")
        )
        if not shopify_customer_id:
            return {"status": "skipped", "reason": "no_customer_id"}
        if db is None:
            return {"status": "simulated", "shopify_customer_id": shopify_customer_id}

        customer = _find_customer_by_shopify_id(db, shopify_customer_id)
        if not customer:
            # Fail-soft: an erasure request for a customer we never synced. There
            # is nothing to flag -- log, no crash.
            logger.info(
                "[SHOPIFY_CUSTOMER_DELETE] no IMS customer for shopify_customer_id="
                "%s -- ignored",
                shopify_customer_id,
            )
            return {
                "status": "customer_not_found",
                "shopify_customer_id": shopify_customer_id,
            }

        # IDEMPOTENCY: a re-delivered customers/delete must not re-flag / re-write.
        if customer.get("shopify_erasure_requested"):
            return {
                "status": "duplicate",
                "shopify_customer_id": shopify_customer_id,
                "customer_id": customer.get("customer_id"),
            }

        now = datetime.now(timezone.utc).isoformat()
        update = {
            "shopify_erasure_requested": True,
            "shopify_erasure_requested_at": now,
            "updated_at": now,
        }
        try:
            coll = db.get_collection("customers")
            coll.update_one(
                {"shopify_customer_id": shopify_customer_id}, {"$set": update}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SHOPIFY_CUSTOMER_DELETE] customer update failed: %s", exc)
            return {"status": "error", "error": str(exc)}

        logger.info(
            "[SHOPIFY_CUSTOMER_DELETE] customer=%s shopify_id=%s flagged for data "
            "erasure (PII-linked history retained)",
            customer.get("customer_id"),
            shopify_customer_id,
        )
        return {
            "status": "erasure_flagged",
            "shopify_customer_id": shopify_customer_id,
            "customer_id": customer.get("customer_id"),
        }
    except Exception as exc:  # noqa: BLE001 -- the drain loop must never die here
        logger.warning(
            "[SHOPIFY_CUSTOMER_DELETE] handle_shopify_customer_delete failed soft: %s",
            exc,
        )
        return {"status": "skipped", "reason": f"exception:{type(exc).__name__}"}
