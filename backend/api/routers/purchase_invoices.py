"""
IMS 2.0 - Purchase Invoices Router  (Phase 1 + Phase 2)
=======================================================
A FIRST-CLASS purchase invoice: the vendor's tax invoice recorded with LINE
ITEMS (HSN + per-rate GST split) that books BOTH the accounts-payable (AP)
liability AND the input-tax-credit (ITC) ledger from a PO + GRN -- and FIXES the
inter-state classification bug (place_of_supply was read by the ITC code but
written nowhere, so inter-state purchases were mis-booked CGST+SGST not IGST).

PHASE 2 adds the procure-to-pay CONTROL on top of Phase 1:
  * 3-WAY MATCH -- when an invoice carries po_id + grn_id, compare PO (ordered
    qty/price) vs GRN (accepted qty) vs invoice (billed qty/price) per product
    via services/purchase_match.three_way_match. The verdict (MATCHED /
    ON_HOLD_EXCEPTION + per-line reasons) is stored on the invoice doc.
    GET /{id}/match returns the detail; POST /{id}/approve-exception lets an
    ADMIN/ACCOUNTANT override a hold to MATCHED_OVERRIDE (audited).
  * INVENTORY VALUATION TRUE-UP -- on booking, the invoice's per-unit landed
    price trues up the product's moving-average cost (services/purchase_match.
    valuation_trueup_for_invoice). Fail-soft: a valuation write NEVER blocks the
    booking. (GRN acceptance in vendors.py provisionally stamps the PO price as
    unit_cost on each minted stock_unit; the invoice is the authoritative cost.)
  * CONFIG -- GET/PUT /config exposes valuation_method (MOVING_AVERAGE default,
    alt FIFO) + match_tolerance_pct (default 5), stored in a single
    ``purchase_settings`` doc with safe defaults when unset.

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
from ..services import purchase_match as pmatch

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
    # GST reverse charge (RCM): when True this is an inward supply on which the
    # BUYER is liable to pay GST (GSTR-3B Table 3.1(d)) -- e.g. unregistered-
    # supplier purchases, GTA freight, legal/advocate services.
    reverse_charge: bool = False
    notes: Optional[str] = None
    # Client-computed grand total, if provided, is reconciled against the
    # server-computed taxable+tax (Rs 1 slack) -- a tamper / drift guard.
    total: Optional[float] = Field(default=None, ge=0)


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "_id"}


# ---------------------------------------------------------------------------
# Phase 2 config (valuation_method + match_tolerance_pct) -- single settings doc
# ---------------------------------------------------------------------------

# All Phase-2 purchase config lives in ONE doc so the resolver is a single read.
_PURCHASE_SETTINGS_COLL = "purchase_settings"
_PURCHASE_SETTINGS_ID = "default"


def _read_purchase_settings(db) -> Optional[dict]:
    """Fetch the raw purchase_settings doc (or None). Fail-soft -- any DB error
    returns None so resolve_config falls back to the static defaults."""
    if db is None:
        return None
    try:
        doc = db.get_collection(_PURCHASE_SETTINGS_COLL).find_one(
            {"_id": _PURCHASE_SETTINGS_ID}, {"_id": 0}
        )
        return doc
    except Exception:
        return None


def _resolved_purchase_config(db) -> dict:
    """Effective {valuation_method, match_tolerance_pct} with safe defaults."""
    return pmatch.resolve_config(_read_purchase_settings(db))


class PurchaseConfigUpdate(BaseModel):
    valuation_method: Optional[str] = None  # MOVING_AVERAGE | FIFO
    match_tolerance_pct: Optional[float] = Field(default=None, ge=0, le=100)


# ---------------------------------------------------------------------------
# Phase 2 match-on-book + valuation true-up (fail-soft; never block the booking)
# ---------------------------------------------------------------------------


def _run_match_for_invoice(db, po_id, grn_id, computed_lines, tolerance_pct):
    """Fetch the PO + GRN and run the 3-way match against the computed invoice
    lines. Returns the match dict, or None when there is no PO/GRN to match
    against (a manual invoice with no link). Fail-soft -- a fetch/compute error
    returns None so booking proceeds (the invoice is simply unmatched)."""
    if not (po_id or grn_id):
        return None
    try:
        po = None
        grn = None
        po_repo = get_purchase_order_repository()
        grn_repo = get_grn_repository()
        if po_id and po_repo is not None:
            po = po_repo.find_by_id(po_id)
        if grn_id and grn_repo is not None:
            grn = grn_repo.find_by_id(grn_id)
        # Need at least one comparison doc to make a meaningful verdict.
        if po is None and grn is None:
            return None
        return pmatch.three_way_match(po, grn, computed_lines, tolerance_pct)
    except Exception:
        return None


def _product_state_for_valuation(db, product_ids, store_id=None) -> dict:
    """Build {product_id: {on_hand_qty, cost_price}} for the moving-average
    true-up: the CURRENT on-hand quantity (count of AVAILABLE serialized
    stock_units) and current cost (product cost_price / landed_cost) per
    product. Fail-soft: any error yields an empty/partial map (the true-up then
    treats missing products as zero on-hand at zero cost -> takes the invoice
    cost, which is the correct first-receipt behaviour)."""
    state: dict = {}
    if db is None or not product_ids:
        return state
    pids = [p for p in product_ids if p]
    # Current cost per product from the product master.
    try:
        for p in db.get_collection("products").find(
            {"product_id": {"$in": pids}},
            {"_id": 0, "product_id": 1, "cost_price": 1, "landed_cost": 1},
        ):
            pid = p.get("product_id")
            if pid is None:
                continue
            state[pid] = {
                "cost_price": (
                    p.get("cost_price")
                    if p.get("cost_price") is not None
                    else p.get("landed_cost")
                ),
                "on_hand_qty": 0,
            }
    except Exception:
        pass
    # Current on-hand qty from the canonical serialized stock_units collection.
    try:
        coll = db.get_collection("stock_units")
        for pid in pids:
            flt = {"product_id": pid, "status": "AVAILABLE"}
            if store_id:
                flt["store_id"] = store_id
            try:
                cnt = coll.count_documents(flt)
            except Exception:
                # very old pymongo / fake: fall back to count()
                cnt = coll.count(flt) if hasattr(coll, "count") else 0
            state.setdefault(pid, {"cost_price": None, "on_hand_qty": 0})
            state[pid]["on_hand_qty"] = int(cnt or 0)
    except Exception:
        pass
    return state


def _apply_valuation_trueup(db, invoice_doc, computed, config) -> Optional[list]:
    """Update each invoiced product's moving-average cost from the invoice's
    per-unit landed price. Returns the list of applied updates (for the audit
    detail), or None when nothing was done. STRICTLY fail-soft -- any error is
    swallowed; a valuation update must NEVER roll back or block the booking."""
    try:
        lines = computed.get("lines") or []
        pids = [ln.get("product_id") for ln in lines if ln.get("product_id")]
        if db is None or not pids:
            return None
        store_id = None
        # Prefer the receiving store from the linked GRN for the on-hand scope.
        grn_id = invoice_doc.get("grn_id")
        if grn_id:
            try:
                grn = db.get_collection("grns").find_one(
                    {"grn_id": grn_id}, {"_id": 0, "store_id": 1}
                )
                store_id = (grn or {}).get("store_id")
            except Exception:
                store_id = None
        product_state = _product_state_for_valuation(db, pids, store_id)
        updates = pmatch.valuation_trueup_for_invoice(
            lines, product_state, config.get("valuation_method")
        )
        if not updates:
            return None
        products = db.get_collection("products")
        applied = []
        for u in updates:
            pid = u.get("product_id")
            new_cost = u.get("new_cost")
            if pid is None or new_cost is None:
                continue
            try:
                products.update_one(
                    {"product_id": pid},
                    {
                        "$set": {
                            "cost_price": new_cost,
                            "moving_avg_cost": new_cost,
                            "valuation_method": u.get("method"),
                            "cost_updated_at": datetime.now().isoformat(),
                            "cost_source": "PURCHASE_INVOICE",
                            "cost_source_id": invoice_doc.get("invoice_id"),
                        }
                    },
                )
                applied.append(u)
            except Exception:
                continue
        return applied or None
    except Exception:
        return None


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

    # Phase 2 -- 3-WAY MATCH: when the invoice links a PO + GRN, compare ordered
    # vs received vs invoiced (qty + price) and stamp the verdict. An out-of-
    # tolerance line puts the invoice ON_HOLD_EXCEPTION (it still books as a
    # payable -- the hold is a review flag, not a hard block, so the liability is
    # recorded; an ADMIN/ACCOUNTANT clears it via /approve-exception). No PO/GRN
    # link -> no match (match_status None = a manual/unmatched invoice).
    config = _resolved_purchase_config(db)
    match = _run_match_for_invoice(
        db, body.po_id, body.grn_id, computed["lines"], config["match_tolerance_pct"]
    )
    match_status = match.get("match_status") if match else None

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
        # Phase 2 -- 3-way match verdict + full per-line detail (None when the
        # invoice has no PO/GRN to match against).
        "match_status": match_status,
        "match_detail": match,
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
        "reverse_charge": bool(body.reverse_charge),
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

    # Phase 2 -- INVENTORY VALUATION TRUE-UP. The invoice's per-unit landed price
    # is the authoritative cost; blend it into each product's moving-average cost
    # (or record the latest layer under FIFO). STRICTLY fail-soft: the helper
    # swallows all errors and is called AFTER the booking insert so a valuation
    # write can never roll back or block the recorded payable.
    valuation_updates = _apply_valuation_trueup(db, doc, computed, config)

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
                        "match_status": match_status,
                        "match_exceptions": (match or {}).get("exceptions"),
                        "valuation_method": config.get("valuation_method"),
                        "valuation_updates": valuation_updates,
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
    rows.sort(
        key=lambda r: r.get("invoice_date") or r.get("bill_date") or "", reverse=True
    )
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


# ---------------------------------------------------------------------------
# Phase 2 -- config (valuation method + match tolerance)
# ---------------------------------------------------------------------------
# Registered BEFORE the GET /{invoice_id} catch-all so the literal /config path
# wins over the {invoice_id} param (same route-order discipline as from-grn).


@router.get("/config")
async def get_purchase_config(current_user: dict = Depends(get_current_user)):
    """Effective purchase config: valuation_method (MOVING_AVERAGE | FIFO) +
    match_tolerance_pct. Returns the stored override merged over safe defaults
    (so the response is always complete + valid even with no settings doc)."""
    cfg = _resolved_purchase_config(_get_db())
    return {
        "config": cfg,
        "defaults": {
            "valuation_method": pmatch.DEFAULT_VALUATION_METHOD,
            "match_tolerance_pct": pmatch.DEFAULT_MATCH_TOLERANCE_PCT,
        },
        "valuation_methods": list(pmatch.VALID_VALUATION_METHODS),
    }


@router.put("/config")
async def update_purchase_config(
    body: PurchaseConfigUpdate,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Update the purchase config (ADMIN / ACCOUNTANT). Only provided fields are
    changed; values are normalised to a valid method / clamped tolerance before
    persisting. Audited."""
    db = _get_db()
    current = _resolved_purchase_config(db)
    payload = dict(current)
    if body.valuation_method is not None:
        payload["valuation_method"] = pmatch.normalize_valuation_method(
            body.valuation_method
        )
    if body.match_tolerance_pct is not None:
        payload["match_tolerance_pct"] = pmatch.normalize_tolerance_pct(
            body.match_tolerance_pct
        )

    if db is not None:
        try:
            db.get_collection(_PURCHASE_SETTINGS_COLL).update_one(
                {"_id": _PURCHASE_SETTINGS_ID},
                {
                    "$set": {
                        **payload,
                        "updated_by": current_user.get("user_id"),
                        "updated_at": datetime.now().isoformat(),
                    }
                },
                upsert=True,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail="Failed to save purchase config"
            ) from exc

    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "purchase_config.update",
                    "entity_type": "purchase_settings",
                    "entity_id": _PURCHASE_SETTINGS_ID,
                    "user_id": current_user.get("user_id"),
                    "detail": {"from": current, "to": payload},
                }
            )
    except Exception:
        pass

    return {"message": "Purchase config updated", "config": payload}


