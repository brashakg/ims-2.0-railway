"""PO/GRN variance report and variance dismissal."""

from ._shared import (
    BaseModel,
    Depends,
    Field,
    HTTPException,
    Optional,
    Query,
    _AP_ROLES,
    _get_db,
    datetime,
    get_audit_repository,
    get_grn_repository,
    get_purchase_order_repository,
    require_roles,
    router,
    validate_store_access,
)


# ============================================================================
# F8 - PO vs GRN VARIANCE / BACKORDER
# ----------------------------------------------------------------------------
# Read-mostly accountability surface: every open/partial PO line whose received
# (ACCEPTED) qty trails the ordered qty is surfaced with its open qty, days
# overdue, and an explicit aging enum. An ADMIN/ACCOUNTANT can dismiss a line
# with a mandatory justification (single-doc $push on the PO + one audit row).
# NO order/POS/payment/AP mutation happens here -- the dismiss only annotates
# PO metadata; the debit-note hint is a prompt the operator may ignore.
# ============================================================================

# Roles that may see the variance report (read). Adds STORE_MANAGER to the AP
# pair so a store can chase its own late deliveries; SUPERADMIN auto-passes.
_VARIANCE_READ_ROLES = ("ADMIN", "ACCOUNTANT", "STORE_MANAGER", "AREA_MANAGER")


def _resolve_booked_bills(lines: list) -> None:
    """F8 P3: stamp booked_bill_id on each variance-report row, in place.

    The dismiss endpoint only suggests a debit note when BOTH grn_id and
    bill_id are supplied -- but report rows never carried them, so the prompt
    was unreachable from the UI. The engine now stamps latest_accepted_grn_id
    (pure, from the GRNs it already reads); this helper resolves the booked
    invoice side: the newest vendor_bill linked to the row's PO whose lines
    contain the row's product. Read-only + strictly fail-soft -- no DB / any
    error leaves booked_bill_id None and the report still serves.
    """
    if not lines:
        return
    for ln in lines:
        ln.setdefault("booked_bill_id", None)
    db = None
    try:
        db = _get_db()
    except Exception:  # noqa: BLE001
        db = None
    if db is None:
        return
    po_ids = sorted({ln.get("po_id") for ln in lines if ln.get("po_id")})
    if not po_ids:
        return
    try:
        bills = list(
            db.get_collection("vendor_bills").find(
                {"po_id": {"$in": po_ids}},
                {
                    "_id": 0,
                    "bill_id": 1,
                    "po_id": 1,
                    "lines": 1,
                    "invoice_date": 1,
                    "created_at": 1,
                },
            )
        )
    except Exception:  # noqa: BLE001
        return
    # Newest first (string-coerced so mixed str/datetime stamps can't raise),
    # so the FIRST bill matching a (po, product) is the latest booking.
    bills.sort(
        key=lambda b: (
            str(b.get("invoice_date") or ""),
            str(b.get("created_at") or ""),
        ),
        reverse=True,
    )
    bills_by_po: dict = {}
    for b in bills:
        bills_by_po.setdefault(b.get("po_id"), []).append(b)
    for ln in lines:
        pid = ln.get("product_id")
        for b in bills_by_po.get(ln.get("po_id"), []):
            if any(
                isinstance(bl, dict) and bl.get("product_id") == pid
                for bl in (b.get("lines") or [])
            ):
                ln["booked_bill_id"] = b.get("bill_id")
                break


class DismissVarianceRequest(BaseModel):
    product_id: str
    # Mandatory justification, >= 10 chars: a dismiss is an accountable decision
    # (the line is no longer chased), so it must carry a real reason.
    reason: str = Field(..., min_length=10)
    # Optional links: when BOTH are present and the booked invoice over-bills the
    # accepted qty, the response prompts a debit note (prompt only -- no AP write).
    grn_id: Optional[str] = None
    bill_id: Optional[str] = None


@router.get("/variance-report")
async def po_grn_variance_report(
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_roles(*_VARIANCE_READ_ROLES)),
):
    """PO-ordered vs GRN-received variance + backorder report.

    For every open PO (SENT / ACKNOWLEDGED / PARTIALLY_RECEIVED), compares the
    ordered qty per product against the cumulative ACCEPTED qty across its GRNs
    and returns the per-line variance (open_qty, received/accepted/rejected,
    SHORT/OVER/EXACT/UNMATCHED, days_overdue, aging_status enum). Fully-satisfied
    lines (open_qty 0 + EXACT) are omitted; dismissed lines are hidden.

    Fail-soft: no DB -> empty list. Pure variance math lives in
    po_variance_engine so this handler only fetches + filters.
    """
    from ...services import po_variance_engine

    po_repo = get_purchase_order_repository()
    grn_repo = get_grn_repository()
    if po_repo is None:
        return {"lines": [], "total": 0}

    active_store = validate_store_access(store_id, current_user)

    try:
        # limit=0 -> ALL open POs; the variance total + backorders must not be
        # silently capped at the default 100 (a chain can have >100 open POs).
        pos = po_repo.find_pending(limit=0) or []
    except Exception:  # noqa: BLE001
        pos = []

    if active_store:
        pos = [p for p in pos if p.get("delivery_store_id") == active_store]

    # Fetch each PO's GRNs (by po_id). Fail-soft per PO.
    grns_by_po: dict = {}
    for po in pos:
        pid = po.get("po_id")
        if not pid:
            continue
        try:
            grns_by_po[pid] = grn_repo.find_by_po(pid) if grn_repo is not None else []
        except Exception:  # noqa: BLE001
            grns_by_po[pid] = []

    lines = po_variance_engine.variance_report_lines(pos, grns_by_po)
    total = len(lines)
    page = lines[skip : skip + limit]
    # F8 P3: resolve booked_bill_id per (po, product) for the served page only
    # (one $in query), so the Dismiss flow can pass grn_id + bill_id and the
    # debit-note prompt is reachable. Fail-soft -- never blocks the report.
    _resolve_booked_bills(page)
    return {"lines": page, "total": total}


