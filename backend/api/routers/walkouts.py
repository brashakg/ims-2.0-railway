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
SUPERADMIN / ADMIN / AREA_MANAGER / STORE_MANAGER / ACCOUNTANT /
SALES_STAFF / SALES_CASHIER / CASHIER (see _ALLOWED_CREATE_ROLES). Other
roles (OPTOMETRIST, WORKSHOP_STAFF) get 403 — they don't log walkouts.

Side effects on POST:
  1. If the supplied mobile isn't in `customers`, a skeleton customer
     row is auto-created with `source: "walkout"`. The new
     `customer_id` is linked back onto the walkout.
  2. An audit-log row is written (`action: "walkout.create"`).
"""

from datetime import datetime, date as date_type, timedelta
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple
import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auth import get_current_user
from ..utils.ist import ist_today, now_ist
from ..dependencies import (
    can_access_store_scoped,
    get_audit_repository,
    get_customer_repository,
    get_db,
    get_task_repository,
    get_user_repository,
    get_walkin_counter_repository,
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


# Follow-up + result enums (Phase 3)


class FollowUpMode(str, Enum):
    CALL = "CALL"
    WHATSAPP = "WHATSAPP"
    SMS = "SMS"
    EMAIL = "EMAIL"
    IN_PERSON = "IN-PERSON"


class FollowUpStatus(str, Enum):
    PENDING = "PENDING"
    DONE = "DONE"
    NOT_REACHABLE = "NOT REACHABLE"
    NOT_REQUIRED = "NOT REQUIRED"
    ESCALATED = "ESCALATED"
    # F45 D6 -- additive Excel-alignment statuses (existing values unchanged).
    RESCHEDULED = "RESCHEDULED"
    NOT_INTERESTED = "NOT INTERESTED"


class ApprovalStatus(str, Enum):
    """Anti-fake-closure: a DONE follow-up logged by a salesperson must
    be approved by a manager. Statuses not requiring approval (NOT
    REACHABLE / NOT REQUIRED / ESCALATED) leave approval_status = None.
    """

    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class WalkoutResult(str, Enum):
    DUE = "DUE"
    NEGATIVE = "NEGATIVE"
    CONVERTED = "CONVERTED"
    # F45 D6 -- additive Excel-alignment outcomes (existing values unchanged).
    # WON mirrors CONVERTED semantics for legacy-sheet imports; LOST mirrors
    # NEGATIVE. The pipeline treats CONVERTED as the canonical "credit" state.
    WON = "WON"
    LOST = "LOST"


# ============================================================================
# REQUEST / RESPONSE MODELS
# ============================================================================


_MOBILE_RE = re.compile(r"^\d{10}$")


class CreateWalkoutRequest(BaseModel):
    """Phase 1 intake payload. See doc §"Schema — walkouts collection".

    Mobile is now optional (some customers don't share their number).
    When omitted/empty, the auto-create skeleton customer step is
    skipped and the walkout doc carries customer_id=None. The frontend
    surfaces a confirmation warning in that case because follow-up
    SMS/WhatsApp/call routes are unavailable without a number.
    """

    customer_name: str = Field(..., min_length=1, max_length=120)
    mobile: Optional[str] = Field(None, description="10-digit Indian mobile; optional")
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
    def _validate_mobile(cls, v):
        # Empty string -> None (omitted); a present value must be 10 digits.
        if v is None:
            return None
        v = (v or "").strip()
        if v == "":
            return None
        if not _MOBILE_RE.match(v):
            raise ValueError("Mobile must be exactly 10 digits or left blank")
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

    model_config = ConfigDict(extra="allow")


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
        # Same shape as CreateWalkoutRequest: None / "" -> None; else
        # must be 10 digits. Mirrors the optional-mobile policy.
        if v is None:
            return None
        v = (v or "").strip()
        if v == "":
            return None
        if not _MOBILE_RE.match(v):
            raise ValueError("Mobile must be exactly 10 digits or left blank")
        return v


class DeleteWalkoutRequest(BaseModel):
    """Soft-delete payload."""

    reason: str = Field(..., min_length=1, max_length=400)


class CreateFollowUpRequest(BaseModel):
    """Phase 3 — append a new follow-up sub-doc.

    Owner now requires three rounds of follow-up (was two) so manager
    can verify each round actually happened (anti-fake-closure).
    """

    round: Literal[1, 2, 3] = Field(..., description="Follow-up round 1, 2, or 3")
    scheduled_date: date_type
    scheduled_time: Optional[str] = Field(
        None, max_length=5, description="HH:MM 24-hour"
    )
    mode: FollowUpMode
    supervisor_id: Optional[str] = None
    notes: str = ""


class ApproveFollowUpRequest(BaseModel):
    """Manager approval / rejection for a DONE follow-up.

    Issued by STORE_MANAGER / AREA_MANAGER / ADMIN / SUPERADMIN only.
    Salespeople cannot self-approve a follow-up they marked DONE.
    """

    decision: ApprovalDecision
    manager_note: Optional[str] = Field(None, max_length=400)


class UpdateFollowUpRequest(BaseModel):
    """Phase 3 — partial update of an existing follow-up sub-doc."""

    status: Optional[FollowUpStatus] = None
    notes: Optional[str] = None
    scheduled_date: Optional[date_type] = None
    scheduled_time: Optional[str] = None
    mode: Optional[FollowUpMode] = None


class SetResultRequest(BaseModel):
    """Phase 3 — outcome write."""

    result: WalkoutResult
    converted_order_id: Optional[str] = Field(
        None, description="Required when result=CONVERTED"
    )


# ============================================================================
# RBAC
# ============================================================================
_ALLOWED_CREATE_ROLES = {
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "ACCOUNTANT",
    "SALES_STAFF",
    "SALES_CASHIER",
    "CASHIER",
}
# AREA_MANAGER + ACCOUNTANT added so the create set matches the frontend
# walkouts route (App.tsx). Both are already _REATTRIBUTE_ROLES (trusted to
# edit/attribute walkouts), so allowing them to log one is consistent — and
# fixes the broken-UX where they could open the intake modal but the POST 403'd.
# Roles that can edit any walkout in their store (or any store, for
# the global ones). Sales staff can only edit walkouts they own.
_GLOBAL_EDIT_ROLES = {"SUPERADMIN", "ADMIN"}
_STORE_EDIT_ROLES = {"STORE_MANAGER", "AREA_MANAGER"}
# Roles that can soft-delete a walkout — narrower than edit.
_DELETE_ROLES = {"SUPERADMIN", "STORE_MANAGER"}
# Roles that can attribute a walkout to a salesperson other than
# themselves (per UX spec: managers / admin / accountant + above can
# pick from a dropdown; everyone below is auto-locked to self).
_REATTRIBUTE_ROLES = _GLOBAL_EDIT_ROLES | _STORE_EDIT_ROLES | {"ACCOUNTANT"}
# Roles that can APPROVE / REJECT a DONE follow-up. Anti-fake-closure:
# the salesperson who marked the follow-up DONE cannot self-approve.
# ACCOUNTANT is intentionally excluded — approval is a manager judgment
# about whether the follow-up actually happened, not a finance check.
_APPROVE_FOLLOWUP_ROLES = _GLOBAL_EDIT_ROLES | _STORE_EDIT_ROLES
# Roles trusted to self-approve when they mark a follow-up DONE
# themselves. Same set as approvers (managers + admins).
_SELF_APPROVE_ROLES = _APPROVE_FOLLOWUP_ROLES


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
    if (roles & _STORE_EDIT_ROLES) and walkout.get("store_id") == _user_store_id(
        current_user
    ):
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
    mobile: Optional[str],
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

    When mobile is empty/None (some customers don't share their
    number), skip the auto-link/create entirely and return None. The
    walkout doc stores customer_id=None and downstream follow-ups via
    call/SMS/WhatsApp won't be possible — only IN-PERSON.
    """
    # Mobile is optional now; without it there's no key to link a
    # customer record by, so don't auto-create.
    if not mobile:
        return None

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
            audit_repo.create(
                {
                    "log_id": uuid.uuid4().hex,
                    "timestamp": datetime.now(),
                    "user_id": current_user.get("user_id"),
                    "action": "customer.create",
                    "entity_type": "customer",
                    "entity_id": new_id,
                    "store_id": store_id,
                    "severity": "info",
                    "detail": {"via_walkout": True, "mobile": mobile},
                }
            )
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
        audit_repo.create(
            {
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
            }
        )
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
        audit_repo.create(
            {
                "log_id": uuid.uuid4().hex,
                "timestamp": datetime.now(),
                "user_id": current_user.get("user_id"),
                "action": action,
                "entity_type": "walkout",
                "entity_id": walkout_id,
                "store_id": store_id,
                "severity": "info",
                "detail": detail,
            }
        )
    except Exception as e:
        logger.warning(f"[WALKOUT] audit-log failed (non-fatal): {e}")


# ============================================================================
# F45 — reason-driven policy + 50/50 sale-credit + manager escalation
# ============================================================================
# Reasons that map to a promo-voucher follow-up suggestion (price objection).
_VOUCHER_REASONS = {"BUDGET/PRICE"}
# Reasons that map to a "notify on restock" watch (availability objection).
_RESTOCK_REASONS = {"BRAND", "COLOR", "NOT AVAILABLE"}
# Reason that fires an immediate manager escalation (service complaint).
_ESCALATE_REASONS = {"STAFF BEHAVIOUR"}


def _compute_policy_suggestion(
    primary_reason: str, secondary_reason: Optional[str]
) -> Dict[str, Any]:
    """Pure function (no DB). Map the walkout reason to a follow-up policy.

    Excel I.7 intent:
      BUDGET/PRICE          -> offer a promo voucher (action PROMO_VOUCHER)
      BRAND/COLOR/NOT AVAIL -> notify on restock   (action RESTOCK_WATCH)
      STAFF BEHAVIOUR       -> escalate to manager  (action MANAGER_ESCALATE)
      everything else       -> standard FU          (action STANDARD_FU)

    The primary reason wins; the secondary reason only promotes a restock
    watch when the primary did not already produce a stronger action.
    """
    primary = (primary_reason or "").strip().upper()
    secondary = (secondary_reason or "").strip().upper()

    escalate = primary in _ESCALATE_REASONS
    voucher = primary in _VOUCHER_REASONS
    restock = (primary in _RESTOCK_REASONS) or (
        secondary in _RESTOCK_REASONS and not (escalate or voucher)
    )

    if escalate:
        action = "MANAGER_ESCALATE"
        channel = "CALL"
    elif voucher:
        action = "PROMO_VOUCHER"
        channel = "WHATSAPP"
    elif restock:
        action = "RESTOCK_WATCH"
        channel = "WHATSAPP"
    else:
        action = "STANDARD_FU"
        channel = "CALL"

    return {
        "action": action,
        "voucher_eligible": bool(voucher),
        "restock_watch": bool(restock),
        "escalate_immediate": bool(escalate),
        "suggested_fu_channel": channel,
    }


def _store_manager_user_id(store_id: str) -> Optional[str]:
    """Best-effort: find a STORE_MANAGER for the given store to assign the
    escalation task to. Returns None if none found / repo unavailable."""
    try:
        user_repo = get_user_repository()
        if user_repo is None:
            return None
        candidates = user_repo.find_many(
            {"roles": "STORE_MANAGER", "store_ids": store_id}
        )
        if candidates:
            u = candidates[0]
            return u.get("user_id") or u.get("id")
    except Exception:
        pass
    return None


def _create_manager_escalation_task(
    walkout_id: str,
    store_id: str,
    sales_person_id: str,
    walkout_doc: Dict,
    current_user: dict,
) -> Optional[str]:
    """Fire a P1 staff Task to the store's STORE_MANAGER when a walkout is
    logged with reason STAFF BEHAVIOUR (Excel I.7). Best-effort -- logs a
    warning on failure and never raises. Returns the task_id or None.

    This is an in-app TASK, NOT an outbound customer message: it never rides
    a comms channel, so it is unaffected by the WhatsApp ban (build-dark).
    """
    try:
        task_repo = get_task_repository()
    except Exception:
        task_repo = None
    if task_repo is None:
        return None
    assignee = _store_manager_user_id(store_id) or sales_person_id
    task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
    task_doc = {
        "task_id": task_id,
        "title": (
            f"Staff-behaviour walkout -- {walkout_doc.get('customer_name')} "
            f"({walkout_doc.get('mobile') or 'no mobile'})"
        ),
        "description": (
            f"A customer walked out citing STAFF BEHAVIOUR (walkout "
            f"{walkout_id}). Review with the sales associate and follow up "
            f"with the customer. Notes: "
            f"{walkout_doc.get('action_remarks') or '--'}"
        ),
        "priority": "P1",
        "status": "open",
        "assigned_to": assignee,
        "assigned_by": current_user.get("user_id"),
        "store_id": store_id,
        "type": "system",
        "due_date": datetime.now(),
        "created_at": datetime.now(),
        "escalation_level": 0,
        "source": {
            "type": "walkout_staff_behaviour",
            "walkout_id": walkout_id,
            "sales_person_id": sales_person_id,
        },
    }
    try:
        task_repo.create(task_doc)
    except Exception as e:
        logger.warning(f"[WALKOUT] manager escalation task create failed: {e}")
        return None
    _audit_walkout_action(
        action="walkout.escalate.staff_behaviour",
        walkout_id=walkout_id,
        store_id=store_id,
        current_user=current_user,
        detail={"task_id": task_id, "assignee": assignee, "priority": "P1"},
    )
    return task_id


def _resolve_closing_user(db, converted_order_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Resolve the closing associate (who actually sold the order) from the
    orders collection. Returns (user_id, user_name). Fail-soft -> (None, None).
    """
    if db is None or not converted_order_id:
        return None, None
    try:
        orders_coll = db.get_collection("orders")
        order = orders_coll.find_one(
            {"order_id": converted_order_id}
        ) or orders_coll.find_one({"order_number": converted_order_id})
    except Exception:
        return None, None
    if not order:
        return None, None
    uid = (
        order.get("sales_person_id")
        or order.get("salesperson_id")
        or order.get("created_by")
    )
    if not uid:
        return None, None
    return uid, (_resolve_sales_person_name(uid) or uid)


def _write_sale_credits(
    db,
    *,
    walkout_id: str,
    store_id: str,
    order_id: str,
    logging_user_id: str,
    logging_user_name: Optional[str],
    closing_user_id: str,
    closing_user_name: Optional[str],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Write the 50/50 sale-credit split (DECISIONS sec 3).

    P0-1 COMPLIANT: standalone Mongo has NO multi-doc transactions. We write
    each credit type to its OWN single document in `walkout_sale_credits` via
    a guarded find_one_and_update upsert (deterministic _id makes it
    idempotent on retry), then mirror the two dicts onto the walkout doc's
    embedded `sale_credits` array in a separate single-doc update. No
    cross-collection atomic write is attempted.

    When the logging associate IS the closing associate, both 50% rows point
    at the same user (SC rollup sums them to 100% of that conversion).

    Returns the list of credit dicts written (for the response / embed).
    """
    # month_key is the IST business month (BUG-104): an un-injected `now` is
    # the server's UTC wall-clock, and a conversion credited at 00:30 IST on
    # the 1st must not roll into the PRIOR month's SC rollup. An injected
    # `now` (tests) stays authoritative for determinism.
    explicit_now = now is not None
    now = now or datetime.now()
    month_key = now.strftime("%Y-%m") if explicit_now else now_ist().strftime("%Y-%m")
    plan = [
        ("LOGGING", logging_user_id, logging_user_name),
        ("CLOSING", closing_user_id, closing_user_name),
    ]
    credits: List[Dict[str, Any]] = []
    coll = None
    if db is not None:
        try:
            coll = db.get_collection("walkout_sale_credits")
        except Exception:
            coll = None
    for credit_type, uid, uname in plan:
        credit = {
            "_id": f"WC-{walkout_id}-{credit_type}",
            "walkout_id": walkout_id,
            "order_id": order_id,
            "store_id": store_id,
            "credit_type": credit_type,
            "user_id": uid,
            "user_name": uname,
            "pct": 50,
            "credited_at": now,
            "month_key": month_key,
        }
        if coll is not None:
            try:
                # Single-document upsert -- idempotent on retry via _id +
                # $setOnInsert. One op per credit type; never cross-collection.
                coll.find_one_and_update(
                    {"_id": credit["_id"]},
                    {"$setOnInsert": credit},
                    upsert=True,
                )
            except Exception as e:
                logger.warning(
                    f"[WALKOUT] sale-credit upsert failed ({credit_type}): {e}"
                )
        # Embedded mirror omits the Mongo _id.
        credits.append({k: v for k, v in credit.items() if k != "_id"})
    return credits


# Follow-up modes that route to an outbound message (vs a staff task only).
_MESSAGE_FU_MODES = {"WHATSAPP", "SMS", "EMAIL"}


async def _maybe_queue_followup_outbound(
    db,
    *,
    walkout_row: Dict,
    round_num: int,
    mode: str,
    current_user: dict,
) -> str:
    """F45 D4 -- DARK outbound for a WHATSAPP/SMS/EMAIL overdue FU.

    BUILD-DARK CONTRACT (COMMS CHANNEL DIRECTIVE 2026-06-07): this NEVER calls
    a provider directly. It rides the EXISTING E6 gate + send_notification
    path, which writes a PENDING row and only dispatches under
    DISPATCH_MODE=live (Railway default off). The 30-day/3-message cap
    (reminder_rail.check_frequency_cap, a SOFT ceiling) is checked first; a
    capped customer is suppressed (no notification, no ledger row). On a queue,
    a comms_ledger row is recorded so the cap stays accurate.

    Returns one of: 'no_customer', 'capped', 'queued', 'unavailable', 'error'.
    A staff Task is ALWAYS created by the caller regardless of this outcome.
    """
    customer_id = walkout_row.get("customer_id")
    phone = walkout_row.get("mobile") or ""
    if not customer_id or not phone:
        return "no_customer"
    if db is None:
        return "unavailable"
    try:
        from api.services import reminder_rail
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[WALKOUT] reminder_rail unavailable: {e}")
        return "unavailable"

    # E6 SOFT-ceiling cap: a customer at 3/30 is suppressed.
    try:
        if not reminder_rail.check_frequency_cap(db, customer_id):
            return "capped"
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[WALKOUT] freq-cap check failed (fail-open): {e}")

    channel = "SMS" if mode == "SMS" else ("EMAIL" if mode == "EMAIL" else "WHATSAPP")
    try:
        from api.services.notification_service import send_notification
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[WALKOUT] send_notification unavailable: {e}")
        return "unavailable"

    try:
        await send_notification(
            store_id=walkout_row.get("store_id") or "",
            customer_id=customer_id,
            customer_phone=phone,
            customer_name=walkout_row.get("customer_name", "Customer"),
            template_id="WALKOUT_RECOVERY",
            channel=channel,
            variables={"customer_name": walkout_row.get("customer_name", "Customer")},
            category="PROMOTIONAL",
            triggered_by=f"walkout_fu:{walkout_row.get('walkout_id')}:{round_num}",
            related_entity_type="walkout",
            related_entity_id=walkout_row.get("walkout_id"),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[WALKOUT] dark FU outbound failed: {e}")
        return "error"
    # Record the cap ledger row so the 30-day cap stays accurate.
    try:
        reminder_rail.record_outbound(
            db, customer_id, channel=channel, category="MARKETING"
        )
    except Exception:  # noqa: BLE001
        pass
    return "queued"


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

    # The walkout day key is the store's IST business day (BUG-104): a walkout
    # logged at 00:30 IST must NOT land on the prior UTC day, or the
    # conversion-feed (which filters on date_str) misses it.
    target_date = payload.date or ist_today()
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
            if payload.secondary_walkout_reason
            else None
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

    # F45 D3 -- reason-driven follow-up policy. Pure compute, then stamp on
    # the doc. STAFF BEHAVIOUR additionally fires an immediate P1 staff Task
    # to the store manager (an in-app task, never an outbound message).
    suggestion = _compute_policy_suggestion(
        payload.primary_walkout_reason.value,
        payload.secondary_walkout_reason.value
        if payload.secondary_walkout_reason
        else None,
    )
    if suggestion.get("escalate_immediate"):
        task_id = _create_manager_escalation_task(
            saved["walkout_id"],
            store_id,
            payload.sales_person_id,
            saved,
            current_user,
        )
        if task_id:
            suggestion["escalation_task_id"] = task_id
    enriched = repo.update_policy_suggestion(saved["walkout_id"], suggestion)
    if enriched is not None:
        saved = enriched

    # JSON-serialize the datetime fields
    return _serialize_walkout(saved)


@router.get("")
@router.get("/", include_in_schema=False)
async def list_walkouts(
    current_user: dict = Depends(get_current_user),
    date_from: Optional[str] = Query(
        None, description="ISO date (YYYY-MM-DD), inclusive"
    ),
    date_to: Optional[str] = Query(
        None, description="ISO date (YYYY-MM-DD), inclusive"
    ),
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


# ----------------------------------------------------------------------------
# Phase 3 — collection-level FU endpoints (registered BEFORE /{walkout_id}
# so FastAPI doesn't swallow "/followups/due-today" as a walkout id).
# ----------------------------------------------------------------------------


@router.get("/pos-compliance-check")
async def pos_compliance_check(
    current_user: dict = Depends(get_current_user),
    store_id: str = Query(..., description="Store to check"),
    sales_person_id: str = Query(..., description="Salesperson to check"),
):
    """F45 D5 -- POS soft-block compliance counter.

    Returns {open_count, overdue_count, oldest_open_date} for a
    (store, salesperson): walkouts with NO result yet. SOFT-BLOCK ONLY --
    this is a read-only counter that drives a dismissable POS banner; it
    NEVER blocks a sale (DECISIONS sec 2 item 10). overdue_count counts the
    open walkouts logged more than 48h ago.
    """
    repo = _walkout_repo()
    if repo is None:
        return {"open_count": 0, "overdue_count": 0, "oldest_open_date": None}

    rows = repo.list_open_for_staff(store_id, sales_person_id)
    open_count = len(rows)

    # date_str is an IST day key, so the 48h overdue cutoff must be IST too.
    cutoff = (now_ist() - timedelta(hours=48)).date().isoformat()
    overdue_count = 0
    oldest: Optional[str] = None
    for r in rows:
        ds = r.get("date_str") or ""
        if ds:
            if oldest is None or ds < oldest:
                oldest = ds
            if ds <= cutoff:
                overdue_count += 1
    return {
        "open_count": open_count,
        "overdue_count": overdue_count,
        "oldest_open_date": oldest,
    }


@router.get("/followups/due-today")
async def followups_due_today(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(
        None, description="Cross-store override (SUPERADMIN/ADMIN only)"
    ),
):
    """Pending follow-ups whose scheduled_date is today.

    Sales staff see only their own; managers see the whole store;
    SUPERADMIN/ADMIN can override `store_id` for cross-store views.
    """
    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    roles = _user_role_set(current_user)
    if roles & _GLOBAL_EDIT_ROLES:
        effective_store = store_id or None
    else:
        effective_store = _user_store_id(current_user)
        if not effective_store:
            raise HTTPException(
                status_code=400,
                detail="No active store on this session",
            )

    today = _ist_today_str()
    rows = repo.list_followups_due_today(today, store_id=effective_store)

    # Sales-staff RBAC scope: own only.
    if not (roles & (_GLOBAL_EDIT_ROLES | _STORE_EDIT_ROLES | {"ACCOUNTANT"})):
        uid = current_user.get("user_id")
        rows = [r for r in rows if r.get("sales_person_id") == uid]

    return {"items": [_serialize_value(r) for r in rows], "as_of": today}


@router.post("/followups/escalate-overdue")
async def escalate_overdue_followups(
    current_user: dict = Depends(get_current_user),
):
    """Cron-callable: turns every PENDING follow-up whose scheduled_date
    is in the past into a Task assigned to the supervisor (or, if absent,
    to the sales person). Round 1 -> P2 priority, round 2 / 3 -> P1
    (later rounds reflect a customer who's been slipping through the
    cracks and needs an urgent push).

    Only SUPERADMIN/ADMIN/STORE_MANAGER can invoke (it writes to
    multiple users' task queues).
    """
    roles = _user_role_set(current_user)
    if not (roles & (_GLOBAL_EDIT_ROLES | _STORE_EDIT_ROLES)):
        raise HTTPException(
            status_code=403,
            detail="Only superadmin / admin / store-manager can run escalation",
        )

    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    db = get_db()
    today = _ist_today_str()
    overdue = repo.list_overdue_followups(today)

    try:
        task_repo = get_task_repository()
    except Exception:
        task_repo = None

    created = []
    # F45 D4 -- dark-outbound tally (no live send; PENDING / cap-suppressed).
    outbound = {"queued": 0, "capped": 0, "no_customer": 0, "unavailable": 0, "error": 0}
    for row in overdue:
        round_num = int(row.get("round") or 0)
        priority = "P1" if round_num >= 2 else "P2"
        assignee = (
            row.get("supervisor_id")
            or row.get("sales_person_id")
            or current_user.get("user_id")
        )
        task_id = f"TASK-{uuid.uuid4().hex[:8].upper()}"
        task_doc = {
            "task_id": task_id,
            "title": (
                f"Walkout FU{round_num} overdue — {row.get('customer_name')} "
                f"({row.get('mobile')})"
            ),
            "description": (
                f"Pending follow-up for walkout {row.get('walkout_id')} "
                f"scheduled on {row.get('scheduled_date')} via "
                f"{row.get('mode')}. Notes: {row.get('notes') or '—'}"
            ),
            "priority": priority,
            "status": "open",
            "assigned_to": assignee,
            "assigned_by": current_user.get("user_id"),
            "store_id": row.get("store_id"),
            "type": "system",
            "due_date": datetime.now(),
            "created_at": datetime.now(),
            "escalation_level": 0,
            "source": {
                "type": "walkout_followup",
                "walkout_id": row.get("walkout_id"),
                "round": round_num,
            },
        }
        if task_repo is not None:
            try:
                task_repo.create(task_doc)
            except Exception as e:
                logger.warning(f"[WALKOUT] task create failed: {e}")
                continue
        repo.stamp_followup_escalation(row.get("walkout_id"), round_num, task_id)

        # F45 D4 -- for WHATSAPP/SMS/EMAIL mode FUs, ALSO ride the DARK E6
        # outbound path (freq-capped, PENDING, DISPATCH_MODE-gated -- never a
        # live provider send). CALL / IN-PERSON modes stay TASK-ONLY. The Task
        # above is created unconditionally regardless of this outcome.
        mode = str(row.get("mode") or "").strip().upper()
        outbound_status = None
        if mode in _MESSAGE_FU_MODES:
            outbound_status = await _maybe_queue_followup_outbound(
                db,
                walkout_row=row,
                round_num=round_num,
                mode=mode,
                current_user=current_user,
            )
            if outbound_status in outbound:
                outbound[outbound_status] += 1

        _audit_walkout_action(
            action="walkout.followup.escalate",
            walkout_id=row.get("walkout_id"),
            store_id=row.get("store_id") or "",
            current_user=current_user,
            detail={
                "round": round_num,
                "task_id": task_id,
                "priority": priority,
                "assignee": assignee,
                "mode": mode,
                "outbound": outbound_status,
            },
        )
        created.append(
            {
                "walkout_id": row.get("walkout_id"),
                "round": round_num,
                "task_id": task_id,
                "priority": priority,
                "assignee": assignee,
                "outbound": outbound_status,
            }
        )

    return {
        "escalated": len(created),
        "created_tasks": created,
        "outbound": outbound,
        "as_of": today,
    }


# ----------------------------------------------------------------------------
# Phase 4 — Walk-in counter + dashboard aggregations
# ----------------------------------------------------------------------------


class ManualTopupRequest(BaseModel):
    """Phase 4 — UI-driven walk-in topup."""

    delta: int = Field(..., ge=1, le=50)
    reason: str = Field(..., min_length=1, max_length=200)
    sales_person_id: Optional[str] = None


def _resolve_dashboard_store(current_user: dict, store_override: Optional[str]) -> str:
    """SUPERADMIN/ADMIN may override store; everyone else is locked to
    their session store. Raises 400 if neither path yields one."""
    roles = _user_role_set(current_user)
    if roles & _GLOBAL_EDIT_ROLES and store_override:
        return store_override
    store = _user_store_id(current_user)
    if not store:
        raise HTTPException(status_code=400, detail="No active store on this session")
    return store


# ----------------------------------------------------------------------------
# N3 — manual footfall capture (per-staff walk-in entry + status)
# ----------------------------------------------------------------------------
# Roles allowed to SET a per-staff walk-in count. Narrower than the POS "+1"
# button: only managers + admin may set/edit attribution, so sales staff cannot
# self-inflate their own conversion denominator.
_WALKIN_EDIT_ROLES = _GLOBAL_EDIT_ROLES | _STORE_EDIT_ROLES
# Customer-facing roles whose presence on the roster is expected to carry a
# walk-in count -- this is the "expected staff" set the entry-status enum is
# computed against (accountants / workshop staff don't handle walk-ins).
_FOOTFALL_STAFF_ROLES = {
    "STORE_MANAGER",
    "SALES_STAFF",
    "SALES_CASHIER",
    "CASHIER",
    "OPTOMETRIST",
}
# The footfall day is the store's local (Indian, IST=UTC+5:30) day, so every
# day key / "no future date" guard must compare against IST, not the server's
# UTC clock (BUG-104). Rides the canonical api.utils.ist helpers.


def _ist_today_str() -> str:
    """Today's date in IST (the store's local day)."""
    return ist_today().isoformat()


def _validate_not_future(date_str: str) -> str:
    """Reject a footfall date in the future (IST). Returns the validated
    ISO date string; raises 422 on a malformed or future date."""
    try:
        d = date_type.fromisoformat(date_str)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    if d.isoformat() > _ist_today_str():
        raise HTTPException(
            status_code=422,
            detail="Footfall date cannot be in the future",
        )
    return d.isoformat()


def _expected_footfall_staff(store_id: str) -> List[str]:
    """Active customer-facing staff ids for a store -- the roster the footfall
    entry-status enum is measured against. Fail-soft: user repo unavailable ->
    empty list (status then collapses to PENDING/COMPLETE on entries alone)."""
    try:
        user_repo = get_user_repository()
        if user_repo is None:
            return []
        users = user_repo.find_by_store(store_id, active_only=True) or []
    except Exception:  # noqa: BLE001
        return []
    out: List[str] = []
    for u in users:
        roles = set(u.get("roles", []) or [])
        if roles & _FOOTFALL_STAFF_ROLES:
            uid = u.get("user_id") or u.get("id")
            if uid:
                out.append(uid)
    return out


class PerStaffWalkinRequest(BaseModel):
    """N3 — manager sets/updates one staff member's walk-in count for a day."""

    staff_id: str = Field(..., min_length=1)
    walk_ins: int = Field(..., ge=0, le=1000)
    date_str: Optional[str] = Field(
        None, description="ISO date (YYYY-MM-DD); defaults to today (IST)"
    )
    reason: Optional[str] = Field(None, max_length=200)


@router.get("/walkins/today")
async def walkins_today(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """Today's walk-in count + per-staff breakdown for the dashboard.

    N3: annotates the response with the footfall ``entry_status`` enum
    (PENDING / PARTIAL / COMPLETE) computed from the per-staff entries against
    the active customer-facing roster."""
    store = _resolve_dashboard_store(current_user, store_id)
    repo = get_walkin_counter_repository()
    if repo is None:
        return {
            "store_id": store,
            "date_str": _ist_today_str(),
            "pos_auto_count": 0,
            "manual_topup": 0,
            "total": 0,
            "per_staff": {},
            "entry_status": "PENDING",
        }
    # Use the IST day so this read reconciles with the per-staff PATCH (which
    # writes against the IST date) around the UTC midnight boundary.
    doc = repo.get_today(store, date_str=_ist_today_str())
    expected = _expected_footfall_staff(store)
    doc["entry_status"] = repo.compute_entry_status(doc.get("per_staff") or {}, expected)
    return _serialize_value(doc)


@router.get("/walkins/mtd")
async def walkins_mtd(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
    year: Optional[int] = Query(None, ge=2024, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
):
    """Month-to-date walk-in totals + per-staff breakdown."""
    store = _resolve_dashboard_store(current_user, store_id)
    now = now_ist()  # IST month boundary, not the server's UTC month
    yr = year or now.year
    mo = month or now.month
    repo = get_walkin_counter_repository()
    if repo is None:
        return {
            "store_id": store,
            "year": yr,
            "month": mo,
            "pos_auto_count": 0,
            "manual_topup": 0,
            "total": 0,
            "per_staff": {},
            "days_with_data": 0,
        }
    return _serialize_value(repo.get_mtd(store, yr, mo))


@router.post("/walkins/manual-topup", status_code=201)
async def walkins_manual_topup(
    payload: ManualTopupRequest,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """Bump the day's counter for browse-and-leave customers. Audited."""
    store = _resolve_dashboard_store(current_user, store_id)
    # Only managers + admin + accountant may manually inflate the counter
    roles = _user_role_set(current_user)
    if not (roles & _REATTRIBUTE_ROLES):
        raise HTTPException(
            status_code=403,
            detail="Only managers / admin / accountant can manually top up walk-ins",
        )
    repo = get_walkin_counter_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    result = repo.manual_topup(
        store_id=store,
        added_by=current_user.get("user_id") or "",
        delta=payload.delta,
        reason=payload.reason,
        sales_person_id=payload.sales_person_id,
    )
    _audit_walkout_action(
        action="walkin.manual_topup",
        walkout_id=f"walkin:{store}:{_ist_today_str()}",
        store_id=store,
        current_user=current_user,
        detail={
            "delta": payload.delta,
            "reason": payload.reason,
            "sales_person_id": payload.sales_person_id,
        },
    )
    return _serialize_value(result)


# Roles allowed to tap the POS "+1 walk-in" button — every customer-
# facing in-store role, not just managers (unlike manual-topup, which
# can inflate the count and stays manager-gated).
_WALKIN_POS_ROLES = _ALLOWED_CREATE_ROLES | {
    "OPTOMETRIST",
    "AREA_MANAGER",
    "ACCOUNTANT",
}


class PosWalkinRequest(BaseModel):
    """POS "+1 walk-in" tap. Attributes the footfall to a salesperson and
    deduplicates per (mobile, day) when a mobile is supplied."""

    sales_person_id: str = Field(..., min_length=1)
    mobile: Optional[str] = None


@router.post("/walkins/pos-increment", status_code=201)
async def walkins_pos_increment(
    payload: PosWalkinRequest,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """Sales-staff-allowed walk-in increment from the POS. Unlike
    manual-topup (manager-only, arbitrary delta), this bumps the counter
    by exactly one and attributes it to the order's salesperson, so the
    per-staff conversion denominator stays accurate. Deduped per
    (mobile, day) by the repo's auto_increment."""
    roles = _user_role_set(current_user)
    if not (roles & _WALKIN_POS_ROLES):
        raise HTTPException(status_code=403, detail="Not allowed to record walk-ins")
    store = _resolve_dashboard_store(current_user, store_id)
    repo = get_walkin_counter_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    result = repo.auto_increment(
        store_id=store,
        sales_person_id=payload.sales_person_id,
        mobile=payload.mobile,
    )
    return _serialize_value(result)


@router.get("/walkins/status")
async def walkins_status(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
    date: Optional[str] = Query(
        None, description="ISO date (YYYY-MM-DD); defaults to today (IST)"
    ),
):
    """N3 footfall capture status for a (store, date).

    Returns the PENDING / PARTIAL / COMPLETE enum plus the staff who have a
    walk-in entry and those still missing, so the manager dashboard + SC
    scorecard header can show a compliance badge. Future dates are rejected."""
    store = _resolve_dashboard_store(current_user, store_id)
    target_date = _validate_not_future(date or _ist_today_str())

    repo = get_walkin_counter_repository()
    expected = _expected_footfall_staff(store)
    per_staff: Dict[str, int] = {}
    if repo is not None:
        doc = repo.get_today(store, date_str=target_date)
        per_staff = {
            sp: int(n) for sp, n in (doc.get("per_staff") or {}).items() if sp
        }

    entered_ids = set(per_staff.keys())
    expected_ids = {s for s in expected if s}
    staff_missing = sorted(expected_ids - entered_ids)
    status = (
        repo.compute_entry_status(per_staff, expected)
        if repo is not None
        else "PENDING"
    )

    total_walk_ins = sum(per_staff.values())
    # Store conversion % is only meaningful once every expected staff is
    # accounted for; otherwise it's a partial (misleading) denominator -> null.
    store_conversion_pct: Optional[float] = None

    return {
        "store_id": store,
        "date_str": target_date,
        "status": status,
        "staff_with_data": [
            {"staff_id": sp, "walk_ins": n} for sp, n in sorted(per_staff.items())
        ],
        "staff_missing": staff_missing,
        "total_walk_ins": total_walk_ins,
        "store_conversion_pct": store_conversion_pct,
    }


@router.patch("/walkins/per-staff")
async def walkins_per_staff(
    payload: PerStaffWalkinRequest,
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """N3 — manager sets/updates ONE staff member's walk-in count for a day.

    Unlike pos-increment (a +1 floor) or manual-topup (an aggregate store
    delta), this OVERWRITES the per-staff attribution that drives the SC
    conversion denominator -- so it is gated to managers + admin only (a sales
    person cannot inflate their own conversion %). ``walk_ins=0`` is allowed
    (a staff with no customers today is real data). Atomic single-doc write;
    audited as ``walkin.per_staff_update``. Future dates rejected (IST)."""
    roles = _user_role_set(current_user)
    if not (roles & _WALKIN_EDIT_ROLES):
        raise HTTPException(
            status_code=403,
            detail="Only managers / admin can set per-staff walk-in counts",
        )
    store = _resolve_dashboard_store(current_user, store_id)
    target_date = _validate_not_future(payload.date_str or _ist_today_str())

    repo = get_walkin_counter_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    expected = _expected_footfall_staff(store)
    result = repo.set_per_staff(
        store_id=store,
        staff_id=payload.staff_id,
        walk_ins=payload.walk_ins,
        updated_by=current_user.get("user_id") or "",
        date_str=target_date,
        reason=payload.reason,
        expected_staff=expected,
    )
    _audit_walkout_action(
        action="walkin.per_staff_update",
        walkout_id=f"walkin:{store}:{target_date}",
        store_id=store,
        current_user=current_user,
        detail={
            "staff_id": payload.staff_id,
            "walk_ins": payload.walk_ins,
            "date_str": target_date,
            "reason": payload.reason,
        },
    )
    return _serialize_value(result)


@router.get("/dashboard/per-staff")
async def dashboard_per_staff(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
):
    """Per-staff dashboard cards.

    Canonical aggregation contract (so the per-staff grid reconciles
    with the top-card "Walk-ins today" KPI):

      - WALK-INS TODAY headline (GET /walkins/today)
        = pos_auto_count + manual_topup
        = sum(per_staff.values()) + unattributed_walk_ins_today

      - Per-staff `walk_ins_today` here is sourced from the SAME
        ``walkin_counter_repository.get_today(store).per_staff`` dict
        the headline uses. They share one source of truth.

      - When a walk-in is logged without ``sales_person_id`` (allowed by
        POS hook + manual-topup), it counts in the headline ``total``
        but NOT in any attributable per-staff row. We surface those
        as a synthetic ``sales_person_id="unattributed"`` row when
        non-zero, so the grid rows always sum to the headline.

      - All other per-row metrics (walkouts MTD/today, converted_mtd,
        walk_ins_mtd, fu_due_today) follow the same rule: sum across
        rows reconciles to the corresponding aggregate.

    Display names:
      - Every row carries ``sales_person_name`` resolved via the
        priority chain ``full_name -> username -> user_id`` (mirroring
        PR #276 POS resolver), never the raw user_id.
      - Salespersons appearing ONLY via the walk-in counter (not via a
        walkout record) get their name resolved from ``users`` here so
        no raw ``user-*`` id leaks into the UI.
      - Missing/deleted users fall back to ``"Unknown user"`` (not the
        raw id).
    """
    store = _resolve_dashboard_store(current_user, store_id)
    walkout_repo = _walkout_repo()
    walkin_repo = get_walkin_counter_repository()
    if walkout_repo is None:
        return {"store_id": store, "items": []}

    now = ist_today()  # IST business day, not the server's UTC date (BUG-104)
    today = now.isoformat()
    mtd_from = now.replace(day=1).isoformat()

    # Walkouts MTD by sales_person (built from list — small N per store).
    mtd_walkouts = walkout_repo.list_walkouts(
        store_id=store,
        date_from=mtd_from,
        date_to=today,
        limit=2000,
    )
    today_walkouts = [w for w in mtd_walkouts if w.get("date_str") == today]

    per_staff: Dict[str, Dict[str, Any]] = {}

    def _slot(sp: str) -> Dict[str, Any]:
        if sp not in per_staff:
            per_staff[sp] = {
                "sales_person_id": sp,
                "sales_person_name": None,
                "walkouts_mtd": 0,
                "walkouts_today": 0,
                "converted_mtd": 0,
                "walk_ins_today": 0,
                "walk_ins_mtd": 0,
                "fu_due_today": 0,
            }
        return per_staff[sp]

    for w in mtd_walkouts:
        sp = w.get("sales_person_id") or ""
        if not sp:
            continue
        slot = _slot(sp)
        slot["sales_person_name"] = (
            w.get("sales_person_name") or slot["sales_person_name"]
        )
        slot["walkouts_mtd"] += 1
        if w.get("result") == "CONVERTED":
            slot["converted_mtd"] += 1
    for w in today_walkouts:
        sp = w.get("sales_person_id") or ""
        if not sp:
            continue
        _slot(sp)["walkouts_today"] += 1

    # Track headline totals to surface the unattributed remainder.
    total_walk_ins_today = 0
    attributed_walk_ins_today = 0
    total_walk_ins_mtd = 0
    attributed_walk_ins_mtd = 0
    if walkin_repo is not None:
        today_doc = walkin_repo.get_today(store, date_str=today)
        total_walk_ins_today = int(today_doc.get("total") or 0)
        for sp, count in (today_doc.get("per_staff") or {}).items():
            if not sp:
                continue
            _slot(sp)["walk_ins_today"] = int(count)
            attributed_walk_ins_today += int(count)
        mtd_doc = walkin_repo.get_mtd(store, now.year, now.month)
        total_walk_ins_mtd = int(mtd_doc.get("total") or 0)
        for sp, count in (mtd_doc.get("per_staff") or {}).items():
            if not sp:
                continue
            _slot(sp)["walk_ins_mtd"] = int(count)
            attributed_walk_ins_mtd += int(count)

    # FU-due-today per staff
    due_rows = walkout_repo.list_followups_due_today(today, store_id=store)
    for r in due_rows:
        sp = r.get("sales_person_id") or ""
        if not sp:
            continue
        _slot(sp)["fu_due_today"] += 1

    # Surface the unattributed walk-in remainder so the grid reconciles
    # with the headline KPI. We only synthesize a row when there IS a
    # remainder (else the grid stays clean).
    unattributed_today = max(0, total_walk_ins_today - attributed_walk_ins_today)
    unattributed_mtd = max(0, total_walk_ins_mtd - attributed_walk_ins_mtd)
    if unattributed_today > 0 or unattributed_mtd > 0:
        slot = _slot("unattributed")
        slot["sales_person_name"] = "Unattributed"
        slot["walk_ins_today"] = unattributed_today
        slot["walk_ins_mtd"] = unattributed_mtd

    # Resolve display names for rows that don't already have one (the
    # walk-in-only or fu-only sources don't carry the name). Cache per
    # request so one user_repo lookup serves multiple appearances.
    name_cache: Dict[str, str] = {}

    def _resolve_name(sp: str) -> str:
        if sp == "unattributed":
            return "Unattributed"
        if sp in name_cache:
            return name_cache[sp]
        resolved = _resolve_sales_person_name(sp)
        # _resolve_sales_person_name returns the raw id as a last-resort
        # fallback when the user is found but has no name field; it
        # returns None when the user lookup itself fails. We treat the
        # latter as a deleted/missing user and surface "Unknown user"
        # so the raw id never reaches the UI.
        if not resolved or resolved == sp:
            resolved = "Unknown user"
        name_cache[sp] = resolved
        return resolved

    # Conversion% (Module ii will use the formal scoring; this is the
    # raw MTD ratio for the dashboard chip).
    items = []
    for sp, slot in per_staff.items():
        denom = slot["walkouts_mtd"]
        slot["conversion_pct_mtd"] = (
            round(100.0 * slot["converted_mtd"] / denom, 1) if denom else 0.0
        )
        if not slot["sales_person_name"]:
            slot["sales_person_name"] = _resolve_name(sp)
        items.append(slot)

    items.sort(key=lambda s: (-s["walkouts_mtd"], s["sales_person_id"]))
    return {
        "store_id": store,
        "as_of": today,
        "items": items,
        "totals": {
            "walk_ins_today": total_walk_ins_today,
            "walk_ins_mtd": total_walk_ins_mtd,
            "walkouts_mtd": sum(s["walkouts_mtd"] for s in items),
            "walkouts_today": sum(s["walkouts_today"] for s in items),
        },
    }


@router.get("/dashboard/top-reasons")
async def dashboard_top_reasons(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=50),
    days: int = Query(30, ge=1, le=365),
):
    """Top primary_walkout_reason values, sorted desc by count."""
    store = _resolve_dashboard_store(current_user, store_id)
    repo = _walkout_repo()
    if repo is None:
        return {"store_id": store, "items": []}

    today = ist_today()
    date_from = (today - timedelta(days=days - 1)).isoformat()
    walkouts = repo.list_walkouts(
        store_id=store,
        date_from=date_from,
        date_to=today.isoformat(),
        limit=5000,
    )
    counts: Dict[str, int] = {}
    for w in walkouts:
        r = w.get("primary_walkout_reason") or "OTHER"
        counts[r] = counts.get(r, 0) + 1
    items = [{"reason": k, "count": v} for k, v in counts.items()]
    items.sort(key=lambda x: (-x["count"], x["reason"]))
    return {
        "store_id": store,
        "days": days,
        "items": items[:limit],
    }


@router.get("/dashboard/result-breakdown")
async def dashboard_result_breakdown(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
):
    """Counts of DUE / NEGATIVE / CONVERTED / no_result over the last N days."""
    store = _resolve_dashboard_store(current_user, store_id)
    repo = _walkout_repo()
    if repo is None:
        return {"store_id": store, "buckets": {}}

    today = ist_today()
    date_from = (today - timedelta(days=days - 1)).isoformat()
    walkouts = repo.list_walkouts(
        store_id=store,
        date_from=date_from,
        date_to=today.isoformat(),
        limit=5000,
    )
    buckets = {"DUE": 0, "NEGATIVE": 0, "CONVERTED": 0, "no_result": 0}
    for w in walkouts:
        r = w.get("result")
        if r in buckets:
            buckets[r] += 1
        else:
            buckets["no_result"] += 1
    return {
        "store_id": store,
        "days": days,
        "total": len(walkouts),
        "buckets": buckets,
    }


@router.get("/conversion-feed")
async def conversion_feed(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
    date: Optional[str] = Query(
        None, description="ISO date (YYYY-MM-DD), defaults to today"
    ),
):
    """The Module (ii) contract.

    For (store, date) returns one row per salesperson active on that
    day, with the math Module (ii) consumes:

        conversion_score = MIN(20, MAX(0, (walk_ins - walkouts + retro) / walk_ins × 20))

    where retro_conversions_today is "walkouts from prior days that
    flipped to CONVERTED today" — i.e. retroactive credit. The 20-point
    cap implicitly bounds. N3 / CORRECTIONS.md HARDENING line 92: if
    walk_ins == 0 there is no denominator, so the score is null (unscored)
    with footfall_missing=true -- NOT a silent 0.
    """
    store = _resolve_dashboard_store(current_user, store_id)
    repo = _walkout_repo()
    if repo is None:
        return []

    # Default to the IST business day (BUG-104): walkout date_str + the walk-in
    # counter day key are both IST, so the conversion-date filter must be too --
    # at 00:30 IST the UTC default pointed at YESTERDAY's feed.
    target_date = date or _ist_today_str()

    # Walkouts logged that day
    walkouts_today = repo.list_walkouts(
        store_id=store,
        date_from=target_date,
        date_to=target_date,
        limit=5000,
    )
    walkouts_count: Dict[str, int] = {}
    name_by_id: Dict[str, str] = {}
    for w in walkouts_today:
        sp = w.get("sales_person_id") or ""
        walkouts_count[sp] = walkouts_count.get(sp, 0) + 1
        if w.get("sales_person_name"):
            name_by_id[sp] = w["sales_person_name"]

    # Retroactive conversions: walkouts from prior days whose
    # result_set_at falls on `target_date`. Pull the last 90 days as a
    # window — the spec doesn't bound it but Phase 6 can paginate.
    from datetime import timedelta, date as _d

    target_d = _d.fromisoformat(target_date)
    window_from = (target_d - timedelta(days=90)).isoformat()
    window_to = (target_d - timedelta(days=1)).isoformat()
    prior_window = repo.list_walkouts(
        store_id=store,
        date_from=window_from,
        date_to=window_to,
        limit=5000,
    )
    retro_count: Dict[str, int] = {}
    for w in prior_window:
        if w.get("result") != "CONVERTED":
            continue
        rsa = w.get("result_set_at")
        if not rsa:
            continue
        rsa_str = (
            rsa[:10]
            if isinstance(rsa, str)
            else (rsa.date().isoformat() if isinstance(rsa, datetime) else "")
        )
        if rsa_str != target_date:
            continue
        sp = w.get("sales_person_id") or ""
        retro_count[sp] = retro_count.get(sp, 0) + 1
        if w.get("sales_person_name"):
            name_by_id.setdefault(sp, w["sales_person_name"])

    # Walk-ins per staff that day
    walkin_repo = get_walkin_counter_repository()
    walkins_per_staff: Dict[str, int] = {}
    if walkin_repo is not None:
        today_doc = walkin_repo.get_today(store, date_str=target_date)
        for sp, count in (today_doc.get("per_staff") or {}).items():
            walkins_per_staff[sp] = int(count)

    all_staff = set(walkouts_count) | set(retro_count) | set(walkins_per_staff)
    out = []
    for sp in sorted(all_staff):
        walk_ins = int(walkins_per_staff.get(sp, 0))
        walkouts = int(walkouts_count.get(sp, 0))
        retro = int(retro_count.get(sp, 0))
        # N3 / CORRECTIONS.md HARDENING line 92 (binding): no walk-in footfall
        # -> conversion is UNSCORED (null + footfall_missing flag), never a
        # silent 0 that would corrupt the SC conversion component / payout.
        if walk_ins > 0:
            raw = (walk_ins - walkouts + retro) / walk_ins * 20.0
            score: Optional[float] = round(max(0.0, min(20.0, raw)), 2)
            footfall_missing = False
        else:
            score = None
            footfall_missing = True
        out.append(
            {
                "sales_person_id": sp,
                "name": name_by_id.get(sp),
                "walk_ins_today": walk_ins,
                "walkouts_today": walkouts,
                "retro_conversions_today": retro,
                "conversion_score": score,
                "footfall_missing": footfall_missing,
            }
        )
    return out


@router.get("/dashboard/fu-status")
async def dashboard_fu_status(
    current_user: dict = Depends(get_current_user),
    store_id: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
):
    """Per-round breakdown of FU statuses:
    `{ fu1: {DONE, PENDING, ...}, fu2: {...}, fu3: {...} }`."""
    store = _resolve_dashboard_store(current_user, store_id)
    repo = _walkout_repo()
    if repo is None:
        return {"store_id": store, "fu1": {}, "fu2": {}, "fu3": {}}

    today = ist_today()
    date_from = (today - timedelta(days=days - 1)).isoformat()
    walkouts = repo.list_walkouts(
        store_id=store,
        date_from=date_from,
        date_to=today.isoformat(),
        limit=5000,
    )
    out: Dict[str, Dict[str, int]] = {"fu1": {}, "fu2": {}, "fu3": {}}
    for w in walkouts:
        for fu in w.get("followups") or []:
            round_num = fu.get("round")
            key = (
                "fu1"
                if round_num == 1
                else ("fu2" if round_num == 2 else ("fu3" if round_num == 3 else None))
            )
            if not key:
                continue
            status = fu.get("status") or "PENDING"
            out[key][status] = out[key].get(status, 0) + 1
    return {"store_id": store, "days": days, **out}


# ----------------------------------------------------------------------------
# Per-walkout FU + result endpoints
# ----------------------------------------------------------------------------


@router.post("/{walkout_id}/followups", status_code=201)
async def append_followup(
    walkout_id: str,
    payload: CreateFollowUpRequest,
    current_user: dict = Depends(get_current_user),
):
    """Append round 1, 2, or 3. Rejects duplicate rounds (409) and any
    round outside 1-3 (422 via pydantic Literal)."""
    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    existing = repo.find_by_walkout_id(walkout_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Walkout not found")
    _check_edit_permission(existing, current_user)

    existing_rounds = {fu.get("round") for fu in (existing.get("followups") or [])}
    if payload.round in existing_rounds:
        raise HTTPException(
            status_code=409,
            detail=f"Follow-up round {payload.round} already exists",
        )

    supervisor_name = (
        _resolve_sales_person_name(payload.supervisor_id)
        if payload.supervisor_id
        else None
    )

    fu_doc = {
        "round": payload.round,
        "scheduled_date": datetime.combine(payload.scheduled_date, datetime.min.time()),
        "scheduled_time": payload.scheduled_time,
        "mode": payload.mode.value,
        "supervisor_id": payload.supervisor_id,
        "supervisor_name": supervisor_name,
        "status": FollowUpStatus.PENDING.value,
        "notes": payload.notes,
        "completed_at": None,
        "completed_by": None,
        "escalation_task_id": None,
        # Approval starts empty — only relevant once the FU is DONE.
        "approval_required": False,
        "approval_status": None,
        "approved_by_user_id": None,
        "approved_by_name": None,
        "approved_at": None,
        "manager_note": None,
        "created_at": datetime.now(),
    }

    updated = repo.append_followup(walkout_id, fu_doc)
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to append follow-up")

    _audit_walkout_action(
        action="walkout.followup.create",
        walkout_id=walkout_id,
        store_id=existing.get("store_id", ""),
        current_user=current_user,
        detail={
            "round": payload.round,
            "mode": payload.mode.value,
            "scheduled_date": payload.scheduled_date.isoformat(),
        },
    )
    return _serialize_walkout(updated)


@router.patch("/{walkout_id}/followups/{round_num}")
async def update_followup(
    walkout_id: str,
    round_num: int,
    payload: UpdateFollowUpRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update FU status / notes / scheduling.

    When status flips to DONE, stamps `completed_at`+`completed_by`
    AND sets the approval state per anti-fake-closure rules:
      - Manager-tier actor (STORE_MANAGER / AREA_MANAGER / ADMIN /
        SUPERADMIN) self-approves: approval_status=APPROVED with their
        stamp; approval_required is True for record-keeping.
      - Salesperson actor: approval_status=PENDING_APPROVAL, awaiting
        a manager review on the dedicated /approve endpoint.

    Other DONE-adjacent statuses (NOT REACHABLE / NOT REQUIRED /
    ESCALATED) do not require approval — approval_required stays False
    and approval_status stays None.
    """
    if round_num not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="Invalid round")

    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    existing = repo.find_by_walkout_id(walkout_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Walkout not found")
    _check_edit_permission(existing, current_user)

    existing_fu = next(
        (
            fu
            for fu in (existing.get("followups") or [])
            if fu.get("round") == round_num
        ),
        None,
    )
    if not existing_fu:
        raise HTTPException(
            status_code=404, detail=f"Follow-up round {round_num} not found"
        )

    incoming = payload.model_dump(exclude_unset=True)
    patch: Dict[str, Any] = {}
    changes: Dict[str, Dict[str, Any]] = {}

    for key, val in incoming.items():
        new_val = val.value if isinstance(val, Enum) else val
        if key == "scheduled_date" and val is not None:
            new_val = datetime.combine(val, datetime.min.time())
        old_val = existing_fu.get(key)
        if new_val != old_val:
            patch[key] = new_val
            changes[key] = {"from": old_val, "to": new_val}

    if "status" in patch and patch["status"] == FollowUpStatus.DONE.value:
        now = datetime.now()
        patch["completed_at"] = now
        patch["completed_by"] = current_user.get("user_id")
        # Approval gate: salespeople -> PENDING_APPROVAL; managers
        # self-approve. The audit trail captures both branches.
        patch["approval_required"] = True
        if _user_role_set(current_user) & _SELF_APPROVE_ROLES:
            patch["approval_status"] = ApprovalStatus.APPROVED.value
            patch["approved_by_user_id"] = current_user.get("user_id")
            patch["approved_by_name"] = _resolve_sales_person_name(
                current_user.get("user_id")
            ) or current_user.get("username")
            patch["approved_at"] = now
        else:
            patch["approval_status"] = ApprovalStatus.PENDING_APPROVAL.value
            patch["approved_by_user_id"] = None
            patch["approved_by_name"] = None
            patch["approved_at"] = None
    elif "status" in patch and patch["status"] in (
        FollowUpStatus.NOT_REACHABLE.value,
        FollowUpStatus.NOT_REQUIRED.value,
        FollowUpStatus.ESCALATED.value,
    ):
        # Non-DONE terminal statuses don't require approval. Clear any
        # stale approval state from a prior DONE flip.
        patch["approval_required"] = False
        patch["approval_status"] = None
        patch["approved_by_user_id"] = None
        patch["approved_by_name"] = None
        patch["approved_at"] = None
        patch["manager_note"] = None

    if not patch:
        return _serialize_walkout(existing)

    updated = repo.update_followup(
        walkout_id, round_num, patch, updated_by=current_user.get("user_id")
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to update follow-up")

    _audit_walkout_action(
        action="walkout.followup.update",
        walkout_id=walkout_id,
        store_id=existing.get("store_id", ""),
        current_user=current_user,
        detail={"round": round_num, "changes": changes},
    )
    return _serialize_walkout(updated)


@router.post("/{walkout_id}/followups/{round_num}/approve")
async def approve_followup(
    walkout_id: str,
    round_num: int,
    payload: ApproveFollowUpRequest,
    current_user: dict = Depends(get_current_user),
):
    """Manager approves / rejects a DONE follow-up.

    Anti-fake-closure: only STORE_MANAGER / AREA_MANAGER / ADMIN /
    SUPERADMIN can approve; salespeople and accountants cannot
    rubber-stamp their own claimed follow-ups. STORE_MANAGER /
    AREA_MANAGER are scoped to their own store; ADMIN/SUPERADMIN can
    approve in any store.

    Each call writes an audit row (`walkout.followup.approve` for
    APPROVED, `walkout.followup.reject` for REJECTED) carrying the
    walkout_id, round, the from/to approval_status, the approver
    user_id, and the manager_note when provided.
    """
    if round_num not in (1, 2, 3):
        raise HTTPException(status_code=400, detail="Invalid round")

    roles = _user_role_set(current_user)
    if not (roles & _APPROVE_FOLLOWUP_ROLES):
        raise HTTPException(
            status_code=403,
            detail=(
                "Only store managers / area managers / admins can approve " "follow-ups"
            ),
        )

    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    existing = repo.find_by_walkout_id(walkout_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Walkout not found")

    # Store scoping for non-global approvers.
    if not (roles & _GLOBAL_EDIT_ROLES):
        if existing.get("store_id") != _user_store_id(current_user):
            raise HTTPException(
                status_code=403,
                detail="You can only approve follow-ups for your own store",
            )

    fu = next(
        (f for f in (existing.get("followups") or []) if f.get("round") == round_num),
        None,
    )
    if not fu:
        raise HTTPException(
            status_code=404, detail=f"Follow-up round {round_num} not found"
        )
    if fu.get("status") != FollowUpStatus.DONE.value:
        raise HTTPException(
            status_code=422,
            detail="Only DONE follow-ups can be approved or rejected",
        )

    prior_status = fu.get("approval_status")
    approver_id = current_user.get("user_id") or ""
    approver_name = (
        _resolve_sales_person_name(approver_id)
        or current_user.get("username")
        or approver_id
    )
    updated = repo.approve_followup(
        walkout_id,
        round_num,
        approver_user_id=approver_id,
        approver_name=approver_name,
        decision=payload.decision.value,
        manager_note=payload.manager_note,
    )
    if updated is None:
        raise HTTPException(
            status_code=500, detail="Failed to record approval decision"
        )

    action = (
        "walkout.followup.approve"
        if payload.decision == ApprovalDecision.APPROVED
        else "walkout.followup.reject"
    )
    _audit_walkout_action(
        action=action,
        walkout_id=walkout_id,
        store_id=existing.get("store_id", ""),
        current_user=current_user,
        detail={
            "round": round_num,
            "from": prior_status,
            "to": payload.decision.value,
            "approver_user_id": approver_id,
            "manager_note": payload.manager_note,
        },
    )
    return _serialize_walkout(updated)


@router.patch("/{walkout_id}/result")
async def set_walkout_result(
    walkout_id: str,
    payload: SetResultRequest,
    current_user: dict = Depends(get_current_user),
):
    """Mark walkout outcome. CONVERTED requires `converted_order_id`
    that exists in the orders collection (else 422)."""
    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    existing = repo.find_by_walkout_id(walkout_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Walkout not found")
    _check_edit_permission(existing, current_user)

    db = get_db()
    if payload.result == WalkoutResult.CONVERTED:
        if not payload.converted_order_id:
            raise HTTPException(
                status_code=422,
                detail="converted_order_id is required for CONVERTED",
            )
        # Validate the order exists. Fail-soft when DB is absent
        # (keeps tests + dev-mode fail-soft, prod path always
        # validates).
        try:
            if db is not None:
                orders_coll = db.get_collection("orders")
                order = orders_coll.find_one(
                    {"order_id": payload.converted_order_id}
                ) or orders_coll.find_one({"order_number": payload.converted_order_id})
                if not order:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Order {payload.converted_order_id} not found",
                    )
        except HTTPException:
            raise
        except Exception:
            pass

    # Snapshot prior values BEFORE mutating — set_result may share
    # dict references with `existing` depending on the underlying repo
    # / collection (true for the FakeCollection used in tests).
    prior_result = existing.get("result")
    prior_store = existing.get("store_id", "")
    logging_user_id = existing.get("sales_person_id") or ""
    logging_user_name = existing.get("sales_person_name")

    updated = repo.set_result(
        walkout_id,
        payload.result.value,
        payload.converted_order_id,
        set_by=current_user.get("user_id") or "",
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to set result")

    # F45 D2 -- 50/50 sale-credit split on CONVERTED. Resolve the closing
    # associate from the order; if absent, the logging associate closes it
    # too (both 50% rows then point at the same user). P0-1 compliant: two
    # single-doc upserts to walkout_sale_credits + one $set on the walkout.
    if payload.result == WalkoutResult.CONVERTED:
        closing_user_id, closing_user_name = _resolve_closing_user(
            db, payload.converted_order_id
        )
        if not closing_user_id:
            closing_user_id, closing_user_name = logging_user_id, logging_user_name
        credits = _write_sale_credits(
            db,
            walkout_id=walkout_id,
            store_id=prior_store,
            order_id=payload.converted_order_id,
            logging_user_id=logging_user_id,
            logging_user_name=logging_user_name,
            closing_user_id=closing_user_id,
            closing_user_name=closing_user_name,
        )
        embedded = repo.set_sale_credits(walkout_id, credits)
        if embedded is not None:
            updated = embedded

    _audit_walkout_action(
        action="walkout.result.set",
        walkout_id=walkout_id,
        store_id=prior_store,
        current_user=current_user,
        detail={
            "from": prior_result,
            "to": payload.result.value,
            "converted_order_id": payload.converted_order_id,
        },
    )
    return _serialize_walkout(updated)


@router.get("/{walkout_id}")
async def get_walkout(
    walkout_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Fetch one walkout by id. Enforces store-scoping: a user may only
    read a walkout if the walkout's store_id is one they have access to.
    Admins (SUPERADMIN/ADMIN) may read any store. Store-scoped roles are
    bounded to their own stores. Non-existent-or-inaccessible walkouts both
    return 404 (existence-hiding)."""
    repo = _walkout_repo()
    if repo is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    walkout = repo.find_by_walkout_id(walkout_id)
    if not walkout:
        raise HTTPException(status_code=404, detail="Walkout not found")
    # Store-scope check: the walkout carries customer PII (name + mobile).
    # Existence-hide an inaccessible walkout (cross-store reads return 404).
    if not can_access_store_scoped(walkout.get("store_id"), current_user):
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
        raise HTTPException(status_code=500, detail="Failed to delete walkout")

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


def _serialize_value(value):
    """Recursively ISO-format datetimes inside dicts / lists. Anything
    that isn't a datetime / dict / list flows through untouched."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    return value


def _serialize_walkout(doc: dict) -> dict:
    """JSON-friendly dict. Strips MongoDB internals + ISO-formats dates
    (including the embedded follow-up sub-docs added in Phase 3)."""
    if not doc:
        return doc
    return _serialize_value(doc)
