"""
IMS 2.0 - Vendor Returns Router
=================================
Vendor returns, debit notes, and credit note management
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime
import uuid
from .auth import require_roles
from ..dependencies import (
    can_access_store_scoped,
    get_db,
    resolve_store_scope,
    validate_store_access,
)
from ..services.cost_mask import PURCHASE_ROLES, RETURN_READERS, mask_vendor_return
from ..services.payables_mask import require_payables

# The transitions that RECORD supplier money: credit_issued writes the
# credit-note number and amount. Owner ruling 2026-10-12: the accounts roles
# alone (services/payables_mask.require_payables). Every other transition is
# the goods side and stays with _VENDOR_RETURN_ROLES -- save 'replaced' on a
# return owed a credit, which forgoes it (update_return_status).
_CREDIT_TRANSITIONS = frozenset({"credit_issued"})

# A vendor return mints a debit/credit note -- a financial instrument against a
# vendor. Restrict create + status changes to the same roles that manage vendors
# / AP (the purchase roles, services/cost_mask); it was previously open to ANY
# authenticated user (down to a cashier).
_VENDOR_RETURN_ROLES = PURCHASE_ROLES

# F60: a return carries the unit cost of each returned piece, so READS go to the
# writers plus the Vendor Returns screen (/purchase/vendor-returns also admits
# WORKSHOP_STAFF, who logs the defective pair). SALES_STAFF / CASHIER /
# OPTOMETRIST have no screen and no read. rtv_debit_notes reads use this too.
# Owner ruling 2026-09-29: WORKSHOP_STAFF sees the item, quantity and reason
# only; 2026-10-12 (R3): so does every other role outside the accounts roles,
# managers included -- services/cost_mask.mask_vendor_return projects every
# read. The tuple is cost_mask.RETURN_READERS, so the rbac_policy rows are the
# same list.
_VENDOR_RETURN_READERS = RETURN_READERS

router = APIRouter()


# ============================================================================
# SCHEMAS
# ============================================================================


class ReturnItemCreate(BaseModel):
    product_id: str
    product_name: str
    # Bounded so a zero/negative/garbage qty can't produce a bogus (or negative)
    # debit-note amount. Upper bound mirrors the orders.py line guard.
    quantity: int = Field(..., gt=0, le=100000)
    reason: str  # defective, wrong_item, expired, damaged_in_transit, quality_issue, not_as_ordered, other
    unit_price: float = Field(..., ge=0, le=10_000_000)


class VendorReturnCreate(BaseModel):
    vendor_id: str
    vendor_name: str
    store_id: str
    items: List[ReturnItemCreate]
    return_type: Literal["credit_note", "replacement"]
    notes: Optional[str] = None
    # F21: optional physical-unit linkage. When supplied, the matched QUARANTINED
    # stock_units are backfilled with rtv_vendor_id = this return's id, tying the
    # defective units physically shipped back to the RTV record.
    stock_ids: Optional[List[str]] = None


class VendorReturnStatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None
    # N9: courier/logistics tracking, captured on the "shipped" transition so
    # staff can see where an in-transit RMA physically is (mirrors the transfer
    # ship-stamp in transfers.py). Plain capture fields -- no provider call.
    courier_name: Optional[str] = Field(default=None, max_length=120)
    tracking_number: Optional[str] = Field(default=None, max_length=120)
    tracking_url: Optional[str] = Field(default=None, max_length=500)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _get_db():
    """Get raw MongoDB database for collections without a dedicated repository"""
    try:
        conn = get_db()
        if conn is not None and hasattr(conn, "db"):
            return conn.db
        elif hasattr(conn, "client"):
            return conn.client.ims_db
    except Exception:
        pass
    return None


def generate_return_id() -> str:
    """Generate unique vendor return ID"""
    timestamp = datetime.now().strftime("%Y%m%d")
    unique_part = str(uuid.uuid4())[:8].upper()
    return f"VR-{timestamp}-{unique_part}"


def generate_credit_note_number() -> str:
    """Generate unique credit note number"""
    timestamp = datetime.now().strftime("%y%m%d%H%M")
    return f"CN-{timestamp}-{str(uuid.uuid4())[:6].upper()}"


def _link_stock_units_to_rtv(db, stock_ids, return_id, store_id, actor_id) -> None:
    """F21: backfill rtv_vendor_id on QUARANTINED stock_units + audit each link.

    Only units currently in QUARANTINED status are matched/updated (the status
    filter silently skips anything else). Each match is a single-document update
    (standalone Mongo -- no transactions). One QUARANTINE_LINKED_RTV audit row is
    written per linked unit via the hash-chained AuditRepository.create facade.
    Fail-soft: a backfill error never undoes the vendor-return write."""
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
    except Exception:  # noqa: BLE001
        audit = None
    stock_coll = db.get_collection("stock_units")
    for sid in stock_ids:
        try:
            result = stock_coll.update_one(
                {"stock_id": sid, "status": "QUARANTINED", "store_id": store_id},
                {"$set": {"rtv_vendor_id": return_id}},
            )
            if getattr(result, "modified_count", 0) and audit is not None:
                audit.create(
                    {
                        "action": "QUARANTINE_LINKED_RTV",
                        "entity_type": "STOCK_UNIT",
                        "entity_id": sid,
                        "store_id": store_id,
                        "user_id": actor_id,
                        "before_state": {"rtv_vendor_id": None},
                        "after_state": {"rtv_vendor_id": return_id},
                        "detail": {"return_id": return_id},
                    }
                )
        except Exception:  # noqa: BLE001
            # One bad id never blocks the rest / the return itself.
            continue


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def list_vendor_returns(
    store_id: Optional[str] = Query(None),
    vendor_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_roles(*_VENDOR_RETURN_READERS)),
):
    """List vendor returns with optional filters"""
    db = _get_db()
    if db is None:
        return {"returns": [], "total": 0}

    # BUG-062 tail: scope vendor returns to the caller's store reach (validate an
    # explicit ?store_id, pin store-roles to their own, HQ roles see all).
    store_id = resolve_store_scope(store_id, current_user)
    try:
        collection = db.get_collection("vendor_returns")
        filter_dict = {}

        if store_id:
            filter_dict["store_id"] = store_id
        if vendor_id:
            filter_dict["vendor_id"] = vendor_id
        if status:
            filter_dict["status"] = status

        # Get total count
        total = collection.count_documents(filter_dict)

        # Get paginated results
        returns = list(
            collection.find(filter_dict).sort("created_at", -1).skip(skip).limit(limit)
        )
        # Clean up MongoDB _id field for response
        for ret in returns:
            if "_id" in ret:
                del ret["_id"]

        # THE projection (cost_mask.mask_vendor_return): anyone outside the
        # accounts roles reads items, quantities and reasons only -- no price,
        # no total, no credit (owner rulings 2026-10-01 and 2026-10-12, R3).
        returns = [mask_vendor_return(r, current_user) for r in returns]
        return {"returns": returns, "total": total}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="A database error occurred. Please try again or contact support.",
        )


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_vendor_return(
    return_data: VendorReturnCreate,
    current_user: dict = Depends(require_roles(*_VENDOR_RETURN_ROLES)),
):
    """Create a new vendor return (debit note). Vendor/AP roles only."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    # F21 IDOR fix: the caller must own the return's store (store-roles are pinned
    # to their own store; HQ roles pass). Without this a STORE_MANAGER could forge
    # store_id + RTV-link another store's QUARANTINED units (cross-store write).
    validate_store_access(return_data.store_id, current_user)

    try:
        collection = db.get_collection("vendor_returns")
        return_id = generate_return_id()

        # Calculate total value
        total_value = sum(item.quantity * item.unit_price for item in return_data.items)

        # Create return document
        return_doc = {
            "return_id": return_id,
            "vendor_id": return_data.vendor_id,
            "vendor_name": return_data.vendor_name,
            "store_id": return_data.store_id,
            "items": [
                {
                    "product_id": item.product_id,
                    "product_name": item.product_name,
                    "quantity": item.quantity,
                    "reason": item.reason,
                    "unit_price": item.unit_price,
                }
                for item in return_data.items
            ],
            "return_type": return_data.return_type,
            "status": "created",
            "total_value": total_value,
            "credit_note_number": None,
            "credit_note_amount": None,
            "notes": return_data.notes or "",
            "created_at": datetime.now().isoformat(),
            "created_by": current_user.get("user_id"),
            "status_history": [
                {
                    "status": "created",
                    "timestamp": datetime.now().isoformat(),
                    "changed_by": current_user.get("user_id"),
                    "notes": "Return created",
                }
            ],
        }

        collection.insert_one(return_doc)

        # F21: backfill the physical-unit linkage. For each supplied stock_id
        # that is currently QUARANTINED, set rtv_vendor_id = this return's id and
        # write a STOCK_UNIT audit row. A unit NOT in QUARANTINED status is
        # silently skipped (the {status: QUARANTINED} filter simply matches
        # nothing). Standalone Mongo: each update is a single-document op.
        if return_data.stock_ids:
            _link_stock_units_to_rtv(
                db, return_data.stock_ids, return_id,
                return_data.store_id, current_user.get("user_id"),
            )

        return {
            "return_id": return_id,
            "message": "Vendor return created successfully",
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail="Failed to create return. Please try again."
        )


