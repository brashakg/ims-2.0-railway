"""Purchase-order list and creation (manual + from forecast)."""

from ._shared import (
    BaseModel,
    Depends,
    Field,
    HTTPException,
    Optional,
    Query,
    _VENDOR_ROLES,
    _auto_reorder_disabled,
    _get_db,
    _pm,
    _po_catalog_gate_on,
    datetime,
    get_audit_repository,
    get_current_user,
    get_product_repository,
    get_purchase_order_repository,
    get_vendor_repository,
    is_online_store,
    logger,
    require_roles,
    router,
    timedelta,
    uuid,
    validate_store_access,
)
from .gst import (
    _PO_PROVISIONAL_COST_SOURCE,
    _promote_cost_from_rate,
    build_po_gst,
    po_gst_context,
)
from .models import POCreate
from .numbering import generate_po_number


# ============================================================================
# PURCHASE ORDER ENDPOINTS
# ============================================================================


@router.get("/purchase-orders")
async def list_pos(
    vendor_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List purchase orders with filters"""
    po_repo = get_purchase_order_repository()
    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )

    if po_repo is None:
        return {"purchase_orders": [], "total": 0}

    filter_dict = {}
    if vendor_id:
        filter_dict["vendor_id"] = vendor_id
    if status:
        filter_dict["status"] = status
    if active_store:
        filter_dict["delivery_store_id"] = active_store

    pos = po_repo.find_many(filter_dict, skip=skip, limit=limit)

    return {"purchase_orders": pos or [], "total": len(pos) if pos else 0}


# INV-9: Demand forecast -> nightly draft-PO suggestions
# Reads the analytics-v2 demand-forecast data for the caller's store and
# creates a DRAFT purchase order for each product that needs reorder,
# grouped by the product's preferred_vendor_id.  If no vendor is attached to
# a product, the item is placed on a catch-all "unassigned" suggestions list.
# Only SUPERADMIN / ADMIN / AREA_MANAGER / STORE_MANAGER may trigger this
# (mirrors the PO create gate).  Fail-soft: if the demand data can't be read
# the endpoint returns an empty result rather than 500.


class ForecastPoRequest(BaseModel):
    store_id: Optional[str] = None  # defaults to caller's active store
    horizon_days: int = Field(30, ge=7, le=90)  # forecast window
    safety_stock_days: int = Field(7, ge=0, le=30)  # extra buffer days
    # If True a real DRAFT PO doc is persisted per vendor; otherwise returns
    # suggestions only (dry_run=True is safe for the nightly ORACLE cron).
    dry_run: bool = False


@router.post("/purchase-orders/from-forecast", status_code=201)
async def create_pos_from_forecast(
    body: ForecastPoRequest,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Generate DRAFT purchase orders from the demand forecast (INV-9).

    Algorithm:
    1. Pull 90-day sales velocity per product for the store.
    2. For each product where predicted demand > current_stock + safety_stock,
       compute the recommended order quantity.
    3. Group by preferred_vendor_id (stored on the product doc).
    4. Create one DRAFT PO per vendor group (unless dry_run=True).

    Returns a summary and the list of created (or would-be-created) POs.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    active_store = body.store_id or current_user.get("active_store_id") or ""
    if not active_store:
        raise HTTPException(status_code=400, detail="store_id is required")

    # W1.4 / OS-006: forecast POs deliver to the requesting store -- never an
    # ONLINE (stockless, pooled) store.
    if is_online_store(db, active_store):
        raise HTTPException(
            status_code=400,
            detail=(
                "Online stores hold no stock - switch to a physical shop "
                "before generating purchase orders."
            ),
        )

    horizon = body.horizon_days
    safety = body.safety_stock_days

    try:
        from datetime import timedelta

        now = datetime.now()
        ninety_days_ago = now - timedelta(days=90)

        # --- Step 1: compute sales velocity per product (last 90 days) ---
        orders = list(
            db.get_collection("orders")
            .find(
                {
                    "store_id": active_store,
                    "status": {"$nin": ["CANCELLED", "DRAFT"]},
                    "created_at": {"$gte": ninety_days_ago},
                },
                {"items": 1, "order_items": 1},
            )
            .limit(10000)
        )

        product_sales: dict = {}
        for o in orders:
            for item in o.get("items") or o.get("order_items") or []:
                pid = item.get("product_id", "")
                if not pid:
                    continue
                qty = int(item.get("quantity", 1) or 1)
                if pid not in product_sales:
                    product_sales[pid] = {
                        "product_name": item.get("product_name")
                        or item.get("name", ""),
                        "sku": item.get("sku", ""),
                        "qty_90d": 0,
                    }
                product_sales[pid]["qty_90d"] += qty

        if not product_sales:
            return {
                "store_id": active_store,
                "dry_run": body.dry_run,
                "pos_created": 0,
                "suggestions": [],
                "message": "No sales data found for the last 90 days",
            }

        # --- Step 2: join with products for stock + vendor ---
        products_coll = db.get_collection("products")
        product_ids = list(product_sales.keys())
        prod_docs = {
            p.get("product_id"): p
            for p in products_coll.find({"product_id": {"$in": product_ids}})
            if p.get("product_id")
        }

        reorder_items: dict = {}  # vendor_id -> list of line items
        suggestions = []

        for pid, sales in product_sales.items():
            avg_daily = sales["qty_90d"] / 90.0
            predicted = avg_daily * horizon
            buffer = avg_daily * safety
            need = predicted + buffer

            prod = prod_docs.get(pid, {})
            # Owner decision (2026-07-04): reorder_quantity <= 0 (the new -1
            # default) disables auto-reorder for the product -- no forecast
            # suggestion, no draft PO line (see api/services/reorder_policy.py).
            if _auto_reorder_disabled(prod):
                continue
            current_stock = int(prod.get("quantity", 0) or prod.get("stock", 0) or 0)
            reorder_qty = max(0, round(need - current_stock))
            if reorder_qty == 0:
                continue

            vendor_id = prod.get("preferred_vendor_id") or "UNASSIGNED"
            unit_price = float(
                prod.get("cost_price", 0) or prod.get("purchase_price", 0) or 0
            )
            sku = sales.get("sku") or prod.get("sku", "")
            product_name = sales.get("product_name") or prod.get("name", "")

            suggestion = {
                "product_id": pid,
                "product_name": product_name,
                "sku": sku,
                "vendor_id": vendor_id,
                "current_stock": current_stock,
                "avg_daily_sales": round(avg_daily, 2),
                "predicted_demand": round(predicted, 1),
                "safety_buffer": round(buffer, 1),
                "reorder_quantity": reorder_qty,
                "estimated_unit_price": unit_price,
            }
            suggestions.append(suggestion)

            if vendor_id != "UNASSIGNED":
                reorder_items.setdefault(vendor_id, []).append(
                    {
                        "product_id": pid,
                        "product_name": product_name,
                        "sku": sku,
                        "quantity": reorder_qty,
                        "unit_price": unit_price,
                    }
                )

        # --- Step 3: create DRAFT POs per vendor group ---
        created_pos = []
        if not body.dry_run and reorder_items:
            po_repo = get_purchase_order_repository()
            vendor_repo = get_vendor_repository()
            # The receiving shop is the same for every group -- read it once.
            _, _store_doc = po_gst_context(active_store, None)

            for v_id, lines in reorder_items.items():
                vendor = None
                if vendor_repo is not None:
                    vendor = vendor_repo.find_by_id(v_id)
                if vendor is None:
                    # Skip if vendor not found; include in suggestions only
                    continue

                po_id = str(uuid.uuid4())
                po_number = generate_po_number(active_store)
                # SAME per-line GST as the manual door (build_po_gst): the rate
                # comes off each product's HSN and splits CGST+SGST vs IGST from
                # the vendor's and the shop's GST numbers. This used to be a
                # flat `subtotal * 0.18` with no per-line tax_rate stored, which
                # over-taxed every 5% lens/frame order AND made the bill later
                # drafted off it charge 0%.
                computed = build_po_gst(lines, prod_docs.get, vendor, _store_doc)
                subtotal = computed["subtotal"]
                tax = computed["tax"]
                total = computed["total"]

                po_doc = {
                    "po_id": po_id,
                    "po_number": po_number,
                    "vendor_id": v_id,
                    "vendor_name": vendor.get("trade_name") or vendor.get("legal_name"),
                    "delivery_store_id": active_store,
                    "items": computed["items"],
                    "subtotal": subtotal,
                    "tax_amount": tax,
                    "total_amount": total,
                    "gst_summary": computed["gst_summary"],
                    "gst_warnings": computed["warnings"],
                    **computed["parties"],
                    "status": "DRAFT",
                    "source": "demand_forecast",
                    "forecast_horizon_days": horizon,
                    "created_by": current_user.get("user_id"),
                    "created_at": now.isoformat(),
                    "notes": (
                        f"Auto-generated from {horizon}-day demand forecast "
                        f"(safety stock {safety} days)"
                    ),
                }

                if po_repo is not None:
                    try:
                        po_repo.create(po_doc)
                        created_pos.append(
                            {
                                "po_id": po_id,
                                "po_number": po_number,
                                "vendor_id": v_id,
                                "vendor_name": po_doc["vendor_name"],
                                "lines": len(lines),
                                "total_amount": round(total, 2),
                            }
                        )
                    except Exception as _e:
                        logger.warning(
                            f"[INV-9] PO create failed for vendor {v_id}: {_e}"
                        )

        return {
            "store_id": active_store,
            "dry_run": body.dry_run,
            "horizon_days": horizon,
            "safety_stock_days": safety,
            "products_needing_reorder": len(suggestions),
            "pos_created": len(created_pos),
            "created_pos": created_pos,
            "suggestions": suggestions,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"create_pos_from_forecast error: {e}")
        return {
            "store_id": active_store,
            "dry_run": body.dry_run,
            "pos_created": 0,
            "suggestions": [],
            "message": "Forecast data unavailable",
        }


def _typed_in_payload(it) -> dict:
    """The product-door payload for a line typed in instead of picked."""
    np = it.new_product
    return {
        "category": np.category,
        "brand": np.brand,
        "model": np.model,
        "colour": np.colour,
        "size": np.size,
        "mrp": np.mrp,
        # The PO rate is the PROVISIONAL cost (ruling 10); the purchase invoice
        # corrects it to the actual one (ruling 12).
        "cost_price": it.unit_price or None,
        "as_draft": True,
        "provisional": True,
    }


def _new_product_invalid(err) -> HTTPException:
    return HTTPException(
        status_code=err.status,
        detail={
            "code": "NEW_PRODUCT_INVALID",
            "message": err.message,
            "field": err.field,
        },
    )


def price_po_lines(items, vendor, delivery_store_id, current_user):
    """ONE path from the lines a person typed to the lines a PO stores.

    Both doors that take typed lines -- create (POST) and the draft edit (PUT)
    -- call this, so an edited order is priced and gated exactly as a new one:
    the catalogue gate runs, typed-in new products are validated by the product
    door and given their product_id, and GST is built per line (build_po_gst).
    Mutates `items` (a typed-in line gets its product_id). Writes NOTHING.
    Returns (build_po_gst result, the product docs read, the typed-in products
    still to write) -- after the order write, hand the last to
    po_detail.settle_typed_in_lines, then the docs to fill_cost_from_rate.
    """
    product_repo = get_product_repository()

    # Hub Phase 2: every PO line must reference a REAL catalogued product on the
    # `products` spine. This rejects a fabricated / placeholder id (e.g. the UI's
    # old `new-<timestamp>` id) at PO creation, so a PO can never carry a line
    # that GRN would later mint as ghost stock. Gated behind pm.po_catalog_gate
    # (DARK by default) so the existing free-text Create-PO form keeps working
    # until the Buy Desk picker ships. Fail-soft when no product repo. Checked
    # BEFORE any typed-in product is created, so a refused order never leaves
    # one behind; a typed-in line is given its product_id below.
    if product_repo is not None and _po_catalog_gate_on():
        unknown = [
            it.product_id
            for it in items
            if it.new_product is None and product_repo.find_by_id(it.product_id) is None
        ]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        "One or more PO lines reference an unknown product. "
                        "Catalog the product first, then add it to the PO."
                    ),
                    "code": "UNKNOWN_PRODUCT",
                    "product_ids": unknown,
                },
            )

    # Ruling 13 -- BUY FIRST, CATALOGUE LATER. Any line that carried typed-in
    # identity instead of a product_id becomes a REAL row on the products spine,
    # through the ONE product door, born provisional: inactive, no selling
    # price, catalog_status DRAFT. That keeps product_id the single join key for
    # receiving, the stock mint, the invoice and the 3-way match, instead of
    # forking identity into a second placeholder system.
    #
    # Nothing is written here. Every typed-in line passes the door's own
    # validation (build_canonical_product -> normalise_door_payload, the same
    # core create_via_door runs, minus the write) and the door's duplicate rule
    # (find_existing_product) first, and a new one is given its product_id now
    # so the stored line can carry it. The product itself is written by
    # po_detail.settle_typed_in_lines, called only AFTER the order write: a
    # refused order or edit -- a later line the door refuses, a colleague who
    # sent the draft meanwhile, a lost compare-and-set -- leaves no provisional
    # product behind, however many lines it typed in.
    db = _get_db()
    typed_in = []  # typed-in products still to write, each with its line
    previews = {}  # product_id -> the doc the door built (GST reads it below)
    claimed = {}  # identity -> (product_id, name, sku) minted in this request
    for it in items:
        np = it.new_product
        if np is None:
            continue
        payload = _typed_in_payload(it)
        try:
            preview = _pm.build_canonical_product(
                payload, source="FORM", product_repo=product_repo, db=db
            )
        except _pm.ProductMasterError as err:
            raise _new_product_invalid(err) from err
        key = preview.get("identity_key") or preview.get("sku")
        # An identical brand+model+colour+size already exists: reuse it rather
        # than refusing the order or minting a twin. The buyer has just typed a
        # description of a product we already know. Two lines of one request
        # that describe the same product share one new row.
        existing = _pm.find_existing_product(preview, product_repo)
        if existing is not None:
            pid, name, sku = (
                existing.get("product_id"), existing.get("name"), existing.get("sku")
            )
        elif key in claimed:
            pid, name, sku = claimed[key]
        else:
            pid, name, sku = str(uuid.uuid4()), preview.get("name"), preview.get("sku")
            claimed[key] = (pid, name, sku)
            previews[pid] = preview
            # The door writes exactly the SKU the stored line will carry; left
            # to itself it would mint a second collision suffix. A minted SKU
            # keeps the colour and size as typed ('C.01', '14.0'), which the
            # door refuses as a SUPPLIED SKU -- that one it mints itself, and
            # the stored line is corrected after the write.
            if _pm.is_acceptable_sku(sku):
                payload["sku"] = sku
            typed_in.append(
                {"line": it, "payload": payload, "product_id": pid,
                 "identity_key": preview.get("identity_key")}
            )
        it.product_id = pid
        it.product_name = it.product_name or name or f"{np.brand} {np.model}".strip()
        it.sku = sku if pid in previews else (it.sku or sku)
        it.new_product = None

    # Who supplies whom decides CGST+SGST vs IGST (owner: "GST should be
    # calculated according to interstate or intrastate as per GST norms").
    # Read the delivery store -- with 3 entities over 4 GSTINs in 2 states,
    # "our state" is never a constant.
    _, store_doc = po_gst_context(delivery_store_id, None)

    # Per-line GST + place-of-supply split: ONE shared computation, the same
    # one both automatic PO doors call (see build_po_gst). Products are fetched
    # ONCE here and handed back for the cost fill after the write.
    products = dict(previews)
    if product_repo is not None:
        for it in items:
            if it.product_id not in products:
                products[it.product_id] = product_repo.find_by_id(it.product_id)
    computed = build_po_gst(
        # A typed-in line was given its product_id above (the product itself
        # is written after the order); the spent `new_product: None` payload
        # must not ride through **line onto the stored item.
        [it.model_dump(exclude={"new_product"}) for it in items],
        products.get,
        vendor,
        store_doc,
    )

    return computed, products, typed_in


def create_typed_in_products(typed_in, products, current_user):
    """Write the typed-in products price_po_lines held back. Call it only
    AFTER the order that names them is saved (po_detail.settle_typed_in_lines
    does, and repairs the stored lines from what this returns).

    Each product is written under the product_id and SKU its line already
    carries. Returns (moved, failed):
      moved  -- {pre-minted product_id: {product_id, sku}} for a line the
                stored order must be pointed at: the identical product (same
                identity key) was created by someone else meanwhile, so the
                line names that one; or another product took the SKU, so the
                door minted a fresh one.
      failed -- [{product_id, product_name}] for a product the door could not
                write at all; its line must come off the order.
    `products` and each line are kept in step, so the cost fill that follows
    reads the row the line really names, and never one that does not exist."""
    if not typed_in:
        return {}, []
    product_repo = get_product_repository()
    moved, failed = {}, []
    for entry in typed_in:
        it, pid = entry["line"], entry["product_id"]
        pinned = entry["payload"]
        attempts = [pinned]
        if "sku" in pinned:
            attempts.append({k: v for k, v in pinned.items() if k != "sku"})
        created = winner = None
        for payload in attempts:
            try:
                created = _pm.create_via_door(
                    payload,
                    source="FORM",
                    actor=current_user.get("user_id"),
                    actor_name=current_user.get("username"),
                    extra_fields={"product_id": pid},
                    product_repo=product_repo,
                    audit_repo=get_audit_repository(),
                    db=_get_db(),
                )
                break
            except _pm.ProductMasterError as err:
                conflict = (err.conflict or {}) if err.status == 409 else {}
                same = entry.get("identity_key") and conflict.get(
                    "identity_key"
                ) == entry.get("identity_key")
                if conflict.get("product_id") and same:
                    winner = conflict
                    break
                if err.status == 409 and payload is not attempts[-1]:
                    continue  # another product holds the SKU: let the door mint
                logger.error(
                    "[VENDOR] typed-in product %s (%s) was not created: %s",
                    pid, it.product_name, err.message,
                )
                break
        products.pop(pid, None)
        if winner is not None:
            name = winner.get("name") or it.product_name
            moved[pid] = {
                "product_id": winner["product_id"], "sku": winner.get("sku"),
                "product_name": name,
            }
            it.product_id, it.sku, it.product_name = winner["product_id"], winner.get("sku"), name
            if product_repo is not None:
                products[it.product_id] = product_repo.find_by_id(it.product_id)
        elif created is not None:
            products[pid] = created
            if created.get("sku") and created.get("sku") != it.sku:
                moved[pid] = {"product_id": pid, "sku": created.get("sku")}
                it.sku = created.get("sku")
        else:
            failed.append({"product_id": pid, "product_name": it.product_name})
    return moved, failed


def fill_cost_from_rate(po_id, po_number, items, products, current_user) -> list:
    """Owner ruling 2026-08-26: the rate typed on the PO IS the cost, so saving
    the PO finishes the cataloguing. Done when the lines are SAVED, not on send:
    the buyer has agreed the price the moment the line is saved, a draft PO may
    never be sent, and the next of 40 lines should already see the product as
    costed. Never overwrites an existing cost.

    Call it only AFTER the order write succeeded: an order or edit that was
    refused must leave every product's cost as it was. Every cost it writes is
    audited here -- cost feeds margin and valuation, so "who set this cost and
    from where" must be answerable. Fail-soft: an audit failure never undoes
    the PO write. Returns [{product_id, cost_price}] for each cost written."""
    product_repo = get_product_repository()
    cost_filled = []
    for item in items:
        prod = products.get(item.product_id)
        if _promote_cost_from_rate(
            item.product_id,
            prod,
            item.unit_price,
            _PO_PROVISIONAL_COST_SOURCE,
            product_repo,
        ):
            cost_filled.append(
                {"product_id": item.product_id, "cost_price": round(item.unit_price, 2)}
            )
            # Keep the cached doc honest: two lines of one PO may carry the same
            # product, and the second must see the cost the first just wrote
            # (otherwise it overwrites it at its own price).
            products[item.product_id] = {
                **(prod or {}),
                "cost_price": round(item.unit_price, 2),
                "cost_source": _PO_PROVISIONAL_COST_SOURCE,
            }

    if not cost_filled:
        return cost_filled
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "purchase.cost_from_po_rate",
                    "entity_type": "purchase_order",
                    "entity_id": po_id,
                    "user_id": current_user.get("user_id"),
                    "detail": {"po_number": po_number, "products": cost_filled},
                }
            )
    except Exception:  # noqa: BLE001
        pass
    return cost_filled


# Owner ruling 2026-09-28: the CATALOGUE MANAGER raises a DRAFT from the Buy
# Desk and the store manager checks and sends it. This door only ever writes a
# DRAFT, so adding the role here grants exactly "draft" -- sending, editing and
# cancelling stay on _VENDOR_ROLES (the rbac row carves this route its own
# capability key so the vendors:write union does not grow; see capabilities).
_PO_DRAFT_ROLES = (*_VENDOR_ROLES, "CATALOG_MANAGER")


@router.post("/purchase-orders", status_code=201)
async def create_po(
    po: POCreate, current_user: dict = Depends(require_roles(*_PO_DRAFT_ROLES))
):
    """Create a new purchase order (always a DRAFT)."""
    po_repo = get_purchase_order_repository()
    vendor_repo = get_vendor_repository()

    # F2 store boundary: a store-scoped role may only raise a PO for a store it
    # can access. validate_store_access 403s another store; ADMIN / AREA_MANAGER /
    # SUPERADMIN pass. Without this, delivery_store_id came straight off the
    # request body, so a store-scoped user could craft a PO against another
    # store's inventory.
    validate_store_access(po.delivery_store_id, current_user)

    # W1.4 / OS-006: an ONLINE store (pooled, stockless) can never be the
    # delivery store -- receiving there would mint phantom owned stock that
    # corrupts the pooled-inventory model feeding the live storefront.
    if is_online_store(None, po.delivery_store_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "Online stores hold no stock - choose a physical shop as the "
                "delivery store for this purchase order."
            ),
        )

    po_id = str(uuid.uuid4())
    po_number = generate_po_number(po.delivery_store_id)

    # Validate vendor exists
    if vendor_repo is not None:
        vendor = vendor_repo.find_by_id(po.vendor_id)
        if vendor is None:
            raise HTTPException(status_code=404, detail="Vendor not found")

    computed, products, typed_in = price_po_lines(
        po.items,
        vendor if vendor_repo is not None else None,
        po.delivery_store_id,
        current_user,
    )
    stored_items = computed["items"]
    subtotal = computed["subtotal"]
    tax = computed["tax"]
    total = computed["total"]
    gst_summary = computed["gst_summary"]
    parties = computed["parties"]
    interstate = computed["interstate"]
    gst_warnings = computed["warnings"]

    if po_repo is not None:
        saved = po_repo.create(
            {
                "po_id": po_id,
                "po_number": po_number,
                "vendor_id": po.vendor_id,
                "vendor_name": (
                    vendor.get("trade_name")
                    if vendor_repo is not None and vendor
                    else None
                ),
                "delivery_store_id": po.delivery_store_id,
                "items": stored_items,
                "subtotal": subtotal,
                "tax_amount": tax,
                "total_amount": total,
                "expected_date": po.expected_date,
                "notes": po.notes,
                "status": "DRAFT",
                "gst_summary": gst_summary,
                **parties,
                "created_by": current_user.get("user_id"),
                "created_at": datetime.now().isoformat(),
            }
        )

        # BaseRepository.create answers None on a failed insert instead of
        # raising: say so, rather than 201 for an order that does not exist and
        # then products and costs written for it.
        if not saved:
            raise HTTPException(
                status_code=500, detail="The purchase order could not be saved."
            )

    # Only now that the order is saved: a refused order leaves no product.
    from .po_detail import settle_typed_in_lines  # po_detail imports this module

    not_created = settle_typed_in_lines(
        po_repo, po_id, typed_in, products, current_user
    )
    if typed_in and po_repo is not None:
        # Settling may have corrected or removed lines: answer with the order
        # as stored, never the totals priced before it.
        stored = po_repo.find_by_id(po_id) or {}
        if stored.get("status") == "CANCELLED":
            # 409, never 5xx: the browser client replays every 5xx POST three
            # times, and each replay would raise (and cancel) another order.
            names = ", ".join(n.get("product_name") or "a typed-in item" for n in not_created)
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "TYPED_IN_NOT_ADDED",
                    "message": (
                        f"{names} could not be added to the catalogue, so nothing "
                        f"was left to order and {po_number} was cancelled. Try "
                        "again in a moment."
                    ),
                    "po_id": po_id,
                    "products_not_created": not_created,
                },
            )
        total = stored.get("total_amount", total)
        gst_summary = stored.get("gst_summary", gst_summary)
        gst_warnings = [
            {
                "product_id": it.get("product_id"),
                "product_name": it.get("product_name"),
                "missing": it.get("gst_missing"),
                "taxed": not it.get("gst_unresolved"),
            }
            for it in stored.get("items") or []
            if it.get("gst_missing")
        ]
    cost_filled = fill_cost_from_rate(
        po_id, po_number, po.items, products, current_user
    )

    return {
        "po_id": po_id,
        "po_number": po_number,
        "total_amount": total,
        "interstate": interstate,
        "gst_summary": gst_summary,
        # EVERY line whose HSN could not settle the rate -- including the ones
        # taxed anyway off the catalogue rate. HSN is mandatory on a GST
        # purchase document, so a taxed line with no HSN is still a problem the
        # buyer has to be told about.
        "gst_warnings": gst_warnings,
        "cost_filled": cost_filled,
        "products_not_created": not_created,
        "message": "Purchase order created",
    }
