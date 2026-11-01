"""Accounts payable: bill models, AP aging and vendor bills."""

from api.utils.event_time import utc_now_iso
from api.utils.ist import now_ist_naive

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
    get_audit_repository,
    datetime,
    field_validator,
    get_grn_repository,
    get_vendor_repository,
    logger,
    require_roles,
    resolve_store_scope,
    router,
    uuid,
    can_access_store_scoped,
)
from .models import GRN_SUBTYPE_DC

# The 17(5) tick: a services / expense bill's, or -- when it is booked -- a
# bill for goods bought WITHOUT a purchase order (gifts, samples, personal
# use; LOW 13), whose GST then goes into those goods' cost. A purchase-order
# stock bill keeps its input-credit switch (Purchase > Invoices).
_ITC_BLOCKED_SERVICES_ONLY = {
    "code": "ITC_BLOCKED_SERVICES_ONLY",
    "message": (
        "'Credit not claimable (section 17(5))' is for services and expense "
        "bills (food, vehicles, club fees), and for goods bought without a "
        "purchase order when their bill is booked. A purchase-order stock bill "
        "keeps its input-credit switch under Purchase > Invoices."
    ),
}


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
    # This door books no reverse charge (the line-detail door does). Read only
    # so a walk-in bill sent with it is refused in words, as on that door
    # (purchase_invoices.refuse_walk_in_reverse_charge), never booked quietly.
    reverse_charge: bool = False
    # The shop the bill is for (owner ruling 2026-10-12, R9). REQUIRED from
    # ADMIN / SUPERADMIN when no goods receipt places the bill (rent,
    # freight): never his topbar shop. A receipt's bill keeps the receipt's
    # shop; anyone else's is their own. See purchase_invoices._bill_shop.
    store_id: Optional[str] = None
    # Section 17(5): the accountant's "credit not claimable" tick for a
    # services / expense bill (food, motor vehicles, club fees ...; owner and
    # CA 2026-10-18). Ticked, the bill's GST is never input credit (GSTR-3B
    # Table 4, the Finance GST tab, the ITC register) -- it stays in what the
    # bill costs. Not sent: the expense type's default (ap_engine.
    # EXPENSE_TYPES), unticked when no type is named; changeable later through
    # PUT /{vendor_id}/bills/{bill_id}/itc-blocked until the month is locked.
    itc_blocked: Optional[bool] = None
    # What the expense is for (ap_engine.EXPENSE_TYPES): sets the default tick.
    expense_type: Optional[str] = None

    @field_validator("expense_type", mode="before")
    @classmethod
    def _known_expense_type(cls, v):
        return ap_engine.normalize_expense_type(v)

    @field_validator("bill_kind", mode="before")
    @classmethod
    def _normalize_bill_kind(cls, v):
        return ap_engine.normalize_bill_kind(v)

    @field_validator("bill_date", mode="before")
    @classmethod
    def _iso_bill_date(cls, v):
        return ap_engine.iso_bill_date(v)


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
    # The shop the money is paid for (F63) when no bill is named; a named
    # bill's shop wins. See ap_payments._money_shop.
    store_id: Optional[str] = None
    # One id per payment the form records: a retried request (the client
    # retries a 5xx, a 504 can come after the save) answers the payment it
    # already recorded, never a second one. See ap_payments.create_vendor_payment.
    request_id: Optional[str] = Field(default=None, min_length=8, max_length=64)

    @field_validator("payment_date", mode="before")
    @classmethod
    def _iso_payment_date(cls, v):
        return ap_engine.iso_payment_date(v)


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
    # The shop the note is for (F63) when no bill is named; a named bill's
    # shop wins. See ap_payments._money_shop.
    store_id: Optional[str] = None

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


def _left_to_pay(stamped: dict) -> float:
    """What can still be paid on a bill stamped by stamp_bills_as_of: today's
    outstanding less the money recorded for later days (a post-dated cheque),
    i.e. what is left once everything recorded clears. THE figure: the
    payment form shows it (list_vendor_bills' left_to_pay) and
    _refuse_more_than_bill_left tests money against it."""
    out = float(stamped.get("outstanding") or 0) - float(stamped.get("post_dated_money") or 0)
    return round(max(out, 0.0), 2)


# A payment may pass what is left on its bill by up to Re 1 (rounding on the
# supplier's side or ours); beyond that it is refused.
_LEFT_TOLERANCE = 1.0


