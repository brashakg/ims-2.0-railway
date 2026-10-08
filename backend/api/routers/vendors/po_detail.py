"""Single purchase order: timeline, read, edit, send, cancel (whole or a line)."""

from collections import Counter
from datetime import timezone

from ._shared import (
    Depends,
    HTTPException,
    Optional,
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
    get_stock_repository,
    get_vendor_repository,
    logger,
    require_roles,
    router,
)
from .gst import build_po_gst, po_gst_context
from .grn_accept_lock import _grn_already_minted
from .models import POLineCancel, POUpdate, cancel_reason, expected_date_not_backdated
from .numbering import (
    _cumulative_received_by_product,
    compute_po_receipt_state,
    po_line_status,
)
from .purchase_orders import (
    create_typed_in_products,
    fill_cost_from_rate,
    price_po_lines,
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
        if not po.get("items"):
            raise HTTPException(
                status_code=400,
                detail="This order has no lines - add what to order before sending it.",
            )

        # A line must name a product that exists. This is the ONE existence
        # check and it does not depend on the catalogue gate: a line whose
        # product was never written (a create that failed part-way) must not
        # go out to a vendor. Only an order bearing a `source` is exempt --
        # cl_po / forecast lines come from system data whose ids are not on
        # the products spine (see the gate below). Fail-soft when no product
        # repo.
        product_repo = get_product_repository()
        found: dict = {}
        if product_repo is not None and not po.get("source"):
            ghosts = []
            for it in po.get("items", []) or []:
                pid = it.get("product_id")
                prod = product_repo.find_by_id(pid) if pid else None
                if prod is None:
                    ghosts.append(
                        {"product_id": pid, "missing": ["product_not_found"]}
                    )
                else:
                    found[pid] = prod
            if ghosts:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": (
                            "Cannot send this order: a line names a product "
                            "that does not exist. Remove the line or pick the "
                            "product again."
                        ),
                        "code": "PO_LINE_PRODUCT_MISSING",
                        "lines": ghosts,
                    },
                )

        # Hub Phase 2 SENT gate: a PO may be DRAFTED against an incomplete product,
        # but cannot be SENT to the vendor until every line is catalog-complete.
        # cost_price is the ONE allowed gap -- it legitimately arrives at GRN (the
        # receiving flow backfills it from this PO), so a product that is DRAFT
        # ONLY because cost is unknown is still sendable. Any OTHER gap (missing
        # category attribute, mrp/offer, hsn/gst) blocks the send.
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
        if product_repo is not None and _po_catalog_gate_on() and not po.get("source"):
            blocked = []
            for it in po.get("items", []) or []:
                pid = it.get("product_id")
                prod = found[pid]
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

        # Sends exactly what was checked above: an edit landing in between
        # refuses the send instead of going to the vendor unchecked.
        if not po_repo.update_if(
            po_id,
            _as_read(po),
            {
                "status": "SENT",
                "sent_at": datetime.now().isoformat(),
                "sent_by": current_user.get("user_id"),
            },
        ):
            raise HTTPException(status_code=409, detail=_CHANGED_MEANWHILE)

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

# Nothing left to cancel. Every other status (DRAFT, SENT, ACKNOWLEDGED, and
# any legacy word) cancels whole -- unless receipts put stock on the shelf, then
# only what is still due is cancelled.
_CLOSED = ("RECEIVED", "CANCELLED")
_CLOSED_DETAIL = (
    "A received or cancelled order cannot be cancelled - nothing on it is "
    "still due."
)
# A delivery logged against the order but not (fully) in stock yet, and what
# clears each: void refuses anything not PENDING, and a PARTIALLY_ACCEPTED one
# holds lines until their product is catalogued.
_WAITING_GRN = {
    "PENDING": "is logged but not accepted - accept it or void it",
    "PARTIALLY_ACCEPTED": (
        "has lines held until their product is catalogued - catalogue it, "
        "then accept the delivery again"
    ),
}
_CHANGED_MEANWHILE = "This order changed since you opened it - reload it and try again."


def _now_iso() -> str:
    """Now, WITH its zone (owner ruling: times are saved with their zone and
    shown in IST). UTC, so it still sorts beside the older naive-UTC stamps."""
    return datetime.now(timezone.utc).isoformat()