@router.post("/purchase-orders/{po_id}/dismiss-variance")
async def dismiss_po_variance(
    po_id: str,
    body: DismissVarianceRequest,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Dismiss a PO-vs-GRN variance/backorder line with a justification.

    Records the dismissal on the PO (single find_one_and_update $push to
    dismissed_variances[] -- ONE document, ONE collection; no cross-collection
    atomic write, per CORRECTIONS P0-1) plus one immutable audit row
    (AuditRepository.create). When grn_id + bill_id are both supplied and the
    booked invoice over-bills the accepted qty for the product, the response
    PROMPTS a debit note (suggested amount = over-billed qty * invoice price) --
    a hint only; it never creates a debit note or touches AP.

    Does NOT change PO status, GRN stock, or payable. The dismissed task is
    handled by the caller (status DISMISSED, not COMPLETED, so SLA logic does
    not treat it as genuine resolution).
    """
    po_repo = get_purchase_order_repository()
    if po_repo is None:
        raise HTTPException(status_code=503, detail="Purchase orders unavailable")

    po = po_repo.find_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    reason = (body.reason or "").strip()
    if len(reason) < 10:
        raise HTTPException(
            status_code=400,
            detail="A dismissal reason of at least 10 characters is required.",
        )

    # Debit-note suggestion: only when BOTH a GRN and a booked invoice are linked
    # AND the invoice over-bills the accepted qty for this product. Fail-soft.
    debit_note_suggested = False
    suggested_amount: Optional[float] = None
    if body.grn_id and body.bill_id:
        try:
            db = _get_db()
            grn = None
            grn_repo = get_grn_repository()
            if grn_repo is not None:
                grn = grn_repo.find_by_id(body.grn_id)
            bill = None
            if db is not None:
                bill = db.get_collection("vendor_bills").find_one(
                    {"bill_id": body.bill_id}, {"_id": 0}
                )
            accepted_qty = 0
            for it in (grn or {}).get("items", []) or []:
                if isinstance(it, dict) and it.get("product_id") == body.product_id:
                    try:
                        accepted_qty += int(it.get("accepted_qty", 0) or 0)
                    except (TypeError, ValueError):
                        continue
            invoiced_qty = 0.0
            invoice_unit_price = 0.0
            for ln in (bill or {}).get("lines", []) or []:
                if isinstance(ln, dict) and ln.get("product_id") == body.product_id:
                    try:
                        q = float(ln.get("qty", 0) or 0)
                    except (TypeError, ValueError):
                        q = 0.0
                    invoiced_qty += q
                    try:
                        up = float(ln.get("unit_price", 0) or 0)
                    except (TypeError, ValueError):
                        up = 0.0
                    if up <= 0 and q:
                        try:
                            taxable = float(ln.get("taxable", 0) or 0)
                        except (TypeError, ValueError):
                            taxable = 0.0
                        up = taxable / q if q else 0.0
                    if up > invoice_unit_price:
                        invoice_unit_price = up
            over_billed = invoiced_qty - accepted_qty
            if over_billed > 0 and invoice_unit_price > 0:
                debit_note_suggested = True
                suggested_amount = round(over_billed * invoice_unit_price, 2)
        except Exception:  # noqa: BLE001
            debit_note_suggested = False
            suggested_amount = None

    now_iso = datetime.now().isoformat()
    entry = {
        "product_id": body.product_id,
        "reason": reason,
        "dismissed_by": current_user.get("user_id"),
        "dismissed_at": now_iso,
        "grn_id": body.grn_id,
        "bill_id": body.bill_id,
        "debit_note_suggested": debit_note_suggested,
        "suggested_amount": suggested_amount,
    }

    # Single-document, single-collection mutation (CORRECTIONS P0-1).
    try:
        po_repo.collection.find_one_and_update(
            {"po_id": po_id},
            {
                "$push": {"dismissed_variances": entry},
                "$set": {"updated_at": now_iso},
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail="Failed to record variance dismissal"
        ) from exc

    # One immutable audit row (AuditRepository.create -- NOT a dual balance
    # write). Fail-soft: a missing audit must not undo the dismissal.
    try:
        audit_repo = get_audit_repository()
        if audit_repo is not None:
            audit_repo.create(
                {
                    "action": "po_variance_dismiss",
                    "entity_type": "purchase_order",
                    "entity_id": po_id,
                    "target": po_id,
                    "user_id": current_user.get("user_id"),
                    "store_id": po.get("delivery_store_id"),
                    "before": {
                        "product_id": body.product_id,
                        "po_status": po.get("status"),
                    },
                    "after": {
                        "reason": reason,
                        "dismissed_by": current_user.get("user_id"),
                        "debit_note_suggested": debit_note_suggested,
                    },
                    "timestamp": datetime.now(),
                }
            )
    except Exception:  # noqa: BLE001
        pass

    return {
        "dismissed": True,
        "po_id": po_id,
        "product_id": body.product_id,
        "vendor_id": po.get("vendor_id"),
        "debit_note_suggested": debit_note_suggested,
        "suggested_amount": suggested_amount,
    }
