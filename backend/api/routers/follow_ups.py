"""
IMS 2.0 - Customer Follow-ups Router
=====================================
Automated customer follow-up management for optical retail:
- Eye test reminders (yearly)
- Frame replacement reminders (2 years)
- Order delivery notifications
- Prescription expiry reminders
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime, date, timedelta
from enum import Enum
import uuid

from .auth import get_current_user
from ..dependencies import get_db as _dep_get_db

router = APIRouter()

def _get_db():
    """Get raw database connection"""
    try:
        from database.connection import get_db
        return get_db().db
    except Exception:
        return _dep_get_db()

# ============================================================================
# SCHEMAS
# ============================================================================

class FollowUpType(str, Enum):
    """Types of customer follow-ups"""
    eye_test_reminder = "eye_test_reminder"
    frame_replacement = "frame_replacement"
    order_delivery = "order_delivery"
    prescription_expiry = "prescription_expiry"
    general = "general"


class FollowUpStatus(str, Enum):
    """Status of follow-up"""
    pending = "pending"
    completed = "completed"
    skipped = "skipped"


class FollowUpOutcome(str, Enum):
    """Outcome of completed follow-up"""
    called_interested = "called_interested"
    called_not_interested = "called_not_interested"
    no_answer = "no_answer"
    rescheduled = "rescheduled"
    completed = "completed"


class CreateFollowUpRequest(BaseModel):
    """Request to create a follow-up"""
    customer_id: str = Field(..., description="Customer ID")
    customer_name: str = Field(..., description="Customer name")
    customer_phone: str = Field(..., description="Customer phone")
    store_id: str = Field(..., description="Store ID")
    type: FollowUpType = Field(..., description="Type of follow-up")
    scheduled_date: str = Field(..., description="Scheduled date (YYYY-MM-DD)")
    notes: str = Field(default="", description="Notes for follow-up")


class CompleteFollowUpRequest(BaseModel):
    """Request to complete a follow-up"""
    outcome: FollowUpOutcome = Field(..., description="Outcome of follow-up")
    notes: str = Field(default="", description="Additional notes")


class FollowUpResponse(BaseModel):
    """Follow-up data response"""
    follow_up_id: str
    customer_id: str
    customer_name: str
    customer_phone: str
    store_id: str
    type: str
    scheduled_date: str
    status: str
    outcome: Optional[str] = None
    notes: str
    created_at: str
    completed_at: Optional[str] = None
    completed_by: Optional[str] = None


class FollowUpSummary(BaseModel):
    """Summary statistics for follow-ups"""
    due_today: int
    this_week: int
    overdue: int
    completed_this_month: int
    pending_total: int


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/", response_model=List[FollowUpResponse])
async def list_follow_ups(
    store_id: str = Query(..., description="Store ID"),
    type_filter: Optional[str] = Query(None, description="Filter by type"),
    status_filter: Optional[str] = Query(None, description="Filter by status"),
    date_from: Optional[str] = Query(None, description="Filter from date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter to date (YYYY-MM-DD)"),
    current_user: dict = Depends(get_current_user)
):
    """
    List pending follow-ups for a store with optional filters.
    Filters:
    - type: eye_test_reminder, frame_replacement, order_delivery, prescription_expiry, general
    - status: pending, completed, skipped
    - date_from/date_to: date range filter
    """
    db = _get_db()
    if not db or not db.is_connected:
        raise HTTPException(status_code=500, detail="Database connection failed")

    collection = db.get_collection("follow_ups")
    
    # Build query filter
    query = {"store_id": store_id}
    
    if type_filter:
        query["type"] = type_filter
    if status_filter:
        query["status"] = status_filter
    
    # Date range filter
    if date_from or date_to:
        date_query = {}
        if date_from:
            date_query["$gte"] = date_from
        if date_to:
            date_query["$lte"] = date_to
        if date_query:
            query["scheduled_date"] = date_query
    
    # Fetch follow-ups, sorted by scheduled_date (due soonest first)
    follow_ups = list(collection.find(query).sort("scheduled_date", 1))
    
    return [FollowUpResponse(**fu) for fu in follow_ups]


@router.post("", response_model=FollowUpResponse)
@router.post("/", response_model=FollowUpResponse)
async def create_follow_up(
    request: CreateFollowUpRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new follow-up for a customer.
    """
    db = _get_db()
    if not db or not db.is_connected:
        raise HTTPException(status_code=500, detail="Database connection failed")

    collection = db.get_collection("follow_ups")
    
    # Generate follow-up ID
    follow_up_id = f"FU-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
    
    follow_up = {
        "follow_up_id": follow_up_id,
        "customer_id": request.customer_id,
        "customer_name": request.customer_name,
        "customer_phone": request.customer_phone,
        "store_id": request.store_id,
        "type": request.type.value,
        "scheduled_date": request.scheduled_date,
        "status": "pending",
        "outcome": None,
        "notes": request.notes,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
        "completed_by": None,
    }
    
    result = collection.insert_one(follow_up)
    follow_up["_id"] = result.inserted_id
    
    return FollowUpResponse(**follow_up)


