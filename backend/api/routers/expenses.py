"""
IMS 2.0 - Expenses Router
=========================
Real database queries for expense and advance management
"""

import io
from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime
import uuid
from .auth import get_current_user, require_roles
from ..dependencies import get_expense_repository, get_advance_repository
from ..services.file_store import (
    get_file_store,
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
)

router = APIRouter()

# Roles permitted to approve / reject / disburse / settle expenses and advances.
# Mirrors the finance/expenses frontend route guard. SUPERADMIN auto-passes
# inside require_roles, so it is intentionally omitted from this tuple.
_APPROVAL_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")

# Roles that see ALL expenses (not just their own) in the general list, and
# that can perform accountant-side ledger entry. SUPERADMIN auto-passes.
_ADMIN_ROLES = ("SUPERADMIN", "ADMIN")
_ACCOUNTANT_ROLES = ("ADMIN", "ACCOUNTANT")


def _is_admin(current_user: dict) -> bool:
    return any(r in current_user.get("roles", []) for r in _ADMIN_ROLES)


# ============================================================================
# SCHEMAS
# ============================================================================


class ExpenseCreate(BaseModel):
    category: str
    amount: float
    description: str
    expense_date: date
    advance_id: Optional[str] = None
    payment_mode: Optional[str] = None  # CASH / UPI / CARD / BANK_TRANSFER / CHEQUE
    store_id: Optional[str] = None


class AdvanceCreate(BaseModel):
    advance_type: str
    amount: float
    purpose: str
    expected_settlement_date: Optional[date] = None


# ============================================================================
# EXPENSE ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def list_expenses(
    employee_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List expenses with optional filters.

    Ownership scope: a normal user sees ONLY the expenses they uploaded.
    ADMIN / SUPERADMIN see all (optionally filtered by store/employee).
    """
    expense_repo = get_expense_repository()

    if expense_repo is None:
        return {"expenses": [], "total": 0}

    filter_dict = {}

    if _is_admin(current_user):
        # Admins see everything; honour explicit store/employee filters if given.
        if store_id:
            filter_dict["store_id"] = store_id
        if employee_id:
            filter_dict["employee_id"] = employee_id
    else:
        # Everyone else sees only their own expenses, regardless of store.
        filter_dict["employee_id"] = current_user.get("user_id")

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


def _period_locked(year: int, month: int) -> bool:
    """True if the finance accounting period (month/year) has been locked."""
    try:
        from database.connection import get_db

        db = get_db().db
        if db is None:
            return False
        return db.get_collection("period_locks").find_one(
            {"month": int(month), "year": int(year)}
        ) is not None
    except Exception:
        return False


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_expense(
    expense: ExpenseCreate, current_user: dict = Depends(get_current_user)
):
    """Create a new expense"""
    expense_repo = get_expense_repository()
    expense_id = str(uuid.uuid4())

    d = expense.expense_date
    if _period_locked(d.year, d.month):
        raise HTTPException(
            status_code=423,
            detail=f"Accounting period {d.month:02d}/{d.year} is locked; cannot add expenses to a closed month.",
        )

    if expense_repo is not None:
        now = datetime.now().isoformat()
        expense_repo.create(
            {
                "expense_id": expense_id,
                "employee_id": current_user.get("user_id"),
                "employee_name": current_user.get("full_name"),
                "store_id": expense.store_id or current_user.get("active_store_id"),
                "category": expense.category,
                "amount": expense.amount,
                "description": expense.description,
                "expense_date": expense.expense_date.isoformat(),
                "payment_mode": expense.payment_mode,
                "advance_id": expense.advance_id,
                # Created via the "Submit expense" action -> goes straight into
                # the approval queue (PENDING). A DRAFT stage is unused by the UI.
                "status": "PENDING",
                "created_at": now,
                "submitted_at": now,
            }
        )

    return {"expense_id": expense_id, "message": "Expense submitted for approval"}


@router.post("/{expense_id}/upload-bill")
async def upload_bill(
    expense_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Upload bill/receipt for an expense.

    Persists the bytes durably in the GridFS-backed file store (Railway's
    disk is ephemeral, so a filename alone would not survive a redeploy)
    and records the resulting file_id on the expense document. Mirrors the
    handoffs upload pattern: size + mime validation, then store.put(...).
    """
    expense_repo = get_expense_repository()
    if expense_repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    existing = expense_repo.find_by_id(expense_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Expense not found")

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Read + validate before persisting anything.
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB cap",
        )
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{mime}' not allowed. Accepted: {sorted(ALLOWED_MIME_TYPES)}",
        )

    store = get_file_store()
    if store is None:
        # Fail-soft: don't 500 — tell the caller storage is unavailable.
        return {
            "message": "File storage unavailable; bill not saved",
            "filename": file.filename,
            "persisted": False,
        }

    file_id = store.put(
        content=content,
        filename=file.filename,
        mime_type=mime,
        metadata={"expense_id": expense_id},
    )
    if not file_id:
        raise HTTPException(status_code=500, detail="File store write failed")

    uploaded_at = datetime.now().isoformat()
    expense_repo.update(
        expense_id,
        {
            "bill_file_id": file_id,
            "bill_filename": file.filename,
            "bill_mime": mime,
            "bill_uploaded_at": uploaded_at,
        },
    )

    return {
        "message": "Bill uploaded",
        "filename": file.filename,
        "file_id": file_id,
        "persisted": True,
    }


