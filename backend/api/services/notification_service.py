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

from api.utils.event_time import utc_now_iso

from datetime import datetime
import os
import uuid
import logging

logger = logging.getLogger(__name__)

# DLT Principal Entity ID (registered on the telecom DLT platform). Stamped on
# every outbound row for audit; read from env so it is environment-specific and
# never hard-coded. Empty when not yet registered -- the field is still present.
DLT_PE_ID = os.getenv("DLT_PE_ID", "") or os.getenv("MSG91_DLT_PE_ID", "")


# Flows whose link variables get MSG91 short-URL click tracking: the review
# request plus the recall class (the 11-month re-test business). Scoped on
# purpose -- other flows' links (portal OTP links, survey links) stay as-is
# until the owner asks for click data on them.
SHORTURL_FLOWS = frozenset(
    {
        "GOOGLE_REVIEW_REQUEST",
        "PRESCRIPTION_EXPIRY",
        "ANNUAL_CHECKUP_REMINDER",
        "CL_REORDER_REMINDER",
    }
)


# Utility-class flows that fall back to ONE DLT SMS when their WhatsApp send
# is reported FAILED by the delivery-report webhook: the order-ready, recall
# and workshop-ready classes. Deliberately NOT marketing flows (a failed
# birthday blast stays failed) and NOT auth/OTP flows (they have their own
# retry semantics). The fallback DLT template id per flow lives in the
# template registry (notification_templates.resolve_sms_fallback), never env.
SMS_FALLBACK_FLOWS = frozenset(
    {
        "ORDER_DELIVERED",  # order ready for pickup
        "PRESCRIPTION_EXPIRY",  # recall class
        "ANNUAL_CHECKUP_REMINDER",
        "CL_REORDER_REMINDER",
        "WORKSHOP_READY",  # workshop ready
        "repair_ready",
    }
)


def _dispatch_mode() -> str:
    """Current dispatch mode (off/test/live), read fresh so a runtime env change
    is reflected. Falls back to the provider module's value, else the env."""
    try:
        from agents.providers import dispatch_mode as _dm

        return _dm()
    except Exception:
        # Same parse as agents.providers line-one: strip THEN lower.
        return os.getenv("DISPATCH_MODE", "off").strip().lower()


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
    # The tracker offers manual sends for CONFIRMED / READY / DELIVERED —
    # all three need a registered default or populate_template returns "".
    # (The old ORDER_DELIVERED copy said "ready for pickup" — that message
    # belongs to ORDER_READY.)
    "ORDER_CONFIRMED": "Hi {customer_name}, your order {order_number} at {store_name} is confirmed. We will message you as soon as it is ready.",
    "ORDER_READY": "Hi {customer_name}, your order {order_number} from {store_name} is ready for pickup!",
    "ORDER_DELIVERED": "Hi {customer_name}, your order {order_number} from {store_name} has been delivered. Thank you for shopping with us!",
    "GOOGLE_REVIEW_REQUEST": "Hi {customer_name}, thank you for choosing {store_name}! We'd love your feedback. Please leave us a review: {review_link}",
    "WALKOUT_RECOVERY": "Hi {customer_name}, you recently visited {store_name} and tried {frame_names}. We'd love to help you find the perfect pair! Visit us again for a special {discount_percent}% offer. Valid till {validity_date}.",
    "REFERRAL_INVITE": "Hi {customer_name}, share the gift of clear vision! Give your friends and family this referral code: {referral_code}. They get {referee_reward} off their first purchase, and you earn {referrer_reward} in store credit!",
    "NPS_SURVEY": "Hi {customer_name}, how was your experience at {store_name}? Rate us 1-10: {survey_link}. Your feedback helps us serve you better!",
}


