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
  - FAIL-SOFT by default: every function catches all exceptions, never raises.
  - STRICT for the durable receiver (webhooks._process_whatsapp_message):
    with strict=True a storage failure RAISES instead of being swallowed, so
    the message's inbox row stays unfinished and is retried; and every write
    is keyed on the inbound message id, so a retry cannot apply it twice.
  - DARK when DISPATCH_MODE != live (outbound replies are SIMULATED).
  - Consent/opt-out is checked before any outbound action.
  - No emojis in Python (Windows cp1252 risk).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from ..utils.ist import ist_date_str

# L1-F30: this module had its own `_get_db` that did
# `getattr(d, "db", None) or d`. PyMongo refuses bool(Database), so with the
# real connection it always raised, was swallowed, and every intent ran as if
# there were no database (no customer lookup, no STOP record, no human handoff,
# no follow-up). The copy is gone: the messaging services share the one helper
# that never truth-tests a pymongo object. Tests still patch
# `api.services.whatsapp_intents._get_db`.
from .message_events import _get_db

logger = logging.getLogger(__name__)


class IntentNotApplied(RuntimeError):
    """Strict mode only: an intent's effect could not be stored."""


def _need_db(strict: bool):
    """The database, or None when it is unreachable (strict: raise instead --
    "no database" must never read as "nothing to do")."""
    db = _get_db()
    if db is None and strict:
        raise IntentNotApplied("storage unavailable")
    return db


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


def _lookup_customer_by_phone(
    phone: str, strict: bool = False
) -> Optional[Dict[str, Any]]:
    """Find a customer by normalised mobile number. Fail-soft -> None."""
    db = _need_db(strict)
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
        if strict:
            raise
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


