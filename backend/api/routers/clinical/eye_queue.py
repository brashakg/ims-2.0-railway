"""Clinical root + the eye-test queue: list, add, status, remove, start-test, stats.

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends, Query
from datetime import datetime
import uuid
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_eye_test_queue_repository,
    get_eye_test_repository,
    validate_store_access,
)
from ._shared import (
    _CLINICAL_ROLES,
    _QUEUE_ADD_ROLES,
    _VALID_QUEUE_STATUSES,
    _store_scope_or_404,
    router,
)
from .models import QueueItemCreate, StatusUpdate
from .helpers import _convert_to_camel, _get_empty_queue


# ============================================================================
# QUEUE ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def get_clinical_root():
    """Root endpoint for clinical/eye test queue"""
    return {
        "module": "clinical",
        "status": "active",
        "message": "clinical queue endpoint ready",
    }


@router.get("/queue")
async def get_queue(
    store_id: str = Query(..., alias="store_id"),
    current_user: dict = Depends(get_current_user),
):
    """Get eye test queue for a store"""
    # BUG-062: 403 a store-scoped caller asking for another store's queue.
    store_id = validate_store_access(store_id, current_user)
    queue_repo = get_eye_test_queue_repository()

    if queue_repo is not None:
        queue_items = queue_repo.get_store_queue(store_id)
        # Convert to camelCase and add 'id' alias
        result = []
        for item in queue_items:
            converted = _convert_to_camel(item)
            converted["id"] = item.get("queue_id")
            result.append(converted)
        return {"queue": result}

    # Return empty queue when no DB available
    return {"queue": _get_empty_queue()}


@router.post("/queue")
async def add_to_queue(
    item: QueueItemCreate,
    current_user: dict = Depends(require_roles(*_QUEUE_ADD_ROLES)),
):
    """Add a patient to the eye test queue"""
    queue_repo = get_eye_test_queue_repository()

    if queue_repo is not None:
        created = queue_repo.add_to_queue(
            store_id=item.store_id,
            patient_name=item.patient_name,
            customer_phone=item.customer_phone,
            age=item.age,
            reason=item.reason,
            customer_id=item.customer_id,
            patient_id=item.patient_id,
        )
        if created:
            result = _convert_to_camel(created)
            result["id"] = created.get("queue_id")
            return result
        raise HTTPException(status_code=500, detail="Failed to add to queue")

    # Fallback for demo
    new_item = {
        "id": str(uuid.uuid4()),
        "queueId": str(uuid.uuid4()),
        "tokenNumber": "T001",
        "patientName": item.patient_name,
        "customerPhone": item.customer_phone,
        "age": item.age,
        "reason": item.reason,
        "customerId": item.customer_id,
        "patientId": item.patient_id,
        "status": "WAITING",
        "createdAt": datetime.now().isoformat(),
        "waitTime": 0,
    }
    return new_item


@router.patch("/queue/{queue_id}/status")
async def update_queue_status(
    queue_id: str,
    body: StatusUpdate,
    current_user: dict = Depends(require_roles(*_CLINICAL_ROLES)),
):
    """Update queue item status.

    Validates the requested status against the canonical lifecycle states up
    front. The repository silently no-ops on an unknown status, so the previous
    handler returned a misleading 200 "Status updated" for garbage like
    ``{"status": "BANANA"}`` -- a caller could believe a state change happened
    that never did. We now reject an unknown status with 400.
    """
    status = (body.status or "").strip().upper()
    if status not in _VALID_QUEUE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid queue status '{body.status}'. "
                f"Allowed: {', '.join(_VALID_QUEUE_STATUSES)}"
            ),
        )

    queue_repo = get_eye_test_queue_repository()

    if queue_repo is not None:
        # The item may legitimately be absent (sample/demo data); the repo
        # no-ops in that case. We don't 404 -- the frontend treats this as a
        # best-effort state sync -- but we DO echo the normalised status.
        # Cross-store IDOR guard: when the item IS found, a store-scoped
        # caller may only mutate their own store's queue (404-hide otherwise).
        queue_item = queue_repo.find_by_id(queue_id)
        if queue_item:
            _store_scope_or_404(queue_item, current_user, "Queue item")
        queue_repo.update_status(queue_id, status)

    return {"message": "Status updated", "status": status}


@router.delete("/queue/{queue_id}")
async def remove_from_queue(
    queue_id: str, current_user: dict = Depends(require_roles(*_CLINICAL_ROLES))
):
    """Remove a patient from the queue"""
    queue_repo = get_eye_test_queue_repository()

    if queue_repo is not None:
        # Cross-store IDOR guard: a store-scoped caller may only remove items
        # from their own store's queue (404-hide otherwise). Absent items keep
        # the historical best-effort 200.
        queue_item = queue_repo.find_by_id(queue_id)
        if queue_item:
            _store_scope_or_404(queue_item, current_user, "Queue item")
        queue_repo.remove_from_queue(queue_id)

    return {"message": "Removed from queue"}


@router.post("/queue/{queue_id}/start-test")
async def start_test(
    queue_id: str, current_user: dict = Depends(require_roles(*_CLINICAL_ROLES))
):
    """Start an eye test for a queue item"""
    queue_repo = get_eye_test_queue_repository()
    test_repo = get_eye_test_repository()

    if queue_repo is not None and test_repo is not None:
        # Get queue item
        queue_item = queue_repo.find_by_id(queue_id)

        if queue_item:
            # Cross-store IDOR guard: a store-scoped caller may only start a
            # test for their own store's queue item (404-hide otherwise).
            _store_scope_or_404(queue_item, current_user, "Queue item")
            # Update queue status
            queue_repo.update_status(queue_id, "IN_PROGRESS")

            # Create test record
            test = test_repo.create_test(
                queue_id=queue_id,
                patient_name=queue_item.get("patient_name", ""),
                customer_phone=queue_item.get("customer_phone", ""),
                store_id=queue_item.get("store_id", ""),
                optometrist_id=current_user.get("user_id", ""),
                optometrist_name=current_user.get("full_name", "Unknown"),
                customer_id=queue_item.get("customer_id"),
                patient_id=queue_item.get("patient_id"),
            )

            if test:
                # Stamp the test_id back onto the queue doc so a page
                # reload mid-test still lets "Continue" resolve the
                # right test record. Without this stamp the frontend
                # fell back to queue_id-as-test_id on Continue, the
                # completion call no-op'd (find_by_id on a queue_id
                # returns nothing), and the queue stayed IN_PROGRESS
                # forever.
                queue_repo.update(queue_id, {"test_id": test.get("test_id")})
                return {"testId": test.get("test_id"), "message": "Test started"}

        # Queue item may be from sample data, create test anyway
        test_id = str(uuid.uuid4())
        return {"testId": test_id, "message": "Test started"}

    # Fallback for demo
    test_id = str(uuid.uuid4())
    return {"testId": test_id, "message": "Test started"}


@router.get("/queue/stats")
async def get_queue_stats(
    store_id: str = Query(..., alias="store_id"),
    current_user: dict = Depends(get_current_user),
):
    """Get queue statistics for today"""
    # BUG-062: 403 a store-scoped caller asking for another store's stats.
    store_id = validate_store_access(store_id, current_user)
    queue_repo = get_eye_test_queue_repository()

    if queue_repo is not None:
        return queue_repo.get_today_stats(store_id)

    # Return zeros when no DB available
    return {"total": 0, "waiting": 0, "in_progress": 0, "completed": 0, "no_show": 0}
