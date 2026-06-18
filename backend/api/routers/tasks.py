"""
IMS 2.0 - Tasks, SOPs & Escalation System
===========================================
Complete task management with auto-escalation and SOP templates
"""

import io

from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import uuid

from .auth import get_current_user, require_roles
from ..dependencies import (
    can_access_store_scoped,
    get_task_repository,
    get_user_repository,
    get_db,
    get_order_repository,
    user_store_scope,
    validate_store_access,
)
from ..services.file_store import (
    get_file_store,
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
)
from ..utils.ist import ist_today
from ..services.task_triggers import (
    create_system_task,
    is_suspicious_closure,
    payment_anomalies,
    silent_tasks,
)
from ..services.task_sla import (
    DEFAULT_SLA,
    MAX_ESCALATION_LEVEL,
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

# Valid priority codes (P0=critical, P4=low).
VALID_PRIORITIES = {"P0", "P1", "P2", "P3", "P4"}

# Valid task statuses.
VALID_STATUSES = {"OPEN", "IN_PROGRESS", "COMPLETED", "ESCALATED", "CANCELLED"}

# Statuses from which no further lifecycle transitions are allowed.
TERMINAL_STATUSES = {"COMPLETED", "CANCELLED"}


def _ensure_task_store_access(task: dict, current_user: dict) -> None:
    """Object-level store guard for the single-task endpoints (P2 IDOR).

    A task stamped with a store_id may only be read / acted on by a caller
    whose store reach covers that store (SUPERADMIN/ADMIN are cross-store via
    can_access_store_scoped). A task with NO store_id is a GLOBAL/system task
    -> any authenticated caller may proceed (it cannot leak another store's
    data because it belongs to none).
    """
    store_id = task.get("store_id")
    if not store_id:
        return
    if not can_access_store_scoped(store_id, current_user):
        raise HTTPException(
            status_code=403, detail="No access to this task's store"
        )


# Manager-tier roles that may act on ANY task in a store they can reach
# (the same rungs the escalation ladder climbs). A non-manager who is neither
# the assignee nor the assigner/creator must not act on someone else's task.
_TASK_MANAGER_ROLES = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}


def _ensure_task_actor(task: dict, current_user: dict) -> None:
    """Object-level ownership guard for the MUTATING lifecycle actions (P2).

    ``_ensure_task_store_access`` only proves the caller is in the task's
    store -- in a shared-POS store that lets ANY low-role staffer complete /
    start / acknowledge / escalate / reassign a colleague's task, defeating
    SLA accountability (live-proven: a non-assignee SALES_STAFF completed a
    Store-Manager-owned task). Allow the action only if the caller is:
      - the assignee (assigned_to), or
      - the assigner / creator (assigned_by / created_by), or
      - a manager-tier role (STORE_MANAGER / AREA_MANAGER / ADMIN / SUPERADMIN)
        -- they have already passed the store gate, so they may manage their
        store's tasks.
    Anyone else (a plain SALES_STAFF / CASHIER who doesn't own the task) gets
    403. This is the INNER gate; ``_ensure_task_store_access`` stays the outer
    one and must be called first.
    """
    roles = {str(r).strip().upper() for r in (current_user.get("roles") or [])}
    if roles & _TASK_MANAGER_ROLES:
        return
    uid = current_user.get("user_id")
    owners = {
        task.get("assigned_to"),
        task.get("assigned_by"),
        task.get("created_by"),
    }
    if uid and uid in owners:
        return
    raise HTTPException(
        status_code=403,
        detail="Only the assignee, the assigner, or a manager may act on this task",
    )


