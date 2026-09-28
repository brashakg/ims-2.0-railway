"""Single purchase order: timeline, read, edit, send, cancel (whole or a line)."""

from ._shared import (
    Depends,
    HTTPException,
    Query,
    _VENDOR_ROLES,
    _get_db,
    _pm,
    _po_catalog_gate_on,
    can_access_store_scoped,
    datetime,
    get_audit_repository,
    get_current_user,
    get_grn_repository,
    get_product_repository,
    get_purchase_order_repository,
    get_vendor_repository,
    logger,
    require_roles,
    router,
)
from .gst import build_po_gst, po_gst_context
from .models import POLineCancel, POUpdate, cancel_reason, expected_date_not_backdated
from .numbering import compute_po_receipt_state
from .purchase_orders import audit_cost_filled, price_po_lines


def _stamp_event_actors(events: list) -> None:
    """Replace each event's raw ``actor`` user id with a display name in ``by``.

    In place, batched (one users read for the whole timeline), fail-soft: on
    any lookup problem the ids still surface as ``by`` rather than vanishing.
    """
    names: dict = {}
    try:
        from ...services.name_resolver import user_name_map

        names = user_name_map(_get_db(), [e.get("actor") for e in events])
    except Exception:  # noqa: BLE001
        names = {}
    for e in events:
        actor = e.pop("actor", None)
        if actor:
            e["by"] = names.get(str(actor)) or str(actor)


