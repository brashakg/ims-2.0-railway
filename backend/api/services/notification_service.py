"""
IMS 2.0 - Notification Service
================================
Shared notification sending and logging for all marketing features.
MVP: Logs to notification_logs collection. Actual WhatsApp/SMS API integration
is a future enhancement when API keys are configured.
"""

from datetime import datetime
import uuid
import logging

logger = logging.getLogger(__name__)


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
) -> dict:
    """
    Send a notification to a customer.
    MVP: Logs to notification_logs collection with status PENDING.
    Future: Integrate with WhatsApp Business API / MSG91 / Twilio.
    """
    if variables is None:
        variables = {}

    # Always include customer_name in variables
    variables.setdefault("customer_name", customer_name)

    # Build message from template
    message = populate_template(template_id, variables)

    # Create notification log entry
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
    }

    # Persist to MongoDB
    db = _get_db()
    if db:
        try:
            coll = db.get_collection("notification_logs")
            coll.insert_one(notification)
            logger.info("Notification logged: %s -> %s (%s)", template_id, customer_phone, channel)
        except Exception as e:
            logger.warning("Failed to log notification: %s", e)

    return notification