# ---------------------------------------------------------------------------
# Phase 2 -- 3-way match detail + exception override
# ---------------------------------------------------------------------------


@router.get("/{invoice_id}/match")
async def get_invoice_match(
    invoice_id: str, current_user: dict = Depends(get_current_user)
):
    """Return the stored 3-way match detail for an invoice.

    If the invoice was booked before a match was run, or had no PO/GRN link,
    match_detail is None and match_status reflects that (None / the stored
    verdict). Re-computes on the fly from the linked PO/GRN when the stored
    detail is absent but a link exists (so an older invoice still answers)."""
    db = _get_db()
    if db is None:
        return {"invoice_id": invoice_id, "match_status": None, "match_detail": None}
    try:
        doc = db.get_collection("vendor_bills").find_one(
            {"bill_id": invoice_id}, {"_id": 0}
        )
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")

    detail = doc.get("match_detail")
    status = doc.get("match_status")
    if detail is None and (doc.get("po_id") or doc.get("grn_id")):
        # Lazily recompute for invoices booked before Phase 2 (or where the
        # stored detail was dropped). Read-only -- does not persist.
        cfg = _resolved_purchase_config(db)
        detail = _run_match_for_invoice(
            db,
            doc.get("po_id"),
            doc.get("grn_id"),
            doc.get("lines") or [],
            cfg["match_tolerance_pct"],
        )
        if detail and status is None:
            status = detail.get("match_status")

    return {
        "invoice_id": invoice_id,
        "match_status": status,
        "match_detail": detail,
        "po_id": doc.get("po_id"),
        "grn_id": doc.get("grn_id"),
    }


