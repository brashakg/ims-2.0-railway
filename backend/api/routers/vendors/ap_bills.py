"""Accounts payable: bill models, AP aging and vendor bills."""

from ._shared import (
    BaseModel,
    Depends,
    Field,
    HTTPException,
    Optional,
    Query,
    _AP_ROLES,
    _get_db,
    ap_engine,
    datetime,
    field_validator,
    get_current_user,
    get_grn_repository,
    get_vendor_repository,
    logger,
    require_roles,
    router,
    uuid,
)
from .models import GRN_SUBTYPE_DC


# ============================================================================
# ACCOUNTS-PAYABLE: vendor bills, payments, debit notes, ledger, aging
# ============================================================================
# The PO/GRN flow above tracks GOODS. This block tracks MONEY: a vendor bill
# (purchase invoice) is the payable; payments (with optional TDS) and debit
# notes discharge it. Pure money/date math lives in services/ap_engine.py so
# these handlers stay thin (fetch rows -> call engine -> return).
#
# Route-order: every route here is a decorator, so it registers BEFORE the
# catch-all `/{vendor_id}` added at the very bottom. `/ap-aging` (one segment)
# therefore resolves to its own handler, not to get_vendor.


class VendorBillCreate(BaseModel):
    bill_number: str  # the vendor's own invoice / bill number
    bill_date: str  # ISO date (YYYY-MM-DD)
    taxable_amount: float = Field(..., ge=0)
    tax_amount: float = Field(0, ge=0)
    total_amount: float = Field(..., gt=0)
    po_id: Optional[str] = None
    grn_id: Optional[str] = None
    notes: Optional[str] = None
    # What this bill is FOR. "GOODS" must link its goods receipt (grn_id);
    # "SERVICES" (freight, rent, job-work, expenses) books header-only as
    # always. REQUIRED on this door when no receipt is linked -- this form used
    # to book "20 pcs assorted frames" as prose with no receipt, no products,
    # which dodged owner ruling 15 entirely. Optional in the SCHEMA so the
    # handler can refuse with a stable, plain-English 422 (BILL_KIND_REQUIRED)
    # instead of a raw pydantic error, and so stored legacy rows re-read fine.
    bill_kind: Optional[str] = None

    @field_validator("bill_kind", mode="before")
    @classmethod
    def _normalize_bill_kind(cls, v):
        return ap_engine.normalize_bill_kind(v)


class VendorPaymentCreate(BaseModel):
    amount: float = Field(..., gt=0)  # cash actually paid to the vendor
    payment_date: str  # ISO date
    mode: str = "BANK"  # CASH / BANK / UPI / CHEQUE / NEFT
    bill_id: Optional[str] = None  # allocate to a bill; else on-account/advance
    tds_section: Optional[str] = "NONE"  # see ap_engine.TDS_SECTIONS
    tds_base: Optional[float] = Field(default=None, ge=0)  # base for auto-TDS
    tds_amount: Optional[float] = Field(default=None, ge=0)  # explicit override
    reference: Optional[str] = None  # UTR / cheque no / txn id
    notes: Optional[str] = None


# Recognized vendor credit-note / debit-note types. Every type lands in the
# SAME vendor_debit_notes collection and reduces the payable by its `amount`
# (ap_engine.build_aging / build_ledger treat all rows identically), so the
# GL/ledger treatment of the two new types (DISCOUNT_CN, QUALITY_CN) mirrors the
# existing ones exactly -- the type only categorises WHY the payable dropped.
#   RETURN_CN  -- goods returned to vendor (RTV) -- the historical default here.
#   SCHEME_CN  -- scheme / target / volume rebate from the vendor.
#   DISCOUNT_CN-- a negotiated post-billing price discount (NEW).
#   QUALITY_CN -- compensation for defective / sub-spec goods kept, not returned (NEW).
# (VOLUME_REBATE is the machine-posted scheme source written by rebate_engine;
#  it is recognised on read but not a manual-create option here.)
VENDOR_CN_TYPES = ("RETURN_CN", "SCHEME_CN", "DISCOUNT_CN", "QUALITY_CN")


class DebitNoteCreate(BaseModel):
    amount: float = Field(..., gt=0)
    date: str  # ISO date
    reason: str
    bill_id: Optional[str] = None  # allocate to a bill; else on-account
    grn_id: Optional[str] = None  # link to the rejected-goods GRN, if any
    # Credit-note category. Defaults to RETURN_CN (the historical behaviour --
    # debit notes here were created for rejected/returned goods).
    cn_type: str = "RETURN_CN"

    @field_validator("cn_type")
    @classmethod
    def _validate_cn_type(cls, v):
        v = (v or "RETURN_CN").strip().upper()
        if v not in VENDOR_CN_TYPES:
            raise ValueError("cn_type must be one of " + ", ".join(VENDOR_CN_TYPES))
        return v


