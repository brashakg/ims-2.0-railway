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

F19 adds the DYNAMIC LANDED-COST PURCHASE MATRIX on top: freight / duty /
customs / forex / insurance / other components are captured per bill in integer
paise, previewed, then allocated ONE-WAY across the bill's lines (paise-exact;
math in services/landed_cost.py). Allocation flips ``landed_cost_allocated``
under a guarded single-document find_one_and_update (loser 409s) after which
the components are immutable. The per-line landed unit cost is persisted on the
bill lines and rolled into the product master as ``landed_cost`` /
``landed_cost_paise`` -- the moving-average ``cost_price`` writer is NOT
re-invoked (see allocate_invoice_landed_costs for why).

Roles: create / book is an accounting action -> ADMIN / ACCOUNTANT (+SUPERADMIN
via require_roles). Reads are AUTHENTICATED.
"""

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from .auth import get_current_user, require_roles
from ..dependencies import (
    get_vendor_repository,
    get_purchase_order_repository,
    get_grn_repository,
    get_audit_repository,
)
from ..services import ap_engine
from ..services import landed_cost as lc
from ..services import purchase_invoice_engine as pinv
from ..services import purchase_match as pmatch
from ..services import product_master as _pm

router = APIRouter()
logger = logging.getLogger(__name__)

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
    # Ruling 12: the prices on the INVOICE are the actual ones. unit_price is
    # the actual cost (the true-up already treats it as authoritative); `mrp` is
    # the actual retail price printed on the vendor's bill. Optional -- an
    # invoice that does not restate the MRP leaves the product's alone.
    mrp: Optional[float] = Field(default=None, gt=0)


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
    # F9: grn_ids of the Delivery Challans this ONE consolidated invoice covers.
    # When present, the booking runs dc_bulk_match (DC-received vs billed qty)
    # and flips dc_matched=true on each linked DC.
    linked_dc_ids: Optional[List[str]] = None
    # What this bill is FOR: "GOODS" or "SERVICES". Free-text lines carry no
    # product_id, so the goods trigger below cannot see them -- the manual form
    # booked "20 pcs assorted frames" as prose with no receipt. With no receipt
    # linked the caller must now declare the kind; GOODS demands the receipt,
    # SERVICES books as before. Kept Optional in the SCHEMA so the handler can
    # answer with a stable plain-English 422 (BILL_KIND_REQUIRED), and so a
    # receipt-linked booking (from-GRN / from-DCs) needs no declaration.
    bill_kind: Optional[str] = None

    @field_validator("bill_kind", mode="before")
    @classmethod
    def _normalize_bill_kind(cls, v):
        return ap_engine.normalize_bill_kind(v)

    @field_validator("invoice_number", mode="before")
    @classmethod
    def _strip_invoice_number(cls, v):
        """F4: trim surrounding whitespace so '  INV-12  ' and 'INV-12' are the
        SAME vendor invoice for the duplicate guard + unique index (a stray space
        must not create a phantom distinct bill). Case is preserved -- vendor
        invoice numbers can be case-significant."""
        return v.strip() if isinstance(v, str) else v


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


# F9: Delivery-Challan subtype string (kept here to avoid a cross-router import
# cycle with vendors.py; the canonical constant lives in vendors.GRN_SUBTYPE_DC).
GRN_SUBTYPE_DC = "DELIVERY_CHALLAN"


def _load_linked_dcs(db, dc_ids):
    """Load the DC (Delivery-Challan GRN) docs for the given grn_ids and verify
    each is a usable, still-open DC. Returns the list of DC docs.

    Raises HTTPException (404 / 409 / 400) if any id is missing, is not a DC, is
    not ACCEPTED, or is already matched to another bulk invoice -- so a DC can
    never be double-billed. Fail-soft only on db-None (returns []).
    """
    if db is None or not dc_ids:
        return []
    coll = db.get_collection("grns")
    docs = []
    for dc_id in dc_ids:
        try:
            doc = coll.find_one({"grn_id": dc_id}, {"_id": 0})
        except Exception:
            doc = None
        if not doc:
            raise HTTPException(status_code=404, detail=f"DC {dc_id} not found")
        if doc.get("grn_subtype") != GRN_SUBTYPE_DC:
            raise HTTPException(
                status_code=400, detail=f"{dc_id} is not a Delivery Challan"
            )
        if doc.get("status") != "ACCEPTED":
            raise HTTPException(
                status_code=400,
                detail=f"DC {dc_id} must be ACCEPTED before it can be billed",
            )
        if doc.get("dc_matched"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"DC {dc_id} is already matched to invoice "
                    f"{doc.get('linked_bulk_invoice_id')}"
                ),
            )
        docs.append(doc)
    return docs


def _load_standard_grn(grn_id, expected_vendor_id=None):
    """Load a STANDARD (PO-backed) GRN and verify it is ACCEPTED -- and that it
    belongs to the vendor being billed -- before it can be billed. The mirror of
    the DC guards in `_load_linked_dcs` + `_assert_dcs_single_vendor_store`.

    A GRN only mints stock at accept time, so booking a purchase invoice against
    a PENDING / PARTIALLY_ACCEPTED GRN would record a payable (and its ITC) for
    goods not yet accepted into stock. The frontend already filters to ACCEPTED
    GRNs; this is the server-side enforcement.

    ``expected_vendor_id`` (the vendor the bill is being booked against) is
    cross-checked against the GRN's own vendor_id -- WITHOUT it, a receipt from
    vendor B could be billed under vendor A: A's payable rises, A's GSTIN claims
    the ITC, and B's goods stay unbilled. The DC path has blocked exactly this
    since F9 P3 (`_assert_dcs_single_vendor_store` -> 409 mixed_vendors); the
    single-GRN branch had drifted without it. Same verdict, same 409 shape. A
    GRN with no vendor_id (legacy row) is not checked, matching the DC guard.

    Loads via the GRN repository (the same path draft/match already use), so a
    caller need not hold the doc. Raises HTTPException (404 / 400 / 409) if the
    GRN is missing, its status is not ACCEPTED, or it belongs to another vendor.
    A Delivery Challan is out of scope here (it is billed via the /from-dcs
    consolidated path) -> 400. Fail-soft only when there is no GRN repository /
    no DB (returns None).
    """
    if not grn_id:
        return None
    grn_repo = get_grn_repository()
    if grn_repo is None:
        return None
    doc = grn_repo.find_by_id(grn_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"GRN {grn_id} not found")
    if doc.get("grn_subtype") == GRN_SUBTYPE_DC:
        raise HTTPException(
            status_code=400,
            detail=(
                f"GRN {grn_id} is a Delivery Challan; consolidate it via the "
                f"/from-dcs bulk-invoice path, not a single-GRN invoice."
            ),
        )
    if doc.get("status") != "ACCEPTED":
        raise HTTPException(
            status_code=400,
            detail=(
                f"GRN {grn_id} must be ACCEPTED before it can be billed "
                f"(current status: {doc.get('status') or 'UNKNOWN'}). Accept the "
                f"goods receipt first, then raise the purchase invoice."
            ),
        )
    grn_vendor_id = doc.get("vendor_id")
    if expected_vendor_id and grn_vendor_id and grn_vendor_id != expected_vendor_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "grn_vendor_mismatch",
                "message": (
                    f"GRN {grn_id} was received from vendor {grn_vendor_id}, but "
                    f"this invoice is being booked against vendor "
                    f"{expected_vendor_id}. A goods receipt can only be billed "
                    f"by the vendor that supplied it."
                ),
                "grn_vendor_id": grn_vendor_id,
                "invoice_vendor_id": expected_vendor_id,
            },
        )
    return doc


def _assert_grn_not_over_billed(db, grn, proposed_lines):
    """LEAK GUARD: a goods receipt may be billed in PARTS, but never for more
    units than it actually accepted.

    Without this, the single-GRN branch had no "already invoiced" control at all
    -- one 20-unit receipt could be billed three times over (three 201s, all
    match_status MATCHED, exceptions []) and the AP payable simply tripled. The
    DC branch has been safe since F9 (a DC carries dc_matched and 409s on the
    second attempt); this branch had drifted without an equivalent.

    Deliberately NOT a binary "this GRN is already invoiced" refusal: see
    purchase_match.over_billed_products for why part-billing must keep working.
    The rule enforced is the one the rest of the purchase flow already models --
    cumulative billed qty must not EXCEED accepted qty, per product.

    Reads every bill already linked to this grn_id and adds this invoice's
    computed lines on top. Raises 409 (grn_over_billed) naming each offending
    product. A DB read failure is a LOUD 503, not a silent pass: unlike the
    duplicate-invoice-number guard there is no unique index behind this one, so
    failing soft here would simply re-open the leak.

    RESIDUAL RACE (accepted): two bookings of the same GRN in the same instant
    both pass this pre-check and both insert. Closing that needs a claimed
    counter on the GRN (the DC path's guarded find_one_and_update shape); AP
    booking is a deliberate, low-frequency single-accountant action, so the
    pre-check is proportionate. The over-bill still surfaces in the PO-variance
    report, which prompts a debit note for exactly this overage.
    """
    if db is None or not grn:
        return
    grn_id = grn.get("grn_id")
    if not grn_id:
        return
    try:
        prior_bills = list(
            db.get_collection("vendor_bills").find(
                {"grn_id": grn_id},
                {"_id": 0, "bill_id": 1, "bill_number": 1, "lines": 1},
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[AP] over-bill guard could not read prior bills for GRN %s: %s",
            grn_id,
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not verify how much of this goods receipt has already "
                "been billed. The invoice was NOT booked -- retry shortly."
            ),
        ) from exc

    # A prior bill on this receipt that carries NO LINES declares no quantity,
    # so it contributes 0 to the cap and the receipt reads as unbilled -- one
    # 20-unit receipt then takes a header-only bill for the full value AND a
    # full line-level invoice on top (reproduced: Rs 151,200 booked against
    # Rs 75,600 of goods). vendors.create_vendor_bill is the door that mints
    # such a bill: it accepts a grn_id but stores only header amounts. We cannot
    # apportion what it never declared, so refuse -- the same conservative
    # stance assert_grn_billable_header_only already takes from the other side.
    blind = [b for b in prior_bills if not (b.get("lines") or [])]
    if blind:
        blind_by = [b.get("bill_number") or b.get("bill_id") for b in blind]
        logger.error(
            "[AP] blind-bill BLOCKED on GRN %s: prior bill(s) %s carry no line "
            "detail, so the units they consumed cannot be determined",
            grn_id,
            ",".join(str(b) for b in blind_by),
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "grn_billed_without_lines",
                "message": (
                    f"Goods receipt {grn_id} already carries bill(s) "
                    f"{', '.join(str(b) for b in blind_by)} recorded WITHOUT "
                    f"line detail, so how many units they already covered "
                    f"cannot be determined. Void that bill and re-raise it as a "
                    f"purchase invoice with lines, then bill the balance."
                ),
                "grn_id": grn_id,
                "billed_by": blind_by,
            },
        )

    prior_lines = []
    for bill in prior_bills:
        prior_lines.extend(bill.get("lines") or [])
    over = pmatch.over_billed_products(grn, prior_lines, proposed_lines)
    if not over:
        # The per-product totals the cap just derived from REAL bills. The
        # atomic claim baselines its counter from these, so a receipt whose
        # bills predate the counter cannot start from a false zero.
        return pmatch.billed_qty_by_product(prior_lines)

    already_billed_by = [b.get("bill_number") or b.get("bill_id") for b in prior_bills]
    detail_lines = "; ".join(
        f"{o['product_id']}: receipt accepted {o['accepted_qty']:g}, already "
        f"billed {o['already_billed_qty']:g}, this invoice {o['this_bill_qty']:g} "
        f"(over by {o['over_by']:g})"
        for o in over
    )
    logger.error(
        "[AP] over-bill BLOCKED on GRN %s (already billed by %s): %s",
        grn_id,
        ",".join(str(b) for b in already_billed_by) or "-",
        detail_lines,
    )
    raise HTTPException(
        status_code=409,
        detail={
            "code": "grn_over_billed",
            "message": (
                f"This invoice would bill more units than goods receipt "
                f"{grn_id} accepted -- {detail_lines}. Bill only the balance "
                f"still unbilled on this receipt, or raise a fresh receipt for "
                f"the extra goods."
            ),
            "grn_id": grn_id,
            "already_billed_by": already_billed_by,
            "products": over,
        },
    )


def _baseline_grn_counter(grns, grn_id, priors):
    """Seed billed_qty from the bills that ALREADY exist, where it is absent.

    Without this the counter starts at zero on every receipt booked before it
    existed, so its ceiling (accepted - this_qty) is measured from a false
    floor and two concurrent bookings both fit under it. Reproduced on a
    20-unit receipt already carrying a 12-unit bill: two concurrent 8-unit
    invoices BOTH booked, 28 units against 20 accepted.

    One guarded update per product, filtered on the counter being ABSENT, so it
    is idempotent and race-safe: a rival that seeds first simply makes ours a
    no-op, and both then claim against the same true baseline. Seeding is never
    destructive -- it only ever writes a value the real bills already prove.
    Fail-soft: a seed that does not land leaves the counter low, which the
    guarded claim below still measures honestly (it just keeps the race window
    open for that product), and the read-based cap remains in force.
    """
    for pid, qty in (priors or {}).items():
        if not qty or qty <= 0:
            continue
        try:
            grns.update_one(
                {"grn_id": grn_id, "billed_qty.%s" % pid: {"$exists": False}},
                {"$set": {"billed_qty.%s" % pid: float(qty)}},
            )
        except Exception:  # noqa: BLE001
            logger.error(
                "[AP] could not baseline billed_qty.%s on GRN %s from prior "
                "bills (%s units) -- the atomic claim will measure from a low "
                "floor for this product",
                pid,
                grn_id,
                qty,
            )


def _claim_grn_units(db, grn, proposed_lines, invoice_id, priors=None):
    """ATOMICALLY reserve this invoice's units against the goods receipt, so a
    concurrent booking of the same receipt cannot slip past the cap.

    ``_assert_grn_not_over_billed`` above is a read-then-decide check: two
    bookings in the same instant both read the same prior-bill set, both pass,
    and both insert. This closes that window with the DC path's proven shape --
    ONE guarded find_one_and_update on ONE document (PROTOCOL P0-1, exactly as
    _stamp_dcs_matched claims a DC) -- carrying a per-product counter on the GRN:

        grns.billed_qty       {product_id: units claimed}
        grns.billed_claim_ids [invoice_id, ...]

    The filter says, for every product this bill touches, "the counter is
    missing, or no greater than accepted_qty - this_qty". Losing that race
    returns None, and the caller refuses the booking. `$not: {$gt: n}` is the
    idiom that also matches a MISSING counter (a `$lte` would not), which is
    what makes the first claim on a receipt work.

    BOTH CHECKS STAY, and neither is redundant:
      * the read-based cap is truth-from-reality -- it sees bills booked before
        this counter existed (every historical GRN has no billed_qty), so it,
        not the counter, is what protects legacy data;
      * the counter closes the race window the read cannot.
    That pairing also fixes the direction of any counter drift: a counter
    LOWER than reality cannot leak money (the read-based cap already refused),
    while a counter HIGHER than reality can only cause a false refusal --
    loud and visible, never a silent over-payment. billed_claim_ids is what
    makes such a drift diagnosable (a claim whose invoice_id has no bill).

    A multi-product bill claims every product in ONE update, and Mongo applies
    it all-or-nothing: if any single product is already full the whole claim is
    refused and NO counter moves (verified against MongoDB 8.3.2, along with the
    $not/$gt-vs-missing behaviour this filter depends on). The one shape that
    errors rather than refusing is a billed_qty that exists as a NON-object --
    only reachable by hand-editing the row, and it surfaces as the loud 503
    below, never as a miscount.

    Returns the {product_id: {qty, max_prior}} claim on success, None when the
    race was lost, and {} when there was nothing claimable (no keyed products,
    or a receipt whose items carry no product_id -- the same fail-open-on-
    unknowable rule the cap uses). A DB error is a LOUD 503: a silent pass here
    would re-open the very window this exists to close.
    """
    if db is None or not grn:
        return {}
    grn_id = grn.get("grn_id")
    if not grn_id:
        return {}
    claim = pmatch.billing_claim_thresholds(grn, proposed_lines)
    if claim is None:
        # The bill overshoots the receipt ON ITS OWN -- no prior quantity would
        # make it fit, so no rival is involved. Raise the accurate refusal here
        # rather than returning None, whose caller-side wording blames a race.
        # (The read-based cap 409s first in practice; this must not depend on
        # that, and it must not lie if it is ever reached.)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "grn_over_billed",
                "message": (
                    f"This invoice bills more units than goods receipt "
                    f"{grn_id} ever accepted. Bill only what the receipt "
                    f"covers, or raise a fresh receipt for the extra goods."
                ),
                "grn_id": grn_id,
            },
        )
    if not claim:
        return {}

    # product_id becomes a Mongo FIELD NAME here, so it must carry no dot and no
    # leading '$'. Every product_id in production is a plain uuid4 (143 of 143
    # checked 2026-08-27); one that broke the rule would surface as the loud 503
    # below, never as a silent miscount.
    flt = {"grn_id": grn_id}
    inc = {}
    for pid, spec in claim.items():
        flt["billed_qty.%s" % pid] = {"$not": {"$gt": spec["max_prior"]}}
        inc["billed_qty.%s" % pid] = spec["qty"]
    grns = db.get_collection("grns")
    if not hasattr(grns, "find_one_and_update"):
        # The handle cannot do a guarded claim at all -- a stub/mock collection,
        # never a real Mongo one. That is a MISSING CAPABILITY, not a failed
        # operation, so it must not masquerade as a transient 503: fall back to
        # the read-based cap (which has already vetted this bill) and say so.
        logger.warning(
            "[AP] collection handle has no find_one_and_update -- booking "
            "invoice %s against GRN %s without an atomic unit claim; the "
            "read-based over-bill cap is the only control on this booking",
            invoice_id,
            grn_id,
        )
        return {}
    _baseline_grn_counter(grns, grn_id, priors)
    try:
        won = grns.find_one_and_update(
            flt,
            {
                "$inc": inc,
                "$addToSet": {"billed_claim_ids": invoice_id},
                "$set": {"billed_claim_at": datetime.now().isoformat()},
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[AP] could not claim billable units on GRN %s for invoice %s: %s",
            grn_id,
            invoice_id,
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not reserve this goods receipt's units. The invoice was "
                "NOT booked -- retry shortly. If it keeps failing, this receipt's "
                "billed_qty counter needs attention (see the server log)."
            ),
        ) from exc
    if won is not None:
        return claim

    # No match can mean two very different things, and refusing on both would
    # turn an absent row into a permanent false refusal. Re-read WITHOUT the
    # counter conditions to tell them apart: a row that exists means a rival
    # really did take the units; no row at all means there is nothing here to
    # claim against (the GRN reached us through the repository, which need not
    # be backed by this handle). Fail OPEN in the second case -- the read-based
    # cap has already vetted this bill against the receipt, so the money stays
    # protected; only the race window is left open, and it is logged.
    try:
        exists = grns.find_one({"grn_id": grn_id}, {"_id": 1})
    except Exception as exc:  # noqa: BLE001
        # UNKNOWN is not ABSENT. Mapping a read blip onto "no row" would book
        # the loser of a real race -- and in a real race the read-based cap has
        # already passed on a stale view, so this counter is the ONLY control
        # left. The guarded update proved the row reachable a microsecond ago,
        # so refuse loudly instead of guessing. (503, not the caller's race 409:
        # we no longer know that a rival is what happened.)
        logger.error(
            "[AP] could not re-read GRN %s after a lost unit claim on invoice "
            "%s -- refusing rather than booking: %s",
            grn_id,
            invoice_id,
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not confirm this goods receipt's remaining units. The "
                "invoice was NOT booked -- retry shortly."
            ),
        ) from exc
    if exists is None:
        logger.warning(
            "[AP] no grns row for %s -- booking invoice %s without an atomic "
            "unit claim; the read-based over-bill cap is the only control on "
            "this booking",
            grn_id,
            invoice_id,
        )
        return {}
    return None


def _release_grn_units(db, grn, claim, invoice_id):
    """Give back units claimed by a booking that did not survive.

    Called when the bill insert fails after a successful claim. Without it a
    crashed booking would leave the receipt looking fully billed and block the
    legitimate invoice -- a false refusal, which is the safe direction but still
    an operational stall. Fail-soft with a LOUD log: the units are recoverable
    from billed_claim_ids, and the read-based cap still governs correctness.
    """
    if db is None or not grn or not claim:
        return
    grn_id = grn.get("grn_id")
    if not grn_id:
        return
    dec = {"billed_qty.%s" % pid: -spec["qty"] for pid, spec in claim.items()}
    try:
        db.get_collection("grns").update_one(
            {"grn_id": grn_id},
            {"$inc": dec, "$pull": {"billed_claim_ids": invoice_id}},
        )
    except Exception:  # noqa: BLE001
        logger.error(
            "[AP] CRITICAL: could not release claimed units on GRN %s for "
            "rolled-back invoice %s (claim: %s) -- the receipt will look more "
            "billed than it is until reconciled",
            grn_id,
            invoice_id,
            claim,
        )


def assert_grn_billable_header_only(db, grn_id, vendor_id):
    """The same two guards for the HEADER-ONLY vendor-bill door
    (vendors.create_vendor_bill), which accepts a grn_id but carries no lines.

    With no lines there is no per-product quantity to apportion, so the
    cumulative test above cannot run -- the conservative equivalent is that a
    receipt already carrying a bill may not take a second, blind one. (Split
    billing stays available through the first-class purchase-invoice door, which
    does carry lines and is quantity-checked.)
    """
    grn = _load_standard_grn(grn_id, expected_vendor_id=vendor_id)
    if grn is None or db is None:
        return
    try:
        existing = db.get_collection("vendor_bills").find_one(
            {"grn_id": grn_id}, {"_id": 0, "bill_id": 1, "bill_number": 1}
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[AP] header-bill GRN guard could not read prior bills for %s: %s",
            grn_id,
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not verify whether this goods receipt has already been "
                "billed. The bill was NOT recorded -- retry shortly."
            ),
        ) from exc
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "grn_already_billed",
                "message": (
                    f"Goods receipt {grn_id} is already billed by "
                    f"{existing.get('bill_number') or existing.get('bill_id')}. "
                    f"To bill the balance of a part-delivered receipt, use the "
                    f"purchase-invoice screen (it checks quantities line by "
                    f"line); a header-only bill cannot prove what is left."
                ),
                "grn_id": grn_id,
                "billed_by": existing.get("bill_number") or existing.get("bill_id"),
            },
        )


def _assert_dcs_single_vendor_store(dc_docs, expected_vendor_id=None):
    """F9 P3: a consolidated invoice covers ONE vendor's DCs at ONE store.

    The from-dcs draft used to resolve vendor_id/store_id first-wins, so a
    cross-vendor multi-select silently booked vendor B's goods on vendor A's
    payable (and the ITC under A's GSTIN). Hard 409 instead:
      * mixed_vendors -- the DCs span more than one vendor_id, or the explicit
        invoice vendor doesn't match the (single) DC vendor.
      * mixed_stores  -- the DCs were received at more than one store.
    DCs with no vendor_id/store_id (legacy rows) are ignored by the check.
    """
    vendor_ids = {dc.get("vendor_id") for dc in dc_docs or [] if dc.get("vendor_id")}
    store_ids = {dc.get("store_id") for dc in dc_docs or [] if dc.get("store_id")}
    if len(vendor_ids) > 1 or (
        expected_vendor_id and vendor_ids and expected_vendor_id not in vendor_ids
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "mixed_vendors",
                "message": (
                    "All linked Delivery Challans must belong to one vendor "
                    "(and match the invoice's vendor). A consolidated invoice "
                    "cannot mix vendors."
                ),
            },
        )
    if len(store_ids) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "mixed_stores",
                "message": (
                    "All linked Delivery Challans must have been received at "
                    "one store. Draft one consolidated invoice per store."
                ),
            },
        )


def _stamp_dcs_matched(db, dc_ids, invoice_id):
    """F9: flip dc_matched=true + linked_bulk_invoice_id on each linked DC.

    PROTOCOL P0-1: one single-document find_one_and_update per DC, one at a time
    -- never a cross-collection / multi-document transaction. The guarded filter
    (dc_matched != True) makes a concurrent second booking of the same DC a
    no-op (it returns None), so the same DC can never be linked twice even under
    a race. Returns the list of grn_ids actually stamped."""
    if db is None or not dc_ids:
        return []
    coll = db.get_collection("grns")
    stamped = []
    for dc_id in dc_ids:
        try:
            updated = coll.find_one_and_update(
                {"grn_id": dc_id, "dc_matched": {"$ne": True}},
                {
                    "$set": {
                        "dc_matched": True,
                        "linked_bulk_invoice_id": invoice_id,
                        "dc_matched_at": datetime.now().isoformat(),
                    }
                },
            )
            if updated is not None:
                stamped.append(dc_id)
        except Exception:
            continue
    return stamped


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


def _product_state_for_valuation(
    db, product_ids, store_id=None, exclude_grn_id=None
) -> dict:
    """Build {product_id: {on_hand_qty, cost_price}} for the moving-average
    true-up: the CURRENT on-hand quantity (count of AVAILABLE serialized
    stock_units) and current cost (product cost_price / landed_cost) per
    product. Fail-soft: any error yields an empty/partial map (the true-up then
    treats missing products as zero on-hand at zero cost -> takes the invoice
    cost, which is the correct first-receipt behaviour).

    S9: `exclude_grn_id` subtracts the units THIS delivery minted. The blend
    adds the invoiced quantity as the incoming layer, so counting the same
    delivery again in the "existing" on-hand made every delivery appear twice in
    its own average and dragged the new cost back toward the old one (receive 10
    @100, be billed @120 -> 110 instead of 120). Subtracting only the units
    still AVAILABLE from this GRN is exactly right: a unit from this delivery
    that has already sold is no longer AVAILABLE, so it was never in the count."""
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
            # S9: take this delivery's own units back out of the "existing"
            # on-hand. Counted as a second equality query rather than a $ne so
            # the arithmetic is identical on Mongo and on the test doubles.
            own = 0
            if exclude_grn_id:
                own_flt = dict(flt)
                own_flt["source_id"] = exclude_grn_id
                try:
                    own = int(coll.count_documents(own_flt) or 0)
                except Exception:
                    own = 0
            state.setdefault(pid, {"cost_price": None, "on_hand_qty": 0})
            state[pid]["on_hand_qty"] = max(0, int(cnt or 0) - own)
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
        product_state = _product_state_for_valuation(
            db, pids, store_id, exclude_grn_id=grn_id
        )
        updates = pmatch.valuation_trueup_for_invoice(
            lines, product_state, config.get("valuation_method")
        )
        if not updates:
            return None
        products = db.get_collection("products")
        catalog_products = db.get_collection("catalog_products")
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
            # Fail-soft companion write to catalog_products so catalog-only
            # products (not yet in the products spine) also get a fresh cost.
            try:
                catalog_products.update_one(
                    {"id": pid},
                    {
                        "$set": {
                            "pricing.cost_price": new_cost,
                            "pricing.cost_updated_at": datetime.now().isoformat(),
                            "pricing.cost_source": "PURCHASE_INVOICE",
                        }
                    },
                )
            except Exception:
                pass  # never roll back the products write on a catalog miss
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


def _line_product_ids(lines) -> list:
    """Distinct product ids named by the invoice lines, in line order.

    ONE reader for the two ruling-15 gates below: what counts as "this bill is
    for goods" and what gets checked for cataloguing must never drift apart.
    """
    pids = []
    for ln in lines or []:
        pid = getattr(ln, "product_id", None) or (
            ln.get("product_id") if isinstance(ln, dict) else None
        )
        if pid and pid not in pids:
            pids.append(pid)
    return pids


def _line_products(db, lines) -> Optional[dict]:
    """product_id -> the products-spine doc, for every line that names one.

    Three distinct answers, and the difference matters:
      * ``None``  -- we could not READ (no DB, or the query blew up). Callers
                     must not turn a lookup failure into a verdict.
      * ``{}``    -- this bill names no goods we stock (services, freight,
                     rent, an expense bill), or names ids we have never heard
                     of.
      * a mapping -- the stocked goods this bill is settling.
    """
    if db is None:
        return None
    pids = _line_product_ids(lines)
    if not pids:
        return {}
    try:
        return {
            p.get("product_id"): p
            for p in db.get_collection("products").find(
                {"product_id": {"$in": pids}}, {"_id": 0}
            )
        }
    except Exception:  # noqa: BLE001 - cannot read: do not invent a failure
        return None


def _assert_products_catalogued(lines, found) -> None:
    """Ruling 15: the INVOICE is the gate. It may only proceed for products that
    are properly catalogued -- and it must say WHICH detail is missing, by name.

    The permissive front (a PO line typed in for an item nobody had catalogued)
    is only safe because the purchase cannot be FINALISED until someone has
    finished the product. Reads the same chokepoint everything else does,
    product_master.compute_catalog_status, so there is no second done-rule.

    Fail-soft ONLY on a missing DB/product collection: a lookup failure must not
    invent an incomplete product, but a product we CAN read and that IS
    incomplete is a hard 422 before any write.

    cost_price is DELIBERATELY not a gap here -- see the comment at the gap
    line below. Do not "restore" it.

    ``found`` comes from _line_products: None/{} means there is nothing we can
    read and judge, so there is nothing to refuse.
    """
    if not found:
        return
    blocked = []
    for pid in _line_product_ids(lines):
        prod = found.get(pid)
        if prod is None:
            continue
        # Rulings 11 + 12: the COST ARRIVES LATE, and THIS bill is the
        # authority on it -- _apply_valuation_trueup writes this very line's
        # unit_price into products.cost_price moments after this gate passes.
        # Refusing the bill for the one figure the bill is holding would refuse
        # essentially every vendor bill in the system: all 68 live products
        # carry exactly done_gaps ["cost_price"] and nothing else. send_po
        # subtracts the same field for the same reason (vendors.py,
        # PO_LINES_INCOMPLETE). DO NOT put cost_price back into this gate --
        # the product ends up WITH a cost precisely because the bill booked.
        # (list comprehension, not a set: the refusal message must keep
        # compute_catalog_status's field order.)
        gaps = [g for g in _pm.compute_catalog_status(prod)[1] if g != "cost_price"]
        if gaps:
            blocked.append(
                {
                    "product_id": pid,
                    "product": prod.get("name")
                    or " ".join(
                        str(prod.get(k) or "") for k in ("brand", "model")
                    ).strip()
                    or prod.get("sku"),
                    "missing": [_pm.field_label(g) for g in gaps],
                }
            )
    if blocked:
        first = blocked[0]
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PRODUCT_NOT_CATALOGUED",
                "message": (
                    f"{first['product']} is still missing "
                    f"{', '.join(first['missing'])}"
                    + (
                        f" (and {len(blocked) - 1} other item(s) on this bill)"
                        if len(blocked) > 1
                        else ""
                    )
                    + ". Finish cataloguing it, then book the bill."
                ),
                "lines": blocked,
            },
        )


def _apply_invoice_mrp(db, lines, invoice_id) -> Optional[list]:
    """Ruling 12: the MRP on the vendor's invoice is the actual retail price.

    Writes it onto the product and returns {product_id, old_mrp, new_mrp} for
    the audit row, so a change of retail price is never silent. Only lines that
    actually carry an MRP are touched, and an unchanged value is a no-op.
    STRICTLY fail-soft, like the cost true-up: this runs AFTER the booking and
    must never roll it back.
    """
    try:
        if db is None:
            return None
        products = db.get_collection("products")
        applied = []
        seen = set()
        for ln in lines or []:
            pid = ln.get("product_id")
            new_mrp = ln.get("mrp")
            if not pid or new_mrp is None or pid in seen:
                continue
            seen.add(pid)
            cur = products.find_one({"product_id": pid}, {"_id": 0, "mrp": 1}) or {}
            old = cur.get("mrp")
            try:
                if old is not None and round(float(old), 2) == round(
                    float(new_mrp), 2
                ):
                    continue
            except (TypeError, ValueError):
                pass
            products.update_one(
                {"product_id": pid},
                {
                    "$set": {
                        "mrp": round(float(new_mrp), 2),
                        "mrp_source": "PURCHASE_INVOICE",
                        "mrp_source_id": invoice_id,
                        "mrp_updated_at": datetime.now().isoformat(),
                    }
                },
            )
            applied.append(
                {
                    "product_id": pid,
                    "old_mrp": old,
                    "new_mrp": round(float(new_mrp), 2),
                }
            )
        return applied or None
    except Exception:  # noqa: BLE001
        return None


class CataloguingRequest(BaseModel):
    """Ask a cataloguer to finish the products blocking a bill."""

    product_ids: List[str] = Field(..., min_length=1)
    note: Optional[str] = None


@router.post("/request-cataloguing", status_code=201)
async def request_cataloguing(
    body: CataloguingRequest,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """The way past the gate for the person who is stopped by it.

    An accountant holds no `products:write` -- deliberately: whoever settles the
    money does not also define the product. So when the invoice refuses an
    incomplete product, the accountant's only move would otherwise be to find a
    developer. This raises the P2 task for the cataloguer instead, naming the
    products and exactly what each one is missing.

    A gate you can clear by asking a named colleague is a gate. A gate whose
    only exit is a developer is a wall.
    """
    db = _get_db()
    items = []
    for pid in dict.fromkeys(body.product_ids):
        prod = None
        if db is not None:
            try:
                prod = db.get_collection("products").find_one(
                    {"product_id": pid}, {"_id": 0}
                )
            except Exception:  # noqa: BLE001
                prod = None
        gaps = _pm.compute_catalog_status(prod)[1] if prod else []
        items.append(
            {
                "product_id": pid,
                "product": (prod or {}).get("name")
                or " ".join(
                    str((prod or {}).get(k) or "") for k in ("brand", "model")
                ).strip()
                or pid,
                "missing": [_pm.field_label(g) for g in gaps],
            }
        )

    lines = [
        f"- {i['product']}: needs "
        + (", ".join(i["missing"]) if i["missing"] else "review")
        for i in items
    ]
    try:
        from ..services.task_triggers import create_system_task
        from ..dependencies import get_task_repository

        create_system_task(
            get_task_repository(),
            title=f"Finish cataloguing {len(items)} item(s) - a vendor bill is waiting",
            description=(
                "A purchase invoice cannot be booked until these products are "
                "catalogue-complete:\n"
                + "\n".join(lines)
                + (f"\n\nNote: {body.note}" if body.note else "")
            ),
            priority="P2",
            category="Catalog",
            store_id=current_user.get("active_store_id"),
            dedupe_ref="catalogue-for-bill:"
            + ",".join(sorted(i["product_id"] for i in items)),
        )
    except Exception:  # noqa: BLE001 - asking must never 500 the screen
        logger.warning("[PI] could not raise the cataloguing task", exc_info=True)

    return {"requested": items, "message": "Cataloguing requested"}


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

    # F3: a STANDARD PO-backed GRN must be ACCEPTED before it can be billed -- a
    # GRN mints stock only at accept time, so booking against a PENDING /
    # PARTIALLY_ACCEPTED GRN records a payable + ITC for goods not yet received
    # into stock. It must also belong to THIS vendor (expected_vendor_id), the
    # single-GRN mirror of the DC path's mixed_vendors 409. DC-consolidated
    # invoices validate each linked DC separately below (via _load_linked_dcs),
    # so the single-GRN guard skips them.
    grn_doc = None
    if body.grn_id and not body.linked_dc_ids:
        grn_doc = _load_standard_grn(body.grn_id, expected_vendor_id=body.vendor_id)

    # Ruling 15 -- the bill must be LINKED to the goods-received record. A bill
    # for goods nobody counted in settles a purchase whose quantities were
    # never tallied: the 3-way match has nothing to compare the bill to, and
    # the rejected-goods hold (ruling 7) cannot fire either, because it is
    # reached through the GRN.
    #
    # THE TRIGGER IS THE GOODS, NOT THE PAPERWORK. Gating on po_id alone made
    # the whole of ruling 15 optional -- leaving the purchase-order box blank
    # booked a bill for 20 stocked frames with no receipt at all. Any line that
    # NAMES a product is a goods line. A bill that names no product (services,
    # freight, rent, an expense bill) has no receipt to link and is untouched.
    #
    # Naming the id is the trigger, not finding it: whether the products spine
    # can be read must not decide whether a bill needs a receipt, or an
    # unreadable DB (or a typo'd id) would be the bypass instead.
    #
    # AND THE DECLARATION IS A TRIGGER TOO. A free-text line ("20 pcs assorted
    # frames") names no product_id, so the goods trigger above is blind to it
    # -- prose was the bypass. With no receipt linked, the caller must now say
    # what the bill is FOR (bill_kind): GOODS joins the trigger; SERVICES
    # (freight, rent, job-work, expenses) books header-and-lines as before;
    # saying nothing is refused outright. Declaring SERVICES cannot dodge the
    # product/PO trigger -- a line that names a stocked product is goods,
    # whatever the header claims. (Residual, accepted by the owner as the
    # floor: goods typed as prose AND deliberately declared services still
    # book -- software cannot read the carton.)
    #
    # A genuine no-order purchase (goods bought over the counter, no PO) has a
    # way out the UI can actually WALK: the Goods Receipt screen's
    # Delivery-Challan mode receives without a PO (vendor picker + product
    # lines), and the receipt it posts is linkable from every billing door.
    if not body.grn_id and not body.linked_dc_ids:
        if (
            body.po_id
            or _line_product_ids(body.lines)
            or body.bill_kind == ap_engine.BILL_KIND_GOODS
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "GRN_LINK_REQUIRED",
                    "message": (
                        "Link the goods receipt for this bill before booking "
                        "it - the quantities have to be tallied before the "
                        "purchase is final. If the goods arrived without a "
                        "purchase order, log them as a Delivery Challan on "
                        "the Goods Receipt screen (tick 'This is a Delivery "
                        "Challan', pick the vendor, add what arrived), then "
                        "bill against that receipt."
                    ),
                },
            )
        if body.bill_kind is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "BILL_KIND_REQUIRED",
                    "message": (
                        "Say what this bill is for: goods, or "
                        "services/expenses. A goods bill must link its goods "
                        "receipt; a services or expense bill (freight, rent, "
                        "job-work) books as before."
                    ),
                },
            )

    # Ruling 15 -- and only for CATALOGUED products, naming what is missing.
    _assert_products_catalogued(body.lines, _line_products(db, body.lines))

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

    # A goods receipt may be billed in PARTS, never for more units than it
    # accepted. Runs on the computed lines (so it sees exactly what is about to
    # be booked) and BEFORE any write. Skipped when there is no resolvable GRN.
    # Hands back the per-product units the EXISTING bills already consumed, so
    # the atomic claim below can baseline its counter from reality instead of
    # from a false zero on any receipt whose bills predate the counter.
    grn_priors = None
    if grn_doc is not None:
        grn_priors = _assert_grn_not_over_billed(db, grn_doc, computed["lines"])

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

    # F9 -- DELIVERY-CHALLAN BULK TALLY: when the invoice consolidates a set of
    # DCs, verify each DC is a still-open ACCEPTED Delivery Challan (raises 404/
    # 400/409 otherwise so a DC can't be double-billed), period-lock the EARLIEST
    # DC date (goods may have arrived in a now-closed month even if the invoice
    # date is in the open month), and run dc_bulk_match (DC-received vs billed
    # qty). A quantity mismatch -> ON_HOLD_EXCEPTION (auto-hold, same as the
    # 3-way match): the AP liability is still recorded, just flagged for review.
    dc_docs = []
    dc_match = None
    dc_match_status = "N_A"
    if body.linked_dc_ids:
        dc_docs = _load_linked_dcs(db, body.linked_dc_ids)
        # F9 P3: hard-block a cross-vendor / cross-store consolidation BEFORE
        # any write -- the payable + ITC would be booked against the wrong
        # vendor/GSTIN otherwise.
        _assert_dcs_single_vendor_store(dc_docs, expected_vendor_id=body.vendor_id)
        # Period-lock the earliest DC date.
        earliest = None
        for dc in dc_docs:
            d = dc.get("dc_date")
            if d and (earliest is None or d < earliest):
                earliest = d
        if earliest:
            try:
                from .finance import check_period_locked

                check_period_locked(db, earliest)
            except HTTPException:
                raise
            except Exception:
                pass
        dc_match = pmatch.dc_bulk_match(
            dc_docs, computed["lines"], config["match_tolerance_pct"]
        )
        dc_match_status = dc_match.get("match_status")
        # The DC tally verdict feeds the header match_status too, so an existing
        # ON_HOLD review surfaces in the standard invoice list / dashboards.
        if match_status is None:
            match_status = dc_match_status

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
        # F9 -- DC bulk-tally verdict + which DCs this invoice consolidates.
        # dc_match_status is N_A for a non-DC invoice (backward-compatible).
        "linked_dc_ids": body.linked_dc_ids or None,
        "dc_match_status": dc_match_status,
        "dc_match_detail": dc_match,
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
        # The declared/derived nature of the bill: receipt-linked or
        # goods-triggered bookings are GOODS whatever the caller sent; only a
        # declared-SERVICES prose bill reaches here as SERVICES. Legacy rows
        # (booked before this field existed) simply lack it -- readers do not
        # key on it.
        "bill_kind": (
            ap_engine.BILL_KIND_GOODS
            if (
                body.grn_id
                or body.linked_dc_ids
                or body.po_id
                or _line_product_ids(body.lines)
            )
            else body.bill_kind
        ),
        "tds": round(body.tds, 2),
        "itc_eligible": bool(body.itc_eligible),
        "reverse_charge": bool(body.reverse_charge),
        "outstanding": total,
        "status": "OUTSTANDING",
        "notes": body.notes,
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }

    # ATOMIC CLAIM of the receipt's units, BEFORE the payable exists. Claiming
    # first (rather than inserting first and compensating, as the DC branch
    # does) means a payable exceeding the receipt is never written even
    # transiently; the cost is a claim to release if the insert then fails,
    # which the except-block below does. grn_doc is only set on the single-GRN
    # branch, so this can never interleave with the DC stamping further down.
    grn_claim = None
    if grn_doc is not None:
        grn_claim = _claim_grn_units(
            db, grn_doc, computed["lines"], invoice_id, priors=grn_priors
        )
        if grn_claim is None:
            logger.error(
                "[AP] concurrent over-bill rejected: invoice %s (vendor %s) lost "
                "the unit-claim race on GRN %s",
                body.invoice_number,
                body.vendor_id,
                body.grn_id,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "grn_over_billed",
                    "message": (
                        f"Another invoice claimed goods receipt {body.grn_id}'s "
                        f"remaining units while this booking was in flight. "
                        f"Nothing was booked -- re-draft from the receipt to see "
                        f"the balance that is actually left."
                    ),
                    "grn_id": body.grn_id,
                },
            )

    if db is not None:
        try:
            db.get_collection("vendor_bills").insert_one(dict(doc))
        except HTTPException:
            raise
        except Exception as exc:
            # F4: the app-level pre-check above narrows the common case, but the
            # race window (two concurrent bookings of the same vendor invoice no)
            # is closed by the UNIQUE partial index -- the insert LOSER surfaces
            # here as a DuplicateKeyError -> 409 (not a 500). Matched by class name
            # so no hard pymongo import (MockCollection never raises it).
            # Either way the units this booking claimed go back to the receipt.
            _release_grn_units(db, grn_doc, grn_claim, invoice_id)
            if exc.__class__.__name__ == "DuplicateKeyError":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Invoice number '{body.invoice_number}' is already "
                        f"recorded for this vendor. Duplicate vendor invoices are "
                        f"not allowed."
                    ),
                ) from exc
            raise HTTPException(
                status_code=500, detail="Failed to save purchase invoice"
            ) from exc

    # F9 -- stamp dc_matched=true + linked_bulk_invoice_id on each linked DC.
    # PROTOCOL P0-1: one single-document find_one_and_update per DC (no cross-
    # collection transaction). Done AFTER the bill insert so the AP liability is
    # the source of truth; the guarded filter blocks a concurrent double-link.
    stamped_dc_ids = []
    if body.linked_dc_ids:
        stamped_dc_ids = _stamp_dcs_matched(db, body.linked_dc_ids, invoice_id)
        # F9 P2 race guard: a concurrent booking of the same DC set passes the
        # _load_linked_dcs pre-check on both requests, but only ONE wins each
        # guarded stamp. The loser used to leave an ORPHAN bill (payable + ITC
        # booked twice with zero DCs linked, only auditable via stamped_dc_ids).
        # Compensate: un-stamp whatever WE stamped (guarded by our own
        # invoice_id so a rival's stamp is never touched), delete the
        # just-inserted bill (single-document delete), log loudly, 409.
        if db is not None and len(stamped_dc_ids) != len(body.linked_dc_ids):
            lost_dc_ids = [
                d for d in body.linked_dc_ids if d not in set(stamped_dc_ids)
            ]
            grn_coll = db.get_collection("grns")
            for dc_id in stamped_dc_ids:
                try:
                    grn_coll.find_one_and_update(
                        {"grn_id": dc_id, "linked_bulk_invoice_id": invoice_id},
                        {
                            "$set": {
                                "dc_matched": False,
                                "linked_bulk_invoice_id": None,
                                "dc_matched_at": None,
                            }
                        },
                    )
                except Exception:  # noqa: BLE001
                    logger.error(
                        "[F9] dc-race rollback: could not un-stamp DC %s from "
                        "rolled-back invoice %s -- manual reconciliation needed",
                        dc_id,
                        invoice_id,
                    )
            try:
                db.get_collection("vendor_bills").delete_one({"bill_id": invoice_id})
            except Exception:  # noqa: BLE001
                logger.error(
                    "[F9] CRITICAL: dc-race rollback could not delete orphan "
                    "bill %s (invoice %s, vendor %s) -- the payable is "
                    "double-booked until manually removed",
                    invoice_id,
                    body.invoice_number,
                    body.vendor_id,
                )
            logger.error(
                "[F9] concurrent DC double-booking rejected: invoice %s "
                "(vendor %s) lost the stamp race on DC(s) %s; bill rolled back",
                body.invoice_number,
                body.vendor_id,
                ",".join(lost_dc_ids),
            )
            # Best-effort audit row for the rejected booking (fail-soft).
            try:
                audit = get_audit_repository()
                if audit is not None:
                    audit.create(
                        {
                            "action": "purchase_invoice.dc_race_rollback",
                            "entity_type": "vendor_bill",
                            "entity_id": invoice_id,
                            "user_id": current_user.get("user_id"),
                            "detail": {
                                "vendor_id": body.vendor_id,
                                "invoice_number": body.invoice_number,
                                "linked_dc_ids": body.linked_dc_ids,
                                "stamped_dc_ids": stamped_dc_ids,
                                "lost_dc_ids": lost_dc_ids,
                            },
                        }
                    )
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "dc_already_matched",
                    "message": (
                        "One or more Delivery Challans were matched to another "
                        "invoice while this booking was in flight "
                        f"({', '.join(lost_dc_ids)}). The booking was rolled "
                        "back; re-draft from the remaining open DCs."
                    ),
                },
            )

    # Phase 2 -- INVENTORY VALUATION TRUE-UP. The invoice's per-unit landed price
    # is the authoritative cost; blend it into each product's moving-average cost
    # (or record the latest layer under FIFO). STRICTLY fail-soft: the helper
    # swallows all errors and is called AFTER the booking insert so a valuation
    # write can never roll back or block the recorded payable.
    valuation_updates = _apply_valuation_trueup(db, doc, computed, config)

    # Ruling 12 -- the MRP on the bill is the actual retail price. Same
    # fail-soft discipline as the cost true-up, and audited below.
    mrp_updates = _apply_invoice_mrp(
        db, [ln.model_dump() for ln in body.lines], invoice_id
    )

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
                        "mrp_updates": mrp_updates,
                        # F9 -- DC bulk-tally audit trail.
                        "linked_dc_ids": body.linked_dc_ids,
                        "dc_matched_ids": stamped_dc_ids,
                        "dc_match_status": dc_match_status,
                        "dc_match_exceptions": (dc_match or {}).get("exceptions"),
                    },
                }
            )
    except Exception:
        pass

    return _clean(doc)


# ---------------------------------------------------------------------------
# List / get
# ---------------------------------------------------------------------------


def _stamp_bill_actor_names(db, bills: list) -> None:
    """Add ``*_name`` beside the raw user ids the AP screens print.

    Two of them reach the owner as prose: the override banner
    ("Override approved by ...", exception_override.approved_by) and the recon
    console's tick tooltips / last-updated line (recon.<flag>_by,
    recon.last_updated_by). Every writer stamps a user id ("user-superadmin"),
    never a name, so those lines used to read the id straight out. The name was
    in the users collection all along -- nobody looked it up.

    In place, batched (one users read per id-set), fail-soft: an id that no
    longer resolves gets no ``_name`` and the screen falls back to the id.
    """
    from ..services.name_resolver import stamp_user_names

    from .purchase_recon import RECON_ACTOR_FIELDS

    stamp_user_names(db, [b.get("exception_override") for b in bills], ("approved_by",))
    stamp_user_names(db, [b.get("recon") for b in bills], RECON_ACTOR_FIELDS)


@router.get("")
@router.get("/")
async def list_purchase_invoices(
    vendor_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="OUTSTANDING / PARTIAL / PAID"),
    unmatched: Optional[bool] = Query(
        None, description="true -> only invoices with no po_id/grn_id link"
    ),
    # F1: purchase-invoice READS expose supplier bill / AP / GST-ITC / 3-way-match
    # data -- restrict to the AP roles (ACCOUNTANT/ADMIN; SUPERADMIN auto-passes),
    # same gate as the create/approve writes, instead of any authenticated user.
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
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
    _stamp_bill_actor_names(db, rows)
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

    # F3: only a still-ACCEPTED standard GRN can be drafted into an invoice, so
    # the user sees the block here rather than after filling in the POST body. A
    # DC is drafted via /from-dcs (_load_standard_grn 400s a DC). Fail-soft when
    # there is no DB (returns None -> no block).
    if grn:
        _load_standard_grn(grn_id)

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
            store_doc = db.get_collection("stores").find_one(
                {"store_id": grn.get("store_id")},
                {"_id": 0, "entity_id": 1},
            )
            recipient_entity_id = (store_doc or {}).get("entity_id")
    except Exception:
        recipient_entity_id = None
    # No place-of-supply hint: the bill receives on the entity's PRIMARY
    # registration. For a single-state entity that is the only GSTIN it has.
    # For an entity registered in TWO states buying for a shop outside its
    # primary state, this still classifies differently from the purchase order
    # (which reads the receiving shop's own state) -- a known, open
    # disagreement. Changing it would RE-CLASSIFY live purchase bills, and this
    # business does not re-state, so it is an owner decision that has not been
    # taken. It is written down here and in the PR rather than half-built: a
    # switch nobody can reach is not a decision, it is dead code.
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
# F9 -- draft a consolidated invoice from a SET of Delivery Challans
# ---------------------------------------------------------------------------
# Registered BEFORE the GET /{invoice_id} catch-all (same route-order discipline
# as /from-grn) so the literal /from-dcs path wins over the {invoice_id} param.


@router.get("/from-dcs")
async def draft_invoice_from_dcs(
    dc_ids: str = Query(..., description="Comma-separated DC grn_ids to consolidate"),
    vendor_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Build a DRAFT consolidated purchase invoice from N Delivery Challans --
    does NOT persist or book. Loads each DC, aggregates accepted lines by
    product_id (summing qty across DCs), and returns the prefilled draft with
    ``linked_dc_ids`` set so the accountant reviews then POSTs it as one bulk
    invoice.

    The aggregated lines + totals are ready to drop into POST / (the create
    body) after the accountant confirms / adjusts the billed quantities."""
    ids = [d.strip() for d in (dc_ids or "").split(",") if d.strip()]
    if not ids:
        raise HTTPException(status_code=400, detail="No DC ids provided")

    db = _get_db()
    dc_docs = _load_linked_dcs(db, ids)
    # F9 P3: the draft must not consolidate across vendors (or stores) -- the
    # old first-wins resolution silently mis-attributed a cross-vendor select.
    _assert_dcs_single_vendor_store(dc_docs, expected_vendor_id=vendor_id)

    # Aggregate accepted lines across all DCs by product_id. Reuse lines_from_grn
    # per DC (no PO -> unit_price 0 + gst_rate 0; the accountant fills the rate),
    # then sum qty per product.
    agg: dict = {}
    order: list = []
    resolved_vendor_id = vendor_id
    store_id = None
    for dc in dc_docs:
        resolved_vendor_id = resolved_vendor_id or dc.get("vendor_id")
        store_id = store_id or dc.get("store_id")
        for ln in pinv.lines_from_grn(dc, None):
            pid = ln.get("product_id")
            key = pid if pid is not None else f"_noid_{len(order)}"
            if key not in agg:
                agg[key] = dict(ln)
                order.append(key)
            else:
                try:
                    agg[key]["qty"] = (agg[key].get("qty") or 0) + (ln.get("qty") or 0)
                except (TypeError, ValueError):
                    pass
    raw_lines = [agg[k] for k in order]

    vendor = None
    vendor_repo = get_vendor_repository()
    if vendor_repo is not None and resolved_vendor_id:
        vendor = vendor_repo.find_by_id(resolved_vendor_id)
    supplier_gstin = (
        _vendor_gstin(db, vendor, resolved_vendor_id) if resolved_vendor_id else None
    )
    # Default the recipient to the entity that owns the receiving store.
    recipient_entity_id = None
    try:
        if db is not None and store_id:
            store = db.get_collection("stores").find_one(
                {"store_id": store_id}, {"_id": 0, "entity_id": 1}
            )
            recipient_entity_id = (store or {}).get("entity_id")
    except Exception:
        recipient_entity_id = None
    recipient = _resolve_recipient(db, recipient_entity_id, None, None)

    computed = pinv.compute_invoice(
        raw_lines, supplier_gstin, recipient.get("recipient_gstin"), None
    )

    return {
        "status": "DRAFT",
        "vendor_id": resolved_vendor_id,
        "vendor_name": (vendor or {}).get("trade_name")
        or (vendor or {}).get("legal_name"),
        "vendor_gstin": supplier_gstin,
        "recipient_entity_id": recipient.get("recipient_entity_id"),
        "recipient_gstin": recipient.get("recipient_gstin"),
        "place_of_supply": computed["itc_place_of_supply"],
        "supply_place_recipient": computed["place_of_supply"],
        "supplier_state": computed["supplier_state"],
        "interstate": computed["interstate"],
        "linked_dc_ids": ids,
        "dc_count": len(dc_docs),
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
async def get_purchase_config(
    # F1: exposes the accounting policy (valuation method + match tolerance) --
    # AP roles only (ACCOUNTANT/ADMIN; SUPERADMIN auto-passes).
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
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
    invoice_id: str,
    # F1: 3-way match detail (PO vs GRN vs invoice) is AP data -- AP roles only.
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
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


@router.get("/{invoice_id}/dc-match")
async def get_invoice_dc_match(
    invoice_id: str,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """F9 -- return the stored Delivery-Challan bulk-tally detail for an invoice.

    For a non-DC invoice dc_match_status is N_A (or null on a pre-F9 row) and
    dc_match_detail is None. Read-only; role-gated to ACCOUNTANT / ADMIN."""
    db = _get_db()
    if db is None:
        return {
            "invoice_id": invoice_id,
            "dc_match_status": "N_A",
            "dc_match_detail": None,
            "linked_dc_ids": None,
        }
    try:
        doc = db.get_collection("vendor_bills").find_one(
            {"bill_id": invoice_id}, {"_id": 0}
        )
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Purchase invoice not found")
    return {
        "invoice_id": invoice_id,
        "dc_match_status": doc.get("dc_match_status") or "N_A",
        "dc_match_detail": doc.get("dc_match_detail"),
        "linked_dc_ids": doc.get("linked_dc_ids"),
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

    # Name the approver on a COPY, after the write: the stored override must
    # keep the raw id, so a renamed user is never frozen into the audit trail.
    echo = dict(override["exception_override"])
    _stamp_bill_actor_names(db, [{"exception_override": echo}])
    return {
        "invoice_id": invoice_id,
        "match_status": pmatch.MATCH_OVERRIDE,
        "exception_override": echo,
    }


# ---------------------------------------------------------------------------
# F19 -- dynamic landed-cost purchase matrix (capture -> preview -> allocate)
# ---------------------------------------------------------------------------
# Freight / duty / customs / forex / insurance / other spend on a vendor bill
# is captured as integer-paise components with an allocation method
# (BY_VALUE / BY_QTY / BY_WEIGHT), previewed without writes, then allocated
# ONCE across the bill's lines via the pure services/landed_cost.py engine
# (paise-exact: the per-line allocations sum EXACTLY to the component total).
#
# ONE-WAY GUARD: allocation flips ``landed_cost_allocated: true`` under a
# guarded single-document find_one_and_update; a concurrent second allocate
# (or any later component edit) loses the guard and 409s. Costed inventory is
# never silently re-costed.


class LandedCostComponent(BaseModel):
    type: str  # FREIGHT | DUTY | CUSTOMS | FOREX | INSURANCE | OTHER
    label: Optional[str] = None
    amount_paise: int = Field(..., ge=0)

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        t = (v or "").strip().upper()
        if t not in lc.COMPONENT_TYPES:
            raise ValueError(f"component type must be one of {lc.COMPONENT_TYPES}")
        return t


class LandedCostsSet(BaseModel):
    components: List[LandedCostComponent] = Field(default_factory=list)
    allocation_method: str = "BY_VALUE"

    @field_validator("allocation_method")
    @classmethod
    def _known_method(cls, v: str) -> str:
        m = (v or "").strip().upper()
        if m not in lc.ALLOCATION_METHODS:
            raise ValueError(
                f"allocation_method must be one of {lc.ALLOCATION_METHODS}"
            )
        return m


def _load_purchase_invoice_or_404(db, invoice_id: str) -> dict:
    """Fetch the vendor_bills row for an invoice id. 503 when the DB is down
    (a money-path write/preview must not silently no-op), 404 when missing.
    Mirrors approve_invoice_exception."""
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
    return doc


def _check_bill_period_open(db, doc: dict) -> None:
    """F19 period-lock guard, mirroring the F9 DC-date check: 423 when the
    bill's posting month (invoice_date, else bill_date) is in a locked
    accounting period.

    FAIL-CLOSED on a missing/unusable posting date: a landed-cost mutation in
    an unverifiable period must not proceed (the lock would otherwise be
    silently bypassed by a bill with no date). check_period_locked itself is
    fail-soft on DB errors -- infrastructure noise still never blocks."""
    posting_date = doc.get("invoice_date") or doc.get("bill_date")
    if not posting_date or not str(posting_date).strip():
        raise HTTPException(
            status_code=422,
            detail=(
                "This bill has no posting date (invoice_date/bill_date), so the "
                "accounting-period lock cannot be checked. Set the bill's date "
                "before capturing or allocating landed costs."
            ),
        )
    try:
        from .finance import check_period_locked

        check_period_locked(db, posting_date)
    except HTTPException:
        raise
    except Exception:
        pass


@router.post("/{invoice_id}/landed-costs")
async def set_invoice_landed_costs(
    invoice_id: str,
    body: LandedCostsSet,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Set / REPLACE the landed-cost components + allocation method on a bill.

    Allowed only while the bill is NOT yet allocated (409 afterwards -- the
    allocation is one-way and the captured components are part of its audit
    trail). 423 when the bill's posting period is locked. The component list
    replaces wholesale (no per-component patching) so what is stored is always
    exactly what the accountant last reviewed."""
    db = _get_db()
    doc = _load_purchase_invoice_or_404(db, invoice_id)
    if doc.get("landed_cost_allocated"):
        raise HTTPException(
            status_code=409,
            detail=(
                "Landed costs on this invoice are already allocated; "
                "components are immutable after allocation."
            ),
        )
    _check_bill_period_open(db, doc)
    if not body.components:
        raise HTTPException(
            status_code=400,
            detail="At least one landed-cost component is required.",
        )

    components = [c.model_dump() for c in body.components]
    total = lc.components_total_paise(components)  # ge=0 -> never raises here

    # Guarded write: a concurrent allocation may have landed between our read
    # and this update -- the landed_cost_allocated filter makes this a no-op
    # (None) in that case, so allocated components can never be rewritten.
    try:
        updated = db.get_collection("vendor_bills").find_one_and_update(
            {"bill_id": invoice_id, "landed_cost_allocated": {"$ne": True}},
            {
                "$set": {
                    "landed_cost_components": components,
                    "landed_cost_total_paise": total,
                    "allocation_method": body.allocation_method,
                    "landed_cost_allocated": False,
                    "landed_cost_captured_by": current_user.get("user_id"),
                    "landed_cost_captured_at": datetime.now().isoformat(),
                }
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail="Failed to save landed-cost components"
        ) from exc
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Landed costs on this invoice were allocated concurrently; "
                "components are immutable after allocation."
            ),
        )

    # Audit the capture (fail-soft -- never blocks the save).
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "purchase_invoice.set_landed_costs",
                    "entity_type": "vendor_bill",
                    "entity_id": invoice_id,
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "components": components,
                        "landed_cost_total_paise": total,
                        "allocation_method": body.allocation_method,
                    },
                }
            )
    except Exception:
        pass

    return {
        "invoice_id": invoice_id,
        "landed_cost_components": components,
        "landed_cost_total_paise": total,
        "allocation_method": body.allocation_method,
        "landed_cost_allocated": False,
    }


@router.get("/{invoice_id}/landed-costs/preview")
async def preview_invoice_landed_costs(
    invoice_id: str,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Dry-run the landed-cost allocation for review -- NO writes.

    Runs the pure engine over the bill's stored components + method and returns
    the per-line breakdown (allocation, per-unit landed cost, remainder) plus
    the per-product landed unit cost the roll-in would write. 400 when nothing
    is captured yet or the engine rejects the inputs (e.g. BY_WEIGHT with a
    missing line weight)."""
    db = _get_db()
    doc = _load_purchase_invoice_or_404(db, invoice_id)
    components = doc.get("landed_cost_components") or []
    if not components:
        raise HTTPException(
            status_code=400,
            detail=(
                "No landed-cost components captured on this invoice yet; "
                "POST /landed-costs first."
            ),
        )
    method = doc.get("allocation_method") or "BY_VALUE"
    lines = doc.get("lines") or []
    try:
        rows = lc.allocate_landed_costs(lines, components, method)
        per_product = lc.landed_unit_cost_by_product(lines, rows)
        total = lc.components_total_paise(components)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "invoice_id": invoice_id,
        "allocation_method": method,
        "landed_cost_components": components,
        "landed_cost_total_paise": total,
        "landed_cost_allocated": bool(doc.get("landed_cost_allocated")),
        "allocation": rows,
        "landed_unit_cost_by_product_paise": per_product,
    }


@router.post("/{invoice_id}/allocate-landed-costs")
async def allocate_invoice_landed_costs(
    invoice_id: str,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Allocate the captured landed costs across the bill's lines -- ONE-WAY.

    The single guarded find_one_and_update (bill_id + landed_cost_allocated !=
    true) persists the per-line allocation fields and flips
    ``landed_cost_allocated: true`` atomically; under a concurrent double-fire
    exactly ONE request wins and the loser 409s. 423 on a locked posting
    period; 400 when no components are captured (or total is zero) or the
    engine rejects the inputs.

    COST ROLL-IN CHOICE (documented per F19): the existing moving-average
    cost_price writer (_apply_valuation_trueup -> pmatch.
    valuation_trueup_for_invoice) is NOT safely re-invokable -- it blends the
    invoice's receipt qty into the product's CURRENT on-hand average, and at
    booking time it already blended this invoice's base cost (the receipt is
    also now part of on-hand), so calling it again here would double-count the
    same receipt. Instead we persist the per-line landed unit cost on the bill
    and write the product-level ``landed_cost`` / ``landed_cost_paise`` fields
    (fail-soft), which _product_state_for_valuation already reads as a cost
    fallback. cost_price AVCO stays owned by the existing booking-time flow --
    no second AVCO writer is introduced."""
    db = _get_db()
    doc = _load_purchase_invoice_or_404(db, invoice_id)
    if doc.get("landed_cost_allocated"):
        raise HTTPException(
            status_code=409,
            detail="Landed costs on this invoice are already allocated.",
        )
    _check_bill_period_open(db, doc)

    components = doc.get("landed_cost_components") or []
    try:
        total = lc.components_total_paise(components)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not components or total <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "No landed-cost components captured (or zero total); "
                "nothing to allocate. POST /landed-costs first."
            ),
        )
    lines = doc.get("lines") or []
    if not lines:
        raise HTTPException(
            status_code=400,
            detail="Invoice has no lines to allocate landed costs against.",
        )
    method = doc.get("allocation_method") or "BY_VALUE"
    try:
        rows = lc.allocate_landed_costs(lines, components, method)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Merge the per-line allocation fields onto the stored lines (additive --
    # every pre-existing line key is preserved).
    merged_lines = []
    for i, ln in enumerate(lines):
        merged = dict(ln) if isinstance(ln, dict) else {}
        row = rows[i]
        for key in (
            "landed_alloc_paise",
            "landed_per_unit_paise",
            "landed_remainder_paise",
            "landed_unit_cost_paise",
        ):
            merged[key] = row[key]
        merged_lines.append(merged)

    now = datetime.now().isoformat()
    # THE one-way gate: single guarded single-document update (PROTOCOL P0-1:
    # no cross-collection transaction). Exactly one concurrent caller matches
    # the landed_cost_allocated != true filter; everyone else gets None -> 409.
    # The components + method we COMPUTED FROM are pinned in the filter too:
    # an interleaved set-components between our read and this write makes the
    # filter miss (-> 409, caller re-reads), so a persisted allocation can
    # never disagree with the stored capture it claims to be derived from.
    try:
        won = db.get_collection("vendor_bills").find_one_and_update(
            {
                "bill_id": invoice_id,
                "landed_cost_allocated": {"$ne": True},
                "landed_cost_components": doc.get("landed_cost_components"),
                "allocation_method": doc.get("allocation_method"),
            },
            {
                "$set": {
                    "lines": merged_lines,
                    "landed_cost_allocation": rows,
                    "landed_cost_total_paise": total,
                    "landed_cost_allocated": True,
                    "landed_cost_allocated_by": current_user.get("user_id"),
                    "landed_cost_allocated_at": now,
                }
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail="Failed to record landed-cost allocation"
        ) from exc
    if won is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Landed costs on this invoice were allocated concurrently by "
                "another request; allocation is one-way."
            ),
        )

    # Product-master roll-in (STRICTLY fail-soft -- the allocation above is the
    # source of truth; a product write failure never rolls it back). See the
    # docstring for why this writes landed_cost and NOT cost_price.
    rolled_in = []
    try:
        per_product = lc.landed_unit_cost_by_product(lines, rows)
    except Exception:
        per_product = {}
    if per_product:
        try:
            products = db.get_collection("products")
            catalog_products = db.get_collection("catalog_products")
            for pid, unit_paise in per_product.items():
                try:
                    products.update_one(
                        {"product_id": pid},
                        {
                            "$set": {
                                "landed_cost": round(unit_paise / 100.0, 2),
                                "landed_cost_paise": int(unit_paise),
                                "landed_cost_source": "LANDED_COST_ALLOCATION",
                                "landed_cost_source_id": invoice_id,
                                "landed_cost_updated_at": now,
                            }
                        },
                    )
                    rolled_in.append(
                        {
                            "product_id": pid,
                            "landed_unit_cost_paise": int(unit_paise),
                        }
                    )
                except Exception:
                    continue
                # Fail-soft companion write to catalog_products for catalog-only
                # products that are not yet in the products spine.
                try:
                    catalog_products.update_one(
                        {"id": pid},
                        {
                            "$set": {
                                "pricing.landed_cost": round(unit_paise / 100.0, 2),
                                "pricing.landed_cost_paise": int(unit_paise),
                                "pricing.landed_cost_source": "LANDED_COST_ALLOCATION",
                                "pricing.landed_cost_updated_at": now,
                            }
                        },
                    )
                except Exception:
                    pass  # never roll back the products write on a catalog miss
        except Exception:
            rolled_in = []

    # Audit the allocation (fail-soft -- never blocks; the guarded write above
    # is the source of truth).
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "purchase_invoice.allocate_landed_costs",
                    "entity_type": "vendor_bill",
                    "entity_id": invoice_id,
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "allocation_method": method,
                        "landed_cost_total_paise": total,
                        "components": components,
                        "allocation": rows,
                        "product_rollin": rolled_in,
                    },
                }
            )
    except Exception:
        pass

    return {
        "invoice_id": invoice_id,
        "landed_cost_allocated": True,
        "allocation_method": method,
        "landed_cost_total_paise": total,
        "allocation": rows,
        "lines": merged_lines,
        "product_rollin": rolled_in,
        "landed_cost_allocated_by": current_user.get("user_id"),
        "landed_cost_allocated_at": now,
    }


@router.get("/{invoice_id}")
async def get_purchase_invoice(
    invoice_id: str,
    # F1: single-invoice read carries supplier bill / ITC / landed cost / match
    # verdict -- AP roles only (ACCOUNTANT/ADMIN; SUPERADMIN auto-passes).
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
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
    _stamp_bill_actor_names(db, [doc])
    return doc
