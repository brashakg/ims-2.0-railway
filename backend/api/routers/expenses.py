"""
IMS 2.0 - Expenses Router
"""
from fastapi import APIRouter, Depends, Query, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
from .auth import get_current_user

router = APIRouter()

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

@router.get("/")
async def list_expenses(
    employee_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    return {"expenses": []}

@router.post("/", status_code=201)
async def create_expense(expense: ExpenseCreate, current_user: dict = Depends(get_current_user)):
    return {"expense_id": "new-expense-id"}

@router.post("/{expense_id}/upload-bill")
async def upload_bill(expense_id: str, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    return {"message": "Bill uploaded"}

@router.post("/{expense_id}/submit")
async def submit_expense(expense_id: str, current_user: dict = Depends(get_current_user)):
    return {"message": "Expense submitted for approval"}

@router.post("/{expense_id}/approve")
async def approve_expense(expense_id: str, current_user: dict = Depends(get_current_user)):
    return {"message": "Expense approved"}

@router.post("/{expense_id}/reject")
async def reject_expense(expense_id: str, reason: str, current_user: dict = Depends(get_current_user)):
    return {"message": "Expense rejected"}

@router.get("/advances")
async def list_advances(employee_id: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)):
    return {"advances": []}

@router.post("/advances", status_code=201)
async def request_advance(advance: AdvanceCreate, current_user: dict = Depends(get_current_user)):
    return {"advance_id": "new-advance-id"}

@router.post("/advances/{advance_id}/approve")
async def approve_advance(advance_id: str, current_user: dict = Depends(get_current_user)):
    return {"message": "Advance approved"}

@router.post("/advances/{advance_id}/disburse")
async def disburse_advance(advance_id: str, reference: str, current_user: dict = Depends(get_current_user)):
    return {"message": "Advance disbursed"}

@router.get("/pending-approval")
async def get_pending_approvals(current_user: dict = Depends(get_current_user)):
    return {"expenses": [], "advances": []}
