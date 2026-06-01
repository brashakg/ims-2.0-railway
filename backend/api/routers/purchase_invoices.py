"""
IMS 2.0 - Purchase Invoices Router  (Phase 1)
=============================================
A FIRST-CLASS purchase invoice: the vendor's tax invoice recorded with LINE
ITEMS (HSN + per-rate GST split) that books BOTH the accounts-payable (AP)
liability AND the input-tax-credit (ITC) ledger from a PO + GRN -- and FIXES the
inter-state classification bug (place_of_supply was read by the ITC code but
written nowhere, so inter-state purchases were mis-booked CGST+SGST not IGST).

Mounted at ``/api/v1/vendors/purchase-invoices`` and registered BEFORE the
vendors router in main.py so its concrete paths win over the vendors
``GET /{vendor_id}`` catch-all (same route-order discipline as the PO/GRN
endpoints inside vendors.py).

Storage: the SAME ``vendor_bills`` collection AP aging + the ITC register /
GSTR-2B reconcile already read. The header fields a header-only bill carries
(bill_id / vendor_id / bill_number / bill_date / due_date / taxable_amount /
tax_amount / total_amount / outstanding / status / place_of_supply) are written
identically, so NO existing read path changes; ``lines`` + the CGST/SGST/IGST
split totals + ``doc_type:"PURCHASE_INVOICE"`` are a strict, additive superset.

Money math + the inter-state vs intra-state decision live in the pure,
DB-free services/purchase_invoice_engine.py; AP due-date + duplicate-guard reuse
services/ap_engine.py. Booking is audited via get_audit_repository().create.

Roles: create / book is an accounting action -> ADMIN / ACCOUNTANT (+SUPERADMIN
via require_roles). Reads are AUTHENTICATED.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import (
    get_vendor_repository,
    get_purchase_order_repository,
    get_grn_repository,
    get_audit_repository,
)
from ..services import ap_engine
from ..services import purchase_invoice_engine as pinv

router = APIRouter()

# Money-out / books action: limited to ADMIN / ACCOUNTANT. SUPERADMIN auto-passes
# via require_roles. Mirrors the _AP_ROLES gate on vendor bills/payments.
_AP_ROLES = ("ADMIN", "ACCOUNTANT")


def _get_db():
    """Direct DB handle for vendor_bills / entities / vendors (the AP + ITC
    collections), matching vendors.py + finance.py. Fail-soft (mock mode ->
    get_db().db may be a mock; None when unavailable)."""
    from database.connection import get_db

    return get_db().db


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PurchaseInvoiceLine(BaseModel):
    product_id: Optional[str] = None
    description: Optional[str] = None
    hsn: Optional[str] = None
    # qty * unit_price is used when `taxable` is omitted. A line must have a
    # non-negative quantity/price; the taxable value (if given) wins.
    qty: float = Field(0, ge=0)
    unit_price: float = Field(0, ge=0)
    taxable: Optional[float] = Field(default=None, ge=0)
    gst_rate: float = Field(0, ge=0, le=100)


class PurchaseInvoiceCreate(BaseModel):
    vendor_id: str
    invoice_number: str  # the vendor's own tax-invoice number (AP dup key)
    invoice_date: str  # ISO date (YYYY-MM-DD)
    lines: List[PurchaseInvoiceLine] = Field(..., min_length=1)
    po_id: Optional[str] = None
    grn_id: Optional[str] = None
    # Recipient (buyer) -- which of our legal entities is claiming the ITC.
    # Either the entity id (we resolve its GSTIN for the place-of-supply state)
    # or an explicit recipient GSTIN. Drives the IGST vs CGST/SGST decision.
    recipient_entity_id: Optional[str] = None
    recipient_gstin: Optional[str] = None
    # Optional 2-digit place-of-supply state override (else the recipient state).
    place_of_supply: Optional[str] = None
    tds: float = Field(0, ge=0)
    itc_eligible: bool = True
    notes: Optional[str] = None
    # Client-computed grand total, if provided, is reconciled against the
    # server-computed taxable+tax (Rs 1 slack) -- a tamper / drift guard.
    total: Optional[float] = Field(default=None, ge=0)


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "_id"}


# ---------------------------------------------------------------------------
# Recipient / supplier GSTIN resolution
# ---------------------------------------------------------------------------


def _entity_gstin_for_state(entity: dict, state_code: Optional[str]) -> Optional[str]:
    """Pick the entity GSTIN to use as the recipient.

    Prefer the GSTIN whose state matches `state_code` (a multi-GSTIN entity has
    one per state); else the primary GSTIN; else the first. Returns None when
    the entity has no GSTINs.
    """
    gstins = (entity or {}).get("gstins") or []
    if not isinstance(gstins, list) or not gstins:
        return None
    sc = (state_code or "").strip()
    if sc:
        for g in gstins:
            if isinstance(g, dict) and str(g.get("state_code") or "").strip() == sc:
                return g.get("gstin")
    for g in gstins:
        if isinstance(g, dict) and g.get("is_primary"):
            return g.get("gstin")
    first = gstins[0]
    return first.get("gstin") if isinstance(first, dict) else None


def _resolve_recipient(db, body_entity_id, body_gstin, body_pos) -> dict:
    """Resolve {recipient_entity_id, recipient_gstin} for an invoice.

    An explicit recipient_gstin always wins. Otherwise, when a recipient entity
    is given (or there is a single entity to default to), pick its GSTIN for the
    requested place-of-supply state. Fail-soft: returns whatever it can derive.
    """
    recipient_gstin = (body_gstin or "").strip().upper() or None
    entity_id = body_entity_id
    if recipient_gstin:
        return {"recipient_entity_id": entity_id, "recipient_gstin": recipient_gstin}
    if db is None:
        return {"recipient_entity_id": entity_id, "recipient_gstin": None}
    try:
        coll = db.get_collection("entities")
        entity = None
        if entity_id:
            entity = coll.find_one({"entity_id": entity_id}, {"_id": 0})
        else:
            # Default to the sole entity when there is exactly one, so a
            # single-entity client doesn't have to pass it every time.
            docs = list(coll.find({}, {"_id": 0}).limit(2))
            if len(docs) == 1:
                entity = docs[0]
                entity_id = entity.get("entity_id")
        if entity:
            recipient_gstin = _entity_gstin_for_state(entity, body_pos)
    except Exception:
        pass
    return {"recipient_entity_id": entity_id, "recipient_gstin": recipient_gstin}


def _vendor_gstin(db, vendor: Optional[dict], vendor_id: str) -> Optional[str]:
    """Supplier GSTIN from the vendor doc (fetched via repo or direct)."""
    if vendor and vendor.get("gstin"):
        return vendor.get("gstin")
    if db is None:
        return None
    try:
        v = db.get_collection("vendors").find_one(
            {"vendor_id": vendor_id}, {"_id": 0, "gstin": 1}
        )
        return (v or {}).get("gstin")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Create (book AP + ITC from lines, with IGST classification)
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_purchase_invoice(
    body: PurchaseInvoiceCreate,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Create a first-class purchase invoice: compute the per-line CGST/SGST vs
    IGST split from supplier-vs-recipient state, reconcile taxable+tax == total,
    and book it as an AP payable (due date from the vendor's credit terms, with
    a per-vendor duplicate-invoice guard). Writes ``place_of_supply`` so the ITC
    register classifies it correctly."""
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(body.vendor_id) if vendor_repo is not None else None
    if vendor_repo is not None and vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    db = _get_db()

    # Duplicate-invoice guard (application-level; mirrors create_vendor_bill).
    # The same vendor tax-invoice number must not be booked twice -- a double
    # entry would double the payable AND double-count the ITC.
    if db is not None:
        try:
            dup = db.get_collection("vendor_bills").find_one(
                {"vendor_id": body.vendor_id, "bill_number": body.invoice_number},
                {"_id": 0, "bill_id": 1},
            )
            if dup:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Invoice number '{body.invoice_number}' is already "
                        f"recorded for this vendor. Duplicate vendor invoices "
                        f"are not allowed."
                    ),
                )
        except HTTPException:
            raise
        except Exception:
            pass  # fail-soft: skip dup check on DB error, proceed

    supplier_gstin = _vendor_gstin(db, vendor, body.vendor_id)
    recipient = _resolve_recipient(
        db, body.recipient_entity_id, body.recipient_gstin, body.place_of_supply
    )

    computed = pinv.compute_invoice(
        [ln.model_dump() for ln in body.lines],
        supplier_gstin,
        recipient.get("recipient_gstin"),
        body.place_of_supply,
    )

    # Reconcile a client-supplied grand total against the server math.
    if body.total is not None and abs(body.total - computed["total"]) > 1.0:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invoice total {body.total} does not reconcile with the "
                f"computed taxable+tax {computed['total']}."
            ),
        )

    credit_days = int((vendor or {}).get("credit_days", 30) or 30)
    due_date = ap_engine.compute_due_date(body.invoice_date, credit_days)

    invoice_id = str(uuid.uuid4())
    taxable_total = computed["taxable_total"]
    tax_total = computed["tax_total"]
    total = computed["total"]

    doc = {
        "bill_id": invoice_id,
        "invoice_id": invoice_id,
        "doc_type": "PURCHASE_INVOICE",
        "vendor_id": body.vendor_id,
        "vendor_name": (vendor or {}).get("trade_name")
        or (vendor or {}).get("legal_name"),
        "vendor_gstin": supplier_gstin,
        "recipient_entity_id": recipient.get("recipient_entity_id"),
        "recipient_gstin": recipient.get("recipient_gstin"),
        # WRITE place_of_supply = the SUPPLIER (counterparty) state so the
        # existing itc_reconcile.build_itc_register test (place_of_supply vs the
        # recipient entity's primary state) fires IGST on inter-state buys. This
        # is THE FIX: the field used to be unwritten, so every inter-state
        # purchase was mis-classified intra-state (CGST+SGST). The legal
        # recipient-side place of supply is kept separately for display.
        "place_of_supply": computed["itc_place_of_supply"],
        "supply_place_recipient": computed["place_of_supply"],
        "supplier_state": computed["supplier_state"],
        "interstate": computed["interstate"],
        # bill_number is the canonical AP duplicate key (== invoice_number).
        "bill_number": body.invoice_number,
        "invoice_number": body.invoice_number,
        "bill_date": body.invoice_date,
        "invoice_date": body.invoice_date,
        "due_date": due_date,
        "credit_days": credit_days,
        "po_id": body.po_id,
        "grn_id": body.grn_id,
        "lines": computed["lines"],
        # Header money mirrors the split so the ITC register (taxable_amount /
        # tax_amount) AND the new GST report (cgst/sgst/igst_total) both read it.
        "taxable_amount": taxable_total,
        "tax_amount": tax_total,
        "taxable_total": taxable_total,
        "cgst_total": computed["cgst_total"],
        "sgst_total": computed["sgst_total"],
        "igst_total": computed["igst_total"],
        "total_amount": total,
        "total": total,
        "tds": round(body.tds, 2),
        "itc_eligible": bool(body.itc_eligible),
        "outstanding": total,
        "status": "OUTSTANDING",
        "notes": body.notes,
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }

    if db is not None:
        try:
            db.get_collection("vendor_bills").insert_one(dict(doc))
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail="Failed to save purchase invoice"
            ) from exc

    # Audit the booking (fail-soft -- never blocks the save).
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "purchase_invoice.create",
                    "entity_type": "vendor_bill",
                    "entity_id": invoice_id,
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "vendor_id": body.vendor_id,
                        "invoice_number": body.invoice_number,
                        "po_id": body.po_id,
                        "grn_id": body.grn_id,
                        "place_of_supply": computed["place_of_supply"],
                        "interstate": computed["interstate"],
                        "taxable_total": taxable_total,
                        "cgst_total": computed["cgst_total"],
                        "sgst_total": computed["sgst_total"],
                        "igst_total": computed["igst_total"],
                        "total": total,
                    },
                }
            )
    except Exception:
        pass

    return _clean(doc)


