"""
IMS 2.0 - Payroll & Salary Router
==================================
Salary management, advances, payslips, and incentive integration
Indian salary structure with PF, ESI, PT, TDS deductions
"""

from fastapi import APIRouter, Depends, Query, HTTPException, Body
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date, timedelta
from calendar import monthrange
import uuid
import math

from .auth import get_current_user

# Import database connection
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from database.connection import get_db
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class SalaryConfig(BaseModel):
    """Employee salary structure"""
    employee_id: str
    basic_salary: float = Field(..., gt=0, description="Basic salary amount")
    hra_percentage: float = Field(default=40.0, ge=0, le=100, description="HRA as % of basic")
    conveyance_allowance: float = Field(default=1600.0, description="Monthly conveyance")
    medical_allowance: float = Field(default=1250.0, description="Monthly medical")
    special_allowance: float = Field(default=0.0, description="Special allowance")
    pf_employee_percentage: float = Field(default=12.0, description="Employee PF %")
    pf_employer_percentage: float = Field(default=12.0, description="Employer PF %")
    professional_tax: float = Field(default=200.0, description="Professional tax")
    esi_applicable: bool = Field(default=True, description="Is ESI applicable")
    esi_percentage: float = Field(default=0.75, description="ESI percentage")
    bank_account: Optional[str] = None
    pan_number: Optional[str] = None
    aadhar_number: Optional[str] = None


class SalaryAdvance(BaseModel):
    """Salary advance request"""
    employee_id: str
    amount: float = Field(..., gt=0)
    date_requested: date = Field(default_factory=date.today)
    reason: Optional[str] = None


class AdvanceSettlement(BaseModel):
    """Settle advance from salary"""
    advance_id: str
    settlement_month: int = Field(..., ge=1, le=12)
    settlement_year: int


class MonthSalaryCalculation(BaseModel):
    """Calculate salary for a month"""
    employee_id: str
    month: int = Field(..., ge=1, le=12)
    year: int
    working_days: int = Field(default=26, ge=1, le=31)
    leave_without_pay_days: int = Field(default=0, ge=0, le=31)
    advance_deduction: float = Field(default=0.0, ge=0)


class PayslipRequest(BaseModel):
    """Request payslip generation"""
    employee_id: str
    month: int = Field(..., ge=1, le=12)
    year: int


# ============================================================================
# RESPONSE SCHEMAS
# ============================================================================


class SalaryBreakdown(BaseModel):
    """Detailed salary breakdown"""
    basic: float
    hra: float
    conveyance: float
    medical: float
    special_allowance: float
    gross_salary: float
    pf_employee: float
    pf_employer: float
    professional_tax: float
    esi: float
    tds: float
    lwp_deduction: float
    advance_deduction: float
    net_pay: float


class SalaryRecord(BaseModel):
    """Monthly salary record"""
    salary_record_id: str
    employee_id: str
    employee_name: str
    month: int
    year: int
    breakdown: SalaryBreakdown
    status: str  # draft, calculated, approved, paid


class PayslipData(BaseModel):
    """Payslip data"""
    payslip_id: str
    employee_id: str
    employee_name: str
    employee_number: str
    designation: str
    department: str
    month: int
    year: int
    breakdown: SalaryBreakdown
    bank_account: Optional[str]
    generated_at: str


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _get_db():
    """Get database connection"""
    if not DATABASE_AVAILABLE:
        return None
    return get_db()


def _get_employee_details(db, employee_id: str) -> dict:
    """Get employee details from users collection"""
    if not db:
        return {}
    try:
        users_coll = db.get_collection("users")
        user = users_coll.find_one({"user_id": employee_id})
        return user or {}
    except:
        return {}


def _get_salary_config(db, employee_id: str) -> Optional[dict]:
    """Get salary configuration for employee"""
    if not db:
        return None
    try:
        salary_config_coll = db.get_collection("salary_config")
        config = salary_config_coll.find_one({"employee_id": employee_id})
        return config
    except:
        return None