def _template_content(template_id: str) -> str:
    """The wording the server sends for this template: an ENABLED, non-empty
    owner-saved override (Settings -> Notification Templates) else the
    hard-coded TEMPLATES default (fail-soft to the default)."""
    default = TEMPLATES.get(template_id, "")
    try:
        from api.services.notification_templates import resolve_template

        content, _ = resolve_template(
            template_id=template_id,
            trigger_event=template_id,
            default_content=default,
        )
    except Exception:  # noqa: BLE001 - resolver is fail-soft; keep the default
        content = default
    return content


def unfilled_placeholders(content: str, variables: dict) -> list:
    """The {placeholders} in `content` that `variables` does not fill. A
    malformed brace pattern counts as unfilled: it cannot be rendered into a
    clean message either."""
    import string

    missing = []
    try:
        for _lit, field, _spec, _conv in string.Formatter().parse(content or ""):
            if field is None:
                continue
            name = field.split(".")[0].split("[")[0]
            if not name or name not in variables:
                missing.append(field or "{}")
    except ValueError:
        missing.append("malformed braces")
    return missing


def populate_template(template_id: str, variables: dict) -> str:
    """Fill template variables. Returns the formatted message.

    An owner-edited template (Settings -> Notification Templates) overrides the
    hard-coded default when an ENABLED, non-empty saved doc matches this
    template_id; otherwise the hard-coded TEMPLATES default is used (fail-soft,
    so a missing/disabled/empty override never blanks a message).

    A placeholder the variables do not fill is left in the text here;
    send_notification REFUSES to queue such a message (D56)."""
    content = _template_content(template_id)
    if not content:
        return f"[Template {template_id} not found]"
    try:
        return content.format(**variables)
    except (KeyError, IndexError, ValueError) as e:
        logger.warning("Missing template variable %s for %s", e, template_id)
        return content


# D56 -- THE one consent classification (queue writer AND MEGAPHONE drain).
# Templates whose content is promotional whatever category a caller stamps on
# them: a SERVICE / omitted category must never turn a birthday wish into a
# message that ignores STOP (Codex 1419 round 1).
PROMOTIONAL_TEMPLATES = frozenset({"BIRTHDAY_WISH", "WALKOUT_RECOVERY", "REFERRAL_INVITE"})
# Recall reminders. D56: "a reminder tied to a specific order, Rx or
# appointment is transactional; anything else ... is promotional". So these are
# transactional ONLY as a REMINDER carrying one order / Rx / appointment (type
# AND id); a SERVICE / OTP / omitted stamp never exempts them. Today every door
# that sends them is promotional: the hand-sent Rx reminder is MARKETING, the
# CL reorder door uses cl_subscription, a reminder RULE row is reminder_rule.
RECALL_TEMPLATES = frozenset(
    {"PRESCRIPTION_EXPIRY", "ANNUAL_CHECKUP_REMINDER", "CL_REORDER_REMINDER"}
)
# A REMINDER is transactional only when it is about ONE specific order,
# prescription or appointment (D56); anything else is promotional.
_SPECIFIC_REMINDER_ENTITIES = frozenset({"order", "prescription", "appointment"})


def consent_basis_for(
    category: str,
    template_id: str = None,
    related_entity_type: str = None,
    related_entity_id: str = None,
) -> str:
    """Why this message is permitted under DLT/TRAI -- recorded on the row and
    re-derived by the drain right before the provider call (D56):

      * a PROMOTIONAL_TEMPLATES template, or a row produced by a reminder RULE
        (a segment, never one specific order/Rx/appointment): marketing_consent;
      * REMINDER tied to one order / prescription / appointment (entity type
        AND id): transactional -- including a RECALL_TEMPLATES template;
      * a RECALL_TEMPLATES template any other way: marketing_consent;
      * OTP / SERVICE / TRANSACTIONAL: transactional (allowed even to
        marketing opt-outs: order ready, repair ready, review/NPS, OTPs);
      * anything else (MARKETING, PROMOTIONAL, untied REMINDER, none):
        marketing_consent.
    """
    cat = (category or "").upper()
    if (template_id or "") in PROMOTIONAL_TEMPLATES:
        return "marketing_consent"
    if (related_entity_type or "") == "reminder_rule":
        return "marketing_consent"
    if (
        cat == "REMINDER"
        and (related_entity_type or "") in _SPECIFIC_REMINDER_ENTITIES
        and related_entity_id
    ):
        return "transactional"
    if (template_id or "") in RECALL_TEMPLATES:
        return "marketing_consent"
    if cat in ("OTP", "SERVICE", "TRANSACTIONAL"):
        return "transactional"
    return "marketing_consent"