# ============================================================================
# SCHEMAS
# ============================================================================


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=500)
    description: Optional[str] = Field(default=None, max_length=2000)
    category: str = Field(default="General", max_length=100)
    priority: str = Field(default="P3")  # P0-P4
    assigned_to: str = Field(..., min_length=1, max_length=255)
    # Canonical field is `due_at`; `due_date` accepted for backwards compat.
    due_date: Optional[datetime] = None
    due_at: Optional[datetime] = None
    # Canonical field is `source` (SYSTEM|USER|SOP); `type` (manual|sop|system)
    # accepted for backwards compat.
    type: Optional[str] = None
    source: Optional[str] = None
    # Optional file attachment. The bytes are uploaded first via
    # POST /tasks/upload-file (returns a file_id), then that id is passed here
    # so sharing a file is just creating a task that carries it (owner item #5:
    # file-sharing moved from the Hub "Send a file" handoff into the task flow).
    attachment_file_id: Optional[str] = Field(default=None, max_length=128)
    attachment_filename: Optional[str] = Field(default=None, max_length=255)
    attachment_mime: Optional[str] = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _validate_fields(self) -> "TaskCreate":
        # Priority must be one of P0-P4.
        if self.priority and self.priority.upper() not in VALID_PRIORITIES:
            raise ValueError(
                f"Invalid priority '{self.priority}'. Must be one of {sorted(VALID_PRIORITIES)}"
            )
        # due_at must not be before the current moment — creating a task that is
        # already overdue by days/weeks is almost certainly a client bug.  A
        # 5-minute grace covers minor clock drift and quick unit-test runs.
        due = self.due_at or self.due_date
        if due is not None:
            due_naive = due.replace(tzinfo=None) if due.tzinfo else due
            if due_naive < datetime.now() - timedelta(minutes=5):
                raise ValueError(
                    "due_at must not be in the past. "
                    "Use a future date/time for the task deadline."
                )
        return self


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None  # OPEN, IN_PROGRESS, COMPLETED, ESCALATED, CANCELLED
    notes: Optional[str] = None
    assigned_to: Optional[str] = None
    due_at: Optional[datetime] = None
    # Allow attaching (or replacing) a file on an existing task.
    attachment_file_id: Optional[str] = Field(default=None, max_length=128)
    attachment_filename: Optional[str] = Field(default=None, max_length=255)
    attachment_mime: Optional[str] = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _validate_fields(self) -> "TaskUpdate":
        if self.priority and self.priority.upper() not in VALID_PRIORITIES:
            raise ValueError(
                f"Invalid priority '{self.priority}'. Must be one of {sorted(VALID_PRIORITIES)}"
            )
        if self.status:
            norm = canon_status(self.status)
            if norm not in VALID_STATUSES:
                raise ValueError(
                    f"Invalid status '{self.status}'. Must be one of {sorted(VALID_STATUSES)}"
                )
        return self


class TaskComplete(BaseModel):
    completion_notes: str = Field(..., min_length=3, max_length=2000)


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


async def _escalate_reassign_notify(
    repo, task: dict, *, reason: str, by: str, now: datetime
):
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
    # Store scope. Two cases, both kept CONSISTENT with the single-task open
    # gate (_ensure_task_store_access -> can_access_store_scoped) so the Hub
    # list never surfaces a task the SAME caller would 403 on opening:
    #
    # (a) explicit ?store_id  -> validate_store_access (a store-scoped role
    #     asking for ANOTHER store is 403'd; admins/area-managers pass per reach).
    # (b) omitted store_id    -> admins see ALL stores (no store filter); a
    #     store-scoped caller is constrained to the caller's FULL reach
    #     (store_ids UNION active_store_id) PLUS global/no-store tasks via $in.
    #
    # The previous code filtered by a SINGLE value (active_store_id only), which
    # diverged from the open-gate's full-reach model: a multi-store caller (e.g.
    # AREA_MANAGER) could be shown an other-store row whose store wasn't their
    # active one yet open fine, or shown nothing for stores in their reach. We
    # now mirror the gate exactly. (BUG-062 / Hub-task-403.)
    if store_id:
        filters["store_id"] = validate_store_access(store_id, current_user)
    else:
        is_cross, stores = user_store_scope(current_user)
        if not is_cross:
            # A store-scoped caller may see: any of THEIR OWN stores, plus
            # GLOBAL (no-store) system/HQ tasks -- which the single-task open
            # gate (_ensure_task_store_access) lets ANY caller open (a task
            # with no store_id belongs to no store, so it cannot leak another
            # store's data). $in includes None so a null/missing store_id row
            # matches. Worst case (empty reach) -> only global tasks, no leak.
            allowed = sorted(stores) + [None]
            filters["store_id"] = {"$in": allowed}
        # cross-store roles (SUPERADMIN/ADMIN): no store_id filter -> all stores.

    tasks = [
        _canon_task_out(t) for t in repo.find_many(filters, skip=skip, limit=limit)
    ]
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

    # Optional attachment: validate the referenced file actually exists in the
    # store BEFORE persisting (a forged/missing id -> 400, not a later 404 at
    # download time -- mirrors the GRN attachment gate). Storage-down -> 503.
    attachment = None
    if task.attachment_file_id and str(task.attachment_file_id).strip():
        fid = str(task.attachment_file_id).strip()
        fs = get_file_store()
        if fs is None:
            raise HTTPException(status_code=503, detail="File storage unavailable")
        if fs.get(fid) is None:
            raise HTTPException(
                status_code=400,
                detail="attachment_file_id does not reference a stored file",
            )
        attachment = {
            "file_id": fid,
            "filename": task.attachment_filename,
            "mime_type": task.attachment_mime,
        }

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
        "attachment": attachment,
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


