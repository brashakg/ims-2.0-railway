"""
IMS 2.0 - Notification template resolver
========================================
The Settings -> Notification Templates tab lets an owner EDIT the wording of a
message (PUT /settings/notifications/templates/{id} -> notification_templates
collection). Until now that saved text was read ONLY by the settings GETs and
never reached an actual recipient -- every send path used a hard-coded string,
so an owner's edits had no effect.

This module is the bridge: given a template key (template_id) and/or a
trigger_event, it returns the SAVED content/subject when a matching template
doc is ENABLED and non-empty, otherwise it falls back to the caller's
hard-coded default.

Safety contract (do NOT silently suppress):
- A disabled, missing, or empty saved template MUST fall back to the
  hard-coded default. We never return an empty body just because a row exists
  and is_enabled=False -- a critical task escalation must still go out. The
  is_enabled flag is honoured by the *settings UI / drain* for OPTIONAL
  customer marketing; here it only decides "use the override text or the
  default text", never "send nothing".
- Fail-soft: any DB/import error -> return the default. Resolution never raises.

Placeholder substitution mirrors the existing defaults: simple str.format with
{placeholder} tokens. A missing variable returns the (un-substituted) chosen
template rather than raising, matching notification_service.populate_template.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _get_db():
    try:
        from database.connection import get_db

        db = get_db()
        if db and getattr(db, "is_connected", False):
            return db
    except Exception:  # noqa: BLE001
        return None
    return None


def _find_saved_template(
    template_id: Optional[str], trigger_event: Optional[str]
) -> Optional[dict]:
    """Return the saved notification_templates doc matching template_id OR
    trigger_event, preferring an exact template_id match. None when no DB or no
    match. Never raises."""
    db = _get_db()
    if db is None:
        return None
    try:
        coll = db.get_collection("notification_templates")
    except Exception:  # noqa: BLE001
        return None

    # Prefer an exact template_id match, then fall back to trigger_event.
    for query in (
        {"template_id": template_id} if template_id else None,
        {"trigger_event": trigger_event} if trigger_event else None,
    ):
        if not query:
            continue
        try:
            doc = coll.find_one(query)
        except Exception:  # noqa: BLE001
            doc = None
        if doc:
            return doc
    return None


def _is_usable(doc: Optional[dict], field: str) -> bool:
    """A saved override is usable for `field` only when the doc is ENABLED and
    the field has non-empty content. (is_enabled defaults True if the field is
    absent on an older row.)"""
    if not doc:
        return False
    if not doc.get("is_enabled", True):
        return False
    value = doc.get(field)
    return isinstance(value, str) and value.strip() != ""


def resolve_template(
    *,
    template_id: Optional[str] = None,
    trigger_event: Optional[str] = None,
    default_content: str,
    default_subject: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Resolve (content, subject) for a message.

    Returns the SAVED override when an enabled, non-empty doc matches
    template_id (preferred) or trigger_event; otherwise the supplied defaults.
    The content and subject are resolved independently so an override may
    customise the body while leaving the subject on its default (or vice versa).

    Never raises -- any failure yields the defaults.
    """
    try:
        doc = _find_saved_template(template_id, trigger_event)
    except Exception:  # noqa: BLE001
        doc = None

    content = default_content
    subject = default_subject
    if _is_usable(doc, "content"):
        content = doc["content"]
    if default_subject is not None and _is_usable(doc, "subject"):
        subject = doc["subject"]
    return content, subject


