"""
IMS 2.0 - WhatsApp Inbound Intent Router (CRM-14)
===================================================
Keyword/button-payload intent routing for inbound WhatsApp messages.
NO LLM required -- pure keyword matching + button-payload dispatch.

Intents:
  BOOK   / "eye test"   -> create appointment follow-up
  REORDER/ "lens"       -> look up last CL order, draft reorder follow-up
  AGENT  / "help"       -> flag conversation for human handoff
  STOP   / "opt out"    -> record marketing opt-out (DPDP + WhatsApp STOP)

Design contract:
  - FAIL-SOFT: every function catches all exceptions, never raises.
  - DARK when DISPATCH_MODE != live (outbound replies are SIMULATED).
  - Consent/opt-out is checked before any outbound action.
  - No emojis in Python (Windows cp1252 risk).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# Keyword matching tables
# ============================================================================

# Map normalised keyword -> intent name.
# Order matters: checked in sequence; first match wins.
_KEYWORD_MAP: list[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b(stop|optout|opt.out|unsubscribe|no.more)\b", re.IGNORECASE), "OPT_OUT"),
    (re.compile(r"\b(book|appointment|eyetest|eye.test|schedule)\b", re.IGNORECASE), "BOOK"),
    (re.compile(r"\b(reorder|lens|contact.len|cl.order|repeat)\b", re.IGNORECASE), "REORDER"),
    (re.compile(r"\b(agent|human|help|support|staff|talk.to)\b", re.IGNORECASE), "AGENT"),
]

# Button payloads sent by WhatsApp quick-reply / list-reply buttons.
# Meta Business API delivers these in message.interactive.button_reply.id
# or message.interactive.list_reply.id.
_BUTTON_PAYLOAD_MAP: Dict[str, str] = {
    "BOOK_APPT": "BOOK",
    "REORDER_CL": "REORDER",
    "TALK_AGENT": "AGENT",
    "STOP": "OPT_OUT",
}

# ============================================================================
# Reply templates (ASCII-only, no emojis)
# ============================================================================

_REPLY_TEMPLATES: Dict[str, str] = {
    "BOOK": (
        "Hi {name}! We have received your eye test request. "
        "Our team will call you shortly to confirm your appointment. "
        "For urgent bookings call us directly. Thank you."
    ),
    "REORDER": (
        "Hi {name}! We found your last contact lens order. "
        "Our team will prepare a reorder quote and contact you. "
        "Thank you for choosing Better Vision."
    ),
    "AGENT": (
        "Hi {name}! Connecting you with our team. "
        "Please hold -- a staff member will respond shortly."
    ),
    "OPT_OUT": (
        "You have been unsubscribed from Better Vision WhatsApp notifications. "
        "To subscribe again, reply START or visit our store."
    ),
    "UNKNOWN": (
        "Hi {name}! Thank you for reaching out to Better Vision. "
        "Reply BOOK for an eye test appointment, REORDER for contact lens reorder, "
        "or AGENT to speak with our team."
    ),
}


# ============================================================================
# Intent detection
# ============================================================================


def detect_intent(text: str, button_payload: Optional[str] = None) -> str:
    """
    Detect the intent from inbound message text + optional button payload.
    Button payload wins over keyword match (explicit user choice).
    Returns one of: BOOK | REORDER | AGENT | OPT_OUT | UNKNOWN.
    """
    if button_payload:
        mapped = _BUTTON_PAYLOAD_MAP.get(str(button_payload).strip().upper())
        if mapped:
            return mapped

    if not text:
        return "UNKNOWN"

    clean = str(text).strip()
    for pattern, intent in _KEYWORD_MAP:
        if pattern.search(clean):
            return intent

    return "UNKNOWN"


# ============================================================================
# DB helpers (all fail-soft)
# ============================================================================


def _get_db():
    try:
        from database.connection import get_db as _gd
        d = _gd()
        if d is None:
            return None
        return getattr(d, "db", None) or d
    except Exception as e:
        logger.debug("[WA_INTENTS] _get_db failed: %s", e)
        return None


def _lookup_customer_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    """Find a customer by normalised mobile number. Fail-soft -> None."""
    db = _get_db()
    if db is None or not phone:
        return None
    try:
        # Strip to last 10 digits for matching (Indian mobiles stored without country code)
        digits = re.sub(r"\D", "", phone)
        suffixes = set()
        if len(digits) >= 10:
            suffixes.add(digits[-10:])
        if digits.startswith("91") and len(digits) == 12:
            suffixes.add(digits[2:])
        if not suffixes:
            return None
        query = {"mobile": {"$in": list(suffixes)}}
        coll = db.get_collection("customers")
        return coll.find_one(query)
    except Exception as e:
        logger.debug("[WA_INTENTS] customer lookup failed: %s", e)
        return None


def _is_opted_out(customer: Optional[Dict[str, Any]]) -> bool:
    """
    True if the customer has opted out of marketing/WhatsApp comms.
    Checks both marketing_consent flag and whatsapp_opted_out flag.
    Missing/None defaults to consented (same policy as campaigns.py).
    """
    if customer is None:
        return False
    if customer.get("whatsapp_opted_out") is True:
        return True
    if customer.get("marketing_consent") is False:
        return True
    return False


def _record_opt_out(phone: str, customer: Optional[Dict[str, Any]]) -> None:
    """Mark customer opted out. Fail-soft."""
    db = _get_db()
    if db is None:
        return
    try:
        now = datetime.now(timezone.utc).isoformat()
        if customer and customer.get("_id"):
            db.get_collection("customers").update_one(
                {"_id": customer["_id"]},
                {
                    "$set": {
                        "whatsapp_opted_out": True,
                        "whatsapp_opted_out_at": now,
                        "marketing_consent": False,
                    }
                },
            )
            logger.info(
                "[WA_INTENTS] customer %s opted out via WhatsApp STOP",
                customer.get("customer_id", "?"),
            )
        # Also persist in whatsapp_opt_outs collection so we can block even
        # if the customer doc is not found yet (e.g. first-time sender).
        db.get_collection("whatsapp_opt_outs").update_one(
            {"phone": phone},
            {"$set": {"phone": phone, "opted_out_at": now, "channel": "whatsapp"}},
            upsert=True,
        )
    except Exception as e:
        logger.warning("[WA_INTENTS] opt-out persist failed: %s", e)


def _phone_is_opted_out(phone: str) -> bool:
    """Check the opt-out collection directly (covers non-registered senders)."""
    db = _get_db()
    if db is None or not phone:
        return False
    try:
        digits = re.sub(r"\D", "", phone)
        suffixes = list({digits, digits[-10:] if len(digits) >= 10 else digits})
        row = db.get_collection("whatsapp_opt_outs").find_one(
            {"phone": {"$in": suffixes}}
        )
        return row is not None
    except Exception as e:
        logger.debug("[WA_INTENTS] opt-out check failed: %s", e)
        return False


def _get_last_cl_order(customer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Fetch the most recent contact-lens order for the customer. Fail-soft."""
    db = _get_db()
    if db is None or not customer:
        return None
    try:
        cid = customer.get("customer_id") or str(customer.get("_id", ""))
        if not cid:
            return None
        coll = db.get_collection("orders")
        # Look for orders containing contact-lens items (item_type CL or category
        # matching 'contact').
        pipeline = [
            {
                "$match": {
                    "$or": [
                        {"customer_id": cid},
                        {"customer_id": str(cid)},
                    ],
                    "status": {"$nin": ["cancelled", "returned"]},
                }
            },
            {"$sort": {"created_at": -1}},
            {"$limit": 10},
        ]
        for order in coll.aggregate(pipeline):
            items = order.get("items") or []
            for item in items:
                cat = str(item.get("category") or "").lower()
                itype = str(item.get("item_type") or "").lower()
                name = str(item.get("product_name") or "").lower()
                if (
                    "contact" in cat
                    or "cl" == itype
                    or "contact len" in name
                    or "contact_len" in itype
                ):
                    return order
        return None
    except Exception as e:
        logger.debug("[WA_INTENTS] CL order lookup failed: %s", e)
        return None