def _refuse_more_than_bill_left(db, bill: Optional[dict], gross: float) -> None:
    """Money applied to a bill can never exceed what is left on it once every
    recorded payment and credit naming it clears (A12-2): the excess would sit
    against this bill, out of reach of the supplier's other bills. 422 with
    what is left, before anything is written; 503 when unreadable."""
    if bill is None or db is None:
        return
    from ..purchase_invoices import stamp_bills_as_of

    try:
        left = _left_to_pay(stamp_bills_as_of(db, [dict(bill)])[0])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail="What is left on this bill could not be read. Try again."
        ) from exc
    if round(gross - left, 2) > _LEFT_TOLERANCE:
        number = bill.get("bill_number") or bill.get("bill_id")
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MORE_THAN_BILL_LEFT",
                "message": (
                    f"Only Rs {left:,.2f} is left to pay on bill {number}. Apply up to "
                    "that to the bill, and record the rest as an advance (no bill)."
                ),
                "left": left,
            },
        )


def _recompute_bill_status(db, bill_id: Optional[str]) -> None:
    """Re-derive a bill's stored status (OUTSTANDING / PARTIAL / PAID) and
    outstanding from its allocated payments + debit notes. Fail-soft.

    The STORED pair counts every RECORDED payment and note, a post-dated
    cheque included ('what is left once what is recorded clears'), so it is
    never stale -- nothing has to rewrite it on the cheque's day. It is NEVER
    served: every reader that hands a bill to a screen (the Purchase Invoices
    list and detail, /vendors/{id}/bills, the PO timeline) restrikes status and
    outstanding on today through purchase_invoices.stamp_bills_as_of
    (ap_engine.bill_as_of) -- the supplier ledger's as-of rule, so a bill a
    post-dated cheque covers reads PARTIAL / OUTSTANDING until the cheque's
    day, as AP aging, the Purchases report and Cash Flow count it."""
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
        from ...services.supplier_credit_reads import payable_credit_rows
        debit_notes = payable_credit_rows(db, bill_id=bill_id)
        st = ap_engine.bill_settlement(bill, payments, debit_notes)
        db.get_collection("vendor_bills").update_one(
            {"bill_id": bill_id},
            {"$set": {"outstanding": st["outstanding"], "status": st["status"]}},
        )
    except Exception:
        pass


@router.get("/expense-types")
async def expense_types(current_user: dict = Depends(require_roles(*_AP_ROLES))):
    """THE expense types and their section 17(5) default (ap_engine.
    EXPENSE_TYPES), for the bill form's tick. Accounts roles: the bill's own."""
    return {"types": [
        {"value": key, "label": label, "blocked_by_default": blocked}
        for key, label, blocked in ap_engine.EXPENSE_TYPES
    ]}