def _clean(doc: dict) -> dict:
    """Strip Mongo's _id so a freshly-inserted doc is JSON-serialisable."""
    return {k: v for k, v in doc.items() if k != "_id"}


def _recompute_bill_status(db, bill_id: Optional[str]) -> None:
    """Re-derive a bill's status (OUTSTANDING / PARTIAL / PAID) from its
    allocated payments + debit notes. Fail-soft."""
    if db is None or not bill_id:
        return
    try:
        bill = db.get_collection("vendor_bills").find_one(
            {"bill_id": bill_id}, {"_id": 0}
        )
        if not bill:
            return
        payments = list(
            db.get_collection("vendor_payments").find({"bill_id": bill_id}, {"_id": 0})
        )
        debit_notes = list(
            db.get_collection("vendor_debit_notes").find(
                {"bill_id": bill_id}, {"_id": 0}
            )
        )
        out = ap_engine.bill_outstanding(bill, payments, debit_notes)
        total = float(bill.get("total_amount") or 0)
        if out <= 0.01:
            status = "PAID"
        elif out < total:
            status = "PARTIAL"
        else:
            status = "OUTSTANDING"
        db.get_collection("vendor_bills").update_one(
            {"bill_id": bill_id},
            {"$set": {"outstanding": out, "status": status}},
        )
    except Exception:
        pass


