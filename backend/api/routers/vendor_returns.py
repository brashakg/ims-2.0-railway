"""
IMS 2.0 - Vendor Returns Router
=================================
Vendor returns, debit notes, and credit note management
"""

from api.utils.event_time import utc_now_iso

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Annotated, List, Optional, Literal
from datetime import date, datetime
import uuid
from .auth import require_roles
from ..dependencies import (
    in_reach_or_404,
    get_db,
    resolve_store_scope,
    validate_store_access,
    user_store_scope,
)
from ..services.cost_mask import AP_ROLES, PURCHASE_ROLES, RETURN_READERS, mask_vendor_return
from ..services.payables_mask import require_payables
from ..services.rtv_debit_note import debit_note_issued

# The transitions that RECORD supplier money: credit_issued writes the
# credit-note number and amount. Owner ruling 2026-10-12: the accounts roles
# alone (services/payables_mask.require_payables). Every other transition is
# the goods side and stays with _VENDOR_RETURN_ROLES -- save 'replaced' on a
# return owed a credit, which forgoes it (update_return_status).
_CREDIT_TRANSITIONS = frozenset({"credit_issued"})

# A vendor return mints a debit/credit note -- a financial instrument against a
# vendor. Restrict status changes (approve, ship, credit) to the same roles that
# manage vendors / AP (the purchase roles, services/cost_mask); they were
# previously open to ANY authenticated user (down to a cashier). Logging a
# return is the readers' too: owner ruling 2026-10-12 lets WORKSHOP_STAFF log
# the defective pair through the same door; its price is the accounts roles'
# alone (PUT /{return_id}/prices, rulings 2026-10-12 R2 + R3).
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


# What was paid per piece -- ONE rule for the create door and the accounts'
# pricing (PUT /{return_id}/prices): 0 to 1 crore, a real number (NaN /
# Infinity would be stored and then break every read of the return).
UnitPricePaid = Annotated[float, Field(ge=0, le=10_000_000, allow_inf_nan=False)]


class ReturnItemCreate(BaseModel):
    product_id: str
    product_name: str
    # Bounded so a zero/negative/garbage qty can't produce a bogus (or negative)
    # debit-note amount. Upper bound mirrors the orders.py line guard.
    quantity: int = Field(..., gt=0, le=100000)
    reason: str  # defective, wrong_item, expired, damaged_in_transit, quality_issue, not_as_ordered, other
    # The price per piece is the basis of the supplier's credit: supplier
    # money, so the accounts roles' to write as well as to read (owner rulings
    # 2026-10-12 R2 + R3). A manager or the workshop logs the return without
    # it; an accounts role prices it here or later (PUT /{return_id}/prices).
    unit_price: Optional[UnitPricePaid] = None


class ReturnPrices(BaseModel):
    # One price per piece for each of the return's items, in their order.
    unit_prices: List[UnitPricePaid] = Field(..., min_length=1)


class CreditSourceSelection(BaseModel):
    return_line_index: int = Field(ge=0)
    grn_id: str = Field(min_length=1)
    bill_id: str = Field(min_length=1)
    bill_line_index: int = Field(ge=0)
    quantity: int = Field(gt=0, le=100000)
    stock_ids: List[str] = Field(default_factory=list)


class CreditAllocation(BaseModel):
    source_key: str = Field(min_length=64, max_length=64)
    taxable: UnitPricePaid
    cgst: UnitPricePaid
    sgst: UnitPricePaid
    igst: UnitPricePaid


class SupplierCreditPreview(BaseModel):
    confirmation_id: Optional[uuid.UUID] = None
    source_selections: Optional[List[CreditSourceSelection]] = None


class SupplierCreditConfirmation(BaseModel):
    idempotency_key: uuid.UUID
    credit_note_number: str = Field(min_length=1, max_length=120)
    credit_note_date: date
    received_amount: Annotated[float, Field(gt=0, le=1_000_000_000, allow_inf_nan=False)]
    notes: Optional[str] = Field(default=None, max_length=4000)
    source_selections: List[CreditSourceSelection] = Field(default_factory=list)
    allocations: List[CreditAllocation] = Field(default_factory=list)


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
    # Only the receipt draft -> created step may choose the disposition.
    return_type: Optional[Literal["credit_note", "replacement"]] = None
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