@router.get("/ap-aging")
async def ap_aging(
    as_of: Optional[str] = Query(None, description="ISO date; defaults to today"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Accounts-payable aging, grouped by vendor + grand totals.

    Buckets each outstanding bill by days past its due date (current / 1-30 /
    31-60 / 61-90 / 90+). ADMIN / ACCOUNTANT only, in the caller's shop scope.
    """
    # The one Purchase shop scope (F63), as the vendor ledger and the Suppliers
    # card apply it: ADMIN / SUPERADMIN every shop or the one asked for; a Pune
    # accountant ages Pune's share, so each row here is the ledger it opens.
    scope = resolve_store_scope(store_id, current_user)
    db = _get_db()
    if db is None:
        return {"as_of": as_of, "totals": {}, "vendors": []}
    from ..finance import _ap_rows  # the one AP row loader (call time: no cycle)

    return ap_engine.build_aging_by_vendor(*_ap_rows(db, scope, as_of=as_of), as_of)


def _billed_elsewhere_unnamed(
    db, grn_id: str, linked: Optional[dict], exc: HTTPException, current_user: dict
) -> HTTPException:
    """The 409 'this receipt is already billed' names the bill that billed it
    (assert_grn_billable_header_only: 'already billed by <bill>'; a Delivery
    Challan: 'already matched to invoice <id>'). When that bill is outside the
    caller's shop scope -- a legacy bill with no shop, or one booked to another
    shop -- the refusal still says the receipt is billed (the receipt is the
    caller's), but never names the other bill. Every other refusal passes
    through unchanged."""
    if exc.status_code != 409:
        return exc
    detail = exc.detail
    if isinstance(detail, dict) and detail.get("code") == "grn_already_billed":
        flt = {"grn_id": grn_id}
    elif (
        isinstance(detail, str)
        and (linked or {}).get("linked_bulk_invoice_id")
        and str(linked["linked_bulk_invoice_id"]) in detail
    ):
        flt = {"bill_id": linked["linked_bulk_invoice_id"]}
    else:
        return exc
    try:
        other = (
            db.get_collection("vendor_bills").find_one(flt, {"_id": 0, "store_id": 1})
            if db is not None
            else None
        )
    except Exception:  # noqa: BLE001 - unknown: do not name it
        other = None
    if other is not None and can_access_store_scoped(other.get("store_id"), current_user):
        return exc
    return HTTPException(
        status_code=409,
        detail={
            "code": "grn_already_billed",
            "message": f"Goods receipt {grn_id} is already billed. Nothing was recorded.",
            "grn_id": grn_id,
        },
    )


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
                        "tallied before the purchase is final. "
                        + ap_engine.NO_RECEIPT_WAY_OUT
                    ),
                },
            )

    itc_blocked = ap_engine.expense_itc_blocked(bill.expense_type, bill.itc_blocked)
    if itc_blocked and not bill.grn_id and bill.bill_kind != ap_engine.BILL_KIND_SERVICES:
        raise HTTPException(status_code=422, detail=_ITC_BLOCKED_SERVICES_ONLY)

    linked = None  # the goods receipt this bill names, read below
    # This door accepts a grn_id but validated NOTHING about it -- so the same
    # two money leaks the first-class purchase-invoice door had were reachable
    # here too: bill another vendor's receipt, or bill one receipt again and
    # again. Reuse the purchase-invoice guards (imported at call time, like
    # check_period_locked above, so no cross-router import cycle).
    #
    # A DELIVERY CHALLAN receipt is billable here too (a supplier's goods that
    # came on a challan, often with no PO -- THIS is the screen the accountant
    # records the bill on; a walk-in buy has its own "Bought without
    # PO" receipt, D14 - credit only from a registered supplier, G2 - billed
    # like a PO receipt below). The DC takes the
    # same one-bill-per-receipt stance as the STANDARD header guard, enforced
    # by CLAIMING the DC (dc_matched) before the bill is written -- so neither
    # a second header bill nor a later consolidated /from-dcs invoice can bill
    # the same goods again.
    _dc_receipt = None
    _receipt_store = None
    if bill.grn_id:
        from ..purchase_invoices import (
            assert_grn_billable_header_only,
            _load_linked_dcs,
            _assert_dcs_single_vendor_store,
        )

        grn_repo = get_grn_repository()
        linked = grn_repo.find_by_id(bill.grn_id) if grn_repo is not None else None
        # F63: another shop's receipt (or one with no shop, for a non-admin)
        # is not there for the caller -- the SAME 404 a missing receipt gets
        # (_load_standard_grn's words), before anything is claimed or written.
        # Without this a Pune accountant booked a bill on Dhanbad's receipt
        # into Dhanbad's books and read it back in the 201.
        if linked is not None and not can_access_store_scoped(
            linked.get("store_id"), current_user
        ):
            raise HTTPException(status_code=404, detail=f"GRN {bill.grn_id} not found")
        _receipt_store = (linked or {}).get("store_id")
        from ..purchase_invoices import refuse_walk_in_reverse_charge

        refuse_walk_in_reverse_charge(bill.reverse_charge, linked)
        try:
            if linked is not None and linked.get("grn_subtype") == GRN_SUBTYPE_DC:
                # 404 missing / 400 not-ACCEPTED / 409 already-matched.
                dc_docs = _load_linked_dcs(db_early, [bill.grn_id])
                # 409 mixed_vendors when the DC belongs to another vendor.
                _assert_dcs_single_vendor_store(dc_docs, expected_vendor_id=vendor_id)
                _dc_receipt = dc_docs[0] if dc_docs else None
            else:
                assert_grn_billable_header_only(db_early, bill.grn_id, vendor_id, current_user)
        except HTTPException as exc:
            unnamed = _billed_elsewhere_unnamed(db_early, bill.grn_id, linked, exc, current_user)
            if unnamed is exc:
                raise
            raise unnamed from None
        # LOW 13: goods bought without a PO may carry the 17(5) tick; a PO
        # receipt's stock bill may not.
        if itc_blocked and ((linked or {}).get("po_id") or (linked or {}).get("grn_subtype") not in (
            ap_engine.GRN_SUBTYPE_NO_PO, GRN_SUBTYPE_DC,
        )):
            raise HTTPException(status_code=422, detail=_ITC_BLOCKED_SERVICES_ONLY)
        if (linked or {}).get("grn_subtype") == ap_engine.GRN_SUBTYPE_NO_PO:
            from ...services.purchase_invoice_engine import lines_from_grn

            if any(line.get("unit_price") is None for line in lines_from_grn(linked, None)):
                raise HTTPException(status_code=422, detail={
                    "code": "INVOICE_PRICE_REQUIRED",
                    "message": (
                        "This receipt has unpriced items. Open Purchase > Invoices > "
                        "Create from GRN and enter each supplier price before booking the bill."
                    ),
                })

    # Duplicate bill guard: THE same-bill rule (purchase_invoices.
    # find_duplicate_bill -> purchase_invoice_engine.same_bill), the one the
    # receiving door and the line-detail invoice door use. A double entry
    # would double the outstanding payable and the ledger's payment row; a
    # number the supplier reuses in a new financial year is a new bill.
    from ..purchase_invoices import find_duplicate_bill
    from ...services.purchase_invoice_engine import bill_key

    if db_early is not None:
        try:
            dup = find_duplicate_bill(
                db_early,
                vendor_id,
                bill.bill_number,
                bill.bill_date,
                po_id=bill.po_id,
            )
            if dup:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Bill number '{bill.bill_number}' is already recorded "
                        f"for this vendor in this financial year. Duplicate "
                        f"vendor invoices are not allowed."
                    ),
                )
        except HTTPException:
            raise
        except Exception:
            pass  # fail-soft: skip dup check on DB error, proceed with insert

    # F40 on this door too: the recipient company + GSTIN and the tax heads
    # every bill door stores, from the SAME rule (purchase_invoices.
    # _bill_recipient + the engine's classify_supply). This door used to write
    # none of them, so its credit counted in the ITC register and in no
    # GSTR-3B. Refused (422) before anything is claimed or written.
    #
    # R9: the shop is THE bill-shop rule's (purchase_invoices._bill_shop): the
    # receipt's; with none an admin must name it (422 BILL_SHOP_REQUIRED) --
    # it used to be his topbar shop, so a Pune rent bill keyed at HQ sat in
    # HQ's books on HQ's GSTIN; anyone else's own shop, as before.
    from ..purchase_invoices import _bill_shop_and_recipient, _vendor_gstin
    from ...services.purchase_invoice_engine import split_header_tax
    from ...services.org_validation import itc_claimable

    supplier_gstin = _vendor_gstin(db_early, vendor, vendor_id)
    bill_shop, recipient = _bill_shop_and_recipient(
        db_early,
        _receipt_store,
        bill.store_id,
        None,
        current_user,
        gstin_box=False,
    )
    heads = split_header_tax(
        bill.tax_amount, supplier_gstin, recipient.get("recipient_gstin")
    )

    credit_days = int((vendor or {}).get("credit_days", 30) or 30)
    due_date = ap_engine.compute_due_date(bill.bill_date, credit_days)
    bill_id = str(uuid.uuid4())
    doc = {
        "bill_id": bill_id,
        "vendor_id": vendor_id,
        # The receipt's shop; else the admin's pick (R9), else the booker's
        # own -- the shop the recipient was read off: every Purchase tab
        # scopes on it (F63).
        "store_id": bill_shop,
        "vendor_name": (vendor or {}).get("trade_name")
        or (vendor or {}).get("legal_name"),
        "bill_number": bill.bill_number,
        "bill_date": bill.bill_date,
        # The bill's identity for the uniq_vendor_bill_number_key index (the
        # same-bill rule's atomic twin, shared with the line-detail door).
        "bill_number_key": bill_key(bill.bill_number, bill.bill_date),
        "due_date": due_date,
        "credit_days": credit_days,
        "vendor_gstin": supplier_gstin,
        "recipient_entity_id": recipient.get("recipient_entity_id"),
        "recipient_gstin": recipient.get("recipient_gstin"),
        "place_of_supply": heads["itc_place_of_supply"],
        "supply_place_recipient": heads["place_of_supply"],
        "supplier_state": heads["supplier_state"],
        "interstate": heads["interstate"],
        "taxable_amount": round(bill.taxable_amount, 2),
        "tax_amount": round(bill.tax_amount, 2),
        "cgst_total": heads["cgst_total"],
        "sgst_total": heads["sgst_total"],
        "igst_total": heads["igst_total"],
        "total_amount": round(bill.total_amount, 2),
        "outstanding": round(bill.total_amount, 2),
        # No valid supplier GSTIN, no input credit (a bill for goods bought
        # without a PO, G2, the same) -- purchase_invoices does the same on
        # its doors: one rule, org_validation.itc_claimable. A 17(5) tick
        # switches it off too, and says why (itc_blocked).
        "itc_eligible": itc_claimable(supplier_gstin, False, not itc_blocked),
        "itc_blocked": itc_blocked,
        "expense_type": bill.expense_type,
        "po_id": bill.po_id,
        "grn_id": bill.grn_id,
        # A receipt-linked bill IS a goods bill whatever the caller declared;
        # otherwise the declared kind (the gate above proved it is SERVICES).
        "bill_kind": ap_engine.BILL_KIND_GOODS if bill.grn_id else bill.bill_kind,
        "notes": bill.notes,
        "status": "OUTSTANDING",
        "created_by": current_user.get("user_id"),
        "created_at": utc_now_iso(),
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
            # The same-bill rule's atomic twin (uniq_vendor_bill_number_key):
            # a rival booking of this bill won the race -- the guard's 409.
            if exc.__class__.__name__ == "DuplicateKeyError":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Bill number '{bill.bill_number}' is already recorded "
                        f"for this vendor in this financial year. Duplicate "
                        f"vendor invoices are not allowed."
                    ),
                ) from exc
            raise HTTPException(status_code=500, detail="Failed to save bill") from exc
    # G1 on this door too: a walk-in receipt's bill costs its goods by THE
    # true-up the line-detail door runs (purchase_invoices.
    # _apply_valuation_trueup: the price paid, plus the bill's GST when it
    # claims no credit). This form names no lines, so each receipt line
    # carries the bill's tax share as its rate.
    # ponytail: one share for every line; a mixed-rate walk-in bill wants the
    # line-detail door (Purchase -> Invoices -> Create from GRN).
    # F04 (owner + CA 2026-10-18): a PO receipt's bill claiming no credit (an
    # unregistered supplier's, on this door) trues up the same way, priced
    # from its order; one claiming credit leaves the cost as received.
    walk_in = (linked or {}).get("grn_subtype") == ap_engine.GRN_SUBTYPE_NO_PO
    if db is not None and linked and (walk_in or doc["itc_eligible"] is False):
        _true_up_header_bill(db, {**doc, "invoice_id": bill_id}, linked)
    if db is not None:
        # Pieces of this receipt written off before the bill came reverse the
        # credit it just claimed on them (services/writeoff_itc).
        from ...services.writeoff_itc import after_bill_booked

        after_bill_booked(db, doc, current_user.get("user_id", ""))
    return _clean(doc)


def _true_up_header_bill(db, doc: dict, linked: dict) -> None:
    """The cost true-up of a header-only bill on its receipt (G1 / F04): the
    receipt's lines carry the bill's tax share as their rate. A walk-in buy
    costs at the price paid; a PO receipt at the BILL's price -- its taxable
    value spread over the receipt's lines in their order-price proportion
    (evenly per piece when the order names no prices; LOW 17)."""
    from ..purchase_invoices import _apply_valuation_trueup, _resolved_purchase_config
    from ...services.purchase_invoice_engine import lines_from_grn

    walk_in = linked.get("grn_subtype") == ap_engine.GRN_SUBTYPE_NO_PO
    po = None
    if not walk_in and linked.get("po_id"):
        po = db.get_collection("purchase_orders").find_one({"po_id": linked["po_id"]}, {"_id": 0})
    taxable = float(doc.get("taxable_amount") or 0)
    share = ap_engine.header_bill_gst_rate(doc.get("tax_amount"), taxable) or 0.0
    lines = lines_from_grn(linked, po)
    if not walk_in:
        value = sum(float(ln.get("qty") or 0) * float(ln.get("unit_price") or 0) for ln in lines)
        pieces = sum(float(ln.get("qty") or 0) for ln in lines)
        if value > 0:
            lines = [{**ln, "unit_price": round(float(ln.get("unit_price") or 0) * taxable / value, 2)}
                     for ln in lines]
        elif pieces > 0:
            lines = [{**ln, "unit_price": round(taxable / pieces, 2)} for ln in lines]
    _apply_valuation_trueup(
        db, doc, {"lines": [{**ln, "gst_rate": share} for ln in lines]}, _resolved_purchase_config(db),
    )


@router.get("/{vendor_id}/bills")
async def list_vendor_bills(
    vendor_id: str,
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """List a vendor's bills (newest first), in the caller's shop scope."""
    db = _get_db()
    if db is None:
        return {"bills": [], "total": 0}
    from ..purchase_invoices import stamp_bills_as_of

    flt: dict = {"vendor_id": vendor_id}
    # The one Purchase shop scope (F63), as the purchase-invoice list applies
    # it: a supplier who serves two shops must not hand a Pune login Dhanbad's
    # bill ids to open. Admins see every shop (or the one asked for).
    scope = resolve_store_scope(store_id, current_user)
    if scope:
        flt["store_id"] = scope
    try:
        bills = list(db.get_collection("vendor_bills").find(flt, {"_id": 0}))
    except Exception:
        bills = []
    # Status and outstanding on the supplier ledger's as-of rule (today): a
    # post-dated cheque is not paid until its day, so it is noted
    # (post_dated_money / post_dated_until) instead of counted -- this list and
    # the ledger owe the same. The status filter reads that same status.
    bills = [b for b in stamp_bills_as_of(db, bills) if not status or b["status"] == status]
    bills.sort(key=lambda b: b.get("bill_date") or "", reverse=True)
    # What the supplier is owed, as the invoice list reads it (an RCM bill
    # booked before vendor_payable stored its GST in total_amount; its stored
    # outstanding/status are restamped by scripts/repair_rcm_bill_payable.py).
    for b in bills:
        b["total_amount"] = ap_engine.vendor_payable(b)
        b["left_to_pay"] = _left_to_pay(b)
    return {"bills": bills, "total": len(bills)}


class VendorBillItcBlocked(BaseModel):
    itc_blocked: bool


def _refuse_after_return_due(bill: dict) -> None:
    """M6 (GST review 2026-10-19): the 17(5) tick decides what the bill's
    month's GSTR-3B claims, so it cannot change once that return is due --
    the 20th of the following month, IST -- or filed. 422 in words; the
    change is the CA's, in the current return."""
    from datetime import date

    day = date.fromisoformat(ap_engine.iso_bill_date(bill.get("invoice_date") or bill.get("bill_date")))
    due = date(day.year + (day.month == 12), day.month % 12 + 1, 20)
    if now_ist_naive().date() > due:
        raise HTTPException(status_code=422, detail={
            "code": "GSTR3B_DUE_PASSED",
            "message": (
                f"GSTR-3B for {day.strftime('%B %Y')} is due/filed - record the change "
                "in the current return with your CA."
            ),
        })


@router.put("/{vendor_id}/bills/{bill_id}/itc-blocked")
async def set_vendor_bill_itc_blocked(
    vendor_id: str,
    bill_id: str,
    body: VendorBillItcBlocked,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Tick or untick 'credit not claimable (section 17(5))' on a booked
    services / expense bill (owner + CA 2026-10-18). Ticked, its GST is no
    input credit anywhere (itc_eligible False, itc_blocked True); unticked,
    the bill gets back the credit decision it had before the tick. Accounts
    roles only; 404 outside the caller's shops; 423 once the bill's month is
    locked (the one bill-period guard, fail-closed on an unusable date)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Bills are unavailable; please retry.")
    bills = db.get_collection("vendor_bills")
    bill = bills.find_one({"bill_id": bill_id, "vendor_id": vendor_id}, {"_id": 0})
    if bill is None or not can_access_store_scoped(bill.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Bill not found")
    # A goods bill bought without a PO, ticked when booked, may be UNTICKED
    # (review round 2): its unclaimed GST then leaves its units' cost and,
    # for a walk-in buy, the product's. It is never ticked after booking.
    goods_untick = bool(
        bill.get("grn_id") and bill.get("itc_blocked") and not body.itc_blocked
        and not bill.get("source_transfer_id")
    )
    if not goods_untick and (
        bill.get("grn_id")
        or bill.get("source_transfer_id")
        or bill.get("bill_kind") != ap_engine.BILL_KIND_SERVICES
    ):
        raise HTTPException(status_code=422, detail=_ITC_BLOCKED_SERVICES_ONLY)
    from ..purchase_invoices import _check_bill_period_open

    _check_bill_period_open(db, bill)
    _refuse_after_return_due(bill)
    was_blocked = bool(bill.get("itc_blocked"))
    if body.itc_blocked == was_blocked:
        return _clean(bill)
    if body.itc_blocked:
        update = {
            "itc_blocked": True,
            "itc_eligible": False,
            # The credit decision the tick overrides, given back on untick
            # (a bill booked with credit switched off stays off).
            "itc_eligible_before_17_5": bill.get("itc_eligible") is not False,
        }
    else:
        from ...services.org_validation import itc_claimable

        before = bill.get("itc_eligible_before_17_5")
        if before is None:  # ticked when booked: the booking rule decides
            gstin = ap_engine.bill_supplier_gstin(
                bill, lambda vid: ((db.get_collection("vendors").find_one({"vendor_id": vid}) or {}).get("gstin"))
            )
            before = itc_claimable(gstin, bill.get("reverse_charge"))
        update = {"itc_blocked": False, "itc_eligible": bool(before)}
    when = utc_now_iso()
    update.update({"itc_blocked_by": current_user.get("user_id"), "itc_blocked_at": when})
    # Guarded on the state read above: two accountants clicking at once
    # cannot interleave a stale 'before' value.
    done = bills.update_one(
        {"bill_id": bill_id, "itc_blocked": True if was_blocked else {"$ne": True}},
        {"$set": update, **({} if body.itc_blocked else {"$unset": {"itc_eligible_before_17_5": ""}})},
    )
    if not done.matched_count:
        raise HTTPException(status_code=409, detail="This bill changed meanwhile. Refresh and try again.")
    # Only when the untick really gives the credit back (round 3): an
    # unregistered supplier's GST stays no credit, so it stays in the cost.
    if goods_untick and update["itc_eligible"]:
        from ..purchase_invoices import _remove_unclaimed_gst

        _remove_unclaimed_gst(db, bill_id)
        linked = db.get_collection("grns").find_one({"grn_id": bill["grn_id"]}, {"_id": 0})
        if (linked or {}).get("grn_subtype") == ap_engine.GRN_SUBTYPE_NO_PO:
            # The walk-in cost is the latest price paid (re-settable): the
            # product's cost goes back to the price without the GST.
            # ponytail: a challan bill's moving average keeps the GST until
            # its next bill (logged by the review: DC moving-average blend).
            _true_up_header_bill(db, {**bill, **update, "invoice_id": bill_id}, linked)
        # The bill now claims credit on pieces written off while it was
        # ticked (they reversed nothing then): reverse them, in the bill's
        # month -- the month the credit lands. Once per piece, so repeat-safe.
        from ...services.writeoff_itc import after_bill_booked

        after_bill_booked(db, bills.find_one({"bill_id": bill_id}) or {**bill, **update},
                          current_user.get("user_id") or "")
    try:
        repo = get_audit_repository()
        if repo is not None:
            repo.create({
                "action": "vendor_bill.itc_blocked" if body.itc_blocked else "vendor_bill.itc_unblocked",
                "entity_type": "vendor_bill",
                "entity_id": bill_id,
                "user_id": current_user.get("user_id"),
                "severity": "INFO",
                "source": "vendors",
                "before_state": {k: bill.get(k) for k in ("itc_blocked", "itc_eligible")},
                "after_state": {k: update[k] for k in ("itc_blocked", "itc_eligible")},
            })
    except Exception:  # noqa: BLE001 - the audit row never undoes the change
        logger.warning("[AP] audit of the 17(5) tick on %s failed", bill_id, exc_info=True)
    return _clean(bills.find_one({"bill_id": bill_id}, {"_id": 0}) or {**bill, **update})


def _rejected_goods_hold(db, bill_id: Optional[str]) -> Optional[str]:
    """Verified accepted-only exception; billed rejects need confirmed credit."""
    if not bill_id:  # Explicit supplier advances have no bill to inspect.
        return None
    from ...services.receipt_payment_hold import rejected_receipt_hold
    return rejected_receipt_hold(db, bill_id)