def _default_consent_basis(category: str) -> str:
    """Category-only view of consent_basis_for (kept for older callers)."""
    return consent_basis_for(category)


def queue_notification_row(
    *,
    store_id: str,
    customer_id: str,
    customer_phone: str,
    customer_name: str,
    template_id: str,
    channel: str,
    message: str,
    category: str = "SERVICE",
    triggered_by: str = "auto",
    related_entity_type: str = None,
    related_entity_id: str = None,
    consent_basis: str = None,
    extra: dict = None,
) -> dict:
    """Build + persist ONE notification_logs queue row (status PENDING).

    THE one writer of the queue-row shape - send_notification (the async door
    every flow queues through) and queue_sms_fallback (the WhatsApp-FAILED ->
    SMS fallback) both call this, so the row shape cannot drift between them.
    `extra` fields are $set verbatim (dlt_template_id, dedupe_ref,
    customer_email, subject...). Sync + fail-soft; returns the row dict (with
    `dispatched=False`); raises never - a storage error still returns the
    built row so callers keep their honest PENDING contract.
    """
    mode = _dispatch_mode()
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
        "created_at": utc_now_iso(),
        "sent_at": None,
        "delivered_at": None,
        "failure_reason": None,
        # --- DLT / TRAI per-message audit fields (additive) ---
        "pe_id": DLT_PE_ID,
        # The stricter of what the caller asked for and THE rule (D56): a
        # caller can never downgrade a promotional message to transactional.
        "consent_basis": (
            "marketing_consent"
            if consent_basis_for(
                category, template_id, related_entity_type, related_entity_id
            )
            == "marketing_consent"
            else (consent_basis or "transactional")
        ),
        "provider_msg_id": None,  # set by the drain/provider once dispatched
        "delivery_status": "QUEUED",  # advances QUEUED->SENT->DELIVERED via DLR webhook
        "dispatch_mode": mode,  # the mode in effect when queued
    }
    if extra:
        notification.update(extra)

    db = _get_db()
    # NOTE: _get_db() returns a RAW pymongo Database on the happy path (Mongo up),
    # and pymongo Database.__bool__ raises NotImplementedError -- so `if db:` would
    # crash every outbound-comms call (BUG-032). Compare with None explicitly.
    #
    # `persisted` is the durable verdict (Codex 1419 round 1: a swallowed insert
    # failure used to read as "queued"): True only when the row is on disk.
    # `duplicate` is True when the unique dedupe_ref index refused the row
    # because the same message is ALREADY queued (another door, an earlier try).
    persisted = False
    duplicate = False
    if db is not None:
        coll = None
        try:
            coll = db.get_collection("notification_logs")
            coll.insert_one(notification)
            persisted = True
            logger.info(
                "Notification queued (status=PENDING, mode=%s): %s -> %s (%s)",
                mode,
                template_id,
                customer_phone,
                channel,
            )
        except Exception as e:  # noqa: BLE001
            from pymongo.errors import DuplicateKeyError

            if isinstance(e, DuplicateKeyError):
                duplicate = True
            else:
                logger.warning("Failed to log notification: %s", e)
                # Lost acknowledgement: the write may have landed and only the
                # reply was lost. Read it back by its own id before saying no.
                try:
                    if coll is not None and coll.find_one(
                        {"notification_id": notification["notification_id"]},
                        {"_id": 1},
                    ):
                        persisted = True
                except Exception:  # noqa: BLE001
                    pass

    result = dict(notification)
    result.pop("_id", None)  # insert_one mutates the dict with an ObjectId
    result["dispatched"] = False
    result["persisted"] = persisted
    if duplicate:
        result["duplicate"] = True
    return result


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
    customer_email: str = None,
    subject: str = None,
    extra: dict = None,
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

    # Short-URL wrapping for review-request + recall links: ONE wrap site,
    # because every one of these flows queues through this door. Each http(s)
    # link variable is wrapped via MSG91's short-URL API so the click comes
    # back as a message_events "clicked" row (the first conversion metric of
    # the ads funnel). Dark-safe: agents.providers.shorten_url returns the
    # link UNTOUCHED unless DISPATCH_MODE is armed AND MSG91 creds exist, and
    # passes the original through on any provider failure -- so with the gate
    # off the queued message is byte-identical to before this feature.
    if template_id in SHORTURL_FLOWS:
        for _var_name, _var_value in list(variables.items()):
            if isinstance(_var_value, str) and _var_value.startswith(
                ("http://", "https://")
            ):
                try:
                    from agents.providers import shorten_url

                    variables[_var_name] = await shorten_url(_var_value)
                except Exception as _short_exc:  # noqa: BLE001
                    logger.debug("shorturl wrap skipped: %s", _short_exc)

    # Build message from template. D56: a message with an unfilled
    # placeholder is NEVER queued (the customer would read "{order_number}").
    content = _template_content(template_id)
    missing = unfilled_placeholders(content, variables) if content else []
    # The sending shop's own name / phone are filled from its store record when
    # the caller did not pass them (campaign and reminder segments carry only
    # the customer's name). A store that cannot be read leaves them missing.
    if {"store_name", "store_phone"} & set(missing) and store_id:
        try:
            _db = _get_db()
            store = (
                _db.get_collection("stores").find_one(
                    {"store_id": store_id}, {"_id": 0, "name": 1, "store_name": 1, "phone": 1}
                )
                if _db is not None
                else None
            ) or {}
        except Exception:  # noqa: BLE001
            store = {}
        name = store.get("store_name") or store.get("name")
        if name and "store_name" in missing:
            variables["store_name"] = name
        if store.get("phone") and "store_phone" in missing:
            variables["store_phone"] = store["phone"]
        missing = unfilled_placeholders(content, variables)
    if missing:
        logger.warning(
            "Notification %s NOT queued: unfilled placeholder(s) %s",
            template_id,
            missing,
        )
        return {
            "notification_id": None,
            "template_id": template_id,
            "customer_id": customer_id,
            "status": "REFUSED",
            "refused_reason": "unfilled_placeholder",
            "missing_variables": missing,
            "dispatched": False,
            "persisted": False,
        }
    message = populate_template(template_id, variables)

    # EMAIL-channel rows additionally carry the address + subject so the drain
    # can dispatch them via the email transport; absent for the phone channels
    # (row shape unchanged for every pre-existing caller).
    # `extra` fields land on the row in the SAME insert (campaign_id,
    # dedupe_ref...): no second write that could fail after the queue row.
    extra = dict(extra or {})
    if customer_email:
        extra["customer_email"] = customer_email
    if subject:
        extra["subject"] = subject

    # Persist the PENDING row via THE one queue-row writer.
    return queue_notification_row(
        store_id=store_id,
        customer_id=customer_id,
        customer_phone=customer_phone,
        customer_name=customer_name,
        template_id=template_id,
        channel=channel,
        message=message,
        category=category,
        triggered_by=triggered_by,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
        consent_basis=consent_basis,
        extra=extra or None,
    )


