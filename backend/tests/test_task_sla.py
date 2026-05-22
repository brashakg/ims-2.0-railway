"""
Task SLA + escalation timing tests (Tasks/SOP Phase 1)
======================================================
Pure tests of services.task_sla -- the per-priority SLA matrix and the
should_escalate() decision (ack clock + overdue grace). No DB.
"""

import os
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

from api.services.task_sla import (  # noqa: E402
    DEFAULT_SLA,
    canon_source,
    canon_status,
    should_escalate,
    sla_for,
)

NOW = datetime(2026, 5, 22, 12, 0, 0)


# --- canon helpers ----------------------------------------------------------


def test_canon_status_normalizes_casing_and_synonyms():
    assert canon_status("open") == "OPEN"
    assert canon_status("in progress") == "IN_PROGRESS"
    assert canon_status("in-progress") == "IN_PROGRESS"
    assert canon_status("done") == "COMPLETED"
    assert canon_status("complete") == "COMPLETED"
    assert canon_status("Cancelled") == "CANCELLED"
    assert canon_status("canceled") == "CANCELLED"
    assert canon_status("ESCALATED") == "ESCALATED"
    assert canon_status(None) == ""


def test_canon_source_maps_legacy_types():
    assert canon_source("manual") == "USER"
    assert canon_source("sop") == "SOP"
    assert canon_source("system") == "SYSTEM"
    assert canon_source("USER") == "USER"
    assert canon_source(None) == "USER"
    assert canon_source("garbage") == "USER"


# --- sla_for ----------------------------------------------------------------


def test_sla_for_known_priorities():
    assert sla_for("P0") == {"ack_minutes": 15, "grace_minutes": 30}
    assert sla_for("P3") == {"ack_minutes": 1440, "grace_minutes": 4320}
    assert sla_for("p4") == DEFAULT_SLA["P4"]  # case-insensitive


def test_sla_for_unknown_priority_falls_back_to_p3():
    assert sla_for("P9") == DEFAULT_SLA["P3"]
    assert sla_for(None) == DEFAULT_SLA["P3"]


def test_sla_config_override_with_partial_row():
    override = {"P3": {"ack_minutes": 1}}  # grace omitted -> default grace
    row = sla_for("P3", override)
    assert row["ack_minutes"] == 1
    assert row["grace_minutes"] == DEFAULT_SLA["P3"]["grace_minutes"]


# --- should_escalate: terminal/escalated never fire -------------------------


def test_completed_never_escalates():
    task = {"status": "COMPLETED", "priority": "P0", "due_at": NOW - timedelta(days=30)}
    assert should_escalate(task, now=NOW) == (False, "")


def test_cancelled_never_escalates():
    task = {"status": "CANCELLED", "priority": "P0", "due_at": NOW - timedelta(days=30)}
    assert should_escalate(task, now=NOW)[0] is False


def test_already_escalated_does_not_re_escalate_in_phase1():
    task = {"status": "ESCALATED", "priority": "P0", "due_at": NOW - timedelta(days=30)}
    assert should_escalate(task, now=NOW)[0] is False


# --- ack clock --------------------------------------------------------------


def test_open_past_ack_window_escalates():
    # P2 ack window is 240 min; created 5h ago.
    task = {"status": "OPEN", "priority": "P2", "created_at": NOW - timedelta(hours=5)}
    flag, reason = should_escalate(task, now=NOW)
    assert flag is True
    assert "acknowledged" in reason.lower()


def test_open_within_ack_window_does_not_escalate():
    task = {"status": "OPEN", "priority": "P2", "created_at": NOW - timedelta(hours=1)}
    assert should_escalate(task, now=NOW)[0] is False


def test_p0_ack_clock_is_tighter_than_p3():
    created = NOW - timedelta(minutes=20)
    p0 = {"status": "OPEN", "priority": "P0", "created_at": created}  # ack 15m -> breach
    p3 = {"status": "OPEN", "priority": "P3", "created_at": created}  # ack 1d -> fine
    assert should_escalate(p0, now=NOW)[0] is True
    assert should_escalate(p3, now=NOW)[0] is False


def test_in_progress_does_not_use_ack_clock():
    # Acknowledged (IN_PROGRESS) tasks only escalate on the overdue clock, not
    # the ack clock -- even if created long ago, no due date means no escalate.
    task = {"status": "IN_PROGRESS", "priority": "P0", "created_at": NOW - timedelta(days=5)}
    assert should_escalate(task, now=NOW)[0] is False


# --- overdue clock ----------------------------------------------------------


def test_overdue_past_grace_escalates():
    # P1 grace 240 min; due 5h ago -> due+4h is 1h in the past -> breach.
    task = {"status": "IN_PROGRESS", "priority": "P1", "due_at": NOW - timedelta(hours=5)}
    flag, reason = should_escalate(task, now=NOW)
    assert flag is True
    assert "overdue" in reason.lower()


def test_overdue_within_grace_does_not_escalate():
    # P1 grace 4h; due 1h ago -> due+4h is 3h in the future -> fine.
    task = {"status": "IN_PROGRESS", "priority": "P1", "due_at": NOW - timedelta(hours=1)}
    assert should_escalate(task, now=NOW)[0] is False


# --- tolerance: legacy field names + types ----------------------------------


def test_legacy_lowercase_status_and_due_date_field():
    task = {"status": "open", "priority": "P3", "due_date": NOW - timedelta(days=10)}
    # P3 grace 3 days; due 10 days ago -> breach (overdue), via legacy due_date.
    assert should_escalate(task, now=NOW)[0] is True


def test_iso_string_due_at_is_parsed():
    task = {"status": "IN_PROGRESS", "priority": "P3", "due_at": "2026-05-01T00:00:00"}
    # 21 days before NOW, grace 3 days -> breach.
    assert should_escalate(task, now=NOW)[0] is True


def test_iso_string_with_z_suffix_is_parsed():
    task = {"status": "IN_PROGRESS", "priority": "P0", "due_at": "2026-05-22T10:00:00Z"}
    # due 2h before NOW, P0 grace 30m -> breach.
    assert should_escalate(task, now=NOW)[0] is True


def test_sla_config_override_changes_decision():
    task = {"status": "OPEN", "priority": "P3", "created_at": NOW - timedelta(minutes=5)}
    # Default P3 ack is 1 day -> no escalate.
    assert should_escalate(task, now=NOW)[0] is False
    # Override ack to 1 min -> escalate.
    override = {"P3": {"ack_minutes": 1, "grace_minutes": 4320}}
    assert should_escalate(task, now=NOW, sla_config=override)[0] is True


# --- edge cases -------------------------------------------------------------


def test_no_due_no_created_does_not_escalate():
    task = {"status": "OPEN", "priority": "P0"}
    assert should_escalate(task, now=NOW) == (False, "")


def test_unparseable_due_is_ignored():
    task = {"status": "IN_PROGRESS", "priority": "P0", "due_at": "not-a-date"}
    assert should_escalate(task, now=NOW)[0] is False