def _record_opt_out(
    phone: str, customer: Optional[Dict[str, Any]], strict: bool = False
) -> None:
    """Mark customer opted out. Fail-soft. Safe to repeat: the customer's
    first STOP stamp is kept, and the opt-out row is one per phone."""
    db = _need_db(strict)
    if db is None:
        return
    try:
        now = datetime.now(timezone.utc).isoformat()
        if customer and customer.get("_id"):
            db.get_collection("customers").update_one(
                {"_id": customer["_id"], "whatsapp_opted_out": {"$ne": True}},
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
        if strict:
            raise
        logger.warning("[WA_INTENTS] opt-out persist failed: %s", e)


def _phone_is_opted_out(phone: str, strict: bool = False) -> bool:
    """Check the opt-out collection directly (covers non-registered senders)."""
    db = _need_db(strict)
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
        if strict:
            raise
        logger.debug("[WA_INTENTS] opt-out check failed: %s", e)
        return False


def _get_last_cl_order(
    customer: Dict[str, Any], strict: bool = False
) -> Optional[Dict[str, Any]]:
    """Fetch the most recent contact-lens order for the customer. Fail-soft."""
    db = _need_db(strict)
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
        if strict:
            raise
        logger.debug("[WA_INTENTS] CL order lookup failed: %s", e)
        return None


def _create_follow_up(
    customer: Dict[str, Any],
    follow_up_type: str,
    notes: str,
    store_id: str,
    message_id: Optional[str] = None,
    strict: bool = False,
) -> Optional[str]:
    """
    Create a follow-up record (same schema as follow_ups router).
    Returns the new follow_up_id or None on failure.

    With `message_id` (the inbound WhatsApp message that asked for it) the
    follow-up is created AT MOST ONCE for that message: a retry of the same
    message finds the first one and returns its id.
    """
    import uuid
    db = _need_db(strict)
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
        coll = db.get_collection("follow_ups")
        if message_id:
            # ponytail: find-or-insert in one upsert with no unique index
            # behind it -- the inbox row's claim keeps two runs of one message
            # apart. Add a partial unique index on source_message_id if two
            # runs can ever overlap.
            key = {"source": "whatsapp_inbound", "source_message_id": message_id}
            doc["source_message_id"] = message_id
            coll.update_one(key, {"$setOnInsert": doc}, upsert=True)
            return (coll.find_one(key, {"follow_up_id": 1}) or {}).get("follow_up_id")
        coll.insert_one(doc)
        logger.info(
            "[WA_INTENTS] created follow_up %s type=%s for customer %s",
            doc["follow_up_id"],
            follow_up_type,
            cid,
        )
        return doc["follow_up_id"]
    except Exception as e:
        if strict:
            raise
        logger.warning("[WA_INTENTS] follow_up create failed: %s", e)
        return None


# ============================================================================
# Intent handlers
# ============================================================================
# `ctx` is empty for a plain call, or {"message_id": <inbound message id>,
# "strict": True} when the durable receiver applies a stored message.


def _strict_kw(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {"strict": True} if ctx.get("strict") else {}


async def handle_book(
    phone: str,
    customer: Optional[Dict[str, Any]],
    store_id: str,
    **ctx: Any,
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
            **ctx,
        )
    return _REPLY_TEMPLATES["BOOK"].format(name=name)


async def handle_reorder(
    phone: str,
    customer: Optional[Dict[str, Any]],
    store_id: str,
    **ctx: Any,
) -> str:
    """Look up last CL order and draft a reorder follow-up."""
    name = "Customer"
    notes = "Inbound WhatsApp: customer requested contact lens reorder."
    if customer:
        name = customer.get("name") or customer.get("full_name") or "Customer"
        last_order = _get_last_cl_order(customer, **_strict_kw(ctx))
        if last_order:
            order_num = last_order.get("order_number") or last_order.get("order_id", "")
            # BUG-104: this date is read by the CUSTOMER on WhatsApp, so it is
            # an outbound VALUE -> the IST calendar day (+5:30), not the stored
            # UTC one. No bound and no persisted key on this path, so nothing
            # else has to move with it.
            notes = (
                f"Inbound WhatsApp: reorder request. "
                f"Last CL order: {order_num} on "
                f"{ist_date_str(last_order.get('created_at'))}."
            )
        _create_follow_up(customer, "general", notes, store_id, **ctx)
    return _REPLY_TEMPLATES["REORDER"].format(name=name)


async def handle_agent(
    phone: str,
    customer: Optional[Dict[str, Any]],
    store_id: str,
    **ctx: Any,
) -> str:
    """Flag conversation for human handoff."""
    strict = bool(ctx.get("strict"))
    message_id = ctx.get("message_id")
    db = _need_db(strict)
    if db is not None:
        try:
            digits = re.sub(r"\D", "", phone)
            thread = {"phone": digits[-10:] if len(digits) >= 10 else digits}
            flag: Dict[str, Any] = {
                "needs_human": True,
                "needs_human_since": datetime.now(timezone.utc).isoformat(),
            }
            fresh = dict(thread)
            if message_id:
                # One hand-off per asking message: a retry of the same message
                # leaves the flag and its time exactly as first written.
                flag["needs_human_message_id"] = message_id
                fresh["needs_human_message_id"] = {"$ne": message_id}
            coll = db.get_collection("whatsapp_conversations")
            flagged = coll.update_one(fresh, {"$set": flag}).matched_count
            if strict and not flagged and coll.find_one(thread, {"_id": 1}) is None:
                # No thread to flag: staff would never see the request.
                raise IntentNotApplied("no conversation thread to hand off")
        except Exception as e:
            if strict:
                raise
            logger.debug("[WA_INTENTS] agent flag update failed: %s", e)
    name = "Customer"
    if customer:
        name = customer.get("name") or customer.get("full_name") or "Customer"
    return _REPLY_TEMPLATES["AGENT"].format(name=name)


async def handle_opt_out(
    phone: str,
    customer: Optional[Dict[str, Any]],
    store_id: str,
    **ctx: Any,
) -> str:
    """Record STOP and send confirmation reply (no further messages sent)."""
    _record_opt_out(phone, customer, **_strict_kw(ctx))
    return _REPLY_TEMPLATES["OPT_OUT"].format(name="")


async def handle_unknown(
    phone: str,
    customer: Optional[Dict[str, Any]],
    store_id: str,
    **ctx: Any,
) -> str:
    name = "Customer"
    if customer:
        name = customer.get("name") or customer.get("full_name") or "Customer"
    return _REPLY_TEMPLATES["UNKNOWN"].format(name=name)


# ============================================================================
# Coexistence double-answer guard
# ============================================================================
# Each shop's WhatsApp number runs on the shop phone AND the API. A human on
# the shop phone answers customers; IMS must not answer NEXT to them. Policy
# msg.auto_reply_mode (registered in policy_registry, default "off") decides:
#   off         -> never auto-reply (Coexistence-safe default)
#   after_hours -> auto-reply only while the store is CLOSED (store
#                  working_hours where stored, else the shared 21:00-09:00
#                  IST quiet window), evaluated in IST
#   always      -> auto-reply to every inbound
# THE one implementation: dispatch_intent below is the only auto-reply send
# site, and it gates through auto_reply_allowed().

_DEFAULT_OPEN_START_MIN = 9 * 60  # fallback open window 09:00-21:00 IST,
_DEFAULT_OPEN_END_MIN = 21 * 60  # i.e. after-hours == the shared quiet window


def _store_working_minutes(store_id: str) -> Tuple[int, int]:
    """(open_minute, close_minute) of the store's IST working window.

    Reads stores.working_hours "HH:MM-HH:MM"; missing store / missing or
    unparseable value -> the 09:00-21:00 default. Never raises."""
    try:
        db = _get_db()
        if db is None or not store_id:
            return _DEFAULT_OPEN_START_MIN, _DEFAULT_OPEN_END_MIN
        doc = db.get_collection("stores").find_one(
            {"store_id": store_id}, {"_id": 0, "working_hours": 1}
        )
        raw = str((doc or {}).get("working_hours") or "")
        start_s, end_s = raw.split("-", 1)
        sh, sm = start_s.strip().split(":", 1)
        eh, em = end_s.strip().split(":", 1)
        start = int(sh) * 60 + int(sm)
        end = int(eh) * 60 + int(em)
        if 0 <= start < 24 * 60 and 0 <= end < 24 * 60:
            return start, end
    except Exception:  # noqa: BLE001 - fail-soft to the sane default window
        pass
    return _DEFAULT_OPEN_START_MIN, _DEFAULT_OPEN_END_MIN


def _store_open_now(store_id: str, now: Optional[datetime] = None) -> bool:
    """True when the store is inside its working hours RIGHT NOW, in IST
    (BUG-104 discipline: the shared agents.quiet_hours IST clock, never a
    naive server-local now())."""
    from agents.quiet_hours import now_ist  # shared IST clock

    t = now_ist(now)
    minutes = t.hour * 60 + t.minute
    start, end = _store_working_minutes(store_id)
    if start <= end:
        return start <= minutes < end
    # Overnight window (e.g. "18:00-02:00") wraps midnight.
    return minutes >= start or minutes < end


def auto_reply_allowed(
    store_id: str, now: Optional[datetime] = None
) -> Tuple[bool, str]:
    """(allowed, reason) for sending an automated inbound reply.

    Fail-CLOSED: any policy-read error means "off" -- a config hiccup must
    never make IMS answer next to a human on the shop phone."""
    try:
        from api.services.policy_engine import get_policy

        scope = {"store_id": store_id} if store_id else None
        mode = str(get_policy("msg.auto_reply_mode", scope) or "off").strip().lower()
    except Exception:  # noqa: BLE001
        mode = "off"
    if mode == "always":
        return True, "auto_reply_mode=always"
    if mode == "after_hours":
        if _store_open_now(store_id, now):
            return False, "store open - a human on the shop phone answers"
        return True, "after hours - store closed"
    return False, "auto_reply_mode=off (Coexistence default: staff reply in the WhatsApp app)"


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
    *,
    message_id: Optional[str] = None,
    strict: bool = False,
    send_reply: bool = True,
) -> Dict[str, Any]:
    """
    High-level dispatcher called by the webhook handler.

    1. Check opt-out (hard gate -- no further processing if opted out).
    2. Detect intent.
    3. Lookup customer.
    4. Run handler -> reply text.
    5. Send reply via outbound provider (DARK when DISPATCH_MODE != live).

    Returns a dict with intent, reply_sent, and customer_id for logging.

    The durable receiver calls it with `message_id` + `strict=True` +
    `send_reply=False`: steps 1-4 then RAISE if an effect could not be stored
    (nothing is swallowed), every write is keyed on the message so a retry
    applies it once, and the reply is left to `send_intent_reply` -- which the
    receiver calls only after it has durably recorded "intent applied", so a
    retry can never send a customer the same reply twice.
    """
    result: Dict[str, Any] = {
        "phone": phone[-4:] if len(phone) >= 4 else phone,
        "intent": "UNKNOWN",
        "reply_sent": False,
        "customer_id": None,
        "opted_out": False,
    }
    ctx: Dict[str, Any] = {"message_id": message_id, "strict": True} if strict else {}
    lookup = _strict_kw(ctx)

    try:
        # Hard opt-out gate (check collection first -- fastest path).
        if _phone_is_opted_out(phone, **lookup):
            result["opted_out"] = True
            logger.info("[WA_INTENTS] blocked: phone %s is opted out", phone[-4:])
            return result

        intent = detect_intent(text, button_payload)
        result["intent"] = intent

        customer = _lookup_customer_by_phone(phone, **lookup)
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
        result["reply_text"] = await handler(phone, customer, store_id, **ctx)
        if send_reply:
            await send_intent_reply(phone, store_id, result)

    except Exception as e:
        if strict:
            raise
        logger.error("[WA_INTENTS] dispatch_intent unhandled error: %s", e, exc_info=True)

    return result


async def send_intent_reply(phone: str, store_id: str, result: Dict[str, Any]) -> None:
    """Step 5: send the handler's reply (taken out of `result`) when policy
    allows, and record the outcome on `result`. Never raises: a reply is
    best-effort and is attempted AT MOST ONCE per inbound message."""
    reply_text = result.pop("reply_text", None)
    if not reply_text:
        return
    try:
        # Coexistence double-answer guard: the handler ran (opt-outs are
        # recorded, follow-ups created) but the automated REPLY only goes
        # out when policy allows -- by default a human on the shop phone
        # answers, not IMS.
        allowed, why = auto_reply_allowed(store_id)
        if not allowed:
            result["reply_sent"] = False
            result["reply_suppressed"] = why
            logger.info("[WA_INTENTS] auto-reply suppressed: %s", why)
            return

        # Send reply via outbound provider (fail-soft).
        from agents.providers import send_whatsapp

        dispatch_result = await send_whatsapp(
            phone,
            reply_text,
            template_id="WA_INTENT_REPLY",
            store_id=store_id,
        )
        result["reply_sent"] = dispatch_result.ok
        result["reply_status"] = dispatch_result.status
    except Exception as e:
        logger.warning("[WA_INTENTS] send_whatsapp failed: %s", e)
        result["reply_sent"] = False
