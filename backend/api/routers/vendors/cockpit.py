"""Goods-receipt cockpit and the last-purchase-cost lookup."""

from ._shared import (
    Depends,
    Optional,
    Query,
    _RECEIVABLE_PO_STATUSES,
    _VENDOR_ROLES,
    _pm,
    can_access_store_scoped,
    get_grn_repository,
    get_product_repository,
    get_purchase_order_repository,
    get_stock_repository,
    logger,
    require_roles,
    resolve_store_scope,
    router,
    validate_store_access,
    _get_db,
)
from .gst import po_gst_context
from .grn_accept_lock import _GRN_TERMINAL_ACCEPT_STATUSES


@router.get("/goods-receipt/cockpit")
async def goods_receipt_cockpit(
    vendor_id: str = Query(..., description="Vendor to receive against"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Vendor-first goods-receipt cockpit (Purchase P1 / S2).

    One read-only payload with the three worklists the receiving screen needs:
      * open_pos -- this vendor's receivable POs that still have unreceived lines
      * pending_not_received -- per-product residual (ordered - received) summed
        across those open POs
      * pending_cataloged -- ACTIVE cataloged products not already on an open PO
        (products carry no vendor link today, so this list is vendor-agnostic --
        cataloged items ready to be put on a PO / received; capped at 200).

    Residuals read the per-line ordered_qty/received_qty (S1) and fall back to
    the PO header received_qty_by_product for POs created before S1.
    """
    po_repo = get_purchase_order_repository()
    product_repo = get_product_repository()

    open_pos: list = []
    pending: dict = {}
    ordered_product_ids: set = set()

    if po_repo is not None:
        flt: dict = {
            "vendor_id": vendor_id,
            "status": {"$in": list(_RECEIVABLE_PO_STATUSES)},
        }
        # F2 store boundary: resolve the effective store filter for the caller.
        # An explicit store_id is validated (403 if a store-scoped role asks for
        # another store); when omitted, SUPERADMIN/ADMIN see all stores while a
        # store-scoped role is pinned to its OWN active store -- so a store role
        # can no longer see every store's open POs by leaving store_id blank.
        scoped_store = resolve_store_scope(store_id, current_user)
        if scoped_store:
            flt["delivery_store_id"] = scoped_store
        for po in po_repo.find_many(flt, limit=500) or []:
            header_recv = po.get("received_qty_by_product") or {}
            open_lines: list = []
            for it in po.get("items") or []:
                pid = it.get("product_id")
                ordered = it.get("ordered_qty", it.get("quantity", 0)) or 0
                recv = it.get("received_qty")
                if recv is None:
                    recv = header_recv.get(pid, 0)
                recv = recv or 0
                if pid:
                    ordered_product_ids.add(pid)
                if ordered and recv < ordered:
                    residual = ordered - recv
                    open_lines.append(
                        {
                            "product_id": pid,
                            "product_name": it.get("product_name"),
                            "sku": it.get("sku"),
                            "ordered_qty": ordered,
                            "received_qty": recv,
                            "pending_qty": residual,
                            "unit_price": it.get("unit_price"),
                            "tax_rate": it.get("tax_rate"),
                        }
                    )
                    roll = pending.setdefault(
                        pid,
                        {
                            "product_id": pid,
                            "product_name": it.get("product_name"),
                            "sku": it.get("sku"),
                            "ordered_qty": 0,
                            "received_qty": 0,
                            "pending_qty": 0,
                        },
                    )
                    roll["ordered_qty"] += ordered
                    roll["received_qty"] += recv
                    roll["pending_qty"] += residual
            if open_lines:
                open_pos.append(
                    {
                        "po_id": po.get("po_id"),
                        "po_number": po.get("po_number"),
                        "status": po.get("status"),
                        "expected_date": po.get("expected_date"),
                        "lines": open_lines,
                    }
                )

    pending_cataloged: list = []
    if product_repo is not None:
        try:
            actives = product_repo.find_many({"is_active": True}, limit=500) or []
        except Exception:  # noqa: BLE001
            actives = []
        for p in actives:
            pid = p.get("product_id")
            if pid in ordered_product_ids:
                continue
            try:
                status, _gaps = _pm.compute_catalog_status(p)
            except Exception:  # noqa: BLE001
                continue
            if status == "ACTIVE":
                pending_cataloged.append(
                    {
                        "product_id": pid,
                        "product_name": p.get("product_name") or p.get("name"),
                        "sku": p.get("sku"),
                        "category": p.get("category"),
                    }
                )
            if len(pending_cataloged) >= 200:
                break

    return {
        "vendor_id": vendor_id,
        "open_pos": open_pos,
        "pending_not_received": list(pending.values()),
        "pending_cataloged": pending_cataloged,
    }


@router.get("/last-cost")
async def get_last_purchase_cost(
    vendor_id: str = Query(..., description="Vendor to look up prior prices for"),
    product_ids: str = Query(..., description="Comma-separated product_ids to price"),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """The price this vendor was last really paid, per product -- so the PO /
    Buy-Desk form can pre-fill "last paid Rs X on <date>" instead of the
    operator guessing the cost (procurement Phase 2C). THE one last-paid rule:

      1. the cost at acceptance of this vendor's newest accepted goods-receipt
         line for the product, dated by that receipt's acceptance. It is READ
         BACK from the units the receipt minted (goods-receipt accept stamps
         each one with unit_cost), never re-derived here, so the two can never
         disagree. A line that accepted nothing minted nothing and never answers;
      2. only if there is none, the price on this vendor's newest order that was
         sent to it (sent, acknowledged, part- or fully received), dated when it
         was sent -- skipping a cancelled line, and an order whose receipts for
         the product received units but accepted none (a rejected delivery is
         no price paid). A receipt line that arrived as 0 (short-shipped, still
         due) is no delivery at all: that order still answers.

    A DRAFT was never agreed and a CANCELLED (or APPROVED / PENDING, never sent)
    order never bought, so none of them is ever a price paid: the form would put
    a typo or a dropped quote over the catalogue cost, and that becomes the cost
    at acceptance.

    Read-only, store-scoped, fail-soft: DB trouble or no history yields an
    empty map (the form keeps the catalogue cost, no caption). Registered ABOVE
    /purchase-orders/{po_id} so the literal path wins.

    Shape: {"costs": {product_id: {unit_price, po_number, po_id, date}, ...}}.
    """
    wanted = {p.strip() for p in (product_ids or "").split(",") if p.strip()}
    if not vendor_id or not wanted:
        return {"costs": {}}

    po_repo = get_purchase_order_repository()
    if po_repo is None:
        return {"costs": {}}

    costs: dict = {}

    def _take(pid, price, doc, date):
        try:
            price = round(float(price or 0), 2)
        except (TypeError, ValueError):
            return
        if price > 0:
            costs[pid] = {
                "unit_price": price,
                "po_number": doc.get("po_number"),
                "po_id": doc.get("po_id"),
                "date": date,
            }

    try:
        grn_repo = get_grn_repository()
        stock_repo = get_stock_repository()
        grns = (
            grn_repo.find_many(
                {
                    "vendor_id": vendor_id,
                    "status": {"$in": list(_GRN_TERMINAL_ACCEPT_STATUSES)},
                    "items.product_id": {"$in": sorted(wanted)},
                },
                sort=[("accepted_at", -1)],
                # No cap: a capped read let a busy vendor's newer receipts push
                # a product's last receipt (or its rejected delivery) out of
                # view, and the order fallback then answered with a price
                # never paid. ponytail: reads every accepted receipt carrying
                # a wanted product; stream a projected cursor if that grows.
                limit=0,
            )
            if grn_repo is not None and stock_repo is not None
            else []
        )
        received = set()  # (po_id, product_id) some receipt already delivered
        for grn in grns or []:
            if len(costs) >= len(wanted):
                break
            items = [it for it in grn.get("items", []) or [] if isinstance(it, dict)]
            lines = {it.get("product_id") for it in items}
            # Delivered = units arrived. The /purchase/grn screen sends a 0 line
            # for every PO item that did not come; the cockpit drops it. Both
            # must leave that order's price standing.
            received |= {
                (grn.get("po_id"), it.get("product_id"))
                for it in items
                if (it.get("received_qty") or 0) > 0
            }
            # Only surface prices from stores the caller may see (cross-store
            # roles pass); never leak another store's negotiated cost.
            if not can_access_store_scoped(grn.get("store_id"), current_user):
                continue
            for pid in (lines & wanted) - costs.keys():
                unit = stock_repo.find_one(
                    {
                        "source_type": "GRN",
                        "source_id": grn.get("grn_id"),
                        "product_id": pid,
                        "unit_cost": {"$gt": 0},
                    }
                )
                if unit:
                    _take(pid, unit.get("unit_cost"), grn, grn.get("accepted_at"))

        if len(costs) < len(wanted):
            pos = po_repo.find_many(
                {
                    "vendor_id": vendor_id,
                    "status": {"$in": [*_RECEIVABLE_PO_STATUSES, "RECEIVED"]},
                    "items.product_id": {"$in": sorted(wanted - costs.keys())},
                },
                sort=[("sent_at", -1), ("created_at", -1)],
                limit=0,  # uncapped for the same reason as the receipts above
            )
            for po in pos or []:
                if len(costs) >= len(wanted):
                    break
                if not can_access_store_scoped(po.get("delivery_store_id"), current_user):
                    continue
                for it in po.get("items", []) or []:
                    if not isinstance(it, dict) or it.get("line_status") == "CANCELLED":
                        continue  # a line cancel (draft #1165) bought nothing
                    pid = it.get("product_id")
                    if pid not in wanted or pid in costs:
                        continue
                    if (po.get("po_id"), pid) in received:
                        continue  # delivered, and every unit was rejected
                    _take(pid, it.get("unit_price"), po, po.get("sent_at"))
    except Exception as e:  # noqa: BLE001 - read-only helper, never a blocker
        logger.warning("[VENDOR] last-cost lookup failed: %s", e)
        return {"costs": {}}

    return {"costs": costs}


@router.get("/po-gst-heads")
async def get_po_gst_heads(
    store_id: Optional[str] = Query(None, description="Receiving shop (default: your active shop)"),
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """The tax head each vendor's purchase would carry at this shop, decided
    HERE so the PO composer and the Suppliers cards show the verdict the order
    and the bill will book, never one the browser works out from the shop's
    raw GSTIN field (round 12 #3).

    The shop's side is THE shop's GSTIN (org_validation.shop_gstin, through
    po_gst_context); the head is purchase_invoice_engine.classify_supply -- the
    one rule. `heads[vendor_id]` is True (IGST), False (CGST + SGST) or None
    when either side has no state to read (no GSTIN, or a prefix that is not a
    state): the screen says "cannot tell", never a guess.

    Shape: {store_id, shop_gstin, heads: {vendor_id: bool|None}}."""
    from ...services.purchase_invoice_engine import classify_supply

    sid = validate_store_access(store_id, current_user) if store_id else current_user.get("active_store_id")
    if not sid:
        return {"store_id": None, "shop_gstin": "", "heads": {}}
    _, store_doc = po_gst_context(sid, None)
    shop_gstin = str((store_doc or {}).get("gstin") or "").strip()
    heads: dict = {}
    db = _get_db()
    if db is not None:
        try:
            for v in db.get_collection("vendors").find({}, {"_id": 0, "vendor_id": 1, "gstin": 1}).limit(5000):
                vid = v.get("vendor_id")
                if not vid:
                    continue
                verdict = classify_supply(str(v.get("gstin") or "").strip(), shop_gstin)
                both = verdict["supplier_state"] and verdict["recipient_state"]
                heads[vid] = bool(verdict["interstate"]) if both else None
        except Exception as exc:  # noqa: BLE001 - a read aid, never blocks the PO
            logger.warning("[VENDOR] po-gst-heads read failed: %s", exc)
            heads = {}
    return {"store_id": sid, "shop_gstin": shop_gstin, "heads": heads}