# ============================================================================
# ENDPOINTS: TASK ATTACHMENTS (owner item #5 — file-sharing via tasks)
# ============================================================================
# Sharing a file with a colleague is now "create a task assigned to them with
# the file attached" (replaces the Hub "Send a file" handoff). The bytes are
# stored in the same GridFS file store the handoffs / GRN / expense-bill flows
# use. Upload returns a file_id; create/update persists it on the task doc;
# download streams it with the same store-scope/role gate as opening the task.


@router.post("/upload-file")
async def upload_task_file(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Upload a file (image or PDF, <=25 MB) and get back a file_id to attach to
    a task. Mirrors the GRN / expense-bill upload pattern: size + MIME
    validation, then store.put(...). Persists the bytes durably (Railway disk is
    ephemeral). 503 if storage is unavailable."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB cap",
        )
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File type '{mime}' not allowed. Accepted: "
                f"{sorted(ALLOWED_MIME_TYPES)}"
            ),
        )

    store = get_file_store()
    if store is None:
        raise HTTPException(status_code=503, detail="File storage unavailable")

    file_id = store.put(
        content=content,
        filename=file.filename,
        mime_type=mime,
        metadata={
            "kind": "task_attachment",
            "store_id": current_user.get("active_store_id"),
            "uploaded_by": current_user.get("user_id"),
        },
    )
    if not file_id:
        raise HTTPException(status_code=500, detail="File store write failed")

    return {
        "file_id": file_id,
        "filename": file.filename,
        "mime": mime,
        "size": len(content),
        "persisted": True,
    }


