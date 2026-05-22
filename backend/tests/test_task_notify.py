"""
Task escalation notification tests (Tasks/SOP Phase 3)
======================================================
Pure tests of services.task_notify -- the in-app notification builder, the
priority mapping, and the WhatsApp quiet-hours gate. No DB / no network.
"""

import os
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.services.task_notify import (  # noqa: E402
    build_escalation_notification,
    escalation_whatsapp_text,
    notification_priority,
    whatsapp_allowed,
)

TASK = {
    "task_id": "TASK-ABC123",
    "title": "Reorder Ray-Ban Aviator stock",
    "priority": "P1",
    "store_id": "BV-BOK-01",
}


# --- notification_priority --------------------------------------------------


def test_priority_mapping():
    assert notification_priority("P0") == "URGENT"
    assert notification_priority("P1") == "HIGH"
    assert notification_priority("P2") == "NORMAL"
    assert notification_priority("P3") == "LOW"
    assert notification_priority("P4") == "LOW"
    assert notification_priority("p1") == "HIGH"  # case-insensitive
    assert notification_priority("weird") == "NORMAL"
    assert notification_priority(None) == "NORMAL"


# --- build_escalation_notification ------------------------------------------


def test_build_notification_shape():
    n = build_escalation_notification(TASK, "user-99", "Overdue past SLA grace (240m)")
    assert n["notification_type"] == "task_escalation"
    assert n["user_id"] == "user-99"
    assert n["entity_type"] == "task"
    assert n["entity_id"] == "TASK-ABC123"
    assert n["action_url"] == "/tasks"
    assert n["priority"] == "HIGH"  # P1
    assert n["status"] == "SENT"
    assert n["channels"] == ["IN_APP"]
    assert n["notification_id"].startswith("NTF-")
    assert isinstance(n["created_at"], datetime)


def test_build_notification_message_includes_title_and_reason():
    n = build_escalation_notification(TASK, "user-99", "Not acknowledged within SLA (60m)")
    assert "Reorder Ray-Ban Aviator stock" in n["title"]
    assert "Reorder Ray-Ban Aviator stock" in n["message"]
    assert "Not acknowledged within SLA (60m)" in n["message"]
    assert "P1" in n["message"]


def test_build_notification_defaults_when_fields_missing():
    n = build_escalation_notification({}, "u1", "reason")
    assert n["user_id"] == "u1"
    assert n["entity_id"] is None
    assert n["priority"] == "LOW"  # default P3 -> LOW


# --- escalation_whatsapp_text -----------------------------------------------


def test_whatsapp_text_contents():
    txt = escalation_whatsapp_text(TASK, "Overdue past SLA grace (240m)")
    assert "Reorder Ray-Ban Aviator stock" in txt
    assert "P1" in txt
    assert "BV-BOK-01" in txt
    assert "Overdue past SLA grace (240m)" in txt


# --- whatsapp_allowed (quiet hours) -----------------------------------------


def _at(hour: int) -> datetime:
    return datetime(2026, 5, 22, hour, 0, 0)


def test_daytime_allows_all_priorities():
    assert whatsapp_allowed("P3", now=_at(14)) is True
    assert whatsapp_allowed("P0", now=_at(14)) is True


def test_quiet_hours_suppress_low_priority():
    assert whatsapp_allowed("P3", now=_at(23)) is False  # late night
    assert whatsapp_allowed("P2", now=_at(3)) is False   # early morning


def test_quiet_hours_overridden_by_emergencies():
    assert whatsapp_allowed("P0", now=_at(23)) is True
    assert whatsapp_allowed("P1", now=_at(3)) is True


def test_quiet_hour_boundaries():
    # 21:00 is quiet (>= 21); 09:00 is allowed (not < 9).
    assert whatsapp_allowed("P3", now=_at(21)) is False
    assert whatsapp_allowed("P3", now=_at(9)) is True
    assert whatsapp_allowed("P3", now=_at(8)) is False  # 08:xx still quiet
    assert whatsapp_allowed("P3", now=_at(20)) is True  # 20:xx still day