@router.get("/purchase-orders/{po_id}/timeline")
async def get_po_timeline(po_id: str, current_user: dict = Depends(get_current_user)):
    """The full life of a PO on one read (procurement Phase 3): ordered ->
    sent -> box received (GRNs) -> on shelf (accepted) -> bill (purchase
    invoices). One click from any PO number opens the drawer that renders this.

    Read-only; store-scoped exactly like get_po (a store role only sees its own
    PO -> 404 otherwise). Fail-soft: a GRN/invoice lookup problem degrades to a
    shorter timeline, never a 5xx. Returns chronological events with the owner-
    facing five-word status vocabulary the FE chip maps.

    Shape: {"po_id", "po_number", "status", "events": [{kind, label, at, ref,
    detail}], "grns": [...], "invoices": [...]}.
    """
    po_repo = get_purchase_order_repository()
    if po_repo is None:
        return {"po_id": po_id, "events": []}

    po = po_repo.find_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    # Same object-level store boundary as get_po (hide other stores' POs).
    if not can_access_store_scoped(po.get("delivery_store_id"), current_user):
        raise HTTPException(status_code=404, detail="Purchase order not found")

    events: list = []
    events.append(
        {
            # Every PO is born a DRAFT; it is on order only once SENT.
            "kind": "ordered",
            "label": "Draft raised",
            "at": po.get("created_at"),
            "ref": po.get("po_number"),
            "actor": po.get("created_by"),
        }
    )
    if po.get("sent_at"):
        events.append(
            {
                "kind": "sent",
                "label": "Sent",
                "at": po.get("sent_at"),
                "ref": po.get("po_number"),
                "actor": po.get("sent_by"),
                "detail": "PO sent to the vendor",
            }
        )
    # Every edit / cancel since 2026-09-28 is recorded on the order itself
    # (who, when, why). An order cancelled before then carries only the
    # cancelled_* stamp, so that stamp still renders -- once, never beside a
    # recorded cancel of the same order.
    history = [dict(h) for h in (po.get("history") or []) if isinstance(h, dict)]
    if po.get("cancelled_at") and not any(h.get("kind") == "cancelled" for h in history):
        events.append(
            {
                "kind": "cancelled",
                "label": "Cancelled",
                "at": po.get("cancelled_at"),
                "ref": po.get("po_number"),
                "actor": po.get("cancelled_by"),
                "detail": po.get("cancellation_reason") or "PO cancelled",
            }
        )
    events.extend(history)

    # GRNs against this PO -> "Box received" (PENDING) + "On shelf" (ACCEPTED).
    grns_out: list = []
    grn_ids: list = []
    try:
        grn_repo = get_grn_repository()
        if grn_repo is not None:
            grns = grn_repo.find_many({"po_id": po_id}, limit=200) or []
            for g in grns:
                gid = g.get("grn_id")
                if gid:
                    grn_ids.append(gid)
                grns_out.append(
                    {
                        "grn_id": gid,
                        "grn_number": g.get("grn_number"),
                        "status": g.get("status"),
                        "created_at": g.get("created_at"),
                        "accepted_at": g.get("accepted_at"),
                        "total_accepted": g.get("total_accepted"),
                    }
                )
                if g.get("status") == "VOID":
                    continue
                events.append(
                    {
                        "kind": "box_received",
                        "label": "Box received",
                        "at": g.get("created_at"),
                        "ref": g.get("grn_number"),
                        "actor": g.get("created_by"),
                        "detail": f"Goods receipt logged ({g.get('total_received') or 0} units)",
                    }
                )
                if g.get("accepted_at"):
                    events.append(
                        {
                            "kind": "on_shelf",
                            "label": "On shelf",
                            "at": g.get("accepted_at"),
                            "ref": g.get("grn_number"),
                            "actor": g.get("accepted_by"),
                            "detail": f"{g.get('total_accepted') or 0} units accepted into stock",
                        }
                    )
    except Exception as e:  # noqa: BLE001
        logger.warning("[VENDOR] po-timeline grn lookup failed: %s", e)

    # Purchase invoices linked to this PO or any of its GRNs -> "Bill settled".
    invoices_out: list = []
    try:
        db = _get_db()
        if db is not None:
            or_terms: list = [{"po_id": po_id}]
            if grn_ids:
                or_terms.append({"grn_id": {"$in": grn_ids}})
            rows = list(
                db.get_collection("vendor_bills").find(
                    {"doc_type": "PURCHASE_INVOICE", "$or": or_terms},
                    {"_id": 0},
                )
            )
            for r in rows:
                invoices_out.append(
                    {
                        "bill_id": r.get("bill_id"),
                        "invoice_number": r.get("invoice_number")
                        or r.get("bill_number"),
                        "status": r.get("status"),
                        "total": r.get("total"),
                        "created_at": r.get("created_at"),
                    }
                )
                events.append(
                    {
                        "kind": "bill_settled",
                        "label": "Bill settled",
                        "at": r.get("created_at"),
                        "ref": r.get("invoice_number") or r.get("bill_number"),
                        "actor": r.get("created_by"),
                        "detail": f"Purchase invoice booked ({r.get('status') or 'OUTSTANDING'})",
                    }
                )
    except Exception as e:  # noqa: BLE001
        logger.warning("[VENDOR] po-timeline invoice lookup failed: %s", e)

    # Chronological (blank timestamps sort last, stable). `at` MIXES TYPES on
    # prod data: the repo layer's _add_timestamps overwrites created_at with a
    # raw datetime on every create, while sent_at / accepted_at / vendor_bills
    # created_at are ISO strings -- so a bare sort raised TypeError
    # (datetime < str) and 500'd this drawer for every sent PO. Normalise the
    # SORT KEY only (datetime -> isoformat, else str); the event payload keeps
    # its original value. Do NOT "fix" _add_timestamps instead -- every
    # collection depends on its current behavior (one_rule_two_implementations
    # ledger: the two timestamp conventions are the underlying disease).
    def _at_sort_key(ev: dict):
        at = ev.get("at")
        if at is None:
            return (True, "")
        if isinstance(at, datetime):
            return (False, at.isoformat())
        return (False, str(at))

    events.sort(key=_at_sort_key)

    # WHO did it. Every writer stamps a user_id ("user-superadmin"), never a
    # name, so the drawer used to print that id straight into the prose -- an
    # audit trail that cannot name the person is not an audit trail. Resolve
    # every stamped actor in ONE query (same helper + fail-soft shape as
    # _enrich_grn_names). Unresolvable id -> keep the id verbatim (traceable,
    # and never an invented name); nothing stamped -> no "by" at all.
    _stamp_event_actors(events)

    return {
        "po_id": po_id,
        "po_number": po.get("po_number"),
        "status": po.get("status"),
        "vendor_id": po.get("vendor_id"),
        "vendor_name": po.get("vendor_name"),
        "delivery_store_id": po.get("delivery_store_id"),
        "events": events,
        "grns": grns_out,
        "invoices": invoices_out,
    }


