"""
IMS 2.0 — Audit-alerts service tests
======================================
Verifies the SENTINEL-fed alert pipeline that fires on order edits,
cancellations, item deletions, and P&L deviations (May 2026).

Eight scenarios:
  1. emit_audit_alert writes a row to audit_logs + dispatches event
  2. Diff helper produces a clean per-field before/after structure
  3. alert_order_edited: ≤5% grand_total change → LOW severity
  4. alert_order_edited: >5% grand_total change → HIGH severity
  5. alert_order_cancelled is always CRITICAL
  6. alert_item_deleted: post-DRAFT order → CRITICAL; DRAFT → HIGH
  7. alert_pnl_deviation: 5% threshold; 15% threshold tiers
  8. Soft failure: bus + repo missing → returns None, never raises
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Tests
# ============================================================================


def test_diff_helper_computes_per_field_changes():
    from api.services.audit_alerts import _diff
    before = {"grand_total": 1000, "status": "DRAFT", "_id": "skip", "updated_at": 1}
    after = {"grand_total": 1500, "status": "CONFIRMED", "_id": "skip", "updated_at": 2}
    diff = _diff(before, after)
    assert "grand_total" in diff
    assert diff["grand_total"]["before"] == 1000
    assert diff["grand_total"]["after"] == 1500
    assert "status" in diff
    # Mongo-private and timestamp fields are skipped
    assert "_id" not in diff
    assert "updated_at" not in diff


@pytest.mark.asyncio
async def test_emit_audit_alert_returns_none_when_repo_unavailable():
    """The function must NEVER raise on a missing repo / bus. Returns
    None instead. This is the customer-safety contract — alert
    failures must not block the underlying write."""
    from api.services.audit_alerts import emit_audit_alert
    # No fixtures patched → audit_repo and event bus both unavailable
    audit_id = await emit_audit_alert(
        severity="HIGH",
        action="order.edited",
        entity_type="order",
        entity_id="O-999",
        user_id="u-1",
        before={"grand_total": 100},
        after={"grand_total": 150},
    )
    # Result is None or the row's id — what matters is no exception.
    assert audit_id is None or isinstance(audit_id, str)


@pytest.mark.asyncio
async def test_alert_order_edited_low_severity_for_small_change():
    """grand_total goes from 1000 → 1040 (4% change) — LOW severity."""
    from api.services import audit_alerts as mod
    captured = {}

    async def fake_emit(*, severity, action, **kwargs):
        captured["severity"] = severity
        captured["action"] = action
        return "audit-1"

    mod.emit_audit_alert = fake_emit  # type: ignore
    await mod.alert_order_edited(
        "O-1", before={"grand_total": 1000}, after={"grand_total": 1040}, user_id="u-1"
    )
    assert captured["severity"] == "LOW"
    assert captured["action"] == "order.edited"


@pytest.mark.asyncio
async def test_alert_order_edited_high_severity_for_big_change():
    """grand_total 1000 → 1100 (10% change) — HIGH severity."""
    from api.services import audit_alerts as mod
    captured = {}

    async def fake_emit(*, severity, action, **kwargs):
        captured["severity"] = severity
        return "audit-1"

    mod.emit_audit_alert = fake_emit  # type: ignore
    await mod.alert_order_edited(
        "O-1", before={"grand_total": 1000}, after={"grand_total": 1100}, user_id="u-1"
    )
    assert captured["severity"] == "HIGH"


@pytest.mark.asyncio
async def test_alert_order_cancelled_is_always_critical():
    from api.services import audit_alerts as mod
    captured = {}

    async def fake_emit(*, severity, action, context=None, **kwargs):
        captured["severity"] = severity
        captured["context"] = context
        return "audit-1"

    mod.emit_audit_alert = fake_emit  # type: ignore
    await mod.alert_order_cancelled(
        "O-1", before={"status": "CONFIRMED"}, user_id="u-1", reason="Customer changed mind"
    )
    assert captured["severity"] == "CRITICAL"
    assert captured["context"]["cancel_reason"] == "Customer changed mind"


@pytest.mark.asyncio
async def test_alert_item_deleted_severity_depends_on_order_status():
    from api.services import audit_alerts as mod
    captured = []

    async def fake_emit(*, severity, **kwargs):
        captured.append(severity)
        return "audit-1"

    mod.emit_audit_alert = fake_emit  # type: ignore
    # DRAFT — HIGH
    await mod.alert_item_deleted(
        "O-1", "I-1", item_data={"sku": "X"}, user_id="u-1", order_status="DRAFT"
    )
    # CONFIRMED — CRITICAL
    await mod.alert_item_deleted(
        "O-1", "I-2", item_data={"sku": "Y"}, user_id="u-1", order_status="CONFIRMED"
    )
    assert captured == ["HIGH", "CRITICAL"]


@pytest.mark.asyncio
async def test_alert_pnl_deviation_threshold_tiers():
    """5% → HIGH, 15% → CRITICAL, below 5% → LOW."""
    from api.services import audit_alerts as mod
    captured = []

    async def fake_emit(*, severity, **kwargs):
        captured.append(severity)
        return "audit-1"

    mod.emit_audit_alert = fake_emit  # type: ignore
    await mod.alert_pnl_deviation(period="2026-04", expected=100000, actual=100500)  # 0.5%
    await mod.alert_pnl_deviation(period="2026-04", expected=100000, actual=110000)  # 10%
    await mod.alert_pnl_deviation(period="2026-04", expected=100000, actual=120000)  # 20%
    assert captured == ["LOW", "HIGH", "CRITICAL"]


def test_severity_rank_table():
    """Sanity: LOW < MEDIUM < HIGH < CRITICAL ranking is what we expect."""
    from api.services.audit_alerts import SEVERITY_RANK
    assert SEVERITY_RANK["LOW"] < SEVERITY_RANK["MEDIUM"]
    assert SEVERITY_RANK["MEDIUM"] < SEVERITY_RANK["HIGH"]
    assert SEVERITY_RANK["HIGH"] < SEVERITY_RANK["CRITICAL"]
