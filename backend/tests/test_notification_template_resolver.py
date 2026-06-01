"""
IMS 2.0 - Notification template resolver
========================================
The Settings -> Notification Templates editor saves message wording to the
`notification_templates` collection. Until the resolver was added, that saved
text never reached a recipient -- the send paths used hard-coded strings. These
tests verify the bridge:

  - An ENABLED, non-empty saved template OVERRIDES the hard-coded default.
  - A missing / disabled / empty saved template FALLS BACK to the default
    (a disabled row must NEVER blank a critical message).
  - {placeholder} substitution still applies to whichever text is chosen.

The pure-fallback cases need no DB. The override / disabled cases insert a row
into `notification_templates`; they skip when no DB is connected (local runs).
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

import pytest  # noqa: E402

from api.services.notification_templates import (  # noqa: E402
    resolve_template,
    resolve_and_render,
    render,
)


# --- pure (no DB needed): absent saved template -> default ------------------


def test_absent_template_returns_default():
    content, subject = resolve_template(
        template_id="DOES_NOT_EXIST",
        trigger_event="DOES_NOT_EXIST",
        default_content="DEFAULT BODY {x}",
        default_subject="DEFAULT SUBJECT",
    )
    assert content == "DEFAULT BODY {x}"
    assert subject == "DEFAULT SUBJECT"


def test_render_substitutes_and_is_fail_soft():
    assert render("Hi {name}", {"name": "Avi"}) == "Hi Avi"
    # Missing variable -> template returned unchanged (never raises).
    assert render("Hi {name}", {}) == "Hi {name}"


def test_resolve_and_render_default_path():
    out = resolve_and_render(
        template_id="NOPE",
        default_content="Order {order_id} ready",
        variables={"order_id": "ORD-1"},
    )
    assert out == "Order ORD-1 ready"


# --- DB-backed: saved enabled template overrides ----------------------------


def _coll(client):
    """Live notification_templates collection (conftest clears it per-test)."""
    from database.connection import get_db

    db = get_db()
    if not (db and getattr(db, "is_connected", False)):
        return None
    return db.get_collection("notification_templates")


def test_enabled_saved_template_overrides_default(client):
    coll = _coll(client)
    if coll is None:
        pytest.skip("notification_templates collection unavailable (no DB)")
    coll.insert_one(
        {
            "template_id": "PRESCRIPTION_EXPIRY",
            "trigger_event": "PRESCRIPTION_EXPIRY",
            "is_enabled": True,
            "content": "Custom: {customer_name} renew your Rx!",
            "subject": None,
        }
    )
    content, _ = resolve_template(
        template_id="PRESCRIPTION_EXPIRY",
        trigger_event="PRESCRIPTION_EXPIRY",
        default_content="Default reminder {customer_name}",
    )
    assert content == "Custom: {customer_name} renew your Rx!"
    # And it renders with variables.
    rendered = render(content, {"customer_name": "Avi"})
    assert rendered == "Custom: Avi renew your Rx!"


def test_disabled_saved_template_falls_back_to_default(client):
    coll = _coll(client)
    if coll is None:
        pytest.skip("notification_templates collection unavailable (no DB)")
    coll.insert_one(
        {
            "template_id": "TASK_ESCALATION_WHATSAPP",
            "trigger_event": "TASK_ESCALATION_WHATSAPP",
            "is_enabled": False,  # disabled -> must NOT suppress; use default
            "content": "This disabled text must not be used",
        }
    )
    content, _ = resolve_template(
        template_id="TASK_ESCALATION_WHATSAPP",
        trigger_event="TASK_ESCALATION_WHATSAPP",
        default_content="DEFAULT escalation body",
    )
    assert content == "DEFAULT escalation body"


def test_empty_saved_template_falls_back_to_default(client):
    coll = _coll(client)
    if coll is None:
        pytest.skip("notification_templates collection unavailable (no DB)")
    coll.insert_one(
        {
            "template_id": "BIRTHDAY_WISH",
            "trigger_event": "BIRTHDAY_WISH",
            "is_enabled": True,
            "content": "   ",  # whitespace-only -> not usable -> default
        }
    )
    content, _ = resolve_template(
        template_id="BIRTHDAY_WISH",
        trigger_event="BIRTHDAY_WISH",
        default_content="Happy Birthday {customer_name}!",
    )
    assert content == "Happy Birthday {customer_name}!"


def test_populate_template_uses_saved_override(client):
    """End-to-end through notification_service.populate_template: a saved,
    enabled override drives the queued message body."""
    coll = _coll(client)
    if coll is None:
        pytest.skip("notification_templates collection unavailable (no DB)")
    coll.insert_one(
        {
            "template_id": "ORDER_DELIVERED",
            "trigger_event": "ORDER_DELIVERED",
            "is_enabled": True,
            "content": "OVERRIDE: {customer_name} pick up {order_number}",
        }
    )
    from api.services.notification_service import populate_template

    msg = populate_template(
        "ORDER_DELIVERED",
        {"customer_name": "Avi", "order_number": "ORD-9", "store_name": "BV"},
    )
    assert msg == "OVERRIDE: Avi pick up ORD-9"
