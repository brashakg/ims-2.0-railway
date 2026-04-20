"""
IMS 2.0 — TASKMASTER: Real Task Execution
============================================
Hero Identity: Taskmaster / Tony Masters (Marvel)
"Photographic reflexes, masters any task instantly. Our execution
powerhouse — the only agent that actually changes state."

TASKMASTER is the ONE agent that does real writes. Other agents observe
and queue; TASKMASTER executes. Three-tier safety model:

  Tier 1 (auto-act): low-stakes, fully reversible.
    e.g. send WhatsApp Rx reminder, mark a task complete, log an audit.
  Tier 2 (ask-confirm): medium stakes; requires Superadmin approval in UI.
    e.g. auto-create a PO, transfer staff between stores, refund.
  Tier 3 (advisory-only): too risky to ever automate. Surface as a task.
    e.g. write off bad debt, change a price ceiling.

Every action is audit-logged with `before_state` + `after_state` so we
can replay or rollback. Replaces the old `execute_command()` that
returned `{"success": true}` without doing anything.

Default schedule: every 5 minutes.
Scope of MVP implementation:
  - SLA escalation scan: tasks past their `due_at` get escalated up the chain
  - Auto-reorder trigger: when stock drops below `reorder_point`, draft a PO
  - Both auto-act (Tier 1) since they're fully reversible.
"""

from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
import logging

from ..base import JarvisAgent, AgentType, AgentResponse, AgentContext

logger = logging.getLogger(__name__)


