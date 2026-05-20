"""
IMS 2.0 — Audit alerts service
================================

Fires alerts when state-mutating actions land on entities the operator
cares about: order edits, cancellations, item deletions, large
discount approvals, P&L-affecting refunds.

Per the YouTube competitor research (Optical CRM), this maps onto the
"Auto-alerts for order deletions, edits, and P&L changes" feature their
peers ship — but here it's wired through the existing 8-agent runtime:

  emit_audit_alert()
       │
       ├─ writes immutable row to `audit_logs` (always)
       └─ dispatches `audit.alert` event
             │
             ├─ SENTINEL on_event handler — fans out to Slack
             │   (when SLACK_WEBHOOK_URL set + SLACK_ALERT_SEVERITY met)
             └─ persists to `agent_events` audit feed (event bus side)

The function is fail-soft: an alert dispatch failure never blocks the
calling write. Audit IS the safety net, not the alert.

Severities:
  CRITICAL — order cancelled / item deleted post-checkout / refund > ₹1L
  HIGH     — order grand_total changed > 5% post-finalize
  MEDIUM   — discount applied > role's effective cap
  LOW      — line item edited (any other change)

Action codes are short, machine-readable strings. Examples:
  order.cancelled · order.edited · order.item.deleted · order.refund.large
  pnl.deviation · discount.over_cap · price.override
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _diff(before: Optional[Dict], after: Optional[Dict]) -> Dict[str, Dict]:
    """Compute a shallow per-field before/after diff. Skip private
    Mongo `_id`. Useful for the audit row + the event payload."""
    if before is None and after is None:
        return {}
    before = before or {}
    after = after or {}
    keys = set(before.keys()) | set(after.keys())
    diff: Dict[str, Dict] = {}
    for k in sorted(keys):
        if k.startswith("_") or k in {"updated_at", "created_at"}:
            continue
        b, a = before.get(k), after.get(k)
        if b != a:
            diff[k] = {"before": b, "after": a}
    return diff


async def emit_audit_alert(
    *,
    severity: str,
    action: str,
    entity_type: str,
    entity_id: str,
    user_id: Optional[str],
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Write an audit row + dispatch an `audit.alert` event.

    Returns the audit_id on success, or None on a soft-failure (DB
    or event-bus unavailable). Never raises — the caller's flow must
    not break on alert failures.
    """
    severity = (severity or "LOW").upper()
    if severity not in SEVERITY_RANK:
        severity = "LOW"

    audit_row = {
        "timestamp": datetime.now(timezone.utc),
        "severity": severity,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "user_id": user_id,
        "diff": _diff(before, after) if (before or after) else None,
        "context": context or {},
    }

    audit_id: Optional[str] = None
    # 1. Persist to audit_logs (single source of truth for compliance)
    try:
        from ..dependencies import get_audit_repository
        audit_repo = get_audit_repository()
        if audit_repo is not None:
            saved = audit_repo.create(audit_row)
            audit_id = (saved or {}).get("audit_id") or (saved or {}).get("_id")
    except Exception as e:
        logger.debug(f"[AUDIT_ALERT] audit write soft-failed: {e}")

    # 2. Dispatch the event so SENTINEL/CORTEX can react in real time
    try:
        from agents.registry import dispatch_event
        await dispatch_event(
            "audit.alert",
            {
                "audit_id": audit_id,
                "severity": severity,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "user_id": user_id,
                "diff": audit_row["diff"],
                "context": audit_row["context"],
            },
            source=user_id or "system",
        )
    except Exception as e:
        logger.debug(f"[AUDIT_ALERT] event dispatch soft-failed: {e}")

    return audit_id


# ============================================================================
# Convenience helpers — domain-specific wrappers
# ============================================================================


async def alert_order_edited(
    order_id: str, before: Dict, after: Dict, user_id: Optional[str]
) -> Optional[str]:
    """Order edit AFTER it left DRAFT — anything that changes
    grand_total > 5% promotes to HIGH; otherwise LOW."""
    severity = "LOW"
    try:
        gt_before = float(before.get("grand_total", 0) or 0)
        gt_after = float(after.get("grand_total", 0) or 0)
        if gt_before > 0:
            pct = abs(gt_after - gt_before) / gt_before
            if pct > 0.05:
                severity = "HIGH"
    except (TypeError, ValueError):
        pass

    return await emit_audit_alert(
        severity=severity,
        action="order.edited",
        entity_type="order",
        entity_id=order_id,
        user_id=user_id,
        before=before,
        after=after,
    )


async def alert_order_cancelled(
    order_id: str, before: Dict, user_id: Optional[str], reason: str = ""
) -> Optional[str]:
    """Order cancellations are always CRITICAL — every one needs a
    human review trail. The reason becomes the context."""
    return await emit_audit_alert(
        severity="CRITICAL",
        action="order.cancelled",
        entity_type="order",
        entity_id=order_id,
        user_id=user_id,
        before=before,
        after={"status": "CANCELLED"},
        context={"cancel_reason": reason},
    )


async def alert_item_deleted(
    order_id: str,
    item_id: str,
    item_data: Dict,
    user_id: Optional[str],
    order_status: str = "",
) -> Optional[str]:
    """Item deletion: CRITICAL when order is post-checkout (any non-DRAFT
    status); HIGH otherwise."""
    severity = "CRITICAL" if order_status not in {"", "DRAFT", "draft"} else "HIGH"
    return await emit_audit_alert(
        severity=severity,
        action="order.item.deleted",
        entity_type="order_item",
        entity_id=f"{order_id}/{item_id}",
        user_id=user_id,
        before=item_data,
        context={"order_id": order_id, "order_status": order_status},
    )


async def alert_pnl_deviation(
    period: str, expected: float, actual: float, user_id: Optional[str] = None
) -> Optional[str]:
    """End-of-day P&L vs expected: HIGH if delta > 5%; CRITICAL if > 15%."""
    try:
        pct = abs(actual - expected) / expected if expected else 0
    except (TypeError, ZeroDivisionError):
        pct = 0
    severity = "CRITICAL" if pct > 0.15 else ("HIGH" if pct > 0.05 else "LOW")
    return await emit_audit_alert(
        severity=severity,
        action="pnl.deviation",
        entity_type="pnl_period",
        entity_id=period,
        user_id=user_id,
        context={
            "expected": round(float(expected or 0), 2),
            "actual": round(float(actual or 0), 2),
            "delta_pct": round(pct * 100, 2),
        },
    )
