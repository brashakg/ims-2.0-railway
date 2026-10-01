"""Express receive: create + accept a GRN in one operator step."""

from ._shared import (
    Depends,
    HTTPException,
    _VENDOR_ROLES,
    get_grn_repository,
    logger,
    require_roles,
    router,
)
from .models import (
    ExpressGRNCreate,
    GRNCreate,
    GRNItemCreate,
    GRN_SUBTYPE_DC,
    GRN_SUBTYPE_STANDARD,
)
from .grn_create import _create_grn_impl
from .grn_accept import _accept_grn_impl


@router.post("/grn/express", status_code=201)
async def express_receive_grn(
    body: ExpressGRNCreate,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """One-shot receiving chain for a CLEAN delivery (procurement Phase 2).

    Runs create -> accept server-side through the SAME shared impls the
    two-step flow uses -- every control preserved: F-S3 attachment gate +
    BUG-010 file-exists check, PO exists + receivable, F2 store re-point to
    the PO's delivery store + cross-store 404, idempotent per-(grn, line)
    stock mint, PO receipt math. Then computes the purchase-invoice DRAFT and
    the 3-way match PREVIEW via the accountant console's own code paths
    WITHOUT persisting anything (no vendor_bills write, no AP booking -- the
    accountant attestation stays human) and raises a fail-soft accountant
    task deep-linking to the booking screen.

    CLEAN-ONLY: every line must be fully accepted (rejected_qty == 0 and
    accepted_qty == received_qty > 0); anything else answers 400
    EXPRESS_NOT_CLEAN so the frontend falls back to the existing two-step
    receive (which carries the full discrepancy controls). STANDARD PO-backed
    receipts only -- a DELIVERY_CHALLAN is 400 EXPRESS_STANDARD_ONLY.

    Failure atomicity: an HTTPException BEFORE the GRN row exists propagates
    unchanged (nothing was persisted). If accept fails AFTER the GRN was
    created, a 409 with code EXPRESS_PARTIAL carrying the grn_id is returned
    so the FE can point the user at the pending-receipts panel to accept or
    void it -- never a silently stranded PENDING GRN.
    """
    # 1) STANDARD-only: a Delivery Challan has no vendor invoice at receipt
    # time, so there is nothing to draft/match -- express cannot apply.
    if body.grn_subtype == GRN_SUBTYPE_DC:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "EXPRESS_STANDARD_ONLY",
                "message": (
                    "Express receive applies to STANDARD PO-backed receipts "
                    "only. Log a Delivery Challan through the normal "
                    "receiving screen."
                ),
            },
        )

    # 2) CLEAN-ONLY guard: one stable code for every violation so the FE keys
    # on it to route the user to the two-step receive.
    for idx, item in enumerate(body.items):
        if (
            item.rejected_qty != 0
            or item.received_qty <= 0
            or item.accepted_qty != item.received_qty
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "EXPRESS_NOT_CLEAN",
                    "message": (
                        f"Line {idx + 1} (product {item.product_id}) is not a "
                        "clean receipt: express requires rejected_qty=0 and "
                        "accepted_qty equal to a positive received_qty on "
                        "every line. Use the standard receiving flow for "
                        "rejections or short/over receipts."
                    ),
                },
            )

    # 3) Create the GRN through the SHARED impl -- every create-side control
    # (attachment mandatory + file-exists, PO exists + receivable, store
    # re-point + cross-store 404) runs unchanged. An HTTPException here
    # propagates as-is: nothing has been persisted yet.
    try:
        grn_create = GRNCreate(
            po_id=body.po_id,
            vendor_invoice_no=body.vendor_invoice_no,
            vendor_invoice_date=body.vendor_invoice_date,
            # Ruling 14: express receiving IS the tally, declared once at the
            # header. The caller is asserting the whole delivery arrived exactly
            # as ordered and clean (the EXPRESS_NOT_CLEAN rule below refuses
            # anything else), so each line is ticked here rather than the
            # clean-delivery chain being locked out of its own shortcut.
            items=[GRNItemCreate(**it.model_dump(), tallied=True) for it in body.items],
            notes=body.notes,
            grn_subtype=GRN_SUBTYPE_STANDARD,
            attachment_file_id=body.attachment_file_id,
            attachment_filename=body.attachment_filename,
            attachment_mime=body.attachment_mime,
        )
    except ValueError as exc:
        # e.g. blank po_id / vendor_invoice_no -- surface as a clean 400.
        raise HTTPException(status_code=400, detail=str(exc))

    create_res = await _create_grn_impl(grn_create, current_user)
    grn_id = create_res.get("grn_id")
    grn_number = create_res.get("grn_number")

    # 4) Accept through the SHARED impl. From here on the GRN row EXISTS, so a
    # failure must never surface as a generic error that strands a PENDING
    # GRN invisibly: EXPRESS_PARTIAL + grn_id tells the FE exactly where to
    # send the user (the existing pending-receipts panel handles accept/void).
    _partial_detail = {
        "code": "EXPRESS_PARTIAL",
        "grn_id": grn_id,
        "grn_number": grn_number,
        "message": (
            f"Receipt {grn_number} was created but not accepted -- open the "
            "receiving screen to accept or void it."
        ),
    }
    # EXPRESS_PARTIAL is a 409, NOT a 500. The GRN row already exists, so this
    # is a conflict, not a server fault -- and the distinction is a stock-safety
    # one, not a semantic nicety: the frontend api client auto-retries every 5xx
    # POST three times, and each retry creates a NEW grn_id that mints the
    # WHOLE delivery again -- which the per-(grn, line, unit) unique index
    # cannot catch, because it keys on source_id. (_create_grn_impl now also
    # 409s a retry outright via the STANDARD duplicate guard on the vendor
    # invoice number -- this 409 remains the first line of defence.)
    # Before this PR every raise inside accept was a PRE-mint 4xx, so a retried
    # express duplicate held zero units and was harmlessly voidable; the two
    # MID-mint 503s added here (count-verify and heartbeat) fire AFTER stock is
    # on the shelf, which is what turns that retry into real phantom stock.
    try:
        accept_res = await _accept_grn_impl(grn_id, current_user)
    except HTTPException as exc:
        logger.error(
            "[VENDOR] express-receive accept failed for %s: %s",
            grn_id,
            exc.detail,
        )
        raise HTTPException(status_code=409, detail=_partial_detail)
    except Exception as exc:  # noqa: BLE001
        logger.error("[VENDOR] express-receive accept crashed for %s: %s", grn_id, exc)
        raise HTTPException(status_code=409, detail=_partial_detail)

    grn_status = accept_res.get("grn_status")
    if grn_status is not None and grn_status != "ACCEPTED":
        # PARTIALLY_ACCEPTED: one or more lines were HELD (product not yet
        # catalogued / incomplete catalog). The chain cannot finish (F3 blocks
        # the invoice draft on a non-ACCEPTED GRN), so surface the exact
        # recovery instead of a half-true success.
        # 409, not 500 -- same reason as above: this receipt already holds real
        # stock, so it must never be auto-retried into a second one.
        raise HTTPException(
            status_code=409,
            detail={
                **_partial_detail,
                "grn_status": grn_status,
                "message": (
                    f"Receipt {grn_number} was created but only partially "
                    "accepted (some lines are held for cataloguing) -- open "
                    "the receiving screen to finish it."
                ),
            },
        )

    # 5) Invoice DRAFT + 3-way match PREVIEW -- the SAME code paths the
    # accountant's console uses (F3: draft_invoice_from_grn re-asserts the
    # GRN is ACCEPTED via _load_standard_grn). NOTHING is persisted: no
    # vendor_bills write, no AP booking -- express stops at a draft. Fail-soft:
    # the receive above is already complete and correct, so a draft/match
    # problem degrades to nulls rather than failing a successful receipt.
    invoice_draft = None
    match_preview = None
    draft = None
    try:
        from .. import purchase_invoices as _pi

        draft = await _pi.draft_invoice_from_grn(grn_id, current_user)
        invoice_draft = {
            "vendor_id": draft.get("vendor_id"),
            "invoice_number": draft.get("invoice_number"),
            "place_of_supply": draft.get("place_of_supply"),
            "lines_count": len(draft.get("lines") or []),
            "totals": {
                "taxable_total": draft.get("taxable_total"),
                "cgst_total": draft.get("cgst_total"),
                "sgst_total": draft.get("sgst_total"),
                "igst_total": draft.get("igst_total"),
                "tax_total": draft.get("tax_total"),
                "total": draft.get("total"),
            },
        }
        try:
            _pi_db = _pi._get_db()
        except Exception:  # noqa: BLE001
            _pi_db = None
        tolerance = _pi._resolved_purchase_config(_pi_db)["match_tolerance_pct"]
        match = _pi._run_match_for_invoice(
            _pi_db, body.po_id, grn_id, draft.get("lines") or [], tolerance
        )
        if match:
            match_preview = {
                "match_status": match.get("match_status"),
                "exception_count": len(match.get("exceptions") or []),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[VENDOR] express-receive draft/match preview failed for %s: %s",
            grn_id,
            exc,
        )

    # 6) Accountant task (reuses the receiving-discrepancy SYSTEM-task
    # pattern; fail-soft -- a task failure never rolls back the receive).
    accountant_task_id = None
    try:
        from ...services.task_triggers import create_system_task
        from ...dependencies import get_task_repository

        grn_doc = None
        try:
            _repo = get_grn_repository()
            grn_doc = _repo.find_by_id(grn_id) if _repo is not None else None
        except Exception:  # noqa: BLE001
            grn_doc = None
        vendor_label = (
            (draft or {}).get("vendor_name")
            or (grn_doc or {}).get("vendor_name")
            or (grn_doc or {}).get("vendor_id")
            or "vendor"
        )
        book_link = f"/purchase/invoices/book?grn_id={grn_id}"
        task = create_system_task(
            get_task_repository(),
            title=f"Book purchase invoice for GRN {grn_number} ({vendor_label})",
            description=(
                f"Express receive completed for goods receipt {grn_number} "
                f"(vendor invoice {body.vendor_invoice_no}). Review the draft "
                f"and book the purchase invoice: {book_link}."
                + (
                    f" 3-way match preview: {match_preview['match_status']} "
                    f"({match_preview['exception_count']} exception(s))."
                    if match_preview
                    else ""
                )
            ),
            priority="P2",
            category="Purchase",
            store_id=(grn_doc or {}).get("store_id"),
            dedupe_ref=f"express_invoice:{grn_id}",
            assigned_to="ACCOUNTANT",
            extra={
                "link": book_link,
                "payload": {
                    "grn_id": grn_id,
                    "grn_number": grn_number,
                    "po_id": body.po_id,
                    "match_status": (match_preview or {}).get("match_status"),
                    "exception_count": (match_preview or {}).get("exception_count"),
                },
            },
        )
        if task:
            accountant_task_id = task.get("task_id")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[VENDOR] express-receive accountant task failed for %s: %s",
            grn_id,
            exc,
        )

    return {
        "grn_id": grn_id,
        "grn_number": grn_number,
        "accepted_units": accept_res.get("units_added"),
        "po_status": accept_res.get("po_status"),
        "invoice_draft": invoice_draft,
        "match_preview": match_preview,
        "accountant_task_id": accountant_task_id,
    }