def _create_follow_up(
    customer: Dict[str, Any],
    follow_up_type: str,
    notes: str,
    store_id: str,
) -> Optional[str]:
    """
    Create a follow-up record (same schema as follow_ups router).
    Returns the new follow_up_id or None on failure.
    """
    import uuid
    db = _get_db()
    if db is None:
        return None
    try:
        now = datetime.now(timezone.utc)
        cid = customer.get("customer_id") or str(customer.get("_id", ""))
        doc = {
            "follow_up_id": str(uuid.uuid4()),
            "customer_id": cid,
            "customer_name": customer.get("name") or customer.get("full_name") or "Customer",
            "customer_phone": customer.get("mobile") or "",
            "store_id": store_id or customer.get("store_id") or "HQ",
            "follow_up_type": follow_up_type,
            "status": "pending",
            "priority": "medium",
            "notes": notes,
            "created_at": now,
            "updated_at": now,
            "source": "whatsapp_inbound",
        }
        db.get_collection("follow_ups").insert_one(doc)
        logger.info(
            "[WA_INTENTS] created follow_up %s type=%s for customer %s",
            doc["follow_up_id"],
            follow_up_type,
            cid,
        )
        return doc["follow_up_id"]
    except Exception as e:
        logger.warning("[WA_INTENTS] follow_up create failed: %s", e)
        return None


# ============================================================================
# Intent handlers
# ============================================================================


async def handle_book(
    phone: str,
    customer: Optional[Dict[str, Any]],
    store_id: str,
) -> str:
    """Create an eye-test appointment follow-up and return reply text."""
    name = "Customer"
    if customer:
        name = customer.get("name") or customer.get("full_name") or "Customer"
        _create_follow_up(
            customer,
            "eye_test_reminder",
            "Inbound WhatsApp: customer requested eye test booking.",
            store_id,
        )
    return _REPLY_TEMPLATES["BOOK"].format(name=name)