# ---------------------------------------------------------------------------
# WhatsApp template registry (Coexistence build, piece 3)
# ---------------------------------------------------------------------------
# Maps each outbound WhatsApp FLOW to the MSG91/Meta template it sends:
# {flow_key: {template_name, language, category, variables(ordered)}}.
#
# The SEED below is code-versioned and mirrors, byte for byte, the template
# name each flow puts on the wire TODAY (the flow key itself for queued
# notification_service flows -- the drain passes row.template_id as the
# payload name -- and "generic_text" for the three direct-call flows that
# used to pass template_id=None). So a fresh deploy behaves identically.
# As MSG91 approves real templates, the owner replaces these names via
# Settings -> Notifications -> Templates (the wa_* fields on the SAME
# notification_templates doc); the DB row then wins over the seed.
#
# A flow key with NO row here and NO DB row REFUSES to send (the send door
# in agents.providers returns FAILED naming the flow) -- a guessed template
# name is never sent. `variables` is the ORDERED Meta body-variable list;
# empty means the whole message rides body_1 (today's shape).

WA_TEMPLATE_SEED: dict = {
    # --- flows queued through notification_service (drain -> send door) ---
    "PRESCRIPTION_EXPIRY": {"template_name": "PRESCRIPTION_EXPIRY", "language": "en", "category": "utility", "variables": ["customer_name", "store_name", "expiry_date", "store_phone"]},
    "BIRTHDAY_WISH": {"template_name": "BIRTHDAY_WISH", "language": "en", "category": "marketing", "variables": ["customer_name", "store_name"]},
    "ANNUAL_CHECKUP_REMINDER": {"template_name": "ANNUAL_CHECKUP_REMINDER", "language": "en", "category": "marketing", "variables": ["customer_name", "store_name"]},
    "ORDER_DELIVERED": {"template_name": "ORDER_DELIVERED", "language": "en", "category": "utility", "variables": ["customer_name", "order_number", "store_name"]},
    # The optical till's "WhatsApp order receipt" queues this flow key at SALE
    # completion, but the key had no seed row -- so the drain refused it as an
    # unmapped flow in EVERY dispatch mode and the button could never work.
    # Same shape as ORDER_DELIVERED; the owner maps the real approved template
    # name in Settings > Notifications > Templates before arming dispatch.
    "ORDER_CONFIRMED": {"template_name": "ORDER_CONFIRMED", "language": "en", "category": "utility", "variables": ["customer_name", "order_number", "store_name"]},
    "GOOGLE_REVIEW_REQUEST": {"template_name": "GOOGLE_REVIEW_REQUEST", "language": "en", "category": "utility", "variables": ["customer_name", "store_name", "review_link"]},
    "WALKOUT_RECOVERY": {"template_name": "WALKOUT_RECOVERY", "language": "en", "category": "marketing", "variables": ["customer_name", "store_name", "frame_names", "discount_percent", "validity_date"]},
    "REFERRAL_INVITE": {"template_name": "REFERRAL_INVITE", "language": "en", "category": "marketing", "variables": ["customer_name", "referral_code", "referee_reward", "referrer_reward"]},
    "NPS_SURVEY": {"template_name": "NPS_SURVEY", "language": "en", "category": "utility", "variables": ["customer_name", "store_name", "survey_link"]},
    "CL_REORDER_REMINDER": {"template_name": "CL_REORDER_REMINDER", "language": "en", "category": "utility", "variables": []},
    "RX_PORTAL_OTP": {"template_name": "RX_PORTAL_OTP", "language": "en", "category": "auth", "variables": []},
    "POOL_REDEEM_OTP": {"template_name": "POOL_REDEEM_OTP", "language": "en", "category": "auth", "variables": []},
    "repair_ready": {"template_name": "repair_ready", "language": "en", "category": "utility", "variables": []},
    # --- direct-call flows (previously template_id=None -> "generic_text") ---
    "WORKSHOP_READY": {"template_name": "generic_text", "language": "en", "category": "utility", "variables": []},
    "TASK_ESCALATION": {"template_name": "generic_text", "language": "en", "category": "utility", "variables": []},
    "WA_INTENT_REPLY": {"template_name": "generic_text", "language": "en", "category": "utility", "variables": []},
}

_WA_CATEGORIES = ("utility", "marketing", "auth")


