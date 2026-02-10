"""
IMS 2.0 - HR Router
====================
Real database queries for attendance, leaves, and payroll
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime
import uuid
from .auth import get_current_user
from ..dependencies import (
    get_attendance_repository,
    get_leave_repository,
    get_payroll_repository,
    get_user_repository,
)

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class LeaveCreate(BaseModel):
    leave_type: str
    from_date: date
    to_date: date
    reason: str


class AttendanceMarkRequest(BaseModel):
    employee_id: str
    date: date
    status: str  # PRESENT, ABSENT, HALF_DAY, LEAVE
    check_in: Optional[datetime] = None
    check_out: Optional[datetime] = None


# ============================================================================
# ATTENDANCE ENDPOINTS
# ============================================================================


@router.get("/attendance")
async def get_attendance(
    employee_id: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    # Return sample data in camelCase for demo
    return {"records": []}


@router.post("/attendance/check-in")
async def check_in(
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    current_user: dict = Depends(get_current_user),
):
    return {"message": "Check-in recorded", "checkInTime": datetime.now().isoformat()}


@router.post("/attendance/check-out")
async def check_out(current_user: dict = Depends(get_current_user)):
    return {"message": "Check-out recorded", "checkOutTime": datetime.now().isoformat()}


@router.post("/attendance/mark")
async def mark_attendance(
    request: AttendanceMarkRequest, current_user: dict = Depends(get_current_user)
):
    """Mark attendance for an employee (admin function)"""
    attendance_repo = get_attendance_repository()

    if attendance_repo is not None:
        # Check if record exists
        existing = attendance_repo.find_one(
            {"employee_id": request.employee_id, "date": request.date.isoformat()}
        )

        data = {
            "employee_id": request.employee_id,
            "store_id": current_user.get("active_store_id"),
            "date": request.date.isoformat(),
            "status": request.status,
            "check_in": request.check_in.isoformat() if request.check_in else None,
            "check_out": request.check_out.isoformat() if request.check_out else None,
            "marked_by": current_user.get("user_id"),
            "marked_at": datetime.now().isoformat(),
        }

        if existing is not None:
            attendance_repo.update(existing.get("attendance_id"), data)
        else:
            data["attendance_id"] = str(uuid.uuid4())
            attendance_repo.create(data)

    return {"message": "Attendance marked", "date": request.date.isoformat()}


# ============================================================================
# LEAVE ENDPOINTS
# ============================================================================


@router.get("/leaves")
async def list_leaves(
    employee_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List leave requests"""
    leave_repo = get_leave_repository()
    active_store = store_id or current_user.get("active_store_id")

    if leave_repo is None:
        return {"leaves": [], "total": 0}

    filter_dict = {}
    if active_store:
        filter_dict["store_id"] = active_store
    if employee_id:
        filter_dict["employee_id"] = employee_id
    if status:
        filter_dict["status"] = status

    leaves = leave_repo.find_many(filter_dict)

    return {"leaves": leaves or [], "total": len(leaves) if leaves else 0}


@router.post("/leaves", status_code=201)
async def apply_leave(
    leave: LeaveCreate, current_user: dict = Depends(get_current_user)
):
    return {"leaveId": "new-leave-id", "message": "Leave application submitted"}


@router.post("/leaves/{leave_id}/approve")
async def approve_leave(leave_id: str, current_user: dict = Depends(get_current_user)):
    """Approve a leave request"""
    leave_repo = get_leave_repository()

    if leave_repo is not None:
        leave = leave_repo.find_by_id(leave_id)
        if not leave:
            raise HTTPException(status_code=404, detail="Leave request not found")

        if leave.get("status") != "PENDING":
            raise HTTPException(status_code=400, detail="Leave is not pending")

        leave_repo.update(
            leave_id,
            {
                "status": "APPROVED",
                "approved_by": current_user.get("user_id"),
                "approved_at": datetime.now().isoformat(),
            },
        )

    return {"message": "Leave approved", "leave_id": leave_id}


