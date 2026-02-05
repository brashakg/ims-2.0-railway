"""
IMS 2.0 - HR Router
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime
from .auth import get_current_user

router = APIRouter()

class LeaveCreate(BaseModel):
    leave_type: str
    from_date: date
    to_date: date
    reason: str

class AttendanceMarkRequest(BaseModel):
    employee_id: str
    date: date
    status: str
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None

@router.get("/attendance")
async def get_attendance(
    employee_id: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    # Return sample data in camelCase for demo
    return {"records": []}

@router.post("/attendance/check-in")
async def check_in(latitude: Optional[float] = None, longitude: Optional[float] = None, current_user: dict = Depends(get_current_user)):
    return {"message": "Check-in recorded", "checkInTime": datetime.now().isoformat()}

@router.post("/attendance/check-out")
async def check_out(current_user: dict = Depends(get_current_user)):
    return {"message": "Check-out recorded", "checkOutTime": datetime.now().isoformat()}

@router.post("/attendance/mark")
async def mark_attendance(request: AttendanceMarkRequest, current_user: dict = Depends(get_current_user)):
    return {"message": "Attendance marked"}

@router.get("/leaves")
async def list_leaves(employee_id: Optional[str] = Query(None), status: Optional[str] = Query(None), current_user: dict = Depends(get_current_user)):
    return {"leaves": []}

@router.post("/leaves", status_code=201)
async def apply_leave(leave: LeaveCreate, current_user: dict = Depends(get_current_user)):
    return {"leaveId": "new-leave-id", "message": "Leave application submitted"}

@router.post("/leaves/{leave_id}/approve")
async def approve_leave(leave_id: str, current_user: dict = Depends(get_current_user)):
    return {"message": "Leave approved"}

@router.post("/leaves/{leave_id}/reject")
async def reject_leave(leave_id: str, reason: str, current_user: dict = Depends(get_current_user)):
    return {"message": "Leave rejected"}

@router.get("/leaves/balance/{employee_id}")
async def get_leave_balance(employee_id: str, year: int = Query(...), current_user: dict = Depends(get_current_user)):
    return {"employeeId": employee_id, "year": year, "balance": {}}

@router.get("/payroll")
async def list_payroll(year: int = Query(...), month: int = Query(...), current_user: dict = Depends(get_current_user)):
    return {"payroll": []}

@router.post("/payroll/generate")
async def generate_payroll(year: int, month: int, current_user: dict = Depends(get_current_user)):
    return {"message": "Payroll generated"}

@router.post("/payroll/{payroll_id}/approve")
async def approve_payroll(payroll_id: str, current_user: dict = Depends(get_current_user)):
    return {"message": "Payroll approved"}

@router.get("/employee/{employee_id}/salary-slip")
async def get_salary_slip(employee_id: str, year: int, month: int, current_user: dict = Depends(get_current_user)):
    return {"employeeId": employee_id, "year": year, "month": month, "salarySlip": {}}
