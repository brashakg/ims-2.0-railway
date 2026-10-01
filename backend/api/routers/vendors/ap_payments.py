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
)
from .ap_bills import (
    DebitNoteCreate,
    VendorPaymentCreate,
    _clean,
    _recompute_bill_status,
    _rejected_goods_hold,
)

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
    to check."""
    if not bill_id or db is None:
        return
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


def _ledger_rows(db, vendor_id: str, scope: Optional[str]) -> tuple:
    """(bills, payments, debit notes) of one supplier by THE row rule every
    'what we owe' figure reads (ap_engine.supplier_ledger_rows): no transfer
    mirror bills, nothing dated after today, and with `scope` (resolve_store_
    scope) one shop's share -- a bill's own shop; money naming a bill, that
    bill's shop; money naming none, the shop of the supplier's latest bill on
    or before it. So a Pune accountant's ledger, payments and debit notes are
    Pune's share, the same figures /finance/vendor-payments?store_id= and the
    Purchases report give that shop. ALL the supplier's bills are read, so
    money naming a bill finds the bill's shop."""
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
    return ap_engine.supplier_ledger_rows(bills, payments, debit_notes, scope)


@router.post("/{vendor_id}/payments", status_code=201)
async def create_vendor_payment(
    vendor_id: str,
    payment: VendorPaymentCreate,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
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
    # hold below, whose message would describe that bill.
    _named_bill_in_scope_or_404(db, vendor_id, payment.bill_id, current_user)

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
        "amount": round(payment.amount, 2),
        "mode": payment.mode,
        "payment_date": payment.payment_date,
        "tds_section": tds_section,
        "tds_base": payment.tds_base,
        "tds_amount": tds_amount,
        "reference": payment.reference,
        "notes": payment.notes,
        "created_by": current_user.get("user_id"),
        "created_at": datetime.now().isoformat(),
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
    rows = _ledger_rows(db, vendor_id, scope)[1]
    rows.sort(key=lambda p: p.get("payment_date") or "", reverse=True)
    return {"payments": rows, "total": len(rows)}


@router.post("/{vendor_id}/debit-notes", status_code=201)
async def create_debit_note(
    vendor_id: str,
    note: DebitNoteCreate,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Issue a debit note against a vendor (e.g. for rejected/returned goods).
    Reduces the payable. Recomputes the allocated bill's status."""
    vendor_repo = get_vendor_repository()
    vendor = vendor_repo.find_by_id(vendor_id) if vendor_repo is not None else None
    if vendor_repo is not None and vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    # F63: the bill it reduces is in the caller's shop.
    _named_bill_in_scope_or_404(_get_db(), vendor_id, note.bill_id, current_user)

    dn_id = str(uuid.uuid4())
    prefix = vendor_id[:3].upper() if vendor_id else "DN"
    doc = {
        "debit_note_id": dn_id,
        "debit_note_number": f"DN-{prefix}-{datetime.now().strftime('%y%m%d%H%M')}",
        "vendor_id": vendor_id,
        "vendor_name": (vendor or {}).get("trade_name")
        or (vendor or {}).get("legal_name"),
        "bill_id": note.bill_id,
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
        "created_at": datetime.now().isoformat(),
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
    rows = _ledger_rows(db, vendor_id, scope)[2]
    rows.sort(key=lambda d: d.get("date") or "", reverse=True)
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
            "ledger": ap_engine.build_ledger([], [], []),
            "aging": ap_engine.build_aging([], [], []),
        }
    # The one row rule every payable screen reads (no transfer mirror bills;
    # rows dated after today -- a post-dated cheque -- not yet counted), so
    # the closing balance is the Suppliers card's and the report's figure.
    bills, payments, debit_notes = _ledger_rows(db, vendor_id, scope)
    return {
        "vendor_id": vendor_id,
        "vendor": vendor,
        "ledger": ap_engine.build_ledger(bills, payments, debit_notes),
        "aging": ap_engine.build_aging(bills, payments, debit_notes),
    }