def _as_read(po: dict) -> dict:
    """The compare-and-set guard for a write: the order must still hold the
    status and the last-write stamp this request read. Every PO write goes
    through the repository, which moves updated_at, so a send, a receipt or a
    colleague's change in between refuses the write instead of being
    overwritten by it."""
    return {"status": po.get("status"), "updated_at": po.get("updated_at")}


def _qty(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def beyond_open_quantity(po: dict, lines, received_of, qty_key: str) -> list:
    """THE rule behind "no receipt against a cancelled quantity": the names of
    the products on `lines` that the LIVE order no longer has room for.

    A cancel lowers a line's `quantity` to what had arrived (0 for a line
    cancelled outright), so the live quantity is the figure to hold a receipt
    to -- never a figure stamped when the receipt was logged. Only a product
    the order has cancelled units of is held to it; a delivery that merely
    runs over an untouched line is a variance for the receiver to record, as
    before. `qty_key` is the receipt's `received_qty` when logging it and its
    `accepted_qty` when accepting it. `received_of()` returns the units
    already received per product; it is only called when the receipt touches a
    product the order has cancelled units of, so an ordinary receipt never
    depends on it. Create and accept both call this."""
    live: dict = {}
    cancelled: set = set()
    names: dict = {}
    for it in po.get("items") or []:
        pid = it.get("product_id")
        names.setdefault(pid, it.get("product_name") or it.get("sku") or pid)
        live[pid] = live.get(pid, 0) + _qty(it.get("quantity"))
        if it.get("line_status") == "CANCELLED" or _qty(it.get("cancelled_qty")):
            cancelled.add(pid)
    coming: dict = {}
    for line in lines or []:
        pid = line.get("product_id")
        coming[pid] = coming.get(pid, 0) + _qty(line.get(qty_key))
    touched = {pid for pid, qty in coming.items() if qty and pid in cancelled}
    if not touched:
        return []
    already_received = received_of()
    return [
        str(names[pid])
        for pid in touched
        if _qty(already_received.get(pid)) + coming[pid] > live.get(pid, 0)
    ]


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
    waiting = [
        f"{g.get('grn_number') or g.get('grn_id')} {_WAITING_GRN[g['status']]}"
        for g in rows or []
        if g.get("status") in _WAITING_GRN
    ]
    if waiting:
        raise HTTPException(
            status_code=409,
            detail=(
                "A delivery against this order is not in stock yet: "
                + "; ".join(waiting)
                + ". Then cancel what is still due."
            ),
        )


def _units_minted_for(po_id: str, product_id: str, grn_id: Optional[str] = None) -> int:
    """Units a goods receipt actually put in stock for this order and product
    (only receipt `grn_id`'s when given), whatever that receipt's status is
    now. A part-accepted receipt that was then ESCALATED counts in no receipt
    sum, yet its units are on the shelf. Fails CLOSED (503): an unreadable
    stock table must not make arrived stock look cancellable."""
    stock_repo = get_stock_repository()
    if stock_repo is None:
        return 0
    flt = {"source_type": "GRN", "po_id": po_id, "product_id": product_id}
    if grn_id:
        flt["source_id"] = grn_id
    try:
        return _grn_already_minted(stock_repo, flt)
    except Exception as exc:  # noqa: BLE001
        logger.error("[VENDOR] PO %s: could not count received units: %s", po_id, exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not check what has already arrived on this order, so "
                "nothing was changed. Try again in a moment."
            ),
        ) from exc


def _received_by_product(po: dict, leave_out_grn: Optional[str] = None) -> dict:
    """Units on the shelf per product: the most of the ACCEPTED receipts' sum
    (the count grn_accept closes an order on), the units receipts actually
    minted for this order (a part-accepted or escalated receipt is in no
    receipt sum), and the order's own copy (grn_accept's fallback writes only
    the status, so it can lag). Logging a receipt and accepting one both ask
    this; the accept leaves out the units of the receipt being accepted
    (`leave_out_grn`), which its own quantity already covers."""
    po_id = po.get("po_id")
    out = dict(_cumulative_received_by_product(get_grn_repository(), po_id))
    header = po.get("received_qty_by_product") or {}
    minted: dict = {}
    for it in po.get("items") or []:
        pid = it.get("product_id")
        if pid not in minted:
            minted[pid] = _units_minted_for(po_id, pid)
            if leave_out_grn:
                minted[pid] -= _units_minted_for(po_id, pid, leave_out_grn)
        own = header.get(pid)
        own = _qty(it.get("received_qty") if own is None else own)
        out[pid] = max(_qty(out.get(pid)), own, minted[pid])
    return out


