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
import logging

logger = logging.getLogger(__name__)

from .auth import get_current_user, require_roles

# Import database connection
import sys
import os

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

try:
    from database.connection import get_db

    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class OtherAllowance(BaseModel):
    """A named extra earning line in the CTC."""

    name: str
    amount: float = 0.0


class SalaryConfig(BaseModel):
    """Employee Structured-CTC salary configuration. All amounts are MONTHLY.

    Earnings = basic + hra + conveyance + medical + special_allowance + sum(other).
    Statutory deductions (PF/ESI/PT/TDS) are computed by the payroll engine
    (Phase 2); this config only stores the inputs/flags + identifiers.
    """

    employee_id: str
    entity_id: Optional[str] = Field(default=None, description="Employing legal entity")
    store_id: Optional[str] = Field(default=None, description="Primary work store (PT state)")
    designation: Optional[str] = None
    department: Optional[str] = None
    date_of_joining: Optional[str] = None
    # Earnings (monthly)
    basic: float = Field(..., gt=0, description="Monthly Basic")
    hra: float = Field(default=0.0, ge=0, description="Monthly HRA (absolute)")
    conveyance: float = Field(default=0.0, ge=0, description="Monthly conveyance")
    medical: float = Field(default=0.0, ge=0, description="Monthly medical")
    special_allowance: float = Field(default=0.0, ge=0)
    other_allowances: List[OtherAllowance] = Field(default_factory=list)
    # Statutory toggles + params
    pf_applicable: bool = Field(default=True)
    pf_wage_ceiling_cap: bool = Field(
        default=True, description="PF on min(basic, 15000) when True"
    )
    esi_applicable: Optional[bool] = Field(
        default=None, description="None -> auto-eligible when gross <= 21000"
    )
    pt_applicable: bool = Field(default=True)
    tds_monthly: float = Field(default=0.0, ge=0, description="Manual monthly TDS")
    # Statutory identifiers
    uan: Optional[str] = Field(default=None, description="PF Universal Account Number")
    esi_ip_number: Optional[str] = None
    pan: Optional[str] = None
    # Bank (for salary register / transfer)
    bank_account_no: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_name: Optional[str] = None


class SalaryConfigUpdate(BaseModel):
    """Partial update for an existing salary config (all fields optional)."""

    entity_id: Optional[str] = None
    store_id: Optional[str] = None
    designation: Optional[str] = None
    department: Optional[str] = None
    date_of_joining: Optional[str] = None
    basic: Optional[float] = Field(default=None, gt=0)
    hra: Optional[float] = Field(default=None, ge=0)
    conveyance: Optional[float] = Field(default=None, ge=0)
    medical: Optional[float] = Field(default=None, ge=0)
    special_allowance: Optional[float] = Field(default=None, ge=0)
    other_allowances: Optional[List[OtherAllowance]] = None
    pf_applicable: Optional[bool] = None
    pf_wage_ceiling_cap: Optional[bool] = None
    esi_applicable: Optional[bool] = None
    pt_applicable: Optional[bool] = None
    tds_monthly: Optional[float] = Field(default=None, ge=0)
    uan: Optional[str] = None
    esi_ip_number: Optional[str] = None
    pan: Optional[str] = None
    bank_account_no: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_name: Optional[str] = None
    is_active: Optional[bool] = None


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
    except Exception as e:
        logger.error(f"Error in _get_employee_details: {str(e)}", exc_info=True)
        return {}


def _get_salary_config(db, employee_id: str) -> Optional[dict]:
    """Get salary configuration for employee"""
    if not db:
        return None
    try:
        salary_config_coll = db.get_collection("salary_config")
        config = salary_config_coll.find_one({"employee_id": employee_id})
        return config
    except Exception as e:
        logger.error(f"Error in _get_salary_config: {str(e)}", exc_info=True)
        return None


