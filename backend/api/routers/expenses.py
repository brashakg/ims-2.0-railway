"""
IMS 2.0 - Expenses Router
=========================
Real database queries for expense and advance management
"""

import hashlib
import io
from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime
import uuid
from .auth import get_current_user, require_roles
from ..dependencies import get_expense_repository, get_advance_repository, get_db
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

# Roles that may review the duplicate-bill watch-list (an anti-fraud surface for
# approvers/finance). Mirrors _APPROVAL_ROLES; SUPERADMIN auto-passes.
_REVIEW_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")

# Roles allowed to edit the spend-cap configuration. SUPERADMIN auto-passes
# inside require_roles, so it is intentionally omitted here.
_CAP_EDIT_ROLES = ("ADMIN",)

# Single-document settings collection holding the per-(role, category) caps.
_CAPS_COLLECTION = "expense_category_caps"
_CAPS_DOC_ID = "expense_category_caps"

# An expense in any of these states counts against the spender's running total
# for cap purposes (i.e. everything except REJECTED, which never committed money,
# and DRAFT, which the UI does not use). Kept as a module constant so the cap
# query and any future report agree on what "spent" means.
_SPEND_STATUSES = ("PENDING", "APPROVED", "SENT_TO_ACCOUNTANT", "ENTERED", "PAID")

# Advance states that mean the employee still owes a settlement. Mirrors
# AdvanceRepository.find_outstanding; DISBURSED is the state the existing
# disburse endpoint sets, PARTIALLY_SETTLED is reserved for partial settlement.
_UNSETTLED_ADVANCE_STATUSES = ("DISBURSED", "PARTIALLY_SETTLED")


def _is_admin(current_user: dict) -> bool:
    return any(r in current_user.get("roles", []) for r in _ADMIN_ROLES)


# ============================================================================
# ANTI-FRAUD - DUPLICATE-BILL PURE HELPERS (no DB, unit-tested directly)
# ============================================================================
#
# The same physical receipt can be photographed and submitted twice (across two
# claims, or by two employees) to get reimbursed twice. We fingerprint the
# uploaded bytes with SHA-256 and look for a prior expense in the same store
# carrying the same fingerprint. A match is a SOFT flag (the file may legitimately
# recur, e.g. a monthly rent receipt) -- never a hard block.


def sha256_hex(data: bytes) -> str:
    """Stable lowercase hex SHA-256 of the given bytes.

    Pure and total: coerces None to empty bytes so a caller can never crash the
    upload path on a fingerprint. Deterministic across processes (unlike Python's
    salted hash()), which is what makes it usable as a stored dedupe key.
    """
    if data is None:
        data = b""
    return hashlib.sha256(data).hexdigest()


def find_duplicate(new_hash: str, existing: list) -> Optional[str]:
    """Return the expense_id of the first existing expense whose bill_sha256
    matches new_hash, or None.

    `existing` is a list of already-fetched expense docs (dicts). Pure so the
    matching rule stays unit-tested independently of the Mongo query. A blank /
    missing new_hash never matches (returns None) so an unreadable upload can't
    be flagged against every other blank-hash row.
    """
    if not new_hash:
        return None
    for exp in existing or []:
        if not isinstance(exp, dict):
            continue
        if exp.get("bill_sha256") == new_hash:
            return exp.get("expense_id")
    return None


# ============================================================================
# GOVERNANCE - PURE HELPERS (no DB, unit-tested directly)
# ============================================================================
#
# Caps config shape (single settings doc, all keys optional / fail-soft):
#   {
#     "caps": [
#       {"role": "SALES_STAFF", "category": "travel",
#        "daily": 500, "monthly": 5000},
#       ...
#     ],
#     "global": {"daily": 2000, "monthly": 50000}  # fallback
#   }
# A null/missing daily or monthly value means "no limit on that axis".


