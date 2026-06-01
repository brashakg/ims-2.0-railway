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
