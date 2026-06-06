"""
IMS 2.0 - Payroll & Salary Router
==================================
Salary management, advances, payslips, and incentive integration
Indian salary structure with PF, ESI, PT, TDS deductions
"""

from fastapi import APIRouter, Depends, Query, HTTPException, Body
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict
from datetime import datetime, date, timedelta
from calendar import monthrange
import uuid
import math
import logging

logger = logging.getLogger(__name__)

from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from .auth import get_current_user, require_roles
from ..dependencies import validate_store_access, resolve_store_scope
from ..utils.ist import now_ist, ist_day_start_utc
from ..services.payroll_engine import (
    DEFAULT_PT_SLABS,
    pt_for,
    pt_code_for_state,
    compute_payroll,
)
from ..services.payroll_exports import (
    statutory_summary,
    build_salary_jv_xml,
    build_pf_ecr,
    build_payslip_html,
)

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
    store_id: Optional[str] = Field(
        default=None, description="Primary work store (PT state)"
    )
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
    # Commission (HR-3): percentage of closed-order revenue earned as commission.
    # Default 0 means commission is not applicable for this employee.
    commission_rate_percent: float = Field(
        default=0.0,
        ge=0,
        le=100,
        description="Commission rate (%) applied to closed order revenue",
    )


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
    commission_rate_percent: Optional[float] = Field(default=None, ge=0, le=100)


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
    """Get database connection.

    Prefer the shared ``dependencies.get_db`` (which falls back to the SEEDED
    mock DB when no live Mongo is connected) so payroll config / PT-slab / salary
    endpoints work in a mock / test / fresh environment like every other router.
    The old code returned ``database.connection.get_db()`` directly, a
    NOT-connected handle whose ``get_collection()`` is None -> 500 with
    "'NoneType' object has no attribute 'find'" whenever Mongo was absent."""
    try:
        from ..dependencies import get_db as _dep_get_db

        db = _dep_get_db()
        if db is not None:
            return db
    except Exception:  # noqa: BLE001
        pass
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
    except HTTPException:
        raise
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
    # BUG-062 tail: salary configs carry bank/PAN/UAN PII -- a store-scoped role
    # must not read another store's by passing ?store_id (validate), nor all
    # stores by omitting it (pinned to own store; HQ roles still get all).
    store_id = resolve_store_scope(store_id, current_user)
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


def _flatten_payroll_row(record: dict) -> dict:
    """Convert a ``payroll`` collection row (written by the run engine) to the
    flat shape the salary-sheet and payslip endpoints return to the FE.

    The run engine stores a nested ``breakdown.earnings.*`` /
    ``breakdown.deductions.*`` structure.  The legacy ``salary_records``
    collection used a flat breakdown dict.  This helper bridges both so the FE
    never sees empty rows.
    """
    bd = record.get("breakdown") or {}
    earn = bd.get("earnings") or {}
    ded = bd.get("deductions") or {}

    basic = float(earn.get("basic", 0))
    hra = float(earn.get("hra", 0))
    conveyance = float(earn.get("conveyance", 0))
    medical = float(earn.get("medical", 0))
    special = float(earn.get("special_allowance", 0) or earn.get("other_allowances", 0))
    earned_gross = float(earn.get("earned_gross", 0) or earn.get("full_gross", 0))
    pf_emp = float(ded.get("pf_employee", 0))
    esi_emp = float(ded.get("esi_employee", 0) or ded.get("esi", 0))
    pt = float(ded.get("professional_tax", 0))
    tds = float(ded.get("tds", 0))
    lwp_ded = float(ded.get("lwp_deduction", 0) or ded.get("advance_recovery", 0) * 0)
    adv_ded = float(ded.get("advance_recovery", 0) or ded.get("advance_deduction", 0))
    net = float(bd.get("net_pay", 0) or record.get("net_salary", 0))

    return {
        # ID field: payroll rows use payroll_id; legacy used salary_record_id.
        # Expose both so the FE key always resolves.
        "salary_record_id": record.get("payroll_id")
        or record.get("salary_record_id")
        or "",
        "payroll_id": record.get("payroll_id") or "",
        "employee_id": record.get("employee_id", ""),
        "employee_name": record.get("employee_name", ""),
        "basic": basic,
        "hra": hra,
        "conveyance": conveyance,
        "medical": medical,
        "allowances": conveyance + medical + special,
        "gross_salary": earned_gross,
        "pf_employee": pf_emp,
        "esi": esi_emp,
        "professional_tax": pt,
        "tds": tds,
        "lwp_deduction": lwp_ded,
        "advance_deduction": adv_ded,
        "net_pay": net,
        "status": record.get("status", "DRAFT"),
        # Keep the nested breakdown so the print endpoint / Tally export still work.
        "breakdown": {
            "basic": basic,
            "hra": hra,
            "conveyance": conveyance,
            "medical": medical,
            "special_allowance": special,
            "gross_salary": earned_gross,
            "pf_employee": pf_emp,
            "pf_employer": float(
                (record.get("breakdown") or {})
                .get("employer_contributions", {})
                .get("pf_employer_total", 0)
            ),
            "esi": esi_emp,
            "professional_tax": pt,
            "tds": tds,
            "lwp_deduction": lwp_ded,
            "advance_deduction": adv_ded,
            "total_deductions": float(ded.get("total_deductions", 0)),
            "net_pay": net,
        },
    }