def _to_float(value) -> Optional[float]:
    """Best-effort numeric coercion; None/blank/garbage -> None (no limit)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_cap(category: str, role: str, caps: Optional[dict]) -> dict:
    """Resolve the applicable {daily, monthly} cap for a (role, category).

    Resolution order (first match wins):
      1. exact (role, category) entry in caps["caps"]
      2. caps["global"] fallback
      3. {} (no limit)

    Pure and total: any malformed input degrades to "no limit" rather than
    raising, so a bad config can never block a legitimate expense.
    """
    if not isinstance(caps, dict):
        return {}

    role = role or ""
    category = category or ""

    entries = caps.get("caps")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("role") == role and entry.get("category") == category:
                return {
                    "daily": _to_float(entry.get("daily")),
                    "monthly": _to_float(entry.get("monthly")),
                    "source": "role_category",
                }

    fallback = caps.get("global")
    if isinstance(fallback, dict):
        return {
            "daily": _to_float(fallback.get("daily")),
            "monthly": _to_float(fallback.get("monthly")),
            "source": "global",
        }

    return {}


def check_cap(
    category: str,
    role: str,
    amount: float,
    spent_today: float,
    spent_month: float,
    caps: Optional[dict],
):
    """Decide whether a new expense of `amount` is within the spender's caps.

    Returns (ok: bool, reason: str). `reason` is empty when ok is True and a
    human-readable message naming which cap (daily/monthly) was hit and how
    much headroom remained when ok is False.

    Daily is checked before monthly. Exactly-at-cap is allowed (<= cap passes);
    only strictly exceeding the cap is rejected. A None cap on either axis means
    that axis is unlimited.
    """
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        amt = 0.0
    if amt <= 0:
        # Non-positive amounts are validated elsewhere; never block on caps.
        return True, ""

    spent_today = _to_float(spent_today) or 0.0
    spent_month = _to_float(spent_month) or 0.0

    resolved = resolve_cap(category, role, caps)
    daily_cap = resolved.get("daily")
    monthly_cap = resolved.get("monthly")

    if daily_cap is not None and spent_today + amt > daily_cap:
        remaining = max(daily_cap - spent_today, 0.0)
        return False, (
            f"Daily {category} cap of Rs {daily_cap:.0f} for your role would be "
            f"exceeded. Already spent Rs {spent_today:.0f} today; "
            f"Rs {remaining:.0f} remaining."
        )

    if monthly_cap is not None and spent_month + amt > monthly_cap:
        remaining = max(monthly_cap - spent_month, 0.0)
        return False, (
            f"Monthly {category} cap of Rs {monthly_cap:.0f} for your role would "
            f"be exceeded. Already spent Rs {spent_month:.0f} this month; "
            f"Rs {remaining:.0f} remaining."
        )

    return True, ""


def _check_caps_for_roles(
    roles,
    category: str,
    amount: float,
    spent_today: float,
    spent_month: float,
    caps: Optional[dict],
):
    """Apply check_cap across every role the user holds; most restrictive wins.

    A user can hold several roles; the first one whose cap is exceeded blocks
    the expense. If a user has no roles, the global fallback (role "") still
    applies. Pure wrapper over check_cap so the per-role math stays unit-tested.
    """
    role_list = list(roles) if roles else [""]
    seen = set()
    for role in role_list:
        if role in seen:
            continue
        seen.add(role)
        ok, reason = check_cap(category, role, amount, spent_today, spent_month, caps)
        if not ok:
            return False, reason
    return True, ""


def has_blocking_advance(outstanding_advances: list, linked_advance_id) -> bool:
    """True if the employee must settle an advance before a NEW claim.

    `outstanding_advances` is the list of the employee's advances in an
    unsettled state. The claim is allowed (returns False) when there are none,
    OR when the claim links one of those outstanding advances (settlement
    flow). Otherwise it is blocked (returns True).
    """
    if not outstanding_advances:
        return False
    if linked_advance_id:
        outstanding_ids = {
            a.get("advance_id") for a in outstanding_advances if isinstance(a, dict)
        }
        if linked_advance_id in outstanding_ids:
            return False
    return True


def aging_bucket(days_pending: int) -> str:
    """Bucket a day-count into a reimbursement-aging band.

    0-7 -> "0-7", 8-15 -> "8-15", 16+ -> "15+". Negative clamps to "0-7".
    """
    try:
        d = int(days_pending)
    except (TypeError, ValueError):
        d = 0
    if d <= 7:
        return "0-7"
    if d <= 15:
        return "8-15"
    return "15+"


def _days_pending(reference_iso, now: datetime) -> int:
    """Whole days between an ISO timestamp/date string and `now` (>= 0)."""
    if not reference_iso:
        return 0
    try:
        ref = datetime.fromisoformat(str(reference_iso)[:19])
    except ValueError:
        try:
            ref = datetime.fromisoformat(str(reference_iso)[:10])
        except ValueError:
            return 0
    delta = (now - ref).days
    return delta if delta > 0 else 0


def compute_aging(expenses: list, now: datetime) -> dict:
    """Bucket APPROVED / SENT_TO_ACCOUNTANT expenses by how long they've waited.

    Pure: takes already-fetched expense docs and a clock. Each row is aged from
    the most relevant timestamp (sent-to-accountant, else approved, else
    submitted/created). Returns per-bucket counts + totals and the row list so
    the endpoint can serialise directly.
    """
    buckets = {
        "0-7": {"count": 0, "amount": 0.0},
        "8-15": {"count": 0, "amount": 0.0},
        "15+": {"count": 0, "amount": 0.0},
    }
    rows = []
    total_amount = 0.0

    for exp in expenses or []:
        if not isinstance(exp, dict):
            continue
        status = (exp.get("status") or "").upper()
        if status not in ("APPROVED", "SENT_TO_ACCOUNTANT"):
            continue
        reference = (
            exp.get("sent_to_accountant_at")
            or exp.get("approved_at")
            or exp.get("submitted_at")
            or exp.get("created_at")
        )
        days = _days_pending(reference, now)
        bucket = aging_bucket(days)
        amount = _to_float(exp.get("amount")) or 0.0

        buckets[bucket]["count"] += 1
        buckets[bucket]["amount"] += amount
        total_amount += amount

        rows.append(
            {
                "expense_id": exp.get("expense_id"),
                "employee_id": exp.get("employee_id"),
                "employee_name": exp.get("employee_name"),
                "store_id": exp.get("store_id"),
                "category": exp.get("category"),
                "amount": amount,
                "status": status,
                "since": reference,
                "days_pending": days,
                "bucket": bucket,
            }
        )

    rows.sort(key=lambda r: r["days_pending"], reverse=True)
    return {
        "buckets": buckets,
        "rows": rows,
        "total_count": len(rows),
        "total_amount": total_amount,
    }


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


class CapEntry(BaseModel):
    role: str
    category: str
    daily: Optional[float] = None
    monthly: Optional[float] = None


class GlobalCap(BaseModel):
    daily: Optional[float] = None
    monthly: Optional[float] = None


class CapsUpdate(BaseModel):
    caps: list[CapEntry] = []
    global_cap: Optional[GlobalCap] = None


# ============================================================================
# GOVERNANCE - DB HELPERS
# ============================================================================


def _caps_collection():
    """Return the expense-caps collection, or None when DB is unavailable.

    Uses the dependency get_db() wrapper (works against both the live DB and
    the seeded mock) and tolerates either get_collection() or item access.
    """
    db = get_db()
    if db is None or not getattr(db, "is_connected", False):
        return None
    getter = getattr(db, "get_collection", None)
    if callable(getter):
        return getter(_CAPS_COLLECTION)
    try:
        return db[_CAPS_COLLECTION]
    except Exception:
        return None


def _load_caps() -> dict:
    """Load the caps settings doc as a plain dict; {} when absent/unavailable.

    Always returns a {"caps": [...], "global": {...}} shape so callers and the
    pure resolver never have to special-case None.
    """
    coll = _caps_collection()
    if coll is None:
        return {"caps": [], "global": {}}
    try:
        doc = coll.find_one({"_id": _CAPS_DOC_ID})
    except Exception:
        doc = None
    if not isinstance(doc, dict):
        return {"caps": [], "global": {}}
    return {
        "caps": doc.get("caps") or [],
        "global": doc.get("global") or {},
    }


def _spent_for_category(employee_id: str, category: str, on_date: date):
    """(spent_today, spent_month) for one employee+category in committed states.

    Sums amounts of the employee's expenses in _SPEND_STATUSES whose
    expense_date falls on `on_date` (today) and within `on_date`'s month.
    Fail-soft: returns (0.0, 0.0) when the repo is unavailable or errors, so a
    DB hiccup never blocks an expense on a phantom cap.
    """
    repo = get_expense_repository()
    if repo is None or not employee_id:
        return 0.0, 0.0

    month_start = on_date.replace(day=1)
    base_filter = {
        "employee_id": employee_id,
        "category": category,
        "status": {"$in": list(_SPEND_STATUSES)},
    }

    spent_today = 0.0
    spent_month = 0.0
    try:
        # Pull the month's rows once, then split today out client-side. Cheaper
        # than two near-identical queries and keeps the date math in one place.
        month_filter = dict(base_filter)
        month_filter["expense_date"] = {
            "$gte": month_start.isoformat(),
            "$lte": on_date.isoformat(),
        }
        rows = repo.find_many(month_filter, limit=10000) or []
        today_iso = on_date.isoformat()
        for row in rows:
            amt = _to_float(row.get("amount")) or 0.0
            spent_month += amt
            if (row.get("expense_date") or "")[:10] == today_iso:
                spent_today += amt
    except Exception:
        return 0.0, 0.0

    return spent_today, spent_month


def _find_duplicate_bill(
    bill_sha256: str, this_expense: Optional[dict], this_expense_id: str
) -> Optional[str]:
    """expense_id of a prior expense in the same store sharing this fingerprint.

    Scope is the store of the expense being uploaded against (a receipt claimed
    twice within one store is the realistic fraud). The current expense is
    excluded so re-uploading the same file to the same row doesn't self-flag.
    Fail-soft: any DB error -> None (treat as not-a-duplicate; never block the
    upload). Matching itself is delegated to the pure find_duplicate helper.
    """
    if not bill_sha256:
        return None
    repo = get_expense_repository()
    if repo is None:
        return None
    store_id = (this_expense or {}).get("store_id")
    query = {"bill_sha256": bill_sha256}
    if store_id:
        query["store_id"] = store_id
    try:
        candidates = repo.find_many(query, limit=50) or []
    except Exception:
        return None
    others = [
        c
        for c in candidates
        if isinstance(c, dict) and c.get("expense_id") != this_expense_id
    ]
    return find_duplicate(bill_sha256, others)


def _outstanding_advances(employee_id: str) -> list:
    """The employee's advances still in an unsettled state (fail-soft -> [])."""
    repo = get_advance_repository()
    if repo is None or not employee_id:
        return []
    try:
        return (
            repo.find_many(
                {
                    "employee_id": employee_id,
                    "status": {"$in": list(_UNSETTLED_ADVANCE_STATUSES)},
                }
            )
            or []
        )
    except Exception:
        return []


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
        return (
            db.get_collection("period_locks").find_one(
                {"month": int(month), "year": int(year)}
            )
            is not None
        )
    except Exception:
        return False


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_expense(
    expense: ExpenseCreate, current_user: dict = Depends(get_current_user)
):
    """Create a new expense.

    Governance gates (in order, all fail-soft so a DB outage never wrongly
    blocks a claim):
      1. Accounting-period lock (cannot post into a closed month).
      2. Outstanding-advance block: an employee with an unsettled advance must
         settle it (or link it on this claim) before filing a new expense.
      3. Per-(role, category) spend cap: reject if today's / this month's
         running total plus this amount would exceed the configured cap.
    """
    expense_repo = get_expense_repository()
    expense_id = str(uuid.uuid4())

    d = expense.expense_date
    if _period_locked(d.year, d.month):
        raise HTTPException(
            status_code=423,
            detail=f"Accounting period {d.month:02d}/{d.year} is locked; cannot add expenses to a closed month.",
        )

    employee_id = current_user.get("user_id")

    # (2) Outstanding-advance block.
    outstanding = _outstanding_advances(employee_id)
    if has_blocking_advance(outstanding, expense.advance_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "You have an outstanding (unsettled) advance. Settle it first, "
                "or link it to this expense (advance_id) to record a settlement."
            ),
        )

    # (3) Per-role/category spend cap. Admins/Superadmin are exempt (they set
    # the caps and carry unlimited authority). For everyone else we apply the
    # MOST RESTRICTIVE cap across the roles they hold.
    if not _is_admin(current_user):
        caps = _load_caps()
        spent_today, spent_month = _spent_for_category(employee_id, expense.category, d)
        ok, reason = _check_caps_for_roles(
            current_user.get("roles", []),
            expense.category,
            expense.amount,
            spent_today,
            spent_month,
            caps,
        )
        if not ok:
            raise HTTPException(status_code=400, detail=reason)

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

    # Anti-fraud: fingerprint the bytes and look for the same receipt already
    # attached to ANOTHER expense in the same store (same physical bill claimed
    # twice / across two employees). A match is flagged for an approver to
    # scrutinise -- never a hard block, since some receipts legitimately recur.
    bill_sha256 = sha256_hex(content)
    duplicate_of = _find_duplicate_bill(bill_sha256, existing, expense_id)

    store = get_file_store()
    if store is None:
        # Fail-soft: don't 500 — tell the caller storage is unavailable. Still
        # persist the fingerprint so a later re-upload can be matched.
        try:
            expense_repo.update(
                expense_id,
                {
                    "bill_sha256": bill_sha256,
                    "duplicate_bill": bool(duplicate_of),
                    "duplicate_of": duplicate_of,
                },
            )
        except Exception:
            pass
        return {
            "message": "File storage unavailable; bill not saved",
            "filename": file.filename,
            "persisted": False,
            "bill_sha256": bill_sha256,
            "duplicate_bill": bool(duplicate_of),
            "duplicate_of": duplicate_of,
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
            "bill_sha256": bill_sha256,
            "duplicate_bill": bool(duplicate_of),
            "duplicate_of": duplicate_of,
        },
    )

    return {
        "message": "Bill uploaded",
        "filename": file.filename,
        "file_id": file_id,
        "persisted": True,
        "bill_sha256": bill_sha256,
        "duplicate_bill": bool(duplicate_of),
        "duplicate_of": duplicate_of,
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
# GOVERNANCE ENDPOINTS
# ============================================================================


@router.get("/caps")
async def get_expense_caps(current_user: dict = Depends(get_current_user)):
    """Return the per-(role, category) spend caps + global fallback.

    Readable by any authenticated user so the expense form can show the
    spender their remaining headroom. Fail-soft: empty config when no doc /
    no DB.
    """
    caps = _load_caps()
    return {"caps": caps.get("caps", []), "global": caps.get("global", {})}


@router.put("/caps")
async def update_expense_caps(
    payload: CapsUpdate,
    current_user: dict = Depends(require_roles(*_CAP_EDIT_ROLES)),
):
    """Replace the spend-cap configuration (ADMIN / SUPERADMIN only).

    Stores a single settings doc keyed by _CAPS_DOC_ID. The whole config is
    replaced (upsert) so removing a row from the UI removes the cap.
    """
    coll = _caps_collection()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")

    doc = {
        "_id": _CAPS_DOC_ID,
        "caps": [entry.model_dump() for entry in payload.caps],
        "global": (payload.global_cap.model_dump() if payload.global_cap else {}),
        "updated_by": current_user.get("user_id"),
        "updated_at": datetime.now().isoformat(),
    }
    try:
        coll.update_one({"_id": _CAPS_DOC_ID}, {"$set": doc}, upsert=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to save caps") from exc

    return {
        "message": "Expense caps updated",
        "caps": doc["caps"],
        "global": doc["global"],
    }


@router.get("/aging")
async def get_reimbursement_aging(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_ACCOUNTANT_ROLES)),
):
    """Reimbursement aging: APPROVED / SENT_TO_ACCOUNTANT not yet entered/paid.

    Buckets each waiting expense by days-pending (0-7 / 8-15 / 15+). Admin /
    accountant gated. Fail-soft: empty buckets when no DB.
    """
    expense_repo = get_expense_repository()
    empty = {
        "buckets": {
            "0-7": {"count": 0, "amount": 0.0},
            "8-15": {"count": 0, "amount": 0.0},
            "15+": {"count": 0, "amount": 0.0},
        },
        "rows": [],
        "total_count": 0,
        "total_amount": 0.0,
    }
    if expense_repo is None:
        return empty

    filter_dict = {"status": {"$in": ["APPROVED", "SENT_TO_ACCOUNTANT"]}}
    if store_id:
        filter_dict["store_id"] = store_id

    try:
        expenses = expense_repo.find_many(filter_dict, limit=10000) or []
    except Exception:
        return empty

    return compute_aging(expenses, datetime.now())


@router.get("/duplicate-bills")
async def list_duplicate_bills(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REVIEW_ROLES)),
):
    """Anti-fraud watch-list: expenses whose bill matched an earlier receipt.

    Returns rows flagged duplicate_bill=true (same SHA-256 as a prior expense in
    the same store) so an approver can scrutinise possible double-claims. Each
    row carries duplicate_of (the expense_id it collided with). Approver/finance
    gated; fail-soft to an empty list when no DB.
    """
    expense_repo = get_expense_repository()
    if expense_repo is None:
        return {"expenses": [], "total": 0}

    filter_dict = {"duplicate_bill": True}
    if store_id:
        filter_dict["store_id"] = store_id

    try:
        expenses = expense_repo.find_many(filter_dict, limit=10000) or []
    except Exception:
        return {"expenses": [], "total": 0}

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
