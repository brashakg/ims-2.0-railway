"""Vendor payments, debit notes and the vendor ledger."""

from ._shared import (
    Depends,
    HTTPException,
    Optional,
    Query,
    _AP_ROLES,
    _get_db,
    ap_engine,
    datetime,
    get_vendor_repository,
    require_roles,
    resolve_store_scope,
    router,
    uuid,
    validate_store_access,
)
from .ap_bills import (
    DebitNoteCreate,
    VendorPaymentCreate,
    _clean,
    _recompute_bill_status,
    _rejected_goods_hold,
)
from ._shared import can_access_store_scoped

# The answer for a named bill that is not there for the caller -- the words
# purchase_invoices._bill_in_scope_or_404 uses, so another shop's bill and a
# bill that does not exist answer alike.
_NO_BILL = "Purchase invoice not found"


def _named_bill_in_scope_or_404(
    db, vendor_id: str, bill_id: Optional[str], current_user: dict
) -> None:
    """The one Purchase shop scope (F63) on the bill a payment or debit note
    names: ADMIN / SUPERADMIN reach every shop, everyone else only the bill's
    own shop (purchase_invoices._bill_in_scope_or_404, the rule every bill
    read by id applies). Another shop's bill, another supplier's, or none at
    all answers the SAME 404 -- so the door never confirms another shop's bill
    exists -- before anything is written. No bill named (on account): nothing
    to check. Returns the bill (None when none is named)."""
    if not bill_id or db is None:
        return None
    from ..purchase_invoices import _bill_in_scope_or_404

    try:
        bill = db.get_collection("vendor_bills").find_one(
            {"bill_id": bill_id, "vendor_id": vendor_id}, {"_id": 0}
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    if bill is None:
        raise HTTPException(status_code=404, detail=_NO_BILL)
    _bill_in_scope_or_404(bill, current_user)
    return bill


def _named_receipt_in_scope_or_404(
    db, vendor_id: str, grn_id: Optional[str], current_user: dict
) -> Optional[dict]:
    """The same shop rule (F63) on the goods receipt a debit note names: the
    receipt must exist, be THIS supplier's, and be in the caller's reach
    (can_access_store_scoped: ADMIN / SUPERADMIN every shop, everyone else
    their own; a receipt with no shop, only an admin). Anything else is the
    SAME 404 a missing receipt gets -- create_vendor_bill's words -- before
    anything is written. Without it a Pune accountant's note naming Dhanbad's
    receipt released Dhanbad's rejected-goods payment hold (owner ruling 7:
    _rejected_goods_hold clears on ANY note naming the receipt) and booked
    the credit to Pune; a receipt that did not exist was accepted too. No
    receipt named: nothing to check. Returns the receipt (None when none)."""
    if not grn_id or db is None:
        return None
    try:
        grn = db.get_collection("grns").find_one(
            {"grn_id": grn_id}, {"_id": 0, "grn_id": 1, "vendor_id": 1, "store_id": 1}
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    if (
        not grn
        or grn.get("vendor_id") != vendor_id
        or not can_access_store_scoped(grn.get("store_id"), current_user)
    ):
        raise HTTPException(status_code=404, detail=f"GRN {grn_id} not found")
    return grn


def _shop_by_the_suppliers_bills(db, vendor_id: str, money: dict) -> Optional[str]:
    """The shop the supplier ledger gives money that names no bill and carries
    no shop -- ap_engine.supplier_rows' legacy rule (the supplier's latest
    bill dated on or before the money, else its earliest bill; transfer
    mirror bills left out) -- worked out NOW, from the supplier's bills as
    they stand, so the caller can STAMP it. supplier_rows itself decides:
    `money` (its date fields only) is offered to it once per shop the
    supplier has billed, so the shop stamped is exactly the shop the ledger
    would have placed the row in at this moment.

    None when the supplier has never billed a shop (or the bill the rule
    picks has no shop): the money stays unstamped, under all stores only.

    Unreadable bills are a 503: writing the row unstamped would let its shop
    move later, which is what stamping is for."""
    if db is None or not vendor_id:
        return None
    try:
        bills = list(
            db.get_collection("vendor_bills").find({"vendor_id": vendor_id}, {"_id": 0})
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    probe = {**money, "vendor_id": vendor_id, "bill_id": None, "store_id": None}
    shops = sorted({b.get("store_id") for b in bills if isinstance(b, dict) and b.get("store_id")})
    for shop in shops:
        if ap_engine.supplier_rows(bills, [probe], [], shop)[1]:
            return shop
    return None


def _money_shop(
    bill: Optional[dict],
    asked_query: Optional[str],
    asked_body: Optional[str],
    current_user: dict,
    receipt: Optional[dict] = None,
    *,
    db=None,
    vendor_id: Optional[str] = None,
    money: Optional[dict] = None,
) -> Optional[str]:
    """The ONE shop a payment or debit note is recorded in (F63): STAMPED on
    the row at write time, so ap_engine.supplier_rows books it to that shop's
    share for good -- never re-guessed on a later read (a Pune accountant's
    on-account cheque used to land in Dhanbad's ledger because Dhanbad billed
    last; an admin's unstamped payment moved shops when another shop later
    booked a back-dated bill, so a closed month changed and the first shop
    owed a paid bill again).

      * money naming a bill: that bill's shop -- it settles that bill, so it
        is that shop's money (a bill with no shop: no shop; the ledger puts
        money naming a bill with the bill, whatever is stamped);
      * a debit note naming a goods receipt with a shop, and no bill: that
        receipt's shop -- the rejected goods it credits are that shop's;
      * else (on account, or a receipt with NO shop on record -- a legacy
        receipt only an admin reaches) the shop asked for (?store_id or the
        body's store_id);
      * else a non-admin's own active shop;
      * else (ADMIN / SUPERADMIN, or a login with no shop, naming none) the
        supplier's shop by its bills -- _shop_by_the_suppliers_bills, the
        ledger's own legacy rule -- worked out now and stamped. Never the
        admin's topbar shop: that is where HE sits, not the supplier. If the
        supplier bills more than one shop the rule still picks one (the Cash
        Flow form offers an explicit Shop select). A supplier that has never
        billed a shop: unstamped, under all stores only.

    A shop asked for passes validate_store_access first: a non-admin naming
    another shop is refused (403) before anything is written. Asking for a
    shop other than the named bill's or receipt's -- or naming a bill and a
    receipt of two different shops -- is a contradiction (422), never
    silently re-filed."""
    if asked_query and asked_body and asked_query != asked_body:
        raise HTTPException(
            status_code=422,
            detail="store_id in the address and in the body disagree",
        )
    asked = asked_query or asked_body
    if asked:
        asked = validate_store_access(asked, current_user)
    if bill is not None and receipt is not None:
        bill_shop, receipt_shop = bill.get("store_id"), receipt.get("store_id")
        if bill_shop and receipt_shop and bill_shop != receipt_shop:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This bill and this goods receipt belong to different "
                    "shops. Name the bill, or the receipt, of one shop."
                ),
            )
    if bill is not None:
        shop = bill.get("store_id")
        if asked and asked != shop:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This bill is booked to another shop's account; money "
                    "against it belongs to the bill's shop. Leave the shop out."
                    if shop
                    else "This bill has no shop on record, so money against it "
                    "stays with the bill, under all stores only. Leave the shop out."
                ),
            )
        return shop
    if receipt is not None and receipt.get("store_id"):
        shop = receipt["store_id"]
        if asked and asked != shop:
            raise HTTPException(
                status_code=422,
                detail=(
                    "This goods receipt is booked to another shop's account; "
                    "money against it belongs to the goods receipt's shop. "
                    "Leave the shop out."
                ),
            )
        return shop
    shop = asked or resolve_store_scope(None, current_user)
    if shop:
        return shop
    return _shop_by_the_suppliers_bills(db, vendor_id, money or {})