@router.get("/salary-sheet")
async def get_salary_sheet(
    month: int,
    year: int,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get monthly salary sheet for all employees in a store.

    Reads from the ``payroll`` collection (written by POST /payroll/run).
    Falls back to the legacy ``salary_records`` collection so data is never
    silently missing on installs that still have the old schema.
    """
    db = _get_db()
    if not db:
        return {"salaries": [], "total": 0, "store_id": store_id}

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    try:
        query: dict = {"month": month, "year": year}
        if active_store:
            query["store_id"] = active_store

        # Primary: payroll collection (run engine output)
        records = list(db.get_collection("payroll").find(query))

        # Fallback: legacy salary_records
        if not records:
            records = list(db.get_collection("salary_records").find(query))

        salary_data = [_flatten_payroll_row(r) for r in records]

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


def _build_payslip_from_run_row(run_row: dict, employee: dict, db: object) -> dict:
    """Build a payslip dict from a ``payroll`` collection run row.

    Returns the flat breakdown shape the FE payslip display expects.
    """
    flat = _flatten_payroll_row(run_row)
    cfg = _get_salary_config(db, run_row.get("employee_id", ""))
    return {
        "payslip_id": run_row.get("payroll_id") or str(uuid.uuid4()),
        "employee_id": run_row.get("employee_id", ""),
        "employee_name": employee.get("full_name", "")
        or run_row.get("employee_name", ""),
        "employee_number": employee.get("employee_number", ""),
        "designation": employee.get("designation", ""),
        "department": employee.get("department", ""),
        "month": run_row.get("month"),
        "year": run_row.get("year"),
        "breakdown": flat["breakdown"],
        "bank_account": cfg.get("bank_account") if cfg else None,
        "generated_at": run_row.get("updated_at")
        or run_row.get("created_at")
        or datetime.now().isoformat(),
        # Include payroll status so FE can show DRAFT/APPROVED/PAID badge.
        "status": run_row.get("status", "DRAFT"),
    }


@router.get("/payslip/{employee_id}/{month}/{year}")
async def get_payslip(
    employee_id: str,
    month: int,
    year: int,
    current_user: dict = Depends(get_current_user),
):
    """Generate or retrieve payslip for an employee.

    Reads from the ``payroll`` collection (run engine output) first, then falls
    back to the legacy ``salary_records`` collection so existing data is never
    lost.  A payslip record is materialised in ``payslips`` collection only
    once; subsequent calls return the cached version.
    """
    db = _get_db()
    if not db:
        return {"payslip": None}

    try:
        payslips_coll = db.get_collection("payslips")

        # Return cached payslip if one already exists.
        payslip = payslips_coll.find_one(
            {"employee_id": employee_id, "month": month, "year": year}
        )
        if payslip:
            return {"payslip": _strip_id(payslip)}

        employee = _get_employee_details(db, employee_id)

        # Try the run-engine ``payroll`` collection first.
        run_row = db.get_collection("payroll").find_one(
            {"employee_id": employee_id, "month": month, "year": year}
        )
        if run_row:
            payslip = _build_payslip_from_run_row(run_row, employee, db)
            payslips_coll.insert_one({**payslip})
            return {"payslip": _strip_id(payslip)}

        # Legacy fallback: salary_records collection.
        salary_record = db.get_collection("salary_records").find_one(
            {"employee_id": employee_id, "month": month, "year": year}
        )
        if not salary_record:
            return {"payslip": None}

        cfg = _get_salary_config(db, employee_id)
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
            "bank_account": cfg.get("bank_account") if cfg else None,
            "generated_at": datetime.now().isoformat(),
        }
        payslips_coll.insert_one({**payslip})
        return {"payslip": _strip_id(payslip)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.get("/payslip/{employee_id}")
async def get_latest_payslip(
    employee_id: str, current_user: dict = Depends(get_current_user)
):
    """Get the most recent payslip for an employee.

    Searches ``payslips`` cache, then the ``payroll`` run collection, so staff
    see their slip even before a manager has clicked 'generate payslip'.
    """
    db = _get_db()
    if not db:
        return {"payslip": None}

    try:
        payslips_coll = db.get_collection("payslips")

        # Cached payslips (newest first).
        cached = list(
            payslips_coll.find({"employee_id": employee_id})
            .sort([("year", -1), ("month", -1)])
            .limit(1)
        )
        if cached:
            return {"payslip": _strip_id(cached[0])}

        # Fall through to the run collection and synthesise a payslip.
        run_row = (
            list(
                db.get_collection("payroll")
                .find({"employee_id": employee_id})
                .sort([("year", -1), ("month", -1)])
                .limit(1)
            )
            or [None]
        )[0]
        if not run_row:
            return {"payslip": None}

        employee = _get_employee_details(db, employee_id)
        payslip = _build_payslip_from_run_row(run_row, employee, db)
        # Persist so the next call is instant.
        payslips_coll.insert_one({**payslip})
        return {"payslip": _strip_id(payslip)}
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

# DEFAULT_PT_SLABS and pt_for() live in api/services/payroll_engine.py (single
# source of truth) and are imported at the top of this module.


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


_VALID_PT_BASIS = {"MONTHLY", "ANNUAL"}


class PtSlabUpsert(BaseModel):
    """Create/replace a state's Professional Tax slab table."""

    state_name: Optional[str] = None
    basis: str = Field(default="MONTHLY")
    gender_aware: bool = False
    slabs: List[dict] = Field(default_factory=list)
    notes: Optional[str] = None

    @field_validator("basis")  # type: ignore[call-overload]
    @classmethod
    def _validate_basis(cls, v: str) -> str:
        uv = v.strip().upper()
        if uv not in _VALID_PT_BASIS:
            raise ValueError(f"basis must be one of {sorted(_VALID_PT_BASIS)}")
        return uv

    @field_validator("slabs")
    @classmethod
    def _validate_slabs(cls, v: List[dict]) -> List[dict]:
        """Each slab entry must have a non-negative 'tax' amount."""
        for i, slab in enumerate(v):
            if not isinstance(slab, dict):
                raise ValueError(f"slabs[{i}] must be an object")
            tax = slab.get("tax")
            if tax is not None and (not isinstance(tax, (int, float)) or tax < 0):
                raise ValueError(f"slabs[{i}].tax must be a non-negative number")
        return v


@router.put("/pt-slabs/{state_code}")
async def upsert_pt_slab(
    state_code: str,
    payload: PtSlabUpsert,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Create or replace a state's PT slab (admin-only)."""
    from ..services.org_validation import INDIAN_STATE_CODES

    state_code = state_code.upper()
    if state_code not in INDIAN_STATE_CODES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown state_code '{state_code}'. Must be a 2-digit GST state code.",
        )
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
# PAYROLL RUN (compute -> DRAFT -> APPROVED -> PAID/locked)
# ============================================================================

_RUN_ROLES = ("ADMIN", "ACCOUNTANT")  # SUPERADMIN auto-passes


class PayrollRunRequest(BaseModel):
    """Compute (and optionally save) payroll for a month + scope."""

    month: int = Field(..., ge=1, le=12)
    year: int = Field(..., ge=2000, le=2100)
    store_id: Optional[str] = None
    entity_id: Optional[str] = None
    lwp_days: Dict[str, float] = Field(
        default_factory=dict
    )  # employee_id -> unpaid days
    incentives: Optional[Dict[str, float]] = None  # override; else auto-fetched
    advances: Dict[str, float] = Field(
        default_factory=dict
    )  # employee_id -> recovery amount
    dry_run: bool = False  # true = preview, do not persist


class PayrollBatchAction(BaseModel):
    """Approve/lock all matching payroll rows for a month + scope."""

    month: int = Field(..., ge=1, le=12)
    year: int = Field(..., ge=2000, le=2100)
    store_id: Optional[str] = None
    entity_id: Optional[str] = None


def _fetch_incentive(db, employee_id: str, month: int, year: int) -> float:
    """Best-effort monthly incentive from the incentives collection."""
    if db is None:
        return 0.0
    try:
        doc = db.get_collection("incentives").find_one(
            {"staff_id": employee_id, "month": month, "year": year}
        )
        if not doc:
            return 0.0
        for key in ("incentive_amount", "amount", "total", "payout", "net_incentive"):
            v = doc.get(key)
            if isinstance(v, (int, float)):
                return float(v)
        return 0.0
    except Exception:  # pragma: no cover - defensive
        return 0.0


def _resolve_pt_slab(db, config: dict) -> Optional[dict]:
    """Resolve the PT slab for an employee from their store's state."""
    state = None
    store_id = config.get("store_id")
    if db is not None and store_id:
        try:
            store = db.get_collection("stores").find_one({"store_id": store_id})
            if store:
                state = store.get("state") or store.get("gst_state_code")
        except Exception:
            state = None
    code = pt_code_for_state(state)
    if not code:
        return None
    if db is not None:
        try:
            slab = db.get_collection("pt_slabs").find_one({"state_code": code})
            if slab:
                return slab
        except Exception:
            pass
    return DEFAULT_PT_SLABS.get(code)


def _scope_configs(db, store_id: Optional[str], entity_id: Optional[str]) -> list:
    query: dict = {"is_active": {"$ne": False}}
    if entity_id:
        query["entity_id"] = entity_id
    if store_id:
        query["store_id"] = store_id
    return list(db.get_collection("salary_config").find(query))


@router.post("/run")
async def run_payroll(
    req: PayrollRunRequest,
    current_user: dict = Depends(require_roles(*_RUN_ROLES)),
):
    """Compute payroll for every active employee in scope.

    Persists DRAFT rows (idempotent per employee+month+year) unless dry_run.
    Already APPROVED/PAID rows are never overwritten.
    """
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        configs = _scope_configs(db, req.store_id, req.entity_id)
        payroll_coll = db.get_collection("payroll")
        rows = []
        totals = {"gross": 0.0, "deductions": 0.0, "net": 0.0, "employer_cost": 0.0}
        for cfg in configs:
            emp = cfg.get("employee_id")
            lwp = float(req.lwp_days.get(emp, 0) or 0)
            incentive = (req.incentives or {}).get(emp)
            if incentive is None:
                incentive = _fetch_incentive(db, emp, req.month, req.year)
            advance = float(req.advances.get(emp, 0) or 0)
            pt_slab = _resolve_pt_slab(db, cfg)
            breakdown = compute_payroll(
                cfg,
                month=req.month,
                year=req.year,
                lwp_days=lwp,
                incentive=incentive,
                advance_recovery=advance,
                pt_slab=pt_slab,
                gender=cfg.get("gender", "ANY"),
            )
            doc = {
                "employee_id": emp,
                "employee_name": _get_employee_details(db, emp).get("full_name", ""),
                "store_id": cfg.get("store_id"),
                "entity_id": cfg.get("entity_id"),
                "year": req.year,
                "month": req.month,
                "breakdown": breakdown,
                "basic_salary": breakdown["earnings"]["basic"],
                "allowances": round(
                    breakdown["earnings"]["earned_gross"]
                    - breakdown["earnings"]["basic"],
                    2,
                ),
                "incentives": breakdown["earnings"]["incentive"],
                "deductions": breakdown["deductions"]["total_deductions"],
                "advance_deduction": breakdown["deductions"]["advance_recovery"],
                "net_salary": breakdown["net_pay"],
                "status": "DRAFT",
                "updated_at": datetime.now().isoformat(),
            }
            if not req.dry_run:
                existing = payroll_coll.find_one(
                    {"employee_id": emp, "year": req.year, "month": req.month}
                )
                if existing and existing.get("status") in ("APPROVED", "PAID"):
                    rows.append(
                        {
                            "employee_id": emp,
                            "skipped": True,
                            "reason": f"already {existing.get('status')}",
                            "net_salary": existing.get("net_salary"),
                        }
                    )
                    continue
                if existing:
                    doc["payroll_id"] = existing.get("payroll_id")
                    payroll_coll.update_one(
                        {"employee_id": emp, "year": req.year, "month": req.month},
                        {"$set": doc},
                    )
                else:
                    doc["payroll_id"] = str(uuid.uuid4())
                    doc["created_at"] = datetime.now().isoformat()
                    doc["created_by"] = current_user.get("user_id")
                    payroll_coll.insert_one(doc)
            rows.append(_strip_id({k: v for k, v in doc.items()}))
            totals["gross"] += breakdown["earnings"]["total_earnings"]
            totals["deductions"] += breakdown["deductions"]["total_deductions"]
            totals["net"] += breakdown["net_pay"]
            totals["employer_cost"] += breakdown["ctc_cost"]
        totals = {k: round(v, 2) for k, v in totals.items()}
        return {
            "status": "success",
            "month": req.month,
            "year": req.year,
            "count": len(rows),
            "dry_run": req.dry_run,
            "rows": rows,
            "totals": totals,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Payroll run failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll run failed. Please try again."
        )


@router.get("/run/rows")
async def list_payroll_rows(
    month: int,
    year: int,
    store_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """The salary register: saved payroll rows for a month + scope."""
    db = _get_db()
    if not db:
        return {"rows": [], "total": 0, "totals": {}}
    # BUG-062 tail: scope the salary register to the caller's store reach.
    store_id = resolve_store_scope(store_id, current_user)
    try:
        query: dict = {"month": month, "year": year}
        if store_id:
            query["store_id"] = store_id
        if entity_id:
            query["entity_id"] = entity_id
        rows = [_strip_id(r) for r in db.get_collection("payroll").find(query)]
        totals = {
            "gross": round(
                sum(
                    (r.get("breakdown", {}).get("earnings", {}) or {}).get(
                        "total_earnings", 0
                    )
                    for r in rows
                ),
                2,
            ),
            "deductions": round(sum(r.get("deductions", 0) or 0 for r in rows), 2),
            "net": round(sum(r.get("net_salary", 0) or 0 for r in rows), 2),
        }
        return {"rows": rows, "total": len(rows), "totals": totals}
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.post("/approve")
async def approve_payroll(
    req: PayrollBatchAction,
    current_user: dict = Depends(require_roles(*_RUN_ROLES)),
):
    """Move DRAFT payroll rows to APPROVED for a month + scope."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        query: dict = {"month": req.month, "year": req.year, "status": "DRAFT"}
        if req.store_id:
            query["store_id"] = req.store_id
        if req.entity_id:
            query["entity_id"] = req.entity_id
        result = db.get_collection("payroll").update_many(
            query,
            {
                "$set": {
                    "status": "APPROVED",
                    "approved_by": current_user.get("user_id"),
                    "approved_at": datetime.now().isoformat(),
                }
            },
        )
        return {"status": "success", "approved": getattr(result, "modified_count", 0)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Payroll approve failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll approve failed. Please try again."
        )


@router.post("/lock")
async def lock_payroll(
    req: PayrollBatchAction,
    current_user: dict = Depends(require_roles("ADMIN")),
):
    """Lock APPROVED payroll rows as PAID for a month + scope (final)."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        query: dict = {"month": req.month, "year": req.year, "status": "APPROVED"}
        if req.store_id:
            query["store_id"] = req.store_id
        if req.entity_id:
            query["entity_id"] = req.entity_id
        result = db.get_collection("payroll").update_many(
            query,
            {"$set": {"status": "PAID", "paid_at": datetime.now().isoformat()}},
        )
        return {"status": "success", "locked": getattr(result, "modified_count", 0)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Payroll lock failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll lock failed. Please try again."
        )


# ============================================================================
# PAYROLL EXPORTS (payslip print, Tally salary JV, PF ECR, statutory summary)
# ============================================================================


def _payroll_rows(db, month: int, year: int, store_id, entity_id) -> list:
    query: dict = {"month": month, "year": year}
    if store_id:
        query["store_id"] = store_id
    if entity_id:
        query["entity_id"] = entity_id
    return [_strip_id(r) for r in db.get_collection("payroll").find(query)]


@router.get("/registers/summary")
async def payroll_statutory_summary(
    month: int,
    year: int,
    store_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """PF/ESI/PT/TDS + gross/net totals for a month + scope (a filing aid)."""
    db = _get_db()
    if not db:
        return {
            "summary": statutory_summary([]),
            "month": month,
            "year": year,
            "count": 0,
        }
    # BUG-062 tail: scope statutory totals to the caller's store reach.
    store_id = resolve_store_scope(store_id, current_user)
    try:
        rows = _payroll_rows(db, month, year, store_id, entity_id)
        return {
            "summary": statutory_summary(rows),
            "month": month,
            "year": year,
            "count": len(rows),
        }
    except Exception as e:
        logger.error("Payroll operation failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payroll operation failed. Please try again."
        )


@router.get("/tally/salary-jv")
async def payroll_tally_jv(
    month: int,
    year: int,
    entity_id: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_RUN_ROLES)),
):
    """Balanced Tally salary Journal Voucher (XML) for a month + entity."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    # BUG-062 tail: scope the Tally salary-JV export to the caller's store reach.
    store_id = resolve_store_scope(store_id, current_user)
    try:
        rows = _payroll_rows(db, month, year, store_id, entity_id)
        if not rows:
            raise HTTPException(
                status_code=404, detail="No payroll rows for this month/scope"
            )
        entity = None
        if entity_id:
            entity = db.get_collection("entities").find_one({"entity_id": entity_id})
        xml = build_salary_jv_xml(entity, rows, month, year)
        filename = f"salary_jv_{year}_{month:02d}.xml"
        return Response(
            content=xml,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Tally JV export failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Tally JV export failed. Please try again."
        )


@router.get("/registers/pf-ecr")
async def payroll_pf_ecr(
    month: int,
    year: int,
    entity_id: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_RUN_ROLES)),
):
    """EPFO PF ECR text file for a month + scope."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    # BUG-062 tail: scope the PF ECR export to the caller's store reach.
    store_id = resolve_store_scope(store_id, current_user)
    try:
        rows = _payroll_rows(db, month, year, store_id, entity_id)
        emps = [r.get("employee_id") for r in rows]
        cfgs = (
            {
                c.get("employee_id"): c
                for c in db.get_collection("salary_config").find(
                    {"employee_id": {"$in": emps}}
                )
            }
            if emps
            else {}
        )
        text = build_pf_ecr(rows, cfgs)
        filename = f"pf_ecr_{year}_{month:02d}.txt"
        return PlainTextResponse(
            text,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("PF ECR export failed: %s", e)
        raise HTTPException(
            status_code=500, detail="PF ECR export failed. Please try again."
        )


@router.get("/payslip/{employee_id}/{month}/{year}/print", response_class=HTMLResponse)
async def payslip_print(
    employee_id: str,
    month: int,
    year: int,
    current_user: dict = Depends(get_current_user),
):
    """Branded, printable HTML payslip from the computed payroll row."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        row = db.get_collection("payroll").find_one(
            {"employee_id": employee_id, "month": month, "year": year}
        )
        if not row:
            raise HTTPException(
                status_code=404, detail="Payroll row not found for this month"
            )
        row = _strip_id(row)
        entity = None
        if row.get("entity_id"):
            entity = db.get_collection("entities").find_one(
                {"entity_id": row["entity_id"]}
            )
        employee = _get_employee_details(db, employee_id)
        return HTMLResponse(build_payslip_html(row, entity, employee))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Payslip print failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Payslip print failed. Please try again."
        )


# ============================================================================
# COMMISSION LEDGER  (HR-3 -- per-sale commission + staff leaderboard)
# ============================================================================
# Commission is derived from completed orders attributed to each staff member.
# We do NOT store a separate commission document -- we aggregate live from the
# ``orders`` collection on-demand.  The rate is read from salary_config
# (commission_rate_percent, default 0).  If the store has no per-staff rates
# configured the response returns zero commission but still shows the sales
# tally -- useful as a leaderboard even before commission rates are set up.
#
# Roles: any manager tier + accountant (same as salary-sheet).
_COMMISSION_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")


@router.get("/commission/summary")
async def get_commission_summary(
    month: int = Query(..., ge=1, le=12, description="Calendar month (1-12)"),
    year: int = Query(..., ge=2020, le=2100, description="Calendar year"),
    store_id: Optional[str] = Query(None, description="Filter to a single store"),
    employee_id: Optional[str] = Query(
        None, description="Filter to a single employee (self-service)"
    ),
    current_user: dict = Depends(get_current_user),
):
    """Per-staff commission ledger for a month.

    Returns each staff member's: sales count, revenue, computed commission
    amount (revenue * commission_rate_percent / 100), and rank within the store.

    Any authenticated user may fetch their OWN commission (employee_id == their
    user_id).  Manager-tier roles see all staff in scope.
    """
    db = _get_db()
    caller_id = current_user.get("user_id") or current_user.get("id")
    caller_roles = current_user.get("roles") or []

    # Self-service gate: staff may only read their own data.
    is_manager = any(r in caller_roles for r in ("SUPERADMIN", *_COMMISSION_ROLES))
    if not is_manager:
        # Non-manager: restrict to self only.
        employee_id = caller_id

    if db is None:
        return {"items": [], "month": month, "year": year, "total_commission": 0}

    try:
        # Date range for the requested month.
        days_in_month = monthrange(year, month)[1]
        from_dt = datetime(year, month, 1)
        to_dt = datetime(year, month, days_in_month, 23, 59, 59)

        # Build order query.
        order_query: dict = {
            "status": {"$in": ["COMPLETED", "DELIVERED", "PAID"]},
            "created_at": {"$gte": from_dt, "$lte": to_dt},
        }
        active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
        if active_store:
            order_query["store_id"] = active_store
        if employee_id:
            order_query["$or"] = [
                {"sales_staff_id": employee_id},
                {"created_by": employee_id},
            ]

        orders = list(db.get_collection("orders").find(order_query).limit(50000))

        # Aggregate by staff.
        staff_map: dict = {}
        for o in orders:
            sid = o.get("sales_staff_id") or o.get("created_by") or "unknown"
            sname = o.get("sales_staff_name") or o.get("created_by_name") or sid
            store = o.get("store_id", "")
            if sid not in staff_map:
                staff_map[sid] = {
                    "employee_id": sid,
                    "name": sname,
                    "store_id": store,
                    "sales_count": 0,
                    "revenue": 0.0,
                    "orders": [],
                }
            amount = float(o.get("total_amount") or o.get("grand_total") or 0)
            staff_map[sid]["sales_count"] += 1
            staff_map[sid]["revenue"] += amount
            staff_map[sid]["orders"].append(
                {
                    "order_id": o.get("order_id") or o.get("_id", ""),
                    "date": str(o.get("created_at", ""))[:10],
                    "amount": amount,
                }
            )

        # Fetch commission rates from salary_config.
        emp_ids = list(staff_map.keys())
        rate_map: dict = {}
        if emp_ids:
            for cfg in db.get_collection("salary_config").find(
                {"employee_id": {"$in": emp_ids}},
                {"employee_id": 1, "commission_rate_percent": 1},
            ):
                rate_map[cfg["employee_id"]] = float(
                    cfg.get("commission_rate_percent") or 0
                )

        # Build result rows and rank by revenue.
        items = []
        for sid, stats in staff_map.items():
            rate = rate_map.get(sid, 0.0)
            commission = round(stats["revenue"] * rate / 100, 2)
            items.append(
                {
                    "employee_id": sid,
                    "name": stats["name"],
                    "store_id": stats["store_id"],
                    "sales_count": stats["sales_count"],
                    "revenue": round(stats["revenue"], 2),
                    "commission_rate_percent": rate,
                    "commission_amount": commission,
                    "rank": 0,
                    # Include last 10 orders for the detail drilldown.
                    "recent_orders": sorted(
                        stats["orders"], key=lambda x: x["date"], reverse=True
                    )[:10],
                }
            )

        items.sort(key=lambda x: x["revenue"], reverse=True)
        for i, item in enumerate(items):
            item["rank"] = i + 1

        total_commission = round(sum(x["commission_amount"] for x in items), 2)
        return {
            "month": month,
            "year": year,
            "store_id": active_store,
            "items": items,
            "total_commission": total_commission,
        }
    except Exception as e:
        logger.error("Commission summary failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Commission summary failed. Please try again."
        )


@router.get("/commission/leaderboard")
async def get_commission_leaderboard(
    period: str = Query("month", description="today | week | month"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Staff sales leaderboard (revenue-ranked) for today/week/month.

    All authenticated users can view the leaderboard -- it motivates staff.
    SUPERADMIN and manager roles see names; non-managers see anonymised ranks
    (their own name is always revealed).
    """
    db = _get_db()
    if db is None:
        return {"leaderboard": [], "period": period}

    try:
        now = now_ist()
        today = now.date()
        if period == "today":
            # IST-midnight today, as the equivalent naive-UTC instant for created_at
            from_dt = ist_day_start_utc(today)
        elif period == "week":
            # IST-midnight of the Monday of this IST week
            week_start = today - timedelta(days=now.weekday())
            from_dt = ist_day_start_utc(week_start)
        else:  # month
            # IST-midnight of the 1st of this IST month
            from_dt = ist_day_start_utc(today.replace(day=1))

        active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
        query: dict = {
            "status": {"$in": ["COMPLETED", "DELIVERED", "PAID"]},
            "created_at": {"$gte": from_dt},
        }
        if active_store:
            query["store_id"] = active_store

        orders = list(db.get_collection("orders").find(query).limit(50000))

        staff_map: dict = {}
        for o in orders:
            sid = o.get("sales_staff_id") or o.get("created_by") or "unknown"
            sname = o.get("sales_staff_name") or o.get("created_by_name") or sid
            if sid not in staff_map:
                staff_map[sid] = {"name": sname, "sales_count": 0, "revenue": 0.0}
            staff_map[sid]["sales_count"] += 1
            staff_map[sid]["revenue"] += float(
                o.get("total_amount") or o.get("grand_total") or 0
            )

        caller_id = current_user.get("user_id") or current_user.get("id")
        caller_roles = current_user.get("roles") or []
        is_manager = any(
            r in caller_roles
            for r in ("SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER")
        )

        leaderboard = []
        for sid, stats in staff_map.items():
            reveal_name = is_manager or sid == caller_id
            leaderboard.append(
                {
                    "staff_id": sid,
                    "name": stats["name"] if reveal_name else "Staff Member",
                    "sales_count": stats["sales_count"],
                    "revenue": round(stats["revenue"], 2),
                    "rank": 0,
                    "badge": "",
                    "is_self": sid == caller_id,
                }
            )

        leaderboard.sort(key=lambda x: x["revenue"], reverse=True)
        badges = ["Champion", "Star Performer", "Rising Star"]
        for i, entry in enumerate(leaderboard):
            entry["rank"] = i + 1
            entry["badge"] = badges[i] if i < 3 else "Team Player"

        return {"leaderboard": leaderboard, "period": period}
    except Exception as e:
        logger.error("Commission leaderboard failed: %s", e)
        raise HTTPException(
            status_code=500, detail="Commission leaderboard failed. Please try again."
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
