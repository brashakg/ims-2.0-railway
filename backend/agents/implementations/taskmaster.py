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
        "po_send",  # Drafting is fine; sending to vendor needs approval
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

        # 2b. F8: aged-backorder sweep. Open PO lines past their expected_date
        # get an accountable P2 task (P1 once critically overdue 14d+). Tier 1
        # auto-act -- task creation is fully reversible. Fail-soft.
        actions.extend(await self._sweep_aged_backorders())

        # 3. E4: expire stale approval requests past their 60-min TTL. This is a
        # status flip (REQUESTED -> EXPIRED), not a delete -- rows stay
        # auditable. Fail-soft: a missing DB / engine error never breaks the tick.
        try:
            from api.services.approvals import ApprovalEngine

            expired = ApprovalEngine(db=self.db).expire_stale()
            if expired:
                logger.info(
                    "[TASKMASTER] expired %d stale approval request(s)", expired
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("[TASKMASTER] approval expire_stale skipped: %s", e)

        # 4. F29: optometrist-coverage breach sweep. Every PUBLISHED roster slot
        # (today or later) with no optometrist rostered raises ONE deduped in-app
        # bell to the store + area managers. Tier 1 (a notification is fully
        # reversible). Comms are DARK -- in-app only, no WhatsApp/SMS. Fail-soft.
        actions.extend(await self._sweep_coverage_breaches())

        if actions:
            logger.info(
                f"[TASKMASTER] tick complete — {len(actions)} action(s) executed"
            )
        self._actions_taken += len(actions)

    # ----- F29: optometrist-coverage breach sweep (in-app bell, deduped) -----

    async def _sweep_coverage_breaches(self) -> List[Dict[str, Any]]:
        """Raise a deduped in-app bell for every PUBLISHED-roster coverage breach
        (today or later) to the store + area managers. Fail-soft: any error
        (no DB, no roster module, no roster data) is a silent no-op -- it must
        NEVER break the tick. Comms are dark; in-app notifications only."""
        actions: List[Dict[str, Any]] = []
        if self.db is None:
            return actions
        try:
            from api.services import roster_engine as _roster
            from api.services.policy_engine import get_policy
        except Exception as e:  # noqa: BLE001
            logger.debug("[TASKMASTER] coverage sweep imports failed: %s", e)
            return actions

        def _required(store_id):
            try:
                return int(
                    get_policy(
                        _roster.POLICY_REQUIRED_OPTOMS,
                        {"store_id": store_id} if store_id else None,
                        default=_roster.DEFAULT_REQUIRED_OPTOMETRISTS,
                    )
                )
            except Exception:  # noqa: BLE001
                return _roster.DEFAULT_REQUIRED_OPTOMETRISTS

        try:
            breaches = _roster.published_coverage_breaches(
                self.db, required_optoms_for=_required
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("[TASKMASTER] coverage sweep skipped: %s", e)
            return actions
        if not breaches:
            return actions

        notif_coll = self.get_collection("notifications")
        users_coll = self.get_collection("users")
        for breach in breaches:
            for user_id in self._coverage_recipients(
                users_coll, breach.get("store_id")
            ):
                if self._raise_coverage_bell(notif_coll, _roster, breach, user_id):
                    actions.append(
                        {
                            "action": "coverage_breach_alert",
                            "store_id": breach.get("store_id"),
                            "date": breach.get("date"),
                            "shift": breach.get("shift"),
                            "user_id": user_id,
                        }
                    )
        if actions:
            logger.info(
                "[TASKMASTER] raised %d optometrist-coverage bell(s)", len(actions)
            )
        return actions

    def _coverage_recipients(self, users_coll, store_id) -> List[str]:
        """Store managers OF THAT STORE + all area managers. Deduped, order-stable."""
        if users_coll is None:
            return []
        ids: List[str] = []
        try:
            sm_q = {"roles": "STORE_MANAGER", "is_active": True}
            if store_id:
                sm_q["store_ids"] = store_id
            for u in users_coll.find(sm_q):
                uid = u.get("user_id") or u.get("_id")
                if uid:
                    ids.append(str(uid))
            for u in users_coll.find({"roles": "AREA_MANAGER", "is_active": True}):
                uid = u.get("user_id") or u.get("_id")
                if uid:
                    ids.append(str(uid))
        except Exception:  # noqa: BLE001
            return []
        seen = set()
        out = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    def _raise_coverage_bell(self, notif_coll, roster_mod, breach, user_id) -> bool:
        """Insert ONE in-app coverage-breach bell, deduped on the stable key so a
        repeated tick never re-alerts the same breach to the same person. Returns
        True only when a NEW bell was written. Fail-soft."""
        if notif_coll is None:
            return False
        key = roster_mod.coverage_breach_dedupe_key(breach, user_id)
        try:
            if notif_coll.find_one({"dedupe_key": key}) is not None:
                return False  # already alerted -> no duplicate
        except Exception:  # noqa: BLE001
            pass
        try:
            notif_coll.insert_one(
                roster_mod.build_coverage_breach_notification(breach, user_id, key)
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[TASKMASTER] coverage bell write failed: %s", e)
            return False

    async def _escalate_overdue_tasks(self) -> List[Dict[str, Any]]:
        """Escalate SLA-breached tasks UP the role ladder. Tier 1 auto-act.

        Uses the shared pure SLA check (services.task_sla.should_escalate) and
        the shared role-ladder resolver (services.task_escalation) so the
        5-minute agent tick and the /tasks/auto-escalate-overdue endpoint make
        the exact same decision AND pick the same next owner. Honours any
        persisted SLA overrides (task_sla_config). Reassigns ownership to the
        resolved manager (Store Manager -> Area Manager -> Admin -> Superadmin),
        scoped to the task's store."""
        coll = self.get_collection("tasks")
        if coll is None:
            return []
        # Lazy import (matches nexus.py) -- `api.*` is on path at runtime.
        try:
            from api.services.task_sla import should_escalate, DEFAULT_SLA
            from api.services.task_escalation import resolve_escalation_target
            from api.services.task_notify import notify_escalation
        except Exception as e:
            logger.debug(f"[TASKMASTER] escalation modules import failed: {e}")
            return []

        users_coll = self.get_collection("users")

        def _find_by_role(role, sid):
            if users_coll is None:
                return []
            q = {"roles": role, "is_active": True}
            if sid:
                q["store_ids"] = sid
            try:
                return list(users_coll.find(q))
            except Exception:
                return []

        # Load persisted SLA overrides (if any), merged onto the Standard matrix.
        sla_cfg = None
        cfg_coll = self.get_collection("task_sla_config")
        if cfg_coll is not None:
            try:
                doc = cfg_coll.find_one({"config_id": "global"}, {"_id": 0})
                overrides = (doc or {}).get("matrix") or {}
                if overrides:
                    sla_cfg = {
                        p: {**DEFAULT_SLA[p], **(overrides.get(p) or {})}
                        for p in DEFAULT_SLA
                    }
            except Exception:
                sla_cfg = None

        actions = []
        try:
            now = datetime.now()
            candidates = list(
                coll.find(
                    {
                        # Incl. ESCALATED: an ignored task climbs the ladder
                        # again on each fresh breach (multi-hop). should_escalate
                        # gates cadence + level so this can't storm.
                        "status": {
                            "$in": [
                                "OPEN",
                                "IN_PROGRESS",
                                "ESCALATED",
                                "open",
                                "in_progress",
                                "escalated",
                            ]
                        },
                    }
                ).limit(200)
            )
            for task in candidates:
                flag, reason = should_escalate(task, now=now, sla_config=sla_cfg)
                if not flag:
                    continue
                new_level = task.get("escalation_level", 0) + 1
                before = {
                    "status": task.get("status"),
                    "assigned_to": task.get("assigned_to"),
                    "escalation_level": task.get("escalation_level", 0),
                }
                assignee = None
                if task.get("assigned_to") and users_coll is not None:
                    try:
                        assignee = users_coll.find_one(
                            {"user_id": task.get("assigned_to")}
                        )
                    except Exception:
                        assignee = None
                target = resolve_escalation_target(
                    _find_by_role,
                    task.get("store_id"),
                    assignee or {"user_id": task.get("assigned_to")},
                )
                set_fields = {
                    "status": "ESCALATED",
                    "escalation_level": new_level,
                    "escalation_reason": reason,
                    "escalated_at": now,
                    "updated_at": now,
                    "escalated_by": self.agent_id,
                }
                history_entry = {
                    "action": "escalated",
                    "level": new_level,
                    "reason": reason,
                    "from": task.get("assigned_to"),
                    "by": self.agent_id,
                    "at": now,
                }
                if target and target.get("user_id"):
                    set_fields["assigned_to"] = target["user_id"]
                    set_fields["escalated_to"] = target["user_id"]
                    history_entry["to"] = target["user_id"]
                after = {
                    "status": "ESCALATED",
                    "escalation_level": new_level,
                    "assigned_to": set_fields.get(
                        "assigned_to", task.get("assigned_to")
                    ),
                }
                try:
                    coll.update_one(
                        {"_id": task["_id"]},
                        {
                            "$set": set_fields,
                            "$push": {"history": history_entry},
                        },
                    )
                    await self._audit_log(
                        action="task_escalation",
                        target=str(task.get("task_id") or task.get("_id")),
                        before=before,
                        after=after,
                        tier=1,
                    )
                    # Alert the new owner (in-app + WhatsApp), fail-soft.
                    await notify_escalation(
                        self.get_collection("notifications"),
                        target,
                        task,
                        reason,
                        now=now,
                    )
                    actions.append(
                        {
                            "action": "task_escalated",
                            "task_id": task.get("task_id"),
                            "reason": reason,
                            "to": set_fields.get("assigned_to"),
                        }
                    )
                except Exception as e:
                    logger.warning(
                        f"[TASKMASTER] Failed to escalate task {task.get('_id')}: {e}"
                    )
        except Exception as e:
            logger.debug(f"[TASKMASTER] Overdue scan error: {e}")
        return actions

    async def _draft_reorders(self) -> List[Dict[str, Any]]:
        """For SKUs below reorder_point, draft a PO. Tier 2 — DRAFT only,
        not auto-sent. Sending the PO requires Superadmin approval."""
        stock_coll = self.get_collection("stock_units")
        po_coll = self.get_collection("purchase_orders")
        if stock_coll is None or po_coll is None:
            return []
        actions = []
        try:
            low_stock = list(
                stock_coll.find(
                    {
                        "$expr": {"$lt": ["$quantity", "$reorder_point"]},
                    }
                ).limit(20)
            )
            for item in low_stock:
                sku = item.get("sku")
                # Skip if a draft PO already exists for this SKU today
                today_start = (
                    datetime.now(timezone.utc)
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                    .isoformat()
                )
                existing_draft = po_coll.find_one(
                    {
                        "auto_drafted_by": self.agent_id,
                        "sku": sku,
                        "status": "DRAFT",
                        "created_at": {"$gte": today_start},
                    }
                )
                if existing_draft:
                    continue
                draft_po = {
                    "po_number": f"PO-AUTO-{datetime.now(timezone.utc).strftime('%y%m%d-%H%M%S')}-{sku[:6]}",
                    "sku": sku,
                    "vendor_id": item.get("default_vendor_id"),
                    "quantity": max(
                        item.get("reorder_point", 0) * 2 - item.get("quantity", 0), 1
                    ),
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
                    actions.append(
                        {
                            "action": "po_drafted",
                            "sku": sku,
                            "qty": draft_po["quantity"],
                        }
                    )
                except Exception as e:
                    logger.warning(f"[TASKMASTER] Failed to draft PO for {sku}: {e}")
        except Exception as e:
            logger.debug(f"[TASKMASTER] Reorder scan error: {e}")
        return actions

    async def _sweep_aged_backorders(self) -> List[Dict[str, Any]]:
        """F8: turn aged open PO lines into accountable backorder tasks.

        Scans open POs (SENT/ACKNOWLEDGED/PARTIALLY_RECEIVED) past their
        expected_date. For each open line still un-received (and NOT dismissed),
        creates a P2 task deduped by source_ref="backorder:{po_id}:{product_id}".
        Once a PO line is critically overdue (14d+), a still-OPEN P2 task for it
        is escalated to P1 via a single find_one_and_update. Tier 1 auto-act --
        task creation is fully reversible. Every accessor is fail-soft so a
        missing DB never breaks the tick.
        """
        po_coll = self.get_collection("purchase_orders")
        tasks_coll = self.get_collection("tasks")
        if po_coll is None or tasks_coll is None:
            return []
        grn_coll = self.get_collection("grns")

        try:
            from api.services import po_variance_engine
            from api.services.task_triggers import create_system_task
            from database.repositories.task_repository import TaskRepository
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[TASKMASTER] backorder engine import failed: {e}")
            return []

        # Canonical system-task path: the SAME repo + creator the GRN-
        # discrepancy and payment-variance scans use (task_triggers.
        # create_system_task), so a backorder task carries task_number + due_at
        # (priority SLA grace) + the standard SYSTEM shape -- not a bespoke dict.
        task_repo = TaskRepository(tasks_coll)

        actions: List[Dict[str, Any]] = []
        now = datetime.now()
        try:
            pos = list(
                po_coll.find(
                    {"status": {"$in": ["SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"]}}
                ).limit(200)
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[TASKMASTER] backorder PO scan error: {e}")
            return []

        # Fetch ACCEPTED GRNs per PO (the engine itself ignores non-accepted).
        grns_by_po: Dict[str, List[Dict[str, Any]]] = {}
        for po in pos:
            pid = po.get("po_id")
            if not pid:
                continue
            if grn_coll is None:
                grns_by_po[pid] = []
                continue
            try:
                grns_by_po[pid] = list(grn_coll.find({"po_id": pid}))
            except Exception:  # noqa: BLE001
                grns_by_po[pid] = []

        specs = po_variance_engine.aged_backorder_tasks_needed(pos, grns_by_po, now=now)

        for spec in specs:
            ref = spec.get("source_ref")
            if not ref:
                continue
            # Find any existing task for this backorder line.
            try:
                existing = list(tasks_coll.find({"source_ref": ref}))
            except Exception:  # noqa: BLE001
                existing = []
            active = [
                t
                for t in existing
                if str(t.get("status", "")).upper()
                in {"OPEN", "IN_PROGRESS", "ESCALATED"}
            ]

            po_label = spec.get("po_number") or spec.get("po_id")
            product = spec.get("product_name") or spec.get("product_id")
            if not active:
                # No live task yet -> create one through the CANONICAL system-
                # task creator (dedupe-by-source_ref incl. OPEN/IN_PROGRESS/
                # ESCALATED lives inside create_system_task too, so a re-run
                # between the scan above and this call still cannot duplicate).
                title = (
                    f"Critically overdue backorder: {product} on PO {po_label}"
                    if spec.get("escalate")
                    else f"Overdue backorder: {product} on PO {po_label}"
                )
                description = (
                    f"PO {po_label} is {spec.get('days_overdue')} day(s) past its "
                    f"expected date with {spec.get('open_qty')} unit(s) of {product} "
                    f"still un-received. Chase the vendor or short-close the line."
                )
                created = None
                try:
                    created = create_system_task(
                        task_repo,
                        title=title,
                        description=description,
                        priority=spec.get("priority", "P2"),
                        category="Purchase",
                        store_id=None,
                        dedupe_ref=ref,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"[TASKMASTER] backorder task create failed for {ref}: {e}"
                    )
                if created:
                    actions.append(
                        {
                            "action": "backorder_task_created",
                            "po_id": spec.get("po_id"),
                            "product_id": spec.get("product_id"),
                            "priority": spec.get("priority"),
                            "task_id": created.get("task_id"),
                        }
                    )
            elif spec.get("escalate"):
                # A live P2 task exists and the line is now critically overdue
                # -> escalate to P1 (single find_one_and_update). Skip if already P1.
                needs_bump = any(
                    str(t.get("priority", "")).upper() != "P1" for t in active
                )
                if not needs_bump:
                    continue
                try:
                    tasks_coll.find_one_and_update(
                        {
                            "source_ref": ref,
                            "status": {"$in": ["OPEN", "IN_PROGRESS", "ESCALATED"]},
                            "priority": {"$ne": "P1"},
                        },
                        {
                            "$set": {"priority": "P1", "updated_at": now},
                            "$push": {
                                "history": {
                                    "action": "priority_escalated",
                                    "to": "P1",
                                    "reason": "Backorder critically overdue (14d+)",
                                    "by": self.agent_id,
                                    "at": now,
                                }
                            },
                        },
                    )
                    actions.append(
                        {
                            "action": "backorder_task_escalated",
                            "po_id": spec.get("po_id"),
                            "product_id": spec.get("product_id"),
                            "to": "P1",
                        }
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"[TASKMASTER] backorder escalate failed for {ref}: {e}"
                    )
        return actions

    async def _audit_log(
        self, action: str, target: str, before: Dict, after: Dict, tier: int
    ):
        """Every TASKMASTER action records a before/after audit row."""
        coll = self.get_collection("agent_audit_log")
        if coll is None:
            return
        try:
            coll.insert_one(
                {
                    "agent_id": self.agent_id,
                    "action": action,
                    "target": target,
                    "before_state": before,
                    "after_state": after,
                    "safety_tier": tier,
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
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
            now = datetime.now()
            coll.insert_one(
                {
                    "task_id": f"TSK-AUTO-{now.strftime('%y%m%d-%H%M%S')}",
                    "title": f"Review: {anomaly.get('summary', 'anomaly')}",
                    "description": "Auto-created from a detected anomaly (advisory, Tier 3).",
                    "category": "Review",
                    "priority": "P1",
                    "status": "OPEN",
                    "source": "SYSTEM",
                    "assigned_to": "store_manager",
                    "auto_created_by": self.agent_id,
                    "linked_anomaly": anomaly,
                    "due_at": now + timedelta(hours=24),
                    "created_at": now,
                    "updated_at": now,
                    "escalation_level": 0,
                }
            )
        except Exception as e:
            logger.warning(f"[TASKMASTER] Failed to create advisory task: {e}")

    async def run(self, query: str, context: AgentContext) -> AgentResponse:
        """On-demand: report recent actions taken."""
        coll = self.get_collection("agent_audit_log")
        if coll is None:
            return AgentResponse(
                success=False,
                agent_id=self.agent_id,
                message="agent_audit_log unavailable",
            )
        try:
            recent = list(
                coll.find(
                    {"agent_id": self.agent_id},
                    {"_id": 0},
                )
                .sort("executed_at", -1)
                .limit(20)
            )
        except Exception as e:
            return AgentResponse(success=False, agent_id=self.agent_id, message=str(e))
        return AgentResponse(
            success=True,
            agent_id=self.agent_id,
            data={
                "recent_actions": recent,
                "total_actions_session": self._actions_taken,
            },
            message=f"TASKMASTER: {len(recent)} recent action(s); {self._actions_taken} this session",
        )