@router.get("/{expense_id}/bill")
async def download_bill(
    expense_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Stream the bill/receipt attached to an expense by its stored file_id."""
    expense_repo = get_expense_repository()
    if expense_repo is None:
        raise HTTPException(status_code=503, detail="Database not available")

    existing = expense_repo.find_by_id(expense_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Expense not found")

    file_id = existing.get("bill_file_id")
    if not file_id:
        raise HTTPException(status_code=404, detail="No bill attached to this expense")

    store = get_file_store()
    if store is None:
        raise HTTPException(status_code=503, detail="File storage unavailable")

    rec = store.get(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Bill file no longer available")

    content, filename, mime = rec
    return StreamingResponse(
        io.BytesIO(content),
        media_type=mime,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post("/{expense_id}/submit")
async def submit_expense(
    expense_id: str, current_user: dict = Depends(get_current_user)
):
    """Submit expense for approval"""
    expense_repo = get_expense_repository()

    if expense_repo is not None:
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
    expense_id: str,
    current_user: dict = Depends(require_roles(*_APPROVAL_ROLES)),
):
    """Approve an expense"""
    expense_repo = get_expense_repository()

    if expense_repo is not None:
        existing = expense_repo.find_by_id(expense_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Expense not found")

        if existing.get("status") != "PENDING":
            raise HTTPException(
                status_code=400, detail="Expense is not pending approval"
            )

        ed = existing.get("expense_date", "") or ""
        try:
            d = datetime.fromisoformat(ed[:10]) if ed else None
        except Exception:
            d = None
        if d is not None and _period_locked(d.year, d.month):
            raise HTTPException(
                status_code=423,
                detail=f"Accounting period {d.month:02d}/{d.year} is locked; cannot approve.",
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
    current_user: dict = Depends(require_roles(*_APPROVAL_ROLES)),
):
    """Reject an expense"""
    expense_repo = get_expense_repository()

    if expense_repo is not None:
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


@router.post("/{expense_id}/send-to-accountant")
async def send_to_accountant(
    expense_id: str,
    current_user: dict = Depends(require_roles(*_APPROVAL_ROLES)),
):
    """Hand an APPROVED expense to the accountant for ledger entry."""
    expense_repo = get_expense_repository()

    if expense_repo is not None:
        existing = expense_repo.find_by_id(expense_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Expense not found")

        if existing.get("status") != "APPROVED":
            raise HTTPException(
                status_code=400,
                detail="Only approved expenses can be sent to the accountant",
            )

        expense_repo.update(
            expense_id,
            {
                "status": "SENT_TO_ACCOUNTANT",
                "sent_to_accountant_by": current_user.get("user_id"),
                "sent_to_accountant_at": datetime.now().isoformat(),
            },
        )

    return {"message": "Expense sent to accountant", "expense_id": expense_id}


@router.post("/{expense_id}/mark-entered")
async def mark_entered(
    expense_id: str,
    ledger_reference: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_ACCOUNTANT_ROLES)),
):
    """Accountant marks the expense as entered into the books (final state)."""
    expense_repo = get_expense_repository()

    if expense_repo is not None:
        existing = expense_repo.find_by_id(expense_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Expense not found")

        if existing.get("status") != "SENT_TO_ACCOUNTANT":
            raise HTTPException(
                status_code=400,
                detail="Expense must be sent to the accountant first",
            )

        expense_repo.update(
            expense_id,
            {
                "status": "ENTERED",
                "entered_by": current_user.get("user_id"),
                "entered_at": datetime.now().isoformat(),
                "ledger_reference": ledger_reference,
            },
        )

    return {"message": "Expense marked as entered", "expense_id": expense_id}


@router.get("/to-enter")
async def list_to_enter(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_ACCOUNTANT_ROLES)),
):
    """Accountant queue: expenses awaiting ledger entry (SENT_TO_ACCOUNTANT)."""
    expense_repo = get_expense_repository()
    if expense_repo is None:
        return {"expenses": [], "total": 0}

    filter_dict = {"status": "SENT_TO_ACCOUNTANT"}
    if store_id:
        filter_dict["store_id"] = store_id

    expenses = expense_repo.find_many(filter_dict) or []
    return {"expenses": expenses, "total": len(expenses)}


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

    if advance_repo is None:
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

    if advance_repo is not None:
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
    advance_id: str,
    current_user: dict = Depends(require_roles(*_APPROVAL_ROLES)),
):
    """Approve an advance request"""
    advance_repo = get_advance_repository()

    if advance_repo is not None:
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
    current_user: dict = Depends(require_roles(*_APPROVAL_ROLES)),
):
    """Mark advance as disbursed"""
    advance_repo = get_advance_repository()

    if advance_repo is not None:
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
    advance_id: str,
    current_user: dict = Depends(require_roles(*_APPROVAL_ROLES)),
):
    """Mark advance as settled"""
    advance_repo = get_advance_repository()

    if advance_repo is not None:
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
    current_user: dict = Depends(require_roles(*_APPROVAL_ROLES)),
):
    """Get all pending expenses and advances for approval (approvers only)"""
    expense_repo = get_expense_repository()
    advance_repo = get_advance_repository()
    active_store = store_id or current_user.get("active_store_id")

    pending_expenses = []
    pending_advances = []

    if expense_repo is not None:
        filter_dict = {"status": "PENDING"}
        if active_store:
            filter_dict["store_id"] = active_store
        pending_expenses = expense_repo.find_many(filter_dict) or []

    if advance_repo is not None:
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
