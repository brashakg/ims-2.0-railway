"""
IMS 2.0 - Tasks, SOPs & Escalation System
===========================================
Complete task management with auto-escalation and SOP templates
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import uuid

from .auth import get_current_user
from ..dependencies import get_task_repository, get_user_repository, get_db
from ..services.task_sla import (
    DEFAULT_SLA,
    canon_source,
    canon_status,
    should_escalate,
    sla_for,
)
from ..services.task_escalation import resolve_escalation_target
from ..services.task_notify import notify_escalation
from ..services.sop_checklist import (
    DEFAULT_SOP_TEMPLATES,
    apply_item_toggle,
    completion_status,
    default_template_steps,
    merge_checklist,
    progress_of,
)

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=3)
    description: Optional[str] = None
    category: str = Field(default="General")
    priority: str = Field(default="P3")  # P0-P4
    assigned_to: str
    # Canonical field is `due_at`; `due_date` accepted for backwards compat.
    due_date: Optional[datetime] = None
    due_at: Optional[datetime] = None
    # Canonical field is `source` (SYSTEM|USER|SOP); `type` (manual|sop|system)
    # accepted for backwards compat.
    type: Optional[str] = None
    source: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None  # OPEN, IN_PROGRESS, COMPLETED, ESCALATED, CANCELLED
    notes: Optional[str] = None
    assigned_to: Optional[str] = None
    due_at: Optional[datetime] = None


class TaskComplete(BaseModel):
    completion_notes: str


class ChecklistItemComplete(BaseModel):
    item_index: int
    completed: bool


class SOPTemplate(BaseModel):
    name: str
    type: str  # opening, closing, stock_count
    items: List[str]
    store_id: Optional[str] = None


class ChecklistCompletion(BaseModel):
    date: str
    type: str  # opening, closing, stock_count
    items_completed: List[bool]


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def generate_task_id() -> str:
    """Generate unique task ID"""
    return f"TASK-{uuid.uuid4().hex[:8].upper()}"


def _canon_task_out(task: dict) -> dict:
    """Normalize a stored task to the canonical shape on the way OUT, so the
    API always emits UPPERCASE status + ``due_at`` + ``source`` regardless of
    whether the doc was written by the new code or a legacy lowercase write.
    Lets old rows display correctly without a destructive data migration."""
    if not isinstance(task, dict):
        return task
    if task.get("status"):
        task["status"] = canon_status(task["status"])
    if task.get("due_at") is None and task.get("due_date") is not None:
        task["due_at"] = task["due_date"]
    if not task.get("source") and task.get("type"):
        task["source"] = canon_source(task["type"])
    return task


# NOTE: `should_escalate(task, *, now=None, sla_config=None)` is imported from
# services.task_sla -- a pure, per-priority SLA check (ack clock + overdue
# grace) that replaces the old hard-coded 2-hour rule.


def _sla_config_collection():
    """Return the task_sla_config collection (or None if DB unavailable)."""
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return db.get_collection("task_sla_config")
    except Exception:
        return None


def _load_sla_config() -> Optional[dict]:
    """Load persisted per-priority SLA overrides, merged onto DEFAULT_SLA.
    Returns None when nothing is stored (callers fall back to DEFAULT_SLA)."""
    col = _sla_config_collection()
    if col is None:
        return None
    try:
        doc = col.find_one({"config_id": "global"}, {"_id": 0})
    except Exception:
        doc = None
    overrides = (doc or {}).get("matrix") or {}
    if not overrides:
        return None
    return {p: {**DEFAULT_SLA[p], **(overrides.get(p) or {})} for p in DEFAULT_SLA}


def _escalate_and_reassign(
    repo, task: dict, *, reason: str, by: str, now: datetime
) -> Optional[dict]:
    """Resolve the next owner up the role ladder and reassign the task to them.

    Marks the task ESCALATED, bumps escalation_level, records escalated_to +
    an escalation history entry. Returns the target user dict, or None if the
    chain is exhausted (the task is still marked ESCALATED so it surfaces)."""
    user_repo = get_user_repository()
    target = None
    if user_repo is not None:
        assignee = None
        try:
            if task.get("assigned_to"):
                assignee = user_repo.find_by_id(task.get("assigned_to"))
        except Exception:
            assignee = None
        target = resolve_escalation_target(
            user_repo.find_by_role,
            task.get("store_id"),
            assignee or {"user_id": task.get("assigned_to")},
        )

    new_level = task.get("escalation_level", 0) + 1
    history_entry = {
        "action": "escalated",
        "level": new_level,
        "reason": reason,
        "from": task.get("assigned_to"),
        "by": by,
        "at": now,
    }
    update: dict = {
        "status": "ESCALATED",
        "escalation_level": new_level,
        "escalation_reason": reason,
        "escalated_at": now,
        "updated_at": now,
        "escalated_by": by,
    }
    if target and target.get("user_id"):
        update["assigned_to"] = target["user_id"]
        update["escalated_to"] = target["user_id"]
        history_entry["to"] = target["user_id"]
    update["history"] = (task.get("history") or []) + [history_entry]
    repo.update(task.get("task_id"), update)
    return target


def _notifications_coll():
    """Return the notifications collection (or None if DB unavailable)."""
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return db.get_collection("notifications")
    except Exception:
        return None


async def _escalate_reassign_notify(repo, task: dict, *, reason: str, by: str, now: datetime):
    """Escalate + reassign (resolve owner) then alert the new owner in-app +
    WhatsApp. Returns the resolved target user dict (or None)."""
    target = _escalate_and_reassign(repo, task, reason=reason, by=by, now=now)
    await notify_escalation(_notifications_coll(), target, task, reason, now=now)
    return target


# ============================================================================
# ENDPOINTS: TASK MANAGEMENT
# ============================================================================


# Both "" and "/" — the app uses redirect_slashes=False (to keep CORS
# preflights simple), so both the trailing-slash and bare forms must
# resolve to the same handler. Audit Run #2 found /tasks returning 404
# on prod because the frontend calls api.get('/tasks') without slash.
@router.get("")
@router.get("/")
async def list_tasks(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    task_type: Optional[str] = Query(None, alias="type"),
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List tasks with filters (status, priority, assigned_to, type)"""
    repo = get_task_repository()

    if repo is None:
        return {"tasks": [], "total": 0}

    filters: dict = {}

    if status:
        # Tolerant: match canonical UPPERCASE and any legacy lowercase rows.
        canon = canon_status(status)
        filters["status"] = {"$in": list({canon, canon.lower()})}
    if priority:
        filters["priority"] = priority
    if assigned_to:
        filters["assigned_to"] = assigned_to
    if task_type:
        filters["source"] = canon_source(task_type)
    if store_id:
        filters["store_id"] = store_id
    else:
        filters["store_id"] = current_user.get("active_store_id")

    tasks = [_canon_task_out(t) for t in repo.find_many(filters, skip=skip, limit=limit)]
    total = repo.count(filters)

    return {"tasks": tasks, "total": total}