@router.get("/purchase-orders/{po_id}")
async def get_po(po_id: str, current_user: dict = Depends(get_current_user)):
    """Get purchase order details"""
    po_repo = get_purchase_order_repository()

    if po_repo is None:
        return {"po_id": po_id}

    po = po_repo.find_by_id(po_id)
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # F2 object-level store boundary: a store-scoped role may only read a PO for
    # its own store. Cross-store roles pass; otherwise 404 (hide existence of
    # other stores' POs, mirroring the GRN read guards).
    if not can_access_store_scoped(po.get("delivery_store_id"), current_user):
        raise HTTPException(status_code=404, detail="Purchase order not found")

    return po


@router.post("/purchase-orders/{po_id}/send")
async def send_po(
    po_id: str, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
):
    """Send PO to vendor (mark as sent)"""
    po_repo = get_purchase_order_repository()

    if po_repo is not None:
        po = po_repo.find_by_id(po_id)
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        # F2 object-level store boundary: a store-scoped role may only send a PO
        # for its own store (cross-store roles pass; else 404).
        if not can_access_store_scoped(po.get("delivery_store_id"), current_user):
            raise HTTPException(status_code=404, detail="Purchase order not found")

        if po.get("status") != "DRAFT":
            raise HTTPException(status_code=400, detail="Only draft POs can be sent")

        # Hub Phase 2 SENT gate: a PO may be DRAFTED against an incomplete product,
        # but cannot be SENT to the vendor until every line is catalog-complete.
        # cost_price is the ONE allowed gap -- it legitimately arrives at GRN (the
        # receiving flow backfills it from this PO), so a product that is DRAFT
        # ONLY because cost is unknown is still sendable. Any OTHER gap (missing
        # category attribute, mrp/offer, hsn/gst) blocks the send. Fail-soft when
        # no product repo.
        #
        # This gate governs ONLY manually-entered PO lines (the Create-PO form's
        # spine-product picker). Auto-generated POs carry a `source`:
        # cl_po lens replenishment ("cl_po_generator") and demand-forecast
        # ("demand_forecast") source their lines from system data (lens_catalog
        # needs / sales history) whose ids are NOT on the products spine, and were
        # never gated before pm.po_catalog_gate defaulted ON. We therefore skip
        # the gate for any PO bearing a `source`, mirroring the create-side gate
        # which only fires inside the manual create_po endpoint those flows bypass.
        # Without this, every cl_po/forecast DRAFT would 400 PO_LINES_INCOMPLETE.
        product_repo = get_product_repository()
        if product_repo is not None and _po_catalog_gate_on() and not po.get("source"):
            blocked = []
            for it in po.get("items", []) or []:
                pid = it.get("product_id")
                prod = product_repo.find_by_id(pid) if pid else None
                if prod is None:
                    blocked.append(
                        {"product_id": pid, "missing": ["product_not_found"]}
                    )
                    continue
                # Ruling 13: a PROVISIONAL row exists precisely because the buyer
                # is ordering something nobody has catalogued yet. Blocking the
                # send on its (inevitable) gaps would put the obstacle back at
                # the front of the flow. The strictness now lives at the INVOICE
                # (ruling 15), which refuses to settle an incomplete product.
                if prod.get("provisional"):
                    continue
                gaps = set(_pm.compute_catalog_status(prod)[1]) - {"cost_price"}
                if gaps:
                    blocked.append({"product_id": pid, "missing": sorted(gaps)})
            if blocked:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": (
                            "Cannot send this PO: some lines are not catalog-"
                            "complete. Finish cataloguing them, then send."
                        ),
                        "code": "PO_LINES_INCOMPLETE",
                        "lines": blocked,
                    },
                )

        po_repo.update(
            po_id,
            {
                "status": "SENT",
                "sent_at": datetime.now().isoformat(),
                "sent_by": current_user.get("user_id"),
            },
        )

    return {"message": "PO sent to vendor", "po_id": po_id}


