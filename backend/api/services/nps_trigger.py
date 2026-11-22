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

The survey row is written as PENDING_SEND first and becomes SENT only once
its message is STORED in the queue (NOT_SENT when it was refused or not
saved). Readers (the NPS screen, the 30-day window) count a survey through
ONE rule, counted_surveys: PENDING_SEND counts only when its queued message
exists, so a failed status write can never leave a phantom "sent" survey
(Codex 1419 R2-B). Both doors -- this trigger and POST /marketing/nps-survey
-- send through send_survey, so DISPATCH_MODE and the DND gate apply.
"""

from __future__ import annotations

from api.utils.event_time import utc_now_iso

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_NPS_DEDUP_WINDOW_DAYS = 30  # Don't re-send NPS for the same customer within 30 days


def _get_db():
    try:
        from database.connection import get_db
        return get_db().db
    except Exception:
        return None


PENDING_SEND = "PENDING_SEND"


def _ref(nps_id: str) -> str:
    """The queued message's dedupe_ref (indexed): one message per survey row."""
    return f"nps:{nps_id}"


def counted_surveys(db, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """THE one rule for "this survey was sent": SENT / RESPONDED (and older
    rows) count, NOT_SENT never does, and PENDING_SEND counts only when its
    queued message is stored. RAISES on a read error (never a false count)."""
    pending = [r.get("nps_id") for r in rows if r.get("status") == PENDING_SEND]
    stored = set()
    if pending:
        stored = {
            d["dedupe_ref"]
            for d in db.get_collection("notification_logs").find(
                {"dedupe_ref": {"$type": "string", "$in": [_ref(i) for i in pending]}},
                {"_id": 0, "dedupe_ref": 1},
            )
        }
    return [
        r
        for r in rows
        if r.get("status") != "NOT_SENT"
        and (r.get("status") != PENDING_SEND or _ref(r.get("nps_id")) in stored)
    ]


async def send_survey(
    db,
    *,
    order: Dict[str, Any],
    customer: Dict[str, Any],
    store_id: str,
    triggered_by: str,
    auto: bool,
) -> Dict[str, Any]:
    """Write the survey row (PENDING_SEND), queue its message, then mark it
    SENT (stored) or NOT_SENT (refused / not saved). Returns the queue
    writer's verdict plus nps_id. The status marks are best effort: if one
    fails the row stays PENDING_SEND and counted_surveys reads the truth from
    the queue. The row insert itself RAISES (nothing was started)."""
    from .notification_service import not_sent_reason, send_notification

    customer_id = order.get("customer_id", "")
    order_id = order.get("order_id", "")
    nps_id = f"NPS-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    entry = {
        "nps_id": nps_id,
        "store_id": store_id,
        "customer_id": customer_id,
        "customer_name": customer.get("name", order.get("customer_name", "")),
        "order_id": order_id,
        "score": None,
        "feedback": None,
        "status": PENDING_SEND,
        "survey_sent_at": utc_now_iso(),
        "responded_at": None,
        "created_at": utc_now_iso(),
    }
    if auto:
        entry["auto_triggered"] = True
    coll = db.get_collection("nps_responses")
    coll.insert_one(entry)

    try:
        store = db.get_collection("stores").find_one({"store_id": store_id}) or {}
        res = await send_notification(
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
            triggered_by=triggered_by,
            related_entity_type="order",
            related_entity_id=order_id,
            extra={"dedupe_ref": _ref(nps_id)},
        )
    except Exception as exc:  # noqa: BLE001 - told as "not sent"
        logger.warning("[NPS] survey %s: queueing failed: %s", nps_id, exc)
        res = {}
    res = dict(res) if isinstance(res, dict) else {}
    why = not_sent_reason(res)
    update = (
        {"status": "SENT"}
        if why is None
        else {"status": "NOT_SENT", "not_sent_reason": res.get("refused_reason") or "not_saved"}
    )
    try:
        coll.update_one({"nps_id": nps_id, "status": PENDING_SEND}, {"$set": update})
    except Exception as exc:  # noqa: BLE001 - PENDING_SEND is read via the queue
        logger.warning("[NPS] could not mark %s %s: %s", nps_id, update["status"], exc)
    res["nps_id"] = nps_id
    return res


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
    # survey_sent_at is stored in UTC (utc_now_iso), so the cutoff is too: a
    # host-clock cutoff shifted the window by 5.5 h on an IST host. A survey
    # that was never sent (NOT_SENT, or PENDING_SEND with no stored message)
    # does not count (D91, Codex 1419 R2-B).
    from datetime import timedelta, timezone
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=_NPS_DEDUP_WINDOW_DAYS)
    ).isoformat()
    recent = counted_surveys(db, list(nps_coll.find({
        "customer_id": customer_id,
        "survey_sent_at": {"$gte": cutoff},
        "status": {"$ne": "NOT_SENT"},
    })))
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

    res = await send_survey(
        db,
        order=order,
        customer=customer,
        store_id=store_id,
        triggered_by=actor.get("user_id", "system"),
        auto=True,
    )
    logger.info("[NPS_TRIGGER] NPS %s for order %s: %s", res["nps_id"], order_id,
                "stored" if res.get("persisted") else "NOT sent")