def _calculate_tds(gross_salary: float, month: int, year: int) -> float:
    """
    Calculate TDS as per Indian income tax slabs.
    Simplified calculation for monthly payroll.
    Full-year salary threshold: ₹250,000 (2.5L)
    
    This is a simplified monthly calculation.
    """
    annual_salary = gross_salary * 12
    
    # Simplified TDS slab (varies by regime, FY, etc.)
    if annual_salary <= 250000:
        return 0
    elif annual_salary <= 500000:
        taxable = annual_salary - 250000
        tax = taxable * 0.05
    elif annual_salary <= 1000000:
        tax = 250000 * 0.05 + (annual_salary - 500000) * 0.20
    else:
        tax = 250000 * 0.05 + 500000 * 0.20 + (annual_salary - 1000000) * 0.30
    
    # Monthly TDS (rough approximation)
    return max(0, tax / 12)


def _calculate_salary(
    salary_config: dict,
    working_days: int = 26,
    leave_without_pay_days: int = 0,
    advance_deduction: float = 0.0
) -> SalaryBreakdown:
    """
    Calculate complete salary with all components.
    
    Indian salary structure:
    Earnings: Basic + HRA + Conveyance + Medical + Special Allowance
    Deductions: PF (Employee) + Professional Tax + ESI + TDS + LWP + Advances
    """
    
    basic = salary_config.get("basic_salary", 0)
    
    # Earnings
    hra_pct = salary_config.get("hra_percentage", 40)
    hra = (basic * hra_pct) / 100
    
    conveyance = salary_config.get("conveyance_allowance", 1600)
    medical = salary_config.get("medical_allowance", 1250)
    special_allowance = salary_config.get("special_allowance", 0)
    
    gross_salary = basic + hra + conveyance + medical + special_allowance
    
    # Deductions
    pf_employee_pct = salary_config.get("pf_employee_percentage", 12)
    pf_employee = (basic * pf_employee_pct) / 100
    
    pf_employer_pct = salary_config.get("pf_employer_percentage", 12)
    pf_employer = (basic * pf_employer_pct) / 100
    
    professional_tax = salary_config.get("professional_tax", 200)
    
    esi_applicable = salary_config.get("esi_applicable", True)
    esi_pct = salary_config.get("esi_percentage", 0.75) if esi_applicable else 0
    esi = (gross_salary * esi_pct) / 100 if esi_applicable else 0
    
    # TDS calculation
    tds = _calculate_tds(gross_salary, 1, 2026)  # Simplified
    
    # LWP Deduction: (Basic + DA) / 30 * days_absent
    # In Indian salaries, DA is typically part of allowances or fixed
    # Simplified: LWP = (Basic / 26) * leave_without_pay_days
    lwp_deduction = (basic / 26) * leave_without_pay_days if leave_without_pay_days > 0 else 0
    
    # Total deductions (including employer PF - shown for info)
    total_deductions = pf_employee + professional_tax + esi + tds + lwp_deduction + advance_deduction
    
    net_pay = gross_salary - total_deductions
    
    return SalaryBreakdown(
        basic=round(basic, 2),
        hra=round(hra, 2),
        conveyance=round(conveyance, 2),
        medical=round(medical, 2),
        special_allowance=round(special_allowance, 2),
        gross_salary=round(gross_salary, 2),
        pf_employee=round(pf_employee, 2),
        pf_employer=round(pf_employer, 2),
        professional_tax=round(professional_tax, 2),
        esi=round(esi, 2),
        tds=round(tds, 2),
        lwp_deduction=round(lwp_deduction, 2),
        advance_deduction=round(advance_deduction, 2),
        net_pay=round(net_pay, 2)
    )


# ============================================================================
# SALARY CONFIGURATION ENDPOINTS
# ============================================================================