# ============================================================================
# CHANGING AN ORDER (owner rulings 2026-09-28)
# ----------------------------------------------------------------------------
# A DRAFT is editable. A DRAFT or SENT order -- or one line of it -- can be
# cancelled with a reason. A part-received order cancels only what is still
# due: stock that arrived is never un-received. Every change is written to the
# order's own `history` (the timeline drawer reads it: who, when, why) and to
# the audit log.
# ============================================================================

_PART_RECEIVED = ("PARTIALLY_RECEIVED", "PARTIAL")
# Nothing left to cancel. Every other status (DRAFT, SENT, ACKNOWLEDGED, and
# any legacy word) cancels whole; a part-received one cancels what is due.
_CLOSED = ("RECEIVED", "CANCELLED")
_CLOSED_DETAIL = (
    "A received or cancelled order cannot be cancelled - nothing on it is "
    "still due."
)
# A delivery logged against the order but not accepted into stock yet.
_WAITING_GRN = ("PENDING", "PARTIALLY_ACCEPTED")


def _qty(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def _po_for_change(po_id: str, current_user: dict):
    """(repo, po) for a write, behind the same store boundary as every other
    PO write: another store's order answers 404, never a hint it exists."""
    po_repo = get_purchase_order_repository()
    if po_repo is None:
        raise HTTPException(status_code=503, detail="Purchase orders unavailable")
    po = po_repo.find_by_id(po_id)
    if not po or not can_access_store_scoped(po.get("delivery_store_id"), current_user):
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return po_repo, po


def _refuse_if_box_waiting(po_id: str) -> None:
    """A receipt logged but not yet accepted re-derives the order's status the
    moment it is accepted -- it would flip a cancelled order back to received.
    So a cancel waits until that box is accepted or voided."""
    grn_repo = get_grn_repository()
    if grn_repo is None:
        return
    rows = grn_repo.find_many(
        {"po_id": po_id, "status": {"$in": list(_WAITING_GRN)}}, limit=50
    )
    waiting = [g for g in rows or [] if g.get("status") in _WAITING_GRN]
    if waiting:
        numbers = ", ".join(str(g.get("grn_number") or g.get("grn_id")) for g in waiting)
        raise HTTPException(
            status_code=409,
            detail=(
                f"A delivery against this order is logged but not accepted yet "
                f"({numbers}). Accept it or void it first, then cancel what is "
                "still due."
            ),
        )


def _received_per_line(po: dict) -> list:
    """Units received against each line. Receipts are counted per PRODUCT, so a
    product's cumulative accepted quantity is shared out over its lines in
    order -- two lines of one product never both claim the same units."""
    header = po.get("received_qty_by_product") or {}
    left: dict = {}
    out = []
    for it in po.get("items") or []:
        pid = it.get("product_id")
        if pid not in left:
            recv = header.get(pid)
            left[pid] = _qty(it.get("received_qty") if recv is None else recv)
        take = min(left[pid], _qty(it.get("quantity")))
        left[pid] -= take
        out.append(take)
    return out


def _cancel_remainder(line: dict, received: int) -> int:
    """Withdraw what is still due on one line, in place; returns the units.

    The line then orders exactly what arrived, so every reader of `quantity` /
    `ordered_qty` -- the receiving cockpit, the receipt state, the 3-way match
    -- stops treating the withdrawn units as due, with no change of its own.
    """
    due = _qty(line.get("quantity")) - received
    if due <= 0:
        return 0
    line["cancelled_qty"] = _qty(line.get("cancelled_qty")) + due
    line["quantity"] = received
    line["ordered_qty"] = received
    line["line_status"] = "RECEIVED" if received else "CANCELLED"
    return due


def _status_after_cancel(po: dict, items: list, received: list) -> str:
    """Status once units were withdrawn: the receipt rule decides when anything
    arrived; nothing arrived and nothing left -> CANCELLED; else unchanged."""
    if any(received):
        by_product: dict = {}
        for it, r in zip(po.get("items") or [], received):
            pid = it.get("product_id")
            by_product[pid] = by_product.get(pid, 0) + r
        return compute_po_receipt_state(items, by_product)
    if not any(_qty(it.get("quantity")) for it in items):
        return "CANCELLED"
    return po.get("status")


def _reprice(po: dict, items: list) -> dict:
    """Money fields after quantities changed on an already-priced order.

    Same arithmetic as every PO door (build_po_gst), with each line's stored
    rate pinned and the order's stored GST numbers, so a cancel withdraws units
    and never re-taxes what is left. Orders from before the GST numbers were
    stored read the vendor and shop afresh."""
    if "vendor_gstin" in po:
        vendor_doc = {"gstin": po.get("vendor_gstin")}
        store_doc = {
            "gstin": po.get("store_gstin"),
            "state_code": po.get("supply_place_recipient"),
        }
    else:
        vendor_doc, store_doc = po_gst_context(
            po.get("delivery_store_id"), po.get("vendor_id")
        )
    computed = build_po_gst(
        [{**it, "gst_rate": it.get("tax_rate")} for it in items],
        None,
        vendor_doc,
        store_doc,
    )
    for it, priced in zip(items, computed["items"]):
        for key in ("line_tax", "cgst", "sgst", "igst"):
            it[key] = priced[key]
    return {
        "subtotal": computed["subtotal"],
        "tax_amount": computed["tax"],
        "total_amount": computed["total"],
        "gst_summary": computed["gst_summary"],
    }


def _write_change(po_repo, po, patch, events, current_user, action, before, after):
    """Apply one change to an order: the patch, the timeline events (stamped
    with the person and the time) and one audit row. Audit is fail-soft -- a
    missing audit row never undoes the change the person made."""
    now = datetime.now().isoformat()
    who = current_user.get("user_id")
    stamped = [{**ev, "at": now, "actor": who} for ev in events]
    patch = {**patch, "history": [*(po.get("history") or []), *stamped]}
    po_repo.update(po["po_id"], patch)
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": action,
                    "entity_type": "purchase_order",
                    "entity_id": po["po_id"],
                    "target": po.get("po_number"),
                    "user_id": who,
                    "store_id": po.get("delivery_store_id"),
                    "before": before,
                    "after": after,
                    "timestamp": datetime.now(),
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VENDOR] PO change audit failed for %s: %s", po["po_id"], exc)