@router.get("/ap-aging")
async def ap_aging(
    as_of: Optional[str] = Query(None, description="ISO date; defaults to today"),
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Org-wide accounts-payable aging, grouped by vendor + grand totals.

    Buckets each outstanding bill by days past its due date (current / 1-30 /
    31-60 / 61-90 / 90+). ADMIN / ACCOUNTANT only.
    """
    db = _get_db()
    if db is None:
        return {"as_of": as_of, "totals": {}, "vendors": []}
    try:
        bills = list(
            db.get_collection("vendor_bills").find(
                {"status": {"$ne": "PAID"}}, {"_id": 0}
            )
        )
        payments = list(db.get_collection("vendor_payments").find({}, {"_id": 0}))
        debit_notes = list(db.get_collection("vendor_debit_notes").find({}, {"_id": 0}))
    except Exception:
        bills, payments, debit_notes = [], [], []
    return ap_engine.build_aging_by_vendor(bills, payments, debit_notes, as_of)


@router.post("/{vendor_id}/bills", status_code=201)
async def create_vendor_bill(
    vendor_id: str,
    bill: VendorBillCreate,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Record a vendor bill (purchase invoice) as a payable. Due date is
    derived from the vendor's credit terms."""
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None
    if vendor_repo is not None and vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # Data-entry guard: taxable + tax should reconcile to the bill total
    # (allow Rs 1 of rounding slack).
    if abs((bill.taxable_amount + bill.tax_amount) - bill.total_amount) > 1.0:
        raise HTTPException(
            status_code=400,
            detail="taxable_amount + tax_amount must equal total_amount",
        )

    # Accounting period lock: cannot record vendor bills into a closed month.
    db_early = _get_db()
    if db_early is not None:
        from ..finance import check_period_locked

        check_period_locked(db_early, bill.bill_date)

    # Owner ruling 15, on the door that used to dodge it: a bill for GOODS must
    # link its goods receipt, and this form books pure prose ("20 pcs assorted
    # frames", Rs 52,500, no products named), so the only way to know a bill is
    # for goods is to ASK. With no receipt linked the caller must declare
    # GOODS or SERVICES; GOODS then refuses until a receipt is linked, naming
    # the no-PO Delivery-Challan route the receiving screen now actually has.
    # A deliberate SERVICES/expense bill books exactly as before. (Residual,
    # accepted by the owner as the floor: goods deliberately declared as
    # services still book -- no software can read the carton.)
    if not bill.grn_id:
        # A named purchase order IS a goods signal the software can see --
        # the line-detail invoice door refuses exactly this shape
        # (po_id with no receipt -> GRN_LINK_REQUIRED), and the two doors
        # must not drift. A declared SERVICES bill that names a PO is a
        # contradiction, not a carve-out.
        if getattr(bill, "po_id", None):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "GRN_LINK_REQUIRED",
                    "message": (
                        "This bill names a purchase order, so it is a "
                        "goods bill - link the goods receipt for that "
                        "order before recording it. A services or "
                        "expense bill should not name a purchase order."
                    ),
                },
            )
        if bill.bill_kind is None:
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
        if bill.bill_kind == ap_engine.BILL_KIND_GOODS:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "GRN_LINK_REQUIRED",
                    "message": (
                        "This bill is for goods, so link the goods receipt "
                        "before recording it - the quantities have to be "
                        "tallied before the purchase is final. If the goods "
                        "arrived without a purchase order, log them as a "
                        "Delivery Challan on the Goods Receipt screen (tick "
                        "'This is a Delivery Challan', pick the vendor, add "
                        "what arrived), then link that receipt here."
                    ),
                },
            )

    # This door accepts a grn_id but validated NOTHING about it -- so the same
    # two money leaks the first-class purchase-invoice door had were reachable
    # here too: bill another vendor's receipt, or bill one receipt again and
    # again. Reuse the purchase-invoice guards (imported at call time, like
    # check_period_locked above, so no cross-router import cycle).
    #
    # A DELIVERY CHALLAN receipt is billable here too (the whole point of the
    # no-PO receive path: goods bought over the counter get a DC receipt, and
    # THIS is the screen the accountant records the bill on). The DC takes the
    # same one-bill-per-receipt stance as the STANDARD header guard, enforced
    # by CLAIMING the DC (dc_matched) before the bill is written -- so neither
    # a second header bill nor a later consolidated /from-dcs invoice can bill
    # the same goods again.
    _dc_receipt = None
    if bill.grn_id:
        from ..purchase_invoices import (
            assert_grn_billable_header_only,
            _load_linked_dcs,
            _assert_dcs_single_vendor_store,
        )

        grn_repo = get_grn_repository()
        linked = grn_repo.find_by_id(bill.grn_id) if grn_repo is not None else None
        if linked is not None and linked.get("grn_subtype") == GRN_SUBTYPE_DC:
            # 404 missing / 400 not-ACCEPTED / 409 already-matched.
            dc_docs = _load_linked_dcs(db_early, [bill.grn_id])
            # 409 mixed_vendors when the DC belongs to another vendor.
            _assert_dcs_single_vendor_store(dc_docs, expected_vendor_id=vendor_id)
            _dc_receipt = dc_docs[0] if dc_docs else None
        else:
            assert_grn_billable_header_only(db_early, bill.grn_id, vendor_id)

    # Duplicate bill guard: the same vendor invoice number must not be recorded
    # twice for the same vendor. A double-entry would double the outstanding
    # payable and produce a duplicate payment row in the ledger.
    # Compared case/punctuation-FOLDED through the ONE normaliser
    # (purchase_invoice_engine.normalize_invoice_no -- the same fold the GRN
    # duplicate guard and the line-detail invoice door use): the exact-string
    # check here let 'GO-INV/9007' book the payable a second time next to
    # 'GO-INV-9007'. ponytail: linear scan over one vendor's bills; index a
    # normalised column if a vendor ever holds thousands.
    if db_early is not None:
        try:
            from ...services.purchase_invoice_engine import normalize_invoice_no

            _target = normalize_invoice_no(bill.bill_number)
            _rows = db_early.get_collection("vendor_bills").find(
                {"vendor_id": vendor_id},
                {"_id": 0, "bill_id": 1, "bill_number": 1},
            )
            dup = None
            if _target:
                dup = next(
                    (
                        r
                        for r in _rows
                        if normalize_invoice_no(r.get("bill_number")) == _target
                    ),
                    None,
                )
            if dup:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Bill number '{bill.bill_number}' is already recorded "
                        f"for this vendor. Duplicate vendor invoices are not allowed."
                    ),
                )
        except HTTPException:
            raise
        except Exception:
            pass  # fail-soft: skip dup check on DB error, proceed with insert

    credit_days = int((vendor or {}).get("credit_days", 30) or 30)
    due_date = ap_engine.compute_due_date(bill.bill_date, credit_days)
    bill_id = str(uuid.uuid4())
    doc = {
        "bill_id": bill_id,
        "vendor_id": vendor_id,
        "vendor_name": (vendor or {}).get("trade_name")
        or (vendor or {}).get("legal_name"),
        "bill_number": bill.bill_number,
        "bill_date": bill.bill_date,
        "due_date": due_date,
        "credit_days": credit_days,
        "taxable_amount": round(bill.taxable_amount, 2),
        "tax_amount": round(bill.tax_amount, 2),
        "total_amount": round(bill.total_amount, 2),
        "outstanding": round(bill.total_amount, 2),
        "po_id": bill.po_id,
        "grn_id": bill.grn_id,
        # A receipt-linked bill IS a goods bill whatever the caller declared;
        # otherwise the declared kind (the gate above proved it is SERVICES).
        "bill_kind": ap_engine.BILL_KIND_GOODS if bill.grn_id else bill.bill_kind,
        "notes": bill.notes,
        "status": "OUTSTANDING",
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }
    db = _get_db()

    # CLAIM the Delivery Challan before the payable exists (the DC branch's
    # proven guarded-stamp shape): of two racing bills on the same DC exactly
    # one wins the find_one_and_update, so the loser books nothing. Claiming
    # first means a double payable is never written even transiently; the cost
    # is un-stamping if the insert then fails, handled below.
    if _dc_receipt is not None and db is not None:
        from ..purchase_invoices import _stamp_dcs_matched

        stamped = _stamp_dcs_matched(db, [bill.grn_id], bill_id)
        if bill.grn_id not in stamped:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Delivery Challan {bill.grn_id} was just billed by "
                    f"another invoice. Nothing was recorded."
                ),
            )
    if db is not None:
        try:
            db.get_collection("vendor_bills").insert_one(dict(doc))
        except Exception as exc:
            # Give the claimed DC back (guarded by OUR bill_id so a rival's
            # stamp is never touched); without this a failed insert would
            # leave the receipt looking billed with no bill to show for it.
            if _dc_receipt is not None:
                try:
                    db.get_collection("grns").find_one_and_update(
                        {"grn_id": bill.grn_id, "linked_bulk_invoice_id": bill_id},
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
                        "[AP] could not release DC %s after failed bill insert "
                        "%s -- the DC reads as billed until manually cleared",
                        bill.grn_id,
                        bill_id,
                    )
            raise HTTPException(status_code=500, detail="Failed to save bill") from exc
    return _clean(doc)