def resolve_wa_template(flow_key: Optional[str]) -> Optional[dict]:
    """THE one WhatsApp-template lookup for the send door.

    Returns {template_name, language, category, variables} for `flow_key`,
    resolving the owner-edited DB row (wa_template_name etc. on the
    notification_templates doc) over the code seed. Returns None when the
    flow is unmapped in BOTH -- the send door then refuses honestly instead
    of sending a guessed name. Never raises.
    """
    if not flow_key:
        return None
    seed = WA_TEMPLATE_SEED.get(flow_key)
    try:
        doc = _find_saved_template(flow_key, None)
    except Exception:  # noqa: BLE001 - registry read is fail-soft
        doc = None
    db_name = ""
    if isinstance(doc, dict):
        db_name = str(doc.get("wa_template_name") or "").strip()
    if db_name:
        seed = seed or {}
        category = str(doc.get("wa_category") or seed.get("category") or "utility").strip().lower()
        if category not in _WA_CATEGORIES:
            category = "utility"
        variables = doc.get("wa_variables")
        if not isinstance(variables, list):
            variables = seed.get("variables") or []
        return {
            "template_name": db_name,
            "language": str(doc.get("wa_language") or seed.get("language") or "en").strip() or "en",
            "category": category,
            "variables": [str(v) for v in variables],
        }
    if seed:
        return dict(seed)
    return None


def resolve_sms_fallback(flow_key: Optional[str]) -> Optional[str]:
    """THE one SMS-fallback template lookup (DLT template id) for a flow.

    The wa_* registry fields gained an sms_* sibling: `sms_template_id` on the
    SAME notification_templates doc holds the DLT-approved SMS template id the
    flow falls back to when its WhatsApp send is reported FAILED. There is NO
    code seed on purpose - a DLT template id is a real operator registration
    that cannot be guessed, so an unmapped flow returns None and the fallback
    refuses honestly (nothing is queued). Never raises.
    """
    if not flow_key:
        return None
    try:
        doc = _find_saved_template(flow_key, None)
    except Exception:  # noqa: BLE001 - registry read is fail-soft
        doc = None
    if isinstance(doc, dict):
        template_id = str(doc.get("sms_template_id") or "").strip()
        if template_id:
            return template_id
    return None


def wa_registry_report() -> dict:
    """Per-flow mapping report for the messaging preflight (names only).

    {flow_key: {"template_name": ..., "source": "db"|"seed",
                "seed_default": bool}} -- seed_default marks a flow still on
    its code-seeded placeholder name (MSG91 will reject it until the owner
    maps the real approved template name). Never raises.
    """
    out: dict = {}
    for key in sorted(WA_TEMPLATE_SEED):
        resolved = resolve_wa_template(key) or {}
        seed_name = WA_TEMPLATE_SEED[key]["template_name"]
        name = resolved.get("template_name", seed_name)
        source = "seed"
        try:
            doc = _find_saved_template(key, None)
            if isinstance(doc, dict) and str(doc.get("wa_template_name") or "").strip():
                source = "db"
        except Exception:  # noqa: BLE001
            pass
        out[key] = {
            "template_name": name,
            "source": source,
            "seed_default": source == "seed",
        }
    return out


def render(template: str, variables: Optional[dict]) -> str:
    """Apply simple {placeholder} substitution. A missing variable returns the
    template unchanged (matches notification_service.populate_template's
    fail-soft behaviour) rather than raising."""
    if not variables:
        return template
    try:
        return template.format(**variables)
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning("Template substitution skipped (missing var): %s", exc)
        return template


def resolve_and_render(
    *,
    template_id: Optional[str] = None,
    trigger_event: Optional[str] = None,
    default_content: str,
    variables: Optional[dict] = None,
) -> str:
    """Convenience: resolve the content (override or default) then substitute
    {placeholder} variables. Returns the final message string."""
    content, _ = resolve_template(
        template_id=template_id,
        trigger_event=trigger_event,
        default_content=default_content,
    )
    return render(content, variables)