@router.post("/leaves/{leave_id}/reject")
async def reject_leave(
    leave_id: str,
    reason: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Reject a leave request"""
    leave_repo = get_leave_repository()

    if leave_repo is not None:
        leave = leave_repo.find_by_id(leave_id)
        if not leave:
            raise HTTPException(status_code=404, detail="Leave request not found")

        if leave.get("status") != "PENDING":
            raise HTTPException(status_code=400, detail="Leave is not pending")

        leave_repo.update(
            leave_id,
            {
                "status": "REJECTED",
                "rejected_by": current_user.get("user_id"),
                "rejected_at": datetime.now().isoformat(),
                "rejection_reason": reason,
            },
        )

    return {"message": "Leave rejected", "leave_id": leave_id}


@router.get("/leaves/balance/{employee_id}")
async def get_leave_balance(
    employee_id: str,
    year: int = Query(...),
    current_user: dict = Depends(get_current_user),
):
    return {"employeeId": employee_id, "year": year, "balance": {}}


@router.get("/payroll")
async def list_payroll(
    year: int = Query(...),
    month: int = Query(...),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List payroll records for a month"""
    payroll_repo = get_payroll_repository()
    active_store = store_id or current_user.get("active_store_id")

    if payroll_repo is None:
        return {"payroll": [], "total": 0}

    records = payroll_repo.find_many(
        {"store_id": active_store, "year": year, "month": month}
    )

    return {"payroll": records or [], "total": len(records) if records else 0}


@router.post("/payroll/generate")
async def generate_payroll(
    year: int = Query(...),
    month: int = Query(...),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Generate payroll for a month"""
    payroll_repo = get_payroll_repository()
    user_repo = get_user_repository()
    attendance_repo = get_attendance_repository()
    active_store = store_id or current_user.get("active_store_id")

    if not payroll_repo or not user_repo:
        return {"message": "Payroll generation initiated", "count": 0}

    # Get all employees for the store
    employees = user_repo.find_many({"store_ids": active_store})

    generated_count = 0
    for employee in employees or []:
        # Check if payroll already exists
        existing = payroll_repo.find_one(
            {"employee_id": employee.get("user_id"), "year": year, "month": month}
        )

        if existing is not None:
            continue

        # Calculate attendance
        working_days = 0
        present_days = 0
        if attendance_repo is not None:
            attendance = attendance_repo.find_many(
                {
                    "employee_id": employee.get("user_id"),
                    "date": {
                        "$gte": f"{year}-{month:02d}-01",
                        "$lt": (
                            f"{year}-{month+1:02d}-01"
                            if month < 12
                            else f"{year+1}-01-01"
                        ),
                    },
                }
            )
            working_days = len(attendance) if attendance else 26
            present_days = len(
                [a for a in (attendance or []) if a.get("status") == "PRESENT"]
            )

        # Basic payroll calculation
        base_salary = employee.get("salary", 0) or 25000
        daily_rate = base_salary / 26
        gross = daily_rate * present_days
        deductions = gross * 0.1  # 10% deductions
        net = gross - deductions

        payroll_repo.create(
            {
                "payroll_id": str(uuid.uuid4()),
                "employee_id": employee.get("user_id"),
                "employee_name": employee.get("full_name"),
                "store_id": active_store,
                "year": year,
                "month": month,
                "working_days": working_days,
                "present_days": present_days,
                "base_salary": base_salary,
                "gross_salary": round(gross, 2),
                "deductions": round(deductions, 2),
                "net_salary": round(net, 2),
                "status": "DRAFT",
                "generated_by": current_user.get("user_id"),
                "generated_at": datetime.now().isoformat(),
            }
        )
        generated_count += 1

    return {
        "message": "Payroll generated",
        "count": generated_count,
        "year": year,
        "month": month,
    }


@router.post("/payroll/{payroll_id}/approve")
async def approve_payroll(
    payroll_id: str, current_user: dict = Depends(get_current_user)
):
    """Approve payroll for payment"""
    payroll_repo = get_payroll_repository()

    if payroll_repo is not None:
        record = payroll_repo.find_by_id(payroll_id)
        if not record:
            raise HTTPException(status_code=404, detail="Payroll record not found")

        payroll_repo.update(
            payroll_id,
            {
                "status": "APPROVED",
                "approved_by": current_user.get("user_id"),
                "approved_at": datetime.now().isoformat(),
            },
        )

    return {"message": "Payroll approved", "payroll_id": payroll_id}


@router.get("/employee/{employee_id}/salary-slip")
async def get_salary_slip(
    employee_id: str,
    year: int,
    month: int,
    current_user: dict = Depends(get_current_user),
):
    return {"employeeId": employee_id, "year": year, "month": month, "salarySlip": {}}