def _strip_id(doc: Optional[dict]) -> Optional[dict]:
    """Drop Mongo's _id so the doc is JSON-serializable."""
    if doc and "_id" in doc:
        return {k: v for k, v in doc.items() if k != "_id"}
    return doc


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
    advance_deduction: float = 0.0,
) -> SalaryBreakdown:
    """
    Calculate complete salary with all components.

    Indian salary structure:
    Earnings: Basic + HRA + Conveyance + Medical + Special Allowance
    Deductions: PF (Employee) + Professional Tax + ESI + TDS + LWP + Advances
    """

    # Bridge: read Structured-CTC (v2) fields, falling back to legacy names.
    # (Phase 2 replaces this calculator with the full statutory engine.)
    basic = salary_config.get("basic") or salary_config.get("basic_salary", 0)

    # Earnings
    hra = salary_config.get("hra")
    if hra is None:
        hra = (basic * salary_config.get("hra_percentage", 40)) / 100

    conveyance = salary_config.get("conveyance")
    if conveyance is None:
        conveyance = salary_config.get("conveyance_allowance", 0)
    medical = salary_config.get("medical")
    if medical is None:
        medical = salary_config.get("medical_allowance", 0)
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
    lwp_deduction = (
        (basic / 26) * leave_without_pay_days if leave_without_pay_days > 0 else 0
    )

    # Total deductions (including employer PF - shown for info)
    total_deductions = (
        pf_employee + professional_tax + esi + tds + lwp_deduction + advance_deduction
    )

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
        net_pay=round(net_pay, 2),
    )


# ============================================================================
# SALARY CONFIGURATION ENDPOINTS
# ============================================================================