def _ledger_rows(db, vendor_id: str, scope: Optional[str]) -> tuple:
    """EVERY recorded (bill, payment, debit note) of one supplier by THE row
    rule every 'what we owe' figure reads (ap_engine.supplier_rows): no
    transfer mirror bills, and with `scope` (resolve_store_scope) one shop's
    share -- a bill's own shop; money naming a bill, that bill's shop; other
    money, the shop stamped on it (legacy rows: the supplier's latest bill on
    or before it). No as-of cutoff here: the lists show every recorded row,
    post-dated ones flagged, and the ledger strikes its balance on today
    (ap_engine.split_as_of). So a Pune accountant's ledger, payments and debit
    notes are Pune's share, the same figures /finance/vendor-payments?store_id=
    and the Purchases report give that shop. ALL the supplier's bills are
    read, so money naming a bill finds the bill's shop."""
    try:
        bills = list(
            db.get_collection("vendor_bills").find({"vendor_id": vendor_id}, {"_id": 0})
        )
        payments = list(
            db.get_collection("vendor_payments").find(
                {"vendor_id": vendor_id}, {"_id": 0}
            )
        )
        debit_notes = list(
            db.get_collection("vendor_debit_notes").find(
                {"vendor_id": vendor_id}, {"_id": 0}
            )
        )
    except Exception:
        bills, payments, debit_notes = [], [], []
    return ap_engine.supplier_rows(bills, payments, debit_notes, scope)