# Both bare and slashed paths — Phase 6.13 follow-up to 6.10. POST
# variants were still 404/405'ing because the earlier sweep only
# touched GET handlers.
@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_task(
    task: TaskCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new task (P0-P4 priority, manual/sop/system type)"""
    repo = get_task_repository()

    if repo is None:
        return {"task_id": generate_task_id(), "message": "Task created"}

    due_at = task.due_at or task.due_date
    if due_at is None:
        raise HTTPException(status_code=422, detail="due_at (or due_date) is required")
    now = datetime.now()

    task_data = {
        "task_id": generate_task_id(),
        "title": task.title,
        "description": task.description,
        "category": task.category or "General",
        "priority": task.priority,  # P0-P4
        "status": "OPEN",
        "source": canon_source(task.source or task.type),  # SYSTEM | USER | SOP
        "assigned_to": task.assigned_to,
        "assigned_by": current_user.get("user_id"),
        "store_id": current_user.get("active_store_id"),
        "due_at": due_at,
        "created_at": now,
        "updated_at": now,
        "escalation_level": 0,
        "history": [
            {
                "status": "OPEN",
                "timestamp": now,
                "by": current_user.get("user_id"),
                "notes": "Task created",
            }
        ],
    }

    result = repo.create(task_data)

    if result:
        return {
            "task_id": result.get("task_id"),
            "message": "Task created successfully",
        }

    raise HTTPException(status_code=500, detail="Failed to create task")


@router.get("/my-tasks")
async def get_my_tasks(
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get current user's assigned tasks"""
    repo = get_task_repository()

    if repo is None:
        return {"tasks": []}

    filters: dict = {"assigned_to": current_user.get("user_id")}

    if status:
        canon = canon_status(status)
        filters["status"] = {"$in": list({canon, canon.lower()})}
    else:
        # By default, exclude completed/cancelled (tolerant of legacy casing).
        filters["status"] = {
            "$in": ["OPEN", "IN_PROGRESS", "ESCALATED", "open", "in_progress", "escalated"]
        }

    tasks = [
        _canon_task_out(t)
        for t in repo.find_many(filters, sort=[("priority", 1), ("due_at", 1)])
    ]

    return {"tasks": tasks, "total": len(tasks)}


@router.get("/overdue")
async def get_overdue_tasks(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get overdue tasks for escalation"""
    repo = get_task_repository()

    if repo is None:
        return {"tasks": []}

    active_store = store_id or current_user.get("active_store_id")

    now = datetime.now()
    filters: dict = {
        "status": {"$in": ["OPEN", "IN_PROGRESS", "open", "in_progress"]},
        "$or": [{"due_at": {"$lt": now}}, {"due_date": {"$lt": now}}],
    }

    if active_store:
        filters["store_id"] = active_store

    tasks = [_canon_task_out(t) for t in repo.find_many(filters, sort=[("due_at", 1)])]

    return {"tasks": tasks, "total": len(tasks)}


# IMPORTANT: GET /{task_id} is registered at the BOTTOM of this file via
# `router.add_api_route(...)`, NOT here. FastAPI matches routes in the
# order they're registered, and a `/{task_id}` decorator here would
# shadow every specific GET below it (`/checklists`, `/summary`,
# `/sop-templates`, ...) — they'd resolve to this handler with
# task_id="summary" etc. and return 404 ("Task not found"). The Hub's
# OPEN TASKS card silently rendered "—" because of exactly that bug.
async def get_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get task by ID"""
    repo = get_task_repository()

    if repo is None:
        return {"task_id": task_id}

    task = repo.find_by_id(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return _canon_task_out(task)


@router.patch("/{task_id}")
async def update_task(
    task_id: str,
    update: TaskUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update task (status, notes, reassign)"""
    repo = get_task_repository()

    if repo is None:
        return {"task_id": task_id, "message": "Task updated"}

    existing = repo.find_by_id(task_id)

    if not existing:
        raise HTTPException(status_code=404, detail="Task not found")

    update_data = {}

    if update.title:
        update_data["title"] = update.title
    if update.description is not None:
        update_data["description"] = update.description
    if update.priority:
        update_data["priority"] = update.priority
    if update.status:
        update_data["status"] = canon_status(update.status)
    if update.notes:
        update_data["notes"] = update.notes
    if update.assigned_to:
        update_data["assigned_to"] = update.assigned_to
    if update.due_at is not None:
        update_data["due_at"] = update.due_at

    if update_data:
        update_data["updated_at"] = datetime.now()
        if repo.update(task_id, update_data):
            return {"task_id": task_id, "message": "Task updated"}

    return {"task_id": task_id, "message": "No changes made"}


@router.patch("/{task_id}/complete")
async def complete_task(
    task_id: str,
    completion: TaskComplete,
    current_user: dict = Depends(get_current_user),
):
    """Complete a task with completion notes"""
    repo = get_task_repository()

    if repo is None:
        return {"task_id": task_id, "status": "COMPLETED"}

    task = repo.find_by_id(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if canon_status(task.get("status")) == "COMPLETED":
        raise HTTPException(status_code=400, detail="Task already completed")

    now = datetime.now()
    result = repo.update(
        task_id,
        {
            "status": "COMPLETED",
            "completed_at": now,
            "updated_at": now,
            "completion_notes": completion.completion_notes,
            "completed_by": current_user.get("user_id"),
        },
    )

    if result:
        return {"task_id": task_id, "status": "COMPLETED", "message": "Task completed"}

    raise HTTPException(status_code=500, detail="Failed to complete task")


# ============================================================================
# ENDPOINTS: DAILY CHECKLISTS
# ============================================================================


@router.get("/checklists")
async def get_daily_checklists(
    checklist_type: str = Query(..., alias="type"),  # opening, closing, stock_count
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get daily checklists (opening, closing, stock count)"""
    repo = get_task_repository()

    if repo is None:
        return {
            "type": checklist_type,
            "items": [],
            "completed_items": [],
            "progress": 0,
        }

    active_store = store_id or current_user.get("active_store_id")

    # Get SOP template for this checklist type
    filters = {"type": checklist_type, "store_id": active_store, "active": True}

    # This would query sop_templates collection
    # For now, return structure
    return {
        "type": checklist_type,
        "store_id": active_store,
        "items": ["Check inventory", "Verify stock count", "Update system"],
        "completed_items": [False, False, False],
        "progress": "0/3",
    }


@router.post("/checklists/{checklist_type}/complete-item")
async def complete_checklist_item(
    checklist_type: str,
    completion: ChecklistItemComplete,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Mark a checklist item as done"""
    active_store = store_id or current_user.get("active_store_id")

    return {
        "checklist_type": checklist_type,
        "item_index": completion.item_index,
        "completed": completion.completed,
        "message": "Checklist item updated",
    }


# ============================================================================
# ENDPOINTS: AUTO-GENERATION & SOPs
# ============================================================================


@router.post("/auto-generate")
async def auto_generate_daily_tasks(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Auto-generate today's daily tasks from persisted DAILY SOP templates for
    the store. Falls back to the built-in starter set when none are configured."""
    repo = get_task_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is None:
        return {"generated": 0, "message": "Auto-generate failed"}

    # Persisted DAILY templates assigned to this store (or global).
    templates: list = []
    scol = _sop_collection()
    if scol is not None:
        try:
            templates = list(scol.find(
                {
                    "frequency": "DAILY",
                    "is_active": {"$ne": False},
                    "$or": [{"store_id": active_store}, {"store_id": None}],
                },
                {"_id": 0},
            ))
        except Exception:
            templates = []

    now = datetime.now()
    generated_count = 0

    def _make_task(title, description, category, items, template_id=None):
        return {
            "task_id": generate_task_id(),
            "title": title,
            "description": description,
            "category": category or "Operations",
            "priority": "P2",
            "status": "OPEN",
            "source": "SOP",
            "assigned_to": current_user.get("user_id"),
            "assigned_by": current_user.get("user_id"),
            "store_id": active_store,
            "due_at": now + timedelta(days=1),
            "created_at": now,
            "updated_at": now,
            "escalation_level": 0,
            "sop_template_id": template_id,
            "checklist_items": [{"text": t, "completed": False} for t in items],
        }

    if templates:
        for tpl in templates:
            items = [s.get("instruction") for s in (tpl.get("steps") or [])]
            task_data = _make_task(
                tpl.get("title") or "Daily SOP",
                tpl.get("description") or "Daily SOP checklist",
                tpl.get("category"),
                items,
                template_id=tpl.get("template_id"),
            )
            if repo.create(task_data):
                generated_count += 1
    else:
        # No templates configured yet -> built-in starter set.
        for tdef in DEFAULT_SOP_TEMPLATES:
            task_data = _make_task(
                tdef["title"], tdef["description"], tdef["category"], tdef["steps"]
            )
            if repo.create(task_data):
                generated_count += 1

    return {
        "generated": generated_count,
        "message": f"Generated {generated_count} daily task(s) from SOP templates",
    }


# ============================================================================
# ENDPOINTS: ESCALATION MANAGEMENT
# ============================================================================


@router.post("/{task_id}/acknowledge")
async def acknowledge_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Acknowledge a task (prevent escalation)"""
    repo = get_task_repository()

    if repo is None:
        return {"task_id": task_id, "message": "Task acknowledged"}

    task = repo.find_by_id(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    now = datetime.now()
    if repo.update(
        task_id,
        {
            "status": "IN_PROGRESS",
            "acknowledged_at": now,
            "updated_at": now,
            "acknowledged_by": current_user.get("user_id"),
        },
    ):
        return {
            "task_id": task_id,
            "status": "IN_PROGRESS",
            "message": "Task acknowledged",
        }

    raise HTTPException(status_code=500, detail="Failed to acknowledge task")


@router.post("/{task_id}/escalate")
async def escalate_task(
    task_id: str,
    escalate_to: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Manually escalate a task. If `escalate_to` is omitted, the next owner is
    resolved automatically up the role ladder (Store Manager -> Area Manager ->
    Admin -> Superadmin) scoped to the task's store."""
    repo = get_task_repository()

    if repo is None:
        return {"task_id": task_id, "status": "ESCALATED"}

    task = repo.find_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    now = datetime.now()
    by = current_user.get("user_id")

    if escalate_to:
        # Explicit target chosen by the user -- reassign ownership to them.
        new_level = task.get("escalation_level", 0) + 1
        history_entry = {
            "action": "escalated", "level": new_level, "reason": "manual",
            "from": task.get("assigned_to"), "to": escalate_to, "by": by, "at": now,
        }
        repo.update(task_id, {
            "status": "ESCALATED",
            "assigned_to": escalate_to,
            "escalated_to": escalate_to,
            "escalated_at": now,
            "updated_at": now,
            "escalation_level": new_level,
            "escalated_by": by,
            "history": (task.get("history") or []) + [history_entry],
        })
        # Alert the explicit target (in-app + WhatsApp).
        user_repo = get_user_repository()
        target_user = None
        if user_repo is not None:
            try:
                target_user = user_repo.find_by_id(escalate_to)
            except Exception:
                target_user = None
        await notify_escalation(
            _notifications_coll(), target_user or {"user_id": escalate_to}, task, "manual", now=now
        )
        return {
            "task_id": task_id, "status": "ESCALATED", "escalation_level": new_level,
            "escalated_to": escalate_to, "message": "Task escalated",
        }

    # Auto-resolve up the ladder.
    target = await _escalate_reassign_notify(repo, task, reason="manual", by=by, now=now)
    return {
        "task_id": task_id,
        "status": "ESCALATED",
        "escalation_level": task.get("escalation_level", 0) + 1,
        "escalated_to": (target or {}).get("user_id"),
        "message": (
            f"Task escalated to {target.get('user_id')}" if target
            else "Task escalated (no higher owner found)"
        ),
    }


@router.post("/auto-escalate-overdue")
async def auto_escalate_overdue_tasks(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Trigger auto-escalation of overdue tasks (admin endpoint)"""
    repo = get_task_repository()

    if repo is None:
        return {"escalated": 0, "message": "Auto-escalation failed"}

    active_store = store_id or current_user.get("active_store_id")

    # Candidate set: non-terminal, not-yet-escalated tasks. The precise
    # decision (ack clock + overdue grace, per priority) is made by the pure
    # should_escalate() below -- a task can breach its ack SLA before it is
    # even past due, so we can't pre-filter on due date alone.
    filters: dict = {
        "status": {"$in": ["OPEN", "IN_PROGRESS", "open", "in_progress"]},
    }
    if active_store:
        filters["store_id"] = active_store

    candidates = repo.find_many(filters, limit=500)
    now = datetime.now()
    sla_cfg = _load_sla_config()
    escalated_count = 0

    for task in candidates:
        flag, reason = should_escalate(task, now=now, sla_config=sla_cfg)
        if not flag:
            continue
        # Resolve the next owner up the role ladder + reassign + notify.
        await _escalate_reassign_notify(repo, task, reason=reason, by="system", now=now)
        escalated_count += 1

    return {
        "escalated": escalated_count,
        "message": f"Auto-escalated {escalated_count} overdue tasks",
    }


# ============================================================================
# ENDPOINTS: ANALYTICS & REPORTING
# ============================================================================


@router.get("/summary")
async def get_task_summary(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get task summary by status"""
    repo = get_task_repository()

    if repo is None:
        return {"summary": {}, "overdue_count": 0, "escalated_count": 0}

    active_store = store_id or current_user.get("active_store_id")

    filters: dict = {}
    if active_store:
        filters["store_id"] = active_store

    # Count by status -- tolerant of canonical UPPERCASE and legacy lowercase.
    def _count(*variants: str) -> int:
        return repo.count({**filters, "status": {"$in": list(variants)}})

    open_ct = _count("OPEN", "open")
    in_progress_ct = _count("IN_PROGRESS", "in_progress")
    completed_ct = _count("COMPLETED", "completed")
    escalated_ct = _count("ESCALATED", "escalated")

    summary = {
        "OPEN": open_ct,
        "IN_PROGRESS": in_progress_ct,
        "COMPLETED": completed_ct,
        "ESCALATED": escalated_ct,
    }

    now = datetime.now()
    overdue_count = repo.count(
        {
            **filters,
            "status": {"$in": ["OPEN", "IN_PROGRESS", "open", "in_progress"]},
            "$or": [{"due_at": {"$lt": now}}, {"due_date": {"$lt": now}}],
        }
    )

    total = open_ct + in_progress_ct + completed_ct + escalated_ct

    # Both the nested `summary` (canonical keys) and flat convenience keys the
    # dashboard cards read directly (open = open + in-progress).
    return {
        "summary": summary,
        "open": open_ct + in_progress_ct,
        "completed": completed_ct,
        "escalated": escalated_ct,
        "overdue": overdue_count,
        "overdue_count": overdue_count,
        "escalated_count": escalated_ct,
        "total": total,
    }


# ============================================================================
# SLA CONFIG (Tasks/SOP Phase 2)
# ============================================================================
# Persisted per-priority SLA overrides in the `task_sla_config` collection
# (single global doc, config_id="global"). Unset => DEFAULT_SLA (Standard
# matrix). Drives both /auto-escalate-overdue and the TASKMASTER agent.


class SlaRow(BaseModel):
    ack_minutes: int = Field(..., ge=1, le=43200)  # <= 30 days
    grace_minutes: int = Field(..., ge=1, le=43200)


class SlaConfigUpdate(BaseModel):
    matrix: Dict[str, SlaRow]


@router.get("/sla-config")
async def get_sla_config(current_user: dict = Depends(get_current_user)):
    """Return the per-priority SLA matrix (Standard default merged with any
    stored overrides)."""
    col = _sla_config_collection()
    stored = None
    if col is not None:
        try:
            stored = col.find_one({"config_id": "global"}, {"_id": 0})
        except Exception:
            stored = None
    overrides = (stored or {}).get("matrix") or {}
    merged = {p: {**DEFAULT_SLA[p], **(overrides.get(p) or {})} for p in DEFAULT_SLA}
    return {
        "matrix": merged,
        "is_default": not bool(overrides),
        "updated_at": (stored or {}).get("updated_at"),
        "updated_by": (stored or {}).get("updated_by"),
    }


@router.put("/sla-config")
async def update_sla_config(
    payload: SlaConfigUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Persist per-priority SLA overrides. SUPERADMIN / ADMIN only."""
    allowed = {"SUPERADMIN", "ADMIN"}
    if not (set(current_user.get("roles", [])) & allowed):
        raise HTTPException(
            status_code=403, detail="Only SUPERADMIN / ADMIN may edit SLA config"
        )

    clean: dict = {}
    for p, row in payload.matrix.items():
        pu = str(p).upper()
        if pu not in DEFAULT_SLA:
            raise HTTPException(
                status_code=422, detail=f"Unknown priority '{p}' (expected P0-P4)"
            )
        clean[pu] = {"ack_minutes": row.ack_minutes, "grace_minutes": row.grace_minutes}

    col = _sla_config_collection()
    if col is None:
        raise HTTPException(status_code=503, detail="DB unavailable")
    now = datetime.now()
    col.update_one(
        {"config_id": "global"},
        {"$set": {"matrix": clean, "updated_at": now, "updated_by": current_user.get("user_id")}},
        upsert=True,
    )
    merged = {p: {**DEFAULT_SLA[p], **(clean.get(p) or {})} for p in DEFAULT_SLA}
    return {"matrix": merged, "is_default": False, "message": "SLA config updated"}


# ============================================================================
# SOP TEMPLATES (Phase 6.14)
# ============================================================================
# Persisted SOPs in the `sop_templates` collection. Replaces the static
# DEFAULT_CHECKLISTS dict on the frontend so SUPERADMIN can edit items +
# assign templates to roles/users/stores. Phase 4 adds real completion
# tracking in `sop_completions` (one doc per template+store+date) via the
# /sop-checklist endpoints further below.


class SopStep(BaseModel):
    step_number: int = Field(..., ge=1)
    instruction: str = Field(..., min_length=1)
    warning: Optional[str] = None


class SopTemplateCreate(BaseModel):
    title: str = Field(..., min_length=3)
    description: Optional[str] = None
    category: str = "Operations"  # Operations | Finance | Sales | Clinical | Workshop
    frequency: str = "DAILY"  # DAILY | WEEKLY | MONTHLY | AD_HOC
    estimated_time: int = Field(15, ge=1, le=480)  # minutes
    steps: List[SopStep] = []
    assigned_roles: List[str] = []
    assigned_users: List[str] = []
    store_id: Optional[str] = None


class SopTemplateUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    frequency: Optional[str] = None
    estimated_time: Optional[int] = None
    steps: Optional[List[SopStep]] = None
    assigned_roles: Optional[List[str]] = None
    assigned_users: Optional[List[str]] = None
    is_active: Optional[bool] = None


def _sop_collection():
    """Return the sop_templates collection (or None if DB unavailable)."""
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return db.get_collection("sop_templates")
    except Exception:
        return None


def _sop_completions_collection():
    """Return the sop_completions collection (or None if DB unavailable)."""
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return db.get_collection("sop_completions")
    except Exception:
        return None


@router.get("/sop-templates")
async def list_sop_templates(
    category: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    active_only: bool = Query(True),
    current_user: dict = Depends(get_current_user),
):
    """List SOP templates. Filter by category, store, active state."""
    col = _sop_collection()
    if col is None:
        return {"templates": [], "total": 0}

    filter_dict: dict = {}
    if category:
        filter_dict["category"] = category
    if store_id:
        filter_dict["store_id"] = store_id
    if active_only:
        filter_dict["is_active"] = {"$ne": False}

    try:
        templates = list(col.find(filter_dict, {"_id": 0}).sort("updated_at", -1))
    except Exception as e:
        logger = __import__("logging").getLogger(__name__)
        logger.warning(f"[SOP] list failed: {e}")
        templates = []
    return {"templates": templates, "total": len(templates)}


@router.post("/sop-templates", status_code=201)
@router.post("/sop-templates/", status_code=201, include_in_schema=False)
async def create_sop_template(
    payload: SopTemplateCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new SOP template. SUPERADMIN / ADMIN / STORE_MANAGER only."""
    # Role gate — prevents cashiers from editing SOPs
    allowed = {"SUPERADMIN", "ADMIN", "STORE_MANAGER"}
    if not (set(current_user.get("roles", [])) & allowed):
        raise HTTPException(
            status_code=403,
            detail="Only SUPERADMIN / ADMIN / STORE_MANAGER may create SOPs",
        )

    col = _sop_collection()
    if col is None:
        raise HTTPException(status_code=503, detail="DB unavailable")

    now = datetime.now()
    template_id = f"SOP-{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "template_id": template_id,
        "title": payload.title,
        "description": payload.description or "",
        "category": payload.category,
        "frequency": payload.frequency,
        "estimated_time": payload.estimated_time,
        "steps": [s.model_dump() for s in payload.steps],
        "assigned_roles": payload.assigned_roles,
        "assigned_users": payload.assigned_users,
        "store_id": payload.store_id or current_user.get("active_store_id"),
        "is_active": True,
        "created_by": current_user.get("user_id"),
        "created_at": now,
        "updated_at": now,
    }
    try:
        col.insert_one(doc)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save SOP: {e}")
    doc.pop("_id", None)
    return {"template_id": template_id, "template": doc, "message": "SOP created"}


@router.get("/sop-templates/{template_id}")
async def get_sop_template(
    template_id: str,
    current_user: dict = Depends(get_current_user),
):
    col = _sop_collection()
    if col is None:
        raise HTTPException(status_code=503, detail="DB unavailable")
    tpl = col.find_one({"template_id": template_id}, {"_id": 0})
    if not tpl:
        raise HTTPException(status_code=404, detail="SOP template not found")
    return tpl


@router.patch("/sop-templates/{template_id}")
async def update_sop_template(
    template_id: str,
    updates: SopTemplateUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Patch an SOP template. Role-gated like create."""
    allowed = {"SUPERADMIN", "ADMIN", "STORE_MANAGER"}
    if not (set(current_user.get("roles", [])) & allowed):
        raise HTTPException(
            status_code=403,
            detail="Only SUPERADMIN / ADMIN / STORE_MANAGER may edit SOPs",
        )

    col = _sop_collection()
    if col is None:
        raise HTTPException(status_code=503, detail="DB unavailable")

    update_data = {
        k: v for k, v in updates.model_dump(exclude_unset=True).items() if v is not None
    }
    # Serialize nested steps
    if "steps" in update_data and update_data["steps"] is not None:
        update_data["steps"] = [
            s.model_dump() if hasattr(s, "model_dump") else s
            for s in update_data["steps"]
        ]
    update_data["updated_at"] = datetime.now()
    update_data["updated_by"] = current_user.get("user_id")

    result = col.update_one({"template_id": template_id}, {"$set": update_data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="SOP template not found")

    tpl = col.find_one({"template_id": template_id}, {"_id": 0})
    return {"template_id": template_id, "template": tpl, "message": "SOP updated"}


@router.delete("/sop-templates/{template_id}")
async def delete_sop_template(
    template_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Soft-delete (archive) an SOP template. Only SUPERADMIN/ADMIN."""
    allowed = {"SUPERADMIN", "ADMIN"}
    if not (set(current_user.get("roles", [])) & allowed):
        raise HTTPException(
            status_code=403,
            detail="Only SUPERADMIN / ADMIN may archive SOPs",
        )
    col = _sop_collection()
    if col is None:
        raise HTTPException(status_code=503, detail="DB unavailable")

    result = col.update_one(
        {"template_id": template_id},
        {
            "$set": {
                "is_active": False,
                "archived_at": datetime.now(),
                "archived_by": current_user.get("user_id"),
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="SOP template not found")
    return {"template_id": template_id, "message": "SOP archived"}


class SopAssignmentPayload(BaseModel):
    assigned_roles: Optional[List[str]] = None
    assigned_users: Optional[List[str]] = None


@router.post("/sop-templates/{template_id}/assign")
async def assign_sop(
    template_id: str,
    payload: SopAssignmentPayload,
    current_user: dict = Depends(get_current_user),
):
    """
    Replace the assigned_roles + assigned_users arrays on an SOP template.
    Pass null/omit a field to leave it unchanged; pass [] to clear.
    """
    allowed = {"SUPERADMIN", "ADMIN", "STORE_MANAGER"}
    if not (set(current_user.get("roles", [])) & allowed):
        raise HTTPException(status_code=403, detail="Not authorized")

    col = _sop_collection()
    if col is None:
        raise HTTPException(status_code=503, detail="DB unavailable")

    update: dict = {
        "updated_at": datetime.now(),
        "updated_by": current_user.get("user_id"),
    }
    if payload.assigned_roles is not None:
        update["assigned_roles"] = payload.assigned_roles
    if payload.assigned_users is not None:
        update["assigned_users"] = payload.assigned_users

    result = col.update_one({"template_id": template_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="SOP template not found")

    tpl = col.find_one({"template_id": template_id}, {"_id": 0})
    return {
        "template_id": template_id,
        "template": tpl,
        "message": "SOP assignment updated",
    }


# ============================================================================
# SOP DAILY CHECKLISTS — completion tracking (Tasks/SOP Phase 4)
# ============================================================================
# A checklist is a run of an SOP template at a store on a date. The template
# owns the steps; a `sop_completions` doc (one per template+store+date) tracks
# which steps are ticked. Replaces the old stubbed get_daily_checklists /
# complete_checklist_item (hard-coded items, no persistence).


class SopChecklistItemToggle(BaseModel):
    template_id: str
    step_number: int = Field(..., ge=1)
    completed: bool
    date: Optional[str] = None      # YYYY-MM-DD; defaults to today
    store_id: Optional[str] = None


@router.get("/sop-checklist")
async def get_sop_checklist(
    template_id: str = Query(...),
    date: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """An SOP template's steps merged with today's completion state + progress.
    Read-only; the completion doc is created lazily on the first toggle."""
    tcol = _sop_collection()
    if tcol is None:
        raise HTTPException(status_code=503, detail="DB unavailable")
    tpl = tcol.find_one({"template_id": template_id}, {"_id": 0})
    if not tpl:
        raise HTTPException(status_code=404, detail="SOP template not found")

    active_store = store_id or current_user.get("active_store_id")
    day = date or datetime.now().strftime("%Y-%m-%d")

    ccol = _sop_completions_collection()
    completion = None
    if ccol is not None:
        try:
            completion = ccol.find_one(
                {"template_id": template_id, "store_id": active_store, "date": day},
                {"_id": 0},
            )
        except Exception:
            completion = None

    items, progress = merge_checklist(tpl.get("steps") or [], (completion or {}).get("items"))
    return {
        "template_id": template_id,
        "title": tpl.get("title"),
        "store_id": active_store,
        "date": day,
        "items": items,
        "progress": progress,
        "status": (completion or {}).get("status") or completion_status(progress),
    }


@router.post("/sop-checklist/item")
async def toggle_sop_checklist_item(
    payload: SopChecklistItemToggle,
    current_user: dict = Depends(get_current_user),
):
    """Tick / untick one checklist step for a template+store+date. Upserts the
    sop_completions doc and returns the updated checklist + progress."""
    tcol = _sop_collection()
    ccol = _sop_completions_collection()
    if tcol is None or ccol is None:
        raise HTTPException(status_code=503, detail="DB unavailable")
    tpl = tcol.find_one({"template_id": payload.template_id}, {"_id": 0})
    if not tpl:
        raise HTTPException(status_code=404, detail="SOP template not found")

    active_store = payload.store_id or current_user.get("active_store_id")
    day = payload.date or datetime.now().strftime("%Y-%m-%d")
    now = datetime.now()
    steps = tpl.get("steps") or []

    existing = ccol.find_one(
        {"template_id": payload.template_id, "store_id": active_store, "date": day}
    )
    items = apply_item_toggle(
        (existing or {}).get("items") or [], steps,
        payload.step_number, payload.completed,
        by=current_user.get("user_id"), at=now,
    )
    merged, progress = merge_checklist(steps, items)
    status = completion_status(progress)

    doc_set = {
        "template_id": payload.template_id,
        "store_id": active_store,
        "date": day,
        "title": tpl.get("title"),
        "items": items,
        "progress": progress,
        "status": status,
        "updated_at": now,
        "updated_by": current_user.get("user_id"),
    }
    if status == "COMPLETED":
        doc_set["completed_at"] = now
        doc_set["completed_by"] = current_user.get("user_id")

    ccol.update_one(
        {"template_id": payload.template_id, "store_id": active_store, "date": day},
        {
            "$set": doc_set,
            "$setOnInsert": {
                "completion_id": f"SOPC-{uuid.uuid4().hex[:8].upper()}",
                "created_at": now,
            },
        },
        upsert=True,
    )
    return {
        "template_id": payload.template_id,
        "title": tpl.get("title"),
        "store_id": active_store,
        "date": day,
        "items": merged,
        "progress": progress,
        "status": status,
    }


@router.post("/sop-templates/seed-defaults")
async def seed_default_sop_templates(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Create the starter daily SOP templates (opening / closing / stock-count)
    for a store if they don't already exist. SUPERADMIN / ADMIN / STORE_MANAGER."""
    allowed = {"SUPERADMIN", "ADMIN", "STORE_MANAGER"}
    if not (set(current_user.get("roles", [])) & allowed):
        raise HTTPException(status_code=403, detail="Not authorized to seed SOPs")
    col = _sop_collection()
    if col is None:
        raise HTTPException(status_code=503, detail="DB unavailable")

    active_store = store_id or current_user.get("active_store_id")
    now = datetime.now()
    created = 0
    for tdef in DEFAULT_SOP_TEMPLATES:
        if col.find_one({"title": tdef["title"], "store_id": active_store}):
            continue  # already seeded for this store
        col.insert_one({
            "template_id": f"SOP-{uuid.uuid4().hex[:8].upper()}",
            "title": tdef["title"],
            "description": tdef["description"],
            "category": tdef["category"],
            "frequency": tdef["frequency"],
            "estimated_time": tdef["estimated_time"],
            "steps": default_template_steps(tdef["steps"]),
            "assigned_roles": [],
            "assigned_users": [],
            "store_id": active_store,
            "is_active": True,
            "created_by": current_user.get("user_id"),
            "created_at": now,
            "updated_at": now,
        })
        created += 1
    return {
        "created": created,
        "store_id": active_store,
        "message": f"Seeded {created} starter SOP template(s)",
    }


# ============================================================================
# Catch-all parametric routes — registered LAST so they do not shadow
# any specific path above (`/summary`, `/checklists`, `/sop-templates`,
# `/auto-generate`, etc.). FastAPI resolves routes in registration order.
# ============================================================================
router.add_api_route("/{task_id}", get_task, methods=["GET"])


# ============================================================================
# Action aliases — match what the frontend's `tasksApi` already calls
# ============================================================================
# `PUT /tasks/{id}` was 404'ing because only PATCH is decorated above.
# `POST /tasks/{id}/start` and `/reassign` likewise didn't exist.
# Thin wrappers so the task UI works end-to-end without touching FE.


class TaskReassign(BaseModel):
    assigned_to: str
    reason: Optional[str] = None


@router.put("/{task_id}")
async def put_task(
    task_id: str,
    update: TaskUpdate,
    current_user: dict = Depends(get_current_user),
):
    """PUT alias for PATCH /{task_id} — frontend uses PUT for task edits."""
    return await update_task(task_id, update, current_user)


@router.post("/{task_id}/start")
async def start_task(
    task_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Transition a task from open → in_progress."""
    repo = get_task_repository()
    if repo is None:
        return {"task_id": task_id, "status": "IN_PROGRESS"}
    task = repo.find_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    existing = canon_status(task.get("status"))
    if existing == "IN_PROGRESS":
        return {
            "task_id": task_id,
            "status": "IN_PROGRESS",
            "message": "Already in progress",
        }
    if existing == "COMPLETED":
        raise HTTPException(status_code=400, detail="Task already completed")
    now = datetime.now()
    repo.update(
        task_id,
        {
            "status": "IN_PROGRESS",
            "started_at": now,
            "updated_at": now,
            "started_by": current_user.get("user_id"),
        },
    )
    return {"task_id": task_id, "status": "IN_PROGRESS", "message": "Task started"}


@router.post("/{task_id}/reassign")
async def reassign_task(
    task_id: str,
    body: TaskReassign,
    current_user: dict = Depends(get_current_user),
):
    """Reassign an open/in-progress task to another user. Audit-logged via task history."""
    repo = get_task_repository()
    if repo is None:
        return {"task_id": task_id, "assigned_to": body.assigned_to}
    task = repo.find_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if canon_status(task.get("status")) == "COMPLETED":
        raise HTTPException(status_code=400, detail="Cannot reassign a completed task")
    history_entry = {
        "action": "reassigned",
        "from": task.get("assigned_to"),
        "to": body.assigned_to,
        "reason": body.reason,
        "by": current_user.get("user_id"),
        "at": datetime.now(),
    }
    repo.update(
        task_id,
        {
            "assigned_to": body.assigned_to,
            "reassigned_at": datetime.now(),
            "reassigned_by": current_user.get("user_id"),
            "history": (task.get("history") or []) + [history_entry],
        },
    )
    return {
        "task_id": task_id,
        "assigned_to": body.assigned_to,
        "message": "Task reassigned",
    }
