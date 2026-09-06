"""Single purchase order: timeline, read, send, cancel."""

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
    get_current_user,
    get_grn_repository,
    get_product_repository,
    get_purchase_order_repository,
    logger,
    require_roles,
    router,
)


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
            "kind": "ordered",
            "label": "Ordered",
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
    if po.get("cancelled_at"):
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


@router.post("/purchase-orders/{po_id}/cancel")
async def cancel_po(
    po_id: str,
    reason: str = Query(...),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Cancel a purchase order"""
    po_repo = get_purchase_order_repository()

    if po_repo is not None:
        po = po_repo.find_by_id(po_id)
        if not po:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        # F2 object-level store boundary: a store-scoped role may only cancel a
        # PO for its own store (cross-store roles pass; else 404).
        if not can_access_store_scoped(po.get("delivery_store_id"), current_user):
            raise HTTPException(status_code=404, detail="Purchase order not found")

        # A PARTIALLY_RECEIVED PO has stock already in the warehouse. Cancelling
        # it would orphan those stock units (no live PO to trace back to) and
        # leave the GRN with a reference to a cancelled order. Block it -- the
        # operator must raise a debit note for the unreceived portion instead.
        if po.get("status") in [
            "RECEIVED",
            "CANCELLED",
            "PARTIALLY_RECEIVED",
            "PARTIAL",
        ]:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cannot cancel this PO. A fully or partially received PO "
                    "cannot be cancelled because stock has already been posted "
                    "against it. Raise a debit note for any unreceived portion."
                ),
            )

        po_repo.update(
            po_id,
            {
                "status": "CANCELLED",
                "cancelled_at": datetime.now().isoformat(),
                "cancelled_by": current_user.get("user_id"),
                "cancellation_reason": reason,
            },
        )

    return {"message": "PO cancelled", "po_id": po_id}