def _units(n: int) -> str:
    return f"{n} unit" + ("" if n == 1 else "s")


def _describe_edit(old_items: list, new_items: list) -> list:
    """What changed on the lines, in the words the timeline shows."""
    old = {i.get("product_id"): i for i in old_items}
    new = {i.get("product_id"): i for i in new_items}
    out = []
    for pid, n in new.items():
        name = n.get("product_name") or n.get("sku") or pid
        o = old.get(pid)
        if o is None:
            out.append(f"added {name} x{_qty(n.get('quantity'))}")
            continue
        old_qty, new_qty = _qty(o.get("quantity")), _qty(n.get("quantity"))
        if old_qty != new_qty:
            out.append(f"{name}: qty {old_qty} -> {new_qty}")
        old_price = float(o.get("unit_price") or 0)
        new_price = float(n.get("unit_price") or 0)
        if old_price != new_price:
            out.append(f"{name}: cost Rs {old_price:g} -> Rs {new_price:g}")
    for pid, o in old.items():
        if pid not in new:
            out.append(f"removed {o.get('product_name') or o.get('sku') or pid}")
    return out


@router.put("/purchase-orders/{po_id}")
async def update_po(
    po_id: str,
    body: POUpdate,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Edit a DRAFT: quantity, unit cost, add / remove lines, vendor, delivery
    date, notes. Once the order has gone to the vendor it is not rewritten --
    cancel a line or the order instead."""
    po_repo, po = _po_for_change(po_id, current_user)
    if po.get("status") != "DRAFT":
        raise HTTPException(
            status_code=400,
            detail=(
                "Only a draft can be edited - this order has already gone to "
                "the vendor. Cancel a line, or the order, instead."
            ),
        )
    if po.get("source"):
        # Lens top-up / forecast drafts carry lines (power cells, lens catalogue
        # ids) the typed-line shape cannot hold; rewriting them would drop that.
        raise HTTPException(
            status_code=400,
            detail=(
                "This draft was generated automatically - cancel the lines you "
                "do not want instead of editing it."
            ),
        )

    vendor_id = body.vendor_id or po.get("vendor_id")
    vendor = None
    vendor_repo = get_vendor_repository()
    if vendor_repo is not None:
        vendor = vendor_repo.find_by_id(vendor_id)
        if vendor is None:
            raise HTTPException(status_code=404, detail="Vendor not found")

    expected = body.expected_date or None
    if expected and expected != po.get("expected_date"):
        try:
            expected = expected_date_not_backdated(expected)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    notes = body.notes or None

    old_items = po.get("items") or []
    computed, cost_filled = price_po_lines(
        body.items, vendor, po.get("delivery_store_id"), current_user
    )
    new_items = computed["items"]

    changes = _describe_edit(old_items, new_items)
    if vendor_id != po.get("vendor_id"):
        changes.append("vendor changed")
    if expected != (po.get("expected_date") or None):
        was = po.get("expected_date") or "none"
        changes.append(f"delivery date {was} -> {expected or 'none'}")
    if notes != (po.get("notes") or None):
        changes.append("notes changed")
    if not changes:
        return po

    patch = {
        "vendor_id": vendor_id,
        "vendor_name": (
            (vendor.get("trade_name") or vendor.get("legal_name"))
            if vendor
            else po.get("vendor_name")
        ),
        "items": new_items,
        "subtotal": computed["subtotal"],
        "tax_amount": computed["tax"],
        "total_amount": computed["total"],
        "gst_summary": computed["gst_summary"],
        **computed["parties"],
        "expected_date": expected,
        "notes": notes,
    }
    _write_change(
        po_repo,
        po,
        patch,
        [{"kind": "edited", "label": "Edited", "detail": "; ".join(changes)}],
        current_user,
        "purchase_order.edit",
        before={"items": old_items, "total_amount": po.get("total_amount")},
        after={"items": new_items, "total_amount": computed["total"]},
    )
    audit_cost_filled(po_id, po.get("po_number"), cost_filled, current_user)
    return po_repo.find_by_id(po_id)


@router.post("/purchase-orders/{po_id}/cancel")
async def cancel_po(
    po_id: str,
    reason: str = Query(...),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Cancel an order, with a reason the timeline shows beside the person.

    DRAFT / SENT: the whole order is cancelled. PART RECEIVED: only what is
    still due is cancelled -- what arrived stays in stock and on the order, and
    the order closes as received. A received or cancelled order has nothing
    left to cancel.
    """
    try:
        reason = cancel_reason(reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    po_repo, po = _po_for_change(po_id, current_user)
    status = po.get("status")
    if status in _CLOSED:
        raise HTTPException(status_code=400, detail=_CLOSED_DETAIL)
    if status != "DRAFT":
        _refuse_if_box_waiting(po_id)

    if status in _PART_RECEIVED:
        items = [dict(i) for i in po.get("items") or []]
        received = _received_per_line(po)
        units = sum(_cancel_remainder(it, r) for it, r in zip(items, received))
        if units == 0:
            raise HTTPException(
                status_code=400,
                detail="Nothing on this order is still due.",
            )
        new_status = _status_after_cancel(po, items, received)
        patch = {"items": items, **_reprice(po, items), "status": new_status}
        event = {
            "kind": "cancelled",
            "label": "Rest cancelled",
            "detail": (
                f"{_units(units)} still due cancelled; what arrived stays in "
                f"stock. Reason: {reason}"
            ),
        }
    else:
        new_status = "CANCELLED"
        patch = {
            "status": new_status,
            "cancelled_at": datetime.now().isoformat(),
            "cancelled_by": current_user.get("user_id"),
            "cancellation_reason": reason,
        }
        event = {"kind": "cancelled", "label": "Cancelled", "detail": f"Reason: {reason}"}

    _write_change(
        po_repo,
        po,
        patch,
        [event],
        current_user,
        "purchase_order.cancel",
        before={"status": status, "total_amount": po.get("total_amount")},
        after={"status": new_status, "reason": reason},
    )
    return {"message": "PO cancelled", "po_id": po_id, "po": po_repo.find_by_id(po_id)}


@router.post("/purchase-orders/{po_id}/items/{line_index}/cancel")
async def cancel_po_line(
    po_id: str,
    line_index: int,
    body: POLineCancel,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Cancel what is still due on ONE line (by its position on the order).

    On a DRAFT the line is removed (it never went to the vendor). On a sent or
    part-received order the undelivered quantity is withdrawn and whatever
    arrived stays. The order's status follows: nothing left -> CANCELLED;
    everything still asked for has arrived -> RECEIVED.
    """
    po_repo, po = _po_for_change(po_id, current_user)
    status = po.get("status")
    if status in _CLOSED:
        raise HTTPException(status_code=400, detail=_CLOSED_DETAIL)
    items = [dict(i) for i in po.get("items") or []]
    if not 0 <= line_index < len(items):
        raise HTTPException(status_code=404, detail="No such line on this order")
    line = items[line_index]
    if body.product_id and body.product_id != line.get("product_id"):
        raise HTTPException(
            status_code=409,
            detail="This order changed since you opened it - reload it and try again.",
        )
    name = line.get("product_name") or line.get("sku") or line.get("product_id")

    if status == "DRAFT":
        if len(items) <= 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This is the only line on the draft - cancel the whole "
                    "order instead."
                ),
            )
        items.pop(line_index)
        units = _qty(line.get("quantity"))
        new_status = status
    else:
        _refuse_if_box_waiting(po_id)
        received = _received_per_line(po)
        units = _cancel_remainder(items[line_index], received[line_index])
        if units == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Nothing is still due on this line - everything ordered "
                    "has arrived."
                ),
            )
        new_status = _status_after_cancel(po, items, received)

    patch = {"items": items, **_reprice(po, items), "status": new_status}
    events = [
        {
            "kind": "line_cancelled",
            "label": "Line cancelled",
            "detail": f"{name}: {_units(units)} cancelled. Reason: {body.reason}",
        }
    ]
    if new_status == "CANCELLED":
        patch.update(
            {
                "cancelled_at": datetime.now().isoformat(),
                "cancelled_by": current_user.get("user_id"),
                "cancellation_reason": body.reason,
            }
        )
        events.append(
            {
                "kind": "cancelled",
                "label": "Cancelled",
                "detail": f"Nothing left on the order. Reason: {body.reason}",
            }
        )
    _write_change(
        po_repo,
        po,
        patch,
        events,
        current_user,
        "purchase_order.cancel_line",
        before={"line": (po.get("items") or [])[line_index], "status": status},
        after={
            "line_index": line_index,
            "units": units,
            "status": new_status,
            "reason": body.reason,
        },
    )
    return po_repo.find_by_id(po_id)
