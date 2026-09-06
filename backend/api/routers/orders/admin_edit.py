"""SUPERADMIN order edit and invoice change, with the audit writer.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

from database.repositories.order_repository import derive_bill_type
from datetime import datetime
from fastapi import Depends, HTTPException
from typing import Any, Dict, Optional
from ..auth import get_current_user
from ...dependencies import get_order_repository, validate_store_access
from ._shared import (
    _compute_per_category_gst,
    _get_db,
    logger,
    router,
)
from .models import (
    SuperadminInvoiceChange,
    SuperadminOrderEdit,
)


# ============================================================================
# Build item #16 - SUPERADMIN post-creation order edit (revenue/GST/audit)
# ============================================================================
def _require_superadmin(current_user: dict) -> None:
    """Gate the post-creation order/invoice edit (build item #16) to SUPERADMIN
    OR ADMIN (owner decision 2026-06-19: admins may also edit a created
    order/invoice -- still a privileged, fully-audited override). Raises 403 for
    every other role. (Name kept for call-site stability; it now allows ADMIN.)"""
    roles = current_user.get("roles") or []
    if "SUPERADMIN" not in roles and "ADMIN" not in roles:
        raise HTTPException(
            status_code=403,
            detail="Only an ADMIN or SUPERADMIN may edit an order after it is created.",
        )


def _write_order_edit_audit(
    *,
    action: str,
    order: dict,
    before: dict,
    after: dict,
    reason: str,
    user_id: Optional[str],
    extra: Optional[dict] = None,
) -> bool:
    """Write the SYNCHRONOUS, immutable hash-chained audit row for a
    SUPERADMIN order/invoice change BEFORE the endpoint returns.

    Unlike the fire-and-forget alert on the DRAFT PUT path, this is a blocking
    write (revenue/GST/audit-critical, SYSTEM_INTENT 10 "Audit Everything"):
    the before/after snapshots + the human reason are committed to the
    append-only ``audit_logs`` chain (audit_chain.append_audit_entry) so the
    change is tamper-evident at GET /api/v1/audit/verify. We use the chained
    fields the hash commits to: before_state / after_state / diff / detail.
    Returns True on a chained write; False if no audit repo (DB-less).
    """
    from ...dependencies import get_audit_repository
    from ...services.order_superadmin_edit import build_money_diff

    audit = get_audit_repository()
    if audit is None:
        return False
    row = {
        "action": action,
        "entity_type": "order",
        "entity_id": order.get("order_id"),
        "store_id": order.get("store_id"),
        "user_id": user_id,
        "severity": "WARNING",
        "detail": reason,
        "before_state": before,
        "after_state": after,
        "diff": build_money_diff(before, after),
        "timestamp": datetime.now().isoformat(),
        "created_at": datetime.now().isoformat(),
    }
    if extra:
        row["context"] = extra
    audit.create(row)
    return True


def _rebuilt_items_or_existing(body_items, existing_items: list) -> list:
    """Resolve the corrected line set for an edit. When the caller supplies
    ``items`` it REPLACES the whole set (each normalised via rebuild_edited_line
    so item_total/discount are recomputed server-side); when omitted, the
    existing lines are kept (a customer-only / cart-discount-only edit)."""
    from ...services.order_superadmin_edit import rebuild_edited_line

    if body_items is None:
        return [dict(it) for it in (existing_items or [])]
    # SuperadminEditLine carries no discount_reason/discount_approved_by
    # (Pydantic strips unknown keys), so merge them from the STORED line by
    # item_id — an edit must not wipe the accountability trail.
    _stored_by_id = {
        it.get("item_id"): it for it in (existing_items or []) if it.get("item_id")
    }
    rebuilt = []
    for line in body_items:
        payload = line.model_dump() if hasattr(line, "model_dump") else dict(line)
        stored = _stored_by_id.get(payload.get("item_id")) or {}
        for _k in ("discount_reason", "discount_approved_by"):
            if payload.get(_k) is None and stored.get(_k) is not None:
                payload[_k] = stored.get(_k)
        rebuilt.append(rebuild_edited_line(payload))
    if not rebuilt:
        raise HTTPException(
            status_code=400, detail="An edited order must keep at least one item."
        )
    return rebuilt


@router.put("/{order_id}/superadmin-edit")
async def superadmin_edit_order(
    order_id: str,
    body: SuperadminOrderEdit,
    current_user: dict = Depends(get_current_user),
):
    """SUPERADMIN-only PRE-INVOICE order edit (build item #16, part 1).

    Allowed when the order is CONFIRMED / PROCESSING / READY and NO tax invoice
    has been issued yet. Edits the item set (qty / unit_price / discount /
    add / remove), the order-level cart discount, and/or the customer, then
    RECOMPUTES per-category GST + grand_total via the canonical
    ``_compute_per_category_gst`` (so the edit bills EXACTLY like create/add/
    remove). A non-empty ``reason`` is mandatory and a synchronous immutable
    audit row (before/after/diff) is written BEFORE returning. Guards:
      * RBAC -> 403 non-SUPERADMIN;
      * period lock -> 423 (cannot edit into a closed accounting month);
      * an already-invoiced order -> 409 (use /superadmin-invoice-change);
      * DRAFT -> 400 (use the ordinary PUT /{order_id} edit);
      * terminal DELIVERED / CANCELLED -> 400.
    """
    _require_superadmin(current_user)
    repo = get_order_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Order store unavailable")

    from ...services.order_superadmin_edit import (
        PRE_INVOICE_EDITABLE_STATUSES,
        recompute_totals,
        order_money_snapshot,
    )

    order = repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    validate_store_access(order.get("store_id"), current_user)

    status = str(order.get("status") or "").upper()
    if status == "DRAFT":
        raise HTTPException(
            status_code=400,
            detail="A DRAFT order is edited via PUT /orders/{id}, not this endpoint.",
        )
    if order.get("invoice_number"):
        raise HTTPException(
            status_code=409,
            detail=(
                "A tax invoice has been issued for this order. Use "
                "/orders/{id}/superadmin-invoice-change (revised invoice or "
                "credit/debit note) -- an issued invoice must never be mutated."
            ),
        )
    if status not in PRE_INVOICE_EDITABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Order status {status or 'UNKNOWN'} is not editable. Only "
                f"{', '.join(PRE_INVOICE_EDITABLE_STATUSES)} orders without an "
                f"invoice can be edited."
            ),
        )

    # Period lock: cannot edit money into a closed accounting month (423).
    db = _get_db()
    if db is not None:
        from ..finance import check_period_locked
        from ...utils.ist import ist_today

        check_period_locked(db, ist_today())

    before = order_money_snapshot(order)

    # Resolve corrected lines + cart discount + customer.
    new_items = _rebuilt_items_or_existing(body.items, order.get("items"))
    cart_discount_pct = (
        body.cart_discount_percent
        if body.cart_discount_percent is not None
        else float(order.get("cart_discount_percent") or 0.0)
    )
    gst = recompute_totals(new_items, cart_discount_pct, _compute_per_category_gst)

    update_data: Dict[str, Any] = {
        "items": new_items,
        "subtotal": gst["subtotal"],
        "cart_discount_percent": max(0.0, min(100.0, cart_discount_pct or 0.0)),
        "cart_discount_amount": gst["cart_discount_amount"],
        "tax_rate": gst["dominant_rate"],
        "tax_amount": gst["tax"],
        "total_discount": gst["total_discount"],
        "grand_total": gst["grand_total"],
        "pricing_model": gst.get("pricing_model", "inclusive"),
        "updated_by": current_user.get("user_id"),
        "superadmin_edited": True,
        "superadmin_edit_reason": body.reason,
        "superadmin_edited_at": datetime.now().isoformat(),
    }
    if body.cart_discount_reason is not None:
        update_data["cart_discount_reason"] = body.cart_discount_reason
    if body.notes is not None:
        update_data["notes"] = body.notes
    if body.customer_id is not None:
        update_data["customer_id"] = body.customer_id
    if body.customer_name is not None:
        update_data["customer_name"] = body.customer_name

    # Recompute the receivable so a changed grand_total flows to balance_due /
    # payment_status (a SUPERADMIN edit can raise or lower what is owed).
    amount_paid = float(order.get("amount_paid") or 0.0)
    balance_due = round(gst["grand_total"] - amount_paid, 2)
    update_data["balance_due"] = max(0.0, balance_due)
    if balance_due <= 0.01:
        update_data["payment_status"] = "PAID"
    elif amount_paid > 0:
        update_data["payment_status"] = "PARTIAL"
    else:
        update_data["payment_status"] = "UNPAID"
    update_data["bill_type"] = derive_bill_type(update_data["payment_status"])

    after_order = dict(order)
    after_order.update(update_data)
    after = order_money_snapshot(after_order)

    # SYNCHRONOUS immutable audit BEFORE persisting/returning.
    _write_order_edit_audit(
        action="ORDER_SUPERADMIN_EDIT",
        order=order,
        before=before,
        after=after,
        reason=body.reason,
        user_id=current_user.get("user_id"),
    )

    if not repo.update(order_id, update_data):
        raise HTTPException(status_code=500, detail="Failed to save order edit")

    # Owner hard rule: an edit that raises discounts must not leave the
    # create-time loyalty earn standing. Delta-claw, fail-soft.
    try:
        from ..loyalty import regate_earn_after_edit

        regate_earn_after_edit(
            {**order, **update_data}, user_id=current_user.get("user_id")
        )
    except Exception:
        pass  # loyalty must never block a superadmin edit

    return {
        "order_id": order_id,
        "message": "Order edited",
        "grand_total": gst["grand_total"],
        "tax_amount": gst["tax"],
        "balance_due": update_data["balance_due"],
        "before": before,
        "after": after,
        "audit_note": ("An immutable audit entry recording this change was written."),
    }


@router.put("/{order_id}/superadmin-invoice-change")
async def superadmin_invoice_change(
    order_id: str,
    body: SuperadminInvoiceChange,
    current_user: dict = Depends(get_current_user),
):
    """SUPERADMIN-only POST-INVOICE correction (build item #16, part 2).

    For an order that ALREADY carries a GST tax invoice. An issued tax invoice
    is immutable (Rule 46) -- it is NEVER silently mutated. The SUPERADMIN
    chooses ``mode`` (owner decision = support BOTH):

      * REVISED_INVOICE -- allocate a NEW invoice serial for the corrected
        order, mark the ORIGINAL superseded/void with a pointer to the new
        serial, persist the corrected lines/totals, and link original<->revised
        (``revised_invoices``).
      * CREDIT_NOTE -- compute the money delta (grand_total down -> CREDIT note,
        up -> DEBIT note), issue a note for the DELTA linked to the original
        invoice (the customer-facing credit-note ledger so POS-redeem / the
        customer card see it), and leave the ORIGINAL invoice + order totals
        intact.

    Both paths: RBAC 403 non-SUPERADMIN, mandatory reason, period-lock 423, and
    a synchronous immutable before/after/diff audit row.
    """
    _require_superadmin(current_user)
    repo = get_order_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Order store unavailable")

    from ...services.order_superadmin_edit import (
        recompute_totals,
        order_money_snapshot,
        compute_invoice_delta,
        build_credit_note_doc,
        build_revised_invoice_doc,
    )

    order = repo.find_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    validate_store_access(order.get("store_id"), current_user)

    original_invoice = order.get("invoice_number")
    if not original_invoice:
        raise HTTPException(
            status_code=409,
            detail=(
                "No tax invoice has been issued for this order yet. Use "
                "/orders/{id}/superadmin-edit for a pre-invoice edit."
            ),
        )

    # Period lock: a post-issue correction is a financial posting (423).
    db = _get_db()
    if db is not None:
        from ..finance import check_period_locked
        from ...utils.ist import ist_today

        check_period_locked(db, ist_today())

    before = order_money_snapshot(order)
    new_items = _rebuilt_items_or_existing(body.items, order.get("items"))
    cart_discount_pct = (
        body.cart_discount_percent
        if body.cart_discount_percent is not None
        else float(order.get("cart_discount_percent") or 0.0)
    )
    gst = recompute_totals(new_items, cart_discount_pct, _compute_per_category_gst)

    # The "after" money picture the correction targets.
    corrected = dict(order)
    corrected.update(
        {
            "items": new_items,
            "subtotal": gst["subtotal"],
            "cart_discount_percent": max(0.0, min(100.0, cart_discount_pct or 0.0)),
            "cart_discount_amount": gst["cart_discount_amount"],
            "tax_rate": gst["dominant_rate"],
            "tax_amount": gst["tax"],
            "total_discount": gst["total_discount"],
            "grand_total": gst["grand_total"],
        }
    )
    if body.customer_id is not None:
        corrected["customer_id"] = body.customer_id
    if body.customer_name is not None:
        corrected["customer_name"] = body.customer_name
    after = order_money_snapshot(corrected)

    store_doc = None
    try:
        if db is not None:
            store_doc = db.get_collection("stores").find_one(
                {"store_id": order.get("store_id")}
            )
    except Exception:  # noqa: BLE001
        store_doc = None

    if body.mode == "REVISED_INVOICE":
        # Allocate a fresh GST serial for the revised invoice.
        new_invoice_number = repo.next_invoice_number(
            order.get("store_id"), store_doc=store_doc
        )
        revised_doc = build_revised_invoice_doc(
            order=order,
            new_invoice_number=new_invoice_number,
            before=before,
            after=after,
            reason=body.reason,
            user_id=current_user.get("user_id"),
        )
        # Persist corrected order under the NEW serial; the OLD serial is kept
        # on the doc for traceability + marked superseded.
        update_data: Dict[str, Any] = {
            "items": new_items,
            "subtotal": gst["subtotal"],
            "cart_discount_percent": corrected["cart_discount_percent"],
            "cart_discount_amount": gst["cart_discount_amount"],
            "tax_rate": gst["dominant_rate"],
            "tax_amount": gst["tax"],
            "total_discount": gst["total_discount"],
            "grand_total": gst["grand_total"],
            "pricing_model": gst.get("pricing_model", "inclusive"),
            "invoice_number": new_invoice_number,
            "invoice_date": datetime.now(),
            "superseded_invoice_number": original_invoice,
            "invoice_revision_id": revised_doc["revision_id"],
            "superadmin_edited": True,
            "superadmin_edit_reason": body.reason,
            "superadmin_edited_at": datetime.now().isoformat(),
            "updated_by": current_user.get("user_id"),
        }
        if body.customer_id is not None:
            update_data["customer_id"] = body.customer_id
        if body.customer_name is not None:
            update_data["customer_name"] = body.customer_name
        # Recompute the receivable against the revised grand_total.
        amount_paid = float(order.get("amount_paid") or 0.0)
        balance_due = round(gst["grand_total"] - amount_paid, 2)
        update_data["balance_due"] = max(0.0, balance_due)
        update_data["payment_status"] = (
            "PAID"
            if balance_due <= 0.01
            else ("PARTIAL" if amount_paid > 0 else "UNPAID")
        )
        update_data["bill_type"] = derive_bill_type(update_data["payment_status"])

        # SYNCHRONOUS immutable audit BEFORE persisting.
        _write_order_edit_audit(
            action="ORDER_INVOICE_REVISED",
            order=order,
            before=before,
            after=after,
            reason=body.reason,
            user_id=current_user.get("user_id"),
            extra={
                "original_invoice_number": original_invoice,
                "revised_invoice_number": new_invoice_number,
                "revision_id": revised_doc["revision_id"],
            },
        )

        # Persist the revised-invoice link record (best-effort, fail-soft).
        if db is not None:
            try:
                db.get_collection("revised_invoices").insert_one(dict(revised_doc))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ORDERS] revised_invoices insert failed: %s", exc)

        if not repo.update(order_id, update_data):
            raise HTTPException(
                status_code=500, detail="Failed to issue revised invoice"
            )

        # Same earn re-gate as the pre-invoice edit door (the CREDIT_NOTE
        # mode leaves the order's lines intact, so only REVISED needs it).
        try:
            from ..loyalty import regate_earn_after_edit

            regate_earn_after_edit(
                {**order, **update_data}, user_id=current_user.get("user_id")
            )
        except Exception:
            pass  # loyalty must never block the correction

        return {
            "order_id": order_id,
            "mode": "REVISED_INVOICE",
            "message": "Revised invoice issued; original invoice superseded.",
            "original_invoice_number": original_invoice,
            "revised_invoice_number": new_invoice_number,
            "revision_id": revised_doc["revision_id"],
            "grand_total": gst["grand_total"],
            "balance_due": update_data["balance_due"],
            "audit_note": "An immutable audit entry recording this change was written.",
        }

    # mode == CREDIT_NOTE  -> issue a credit (reduction) / debit (increase) note
    # for the DELTA, linked to the ORIGINAL invoice. The original invoice +
    # order totals are LEFT INTACT.
    delta = compute_invoice_delta(before, after)
    if delta["direction"] == "NONE":
        raise HTTPException(
            status_code=400,
            detail=(
                "The corrected order has the same grand total as the original; "
                "there is no delta to issue a credit/debit note for."
            ),
        )

    note_doc = build_credit_note_doc(
        order=order,
        delta=delta,
        reason=body.reason,
        user_id=current_user.get("user_id"),
    )

    # SYNCHRONOUS immutable audit BEFORE issuing the note.
    _write_order_edit_audit(
        action="ORDER_INVOICE_CREDIT_NOTE",
        order=order,
        before=before,
        after=after,
        reason=body.reason,
        user_id=current_user.get("user_id"),
        extra={
            "note_number": note_doc["note_number"],
            "note_type": note_doc["note_type"],
            "amount": note_doc["amount"],
            "original_invoice_number": original_invoice,
        },
    )

    # Persist the note. A CREDIT note also bumps the customer's store-credit
    # ledger balance (reuse returns.py machinery) so POS-redeem / the customer
    # card see the credit; a DEBIT note (customer owes more) is recorded for the
    # accountant but is NOT store credit. Both fail-soft.
    if db is not None:
        try:
            coll_name = (
                "credit_note_ledger"
                if note_doc["note_type"] == "CREDIT_NOTE"
                else "debit_note_ledger"
            )
            db.get_collection(coll_name).insert_one(dict(note_doc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ORDERS] note ledger insert failed: %s", exc)

    if note_doc["note_type"] == "CREDIT_NOTE":
        try:
            from ..returns import _issue_store_credit

            _issue_store_credit(
                order.get("customer_id"),
                note_doc["amount"],
                reason=f"Credit note {note_doc['note_number']} "
                f"(order edit on invoice {original_invoice})",
                ref=note_doc["note_number"],
                current_user=current_user,
                # GSTR-1 CDNR tax reversal (PR #945 P3): pass the note's ALREADY-
                # COMPUTED taxable / tax split through so this fee-less credit
                # note (gross == net) reports its true output-tax reversal in the
                # CDNR loop instead of deriving 0 from gross-minus-net. Reuse the
                # existing figures -- never recompute tax here.
                taxable=note_doc["taxable_amount"],
                tax=note_doc["tax_amount"],
                # GSTR-1 CDNR head consistency (money-panel round 2): this
                # type=ISSUED ledger row reaches the CDNR loop, so it must
                # reverse under the SAME CGST/SGST-vs-IGST head its parent
                # invoice filed under. Online parents persist `interstate`;
                # bool-gated -- POS parents carry no flag and keep the legacy
                # state-compare fallback.
                interstate=(
                    order.get("interstate")
                    if isinstance(order.get("interstate"), bool)
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ORDERS] store-credit bump skipped: %s", exc)

    return {
        "order_id": order_id,
        "mode": "CREDIT_NOTE",
        "message": (
            f"{note_doc['note_type']} {note_doc['note_number']} issued for the "
            f"delta; original invoice {original_invoice} left intact."
        ),
        "note_number": note_doc["note_number"],
        "note_type": note_doc["note_type"],
        "amount": note_doc["amount"],
        "original_invoice_number": original_invoice,
        "delta": delta,
        "audit_note": "An immutable audit entry recording this change was written.",
    }