def _received_per_line(po: dict, by_product: dict) -> list:
    """Units received against each line. Receipts are counted per PRODUCT, so a
    product's received quantity is shared out over its lines in order -- two
    lines of one product never both claim the same units."""
    left = dict(by_product)
    out = []
    for it in po.get("items") or []:
        pid = it.get("product_id")
        take = min(_qty(left.get(pid)), _qty(it.get("quantity")))
        left[pid] = _qty(left.get(pid)) - take
        out.append(take)
    return out


def _stamp(value) -> str:
    """An updated_at as the API sends it (a datetime goes out as isoformat)."""
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _line_label(line: dict) -> str:
    """How a line is named on the timeline: its own description first -- a
    lens order has one line per power, all one product name."""
    return (
        line.get("description")
        or line.get("product_name")
        or line.get("sku")
        or line.get("product_id")
    )


def _refuse_if_arrivals_unattributed(line: dict, items: list, by_product: dict) -> None:
    """A receipt counts units per PRODUCT, never per line. When some -- not
    all -- of a product's units arrived and the order carries that product on
    more than one open line (a contact-lens order has one line per power),
    nothing says which line they were for, so one of those lines cannot be
    cancelled on its own: it might withdraw boxes that arrived and leave the
    ones that never came looking received."""
    pid = line.get("product_id")
    open_lines = [i for i in items if i.get("product_id") == pid and _qty(i.get("quantity"))]
    arrived = _qty(by_product.get(pid))
    if len(open_lines) > 1 and 0 < arrived < sum(_qty(i.get("quantity")) for i in open_lines):
        name = line.get("product_name") or line.get("sku") or pid
        raise HTTPException(
            status_code=409,
            detail=(
                f"{arrived} of {name} arrived, but the receipt does not say which "
                "of its lines they were for, so one line cannot be cancelled on "
                "its own. Cancel what is still due on the whole order instead."
            ),
        )


def _refresh_received(items: list, received: list) -> None:
    """Each line's own copy of what arrived, from the receipts. The receive
    inbox and the cockpit read a line's received_qty before the header, so a
    cancel that corrected only the header left a lagging line looking due."""
    for it, r in zip(items, received):
        it["received_qty"] = r
        it["line_status"] = po_line_status(it, r)


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
    line["received_qty"] = received
    line["quantity"] = received
    line["ordered_qty"] = received
    line["line_status"] = "RECEIVED" if received else "CANCELLED"
    return due


def _status_after_cancel(po: dict, items: list, by_product: dict) -> str:
    """Status once units were withdrawn: the receipt rule decides when anything
    arrived; nothing arrived and nothing left -> CANCELLED; else unchanged."""
    if any(by_product.values()):
        return compute_po_receipt_state(items, by_product)
    if not any(_qty(it.get("quantity")) for it in items):
        return "CANCELLED"
    return po.get("status")


def _gst_parties_of(po: dict) -> tuple:
    """(vendor_doc, store_doc) for re-pricing a stored order: the order's own
    GST numbers, or -- for orders from before they were stored -- the vendor
    and shop read afresh."""
    if "vendor_gstin" in po:
        return (
            {"gstin": po.get("vendor_gstin")},
            {"gstin": po.get("store_gstin"), "state_code": po.get("supply_place_recipient")},
        )
    return po_gst_context(po.get("delivery_store_id"), po.get("vendor_id"))


