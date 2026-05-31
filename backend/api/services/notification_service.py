"""
IMS 2.0 - Notification Service
================================
Shared notification sending and logging for all marketing features.

Honest-status contract (do NOT fake success):
- This function QUEUES a customer message to the notification_logs collection.
  It does NOT itself hit the provider -- MEGAPHONE's drain pass (or a future
  worker) picks up PENDING rows and dispatches them via agents.providers, which
  is gated by DISPATCH_MODE (off/test/live).
- Therefore the truthful status at queue time is PENDING ("accepted, not yet
  sent"), never a fabricated SENT. The returned dict carries `dispatched=False`
  and the current `dispatch_mode` so callers/UI never imply a message left the
  building. When DISPATCH_MODE is off (the default) nothing is ever dispatched
  to a real customer -- no accidental spam from a fresh deploy.

DLT audit fields (additive, per-message) are written so each notification_logs
row is independently auditable for Indian telecom (TRAI/DLT) compliance:
template_id, pe_id, category, consent_basis, provider_msg_id, delivery_status.
"""

from datetime import datetime
import os
import uuid
import logging

logger = logging.getLogger(__name__)

# DLT Principal Entity ID (registered on the telecom DLT platform). Stamped on
# every outbound row for audit; read from env so it is environment-specific and
# never hard-coded. Empty when not yet registered -- the field is still present.
DLT_PE_ID = os.getenv("DLT_PE_ID", "") or os.getenv("MSG91_DLT_PE_ID", "")


def _dispatch_mode() -> str:
    """Current dispatch mode (off/test/live), read fresh so a runtime env change
    is reflected. Falls back to the provider module's value, else the env."""
    try:
        from agents.providers import dispatch_mode as _dm

        return _dm()
    except Exception:
        return os.getenv("DISPATCH_MODE", "off").lower()


def _get_db():
    try:
        from database.connection import get_db

        return get_db().db
    except Exception:
        return None


# Notification templates (Python-side, matching frontend constants/notifications.ts)
TEMPLATES = {
    "PRESCRIPTION_EXPIRY": "Hi {customer_name}, your prescription from {store_name} is expiring on {expiry_date}. Schedule your eye check-up today! Call us at {store_phone}.",
    "BIRTHDAY_WISH": "Happy Birthday {customer_name}! Wishing you a wonderful year ahead. Visit {store_name} for an exclusive birthday offer!",
    "ANNUAL_CHECKUP_REMINDER": "Hi {customer_name}, it's been a year since your last eye exam at {store_name}. Time for your annual check-up! Book now.",
    "ORDER_DELIVERED": "Hi {customer_name}, your order {order_number} from {store_name} is ready for pickup!",
    "GOOGLE_REVIEW_REQUEST": "Hi {customer_name}, thank you for choosing {store_name}! We'd love your feedback. Please leave us a review: {review_link}",
    "WALKOUT_RECOVERY": "Hi {customer_name}, you recently visited {store_name} and tried {frame_names}. We'd love to help you find the perfect pair! Visit us again for a special {discount_percent}% offer. Valid till {validity_date}.",
    "REFERRAL_INVITE": "Hi {customer_name}, share the gift of clear vision! Give your friends and family this referral code: {referral_code}. They get {referee_reward} off their first purchase, and you earn {referrer_reward} in store credit!",
    "NPS_SURVEY": "Hi {customer_name}, how was your experience at {store_name}? Rate us 1-10: {survey_link}. Your feedback helps us serve you better!",
}


def populate_template(template_id: str, variables: dict) -> str:
    """Fill template variables. Returns the formatted message."""
    template = TEMPLATES.get(template_id, "")
    if not template:
        return f"[Template {template_id} not found]"
    try:
        return template.format(**variables)
    except KeyError as e:
        logger.warning("Missing template variable %s for %s", e, template_id)
        return template


def _default_consent_basis(category: str) -> str:
    """Why this message is permitted under DLT/TRAI -- recorded for audit.

    Transactional/service messages ride the 'transactional' basis (allowed even
    to marketing-opt-outs); everything else is 'marketing_consent' (the caller
    is responsible for having checked the opt-out before queueing)."""
    cat = (category or "").upper()
    if cat in ("SERVICE", "TRANSACTIONAL", "REMINDER", "OTP"):
        return "transactional"
    return "marketing_consent"


async def send_notification(
    store_id: str,
    customer_id: str,
    customer_phone: str,
    customer_name: str,
    template_id: str,
    channel: str = "WHATSAPP",
    variables: dict = None,
    category: str = "SERVICE",
    triggered_by: str = "auto",
    related_entity_type: str = None,
    related_entity_id: str = None,
    consent_basis: str = None,
) -> dict:
    """
    Queue a customer notification (does NOT itself send -- see module docstring).

    Writes a notification_logs row with the HONEST status PENDING and the
    current dispatch_mode, plus per-message DLT audit fields. The returned dict
    carries `dispatched=False`: nothing has gone to the customer yet, and with
    DISPATCH_MODE=off (default) nothing ever will until the mode is changed.
    MEGAPHONE's drain pass is what flips PENDING -> SENT/SIMULATED/FAILED.
    """
    if variables is None:
        variables = {}

    # Always include customer_name in variables
    variables.setdefault("customer_name", customer_name)

    # Build message from template
    message = populate_template(template_id, variables)

    mode = _dispatch_mode()

    # Create notification log entry. Status is PENDING (truthful: accepted +
    # queued, not yet dispatched). The DLT audit block is additive.
    notification = {
        "notification_id": f"NTF-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}",
        "store_id": store_id,
        "customer_id": customer_id,
        "customer_phone": customer_phone,
        "customer_name": customer_name,
        "template_id": template_id,
        "category": category,
        "channel": channel,
        "message": message,
        "status": "PENDING",
        "triggered_by": triggered_by,
        "related_entity_type": related_entity_type,
        "related_entity_id": related_entity_id,
        "created_at": datetime.now().isoformat(),
        "sent_at": None,
        "delivered_at": None,
        "failure_reason": None,
        # --- DLT / TRAI per-message audit fields (additive) ---
        "pe_id": DLT_PE_ID,
        "consent_basis": consent_basis or _default_consent_basis(category),
        "provider_msg_id": None,  # set by the drain/provider once dispatched
        "delivery_status": "QUEUED",  # advances QUEUED->SENT->DELIVERED via DLR webhook
        "dispatch_mode": mode,  # the mode in effect when queued
    }

    # Persist to MongoDB
    db = _get_db()
    if db:
        try:
            coll = db.get_collection("notification_logs")
            coll.insert_one(notification)
            logger.info(
                "Notification queued (status=PENDING, mode=%s): %s -> %s (%s)",
                mode,
                template_id,
                customer_phone,
                channel,
            )
        except Exception as e:
            logger.warning("Failed to log notification: %s", e)

    # Honest, explicit signal to callers: this was queued, NOT sent. A copy with
    # `dispatched=False` (no real customer contact has occurred yet).
    result = dict(notification)
    result.pop("_id", None)  # insert_one mutates the dict with an ObjectId
    result["dispatched"] = False
    return result