@router.patch("/{follow_up_id}/complete", response_model=FollowUpResponse)
async def complete_follow_up(
    follow_up_id: str,
    request: CompleteFollowUpRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Mark a follow-up as completed with outcome.
    """
    db = _get_db()
    if not db or not db.is_connected:
        raise HTTPException(status_code=500, detail="Database connection failed")

    collection = db.get_collection("follow_ups")
    
    result = collection.find_one_and_update(
        {"follow_up_id": follow_up_id},
        {
            "$set": {
                "status": "completed",
                "outcome": request.outcome.value,
                "notes": request.notes,
                "completed_at": datetime.now().isoformat(),
                "completed_by": current_user.get("user_id") or current_user.get("id"),
            }
        },
        return_document=True
    )
    
    if not result:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    
    return FollowUpResponse(**result)


@router.get("/due-today", response_model=List[FollowUpResponse])
async def get_due_today(
    store_id: str = Query(..., description="Store ID"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all follow-ups due today for a store.
    """
    db = _get_db()
    if not db or not db.is_connected:
        raise HTTPException(status_code=500, detail="Database connection failed")

    collection = db.get_collection("follow_ups")
    today = date.today().isoformat()
    
    follow_ups = list(collection.find({
        "store_id": store_id,
        "status": "pending",
        "scheduled_date": {"$lte": today}
    }).sort("scheduled_date", 1))
    
    return [FollowUpResponse(**fu) for fu in follow_ups]


@router.post("/auto-generate")
async def auto_generate_follow_ups(
    store_id: str = Query(..., description="Store ID"),
    current_user: dict = Depends(get_current_user)
):
    """
    Auto-generate follow-ups from order and eye test data.
    - Frame replacement: 2 years from order date
    - Eye test reminder: yearly from last test
    - Prescription expiry: 1 year from issue date
    """
    db = _get_db()
    if not db or not db.is_connected:
        raise HTTPException(status_code=500, detail="Database connection failed")

    follow_ups_collection = db.get_collection("follow_ups")
    orders_collection = db.get_collection("orders")
    tests_collection = db.get_collection("eye_tests")
    prescriptions_collection = db.get_collection("prescriptions")
    
    generated_count = 0
    today = date.today()
    
    try:
        # Generate frame replacement reminders (2 years from order date)
        if orders_collection:
            orders = list(orders_collection.find({"store_id": store_id}))
            for order in orders:
                if order.get("created_at"):
                    order_date = datetime.fromisoformat(order["created_at"]).date()
                    reminder_date = order_date + timedelta(days=730)  # 2 years
                    
                    if reminder_date >= today:
                        # Check if reminder already exists
                        existing = follow_ups_collection.find_one({
                            "customer_id": order.get("customer_id"),
                            "type": "frame_replacement",
                            "scheduled_date": reminder_date.isoformat()
                        })
                        
                        if not existing:
                            fu_id = f"FU-{today.strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
                            follow_ups_collection.insert_one({
                                "follow_up_id": fu_id,
                                "customer_id": order.get("customer_id"),
                                "customer_name": order.get("customer_name"),
                                "customer_phone": order.get("customer_phone"),
                                "store_id": store_id,
                                "type": "frame_replacement",
                                "scheduled_date": reminder_date.isoformat(),
                                "status": "pending",
                                "outcome": None,
                                "notes": f"Frame replacement reminder for order {order.get('order_id')}",
                                "created_at": datetime.now().isoformat(),
                                "completed_at": None,
                                "completed_by": None,
                            })
                            generated_count += 1
        
        # Generate eye test reminders (yearly)
        if tests_collection:
            tests = list(tests_collection.find({"store_id": store_id}))
            for test in tests:
                if test.get("created_at"):
                    test_date = datetime.fromisoformat(test["created_at"]).date()
                    reminder_date = test_date + timedelta(days=365)  # 1 year
                    
                    if reminder_date >= today:
                        existing = follow_ups_collection.find_one({
                            "customer_id": test.get("customer_id"),
                            "type": "eye_test_reminder",
                            "scheduled_date": reminder_date.isoformat()
                        })
                        
                        if not existing:
                            fu_id = f"FU-{today.strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
                            follow_ups_collection.insert_one({
                                "follow_up_id": fu_id,
                                "customer_id": test.get("customer_id"),
                                "customer_name": test.get("customer_name"),
                                "customer_phone": test.get("customer_phone"),
                                "store_id": store_id,
                                "type": "eye_test_reminder",
                                "scheduled_date": reminder_date.isoformat(),
                                "status": "pending",
                                "outcome": None,
                                "notes": "Annual eye test reminder",
                                "created_at": datetime.now().isoformat(),
                                "completed_at": None,
                                "completed_by": None,
                            })
                            generated_count += 1
        
        # Generate prescription expiry reminders (1 year)
        if prescriptions_collection:
            prescriptions = list(prescriptions_collection.find({"store_id": store_id}))
            for prescription in prescriptions:
                if prescription.get("issue_date"):
                    issue_date = datetime.fromisoformat(prescription["issue_date"]).date()
                    expiry_date = issue_date + timedelta(days=365)
                    
                    if expiry_date >= today:
                        existing = follow_ups_collection.find_one({
                            "customer_id": prescription.get("customer_id"),
                            "type": "prescription_expiry",
                            "scheduled_date": expiry_date.isoformat()
                        })
                        
                        if not existing:
                            fu_id = f"FU-{today.strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
                            follow_ups_collection.insert_one({
                                "follow_up_id": fu_id,
                                "customer_id": prescription.get("customer_id"),
                                "customer_name": prescription.get("customer_name"),
                                "customer_phone": prescription.get("customer_phone"),
                                "store_id": store_id,
                                "type": "prescription_expiry",
                                "scheduled_date": expiry_date.isoformat(),
                                "status": "pending",
                                "outcome": None,
                                "notes": "Prescription expiry reminder",
                                "created_at": datetime.now().isoformat(),
                                "completed_at": None,
                                "completed_by": None,
                            })
                            generated_count += 1
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).error("Follow-up generation failed: %s", e)
        raise HTTPException(status_code=500, detail="Error generating follow-ups. Please try again.")
    
    return {
        "status": "success",
        "generated_count": generated_count,
        "message": f"{generated_count} follow-ups auto-generated"
    }


