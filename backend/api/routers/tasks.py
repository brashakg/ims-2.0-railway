"""
IMS 2.0 - Tasks Router
=======================
Task management endpoints
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid

from .auth import get_current_user
from ..dependencies import get_task_repository

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=3)
    description: Optional[str] = None
    category: str
    priority: str = Field(default="P3")  # P1, P2, P3, P4
    assigned_to: str
    due_at: datetime
    linked_entity_type: Optional[str] = None  # ORDER, CUSTOMER, GRN, etc.
    linked_entity_id: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    due_at: Optional[datetime] = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def generate_task_number() -> str:
    """Generate unique task number"""
    return f"TASK-{datetime.now().strftime('%y%m%d')}-{str(uuid.uuid4())[:6].upper()}"


# ============================================================================
# ENDPOINTS
# ============================================================================


# NOTE: Specific routes MUST come before /{task_id} to avoid being matched as task_id
@router.get("/my")
async def my_tasks(
    include_completed: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    """Get tasks assigned to current user"""
    repo = get_task_repository()

    if repo is not None:
        tasks = repo.find_by_assignee(
            current_user.get("user_id"), include_completed=include_completed
        )
        return {"tasks": tasks, "total": len(tasks)}

    return {"tasks": [], "total": 0}


@router.get("/overdue")
async def get_overdue_tasks(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get overdue tasks"""
    repo = get_task_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        tasks = repo.find_overdue(active_store)
        return {"tasks": tasks, "total": len(tasks)}

    return {"tasks": [], "total": 0}


@router.get("/escalated")
async def get_escalated_tasks(current_user: dict = Depends(get_current_user)):
    """Get escalated tasks"""
    repo = get_task_repository()

    if repo is not None:
        # Get tasks escalated to current user
        tasks = repo.find_escalated(current_user.get("user_id"))
        return {"tasks": tasks, "total": len(tasks)}

    return {"tasks": [], "total": 0}