@router.get("/{task_id}/file")
async def download_task_file(
    task_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Stream a task's attached file. Permission: anyone who can SEE the task
    (same store-scope gate as opening it) may fetch its file."""
    repo = get_task_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    task = repo.find_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Same gate as GET /{task_id}: a task with no store_id is global (any caller);
    # a store-stamped task only for callers whose reach covers that store.
    _ensure_task_store_access(task, current_user)

    attachment = task.get("attachment") or {}
    file_id = attachment.get("file_id")
    if not file_id:
        raise HTTPException(status_code=404, detail="No file attached to this task")

    store = get_file_store()
    if store is None:
        raise HTTPException(status_code=503, detail="File storage unavailable")
    rec = store.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="File no longer available")

    file_content, filename, file_mime = rec
    return StreamingResponse(
        io.BytesIO(file_content),
        media_type=file_mime,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


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
            "$in": [
                "OPEN",
                "IN_PROGRESS",
                "ESCALATED",
                "open",
                "in_progress",
                "escalated",
            ]
        }

    # limit=0 -> unbounded (PR-#522 idiom): find_many's default limit=100
    # silently dropped tasks past the first 100 from the user's work list.
    tasks = [
        _canon_task_out(t)
        for t in repo.find_many(
            filters, sort=[("priority", 1), ("due_at", 1)], limit=0
        )
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

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    now = datetime.now()
    filters: dict = {
        "status": {"$in": ["OPEN", "IN_PROGRESS", "open", "in_progress"]},
        "$or": [{"due_at": {"$lt": now}}, {"due_date": {"$lt": now}}],
    }

    if active_store:
        filters["store_id"] = active_store

    # limit=0 -> unbounded (PR-#522 idiom): the default limit=100 silently hid
    # overdue tasks past the first 100 from the escalation view.
    tasks = [
        _canon_task_out(t)
        for t in repo.find_many(filters, sort=[("due_at", 1)], limit=0)
    ]

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

    _ensure_task_store_access(task, current_user)

    return _canon_task_out(task)


@router.patch("/{task_id}")
async def update_task(
    task_id: str,
    update: TaskUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update task (status, notes, reassign).

    Lifecycle guards:
    * Terminal tasks (COMPLETED / CANCELLED) cannot be modified at all.
    * Reassignment via PATCH is blocked on terminal tasks.
    * Backward status transitions (e.g. COMPLETED -> OPEN) are rejected.
    """
    repo = get_task_repository()

    if repo is None:
        return {"task_id": task_id, "message": "Task updated"}

    existing = repo.find_by_id(task_id)

    if not existing:
        raise HTTPException(status_code=404, detail="Task not found")

    _ensure_task_store_access(existing, current_user)

    current_status = canon_status(existing.get("status"))

    # Block ALL modifications to terminal tasks to prevent history rewriting.
    if current_status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot modify a task in '{current_status}' status.",
        )

    update_data = {}

    if update.title:
        update_data["title"] = update.title
    if update.description is not None:
        update_data["description"] = update.description
    if update.priority:
        update_data["priority"] = update.priority
    if update.status:
        new_status = canon_status(update.status)
        # Enforce valid forward-only transitions.
        # Allowed: any non-terminal -> any other non-terminal (e.g. OPEN->IN_PROGRESS,
        # ESCALATED->IN_PROGRESS after de-escalation), or non-terminal -> terminal.
        # Blocked: moving OUT of a terminal status (caught above) or setting a
        # terminal status without going through the dedicated /complete endpoint
        # (which records completed_at / completed_by).  COMPLETED is routed to
        # POST /{id}/complete; CANCELLED is allowed here (manager can cancel).
        if new_status == "COMPLETED":
            raise HTTPException(
                status_code=400,
                detail="Use POST /{task_id}/complete to complete a task (records "
                "completion notes, completed_by, completed_at).",
            )
        update_data["status"] = new_status
        update_data["history"] = (existing.get("history") or []) + [
            {
                "action": "status_change",
                "from": current_status,
                "to": new_status,
                "by": current_user.get("user_id"),
                "at": datetime.now(),
            }
        ]
    if update.notes:
        update_data["notes"] = update.notes
    if update.assigned_to:
        update_data["assigned_to"] = update.assigned_to
    if update.due_at is not None:
        update_data["due_at"] = update.due_at
    if update.attachment_file_id is not None:
        fid = str(update.attachment_file_id).strip()
        if fid:
            # Validate the referenced file exists (forged/missing -> 400).
            fs = get_file_store()
            if fs is None:
                raise HTTPException(status_code=503, detail="File storage unavailable")
            if fs.get(fid) is None:
                raise HTTPException(
                    status_code=400,
                    detail="attachment_file_id does not reference a stored file",
                )
            update_data["attachment"] = {
                "file_id": fid,
                "filename": update.attachment_filename,
                "mime_type": update.attachment_mime,
            }
        else:
            # Empty string clears the attachment (the blob is swept by NEXUS).
            update_data["attachment"] = None

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

    _ensure_task_store_access(task, current_user)
    _ensure_task_actor(task, current_user)

    current_status = canon_status(task.get("status"))

    # Block re-completing or completing a cancelled task.
    if current_status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Task is already in '{current_status}' status and cannot be completed.",
        )

    now = datetime.now()
    result = repo.update(
        task_id,
        {
            "status": "COMPLETED",
            "completed_at": now,
            "updated_at": now,
            "completion_notes": completion.completion_notes,
            "completed_by": current_user.get("user_id"),
            "history": (task.get("history") or [])
            + [
                {
                    "action": "completed",
                    "from": current_status,
                    "by": current_user.get("user_id"),
                    "notes": completion.completion_notes,
                    "at": now,
                }
            ],
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

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

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
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

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
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    if repo is None:
        return {"generated": 0, "message": "Auto-generate failed"}

    # Persisted DAILY templates assigned to this store (or global).
    templates: list = []
    scol = _sop_collection()
    if scol is not None:
        try:
            templates = list(
                scol.find(
                    {
                        "frequency": "DAILY",
                        "is_active": {"$ne": False},
                        "$or": [{"store_id": active_store}, {"store_id": None}],
                    },
                    {"_id": 0},
                )
            )
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

    _ensure_task_store_access(task, current_user)
    _ensure_task_actor(task, current_user)

    current_status = canon_status(task.get("status"))

    # Only OPEN tasks need to be acknowledged (idempotent for IN_PROGRESS).
    if current_status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot acknowledge a task in '{current_status}' status.",
        )
    if current_status == "IN_PROGRESS":
        return {
            "task_id": task_id,
            "status": "IN_PROGRESS",
            "message": "Task already acknowledged",
        }

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

    _ensure_task_store_access(task, current_user)
    _ensure_task_actor(task, current_user)

    current_status = canon_status(task.get("status"))
    if current_status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot escalate a task in '{current_status}' status.",
        )

    # Storm guard (P3): cap manual escalation at the top of the ladder, exactly
    # like the AUTO path (should_escalate stops at MAX_ESCALATION_LEVEL). Once a
    # task is at/above the top rung there is no higher owner; without this the
    # manual button kept bumping escalation_level + history unboundedly with
    # escalated_to=null. Return 200 (no-op) so the UI doesn't error.
    if int(task.get("escalation_level", 0) or 0) >= MAX_ESCALATION_LEVEL:
        return {
            "task_id": task_id,
            "status": canon_status(task.get("status")),
            "escalation_level": int(task.get("escalation_level", 0) or 0),
            "escalated_to": task.get("escalated_to"),
            "message": "Task already at the top of the escalation ladder",
        }

    now = datetime.now()
    by = current_user.get("user_id")

    if escalate_to:
        # Explicit target chosen by the user -- reassign ownership to them.
        new_level = task.get("escalation_level", 0) + 1
        history_entry = {
            "action": "escalated",
            "level": new_level,
            "reason": "manual",
            "from": task.get("assigned_to"),
            "to": escalate_to,
            "by": by,
            "at": now,
        }
        repo.update(
            task_id,
            {
                "status": "ESCALATED",
                "assigned_to": escalate_to,
                "escalated_to": escalate_to,
                "escalated_at": now,
                "updated_at": now,
                "escalation_level": new_level,
                "escalated_by": by,
                "history": (task.get("history") or []) + [history_entry],
            },
        )
        # Alert the explicit target (in-app + WhatsApp).
        user_repo = get_user_repository()
        target_user = None
        if user_repo is not None:
            try:
                target_user = user_repo.find_by_id(escalate_to)
            except Exception:
                target_user = None
        await notify_escalation(
            _notifications_coll(),
            target_user or {"user_id": escalate_to},
            task,
            "manual",
            now=now,
        )
        return {
            "task_id": task_id,
            "status": "ESCALATED",
            "escalation_level": new_level,
            "escalated_to": escalate_to,
            "message": "Task escalated",
        }

    # Auto-resolve up the ladder.
    target = await _escalate_reassign_notify(
        repo, task, reason="manual", by=by, now=now
    )
    return {
        "task_id": task_id,
        "status": "ESCALATED",
        "escalation_level": task.get("escalation_level", 0) + 1,
        "escalated_to": (target or {}).get("user_id"),
        "message": (
            f"Task escalated to {target.get('user_id')}"
            if target
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

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    # Candidate set: every non-terminal task (incl. already-ESCALATED, which
    # may climb the ladder again on a fresh breach). The precise decision (ack
    # clock + overdue grace + re-escalation cadence, per priority) is made by
    # the pure should_escalate() below -- a task can breach its ack SLA before
    # it is even past due, so we can't pre-filter on due date alone.
    filters: dict = {
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
# ENDPOINTS: VARIANCE-DRIVEN AUTOMATION + INTEGRITY DETECTORS
# ============================================================================

_SCAN_ROLES = ("STORE_MANAGER", "AREA_MANAGER", "ADMIN", "ACCOUNTANT")
_INTEGRITY_ROLES = ("STORE_MANAGER", "AREA_MANAGER", "ADMIN")


@router.post("/scan/payment-variance")
async def scan_payment_variance(
    days: int = Query(7, ge=1, le=90),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_SCAN_ROLES)),
):
    """Scan recent orders for money that doesn't reconcile (overpaid /
    payments-mismatch / delivered-but-unpaid) and raise a SYSTEM task per
    offending order (deduped). Safe to run repeatedly / on a schedule."""
    order_repo = get_order_repository()
    task_repo = get_task_repository()
    if order_repo is None:
        return {"scanned": 0, "anomalies": 0, "tasks_created": 0}

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    filters: dict = {"created_at": {"$gte": cutoff}}
    if active_store:
        filters["store_id"] = active_store

    try:
        orders = order_repo.find_many(filters, limit=2000) or []
    except Exception:  # noqa: BLE001
        orders = []

    anomalies = payment_anomalies(orders)
    created = 0
    for a in anomalies:
        t = create_system_task(
            task_repo,
            title=f"Payment variance on order {a['order_id']}",
            description=f"{a['kind']}: {a['detail']}. Verify the payment record.",
            priority="P2",
            category="Finance",
            store_id=active_store,
            dedupe_ref=f"payvar:{a['order_id']}",
        )
        if t:
            created += 1

    return {
        "scanned": len(orders),
        "anomalies": len(anomalies),
        "tasks_created": created,
        "details": anomalies[:50],
    }


@router.get("/integrity/fake-closures")
async def list_fake_closures(
    days: int = Query(30, ge=1, le=180),
    store_id: Optional[str] = Query(None),
    min_seconds: int = Query(20, ge=1, le=600),
    current_user: dict = Depends(require_roles(*_INTEGRITY_ROLES)),
):
    """Tasks marked complete implausibly fast (possible box-ticking). Read-only."""
    repo = get_task_repository()
    if repo is None:
        return {"flagged": [], "count": 0}

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    cutoff = datetime.now() - timedelta(days=days)
    filters: dict = {
        "status": {
            "$in": [
                "COMPLETED",
                "DONE",
                "CLOSED",
                "RESOLVED",
                "completed",
                "done",
                "closed",
            ]
        }
    }
    if active_store:
        filters["store_id"] = active_store

    try:
        candidates = repo.find_many(filters, limit=1000) or []
    except Exception:  # noqa: BLE001
        candidates = []

    flagged = []
    for t in candidates:
        created = t.get("created_at")
        cdt = created if isinstance(created, datetime) else None
        if cdt is not None and cdt < cutoff:
            continue
        if is_suspicious_closure(t, min_seconds=min_seconds):
            flagged.append(_canon_task_out(t))
    return {"flagged": flagged, "count": len(flagged)}


@router.get("/integrity/silent")
async def list_silent_tasks(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_INTEGRITY_ROLES)),
):
    """OPEN tasks never acknowledged within their ack-SLA window. Read-only;
    the auto-escalator acts on these, this just surfaces them for a manager."""
    repo = get_task_repository()
    if repo is None:
        return {"silent": [], "count": 0}

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    filters: dict = {"status": {"$in": ["OPEN", "open"]}}
    if active_store:
        filters["store_id"] = active_store

    try:
        candidates = repo.find_many(filters, limit=1000) or []
    except Exception:  # noqa: BLE001
        candidates = []

    silent = silent_tasks(candidates, now=datetime.now(), sla_config=_load_sla_config())
    return {"silent": [_canon_task_out(t) for t in silent], "count": len(silent)}


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

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

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
        {
            "$set": {
                "matrix": clean,
                "updated_at": now,
                "updated_by": current_user.get("user_id"),
            }
        },
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


