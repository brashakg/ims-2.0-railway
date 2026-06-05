"""
IMS 2.0 - NPS Auto-trigger on Order Delivery
==============================================
CRM-9: Automatically fire an NPS survey when an order transitions to DELIVERED.

Contract: fail-soft always.  A survey failure must never block the delivery
confirmation response.  The caller (orders.deliver_order) wraps this in a
try/except so any exception here is swallowed silently after logging.

To avoid survey spam the trigger is skipped when:
  - The order has no customer (anonymous / walk-in without account).
  - A PENDING or RESPONDED NPS for the same order already exists.
  - The customer has opted out of marketing (marketing_consent == False).

The NPS is inserted into nps_responses with status SENT.  The actual
WhatsApp/SMS delivery uses the same send_notification path as the manual
POST /marketing/nps-survey/{order_id} endpoint (we call that logic directly
rather than duplicating it) -- so DISPATCH_MODE and DND gate apply.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)

_NPS_DEDUP_WINDOW_DAYS = 30  # Don't re-send NPS for the same customer within 30 days


def _get_db():
    try:
        from database.connection import get_db
        return get_db().db
    except Exception:
        return None


async def trigger_nps_on_delivery(
    order: Dict[str, Any],
    actor: Dict[str, Any],
) -> None:
    """Queue an NPS survey for `order` which has just been marked DELIVERED.

    Idempotent: skips if an NPS for this order already exists.  Also skips if
    the same customer received an NPS within _NPS_DEDUP_WINDOW_DAYS to avoid
    survey fatigue.

    Args:
        order:  The full order document (as read from the orders collection
                before the status update).
        actor:  The current-user dict of the staff member who delivered it.
    """
    db = _get_db()
    if db is None:
        logger.debug("[NPS_TRIGGER] No DB — skipping auto NPS for order %s", order.get("order_id"))
        return

    customer_id = order.get("customer_id", "")
    order_id = order.get("order_id", "")
    store_id = order.get("store_id", actor.get("active_store_id", ""))

    if not customer_id:
        logger.debug("[NPS_TRIGGER] No customer_id on order %s — skipping", order_id)
        return

    nps_coll = db.get_collection("nps_responses")

    # Idempotency: skip if an NPS for this order already exists.
    existing_order_nps = nps_coll.find_one({"order_id": order_id})
    if existing_order_nps:
        logger.debug("[NPS_TRIGGER] NPS already exists for order %s — skipping", order_id)
        return

    # Dedup: skip if a recent NPS for this customer was sent within the window.
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=_NPS_DEDUP_WINDOW_DAYS)).isoformat()
    recent = nps_coll.find_one({
        "customer_id": customer_id,
        "survey_sent_at": {"$gte": cutoff},
    })
    if recent:
        logger.debug(
            "[NPS_TRIGGER] Customer %s already received NPS within %d days — skipping",
            customer_id,
            _NPS_DEDUP_WINDOW_DAYS,
        )
        return

    # Consent gate: don't send to opted-out customers.
    customer = db.get_collection("customers").find_one({"customer_id": customer_id}) or {}
    if customer.get("marketing_consent") is False:
        logger.debug("[NPS_TRIGGER] Customer %s opted out — skipping NPS", customer_id)
        return

    # Build the NPS record.
    nps_id = f"NPS-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    nps_entry = {
        "nps_id": nps_id,
        "store_id": store_id,
        "customer_id": customer_id,
        "customer_name": customer.get("name", order.get("customer_name", "")),
        "order_id": order_id,
        "score": None,
        "feedback": None,
        "status": "SENT",
        "auto_triggered": True,
        "survey_sent_at": datetime.now().isoformat(),
        "responded_at": None,
        "created_at": datetime.now().isoformat(),
    }
    nps_coll.insert_one(nps_entry)
    logger.info("[NPS_TRIGGER] NPS %s queued for order %s customer %s", nps_id, order_id, customer_id)

    # Send the WhatsApp/SMS notification (fail-soft).
    try:
        store = db.get_collection("stores").find_one({"store_id": store_id}) or {}
        from ..services.notification_service import send_notification
        await send_notification(
            store_id=store_id,
            customer_id=customer_id,
            customer_phone=customer.get("mobile", ""),
            customer_name=customer.get("name", "Customer"),
            template_id="NPS_SURVEY",
            channel="WHATSAPP",
            variables={
                "store_name": store.get("name", "Better Vision"),
                "survey_link": f"https://bettervision.in/nps/{nps_id}",
            },
            category="SERVICE",
            triggered_by=actor.get("user_id", "system"),
            related_entity_type="order",
            related_entity_id=order_id,
        )
    except Exception as exc:
        # Notification failure is non-fatal — the NPS record is already inserted.
        logger.warning("[NPS_TRIGGER] WhatsApp send failed for NPS %s: %s", nps_id, exc)