@router.get("/{return_id}")
async def get_vendor_return(
    return_id: str,
    current_user: dict = Depends(require_roles(*_VENDOR_RETURN_READERS)),
):
    """Get vendor return details"""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        collection = db.get_collection("vendor_returns")
        return_doc = collection.find_one({"return_id": return_id})

        # ONE per-object store guard: another shop's return reads as not found
        # (404 -- it never confirms the id exists), as every by-id purchase
        # object on this branch does.
        if not return_doc or not can_access_store_scoped(return_doc.get("store_id"), current_user):
            raise HTTPException(status_code=404, detail="Return not found")

        # Clean up MongoDB _id field
        if "_id" in return_doc:
            del return_doc["_id"]

        return mask_vendor_return(return_doc, current_user)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="A database error occurred. Please try again or contact support.",
        )


@router.patch("/{return_id}/status")
async def update_return_status(
    return_id: str,
    status_update: VendorReturnStatusUpdate,
    current_user: dict = Depends(require_roles(*_VENDOR_RETURN_ROLES)),
):
    """Update vendor return status (incl. issuing a credit note). Vendor/AP roles
    only; issuing the credit note -- or closing a credit-note return as
    'replaced', which forgoes that credit -- is the accounts roles' alone."""
    if status_update.status in _CREDIT_TRANSITIONS:
        require_payables(current_user, "Recording a vendor return's credit note")
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        collection = db.get_collection("vendor_returns")
        return_doc = collection.find_one({"return_id": return_id})

        if not return_doc:
            raise HTTPException(status_code=404, detail="Return not found")
        validate_store_access(return_doc.get("store_id"), current_user)
        # A return not typed 'replacement' is owed a credit: closing it as
        # 'replaced' ends it without that credit -- forgoing supplier money,
        # the accounts roles' (owner ruling 2026-10-12, R2). On a replacement
        # return, 'replaced' is the goods coming back: the managers'. Checked
        # before any write; return_type is fixed at create.
        if status_update.status == "replaced" and return_doc.get("return_type") != "replacement":
            require_payables(current_user, "Closing a credit-note return as replaced")

        # Valid status transitions
        current_status = return_doc.get("status")
        valid_transitions = {
            "created": ["approved", "cancelled"],
            "approved": ["shipped", "cancelled"],
            "shipped": ["received_by_vendor"],
            "received_by_vendor": ["credit_issued", "replaced"],
            "credit_issued": [],
            "replaced": [],
            "cancelled": [],
        }

        if status_update.status not in valid_transitions.get(current_status, []):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from {current_status} to {status_update.status}",
            )

        # Generate credit note if transitioning to credit_issued
        credit_note_number = return_doc.get("credit_note_number")
        if status_update.status == "credit_issued" and not credit_note_number:
            credit_note_number = generate_credit_note_number()

        # Update document
        update_dict = {
            "status": status_update.status,
            "updated_at": datetime.now().isoformat(),
            "updated_by": current_user.get("user_id"),
        }

        # N9: persist courier/AWB tracking on the "shipped" transition (and allow
        # a later correction while still in transit). Surfaced on GET so staff
        # can see where the in-transit RMA physically is.
        if status_update.status in ("shipped", "received_by_vendor"):
            for field_name in ("courier_name", "tracking_number", "tracking_url"):
                value = getattr(status_update, field_name, None)
                if value is not None and str(value).strip():
                    update_dict[field_name] = str(value).strip()
            if status_update.status == "shipped" and not return_doc.get("shipped_at"):
                update_dict["shipped_at"] = datetime.now().isoformat()

        # Add status to history
        status_history = return_doc.get("status_history", [])
        history_entry = {
            "status": status_update.status,
            "timestamp": datetime.now().isoformat(),
            "changed_by": current_user.get("user_id"),
            "notes": status_update.notes or "",
        }
        if update_dict.get("tracking_number"):
            history_entry["tracking_number"] = update_dict["tracking_number"]
            history_entry["courier_name"] = update_dict.get("courier_name", "")
        status_history.append(history_entry)
        update_dict["status_history"] = status_history

        if credit_note_number:
            update_dict["credit_note_number"] = credit_note_number
            if status_update.status == "credit_issued":
                update_dict["credit_note_amount"] = return_doc.get("total_value")

        # Compare-and-swap on status: the CURRENT status is baked into the filter
        # so two concurrent transitions out of the same state can't both win. The
        # loser matches zero docs -> 409, so a credit note is issued (and the
        # status advanced) exactly once. The credit-note number generated above is
        # pure (no side effect) and is simply discarded by the loser.
        result = collection.update_one(
            {"return_id": return_id, "status": current_status},
            {"$set": update_dict},
        )
        # A real pymongo UpdateResult exposes matched_count; treat the CAS as LOST
        # only on a DEFINITE zero match. Some in-repo test fakes expose only
        # modified_count (fall back to it) or return None (unknown -> NOT a miss),
        # so we never 409 a successful single-request update. Real Mongo always
        # reports matched_count, so a genuine concurrent transition is still caught.
        matched = getattr(result, "matched_count", None)
        if matched is None:
            matched = getattr(result, "modified_count", None)
        if matched == 0:
            # CAS lost: another request already transitioned this return out of
            # current_status. Surface the CURRENT status so the caller can decide
            # (retry / skip / show a conflict) instead of a blind retry.
            latest = collection.find_one({"return_id": return_id}) or {}
            raise HTTPException(
                status_code=409,
                detail=(
                    "Return was already updated by another request "
                    f"(current status: {latest.get('status')})"
                ),
            )

        # Fetch and return updated document
        updated_doc = collection.find_one({"return_id": return_id})
        if "_id" in updated_doc:
            del updated_doc["_id"]

        return {
            "message": f"Return status updated to {status_update.status}",
            # The same one projection as the reads: the credit just issued
            # and every line price are the accounts roles' (2026-10-01, R3).
            "return": mask_vendor_return(updated_doc, current_user),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="A database error occurred. Please try again or contact support.",
        )
