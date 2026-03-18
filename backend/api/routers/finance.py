# ============================================================================
# Finance & Accounting Router
# ============================================================================

from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from enum import Enum

from api.dependencies import get_current_user, get_db
from api.models.user import UserInDB
from api.repositories.finance_repository import FinanceRepository

# ============================================================================
# Enums
# ============================================================================

class GSTType(str, Enum):
    """GST classification for transactions"""
    CGST_SGST = "CGST_SGST"  # Within state
    IGST = "IGST"  # Inter-state
    EXEMPT = "EXEMPT"  # Exempt transactions


# ============================================================================
# Request/Response Schemas
# ============================================================================

class RevenueRecord(BaseModel):
    """Revenue transaction record"""
    date: datetime
    order_id: str
    amount: float
    gst_amount: float
    gst_type: GSTType
    customer_name: str
    payment_method: str
    remarks: Optional[str] = None

    class Config:
        from_attributes = True


class ExpenseSummary(BaseModel):
    """Expense summary by category"""
    category: str
    amount: float
    gst_amount: float
    gst_type: GSTType
    count: int
    percentage: float

    class Config:
        from_attributes = True


class ProfitLossStatement(BaseModel):
    """P&L statement for a period"""
    period_start: datetime
    period_end: datetime
    total_revenue: float
    revenue_gst: float
    net_revenue: float
    total_expenses: float
    expenses_gst: float
    net_expenses: float
    gross_profit: float
    gross_margin_percent: float
    operating_expenses: float
    net_profit: float
    net_margin_percent: float

    class Config:
        from_attributes = True


class GSTSummary(BaseModel):
    """GST summary for a period"""
    period: str
    cgst_collected: float
    sgst_collected: float
    total_sgst_cgst: float
    igst_collected: float
    total_gst_collected: float
    gst_payable: float
    input_tax_credit: float
    net_gst_payable: float

    class Config:
        from_attributes = True


class OutstandingReceivable(BaseModel):
    """Outstanding receivable from customer"""
    customer_id: str
    customer_name: str
    total_amount: float
    outstanding_amount: float
    overdue_amount: float
    overdue_days: int
    last_transaction_date: datetime
    invoice_count: int

    class Config:
        from_attributes = True


class VendorPayment(BaseModel):
    """Vendor payment tracking"""
    vendor_id: str
    vendor_name: str
    total_purchase: float
    paid_amount: float
    outstanding_amount: float
    overdue_amount: float
    overdue_days: int
    last_purchase_date: datetime
    payment_terms: str

    class Config:
        from_attributes = True


class CashFlowItem(BaseModel):
    """Cash flow item"""
    date: datetime
    description: str
    inflow: float
    outflow: float
    balance: float
    transaction_type: str

    class Config:
        from_attributes = True


class CashFlowSummary(BaseModel):
    """Cash flow summary"""
    period_start: datetime
    period_end: datetime
    opening_balance: float
    total_inflow: float
    total_outflow: float
    closing_balance: float
    items: List[CashFlowItem]

    class Config:
        from_attributes = True


class PeriodLock(BaseModel):
    """Period locking information"""
    period: str  # Format: "YYYY-MM"
    financial_year: str  # Format: "2024-2025"
    locked: bool
    locked_by: Optional[str] = None
    locked_at: Optional[datetime] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class ReconciliationItem(BaseModel):
    """Reconciliation item"""
    account: str
    system_balance: float
    bank_balance: float
    difference: float
    status: str  # MATCHED, PENDING, DISPUTED
    remarks: Optional[str] = None

    class Config:
        from_attributes = True


class BudgetAllocation(BaseModel):
    """Budget allocation for expense category"""
    category: str
    allocated_amount: float
    spent_amount: float
    remaining_amount: float
    spent_percent: float
    status: str  # ON_TRACK, AT_RISK, EXCEEDED
    month: str

    class Config:
        from_attributes = True


class RevenueTrackerResponse(BaseModel):
    """Response for revenue tracker endpoint"""
    total_revenue: float
    total_gst: float
    net_revenue: float
    transaction_count: int
    gst_breakdown: dict
    records: List[RevenueRecord]

    class Config:
        from_attributes = True