# ---------------------------------------------------------------------------
# List / get
# ---------------------------------------------------------------------------


@router.get("")
@router.get("/")
async def list_purchase_invoices(
    vendor_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="OUTSTANDING / PARTIAL / PAID"),
    unmatched: Optional[bool] = Query(
        None, description="true -> only invoices with no po_id/grn_id link"
    ),
    current_user: dict = Depends(get_current_user),
):
    """List first-class purchase invoices (doc_type=PURCHASE_INVOICE), newest
    first. Header-only legacy bills are excluded from this view. Filterable by
    vendor / status / unmatched (no PO or GRN link)."""
    db = _get_db()
    if db is None:
        return {"purchase_invoices": [], "total": 0}
    flt: dict = {"doc_type": "PURCHASE_INVOICE"}
    if vendor_id:
        flt["vendor_id"] = vendor_id
    if status:
        flt["status"] = status
    if unmatched is True:
        flt["po_id"] = None
        flt["grn_id"] = None
    try:
        rows = list(db.get_collection("vendor_bills").find(flt, {"_id": 0}))
    except Exception:
        rows = []
    rows.sort(key=lambda r: r.get("invoice_date") or r.get("bill_date") or "", reverse=True)
    return {"purchase_invoices": rows, "total": len(rows)}


@router.get("/from-grn/{grn_id}")
async def draft_invoice_from_grn(
    grn_id: str,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Build a DRAFT purchase invoice from a GRN -- does NOT persist or book.

    Loads the GRN (for its vendor invoice no/date + po_id + accepted lines) and
    its PO (for unit_price + tax_rate per line), computes the place_of_supply +
    per-line CGST/SGST vs IGST split, and returns the prefilled draft for the
    user to review and POST. The returned `lines` + totals + place_of_supply are
    ready to drop into POST / (the create body) after review.
    """
    grn_repo = get_grn_repository()
    po_repo = get_purchase_order_repository()
    grn = grn_repo.find_by_id(grn_id) if grn_repo is not None else None
    if grn_repo is not None and grn is None:
        raise HTTPException(status_code=404, detail="GRN not found")
    grn = grn or {}

    po = None
    po_id = grn.get("po_id")
    if po_repo is not None and po_id:
        po = po_repo.find_by_id(po_id)

    db = _get_db()
    vendor_id = grn.get("vendor_id") or (po or {}).get("vendor_id")
    vendor = None
    vendor_repo = get_vendor_repository()
    if vendor_repo is not None and vendor_id:
        vendor = vendor_repo.find_by_id(vendor_id)

    supplier_gstin = _vendor_gstin(db, vendor, vendor_id) if vendor_id else None
    # Default the recipient to the entity that owns the receiving store.
    recipient_entity_id = None
    try:
        if db is not None and grn.get("store_id"):
            store = db.get_collection("stores").find_one(
                {"store_id": grn.get("store_id")}, {"_id": 0, "entity_id": 1}
            )
            recipient_entity_id = (store or {}).get("entity_id")
    except Exception:
        recipient_entity_id = None
    recipient = _resolve_recipient(db, recipient_entity_id, None, None)

    raw_lines = pinv.lines_from_grn(grn, po)
    computed = pinv.compute_invoice(
        raw_lines, supplier_gstin, recipient.get("recipient_gstin"), None
    )

    return {
        "status": "DRAFT",
        "vendor_id": vendor_id,
        "vendor_name": grn.get("vendor_name")
        or (vendor or {}).get("trade_name")
        or (vendor or {}).get("legal_name"),
        "vendor_gstin": supplier_gstin,
        "recipient_entity_id": recipient.get("recipient_entity_id"),
        "recipient_gstin": recipient.get("recipient_gstin"),
        # place_of_supply here mirrors what POST will STORE (the supplier state,
        # which the ITC register keys on); supply_place_recipient is the legal
        # recipient side for display. interstate is the human-readable verdict.
        "place_of_supply": computed["itc_place_of_supply"],
        "supply_place_recipient": computed["place_of_supply"],
        "supplier_state": computed["supplier_state"],
        "interstate": computed["interstate"],
        # Prefill the vendor's own invoice no/date captured on the GRN.
        "invoice_number": grn.get("vendor_invoice_no"),
        "invoice_date": grn.get("vendor_invoice_date"),
        "po_id": po_id,
        "grn_id": grn_id,
        "grn_number": grn.get("grn_number"),
        "lines": computed["lines"],
        "taxable_total": computed["taxable_total"],
        "cgst_total": computed["cgst_total"],
        "sgst_total": computed["sgst_total"],
        "igst_total": computed["igst_total"],
        "tax_total": computed["tax_total"],
        "total": computed["total"],
    }


@router.get("/{invoice_id}")
async def get_purchase_invoice(
    invoice_id: str, current_user: dict = Depends(get_current_user)
):
    """Get one purchase invoice by id (looks up the vendor_bills row)."""
    db = _get_db()
    if db is None:
        return {"invoice_id": invoice_id}
    try:
        doc = db.get_collection("vendor_bills").find_one(
            {"bill_id": invoice_id}, {"_id": 0}
        )
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")
    return doc
