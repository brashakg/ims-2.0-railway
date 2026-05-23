"""
IMS 2.0 - Variance-driven task automation + integrity detectors
===============================================================
Turns operational variances into accountable tasks (the whole point of the
system: replace WhatsApp chaos with enforced follow-up), and surfaces two
integrity signals on the task stream itself:

  * STOCK variance  -> a SYSTEM task when a completed stock count is off
  * PAYMENT variance -> a SYSTEM task per order whose money doesn't reconcile
  * FAKE-CLOSURE    -> tasks marked done implausibly fast (gaming)
  * SILENCE         -> OPEN tasks never acknowledged within their ack-SLA

Pure helpers (no DB) are unit-tested; create_system_task takes the repo so it
stays testable with a fake. Everything fail-soft.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .task_sla import sla_for

_ACTIVE = {"OPEN", "IN_PROGRESS", "ESCALATED", "open", "in_progress", "escalated"}


# ---------------------------------------------------------------------------
# Pure threshold / detection logic
# ---------------------------------------------------------------------------

def stock_variance_priority(
    shrinkage_pct: float,
    var_pct: float,
    *,
    min_shrink: float = 2.0,
    high_shrink: float = 10.0,
) -> Optional[str]:
    """Priority for a completed stock count, or None if within tolerance.

    shrinkage_pct = lost units as % of system stock (>=0). var_pct = signed
    overall variance %. Big shrinkage is P1; smaller-but-real is P2; a large
    *positive* swing (phantom stock) is also worth a P2.
    """
    shrink = abs(float(shrinkage_pct or 0))
    swing = abs(float(var_pct or 0))
    if shrink >= high_shrink:
        return "P1"
    if shrink >= min_shrink:
        return "P2"
    if swing >= high_shrink:
        return "P2"
    return None


def payment_anomalies(orders: List[Dict[str, Any]], tolerance: float = 1.0) -> List[Dict[str, Any]]:
    """Find orders whose money doesn't reconcile. Pure.

    - OVERPAID: amount_paid exceeds grand_total beyond tolerance.
    - PAYMENTS_MISMATCH: sum(payments[].amount) != amount_paid beyond tolerance.
    - UNBALANCED_CLOSED: a delivered/completed order still owes money.
    """
    out: List[Dict[str, Any]] = []
    closed = {"DELIVERED", "COMPLETED", "CLOSED"}
    for o in orders or []:
        oid = o.get("order_id") or o.get("id") or o.get("order_number")
        if not oid:
            continue
        grand = float(o.get("grand_total", 0) or 0)
        paid = float(o.get("amount_paid", 0) or 0)
        balance = o.get("balance_due")
        balance = float(balance) if balance is not None else round(grand - paid, 2)
        status = str(o.get("status", "") or "").upper()
        pays = o.get("payments") or []
        pay_sum = round(sum(float(p.get("amount", 0) or 0) for p in pays), 2)

        if paid - grand > tolerance:
            out.append({"order_id": oid, "kind": "OVERPAID",
                        "detail": f"paid {paid} vs total {grand}"})
        elif pays and abs(pay_sum - paid) > tolerance:
            out.append({"order_id": oid, "kind": "PAYMENTS_MISMATCH",
                        "detail": f"payments sum {pay_sum} vs amount_paid {paid}"})
        elif status in closed and balance > tolerance:
            out.append({"order_id": oid, "kind": "UNBALANCED_CLOSED",
                        "detail": f"{status} but balance_due {balance}"})
    return out


def is_suspicious_closure(task: Dict[str, Any], min_seconds: int = 20) -> bool:
    """True if a task was 'completed' implausibly fast after it was created
    (fake-closure / box-ticking). Naive-local datetimes, tolerant of strings."""
    status = str(task.get("status", "") or "").upper()
    if status not in {"COMPLETED", "DONE", "CLOSED", "RESOLVED"}:
        return False
    created = _as_dt(task.get("created_at"))
    closed = _as_dt(task.get("completed_at") or task.get("resolved_at") or task.get("updated_at"))
    if created is None or closed is None:
        return False
    return 0 <= (closed - created).total_seconds() < min_seconds


def silent_tasks(
    tasks: List[Dict[str, Any]], now: Optional[datetime] = None, sla_config: Optional[dict] = None
) -> List[Dict[str, Any]]:
    """OPEN (unacknowledged) tasks past their ack-SLA window. Pure."""
    now = now or datetime.now()
    out: List[Dict[str, Any]] = []
    for t in tasks or []:
        status = str(t.get("status", "") or "").upper()
        if status != "OPEN":
            continue
        created = _as_dt(t.get("created_at"))
        if created is None:
            continue
        ack = sla_for(t.get("priority"), sla_config)["ack_minutes"]
        if now >= created + timedelta(minutes=ack):
            out.append(t)
    return out


def _as_dt(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "").split("+")[0])
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Persistence helper (repo passed in -> testable with a fake)
# ---------------------------------------------------------------------------

def create_system_task(
    repo: Any,
    *,
    title: str,
    description: str,
    priority: str,
    category: str,
    store_id: Optional[str],
    dedupe_ref: str,
    assigned_to: Optional[str] = None,
    due_at: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Create a SYSTEM task, deduped by source_ref: if an ACTIVE task already
    exists for the same source_ref, do nothing (avoids a task per re-run).
    Returns the created task, or None if deduped / no repo."""
    if repo is None:
        return None
    try:
        existing = repo.find_many({"source_ref": dedupe_ref}) or []
        if any(str(t.get("status", "")).upper() in {"OPEN", "IN_PROGRESS", "ESCALATED"} for t in existing):
            return None
    except Exception:  # noqa: BLE001
        pass  # dedupe is best-effort

    now = datetime.now()
    if due_at is None:
        grace = sla_for(priority)["grace_minutes"]
        due_at = now + timedelta(minutes=grace)

    task = {
        "task_id": f"TSK-{uuid.uuid4().hex[:10].upper()}",
        "title": title,
        "description": description,
        "category": category or "Variance",
        "priority": priority,
        "status": "OPEN",
        "source": "SYSTEM",
        "source_ref": dedupe_ref,
        "assigned_to": assigned_to,
        "assigned_by": "system",
        "store_id": store_id,
        "due_at": due_at,
        "created_at": now,
        "updated_at": now,
        "escalation_level": 0,
        "history": [{"status": "OPEN", "timestamp": now, "by": "system", "notes": "Auto-created from variance"}],
    }
    try:
        created = repo.create(task)
        return created or task
    except Exception:  # noqa: BLE001
        return None