# ============================================================================
# Router Setup
# ============================================================================

router = APIRouter(prefix="/finance", tags=["finance"])


def get_finance_repository(db=Depends(get_db)) -> FinanceRepository:
    """Dependency injection for finance repository"""
    return FinanceRepository(db)


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/revenue/tracker", response_model=RevenueTrackerResponse)
async def get_revenue_tracker(
    current_user: UserInDB = Depends(get_current_user),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    gst_type: Optional[GSTType] = Query(None),
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Get revenue tracking data with GST details
    
    - **start_date**: Filter from date (ISO format)
    - **end_date**: Filter to date (ISO format)
    - **gst_type**: Filter by GST type (CGST_SGST, IGST, EXEMPT)
    """
    return await repository.get_revenue_tracker(
        store_id=current_user.store_id,
        start_date=start_date,
        end_date=end_date,
        gst_type=gst_type,
    )


@router.get("/expenses/summary", response_model=List[ExpenseSummary])
async def get_expense_summary(
    current_user: UserInDB = Depends(get_current_user),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    category: Optional[str] = Query(None),
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Get expense summary grouped by category
    
    - **start_date**: Filter from date (ISO format)
    - **end_date**: Filter to date (ISO format)
    - **category**: Filter by specific category
    """
    return await repository.get_expense_summary(
        store_id=current_user.store_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
    )


@router.get("/pl-statement", response_model=ProfitLossStatement)
async def get_profit_loss_statement(
    current_user: UserInDB = Depends(get_current_user),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Get Profit & Loss statement for a period
    Includes revenue, expenses, and profit calculation
    """
    if not start_date:
        # Default to current financial year April-March
        today = datetime.now()
        if today.month >= 4:
            start_date = datetime(today.year, 4, 1)
            end_date = datetime(today.year + 1, 3, 31)
        else:
            start_date = datetime(today.year - 1, 4, 1)
            end_date = datetime(today.year, 3, 31)
    
    if not end_date:
        end_date = datetime.now()
    
    return await repository.get_profit_loss_statement(
        store_id=current_user.store_id,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/gst/summary", response_model=GSTSummary)
async def get_gst_summary(
    current_user: UserInDB = Depends(get_current_user),
    month: Optional[str] = Query(None),  # Format: YYYY-MM
    financial_year: Optional[str] = Query(None),  # Format: 2024-2025
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Get GST summary (CGST, SGST, IGST) for a period
    
    - **month**: Get GST for specific month (YYYY-MM)
    - **financial_year**: Get GST for financial year (YYYY-YYYY format)
    """
    return await repository.get_gst_summary(
        store_id=current_user.store_id,
        month=month,
        financial_year=financial_year,
    )


@router.get("/receivables/outstanding", response_model=List[OutstandingReceivable])
async def get_outstanding_receivables(
    current_user: UserInDB = Depends(get_current_user),
    min_days_overdue: Optional[int] = Query(0),
    sort_by: Optional[str] = Query("overdue_amount"),  # overdue_amount, customer_name, days
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Get outstanding receivables from customers
    
    - **min_days_overdue**: Filter by minimum overdue days
    - **sort_by**: Sort by (overdue_amount, customer_name, days)
    """
    return await repository.get_outstanding_receivables(
        store_id=current_user.store_id,
        min_days_overdue=min_days_overdue,
        sort_by=sort_by,
    )


@router.get("/vendors/payments", response_model=List[VendorPayment])
async def get_vendor_payments(
    current_user: UserInDB = Depends(get_current_user),
    min_days_overdue: Optional[int] = Query(0),
    sort_by: Optional[str] = Query("outstanding_amount"),  # outstanding_amount, vendor_name, days
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Get vendor payment tracking information
    
    - **min_days_overdue**: Filter by minimum overdue days
    - **sort_by**: Sort by (outstanding_amount, vendor_name, days)
    """
    return await repository.get_vendor_payments(
        store_id=current_user.store_id,
        min_days_overdue=min_days_overdue,
        sort_by=sort_by,
    )


@router.get("/cash-flow/summary", response_model=CashFlowSummary)
async def get_cash_flow_summary(
    current_user: UserInDB = Depends(get_current_user),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Get cash flow analysis for a period
    Includes inflows, outflows, and balance
    """
    if not start_date:
        start_date = datetime.now() - timedelta(days=30)
    if not end_date:
        end_date = datetime.now()
    
    return await repository.get_cash_flow_summary(
        store_id=current_user.store_id,
        start_date=start_date,
        end_date=end_date,
    )


@router.post("/period/lock", response_model=PeriodLock)
async def lock_period(
    period: str = Query(...),  # Format: YYYY-MM
    notes: Optional[str] = Query(None),
    current_user: UserInDB = Depends(get_current_user),
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Lock a financial period to prevent further modifications
    Only ADMIN, AREA_MANAGER, ACCOUNTANT can lock periods
    
    - **period**: Month to lock (YYYY-MM format)
    - **notes**: Optional locking notes/reason
    """
    # Check authorization
    allowed_roles = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'ACCOUNTANT']
    if current_user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions to lock periods")
    
    return await repository.lock_period(
        store_id=current_user.store_id,
        period=period,
        locked_by=current_user.id,
        notes=notes,
    )


@router.post("/period/unlock", response_model=PeriodLock)
async def unlock_period(
    period: str = Query(...),  # Format: YYYY-MM
    current_user: UserInDB = Depends(get_current_user),
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Unlock a previously locked financial period
    Only SUPERADMIN can unlock periods
    
    - **period**: Month to unlock (YYYY-MM format)
    """
    if current_user.role != 'SUPERADMIN':
        raise HTTPException(status_code=403, detail="Only SUPERADMIN can unlock periods")
    
    return await repository.unlock_period(
        store_id=current_user.store_id,
        period=period,
    )


@router.get("/reconciliation/status", response_model=List[ReconciliationItem])
async def get_reconciliation_status(
    current_user: UserInDB = Depends(get_current_user),
    status: Optional[str] = Query(None),  # MATCHED, PENDING, DISPUTED
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Get bank reconciliation status
    
    - **status**: Filter by status (MATCHED, PENDING, DISPUTED)
    """
    return await repository.get_reconciliation_status(
        store_id=current_user.store_id,
        status=status,
    )


@router.post("/reconciliation/match")
async def match_reconciliation_item(
    account: str = Query(...),
    system_balance: float = Query(...),
    bank_balance: float = Query(...),
    remarks: Optional[str] = Query(None),
    current_user: UserInDB = Depends(get_current_user),
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Mark a reconciliation item as matched
    """
    allowed_roles = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']
    if current_user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    return await repository.match_reconciliation_item(
        store_id=current_user.store_id,
        account=account,
        system_balance=system_balance,
        bank_balance=bank_balance,
        remarks=remarks,
    )


@router.get("/budget/tracking", response_model=List[BudgetAllocation])
async def get_budget_tracking(
    current_user: UserInDB = Depends(get_current_user),
    month: Optional[str] = Query(None),  # Format: YYYY-MM
    category: Optional[str] = Query(None),
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Get budget allocation and spending tracking
    
    - **month**: Filter by month (YYYY-MM format)
    - **category**: Filter by expense category
    """
    if not month:
        month = datetime.now().strftime("%Y-%m")
    
    return await repository.get_budget_tracking(
        store_id=current_user.store_id,
        month=month,
        category=category,
    )


@router.post("/budget/allocate", response_model=BudgetAllocation)
async def allocate_budget(
    category: str = Query(...),
    allocated_amount: float = Query(...),
    month: Optional[str] = Query(None),  # Format: YYYY-MM
    current_user: UserInDB = Depends(get_current_user),
    repository: FinanceRepository = Depends(get_finance_repository),
):
    """
    Allocate budget for an expense category
    Only ADMIN, AREA_MANAGER, ACCOUNTANT can allocate budgets
    """
    allowed_roles = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'ACCOUNTANT']
    if current_user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    if not month:
        month = datetime.now().strftime("%Y-%m")
    
    return await repository.allocate_budget(
        store_id=current_user.store_id,
        category=category,
        allocated_amount=allocated_amount,
        month=month,
    )