VALID_SOP_CATEGORIES = {"Operations", "Finance", "Sales", "Clinical", "Workshop"}
VALID_SOP_FREQUENCIES = {"DAILY", "WEEKLY", "MONTHLY", "AD_HOC"}


class SopStep(BaseModel):
    step_number: int = Field(..., ge=1)
    instruction: str = Field(..., min_length=1, max_length=1000)
    warning: Optional[str] = Field(default=None, max_length=500)


def _validate_sop_steps(steps: Optional[List[SopStep]]) -> Optional[List[SopStep]]:
    """Raise ValueError if any step_numbers are duplicated."""
    if not steps:
        return steps
    seen = set()
    for s in steps:
        if s.step_number in seen:
            raise ValueError(
                f"Duplicate step_number {s.step_number} in SOP steps. "
                "Each step must have a unique step_number."
            )
        seen.add(s.step_number)
    return steps


class SopTemplateCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=500)
    description: Optional[str] = Field(default=None, max_length=2000)
    category: str = "Operations"  # Operations | Finance | Sales | Clinical | Workshop
    frequency: str = "DAILY"  # DAILY | WEEKLY | MONTHLY | AD_HOC
    estimated_time: int = Field(15, ge=1, le=480)  # minutes
    steps: List[SopStep] = []
    assigned_roles: List[str] = []
    assigned_users: List[str] = []
    store_id: Optional[str] = None

    @model_validator(mode="after")
    def _validate_fields(self) -> "SopTemplateCreate":
        if self.category not in VALID_SOP_CATEGORIES:
            raise ValueError(
                f"Invalid category '{self.category}'. "
                f"Must be one of {sorted(VALID_SOP_CATEGORIES)}"
            )
        if self.frequency.upper() not in VALID_SOP_FREQUENCIES:
            raise ValueError(
                f"Invalid frequency '{self.frequency}'. "
                f"Must be one of {sorted(VALID_SOP_FREQUENCIES)}"
            )
        _validate_sop_steps(self.steps)
        return self


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

    @model_validator(mode="after")
    def _validate_fields(self) -> "SopTemplateUpdate":
        if self.category is not None and self.category not in VALID_SOP_CATEGORIES:
            raise ValueError(
                f"Invalid category '{self.category}'. "
                f"Must be one of {sorted(VALID_SOP_CATEGORIES)}"
            )
        if (
            self.frequency is not None
            and self.frequency.upper() not in VALID_SOP_FREQUENCIES
        ):
            raise ValueError(
                f"Invalid frequency '{self.frequency}'. "
                f"Must be one of {sorted(VALID_SOP_FREQUENCIES)}"
            )
        _validate_sop_steps(self.steps)
        return self


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
        # BUG-062: authorise the requested store (store roles 403 cross-store).
        filter_dict["store_id"] = validate_store_access(store_id, current_user)
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
    date: Optional[str] = None  # YYYY-MM-DD; defaults to today
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

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    # IST audit: the checklist day key must be the IST business day. The UTC
    # day (datetime.now() on Railway) put late-night reads on yesterday's doc.
    day = date or ist_today().isoformat()

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

    items, progress = merge_checklist(
        tpl.get("steps") or [], (completion or {}).get("items")
    )
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
    # IST audit: same IST day key as the GET above -- a tick at 23:30 IST must
    # land on TODAY's completion doc, not yesterday's (UTC) one.
    day = payload.date or ist_today().isoformat()
    now = datetime.now()
    steps = tpl.get("steps") or []

    # Validate that the requested step_number actually exists in this template.
    valid_step_numbers = {s.get("step_number") for s in steps}
    if payload.step_number not in valid_step_numbers:
        raise HTTPException(
            status_code=422,
            detail=(
                f"step_number {payload.step_number} does not exist in template "
                f"'{payload.template_id}'. Valid steps: {sorted(valid_step_numbers)}"
            ),
        )

    existing = ccol.find_one(
        {"template_id": payload.template_id, "store_id": active_store, "date": day}
    )
    items = apply_item_toggle(
        (existing or {}).get("items") or [],
        steps,
        payload.step_number,
        payload.completed,
        by=current_user.get("user_id"),
        at=now,
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

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    now = datetime.now()
    created = 0
    for tdef in DEFAULT_SOP_TEMPLATES:
        if col.find_one({"title": tdef["title"], "store_id": active_store}):
            continue  # already seeded for this store
        col.insert_one(
            {
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
            }
        )
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
    assigned_to: str = Field(..., min_length=1)
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
    _ensure_task_store_access(task, current_user)
    _ensure_task_actor(task, current_user)
    existing = canon_status(task.get("status"))
    if existing == "IN_PROGRESS":
        return {
            "task_id": task_id,
            "status": "IN_PROGRESS",
            "message": "Already in progress",
        }
    # Block starting a task that is already terminal (COMPLETED or CANCELLED).
    if existing in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start a task in '{existing}' status.",
        )
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
    _ensure_task_store_access(task, current_user)
    _ensure_task_actor(task, current_user)
    task_status = canon_status(task.get("status"))
    if task_status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reassign a task in '{task_status}' status.",
        )
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