def _flag_post_dated(rows: list) -> list:
    """Every recorded row, each flagged post_dated (dated after today: not yet
    counted in the balance), newest first by its ledger day."""
    for row in rows:
        row["post_dated"] = ap_engine.is_post_dated(row)
    rows.sort(key=ap_engine.ledger_day, reverse=True)
    return rows


@router.post("/{vendor_id}/payments", status_code=201)
async def create_vendor_payment(
    vendor_id: str,
    payment: VendorPaymentCreate,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
    # ?store_id: the shop the money is for (_money_shop). A plain default, not
    # Query(None), so the handler stays callable directly (tests do).
    store_id: Optional[str] = None,
):
    """Record a payment to a vendor (optionally allocated to a bill, optionally
    with TDS withheld). Recomputes the allocated bill's status."""
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None
    if vendor_repo is not None and vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # TDS: explicit amount wins; else auto-compute from section + base.
    tds_section = (payment.tds_section or "NONE").upper()
    if payment.tds_amount is not None:
        tds_amount = round(payment.tds_amount, 2)
    elif tds_section != "NONE":
        base = payment.tds_base if payment.tds_base is not None else payment.amount
        tds_amount = ap_engine.compute_tds(base, tds_section)["tds_amount"]
    else:
        tds_amount = 0.0

    # Accounting period lock: cannot record vendor payments into a closed month.
    db = _get_db()
    if db is not None:
        from ..finance import check_period_locked

        check_period_locked(db, payment.payment_date)

    # F63: the bill it settles is in the caller's shop -- checked before the
    # hold below, whose message would describe that bill -- and the money is
    # stamped with its shop (an admin's naming no bill and no shop: the
    # supplier's shop by its bills, worked out on this row's own dates).
    created_at = datetime.now().isoformat()
    named = _named_bill_in_scope_or_404(db, vendor_id, payment.bill_id, current_user)
    shop = _money_shop(
        named,
        store_id,
        payment.store_id,
        current_user,
        db=db,
        vendor_id=vendor_id,
        money={"payment_date": payment.payment_date, "created_at": created_at},
    )

    # Owner ruling 7: HOLD the bill while goods were rejected and no debit note
    # exists. Until now a rejection inside the 5% match tolerance was paid in
    # full, silently -- we paid for the defects AND claimed the ITC on them.
    hold = _rejected_goods_hold(db, payment.bill_id)
    if hold:
        raise HTTPException(
            status_code=409,
            detail={"code": "REJECTED_GOODS_NO_DEBIT_NOTE", "message": hold},
        )

    payment_id = str(uuid.uuid4())
    doc = {
        "payment_id": payment_id,
        "vendor_id": vendor_id,
        "vendor_name": (vendor or {}).get("trade_name")
        or (vendor or {}).get("legal_name"),
        "bill_id": payment.bill_id,
        "store_id": shop,
        "amount": round(payment.amount, 2),
        "mode": payment.mode,
        "payment_date": payment.payment_date,
        "tds_section": tds_section,
        "tds_base": payment.tds_base,
        "tds_amount": tds_amount,
        "reference": payment.reference,
        "notes": payment.notes,
        "created_by": current_user.get("user_id"),
        "created_at": created_at,
    }
    db = _get_db()
    if db is not None:
        try:
            db.get_collection("vendor_payments").insert_one(dict(doc))
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail="Failed to save payment"
            ) from exc
        _recompute_bill_status(db, payment.bill_id)
    return _clean(doc)