@router.get("/summary")
async def get_follow_up_summary(
    store_id: str = Query(..., description="Store ID"),
    current_user: dict = Depends(get_current_user)
) -> FollowUpSummary:
    """
    Get summary statistics of follow-ups for a store.
    """
    db = _get_db()
    if not db or not db.is_connected:
        raise HTTPException(status_code=500, detail="Database connection failed")

    collection = db.get_collection("follow_ups")
    today = date.today().isoformat()
    week_from_today = (date.today() + timedelta(days=7)).isoformat()
    month_from_today = (date.today() + timedelta(days=30)).isoformat()
    
    due_today = collection.count_documents({
        "store_id": store_id,
        "status": "pending",
        "scheduled_date": {"$lte": today}
    })
    
    this_week = collection.count_documents({
        "store_id": store_id,
        "status": "pending",
        "scheduled_date": {"$gte": today, "$lte": week_from_today}
    })
    
    overdue = collection.count_documents({
        "store_id": store_id,
        "status": "pending",
        "scheduled_date": {"$lt": today}
    })
    
    month_ago = (date.today() - timedelta(days=30)).isoformat()
    completed_this_month = collection.count_documents({
        "store_id": store_id,
        "status": "completed",
        "completed_at": {"$gte": month_ago}
    })
    
    pending_total = collection.count_documents({
        "store_id": store_id,
        "status": "pending"
    })
    
    return FollowUpSummary(
        due_today=due_today,
        this_week=this_week,
        overdue=overdue,
        completed_this_month=completed_this_month,
        pending_total=pending_total
    )
