"""
IMS 2.0 - Clinical Router
==========================
Eye test queue and clinical management endpoints
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, date
import uuid
from .auth import get_current_user

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================

class QueueItemCreate(BaseModel):
    store_id: str = Field(..., alias="storeId")
    patient_name: str = Field(..., alias="patientName")
    customer_phone: str = Field(..., alias="customerPhone")
    age: Optional[int] = None
    reason: Optional[str] = None
    customer_id: Optional[str] = Field(None, alias="customerId")

    class Config:
        populate_by_name = True


class EyeTestData(BaseModel):
    right_eye: dict = Field(..., alias="rightEye")
    left_eye: dict = Field(..., alias="leftEye")
    pd: Optional[float] = None
    notes: Optional[str] = None

    class Config:
        populate_by_name = True


# ============================================================================
# IN-MEMORY STORAGE (Replace with database in production)
# ============================================================================

# Sample queue data
_queue_storage: dict = {}
_test_storage: dict = {}


def _get_sample_queue(store_id: str) -> List[dict]:
    """Return sample queue data for demo"""
    return [
        {
            "id": "q1",
            "tokenNumber": "T001",
            "patientName": "Rahul Sharma",
            "customerPhone": "9876543210",
            "age": 35,
            "reason": "Routine checkup",
            "status": "WAITING",
            "createdAt": datetime.now().replace(hour=10, minute=0).isoformat(),
            "waitTime": 15
        },
        {
            "id": "q2",
            "tokenNumber": "T002",
            "patientName": "Priya Patel",
            "customerPhone": "9876543211",
            "age": 28,
            "reason": "New glasses",
            "status": "IN_PROGRESS",
            "createdAt": datetime.now().replace(hour=10, minute=30).isoformat(),
            "waitTime": 30
        },
        {
            "id": "q3",
            "tokenNumber": "T003",
            "patientName": "Amit Kumar",
            "customerPhone": "9876543212",
            "age": 45,
            "reason": "Eye strain",
            "status": "WAITING",
            "createdAt": datetime.now().replace(hour=11, minute=0).isoformat(),
            "waitTime": 10
        }
    ]


def _get_sample_completed_tests(store_id: str) -> List[dict]:
    """Return sample completed tests for demo"""
    return [
        {
            "id": "t1",
            "patientName": "Sanjay Gupta",
            "customerPhone": "9876543220",
            "completedAt": datetime.now().replace(hour=9, minute=30).isoformat(),
            "optometrist": "Dr. Meera",
            "rightEye": {"sphere": -2.0, "cylinder": -0.5, "axis": 90},
            "leftEye": {"sphere": -1.75, "cylinder": -0.25, "axis": 85}
        },
        {
            "id": "t2",
            "patientName": "Neha Singh",
            "customerPhone": "9876543221",
            "completedAt": datetime.now().replace(hour=9, minute=0).isoformat(),
            "optometrist": "Dr. Meera",
            "rightEye": {"sphere": 1.0, "cylinder": None, "axis": None},
            "leftEye": {"sphere": 1.25, "cylinder": None, "axis": None}
        }
    ]


# ============================================================================
# QUEUE ENDPOINTS
# ============================================================================

@router.get("/queue")
async def get_queue(
    store_id: str = Query(..., alias="store_id"),
    current_user: dict = Depends(get_current_user)
):
    """Get eye test queue for a store"""
    queue = _queue_storage.get(store_id, _get_sample_queue(store_id))
    return {"queue": queue}


@router.post("/queue")
async def add_to_queue(
    item: QueueItemCreate,
    current_user: dict = Depends(get_current_user)
):
    """Add a patient to the eye test queue"""
    store_id = item.store_id

    # Get current queue
    if store_id not in _queue_storage:
        _queue_storage[store_id] = []

    # Generate token number
    queue = _queue_storage[store_id]
    token_num = len(queue) + 1

    new_item = {
        "id": str(uuid.uuid4()),
        "tokenNumber": f"T{token_num:03d}",
        "patientName": item.patient_name,
        "customerPhone": item.customer_phone,
        "age": item.age,
        "reason": item.reason,
        "customerId": item.customer_id,
        "status": "WAITING",
        "createdAt": datetime.now().isoformat(),
        "waitTime": 0
    }

    queue.append(new_item)
    return new_item


class StatusUpdate(BaseModel):
    status: str


@router.patch("/queue/{queue_id}/status")
async def update_queue_status(
    queue_id: str,
    body: StatusUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update queue item status"""
    # Find and update the queue item
    for store_id, queue in _queue_storage.items():
        for item in queue:
            if item["id"] == queue_id:
                item["status"] = body.status
                return {"message": "Status updated", "status": body.status}

    # Check sample data
    return {"message": "Status updated", "status": body.status}


@router.delete("/queue/{queue_id}")
async def remove_from_queue(
    queue_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Remove a patient from the queue"""
    for store_id, queue in _queue_storage.items():
        for i, item in enumerate(queue):
            if item["id"] == queue_id:
                del queue[i]
                return {"message": "Removed from queue"}

    return {"message": "Removed from queue"}


@router.post("/queue/{queue_id}/start-test")
async def start_test(
    queue_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Start an eye test for a queue item"""
    # Update queue item status
    for store_id, queue in _queue_storage.items():
        for item in queue:
            if item["id"] == queue_id:
                item["status"] = "IN_PROGRESS"

                # Create test record
                test_id = str(uuid.uuid4())
                _test_storage[test_id] = {
                    "id": test_id,
                    "queueId": queue_id,
                    "patientName": item.get("patientName", item.get("patient_name", "")),
                    "customerPhone": item.get("customerPhone", item.get("customer_phone", "")),
                    "startedAt": datetime.now().isoformat(),
                    "status": "IN_PROGRESS"
                }

                return {"testId": test_id, "message": "Test started"}

    # For sample data
    test_id = str(uuid.uuid4())
    return {"testId": test_id, "message": "Test started"}


# ============================================================================
# TEST ENDPOINTS
# ============================================================================

@router.get("/tests")
async def get_tests(
    store_id: str = Query(..., alias="store_id"),
    date: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Get eye tests for a store"""
    if date == "today":
        # Return today's completed tests
        return {"tests": _get_sample_completed_tests(store_id)}

    return {"tests": list(_test_storage.values())}


@router.get("/tests/{test_id}")
async def get_test(
    test_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific eye test"""
    if test_id in _test_storage:
        return _test_storage[test_id]

    raise HTTPException(status_code=404, detail="Test not found")


@router.post("/tests/{test_id}/complete")
async def complete_test(
    test_id: str,
    data: EyeTestData,
    current_user: dict = Depends(get_current_user)
):
    """Complete an eye test with prescription data"""
    if test_id in _test_storage:
        test = _test_storage[test_id]
        test["status"] = "COMPLETED"
        test["completedAt"] = datetime.now().isoformat()
        test["prescription"] = {
            "rightEye": data.right_eye,
            "leftEye": data.left_eye,
            "pd": data.pd,
            "notes": data.notes
        }
        test["optometrist"] = current_user.get("full_name", "Unknown")

        # Update queue status
        for store_id, queue in _queue_storage.items():
            for item in queue:
                if item.get("id") == test.get("queueId"):
                    item["status"] = "COMPLETED"
                    break

        return {"message": "Test completed", "testId": test_id}

    # For demo purposes, just return success
    return {"message": "Test completed", "testId": test_id}