def _reprice(po: dict, items: list) -> dict:
    """Money fields after quantities changed on an already-priced order.

    Same arithmetic as every PO door (build_po_gst), with each line's stored
    rate pinned and the order's stored GST numbers, so a cancel withdraws units
    and never re-taxes what is left. Orders from before the GST numbers were
    stored read the vendor and shop afresh."""
    computed = build_po_gst(
        [{**it, "gst_rate": it.get("tax_rate")} for it in items],
        None,
        *_gst_parties_of(po),
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
    with the person and the time) and one audit row -- only while the order is
    still as this request read it (409 otherwise). Audit is fail-soft -- a
    missing audit row never undoes the change the person made."""
    now = _now_iso()
    who = current_user.get("user_id")
    stamped = [{**ev, "at": now, "actor": who} for ev in events]
    patch = {**patch, "history": [*(po.get("history") or []), *stamped]}
    if not po_repo.update_if(po["po_id"], _as_read(po), patch):
        raise HTTPException(status_code=409, detail=_CHANGED_MEANWHILE)
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


def _money(v: float) -> str:
    """1000 -> '1000', 12345.68 -> '12345.68': never the rounded ':g' form
    that showed a 12345.67 -> 12345.68 change as 12345.7 -> 12345.7."""
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _describe_edit(old_items: list, new_items: list) -> list:
    """What changed on the lines, in the words the timeline shows. Empty means
    the lines are as stored, which is how update_po knows an edit changed
    nothing -- so every line is compared, not one per product: an order may
    carry two lines of one product, and a change to either must count.

    An unchanged line (same product, quantity and cost) is matched first; the
    rest are paired with the next line of the same product, in order."""
    def key(i):
        return (
            i.get("product_id"),
            _qty(i.get("quantity")),
            float(i.get("unit_price") or 0),
            float(i.get("tax_rate") or 0),
            i.get("hsn") or None,
        )

    def name(i):
        return i.get("product_name") or i.get("sku") or i.get("product_id")

    old_left = list(old_items)
    new_left = []
    for n in new_items:
        same = next((o for o in old_left if key(o) == key(n)), None)
        if same is None:
            new_left.append(n)
        else:
            old_left.remove(same)
    out = []
    for n in new_left:
        o = next((x for x in old_left if x.get("product_id") == n.get("product_id")), None)
        if o is None:
            out.append(f"added {name(n)} x{_qty(n.get('quantity'))}")
            continue
        old_left.remove(o)
        old_qty, new_qty = _qty(o.get("quantity")), _qty(n.get("quantity"))
        if old_qty != new_qty:
            out.append(f"{name(n)}: qty {old_qty} -> {new_qty}")
        old_price = float(o.get("unit_price") or 0)
        new_price = float(n.get("unit_price") or 0)
        if old_price != new_price:
            out.append(f"{name(n)}: cost Rs {_money(old_price)} -> Rs {_money(new_price)}")
        old_rate = float(o.get("tax_rate") or 0)
        new_rate = float(n.get("tax_rate") or 0)
        if old_rate != new_rate:
            out.append(f"{name(n)}: GST {_money(old_rate)}% -> {_money(new_rate)}%")
        if (o.get("hsn") or None) != (n.get("hsn") or None):
            out.append(f"{name(n)}: HSN {o.get('hsn') or 'none'} -> {n.get('hsn') or 'none'}")
    for o in old_left:
        out.append(f"removed {name(o)} x{_qty(o.get('quantity'))}")
    return out


_GST_KEYS = ("tax_rate", "gst_source", "gst_unresolved", "gst_missing", "hsn")


def _settled_lines(po: dict, moved: dict, failed_ids: set, products: dict):
    """The stored lines with each moved line pointed at its real product (and,
    when that is a different product, re-taxed from it) and each line whose
    product could not be written taken off. (items, money fields, what
    changed), or None when no stored line names a product being settled."""
    items, retax, changes = [], [], []
    touched = False
    for line in po.get("items") or []:
        pid = line.get("product_id")
        name = _line_label(line)
        if pid in failed_ids:
            touched = True
            changes.append(
                f"{name} x{_qty(line.get('quantity'))} could not be added to "
                "the catalogue and was taken off this order - add it again"
            )
            continue
        line = dict(line)
        fix = moved.get(pid)
        other_product = bool(fix) and fix["product_id"] != pid
        if fix:
            touched = True
            was = line.get("sku")
            line.update(fix)
            real = products.get(fix["product_id"]) or {}
            if other_product and real.get("hsn_code"):
                line["hsn"] = real["hsn_code"]
            changes.append(
                f"{name} is already catalogued as {line.get('sku')}"
                if other_product
                else f"{name}: catalogue number {was} -> {line.get('sku')}"
            )
        items.append(line)
        # A typed rate stays; otherwise the product the line now names sets it.
        retax.append(other_product and line.get("gst_source") != "line")
    if not touched:
        return None
    raw = [
        {**line, "gst_rate": None, "hsn": None}
        if again
        else {**line, "gst_rate": line.get("tax_rate")}
        for line, again in zip(items, retax)
    ]
    computed = build_po_gst(raw, products.get, *_gst_parties_of(po))
    for line, priced, again in zip(items, computed["items"], retax):
        for key in ("line_tax", "cgst", "sgst", "igst") + (_GST_KEYS if again else ()):
            line[key] = priced[key]
    money = {
        "subtotal": computed["subtotal"],
        "tax_amount": computed["tax"],
        "total_amount": computed["total"],
        "gst_summary": computed["gst_summary"],
    }
    return items, money, changes


def settle_typed_in_lines(po_repo, po_id, typed_in, products, current_user) -> list:
    """Write the typed-in products of an order that was just saved, then make
    the stored lines name what the spine really holds (see
    create_typed_in_products). The correction is a compare-and-set write of
    its own, audited and on the timeline, and only while the order is still a
    DRAFT: a colleague's edit or send in between is never overwritten.

    Returns what the person must be told: [{product_id, product_name, reason}]
    for every typed-in line that is not on the order as typed."""
    moved, failed = create_typed_in_products(typed_in, products, current_user)
    dropped = [
        {**f, "reason": "could not be added to the catalogue and was taken off "
                        "this order - add it again"}
        for f in failed
    ]
    if not (moved or failed) or po_repo is None:
        return dropped
    failed_ids = {f["product_id"] for f in failed}
    for _attempt in range(3):
        po = po_repo.find_by_id(po_id)
        if not po or po.get("status") != "DRAFT":
            break
        settled = _settled_lines(po, moved, failed_ids, products)
        if settled is None:
            return dropped
        items, money, changes = settled
        patch = {"items": items, **money}
        events = [{"kind": "edited", "label": "Lines corrected", "detail": "; ".join(changes)}]
        if not items:
            # Nothing left to order: the same rule as a cancel that leaves
            # nothing -- the order is cancelled, never kept as an empty draft
            # that could be sent.
            why = "Cancelled automatically: none of its lines could be added to the catalogue."
            patch.update(
                status="CANCELLED",
                cancelled_at=_now_iso(),
                cancelled_by=current_user.get("user_id"),
                cancellation_reason=why,
            )
            events.append({"kind": "cancelled", "label": "Cancelled", "detail": f"Reason: {why}"})
            dropped = [
                {**d, "reason": "could not be added to the catalogue, and with nothing "
                                "left on it the order was cancelled - raise it again"}
                for d in dropped
            ]
        try:
            _write_change(
                po_repo,
                po,
                patch,
                events,
                current_user,
                "purchase_order.cancel" if not items else "purchase_order.lines_settled",
                before={"items": po.get("items"), "status": po.get("status")},
                after={"items": items, "total_amount": money["total_amount"],
                       "status": patch.get("status", po.get("status")),
                       "cancellation_reason": patch.get("cancellation_reason")},
            )
            return dropped
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
    # The order moved on (sent, cancelled, gone) before its lines could be
    # corrected: say so loudly rather than leave it silent.
    logger.error(
        "[VENDOR] PO %s: typed-in lines not settled (moved=%s failed=%s)",
        po_id, moved, sorted(failed_ids),
    )
    names = {e["product_id"]: e["line"].product_name for e in typed_in}
    return [
        {"product_id": pid, "product_name": names.get(pid),
         "reason": "the order changed before this line could be corrected - "
                   "check it before it goes to the vendor"}
        for pid in [*failed_ids, *moved]
    ]


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

    # Omitted keeps what is stored (like vendor_id); an explicit null or ''
    # clears it.
    given = body.model_fields_set
    expected = (
        (body.expected_date or None)
        if "expected_date" in given
        else (po.get("expected_date") or None)
    )
    if expected and expected != po.get("expected_date"):
        try:
            expected = expected_date_not_backdated(expected)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    notes = (body.notes or None) if "notes" in given else (po.get("notes") or None)

    old_items = po.get("items") or []
    # A line that OMITS its GST rate or HSN keeps its stored line's, as the
    # form does by sending it back; an explicit value wins, and an explicit
    # null or '' goes back to the catalogue's. Only a rate TYPED on the stored
    # line is kept: any other was worked out, and is worked out again -- an
    # UNRESOLVED line is stored at 0%, and keeping that would pin it at 0% and
    # hide its "GST not settled" warning (the form leaves its rate out for
    # exactly that line). The edit names no line, so a product's lines are
    # matched in order (its 2nd line here is its 2nd stored line) -- only
    # while the edit keeps as many lines of it; otherwise lines that differ
    # cannot tell which was meant, and the edit is refused.
    stored: dict = {}
    for old in old_items:
        typed = old.get("tax_rate") if old.get("gst_source") == "line" else None
        stored.setdefault(old.get("product_id"), []).append(
            {"gst_rate": typed, "hsn": old.get("hsn") or None}
        )
    catalogued = [line for line in body.items if line.new_product is None]
    sent = Counter(line.product_id for line in catalogued)
    nth: Counter = Counter()
    for line in catalogued:
        olds = stored.get(line.product_id) or []
        k = nth[line.product_id]
        nth[line.product_id] += 1
        for field in ("gst_rate", "hsn"):
            values = {old[field] for old in olds}
            if field in line.model_fields_set:
                continue
            if len(values) <= 1:
                value = next(iter(values), None)
            elif sent[line.product_id] == len(olds):
                value = olds[k][field]
            else:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{line.product_name} is on this order with more than "
                        f"one {'GST rate' if field == 'gst_rate' else 'HSN'} "
                        "- send it for each of its lines."
                    ),
                )
            if value is not None:
                setattr(line, field, value)
    computed, products, typed_in = price_po_lines(
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
    # Only now that the edit is saved: a refused edit creates no typed-in
    # product and changes no product cost.
    not_created = settle_typed_in_lines(
        po_repo, po_id, typed_in, products, current_user
    )
    fill_cost_from_rate(po_id, po.get("po_number"), body.items, products, current_user)
    return {**(po_repo.find_by_id(po_id) or {}), "products_not_created": not_created}


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
    by_product: dict = {}
    if status != "DRAFT":
        _refuse_if_box_waiting(po_id)
        by_product = _received_by_product(po)

    if any(by_product.values()):
        items = [dict(i) for i in po.get("items") or []]
        received = _received_per_line(po, by_product)
        _refresh_received(items, received)
        units = sum(_cancel_remainder(it, r) for it, r in zip(items, received))
        if units == 0:
            raise HTTPException(
                status_code=400,
                detail="Nothing on this order is still due.",
            )
        new_status = _status_after_cancel(po, items, by_product)
        patch = {
            "items": items,
            **_reprice(po, items),
            "status": new_status,
            "received_qty_by_product": by_product,
        }
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
            "cancelled_at": _now_iso(),
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
    # What the person saw. The order's version is exact; without it (a card
    # built before the server answered) the line's product and quantity are
    # checked -- two lines may carry one product. An empty value was not sent.
    shown = line.get("ordered_qty")  # what the screen shows as the line's qty
    seen = (body.updated_at, body.product_id, body.quantity)
    now = (
        _stamp(po.get("updated_at")),
        line.get("product_id"),
        _qty(line.get("quantity") if shown is None else shown),
    )
    if any(want not in (None, "") and want != got for want, got in zip(seen, now)):
        raise HTTPException(status_code=409, detail=_CHANGED_MEANWHILE)
    name = _line_label(line)

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
        by_product = None
    else:
        _refuse_if_box_waiting(po_id)
        by_product = _received_by_product(po)
        _refuse_if_arrivals_unattributed(line, items, by_product)
        received = _received_per_line(po, by_product)
        _refresh_received(items, received)
        units = _cancel_remainder(items[line_index], received[line_index])
        if units == 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Nothing is still due on this line - everything ordered "
                    "has arrived."
                ),
            )
        new_status = _status_after_cancel(po, items, by_product)

    patch = {"items": items, **_reprice(po, items), "status": new_status}
    if by_product is not None:
        patch["received_qty_by_product"] = by_product
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
                "cancelled_at": _now_iso(),
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