@router.post("/config", status_code=201)
async def create_salary_config(
    config: SalaryConfig,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Create salary configuration for an employee (admin-only).

    Sets highly sensitive fields (basic salary, PAN, Aadhar, bank account),
    so it is restricted to ADMIN; SUPERADMIN auto-passes via require_roles.
    """
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        salary_config_coll = db.get_collection("salary_config")

        # Check if config already exists
        existing = salary_config_coll.find_one({"employee_id": config.employee_id})
        if existing:
            raise HTTPException(
                status_code=409, detail="Salary config already exists for this employee"
            )

        config_doc = config.model_dump()
        config_doc.update(
            {
                "config_id": str(uuid.uuid4()),
                "is_active": True,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "created_by": current_user.get("user_id"),
            }
        )

        salary_config_coll.insert_one(config_doc)

        return {"status": "success", "config_id": config_doc["config_id"]}
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.get("/config/{employee_id}")
async def get_salary_config(
    employee_id: str, current_user: dict = Depends(get_current_user)
):
    """Get salary configuration for an employee"""
    db = _get_db()
    if not db:
        return {"config": None}

    try:
        config = _get_salary_config(db, employee_id)
        return {"config": _strip_id(config) or {}}
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.put("/config/{employee_id}")
async def update_salary_config(
    employee_id: str,
    update: SalaryConfigUpdate,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Update an employee's salary configuration (admin-only). Partial update."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        coll = db.get_collection("salary_config")
        existing = coll.find_one({"employee_id": employee_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Salary config not found")
        updates = {k: v for k, v in update.model_dump().items() if v is not None}
        if not updates:
            return {"status": "no_changes", "config_id": existing.get("config_id")}
        updates["updated_at"] = datetime.now().isoformat()
        coll.update_one({"employee_id": employee_id}, {"$set": updates})
        return {
            "status": "success",
            "config": _strip_id(coll.find_one({"employee_id": employee_id})),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.get("/config")
async def list_salary_configs(
    store_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    """List salary configurations (optionally scoped by store or entity)."""
    db = _get_db()
    if not db:
        return {"configs": [], "total": 0}
    try:
        query: dict = {}
        if entity_id:
            query["entity_id"] = entity_id
        if store_id:
            query["store_id"] = store_id
        if not include_inactive:
            # legacy docs may lack is_active; treat absent as active
            query["is_active"] = {"$ne": False}
        configs = [_strip_id(c) for c in db.get_collection("salary_config").find(query)]
        return {"configs": configs, "total": len(configs)}
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


class BulkSalaryConfig(BaseModel):
    """Wrapper for bulk salary-config upsert (backs the CSV import)."""

    configs: List[SalaryConfig]


@router.post("/config/bulk", status_code=201)
async def bulk_upsert_salary_config(
    payload: BulkSalaryConfig,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Bulk create/update salary configs (upsert by employee_id). CSV import target."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        coll = db.get_collection("salary_config")
        created, updated = 0, 0
        for cfg in payload.configs:
            doc = cfg.model_dump()
            existing = coll.find_one({"employee_id": cfg.employee_id})
            if existing:
                doc["updated_at"] = datetime.now().isoformat()
                coll.update_one({"employee_id": cfg.employee_id}, {"$set": doc})
                updated += 1
            else:
                doc.update(
                    {
                        "config_id": str(uuid.uuid4()),
                        "is_active": True,
                        "created_at": datetime.now().isoformat(),
                        "updated_at": datetime.now().isoformat(),
                        "created_by": current_user.get("user_id"),
                    }
                )
                coll.insert_one(doc)
                created += 1
        return {
            "status": "success",
            "created": created,
            "updated": updated,
            "total": created + updated,
        }
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


# ============================================================================
# SALARY SHEET ENDPOINTS
# ============================================================================


@router.get("/salary-sheet")
async def get_salary_sheet(
    month: int,
    year: int,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get monthly salary sheet for all employees in a store"""
    db = _get_db()
    if not db:
        return {"salaries": [], "total": 0, "store_id": store_id}

    active_store = store_id or current_user.get("active_store_id")

    try:
        salary_records_coll = db.get_collection("salary_records")

        # Query salary records for the month
        records = salary_records_coll.find(
            {"month": month, "year": year, "store_id": active_store}
        )

        salary_data = []
        for record in records:
            salary_data.append(
                {
                    "salary_record_id": record.get("salary_record_id"),
                    "employee_id": record.get("employee_id"),
                    "employee_name": record.get("employee_name"),
                    "basic": record.get("breakdown", {}).get("basic", 0),
                    "hra": record.get("breakdown", {}).get("hra", 0),
                    "allowances": record.get("breakdown", {}).get("conveyance", 0)
                    + record.get("breakdown", {}).get("medical", 0),
                    "gross_salary": record.get("breakdown", {}).get("gross_salary", 0),
                    "pf_employee": record.get("breakdown", {}).get("pf_employee", 0),
                    "esi": record.get("breakdown", {}).get("esi", 0),
                    "professional_tax": record.get("breakdown", {}).get(
                        "professional_tax", 0
                    ),
                    "tds": record.get("breakdown", {}).get("tds", 0),
                    "lwp_deduction": record.get("breakdown", {}).get(
                        "lwp_deduction", 0
                    ),
                    "advance_deduction": record.get("breakdown", {}).get(
                        "advance_deduction", 0
                    ),
                    "net_pay": record.get("breakdown", {}).get("net_pay", 0),
                    "status": record.get("status", "draft"),
                }
            )

        return {
            "month": month,
            "year": year,
            "store_id": active_store,
            "salaries": salary_data,
            "total": len(salary_data),
        }
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.get("/salary/{employee_id}")
async def get_employee_salary(
    employee_id: str,
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get individual employee salary breakdown for a specific month or latest"""
    db = _get_db()
    if not db:
        return {"salary": None}

    try:
        salary_records_coll = db.get_collection("salary_records")

        if month and year:
            # Get specific month
            record = salary_records_coll.find_one(
                {"employee_id": employee_id, "month": month, "year": year}
            )
        else:
            # Get latest
            records = list(
                salary_records_coll.find({"employee_id": employee_id})
                .sort("created_at", -1)
                .limit(1)
            )
            record = records[0] if records else None

        return {"salary": record or {}}
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


# ============================================================================
# SALARY CALCULATION ENDPOINT
# ============================================================================


@router.post("/salary/calculate", status_code=201)
async def calculate_salary(
    calc_request: MonthSalaryCalculation, current_user: dict = Depends(get_current_user)
):
    """Calculate salary for a month with all deductions"""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        # Get salary config
        salary_config = _get_salary_config(db, calc_request.employee_id)
        if not salary_config:
            raise HTTPException(
                status_code=404, detail="Salary configuration not found"
            )

        # Get employee details
        employee = _get_employee_details(db, calc_request.employee_id)
        if not employee:
            raise HTTPException(status_code=404, detail="Employee not found")

        # Calculate salary breakdown
        breakdown = _calculate_salary(
            salary_config,
            working_days=calc_request.working_days,
            leave_without_pay_days=calc_request.leave_without_pay_days,
            advance_deduction=calc_request.advance_deduction,
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
        existing = salary_records_coll.find_one(
            {
                "employee_id": calc_request.employee_id,
                "month": calc_request.month,
                "year": calc_request.year,
            }
        )

        if existing:
            salary_records_coll.update_one(
                {"salary_record_id": existing["salary_record_id"]},
                {"$set": salary_record},
            )
            return {
                "status": "updated",
                "salary_record_id": existing["salary_record_id"],
            }
        else:
            salary_records_coll.insert_one(salary_record)
            return {
                "status": "created",
                "salary_record_id": salary_record["salary_record_id"],
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


# ============================================================================
# SALARY ADVANCE ENDPOINTS
# ============================================================================


@router.post("/advances", status_code=201)
async def record_salary_advance(
    advance: SalaryAdvance, current_user: dict = Depends(get_current_user)
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
            "message": "Salary advance recorded",
        }
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.get("/advances/{employee_id}")
async def get_salary_advances(
    employee_id: str,
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
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
            "total": len(advances) if advances else 0,
        }
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.post("/advances/{advance_id}/settle", status_code=200)
async def settle_salary_advance(
    advance_id: str,
    settlement: AdvanceSettlement = Body(...),
    current_user: dict = Depends(get_current_user),
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
            },
        )

        return {
            "status": "success",
            "advance_id": advance_id,
            "message": "Advance settled and will be deducted from salary",
        }
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


# ============================================================================
# PAYSLIP ENDPOINTS
# ============================================================================


@router.get("/payslip/{employee_id}/{month}/{year}")
async def get_payslip(
    employee_id: str,
    month: int,
    year: int,
    current_user: dict = Depends(get_current_user),
):
    """Generate or retrieve payslip for an employee"""
    db = _get_db()
    if not db:
        return {"payslip": None}

    try:
        salary_records_coll = db.get_collection("salary_records")
        payslips_coll = db.get_collection("payslips")

        # Get salary record
        salary_record = salary_records_coll.find_one(
            {"employee_id": employee_id, "month": month, "year": year}
        )

        if not salary_record:
            raise HTTPException(
                status_code=404, detail="Salary record not found for this month"
            )

        # Check if payslip already exists
        payslip = payslips_coll.find_one(
            {"employee_id": employee_id, "month": month, "year": year}
        )

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
                "bank_account": (
                    _get_salary_config(db, employee_id).get("bank_account")
                    if _get_salary_config(db, employee_id)
                    else None
                ),
                "generated_at": datetime.now().isoformat(),
            }
            payslips_coll.insert_one(payslip)

        return {"payslip": payslip}
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.get("/payslip/{employee_id}")
async def get_latest_payslip(
    employee_id: str, current_user: dict = Depends(get_current_user)
):
    """Get latest payslip for an employee"""
    db = _get_db()
    if not db:
        return {"payslip": None}

    try:
        payslips_coll = db.get_collection("payslips")

        payslips = list(
            payslips_coll.find({"employee_id": employee_id})
            .sort("generated_at", -1)
            .limit(1)
        )

        return {"payslip": payslips[0] if payslips else None}
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


# ============================================================================
# INCENTIVE INTEGRATION
# ============================================================================


@router.get("/incentive-summary/{employee_id}/{month}/{year}")
async def get_incentive_summary(
    employee_id: str,
    month: int,
    year: int,
    current_user: dict = Depends(get_current_user),
):
    """Get incentive earned for a month (integrated from incentives module)"""
    db = _get_db()
    if not db:
        return {"incentive": None}

    try:
        # Try to get from incentives collection if available
        try:
            incentives_coll = db.get_collection("incentives")
            incentive = incentives_coll.find_one(
                {"staff_id": employee_id, "month": month, "year": year}
            )
            return {"incentive": incentive}
        except Exception as e:
            logger.error(f"Error fetching incentive: {str(e)}", exc_info=True)
            return {"incentive": None, "message": "Incentive data not found"}
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


# ============================================================================
# PROFESSIONAL TAX (PT) SLABS - state-aware, editable
# ============================================================================

# EDITABLE seed defaults. PT rules change; the accountant must verify these.
# basis = the salary basis the thresholds are evaluated against (MONTHLY/ANNUAL).
# Each slab: {min, max (None = infinity), amount, amount_february?, gender?}
DEFAULT_PT_SLABS = {
    "MH": {
        "state_code": "MH",
        "state_name": "Maharashtra",
        "basis": "MONTHLY",
        "gender_aware": True,
        "slabs": [
            {"min": 0, "max": 7500, "amount": 0, "gender": "MALE"},
            {"min": 7500.01, "max": 10000, "amount": 175, "gender": "MALE"},
            {"min": 10000.01, "max": None, "amount": 200, "amount_february": 300, "gender": "MALE"},
            {"min": 0, "max": 25000, "amount": 0, "gender": "FEMALE"},
            {"min": 25000.01, "max": None, "amount": 200, "amount_february": 300, "gender": "FEMALE"},
        ],
        "notes": "EDITABLE default - verify current Maharashtra PT. Women nil up to 25,000; +100 in February for the top slab.",
    },
    "JH": {
        "state_code": "JH",
        "state_name": "Jharkhand",
        "basis": "ANNUAL",
        "gender_aware": False,
        "slabs": [
            {"min": 0, "max": 300000, "amount": 0},
            {"min": 300000.01, "max": 500000, "amount": 100},
            {"min": 500000.01, "max": 800000, "amount": 150},
            {"min": 800000.01, "max": 1000000, "amount": 175},
            {"min": 1000000.01, "max": None, "amount": 208},
        ],
        "notes": "EDITABLE default - verify current Jharkhand PT. Annual gross basis; ~2,500/yr cap.",
    },
}


def pt_for(
    slab_doc: Optional[dict], monthly_gross: float, month: int, gender: str = "ANY"
) -> float:
    """Resolve the monthly Professional Tax from a state's slab doc.

    Pure helper, reused by the Phase-2 payroll engine. Annualizes gross when
    the state's basis is ANNUAL; applies the February override when present.
    """
    if not slab_doc:
        return 0.0
    slabs = slab_doc.get("slabs") or []
    basis = (slab_doc.get("basis") or "MONTHLY").upper()
    income = (monthly_gross * 12) if basis == "ANNUAL" else monthly_gross
    gender = (gender or "ANY").upper()
    if slab_doc.get("gender_aware") and gender == "ANY":
        gender = "MALE"  # default unknown gender to the general slab
    for slab in slabs:
        s_gender = (slab.get("gender") or "ANY").upper()
        if s_gender != "ANY" and s_gender != gender:
            continue
        lo = slab.get("min", 0) or 0
        hi = slab.get("max", None)
        if income >= lo and (hi is None or income <= hi):
            amount = slab.get("amount", 0) or 0
            if month == 2 and slab.get("amount_february") is not None:
                amount = slab.get("amount_february")
            return float(amount)
    return 0.0


@router.get("/pt-slabs")
async def list_pt_slabs(current_user: dict = Depends(get_current_user)):
    """List Professional Tax slabs for all states (returns seed defaults if none stored)."""
    db = _get_db()
    if not db:
        return {
            "pt_slabs": list(DEFAULT_PT_SLABS.values()),
            "total": len(DEFAULT_PT_SLABS),
            "source": "defaults",
        }
    try:
        slabs = [_strip_id(s) for s in db.get_collection("pt_slabs").find({})]
        if not slabs:
            return {
                "pt_slabs": list(DEFAULT_PT_SLABS.values()),
                "total": len(DEFAULT_PT_SLABS),
                "source": "defaults",
            }
        return {"pt_slabs": slabs, "total": len(slabs), "source": "db"}
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.get("/pt-slabs/{state_code}")
async def get_pt_slab(state_code: str, current_user: dict = Depends(get_current_user)):
    """Get the PT slab for one state (falls back to a seeded default)."""
    state_code = state_code.upper()
    db = _get_db()
    default = DEFAULT_PT_SLABS.get(state_code)
    if not db:
        return {"pt_slab": default}
    try:
        slab = db.get_collection("pt_slabs").find_one({"state_code": state_code})
        return {"pt_slab": _strip_id(slab) or default}
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


class PtSlabUpsert(BaseModel):
    """Create/replace a state's Professional Tax slab table."""

    state_name: Optional[str] = None
    basis: str = Field(default="MONTHLY")
    gender_aware: bool = False
    slabs: List[dict] = Field(default_factory=list)
    notes: Optional[str] = None


@router.put("/pt-slabs/{state_code}")
async def upsert_pt_slab(
    state_code: str,
    payload: PtSlabUpsert,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Create or replace a state's PT slab (admin-only)."""
    state_code = state_code.upper()
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        coll = db.get_collection("pt_slabs")
        doc = payload.model_dump()
        doc["state_code"] = state_code
        doc["updated_at"] = datetime.now().isoformat()
        coll.update_one({"state_code": state_code}, {"$set": doc}, upsert=True)
        return {
            "status": "success",
            "pt_slab": _strip_id(coll.find_one({"state_code": state_code})),
        }
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.post("/pt-slabs/seed", status_code=201)
async def seed_pt_slabs(current_user: dict = Depends(require_roles("ADMIN"))):
    """Seed default Jharkhand + Maharashtra PT slabs (idempotent; admin-only)."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        coll = db.get_collection("pt_slabs")
        seeded = 0
        for state_code, slab in DEFAULT_PT_SLABS.items():
            if not coll.find_one({"state_code": state_code}):
                doc = dict(slab)
                doc["updated_at"] = datetime.now().isoformat()
                coll.insert_one(doc)
                seeded += 1
        return {
            "status": "success",
            "seeded": seeded,
            "states": list(DEFAULT_PT_SLABS.keys()),
        }
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


# ============================================================================
# ROOT ENDPOINT
# ============================================================================


@router.get("")
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
            "incentive_summary": "GET /payroll/incentive-summary/{employee_id}/{month}/{year}",
        },
    }
