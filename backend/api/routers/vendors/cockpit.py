"""Goods-receipt cockpit and the last-purchase-cost lookup."""

from ._shared import (
    Depends,
    Optional,
    Query,
    _RECEIVABLE_PO_STATUSES,
    _VENDOR_ROLES,
    _pm,
    can_access_store_scoped,
    get_product_repository,
    get_purchase_order_repository,
    logger,
    require_roles,
    resolve_store_scope,
    router,
)


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
    """Most-recent agreed purchase price per product for this vendor, from PO
    history -- so the PO / Buy-Desk form can pre-fill "last paid Rs X on <date>"
    instead of the operator guessing the cost (procurement Phase 2C).

    Reads the vendor's POs newest-first (capped) and takes the first line hit
    per requested product_id. Read-only, fail-soft: DB trouble or no history
    yields an empty map (the form then just shows a blank cost). Registered
    ABOVE /purchase-orders/{po_id} so the literal path wins.

    Shape: {"costs": {product_id: {unit_price, po_number, po_id, date}, ...}}.
    """
    wanted = {p.strip() for p in (product_ids or "").split(",") if p.strip()}
    if not vendor_id or not wanted:
        return {"costs": {}}

    po_repo = get_purchase_order_repository()
    if po_repo is None:
        return {"costs": {}}

    costs: dict = {}
    try:
        # Newest POs for this vendor first; walk lines until every requested
        # product has a price (or the cap is hit).
        pos = po_repo.find_many(
            {"vendor_id": vendor_id},
            sort=[("created_at", -1)],
            limit=100,
        )
        for po in pos or []:
            # Only surface prices from stores the caller may see (cross-store
            # roles pass); never leak another store's negotiated cost.
            if not can_access_store_scoped(po.get("delivery_store_id"), current_user):
                continue
            for it in po.get("items", []) or []:
                if not isinstance(it, dict):
                    continue
                pid = it.get("product_id")
                if pid not in wanted or pid in costs:
                    continue
                try:
                    price = round(float(it.get("unit_price") or 0), 2)
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                costs[pid] = {
                    "unit_price": price,
                    "po_number": po.get("po_number"),
                    "po_id": po.get("po_id"),
                    "date": po.get("created_at"),
                }
            if len(costs) >= len(wanted):
                break
    except Exception as e:  # noqa: BLE001 - read-only helper, never a blocker
        logger.warning("[VENDOR] last-cost lookup failed: %s", e)
        return {"costs": {}}

    return {"costs": costs}