@router.get("/summary")
async def get_task_summary(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get task summary by status"""
    repo = get_task_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        summary = repo.get_task_summary(active_store)
        overdue_count = repo.get_overdue_count(active_store)
        return {"summary": summary, "overdue_count": overdue_count}

    return {"summary": {}, "overdue_count": 0}


@router.get("/")
async def list_tasks(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List tasks with filters"""
    repo = get_task_repository()
    active_store = store_id or current_user.get("active_store_id")

    if repo is not None:
        if assigned_to:
            tasks = repo.find_by_assignee(assigned_to, status)
        elif priority:
            tasks = repo.find_by_priority(priority, active_store)
        elif status:
            filter_dict = {"status": status}
            if active_store:
                filter_dict["store_id"] = active_store
            tasks = repo.find_many(filter_dict, skip=skip, limit=limit)
        else:
            tasks = repo.find_open(active_store)

        return {"tasks": tasks, "total": len(tasks)}

    return {"tasks": [], "total": 0}


@router.post("/", status_code=201)
async def create_task(task: TaskCreate, current_user: dict = Depends(get_current_user)):
    """Create a new task"""
    repo = get_task_repository()

    if repo is not None:
        task_data = {
            "task_number": generate_task_number(),
            "title": task.title,
            "description": task.description,
            "category": task.category,
            "priority": task.priority,
            "assigned_to": task.assigned_to,
            "store_id": current_user.get("active_store_id"),
            "due_at": task.due_at.isoformat() if task.due_at else None,
            "linked_entity_type": task.linked_entity_type,
            "linked_entity_id": task.linked_entity_id,
            "status": "OPEN",
            "created_by": current_user.get("user_id"),
        }

        created = repo.create(task_data)
        if created:
            return {
                "task_id": created["task_id"],
                "task_number": created["task_number"],
                "message": "Task created",
            }

        raise HTTPException(status_code=500, detail="Failed to create task")

    return {"task_id": str(uuid.uuid4()), "message": "Task created"}


@router.get("/{task_id}")
async def get_task(task_id: str, current_user: dict = Depends(get_current_user)):
    """Get task by ID"""
    repo = get_task_repository()

    if repo is not None:
        task = repo.find_by_id(task_id)
        if task:
            return task
        raise HTTPException(status_code=404, detail="Task not found")

    return {"task_id": task_id}


@router.put("/{task_id}")
async def update_task(
    task_id: str, task: TaskUpdate, current_user: dict = Depends(get_current_user)
):
    """Update task details"""
    repo = get_task_repository()

    if repo is not None:
        existing = repo.find_by_id(task_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Task not found")

        if existing.get("status") in ["COMPLETED", "CANCELLED"]:
            raise HTTPException(
                status_code=400, detail="Cannot update completed or cancelled tasks"
            )

        update_data = task.model_dump(exclude_unset=True)
        if "due_at" in update_data and update_data["due_at"]:
            update_data["due_at"] = update_data["due_at"].isoformat()
        update_data["updated_by"] = current_user.get("user_id")

        if repo.update(task_id, update_data):
            return {"task_id": task_id, "message": "Task updated"}

        raise HTTPException(status_code=500, detail="Failed to update task")

    return {"task_id": task_id, "message": "Task updated"}


@router.post("/{task_id}/start")
async def start_task(task_id: str, current_user: dict = Depends(get_current_user)):
    """Start working on a task"""
    repo = get_task_repository()

    if repo is not None:
        task = repo.find_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task.get("status") != "OPEN":
            raise HTTPException(status_code=400, detail="Task must be OPEN to start")

        if repo.start_task(task_id):
            return {
                "task_id": task_id,
                "status": "IN_PROGRESS",
                "message": "Task started",
            }

        raise HTTPException(status_code=500, detail="Failed to start task")

    return {"message": "Task started"}


@router.post("/{task_id}/complete")
async def complete_task(
    task_id: str,
    notes: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Mark task as complete"""
    repo = get_task_repository()

    if repo is not None:
        task = repo.find_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task.get("status") not in ["OPEN", "IN_PROGRESS", "ESCALATED"]:
            raise HTTPException(
                status_code=400, detail="Task cannot be completed in current state"
            )

        if repo.complete_task(task_id, notes or ""):
            return {
                "task_id": task_id,
                "status": "COMPLETED",
                "message": "Task completed",
            }

        raise HTTPException(status_code=500, detail="Failed to complete task")

    return {"message": "Task completed"}


@router.post("/{task_id}/escalate")
async def escalate_task(
    task_id: str,
    escalate_to: str = Query(...),
    level: int = Query(1, ge=1, le=3),
    current_user: dict = Depends(get_current_user),
):
    """Escalate task to another user"""
    repo = get_task_repository()

    if repo is not None:
        task = repo.find_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task.get("status") in ["COMPLETED", "CANCELLED"]:
            raise HTTPException(
                status_code=400, detail="Cannot escalate completed or cancelled tasks"
            )

        if repo.escalate_task(task_id, escalate_to, level):
            return {
                "task_id": task_id,
                "status": "ESCALATED",
                "message": "Task escalated",
            }

        raise HTTPException(status_code=500, detail="Failed to escalate task")

    return {"message": "Task escalated"}


@router.post("/{task_id}/reassign")
async def reassign_task(
    task_id: str,
    new_assignee: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Reassign task to another user"""
    repo = get_task_repository()

    if repo is not None:
        task = repo.find_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task.get("status") in ["COMPLETED", "CANCELLED"]:
            raise HTTPException(
                status_code=400, detail="Cannot reassign completed or cancelled tasks"
            )

        if repo.reassign_task(task_id, new_assignee, current_user.get("user_id")):
            return {"task_id": task_id, "message": f"Task reassigned to {new_assignee}"}

        raise HTTPException(status_code=500, detail="Failed to reassign task")

    return {"message": "Task reassigned"}