@router.get("/{vendor_id}/bills")
async def list_vendor_bills(
    vendor_id: str,
    status: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List a vendor's bills (newest first)."""
    db = _get_db()
    if db is None:
        return {"bills": [], "total": 0}
    flt: dict = {"vendor_id": vendor_id}
    if status:
        flt["status"] = status
    try:
        bills = list(db.get_collection("vendor_bills").find(flt, {"_id": 0}))
    except Exception:
        bills = []
    bills.sort(key=lambda b: b.get("bill_date") or "", reverse=True)
    return {"bills": bills, "total": len(bills)}


def _rejected_goods_hold(db, bill_id: Optional[str]) -> Optional[str]:
    """Owner ruling 7: a purchase bill may not be passed for payment while goods
    on its goods receipt were REJECTED and no debit note has been raised.

    Returns a plain-English reason to refuse the payment, or None when the bill
    is clear. Reads the bill -> its receipts -> the rejected quantities, then
    looks for a debit note that references either that receipt or the bill
    itself (the DebitNoteCreate schema has carried `grn_id` -- "link to the
    rejected-goods GRN" -- since it was written; nothing had ever read it).

    A DC-consolidated bill stores grn_id None and carries its receipts in
    linked_dc_ids (they live in the same `grns` collection). Reading grn_id
    alone paid the very delivery a GRN-linked bill would have held, so BOTH
    fields are walked here -- every payment routes through this one helper.

    Fail-soft: any error returns None, because a lookup failure must not block
    a legitimate payment.
    """
    if db is None or not bill_id:
        return None
    try:
        bill = db.get_collection("vendor_bills").find_one(
            {"bill_id": bill_id},
            {"_id": 0, "grn_id": 1, "linked_dc_ids": 1, "bill_number": 1},
        )
        if not bill:
            return None
        receipt_ids = [g for g in [bill.get("grn_id")] if g]
        receipt_ids += [d for d in (bill.get("linked_dc_ids") or []) if d]
        if not receipt_ids:
            return None
        grns = db.get_collection("grns")
        notes = db.get_collection("vendor_debit_notes")
        for grn_id in receipt_ids:
            grn = grns.find_one(
                {"grn_id": grn_id}, {"_id": 0, "items": 1, "grn_number": 1}
            )
            if not grn:
                continue
            rejected = 0
            for it in grn.get("items") or []:
                try:
                    rejected += int(it.get("rejected_qty") or 0)
                except (TypeError, ValueError):
                    continue
            if rejected <= 0:
                continue
            if any(
                notes.find_one(flt, {"_id": 0, "debit_note_id": 1})
                for flt in ({"grn_id": grn_id}, {"bill_id": bill_id})
            ):
                continue
            return (
                f"{rejected} unit(s) on goods receipt "
                f"{grn.get('grn_number') or grn_id} were rejected and no debit "
                f"note has been raised against the vendor. Raise the debit note "
                f"for the rejected goods first - then this bill can be paid."
            )
        return None
    except Exception:  # noqa: BLE001 - a lookup failure must not block payment
        return None