class ExceptionOverride(BaseModel):
    reason: str = Field(..., min_length=1)


@router.post("/{invoice_id}/approve-exception")
async def approve_invoice_exception(
    invoice_id: str,
    body: ExceptionOverride,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """ADMIN / ACCOUNTANT override of an ON_HOLD_EXCEPTION 3-way match.

    Flips match_status to MATCHED_OVERRIDE with the approver + reason recorded on
    the invoice (the original match_detail is preserved for the audit trail).
    Idempotent-ish: only an ON_HOLD_EXCEPTION can be approved -- a clean MATCHED
    invoice has nothing to override (400), and a missing invoice 404s."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        doc = db.get_collection("vendor_bills").find_one(
            {"bill_id": invoice_id}, {"_id": 0}
        )
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")

    if doc.get("match_status") != pmatch.MATCH_ON_HOLD:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only an invoice on ON_HOLD_EXCEPTION can be exception-approved "
                f"(current match_status: {doc.get('match_status')})."
            ),
        )

    override = {
        "match_status": pmatch.MATCH_OVERRIDE,
        "exception_override": {
            "approved_by": current_user.get("user_id"),
            "reason": body.reason,
            "approved_at": datetime.now().isoformat(),
            "prior_status": pmatch.MATCH_ON_HOLD,
        },
    }
    try:
        db.get_collection("vendor_bills").update_one(
            {"bill_id": invoice_id}, {"$set": override}
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail="Failed to record exception override"
        ) from exc

    # Audit the override (this is a control bypass -- it MUST be recorded; the
    # write above is the source of truth, the audit is best-effort on top).
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "purchase_invoice.approve_exception",
                    "entity_type": "vendor_bill",
                    "entity_id": invoice_id,
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "reason": body.reason,
                        "prior_status": pmatch.MATCH_ON_HOLD,
                        "new_status": pmatch.MATCH_OVERRIDE,
                        "match_exceptions": (doc.get("match_detail") or {}).get(
                            "exceptions"
                        ),
                    },
                }
            )
    except Exception:
        pass

    return {
        "invoice_id": invoice_id,
        "match_status": pmatch.MATCH_OVERRIDE,
        "exception_override": override["exception_override"],
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