def queue_sms_fallback(provider_message_id: str, db=None) -> str:
    """Queue ONE DLT SMS fallback for a WhatsApp send that MSG91 reported
    FAILED. Called by message_events.apply_delivery_report when a `failed`
    spine event is RECORDED (the spine's per-event dedupe already collapses
    provider retries). Returns an honest verdict string:

        "queued" | "duplicate" | "skipped:<reason>"

    Guards, in order:
      - the FAILED report must match a notification_logs row (the outbound
        send stamped provider_msg_id) whose channel is WhatsApp;
      - the flow must be utility-class (SMS_FALLBACK_FLOWS) - marketing and
        auth flows never fan out to SMS;
      - the flow must have an owner-mapped DLT template id in the template
        registry (resolve_sms_fallback; no seed, no guessing) - unmapped
        flows refuse honestly;
      - ONE fallback per provider_message_id, ever: `dedupe_ref` is checked
        AND backstopped by a unique partial index, so a re-recorded failure
        can never fan out into multiple SMS.

    DARK = NOTHING: with DISPATCH_MODE off no real send happens, so MSG91
    never reports a failure and no event ever reaches this function.
    Never raises.
    """
    pid = str(provider_message_id or "").strip()
    if not pid:
        return "skipped:no_provider_message_id"

    if db is None:
        db = _get_db()
    if db is None:
        return "skipped:storage_unavailable"

    try:
        coll = db.get_collection("notification_logs")
        row = coll.find_one(
            {"$or": [{"provider_msg_id": pid}, {"provider_id": pid}]}
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[SMS_FALLBACK] lookup failed: %s", e)
        return "skipped:storage_unavailable"
    if not row:
        return "skipped:no_matching_send"
    if str(row.get("channel") or "").strip().lower() != "whatsapp":
        return "skipped:not_whatsapp"

    flow_key = row.get("template_id")
    if flow_key not in SMS_FALLBACK_FLOWS:
        return "skipped:not_a_fallback_flow"

    from api.services.notification_templates import resolve_sms_fallback

    dlt_template_id = resolve_sms_fallback(flow_key)
    if not dlt_template_id:
        logger.info(
            "[SMS_FALLBACK] flow %s has no sms_template_id mapped - not "
            "queueing (map it under Settings -> Notifications -> Templates)",
            flow_key,
        )
        return "skipped:no_sms_template"

    phone = row.get("customer_phone") or row.get("phone") or ""
    message = row.get("message") or ""
    if not phone or not message:
        return "skipped:missing_phone_or_message"

    dedupe_ref = f"sms_fallback:{pid}"
    try:
        # Unique partial backstop so a multi-worker race cannot double-queue.
        try:
            coll.create_index(
                "dedupe_ref",
                unique=True,
                partialFilterExpression={"dedupe_ref": {"$type": "string"}},
                name="uniq_notification_dedupe_ref",
            )
        except Exception:  # noqa: BLE001 - index ensure is best-effort
            pass
        if coll.find_one({"dedupe_ref": dedupe_ref}):
            return "duplicate"
    except Exception as e:  # noqa: BLE001
        logger.warning("[SMS_FALLBACK] dedupe check failed: %s", e)
        return "skipped:storage_unavailable"

    # queue_notification_row never raises; if the unique dedupe_ref index
    # rejects a concurrent duplicate insert, the writer logs it and no second
    # row exists - the ONE-fallback invariant holds regardless of the verdict.
    queue_notification_row(
        store_id=row.get("store_id"),
        customer_id=row.get("customer_id"),
        customer_phone=phone,
        customer_name=row.get("customer_name") or "",
        template_id=flow_key,
        channel="SMS",
        message=message,
        category=row.get("category") or "SERVICE",
        triggered_by="sms_fallback",
        related_entity_type="notification",
        related_entity_id=row.get("notification_id"),
        consent_basis="transactional",
        extra={
            "dlt_template_id": dlt_template_id,
            "dedupe_ref": dedupe_ref,
        },
    )
    logger.info(
        "[SMS_FALLBACK] queued ONE DLT SMS for flow %s (failed WhatsApp %s)",
        flow_key,
        pid,
    )
    return "queued"
