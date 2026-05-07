"""
IMS 2.0 — Walkouts Router (Module i, Phase 1)
==============================================
Lifts the Pune-incentive Walkouts Log out of Excel into IMS 2.0.

Phase 1 surface:
  POST /api/v1/walkouts             create one walkout (full 30-field shape)
  GET  /api/v1/walkouts/{id}        fetch one

Phases 2-5 add list/edit/delete, follow-ups, dashboard, and the
conversion-feed contract Module (ii) consumes. See
docs/PUNE_INCENTIVE_BUILD_PLAN.md for the full programme.

Auth: every endpoint requires a logged-in user. POST is allowed for
SUPERADMIN / ADMIN / STORE_MANAGER / SALES_STAFF / CASHIER. Other
roles get 403 (deliberate — workshop staff don't log walkouts).

Side effects on POST:
  1. If the supplied mobile isn't in `customers`, a skeleton customer
     row is auto-created with `source: "walkout"`. The new
     `customer_id` is linked back onto the walkout.
  2. An audit-log row is written (`action: "walkout.create"`).
"""
from datetime import datetime, date as date_type
from enum import Enum
from typing import Any, Dict, List, Optional
import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from .auth import get_current_user
from ..dependencies import (
    get_audit_repository,
    get_customer_repository,
    get_db,
    get_user_repository,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# ENUMS — must match the Excel source column values exactly
# ============================================================================


class AgeGroup(str, Enum):
    UNDER_15 = "<15"
    G_15_25 = "15-25"
    G_26_35 = "26-35"
    G_36_45 = "36-45"
    G_46_55 = "46-55"
    G_56_65 = "56-65"
    OVER_65 = "65+"


class Gender(str, Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"


class ProductCategory(str, Enum):
    FRAME = "FRAME"
    SUNGLASS = "SUNGLASS"
    WATCH = "WATCH"
    CLOCK = "CLOCK"
    LENS = "LENS"
    CONTACT_LENS = "CONTACT LENS"
    ACCESSORY = "ACCESSORY"
    OTHER = "OTHER"


class YesNo(str, Enum):
    YES = "YES"
    NO = "NO"


class PriceRange(str, Enum):
    UNDER_1K = "<1000"
    R_1K_2K = "1000-2000"
    R_2K_3K = "2000-3000"
    R_3K_5K = "3000-5000"
    R_5K_10K = "5000-10000"
    R_10K_20K = "10000-20000"
    R_20K_50K = "20000-50000"
    OVER_50K = "50000+"


class WalkoutReason(str, Enum):
    BUDGET_PRICE = "BUDGET/PRICE"
    COLLECTION = "COLLECTION"
    COLOR = "COLOR"
    BRAND = "BRAND"
    ENQUIRY_ONLY = "ENQUIRY ONLY"
    STAFF_BEHAVIOUR = "STAFF BEHAVIOUR"
    NOT_AVAILABLE = "NOT AVAILABLE"
    STYLE_DESIGN = "STYLE/DESIGN"
    FIT_SIZE = "FIT/SIZE"
    OTHER = "OTHER"


class PurchasePlan(str, Enum):
    NEXT_DAY = "NEXT DAY"
    P_1_7 = "1-7 DAYS"
    P_8_15 = "8-15 DAYS"
    P_16_30 = "16-30 DAYS"
    AFTER_MONTH = "AFTER A MONTH"
    UNDECIDED = "UNDECIDED"


# ============================================================================
# REQUEST / RESPONSE MODELS
# ============================================================================


_MOBILE_RE = re.compile(r"^\d{10}$")


class CreateWalkoutRequest(BaseModel):
    """Phase 1 intake payload. See doc §"Schema — walkouts collection"."""

    customer_name: str = Field(..., min_length=1, max_length=120)
    mobile: str = Field(..., description="10-digit Indian mobile")
    age_group: AgeGroup
    gender: Gender
    product_interested: ProductCategory
    has_prescription: YesNo
    displayed_price_range: PriceRange
    required_price_range: PriceRange
    primary_walkout_reason: WalkoutReason
    secondary_walkout_reason: Optional[WalkoutReason] = None
    brand_interest: str = ""
    competitor_mentioned: str = ""
    purchase_planned_in: PurchasePlan
    sales_person_id: str = Field(..., min_length=1)
    action_remarks: str = ""
    date: Optional[date_type] = None  # defaults to today

    @field_validator("mobile")
    @classmethod
    def _validate_mobile(cls, v: str) -> str:
        v = (v or "").strip()
        if not _MOBILE_RE.match(v):
            raise ValueError("Mobile must be exactly 10 digits")
        return v


class WalkoutResponse(BaseModel):
    """What the API returns. Just the doc round-trip; the doc shape is
    fully open since Phase 1 doesn't constrain it."""

    walkout_id: str
    store_id: str
    customer_id: Optional[str]
    customer_name: str
    mobile: str
    sales_person_id: str
    sales_person_name: Optional[str]
    date: Optional[str]
    date_str: str
    created_at: str
    # Phase 1 returns the full doc; downstream phases narrow this.
    # FastAPI will pass through any extra fields we didn't list.

    class Config:
        extra = "allow"


class UpdateWalkoutRequest(BaseModel):
    """Phase 2 PATCH payload. All fields optional — only provided keys
    move. store_id, walkout_id, sales_person_id (for non-managers),
    customer_id, followups, result, soft-delete fields, and timestamps
    are NOT editable through this endpoint (some have dedicated
    endpoints in Phase 3+)."""

    customer_name: Optional[str] = Field(None, min_length=1, max_length=120)
    mobile: Optional[str] = None
    age_group: Optional[AgeGroup] = None
    gender: Optional[Gender] = None
    product_interested: Optional[ProductCategory] = None
    has_prescription: Optional[YesNo] = None
    displayed_price_range: Optional[PriceRange] = None
    required_price_range: Optional[PriceRange] = None
    primary_walkout_reason: Optional[WalkoutReason] = None
    secondary_walkout_reason: Optional[WalkoutReason] = None
    brand_interest: Optional[str] = None
    competitor_mentioned: Optional[str] = None
    purchase_planned_in: Optional[PurchasePlan] = None
    sales_person_id: Optional[str] = None  # SUPERADMIN/ADMIN/STORE_MANAGER only
    action_remarks: Optional[str] = None

    @field_validator("mobile")
    @classmethod
    def _validate_mobile(cls, v):
        if v is None:
            return v
        v = (v or "").strip()
        if not _MOBILE_RE.match(v):
            raise ValueError("Mobile must be exactly 10 digits")
        return v


class DeleteWalkoutRequest(BaseModel):
    """Soft-delete payload."""

    reason: str = Field(..., min_length=1, max_length=400)


# ============================================================================
# RBAC
# ============================================================================
_ALLOWED_CREATE_ROLES = {
    "SUPERADMIN", "ADMIN", "STORE_MANAGER",
    "SALES_STAFF", "SALES_CASHIER", "CASHIER",
}
# Roles that can edit any walkout in their store (or any store, for
# the global ones). Sales staff can only edit walkouts they own.
_GLOBAL_EDIT_ROLES = {"SUPERADMIN", "ADMIN"}
_STORE_EDIT_ROLES = {"STORE_MANAGER", "AREA_MANAGER"}
# Roles that can soft-delete a walkout — narrower than edit.
_DELETE_ROLES = {"SUPERADMIN", "STORE_MANAGER"}
# Roles that can attribute a walkout to a salesperson other than
# themselves (per UX spec: managers / admin / accountant + above can
# pick from a dropdown; everyone below is auto-locked to self).
_REATTRIBUTE_ROLES = (
    _GLOBAL_EDIT_ROLES | _STORE_EDIT_ROLES | {"ACCOUNTANT"}
)


def _user_role_set(current_user: dict) -> set:
    return set(current_user.get("roles", []) or [])


def _user_store_id(current_user: dict) -> Optional[str]:
    return (
        current_user.get("active_store_id")
        or (current_user.get("store_ids") or [None])[0]
    )


def _check_create_permission(current_user: dict) -> None:
    if not (_user_role_set(current_user) & _ALLOWED_CREATE_ROLES):
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to log walkouts",
        )


def _check_edit_permission(walkout: Dict, current_user: dict) -> None:
    """Edit allowed when:
      - SUPERADMIN/ADMIN: any walkout
      - STORE_MANAGER: walkouts in their store
      - SALES_STAFF/SALES_CASHIER/CASHIER: only walkouts they own
        (sales_person_id == current_user.user_id)
    """
    roles = _user_role_set(current_user)
    if roles & _GLOBAL_EDIT_ROLES:
        return
    user_store = _user_store_id(current_user)
    if (roles & _STORE_EDIT_ROLES) and walkout.get("store_id") == user_store:
        return
    # Sales staff: own-only
    if roles & _ALLOWED_CREATE_ROLES:
        if walkout.get("sales_person_id") == current_user.get("user_id"):
            return
    raise HTTPException(
        status_code=403,
        detail="You don't have permission to edit this walkout",
    )


def _check_delete_permission(walkout: Dict, current_user: dict) -> None:
    roles = _user_role_set(current_user)
    if roles & {"SUPERADMIN"}:
        return
    if (roles & _STORE_EDIT_ROLES) and walkout.get("store_id") == _user_store_id(current_user):
        return
    raise HTTPException(
        status_code=403,
        detail="Only superadmin or the walkout's store manager can delete a walkout",
    )


# ============================================================================
# HELPERS — repository accessors with fail-soft
# ============================================================================


def _walkout_collection():
    """Get the raw walkouts collection (avoids the repo abstraction for
    fail-soft reads when the DB is unavailable)."""
    db = get_db()
    if db is None or not getattr(db, "is_connected", True):
        return None
    try:
        return db.get_collection("walkouts")
    except Exception:
        return None


def _walkout_repo():
    """Lazy-init a WalkoutRepository wrapping the collection. Returns
    None if the DB is unavailable so the router can return a clean 503
    instead of crashing."""
    col = _walkout_collection()
    if col is None:
        return None
    # Local import keeps the router importable even if repos break.
    from database.repositories.walkout_repository import WalkoutRepository
    return WalkoutRepository(col)


def _resolve_sales_person_name(sales_person_id: str) -> Optional[str]:
    """Look up the user's display name. Returns None if not found."""
    try:
        user_repo = get_user_repository()
        if user_repo is None:
            return None
        user = user_repo.find_by_id(sales_person_id) or user_repo.find_one(
            {"user_id": sales_person_id}
        )
        if not user:
            return None
        return (
            user.get("name")
            or user.get("full_name")
            or user.get("username")
            or sales_person_id
        )
    except Exception:
        return None


def _ensure_customer(
    mobile: str,
    customer_name: str,
    store_id: str,
    current_user: dict,
) -> Optional[str]:
    """
    Phase 1 customer auto-create.

    If the mobile matches an existing customer, return that customer's
    id. Otherwise create a skeleton customer row tagged
    `source="walkout"` and return the new id. Both branches are
    audit-logged via the caller (separate audit row for the
    auto-create vs the walkout-create itself).

    Returns None if the customer repo is unreachable; caller decides
    whether to proceed with `customer_id=None` or 503.
    """
    customer_repo = get_customer_repository()
    if customer_repo is None:
        return None

    existing = customer_repo.find_by_mobile(mobile)
    if existing:
        return existing.get("customer_id")

    # Auto-create skeleton
    new_id = f"cust-{uuid.uuid4().hex[:8]}"
    skeleton = {
        "customer_id": new_id,
        "name": customer_name,
        "mobile": mobile,
        "primary_store_id": store_id,
        "store_ids": [store_id],
        "source": "walkout",
        "created_via": "walkout_intake",
        "customer_type": "B2C",
        "loyalty_points": 0,
        "store_credit": 0.0,
        "patients": [],
    }
    try:
        customer_repo.create(skeleton)
    except Exception as e:
        logger.warning(f"[WALKOUT] customer auto-create failed: {e}")
        return None

    # Audit-log the side-effect creation
    audit_repo = get_audit_repository()
    if audit_repo is not None:
        try:
            audit_repo.create({
                "log_id": uuid.uuid4().hex,
                "timestamp": datetime.now(),
                "user_id": current_user.get("user_id"),
                "action": "customer.create",
                "entity_type": "customer",
                "entity_id": new_id,
                "store_id": store_id,
                "severity": "info",
                "detail": {"via_walkout": True, "mobile": mobile},
            })
        except Exception:
            pass

    return new_id


def _audit_walkout_create(
    walkout_id: str,
    store_id: str,
    mobile: str,
    sales_person_name: Optional[str],
    current_user: dict,
) -> None:
    """Best-effort audit row. Never raises."""
    audit_repo = get_audit_repository()
    if audit_repo is None:
        return
    try:
        audit_repo.create({
            "log_id": uuid.uuid4().hex,
            "timestamp": datetime.now(),
            "user_id": current_user.get("user_id"),
            "action": "walkout.create",
            "entity_type": "walkout",
            "entity_id": walkout_id,
            "store_id": store_id,
            "severity": "info",
            "detail": {
                "mobile": mobile,
                "sales_person": sales_person_name or "",
            },
        })
    except Exception as e:
        logger.warning(f"[WALKOUT] audit-log failed (non-fatal): {e}")


def _audit_walkout_action(
    *,
    action: str,
    walkout_id: str,
    store_id: str,
    current_user: dict,
    detail: Dict,
) -> None:
    """Generic audit emitter for Phase 2+ writes (update / delete)."""
    audit_repo = get_audit_repository()
    if audit_repo is None:
        return
    try:
        audit_repo.create({
            "log_id": uuid.uuid4().hex,
            "timestamp": datetime.now(),
            "user_id": current_user.get("user_id"),
            "action": action,
            "entity_type": "walkout",
            "entity_id": walkout_id,
            "store_id": store_id,
            "severity": "info",
            "detail": detail,
        })
    except Exception as e:
        logger.warning(f"[WALKOUT] audit-log failed (non-fatal): {e}")


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.post("", status_code=201)
@router.post("/", status_code=201, include_in_schema=False)
async def create_walkout(
    payload: CreateWalkoutRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Log a walkout. Server fills walkout_id, store_id (from session),
    sales_person_name (resolved from user repo), and links/auto-creates
    a customer row from `mobile`. Writes a `walkout.create` audit row.
    """
    _check_create_permission(current_user)

    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    store_id = (
        current_user.get("active_store_id")
        or (current_user.get("store_ids") or [None])[0]
        or ""
    )
    if not store_id:
        raise HTTPException(
            status_code=400,
            detail="No active store on this session",
        )

    # Salesperson attribution rule (per UX spec):
    # SUPERADMIN / ADMIN / AREA_MANAGER / STORE_MANAGER / ACCOUNTANT may
    # log a walkout on behalf of any sales person. Lower-tier roles
    # (SALES_STAFF / SALES_CASHIER / CASHIER / OPTOMETRIST) are forced
    # to themselves regardless of what the client posted.
    if not (_user_role_set(current_user) & _REATTRIBUTE_ROLES):
        payload.sales_person_id = current_user.get("user_id") or payload.sales_person_id

    sales_person_name = _resolve_sales_person_name(payload.sales_person_id)

    target_date = payload.date or datetime.now().date()
    date_str = target_date.isoformat()

    # Customer link / auto-create. None is acceptable — the row gets
    # `customer_id: None` and Phase 2 backfill can patch later.
    customer_id = _ensure_customer(
        payload.mobile, payload.customer_name, store_id, current_user
    )

    doc = {
        "store_id": store_id,
        "date": datetime.combine(target_date, datetime.min.time()),
        "date_str": date_str,
        "customer_id": customer_id,
        "customer_name": payload.customer_name,
        "mobile": payload.mobile,
        "age_group": payload.age_group.value,
        "gender": payload.gender.value,
        "product_interested": payload.product_interested.value,
        "has_prescription": payload.has_prescription.value,
        "displayed_price_range": payload.displayed_price_range.value,
        "required_price_range": payload.required_price_range.value,
        "primary_walkout_reason": payload.primary_walkout_reason.value,
        "secondary_walkout_reason": (
            payload.secondary_walkout_reason.value
            if payload.secondary_walkout_reason else None
        ),
        "brand_interest": payload.brand_interest,
        "competitor_mentioned": payload.competitor_mentioned,
        "purchase_planned_in": payload.purchase_planned_in.value,
        "sales_person_id": payload.sales_person_id,
        "sales_person_name": sales_person_name,
        "action_remarks": payload.action_remarks,
        "created_by": current_user.get("user_id"),
        "updated_by": current_user.get("user_id"),
    }

    saved = repo.create_walkout(doc)
    if saved is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to save walkout (DB write error)",
        )

    _audit_walkout_create(
        saved["walkout_id"],
        store_id,
        payload.mobile,
        sales_person_name,
        current_user,
    )

    # JSON-serialize the datetime fields
    return _serialize_walkout(saved)


@router.get("")
@router.get("/", include_in_schema=False)
async def list_walkouts(
    current_user: dict = Depends(get_current_user),
    date_from: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD), inclusive"),
    date_to: Optional[str] = Query(None, description="ISO date (YYYY-MM-DD), inclusive"),
    sales_person_id: Optional[str] = Query(None),
    primary_walkout_reason: Optional[str] = Query(None),
    result: Optional[str] = Query(
        None, description="DUE / NEGATIVE / CONVERTED / 'none' for unset"
    ),
    store_id: Optional[str] = Query(
        None,
        description="Cross-store filter — only honored for SUPERADMIN/ADMIN; "
                    "everyone else is scoped to their active store.",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List walkouts with filters + pagination.

    Store scoping:
      - SUPERADMIN / ADMIN: may pass `store_id` to filter; default = all stores
      - Everyone else: forced to their active_store_id (the `store_id`
        query parameter is ignored).
    """
    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    roles = _user_role_set(current_user)
    if roles & _GLOBAL_EDIT_ROLES:
        effective_store = store_id or None  # global view
    else:
        effective_store = _user_store_id(current_user)
        if not effective_store:
            raise HTTPException(
                status_code=400,
                detail="No active store on this session",
            )

    items = repo.list_walkouts(
        store_id=effective_store,
        date_from=date_from,
        date_to=date_to,
        sales_person_id=sales_person_id,
        primary_walkout_reason=primary_walkout_reason,
        result=result,
        skip=skip,
        limit=limit,
    )
    total = repo.count_walkouts(
        store_id=effective_store,
        date_from=date_from,
        date_to=date_to,
        sales_person_id=sales_person_id,
        primary_walkout_reason=primary_walkout_reason,
        result=result,
    )

    return {
        "items": [_serialize_walkout(w) for w in items],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get("/{walkout_id}")
async def get_walkout(
    walkout_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Fetch one walkout by id. Phase 1 doesn't enforce store-scoping
    on reads (any logged-in user can fetch by id) — Phase 2 will tighten
    this for cross-store privacy."""
    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    walkout = repo.find_by_walkout_id(walkout_id)
    if not walkout:
        raise HTTPException(status_code=404, detail="Walkout not found")

    return _serialize_walkout(walkout)


@router.patch("/{walkout_id}")
async def update_walkout(
    walkout_id: str,
    payload: UpdateWalkoutRequest,
    current_user: dict = Depends(get_current_user),
):
    """Edit a walkout. Server enforces RBAC, computes the field-level
    diff (old → new) for every changed key, and writes a single
    `walkout.update` audit row with the diff in `detail.changes`.

    sales_person_id is gated separately — only SUPERADMIN/ADMIN/STORE_MANAGER
    can re-attribute. Sales staff attempting to change it get 403.

    Cross-store edits are blocked for STORE_MANAGER and below; the
    walkout's store_id is never editable.
    """
    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    existing = repo.find_by_walkout_id(walkout_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Walkout not found")

    _check_edit_permission(existing, current_user)

    # Build the diff — only include keys the caller actually sent.
    incoming = payload.model_dump(exclude_unset=True)

    # Re-attribution gate
    if "sales_person_id" in incoming:
        roles = _user_role_set(current_user)
        if not (roles & _REATTRIBUTE_ROLES):
            raise HTTPException(
                status_code=403,
                detail="You don't have permission to re-attribute walkouts",
            )

    # Coerce enum values (pydantic gives us Enum instances)
    diff: Dict[str, Any] = {}
    changes: Dict[str, Dict[str, Any]] = {}
    for key, val in incoming.items():
        new_val = val.value if isinstance(val, Enum) else val
        old_val = existing.get(key)
        if new_val != old_val:
            diff[key] = new_val
            changes[key] = {"from": old_val, "to": new_val}

    # Side-effect: re-resolve sales_person_name when sales_person_id changes
    if "sales_person_id" in diff:
        new_name = _resolve_sales_person_name(diff["sales_person_id"])
        if new_name != existing.get("sales_person_name"):
            diff["sales_person_name"] = new_name
            changes["sales_person_name"] = {
                "from": existing.get("sales_person_name"),
                "to": new_name,
            }

    if not diff:
        return _serialize_walkout(existing)

    updated = repo.update_walkout(
        walkout_id, diff, updated_by=current_user.get("user_id")
    )
    if updated is None:
        raise HTTPException(
            status_code=500,
            detail="Failed to update walkout (DB write error)",
        )

    _audit_walkout_action(
        action="walkout.update",
        walkout_id=walkout_id,
        store_id=existing.get("store_id", ""),
        current_user=current_user,
        detail={"changes": changes},
    )

    return _serialize_walkout(updated)


@router.delete("/{walkout_id}", status_code=200)
async def delete_walkout(
    walkout_id: str,
    payload: DeleteWalkoutRequest,
    current_user: dict = Depends(get_current_user),
):
    """Soft-delete a walkout. Hides the row from list + GET-by-id but
    keeps the doc for audit / forensics. SUPERADMIN/ADMIN any store;
    STORE_MANAGER own store only.
    """
    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    existing = repo.find_by_walkout_id(walkout_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Walkout not found")

    _check_delete_permission(existing, current_user)

    ok = repo.soft_delete_walkout(
        walkout_id,
        deleted_by=current_user.get("user_id"),
        reason=payload.reason,
    )
    if not ok:
        raise HTTPException(
            status_code=500, detail="Failed to delete walkout"
        )

    _audit_walkout_action(
        action="walkout.delete",
        walkout_id=walkout_id,
        store_id=existing.get("store_id", ""),
        current_user=current_user,
        detail={"reason": payload.reason},
    )
    return {"walkout_id": walkout_id, "deleted": True}


# ============================================================================
# Serialization
# ============================================================================


def _serialize_walkout(doc: dict) -> dict:
    """JSON-friendly dict. Strips MongoDB internals + ISO-formats dates."""
    if not doc:
        return doc
    out = {k: v for k, v in doc.items() if k != "_id"}
    for k in ("date", "created_at", "updated_at",
              "result_set_at", "deleted_at"):
        v = out.get(k)
        if isinstance(v, datetime):
            out[k] = v.isoformat()
    return out