class TaskmasterAgent(JarvisAgent):
    """Real execution — SLA escalation, auto-reorder, SOP enforcement."""

    agent_id = "taskmaster"
    agent_name = "TASKMASTER"
    agent_type = AgentType.EXECUTOR
    description = "Real execution — SLA escalation, auto-reorder, SOP enforcement, expense anomaly action"
    version = "1.0.0"
    toggleable = True

    # Anything in this list requires explicit human confirmation, NOT auto-act.
    requires_confirmation = [
        "po_send",       # Drafting is fine; sending to vendor needs approval
        "staff_transfer",
        "refund_issue",
        "price_ceiling_change",
        "writeoff",
    ]

    capabilities = [
        "sla_escalation",
        "auto_reorder_draft",
        "sop_verification",
        "expense_anomaly_action",
        "audit_logged_execution",
    ]

    def __init__(self, db=None):
        super().__init__(db=db)
        self._actions_taken = 0

    async def _do_background_work(self):
        """5-minute tick: scan + execute auto-act items, log everything."""
        actions = []

        # 1. SLA escalation: overdue tasks bumped to next escalation level
        actions.extend(await self._escalate_overdue_tasks())

        # 2. Auto-reorder: stock items below reorder_point → draft PO
        actions.extend(await self._draft_reorders())

        if actions:
            logger.info(f"[TASKMASTER] tick complete — {len(actions)} action(s) executed")
        self._actions_taken += len(actions)

    async def _escalate_overdue_tasks(self) -> List[Dict[str, Any]]:
        """Find tasks past due_at + escalate to next owner. Tier 1 auto-act."""
        coll = self.get_collection("tasks")
        if coll is None:
            return []
        actions = []
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            overdue = list(coll.find({
                "due_at": {"$lt": now_iso},
                "status": {"$nin": ["DONE", "CANCELLED"]},
                "escalated": {"$ne": True},
            }).limit(20))
            for task in overdue:
                before = {"escalated": False, "owner": task.get("owner")}
                after = {"escalated": True, "escalation_level": (task.get("escalation_level", 0) + 1)}
                try:
                    coll.update_one(
                        {"_id": task["_id"]},
                        {"$set": {**after, "escalated_at": now_iso, "escalated_by": self.agent_id}}
                    )
                    await self._audit_log(
                        action="task_escalation",
                        target=str(task.get("task_id") or task.get("_id")),
                        before=before,
                        after=after,
                        tier=1,
                    )
                    actions.append({"action": "task_escalated", "task_id": task.get("task_id")})
                except Exception as e:
                    logger.warning(f"[TASKMASTER] Failed to escalate task {task.get('_id')}: {e}")
        except Exception as e:
            logger.debug(f"[TASKMASTER] Overdue scan error: {e}")
        return actions

    async def _draft_reorders(self) -> List[Dict[str, Any]]:
        """For SKUs below reorder_point, draft a PO. Tier 2 — DRAFT only,
        not auto-sent. Sending the PO requires Superadmin approval."""
        stock_coll = self.get_collection("stock")
        po_coll = self.get_collection("purchase_orders")
        if stock_coll is None or po_coll is None:
            return []
        actions = []
        try:
            low_stock = list(stock_coll.find({
                "$expr": {"$lt": ["$quantity", "$reorder_point"]},
            }).limit(20))
            for item in low_stock:
                sku = item.get("sku")
                # Skip if a draft PO already exists for this SKU today
                today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                existing_draft = po_coll.find_one({
                    "auto_drafted_by": self.agent_id,
                    "sku": sku,
                    "status": "DRAFT",
                    "created_at": {"$gte": today_start},
                })
                if existing_draft:
                    continue
                draft_po = {
                    "po_number": f"PO-AUTO-{datetime.now(timezone.utc).strftime('%y%m%d-%H%M%S')}-{sku[:6]}",
                    "sku": sku,
                    "vendor_id": item.get("default_vendor_id"),
                    "quantity": max(item.get("reorder_point", 0) * 2 - item.get("quantity", 0), 1),
                    "status": "DRAFT",
                    "auto_drafted_by": self.agent_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "requires_approval": True,  # Tier 2 — Superadmin must approve before send
                }
                try:
                    po_coll.insert_one(draft_po)
                    await self._audit_log(
                        action="po_draft",
                        target=draft_po["po_number"],
                        before={"sku_quantity": item.get("quantity")},
                        after={"po_status": "DRAFT", "po_qty": draft_po["quantity"]},
                        tier=2,
                    )
                    actions.append({"action": "po_drafted", "sku": sku, "qty": draft_po["quantity"]})
                except Exception as e:
                    logger.warning(f"[TASKMASTER] Failed to draft PO for {sku}: {e}")
        except Exception as e:
            logger.debug(f"[TASKMASTER] Reorder scan error: {e}")
        return actions

    async def _audit_log(self, action: str, target: str, before: Dict, after: Dict, tier: int):
        """Every TASKMASTER action records a before/after audit row."""
        coll = self.get_collection("agent_audit_log")
        if coll is None:
            return
        try:
            coll.insert_one({
                "agent_id": self.agent_id,
                "action": action,
                "target": target,
                "before_state": before,
                "after_state": after,
                "safety_tier": tier,
                "executed_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.warning(f"[TASKMASTER] Audit log write failed: {e}")

    async def on_event(self, event: str, payload: Dict[str, Any]):
        """React to events from other agents."""
        if event == "stock.below_reorder":
            # SENTINEL or another agent saw a stock drop — run reorder check
            await self._draft_reorders()
        elif event == "anomaly.detected" and payload.get("kind") == "rx_out_of_range":
            # ORACLE flagged a bad Rx — we record it as a task instead of
            # auto-correcting (Tier 3 advisory only)
            await self._create_advisory_task(payload)

    async def _create_advisory_task(self, anomaly: Dict[str, Any]):
        """For Tier 3 anomalies — create a task for human review, no auto-action."""
        coll = self.get_collection("tasks")
        if coll is None:
            return
        try:
            coll.insert_one({
                "task_id": f"TSK-AUTO-{datetime.now(timezone.utc).strftime('%y%m%d-%H%M%S')}",
                "title": f"Review: {anomaly.get('summary', 'anomaly')}",
                "priority": "P1",
                "status": "OPEN",
                "owner": "store_manager",
                "auto_created_by": self.agent_id,
                "linked_anomaly": anomaly,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.warning(f"[TASKMASTER] Failed to create advisory task: {e}")

    async def run(self, query: str, context: AgentContext) -> AgentResponse:
        """On-demand: report recent actions taken."""
        coll = self.get_collection("agent_audit_log")
        if coll is None:
            return AgentResponse(success=False, agent_id=self.agent_id, message="agent_audit_log unavailable")
        try:
            recent = list(coll.find(
                {"agent_id": self.agent_id},
                {"_id": 0},
            ).sort("executed_at", -1).limit(20))
        except Exception as e:
            return AgentResponse(success=False, agent_id=self.agent_id, message=str(e))
        return AgentResponse(
            success=True,
            agent_id=self.agent_id,
            data={"recent_actions": recent, "total_actions_session": self._actions_taken},
            message=f"TASKMASTER: {len(recent)} recent action(s); {self._actions_taken} this session",
        )