def _priced_total(items) -> Optional[float]:
    """A return's value -- the credit the supplier owes on it -- once EVERY
    line carries its price; None while any line is unpriced (a manager or the
    workshop logged it and no accounts role has priced it yet). Never a
    partial sum."""
    prices = [(it.get("quantity") or 0, it.get("unit_price")) for it in items or []]
    if not prices or any(price is None for _qty, price in prices):
        return None
    return round(sum(qty * price for qty, price in prices), 2)


_PRICE_REQUIRED = {
    "error": "price_required",
    "message": "This return's lines are not priced yet. Accounts set each "
               "line's price first (Purchase > Vendor Returns > Set prices).",
}


def _return_in_reach(collection, return_id: str, current_user: dict) -> dict:
    """THE per-object store guard of a vendor return -- the detail and the
    status change both ask it. A return outside the caller's shops (another
    shop's, or a legacy one stamped with no shop) is "not found": a 404 that
    never confirms the id exists or names its shop, and never a write to a
    return the detail hides. Admins reach every return."""
    return in_reach_or_404(collection.find_one({"return_id": return_id}), current_user, "Return not found")


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
    current_user: dict = Depends(require_roles(*_VENDOR_RETURN_READERS)),
):
    """Log a vendor return (the debit note is issued later). The purchase roles
    and WORKSHOP_STAFF (owner ruling 2026-10-12) log item, quantity and reason;
    a line's price is the accounts roles' alone (rulings 2026-10-12 R2 + R3),
    given here or later (PUT /{return_id}/prices). Until every line is priced
    the return has no total."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    # F21 IDOR fix: the caller must own the return's store (store-roles are pinned
    # to their own store; HQ roles pass). Without this a STORE_MANAGER could forge
    # store_id + RTV-link another store's QUARANTINED units (cross-store write).
    validate_store_access(return_data.store_id, current_user)
    # A price on a line records the supplier money the credit is built from:
    # the accounts roles' alone (R2 + R3). A manager sends none.
    if any(item.unit_price is not None for item in return_data.items):
        require_payables(current_user, "Pricing a vendor return")
    if return_data.stock_ids and db.get_collection("stock_units").find_one({
        "stock_id": {"$in": return_data.stock_ids},
        "store_id": return_data.store_id,
        "receipt_disposition": "REJECTED",
        "rtv_vendor_id": {"$ne": None},
    }):
        raise HTTPException(status_code=409, detail="Rejected receipt pieces already have a return draft. Open that return instead.")

    try:
        collection = db.get_collection("vendor_returns")
        return_id = generate_return_id()

        items = [
            {
                "product_id": item.product_id,
                "product_name": item.product_name,
                "quantity": item.quantity,
                "reason": item.reason,
                "unit_price": item.unit_price,
            }
            for item in return_data.items
        ]

        # Create return document
        return_doc = {
            "return_id": return_id,
            "vendor_id": return_data.vendor_id,
            "vendor_name": return_data.vendor_name,
            "store_id": return_data.store_id,
            "items": items,
            "return_type": return_data.return_type,
            "status": "created",
            "total_value": _priced_total(items),
            "credit_note_number": None,
            "credit_note_amount": None,
            "notes": return_data.notes or "",
            "created_at": utc_now_iso(),
            "created_by": current_user.get("user_id"),
            "status_history": [
                {
                    "status": "created",
                    "timestamp": utc_now_iso(),
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
        return_doc = _return_in_reach(db.get_collection("vendor_returns"), return_id, current_user)

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
        raise HTTPException(status_code=422, detail="Use Issue Credit to record the supplier's actual credit-note confirmation; an internal status change cannot post supplier credit.")
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    try:
        collection = db.get_collection("vendor_returns")
        return_doc = _return_in_reach(collection, return_id, current_user)
        if return_doc.get("credit_confirmation_fence"):
            raise HTTPException(status_code=409, detail="A supplier credit is being confirmed. Resolve that confirmation before changing the return.")
        # A return not typed 'replacement' is owed a credit: closing it as
        # 'replaced' ends it without that credit -- forgoing supplier money,
        # the accounts roles' (owner ruling 2026-10-12, R2). On a replacement
        # return, 'replaced' is the goods coming back: the managers'. Checked
        # before any write; return_type is fixed at create.
        if status_update.status == "replaced" and return_doc.get("return_type") != "replacement":
            require_payables(current_user, "Closing a credit-note return as replaced")
        # Valid status transitions
        current_status = return_doc.get("status")
        if current_status == "draft":
            if status_update.status == "created" and not status_update.return_type:
                raise HTTPException(status_code=422, detail="Choose credit or replacement before confirming this return.")
        elif status_update.return_type is not None:
            raise HTTPException(status_code=409, detail="A confirmed return keeps its credit or replacement choice.")
        valid_transitions = {
            "draft": ["created", "cancelled"],
            "created": ["approved", "cancelled"],
            "approved": ["shipped", "cancelled"],
            "shipped": ["received_by_vendor"],
            "received_by_vendor": ["replaced"],
            "credit_issued": [],
            "replaced": [],
            "cancelled": [],
        }
        if return_doc.get("receipt_rejection"):
            valid_transitions["cancelled"] = ["draft"]

        if status_update.status not in valid_transitions.get(current_status, []):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from {current_status} to {status_update.status}",
            )

        # Update document
        update_dict = {
            "status": status_update.status,
            "updated_at": utc_now_iso(),
            "updated_by": current_user.get("user_id"),
        }
        if current_status == "draft" and status_update.status == "created":
            update_dict["return_type"] = status_update.return_type
        if return_doc.get("receipt_rejection") and status_update.status == "cancelled":
            update_dict["debit_note_draft_status"] = "CANCELLED"
        if return_doc.get("receipt_rejection") and status_update.status == "draft":
            update_dict["debit_note_draft_status"] = "DRAFT"

        # N9: persist courier/AWB tracking on the "shipped" transition (and allow
        # a later correction while still in transit). Surfaced on GET so staff
        # can see where the in-transit RMA physically is.
        if status_update.status in ("shipped", "received_by_vendor"):
            for field_name in ("courier_name", "tracking_number", "tracking_url"):
                value = getattr(status_update, field_name, None)
                if value is not None and str(value).strip():
                    update_dict[field_name] = str(value).strip()
            if status_update.status == "shipped" and not return_doc.get("shipped_at"):
                update_dict["shipped_at"] = utc_now_iso()

        # Add status to history
        status_history = return_doc.get("status_history", [])
        history_entry = {
            "status": status_update.status,
            "timestamp": utc_now_iso(),
            "changed_by": current_user.get("user_id"),
            "notes": status_update.notes or "",
        }
        if "return_type" in update_dict:
            history_entry["return_type"] = update_dict["return_type"]
        if update_dict.get("tracking_number"):
            history_entry["tracking_number"] = update_dict["tracking_number"]
            history_entry["courier_name"] = update_dict.get("courier_name", "")
        status_history.append(history_entry)
        update_dict["status_history"] = status_history

        # Compare-and-swap on status: the CURRENT status is baked into the filter
        # so two concurrent transitions out of the same state can't both win. The
        # loser matches zero docs -> 409, so a credit note is issued (and the
        # status advanced) exactly once. The credit-note number generated above is
        # pure (no side effect) and is simply discarded by the loser.
        expected = {"return_id": return_id, "status": current_status,
                    "credit_confirmation_fence": {"$exists": False}}
        if return_doc.get("receipt_rejection") and status_update.status in ("cancelled", "draft"):
            from ..services.receipt_return_issue import require_unissued, unissued_filter
            require_unissued(db, return_doc)
            expected.update(unissued_filter())
        result = collection.update_one(
            expected,
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

        # A manager may move a return on, and type its price at approval, but
        # what we pay is read back by the accounts roles only (owner ruling
        # 2026-10-12).
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


# Once the credit is recorded (or the return closed) its prices are what the
# books carry; so are they once the GST debit note was issued at them.
_PRICES_FIXED_STATUSES = frozenset({"credit_issued", "replaced", "cancelled"})


@router.put("/{return_id}/prices")
async def price_vendor_return(
    return_id: str,
    body: ReturnPrices,
    current_user: dict = Depends(require_roles(*AP_ROLES)),
):
    """Set the price per piece of each of a return's lines -- the basis of
    the supplier's credit and of the GST debit note. The accounts roles only
    (owner rulings 2026-10-12 R2 + R3: a return's price is supplier money, the
    accounts roles' to write and to read); a manager raises the return without
    it. Refused once the credit is recorded, the return closed, or the debit
    note issued at the old prices."""
    require_payables(current_user, "Pricing a vendor return")
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    collection = db.get_collection("vendor_returns")
    return_doc = _return_in_reach(collection, return_id, current_user)
    if return_doc.get("credit_confirmation_fence") or return_doc.get("credit_confirmation_locked"):
        raise HTTPException(status_code=409, detail="Supplier confirmation has fixed this return's price evidence.")

    items = return_doc.get("items") or []
    if len(body.unit_prices) != len(items):
        raise HTTPException(
            status_code=422,
            detail=f"Send one price for each of the return's {len(items)} line(s).",
        )
    current_status = return_doc.get("status")
    if current_status in _PRICES_FIXED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"A return that is {current_status} keeps the prices it was settled at.",
        )
    expected = {"return_id": return_id, "status": current_status,
                "credit_confirmation_fence": {"$exists": False}, "credit_confirmation_locked": {"$ne": True}}
    if return_doc.get("receipt_rejection"):
        from ..services.receipt_return_issue import require_unissued, unissued_filter
        require_unissued(db, return_doc)
        expected.update(unissued_filter())
    elif debit_note_issued(db, return_id):
        raise HTTPException(
            status_code=409,
            detail="The GST debit note was issued at this return's prices; they stay.",
        )

    priced = [dict(it, unit_price=float(price)) for it, price in zip(items, body.unit_prices)]
    now = utc_now_iso()
    # Compare-and-swap on the status just read: a credit issued in between
    # makes this a 409, never a re-price under a recorded credit.
    result = collection.update_one(
        expected,
        {"$set": {
            "items": priced,
            "total_value": _priced_total(priced),
            "priced_by": current_user.get("user_id"),
            "priced_at": now,
            "updated_at": now,
            "updated_by": current_user.get("user_id"),
        }},
    )
    matched = getattr(result, "matched_count", None)
    if matched == 0:
        raise HTTPException(status_code=409, detail="Return was updated by another request")
    updated = collection.find_one({"return_id": return_id}) or {}
    updated.pop("_id", None)
    return {"message": "Return prices set", "return": mask_vendor_return(updated, current_user)}


def _credit_scope(current_user):
    cross_shop, shops = user_store_scope(current_user)
    return None if cross_shop else shops


@router.post("/{return_id}/credit-preview")
async def preview_supplier_credit(
    return_id: str, body: SupplierCreditPreview,
    current_user: dict = Depends(require_roles(*AP_ROLES)),
):
    """Accounts-only original evidence and actual supplier-note history."""
    require_payables(current_user, "Previewing supplier credit")
    from ..services.supplier_credit_evidence import SupplierCreditError, credit_preview

    db = _get_db()
    if db is None:
        raise HTTPException(503, "Database not available")
    return_doc = _return_in_reach(db.get_collection("vendor_returns"), return_id, current_user)
    try:
        choices = None if body.source_selections is None else [s.model_dump() for s in body.source_selections]
        return credit_preview(db, return_doc, selections=choices,
                              confirmation_id=str(body.confirmation_id) if body.confirmation_id else None,
                              allowed_store_ids=_credit_scope(current_user))
    except SupplierCreditError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Supplier credit evidence could not be verified. Retry the preview.") from exc


@router.post("/{return_id}/credit-confirmations")
async def confirm_supplier_credit(
    return_id: str, body: SupplierCreditConfirmation,
    current_user: dict = Depends(require_roles(*AP_ROLES)),
):
    """One actual supplier note is wholly pending or one immutable POSTED fact."""
    require_payables(current_user, "Confirming supplier credit")
    from ..services.supplier_credit_confirmation import record_supplier_confirmation
    from ..services.supplier_credit_evidence import SupplierCreditError, confirmation_view, credit_preview

    db = _get_db()
    if db is None:
        raise HTTPException(503, "Database not available")
    return_doc = _return_in_reach(db.get_collection("vendor_returns"), return_id, current_user)
    try:
        event, repeated = record_supplier_confirmation(
            db, return_doc, body.model_dump(mode="json"), actor_id=current_user.get("user_id"),
            allowed_store_ids=_credit_scope(current_user),
        )
        updated = db.get_collection("vendor_returns").find_one({"return_id": return_id}) or {}
        updated.pop("_id", None)
        preview = credit_preview(db, updated, selections=event.get("source_selections"), allowed_store_ids=_credit_scope(current_user))
        state = ("PENDING_EVIDENCE" if event["posting_status"] != "POSTED" else
                 "CONFIRMED" if preview["remaining_amount"] is not None and preview["remaining_amount"] <= 0 else "PARTIAL")
        return {"idempotent": repeated, "confirmation": confirmation_view(event),
                "return": mask_vendor_return(updated, current_user),
                "summary": {"posted_amount": preview["posted_amount"], "remaining_amount": preview["remaining_amount"],
                            "settlement_status": state}}
    except SupplierCreditError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "The supplier confirmation result could not be verified. Retry the same request unchanged.") from exc