async def handle_reorder(
    phone: str,
    customer: Optional[Dict[str, Any]],
    store_id: str,
) -> str:
    """Look up last CL order and draft a reorder follow-up."""
    name = "Customer"
    notes = "Inbound WhatsApp: customer requested contact lens reorder."
    if customer:
        name = customer.get("name") or customer.get("full_name") or "Customer"
        last_order = _get_last_cl_order(customer)
        if last_order:
            order_num = last_order.get("order_number") or last_order.get("order_id", "")
            notes = (
                f"Inbound WhatsApp: reorder request. "
                f"Last CL order: {order_num} on "
                f"{str(last_order.get('created_at', ''))[:10]}."
            )
        _create_follow_up(customer, "general", notes, store_id)
    return _REPLY_TEMPLATES["REORDER"].format(name=name)


async def handle_agent(
    phone: str,
    customer: Optional[Dict[str, Any]],
    store_id: str,
) -> str:
    """Flag conversation for human handoff."""
    db = _get_db()
    if db is not None:
        try:
            digits = re.sub(r"\D", "", phone)
            db.get_collection("whatsapp_conversations").update_one(
                {"phone": digits[-10:] if len(digits) >= 10 else digits},
                {
                    "$set": {
                        "needs_human": True,
                        "needs_human_since": datetime.now(timezone.utc).isoformat(),
                    }
                },
            )
        except Exception as e:
            logger.debug("[WA_INTENTS] agent flag update failed: %s", e)
    name = "Customer"
    if customer:
        name = customer.get("name") or customer.get("full_name") or "Customer"
    return _REPLY_TEMPLATES["AGENT"].format(name=name)


async def handle_opt_out(
    phone: str,
    customer: Optional[Dict[str, Any]],
    store_id: str,
) -> str:
    """Record STOP and send confirmation reply (no further messages sent)."""
    _record_opt_out(phone, customer)
    return _REPLY_TEMPLATES["OPT_OUT"].format(name="")


async def handle_unknown(
    phone: str,
    customer: Optional[Dict[str, Any]],
    store_id: str,
) -> str:
    name = "Customer"
    if customer:
        name = customer.get("name") or customer.get("full_name") or "Customer"
    return _REPLY_TEMPLATES["UNKNOWN"].format(name=name)


# ============================================================================
# Main dispatcher
# ============================================================================

_HANDLER_MAP = {
    "BOOK": handle_book,
    "REORDER": handle_reorder,
    "AGENT": handle_agent,
    "OPT_OUT": handle_opt_out,
    "UNKNOWN": handle_unknown,
}


async def dispatch_intent(
    phone: str,
    text: str,
    button_payload: Optional[str],
    store_id: str,
) -> Dict[str, Any]:
    """
    High-level dispatcher called by the webhook handler.

    1. Check opt-out (hard gate -- no further processing if opted out).
    2. Detect intent.
    3. Lookup customer.
    4. Run handler -> reply text.
    5. Send reply via outbound provider (DARK when DISPATCH_MODE != live).

    Returns a dict with intent, reply_sent, and customer_id for logging.
    """
    result: Dict[str, Any] = {
        "phone": phone[-4:] if len(phone) >= 4 else phone,
        "intent": "UNKNOWN",
        "reply_sent": False,
        "customer_id": None,
        "opted_out": False,
    }

    try:
        # Hard opt-out gate (check collection first -- fastest path).
        if _phone_is_opted_out(phone):
            result["opted_out"] = True
            logger.info("[WA_INTENTS] blocked: phone %s is opted out", phone[-4:])
            return result

        intent = detect_intent(text, button_payload)
        result["intent"] = intent

        customer = _lookup_customer_by_phone(phone)
        if customer:
            result["customer_id"] = customer.get("customer_id") or str(customer.get("_id", ""))

        # Opt-out from customer doc (secondary check).
        if intent != "OPT_OUT" and _is_opted_out(customer):
            result["opted_out"] = True
            logger.info(
                "[WA_INTENTS] blocked: customer %s has marketing_consent=False",
                result["customer_id"],
            )
            return result

        handler = _HANDLER_MAP.get(intent, handle_unknown)
        reply_text = await handler(phone, customer, store_id)

        # Send reply via outbound provider (fail-soft).
        try:
            from agents.providers import send_whatsapp

            dispatch_result = await send_whatsapp(phone, reply_text)
            result["reply_sent"] = dispatch_result.ok
            result["reply_status"] = dispatch_result.status
        except Exception as e:
            logger.warning("[WA_INTENTS] send_whatsapp failed: %s", e)
            result["reply_sent"] = False

    except Exception as e:
        logger.error("[WA_INTENTS] dispatch_intent unhandled error: %s", e, exc_info=True)

    return result
