"""
IMS 2.0 - Expenses Router
=========================
Real database queries for expense and advance management
"""

from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime
import uuid
from .auth import get_current_user
from ..dependencies import get_expense_repository, get_advance_repository

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class ExpenseCreate(BaseModel):
    category: str
    amount: float
    description: str
    expense_date: date
    advance_id: Optional[str] = None


class AdvanceCreate(BaseModel):
    advance_type: str
    amount: float
    purpose: str
    expected_settlement_date: Optional[date] = None


# ============================================================================
# EXPENSE ENDPOINTS
# ============================================================================


@router.get("/")
async def list_expenses(
    employee_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List expenses with optional filters"""
    expense_repo = get_expense_repository()
    active_store = store_id or current_user.get("active_store_id")

    if not expense_repo:
        return {"expenses": [], "total": 0}

    filter_dict = {}
    if active_store:
        filter_dict["store_id"] = active_store
    if employee_id:
        filter_dict["employee_id"] = employee_id
    if status:
        filter_dict["status"] = status

    if from_date and to_date:
        filter_dict["expense_date"] = {
            "$gte": from_date.isoformat(),
            "$lte": to_date.isoformat(),
        }
    elif from_date:
        filter_dict["expense_date"] = {"$gte": from_date.isoformat()}
    elif to_date:
        filter_dict["expense_date"] = {"$lte": to_date.isoformat()}

    expenses = expense_repo.find_many(filter_dict)

    return {"expenses": expenses or [], "total": len(expenses) if expenses else 0}


@router.post("/", status_code=201)
async def create_expense(
    expense: ExpenseCreate, current_user: dict = Depends(get_current_user)
):
    """Create a new expense"""
    expense_repo = get_expense_repository()
    expense_id = str(uuid.uuid4())

    if expense_repo:
        expense_repo.create(
            {
                "expense_id": expense_id,
                "employee_id": current_user.get("user_id"),
                "employee_name": current_user.get("full_name"),
                "store_id": current_user.get("active_store_id"),
                "category": expense.category,
                "amount": expense.amount,
                "description": expense.description,
                "expense_date": expense.expense_date.isoformat(),
                "advance_id": expense.advance_id,
                "status": "DRAFT",
                "created_at": datetime.now().isoformat(),
            }
        )

    return {"expense_id": expense_id, "message": "Expense created"}


@router.post("/{expense_id}/upload-bill")
async def upload_bill(
    expense_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Upload bill/receipt for expense"""
    expense_repo = get_expense_repository()

    if expense_repo:
        existing = expense_repo.find_by_id(expense_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Expense not found")

        # In production, you'd upload to cloud storage (S3, GCS, etc.)
        # For now, just record the filename
        expense_repo.update(
            expense_id,
            {
                "bill_filename": file.filename,
                "bill_uploaded_at": datetime.now().isoformat(),
            },
        )

    return {"message": "Bill uploaded", "filename": file.filename}


@router.post("/{expense_id}/submit")
async def submit_expense(
    expense_id: str, current_user: dict = Depends(get_current_user)
):
    """Submit expense for approval"""
    expense_repo = get_expense_repository()

    if expense_repo:
        existing = expense_repo.find_by_id(expense_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Expense not found")

        if existing.get("status") != "DRAFT":
            raise HTTPException(
                status_code=400, detail="Expense is not in draft status"
            )

        expense_repo.update(
            expense_id,
            {"status": "PENDING", "submitted_at": datetime.now().isoformat()},
        )

    return {"message": "Expense submitted for approval", "expense_id": expense_id}


@router.post("/{expense_id}/approve")
async def approve_expense(
    expense_id: str, current_user: dict = Depends(get_current_user)
):
    """Approve an expense"""
    expense_repo = get_expense_repository()

    if expense_repo:
        existing = expense_repo.find_by_id(expense_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Expense not found")

        if existing.get("status") != "PENDING":
            raise HTTPException(
                status_code=400, detail="Expense is not pending approval"
            )

        expense_repo.update(
            expense_id,
            {
                "status": "APPROVED",
                "approved_by": current_user.get("user_id"),
                "approved_at": datetime.now().isoformat(),
            },
        )

    return {"message": "Expense approved", "expense_id": expense_id}


@router.post("/{expense_id}/reject")
async def reject_expense(
    expense_id: str,
    reason: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Reject an expense"""
    expense_repo = get_expense_repository()

    if expense_repo:
        existing = expense_repo.find_by_id(expense_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Expense not found")

        if existing.get("status") != "PENDING":
            raise HTTPException(
                status_code=400, detail="Expense is not pending approval"
            )

        expense_repo.update(
            expense_id,
            {
                "status": "REJECTED",
                "rejected_by": current_user.get("user_id"),
                "rejected_at": datetime.now().isoformat(),
                "rejection_reason": reason,
            },
        )

    return {"message": "Expense rejected", "expense_id": expense_id}


# ============================================================================
# ADVANCE ENDPOINTS
# ============================================================================


@router.get("/advances")
async def list_advances(
    employee_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List advances with optional filters"""
    advance_repo = get_advance_repository()
    active_store = store_id or current_user.get("active_store_id")

    if not advance_repo:
        return {"advances": [], "total": 0}

    filter_dict = {}
    if active_store:
        filter_dict["store_id"] = active_store
    if employee_id:
        filter_dict["employee_id"] = employee_id
    if status:
        filter_dict["status"] = status

    advances = advance_repo.find_many(filter_dict)

    return {"advances": advances or [], "total": len(advances) if advances else 0}


@router.post("/advances", status_code=201)
async def request_advance(
    advance: AdvanceCreate, current_user: dict = Depends(get_current_user)
):
    """Request a new advance"""
    advance_repo = get_advance_repository()
    advance_id = str(uuid.uuid4())

    if advance_repo:
        advance_repo.create(
            {
                "advance_id": advance_id,
                "employee_id": current_user.get("user_id"),
                "employee_name": current_user.get("full_name"),
                "store_id": current_user.get("active_store_id"),
                "advance_type": advance.advance_type,
                "amount": advance.amount,
                "purpose": advance.purpose,
                "expected_settlement_date": (
                    advance.expected_settlement_date.isoformat()
                    if advance.expected_settlement_date
                    else None
                ),
                "status": "PENDING",
                "created_at": datetime.now().isoformat(),
            }
        )

    return {"advance_id": advance_id, "message": "Advance request submitted"}


@router.post("/advances/{advance_id}/approve")
async def approve_advance(
    advance_id: str, current_user: dict = Depends(get_current_user)
):
    """Approve an advance request"""
    advance_repo = get_advance_repository()

    if advance_repo:
        existing = advance_repo.find_by_id(advance_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Advance not found")

        if existing.get("status") != "PENDING":
            raise HTTPException(
                status_code=400, detail="Advance is not pending approval"
            )

        advance_repo.update(
            advance_id,
            {
                "status": "APPROVED",
                "approved_by": current_user.get("user_id"),
                "approved_at": datetime.now().isoformat(),
            },
        )

    return {"message": "Advance approved", "advance_id": advance_id}


@router.post("/advances/{advance_id}/disburse")
async def disburse_advance(
    advance_id: str,
    reference: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Mark advance as disbursed"""
    advance_repo = get_advance_repository()

    if advance_repo:
        existing = advance_repo.find_by_id(advance_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Advance not found")

        if existing.get("status") != "APPROVED":
            raise HTTPException(
                status_code=400, detail="Advance must be approved first"
            )

        advance_repo.update(
            advance_id,
            {
                "status": "DISBURSED",
                "disbursement_reference": reference,
                "disbursed_by": current_user.get("user_id"),
                "disbursed_at": datetime.now().isoformat(),
            },
        )

    return {
        "message": "Advance disbursed",
        "advance_id": advance_id,
        "reference": reference,
    }


@router.post("/advances/{advance_id}/settle")
async def settle_advance(
    advance_id: str, current_user: dict = Depends(get_current_user)
):
    """Mark advance as settled"""
    advance_repo = get_advance_repository()

    if advance_repo:
        existing = advance_repo.find_by_id(advance_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Advance not found")

        if existing.get("status") != "DISBURSED":
            raise HTTPException(
                status_code=400, detail="Advance must be disbursed first"
            )

        advance_repo.update(
            advance_id, {"status": "SETTLED", "settled_at": datetime.now().isoformat()}
        )

    return {"message": "Advance settled", "advance_id": advance_id}


@router.get("/pending-approval")
async def get_pending_approvals(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get all pending expenses and advances for approval"""
    expense_repo = get_expense_repository()
    advance_repo = get_advance_repository()
    active_store = store_id or current_user.get("active_store_id")

    pending_expenses = []
    pending_advances = []

    if expense_repo:
        filter_dict = {"status": "PENDING"}
        if active_store:
            filter_dict["store_id"] = active_store
        pending_expenses = expense_repo.find_many(filter_dict) or []

    if advance_repo:
        filter_dict = {"status": "PENDING"}
        if active_store:
            filter_dict["store_id"] = active_store
        pending_advances = advance_repo.find_many(filter_dict) or []

    return {
        "expenses": pending_expenses,
        "advances": pending_advances,
        "total_expenses": len(pending_expenses),
        "total_advances": len(pending_advances),
    }