@router.post("/config", status_code=201)
async def create_salary_config(
    config: SalaryConfig,
    current_user: dict = Depends(get_current_user)
):
    """Create salary configuration for an employee"""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    # Check authorization - only admins can create salary configs
    if "ADMIN" not in current_user.get("roles", []):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    try:
        salary_config_coll = db.get_collection("salary_config")
        
        # Check if config already exists
        existing = salary_config_coll.find_one({"employee_id": config.employee_id})
        if existing:
            raise HTTPException(status_code=409, detail="Salary config already exists for this employee")
        
        config_doc = {
            "config_id": str(uuid.uuid4()),
            "employee_id": config.employee_id,
            "basic_salary": config.basic_salary,
            "hra_percentage": config.hra_percentage,
            "conveyance_allowance": config.conveyance_allowance,
            "medical_allowance": config.medical_allowance,
            "special_allowance": config.special_allowance,
            "pf_employee_percentage": config.pf_employee_percentage,
            "pf_employer_percentage": config.pf_employer_percentage,
            "professional_tax": config.professional_tax,
            "esi_applicable": config.esi_applicable,
            "esi_percentage": config.esi_percentage,
            "bank_account": config.bank_account,
            "pan_number": config.pan_number,
            "aadhar_number": config.aadhar_number,
            "created_at": datetime.now().isoformat(),
            "created_by": current_user.get("user_id"),
        }
        
        salary_config_coll.insert_one(config_doc)
        
        return {"status": "success", "config_id": config_doc["config_id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/{employee_id}")
async def get_salary_config(
    employee_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get salary configuration for an employee"""
    db = _get_db()
    if not db:
        return {"config": None}
    
    try:
        config = _get_salary_config(db, employee_id)
        return {"config": config or {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# SALARY SHEET ENDPOINTS
# ============================================================================


@router.get("/salary-sheet")
async def get_salary_sheet(
    month: int,
    year: int,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Get monthly salary sheet for all employees in a store"""
    db = _get_db()
    if not db:
        return {"salaries": [], "total": 0, "store_id": store_id}
    
    active_store = store_id or current_user.get("active_store_id")
    
    try:
        salary_records_coll = db.get_collection("salary_records")
        
        # Query salary records for the month
        records = salary_records_coll.find({
            "month": month,
            "year": year,
            "store_id": active_store
        })
        
        salary_data = []
        for record in records:
            salary_data.append({
                "salary_record_id": record.get("salary_record_id"),
                "employee_id": record.get("employee_id"),
                "employee_name": record.get("employee_name"),
                "basic": record.get("breakdown", {}).get("basic", 0),
                "hra": record.get("breakdown", {}).get("hra", 0),
                "allowances": record.get("breakdown", {}).get("conveyance", 0) + record.get("breakdown", {}).get("medical", 0),
                "gross_salary": record.get("breakdown", {}).get("gross_salary", 0),
                "pf_employee": record.get("breakdown", {}).get("pf_employee", 0),
                "esi": record.get("breakdown", {}).get("esi", 0),
                "professional_tax": record.get("breakdown", {}).get("professional_tax", 0),
                "tds": record.get("breakdown", {}).get("tds", 0),
                "lwp_deduction": record.get("breakdown", {}).get("lwp_deduction", 0),
                "advance_deduction": record.get("breakdown", {}).get("advance_deduction", 0),
                "net_pay": record.get("breakdown", {}).get("net_pay", 0),
                "status": record.get("status", "draft"),
            })
        
        return {
            "month": month,
            "year": year,
            "store_id": active_store,
            "salaries": salary_data,
            "total": len(salary_data)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/salary/{employee_id}")
async def get_employee_salary(
    employee_id: str,
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Get individual employee salary breakdown for a specific month or latest"""
    db = _get_db()
    if not db:
        return {"salary": None}
    
    try:
        salary_records_coll = db.get_collection("salary_records")
        
        if month and year:
            # Get specific month
            record = salary_records_coll.find_one({
                "employee_id": employee_id,
                "month": month,
                "year": year
            })
        else:
            # Get latest
            records = list(salary_records_coll.find(
                {"employee_id": employee_id}
            ).sort("created_at", -1).limit(1))
            record = records[0] if records else None
        
        return {
            "salary": record or {}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# SALARY CALCULATION ENDPOINT
# ============================================================================


@router.post("/salary/calculate", status_code=201)
async def calculate_salary(
    calc_request: MonthSalaryCalculation,
    current_user: dict = Depends(get_current_user)
):
    """Calculate salary for a month with all deductions"""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
        # Get salary config
        salary_config = _get_salary_config(db, calc_request.employee_id)
        if not salary_config:
            raise HTTPException(status_code=404, detail="Salary configuration not found")
        
        # Get employee details
        employee = _get_employee_details(db, calc_request.employee_id)
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        # Calculate salary breakdown
        breakdown = _calculate_salary(
            salary_config,
            working_days=calc_request.working_days,
            leave_without_pay_days=calc_request.leave_without_pay_days,
            advance_deduction=calc_request.advance_deduction
        )
        
        # Create salary record
        salary_records_coll = db.get_collection("salary_records")
        
        salary_record = {
            "salary_record_id": str(uuid.uuid4()),
            "employee_id": calc_request.employee_id,
            "employee_name": employee.get("full_name", ""),
            "store_id": current_user.get("active_store_id"),
            "month": calc_request.month,
            "year": calc_request.year,
            "breakdown": breakdown.dict(),
            "working_days": calc_request.working_days,
            "leave_without_pay_days": calc_request.leave_without_pay_days,
            "status": "calculated",
            "created_at": datetime.now().isoformat(),
            "created_by": current_user.get("user_id"),
        }
        
        # Check if already exists
        existing = salary_records_coll.find_one({
            "employee_id": calc_request.employee_id,
            "month": calc_request.month,
            "year": calc_request.year
        })
        
        if existing:
            salary_records_coll.update_one(
                {"salary_record_id": existing["salary_record_id"]},
                {"$set": salary_record}
            )
            return {"status": "updated", "salary_record_id": existing["salary_record_id"]}
        else:
            salary_records_coll.insert_one(salary_record)
            return {"status": "created", "salary_record_id": salary_record["salary_record_id"]}
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# SALARY ADVANCE ENDPOINTS
# ============================================================================


@router.post("/advances", status_code=201)
async def record_salary_advance(
    advance: SalaryAdvance,
    current_user: dict = Depends(get_current_user)
):
    """Record a salary advance"""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
        salary_advances_coll = db.get_collection("salary_advances")
        
        employee = _get_employee_details(db, advance.employee_id)
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        advance_doc = {
            "advance_id": str(uuid.uuid4()),
            "employee_id": advance.employee_id,
            "employee_name": employee.get("full_name", ""),
            "store_id": current_user.get("active_store_id"),
            "amount": advance.amount,
            "date_requested": advance.date_requested.isoformat(),
            "reason": advance.reason,
            "status": "pending",  # pending, approved, settled, deducted
            "settlement_salary_record_id": None,
            "created_at": datetime.now().isoformat(),
            "created_by": current_user.get("user_id"),
        }
        
        salary_advances_coll.insert_one(advance_doc)
        
        return {
            "status": "success",
            "advance_id": advance_doc["advance_id"],
            "message": "Salary advance recorded"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/advances/{employee_id}")
async def get_salary_advances(
    employee_id: str,
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Get salary advance history for an employee"""
    db = _get_db()
    if not db:
        return {"advances": [], "total": 0}
    
    try:
        salary_advances_coll = db.get_collection("salary_advances")
        
        query = {"employee_id": employee_id}
        if status:
            query["status"] = status
        
        advances = list(salary_advances_coll.find(query))
        
        return {
            "employee_id": employee_id,
            "advances": advances or [],
            "total": len(advances) if advances else 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/advances/{advance_id}/settle", status_code=200)
async def settle_salary_advance(
    advance_id: str,
    settlement: AdvanceSettlement = Body(...),
    current_user: dict = Depends(get_current_user)
):
    """Settle advance against salary"""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
        salary_advances_coll = db.get_collection("salary_advances")
        
        # Get advance
        advance = salary_advances_coll.find_one({"advance_id": advance_id})
        if not advance:
            raise HTTPException(status_code=404, detail="Advance not found")
        
        # Update advance status
        salary_advances_coll.update_one(
            {"advance_id": advance_id},
            {
                "$set": {
                    "status": "settled",
                    "settlement_month": settlement.settlement_month,
                    "settlement_year": settlement.settlement_year,
                    "settled_at": datetime.now().isoformat(),
                    "settled_by": current_user.get("user_id"),
                }
            }
        )
        
        return {
            "status": "success",
            "advance_id": advance_id,
            "message": "Advance settled and will be deducted from salary"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# PAYSLIP ENDPOINTS
# ============================================================================


@router.get("/payslip/{employee_id}/{month}/{year}")
async def get_payslip(
    employee_id: str,
    month: int,
    year: int,
    current_user: dict = Depends(get_current_user)
):
    """Generate or retrieve payslip for an employee"""
    db = _get_db()
    if not db:
        return {"payslip": None}
    
    try:
        salary_records_coll = db.get_collection("salary_records")
        payslips_coll = db.get_collection("payslips")
        
        # Get salary record
        salary_record = salary_records_coll.find_one({
            "employee_id": employee_id,
            "month": month,
            "year": year
        })
        
        if not salary_record:
            raise HTTPException(status_code=404, detail="Salary record not found for this month")
        
        # Check if payslip already exists
        payslip = payslips_coll.find_one({
            "employee_id": employee_id,
            "month": month,
            "year": year
        })
        
        employee = _get_employee_details(db, employee_id)
        
        if not payslip:
            # Create payslip
            payslip = {
                "payslip_id": str(uuid.uuid4()),
                "employee_id": employee_id,
                "employee_name": employee.get("full_name", ""),
                "employee_number": employee.get("employee_number", ""),
                "designation": employee.get("designation", ""),
                "department": employee.get("department", ""),
                "month": month,
                "year": year,
                "breakdown": salary_record.get("breakdown", {}),
                "bank_account": _get_salary_config(db, employee_id).get("bank_account") if _get_salary_config(db, employee_id) else None,
                "generated_at": datetime.now().isoformat(),
            }
            payslips_coll.insert_one(payslip)
        
        return {"payslip": payslip}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/payslip/{employee_id}")
async def get_latest_payslip(
    employee_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get latest payslip for an employee"""
    db = _get_db()
    if not db:
        return {"payslip": None}
    
    try:
        payslips_coll = db.get_collection("payslips")
        
        payslips = list(payslips_coll.find(
            {"employee_id": employee_id}
        ).sort("generated_at", -1).limit(1))
        
        return {"payslip": payslips[0] if payslips else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# INCENTIVE INTEGRATION
# ============================================================================


@router.get("/incentive-summary/{employee_id}/{month}/{year}")
async def get_incentive_summary(
    employee_id: str,
    month: int,
    year: int,
    current_user: dict = Depends(get_current_user)
):
    """Get incentive earned for a month (integrated from incentives module)"""
    db = _get_db()
    if not db:
        return {"incentive": None}
    
    try:
        # Try to get from incentives collection if available
        try:
            incentives_coll = db.get_collection("incentives")
            incentive = incentives_coll.find_one({
                "staff_id": employee_id,
                "month": month,
                "year": year
            })
            return {"incentive": incentive}
        except:
            return {"incentive": None, "message": "Incentive data not found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# ROOT ENDPOINT
# ============================================================================


@router.get("/")
async def payroll_root():
    """Root endpoint for payroll module"""
    return {
        "module": "payroll",
        "status": "active",
        "endpoints": {
            "salary_sheet": "GET /payroll/salary-sheet",
            "employee_salary": "GET /payroll/salary/{employee_id}",
            "calculate_salary": "POST /payroll/salary/calculate",
            "salary_advances": "GET/POST /payroll/advances",
            "payslip": "GET /payroll/payslip/{employee_id}/{month}/{year}",
            "incentive_summary": "GET /payroll/incentive-summary/{employee_id}/{month}/{year}"
        }
    }
