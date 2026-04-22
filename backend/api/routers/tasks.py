"""
IMS 2.0 - Tasks, SOPs & Escalation System
===========================================
Complete task management with auto-escalation and SOP templates
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timedelta
import uuid

from .auth import get_current_user
from ..dependencies import get_task_repository, get_db

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=3)
    description: Optional[str] = None
    priority: str = Field(default="P3")  # P0-P4
    assigned_to: str
    due_date: datetime
    type: str = Field(default="manual")  # manual, sop, system


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None  # open, in_progress, completed, escalated
    notes: Optional[str] = None
    assigned_to: Optional[str] = None


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


def should_escalate(task: dict) -> tuple[bool, str]:
    """
    Determine if task should be escalated.
    Returns (should_escalate, reason)
    """
    if task.get("status") == "completed":
        return False, ""
    
    created_at = task.get("created_at")
    due_date = task.get("due_date")
    now = datetime.now()
    
    # Check if not acknowledged within 2 hours (if in OPEN status)
    if task.get("status") == "open" and created_at:
        time_since_created = now - created_at
        if time_since_created > timedelta(hours=2):
            return True, "Not acknowledged within 2 hours"
    
    # Check if overdue
    if due_date and now > due_date:
        return True, "Task overdue"
    
    return False, ""


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
    
    filters = {}
    
    if status:
        filters["status"] = status
    if priority:
        filters["priority"] = priority
    if assigned_to:
        filters["assigned_to"] = assigned_to
    if task_type:
        filters["type"] = task_type
    if store_id:
        filters["store_id"] = store_id
    else:
        filters["store_id"] = current_user.get("active_store_id")
    
    tasks = repo.find_many(filters, skip=skip, limit=limit)
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
    
    task_data = {
        "task_id": generate_task_id(),
        "title": task.title,
        "description": task.description,
        "priority": task.priority,  # P0, P1, P2, P3, P4
        "status": "open",
        "assigned_to": task.assigned_to,
        "assigned_by": current_user.get("user_id"),
        "store_id": current_user.get("active_store_id"),
        "type": task.type,  # manual, sop, system
        "due_date": task.due_date,
        "created_at": datetime.now(),
        "escalation_level": 0,
        "history": [{
            "status": "open",
            "timestamp": datetime.now(),
            "by": current_user.get("user_id"),
            "notes": "Task created"
        }]
    }
    
    result = repo.create(task_data)
    
    if result:
        return {
            "task_id": result.get("task_id"),
            "message": "Task created successfully"
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
    
    filters = {"assigned_to": current_user.get("user_id")}
    
    if status:
        filters["status"] = status
    else:
        # By default, exclude completed
        filters["status"] = {"$in": ["open", "in_progress", "escalated"]}
    
    tasks = repo.find_many(filters, sort=[("priority", 1), ("due_date", 1)])
    
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
    
    filters = {
        "status": {"$in": ["open", "in_progress"]},
        "due_date": {"$lt": datetime.now()}
    }
    
    if active_store:
        filters["store_id"] = active_store
    
    tasks = repo.find_many(filters, sort=[("due_date", 1)])
    
    return {"tasks": tasks, "total": len(tasks)}


@router.get("/{task_id}")
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
    
    return task


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
        update_data["status"] = update.status
    if update.notes:
        update_data["notes"] = update.notes
    if update.assigned_to:
        update_data["assigned_to"] = update.assigned_to
    
    if update_data:
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
        return {"task_id": task_id, "status": "completed"}
    
    task = repo.find_by_id(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if task.get("status") == "completed":
        raise HTTPException(status_code=400, detail="Task already completed")
    
    result = repo.update(task_id, {
        "status": "completed",
        "completed_at": datetime.now(),
        "completion_notes": completion.completion_notes,
        "completed_by": current_user.get("user_id")
    })
    
    if result:
        return {
            "task_id": task_id,
            "status": "completed",
            "message": "Task completed"
        }
    
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
            "progress": 0
        }
    
    active_store = store_id or current_user.get("active_store_id")
    
    # Get SOP template for this checklist type
    filters = {
        "type": checklist_type,
        "store_id": active_store,
        "active": True
    }
    
    # This would query sop_templates collection
    # For now, return structure
    return {
        "type": checklist_type,
        "store_id": active_store,
        "items": [
            "Check inventory",
            "Verify stock count",
            "Update system"
        ],
        "completed_items": [False, False, False],
        "progress": "0/3"
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
        "message": "Checklist item updated"
    }


# ============================================================================
# ENDPOINTS: AUTO-GENERATION & SOPs
# ============================================================================


@router.post("/auto-generate")
async def auto_generate_daily_tasks(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Auto-generate daily tasks from SOP templates"""
    repo = get_task_repository()
    active_store = store_id or current_user.get("active_store_id")
    
    if repo is None:
        return {"generated": 0, "message": "Auto-generate failed"}
    
    # Default SOP templates for optical retail
    sop_templates = [
        {
            "name": "Opening Checklist",
            "type": "opening",
            "items": [
                "Disarm security system",
                "Turn on all lights and AC",
                "Check cash register float",
                "Clean display cases",
                "Boot up POS system",
                "Verify network connectivity"
            ]
        },
        {
            "name": "Closing Checklist",
            "type": "closing",
            "items": [
                "Count cash in register",
                "Reconcile payment methods",
                "Update daily sales",
                "Lock cash in safe",
                "Clean store",
                "Set security system"
            ]
        },
        {
            "name": "Stock Count",
            "type": "stock_count",
            "items": [
                "Count frames in display",
                "Count lenses in inventory",
                "Check expiry dates",
                "Verify low stock items",
                "Update stock report"
            ]
        }
    ]
    
    generated_count = 0
    
    for template in sop_templates:
        task_data = {
            "task_id": generate_task_id(),
            "title": template["name"],
            "description": f"Daily {template['type']} checklist",
            "priority": "P2",
            "status": "open",
            "assigned_to": current_user.get("user_id"),
            "assigned_by": current_user.get("user_id"),
            "store_id": active_store,
            "type": "sop",
            "due_date": datetime.now() + timedelta(days=1),
            "created_at": datetime.now(),
            "escalation_level": 0,
            "sop_type": template["type"],
            "checklist_items": [{"text": item, "completed": False} for item in template["items"]]
        }
        
        result = repo.create(task_data)
        if result:
            generated_count += 1
    
    return {
        "generated": generated_count,
        "message": f"Generated {generated_count} daily tasks from SOP templates"
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
    
    if repo.update(task_id, {
        "status": "in_progress",
        "acknowledged_at": datetime.now(),
        "acknowledged_by": current_user.get("user_id")
    }):
        return {
            "task_id": task_id,
            "status": "in_progress",
            "message": "Task acknowledged"
        }
    
    raise HTTPException(status_code=500, detail="Failed to acknowledge task")


@router.post("/{task_id}/escalate")
async def escalate_task(
    task_id: str,
    escalate_to: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Manually escalate task"""
    repo = get_task_repository()
    
    if repo is None:
        return {"task_id": task_id, "status": "escalated"}
    
    task = repo.find_by_id(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    current_level = task.get("escalation_level", 0)
    
    if repo.update(task_id, {
        "status": "escalated",
        "escalated_to": escalate_to,
        "escalated_at": datetime.now(),
        "escalation_level": current_level + 1,
        "escalated_by": current_user.get("user_id")
    }):
        return {
            "task_id": task_id,
            "status": "escalated",
            "escalation_level": current_level + 1,
            "message": "Task escalated"
        }
    
    raise HTTPException(status_code=500, detail="Failed to escalate task")


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
    
    filters = {
        "status": {"$in": ["open", "in_progress"]},
        "due_date": {"$lt": datetime.now()},
        "escalation_level": {"$lt": 2}  # Don't escalate beyond level 2
    }
    
    if active_store:
        filters["store_id"] = active_store
    
    overdue_tasks = repo.find_many(filters)
    escalated_count = 0
    
    for task in overdue_tasks:
        should_escalate_flag, reason = should_escalate(task)
        
        if should_escalate_flag:
            current_level = task.get("escalation_level", 0)
            escalate_to = current_user.get("user_id")  # In real app, determine based on level
            
            if repo.update(task["task_id"], {
                "status": "escalated",
                "escalated_to": escalate_to,
                "escalated_at": datetime.now(),
                "escalation_level": current_level + 1,
                "escalation_reason": reason
            }):
                escalated_count += 1
    
    return {
        "escalated": escalated_count,
        "message": f"Auto-escalated {escalated_count} overdue tasks"
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
        return {
            "summary": {},
            "overdue_count": 0,
            "escalated_count": 0
        }
    
    active_store = store_id or current_user.get("active_store_id")
    
    filters = {}
    if active_store:
        filters["store_id"] = active_store
    
    # Count by status
    summary = {}
    for status in ["open", "in_progress", "completed", "escalated"]:
        count = repo.count({**filters, "status": status})
        summary[status] = count
    
    # Count overdue
    overdue_count = repo.count({
        **filters,
        "status": {"$in": ["open", "in_progress"]},
        "due_date": {"$lt": datetime.now()}
    })
    
    # Count escalated
    escalated_count = repo.count({**filters, "status": "escalated"})
    
    return {
        "summary": summary,
        "overdue_count": overdue_count,
        "escalated_count": escalated_count,
        "total": sum(summary.values())
    }


# ============================================================================
# SOP TEMPLATES (Phase 6.14)
# ============================================================================
# Persisted SOPs in the `sop_templates` collection. Replaces the static
# DEFAULT_CHECKLISTS dict on the frontend so SUPERADMIN can edit items +
# assign templates to roles/users/stores. Checklist completion is still
# a per-session concern (lives on the frontend + complete-item endpoint
# above); this is just the template source of truth.


class SopStep(BaseModel):
    step_number: int = Field(..., ge=1)
    instruction: str = Field(..., min_length=1)
    warning: Optional[str] = None


class SopTemplateCreate(BaseModel):
    title: str = Field(..., min_length=3)
    description: Optional[str] = None
    category: str = "Operations"          # Operations | Finance | Sales | Clinical | Workshop
    frequency: str = "DAILY"              # DAILY | WEEKLY | MONTHLY | AD_HOC
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

    update_data = {k: v for k, v in updates.model_dump(exclude_unset=True).items() if v is not None}
    # Serialize nested steps
    if "steps" in update_data and update_data["steps"] is not None:
        update_data["steps"] = [
            s.model_dump() if hasattr(s, "model_dump") else s for s in update_data["steps"]
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
        {"$set": {"is_active": False, "archived_at": datetime.now(),
                  "archived_by": current_user.get("user_id")}},
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

    update: dict = {"updated_at": datetime.now(), "updated_by": current_user.get("user_id")}
    if payload.assigned_roles is not None:
        update["assigned_roles"] = payload.assigned_roles
    if payload.assigned_users is not None:
        update["assigned_users"] = payload.assigned_users

    result = col.update_one({"template_id": template_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="SOP template not found")

    tpl = col.find_one({"template_id": template_id}, {"_id": 0})
    return {"template_id": template_id, "template": tpl, "message": "SOP assignment updated"}