@router.get("/{vendor_id}/payments")
async def list_vendor_payments(
    *,
    store_id: Optional[str] = Query(None),
    vendor_id: str,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """List a vendor's payments (newest first)."""
    # The ledger's payment rows in the caller's shop scope (F63): ADMIN /
    # SUPERADMIN every shop or the one asked for; everyone else their own.
    scope = resolve_store_scope(store_id, current_user)
    db = _get_db()
    if db is None:
        return {"payments": [], "total": 0}
    # Every recorded payment, a post-dated cheque included (flagged): the
    # accountant must see the cheque just keyed even though the balance does
    # not count it until its day.
    rows = _flag_post_dated(_ledger_rows(db, vendor_id, scope)[1])
    return {"payments": rows, "total": len(rows)}


@router.post("/{vendor_id}/debit-notes", status_code=201)
async def create_debit_note(
    vendor_id: str,
    note: DebitNoteCreate,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
    # ?store_id: the shop the money is for (_money_shop). A plain default, not
    # Query(None), so the handler stays callable directly (tests do).
    store_id: Optional[str] = None,
):
    """Issue a debit note against a vendor (e.g. for rejected/returned goods).
    Reduces the payable. Recomputes the allocated bill's status."""
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None
    if vendor_repo is not None and vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    # F63: the bill it reduces and the goods receipt it credits are this
    # supplier's and in the caller's shop (else the same 404 a missing one
    # gets, before anything is written), and the note is stamped with their
    # shop (a receipt with no shop on record: the shop asked for; an admin's
    # naming none: the supplier's shop by its bills).
    db_early = _get_db()
    named = _named_bill_in_scope_or_404(db_early, vendor_id, note.bill_id, current_user)
    receipt = _named_receipt_in_scope_or_404(db_early, vendor_id, note.grn_id, current_user)
    created_at = datetime.now().isoformat()
    shop = _money_shop(
        named,
        store_id,
        note.store_id,
        current_user,
        receipt,
        db=db_early,
        vendor_id=vendor_id,
        money={"date": note.date, "created_at": created_at},
    )

    dn_id = str(uuid.uuid4())
    prefix = vendor_id[:3].upper() if vendor_id else "DN"
    doc = {
        "debit_note_id": dn_id,
        "debit_note_number": f"DN-{prefix}-{datetime.now().strftime('%y%m%d%H%M')}",
        "vendor_id": vendor_id,
        "vendor_name": (vendor or {}).get("trade_name")
        or (vendor or {}).get("legal_name"),
        "bill_id": note.bill_id,
        "store_id": shop,
        "grn_id": note.grn_id,
        "amount": round(note.amount, 2),
        "date": note.date,
        "reason": note.reason,
        # Credit-note category (RETURN_CN / SCHEME_CN / DISCOUNT_CN / QUALITY_CN).
        # `source` mirrors it for parity with the machine-posted rebate CN rows
        # (rebate_engine writes source=VOLUME_REBATE) so every AP/ledger reader
        # can categorise a credit note by a single field.
        "cn_type": note.cn_type,
        "source": note.cn_type,
        "created_by": current_user.get("user_id"),
        "created_at": created_at,
    }
    db = _get_db()
    if db is not None:
        try:
            db.get_collection("vendor_debit_notes").insert_one(dict(doc))
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail="Failed to save debit note"
            ) from exc
        _recompute_bill_status(db, note.bill_id)
    return _clean(doc)


@router.get("/{vendor_id}/debit-notes")
async def list_debit_notes(
    *,
    store_id: Optional[str] = Query(None),
    vendor_id: str,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """List a vendor's debit notes (newest first)."""
    # The ledger's debit-note rows in the caller's shop scope (F63).
    scope = resolve_store_scope(store_id, current_user)
    db = _get_db()
    if db is None:
        return {"debit_notes": [], "total": 0}
    # Every recorded note, a post-dated one included (flagged).
    rows = _flag_post_dated(_ledger_rows(db, vendor_id, scope)[2])
    return {"debit_notes": rows, "total": len(rows)}


@router.get("/{vendor_id}/ledger")
async def vendor_ledger(
    *,
    store_id: Optional[str] = Query(None),
    vendor_id: str,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Full vendor ledger: bills (credit) + payments + debit notes (debit) with
    a running payable balance, plus an aging snapshot for the same vendor."""
    # The caller's shop scope (F63): ADMIN / SUPERADMIN every shop or the one
    # asked for; everyone else their own shop's share, even with store_id
    # dropped, and another shop asked for is a 403.
    scope = resolve_store_scope(store_id, current_user)
    db = _get_db()
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None
    if db is None:
        return {
            "vendor_id": vendor_id,
            "vendor": vendor,
            "ledger": {**ap_engine.build_ledger([], [], []), "post_dated": []},
            "aging": ap_engine.build_aging([], [], []),
        }
    # The one row rule every payable screen reads (no transfer mirror bills),
    # struck on today: the entries, running balance and closing balance count
    # only rows dated up to today, so the closing balance is the Suppliers
    # card's and the report's figure. The rows dated later -- a post-dated
    # cheque, a bill keyed ahead -- are recorded, so they are listed apart in
    # `post_dated` (same entry shape, no running balance), never hidden.
    counted, later = ap_engine.split_as_of(_ledger_rows(db, vendor_id, scope))
    return {
        "vendor_id": vendor_id,
        "vendor": vendor,
        "ledger": {
            **ap_engine.build_ledger(*counted),
            "post_dated": ap_engine.post_dated_entries(*later),
        },
        "aging": ap_engine.build_aging(*counted),
    }
