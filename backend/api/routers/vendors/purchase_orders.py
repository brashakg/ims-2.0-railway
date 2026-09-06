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


@router.post("/purchase-orders", status_code=201)
async def create_po(
    po: POCreate, current_user: dict = Depends(require_roles(*_VENDOR_ROLES))
):
    """Create a new purchase order"""
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

    # Ruling 13 -- BUY FIRST, CATALOGUE LATER. Any line that carried typed-in
    # identity instead of a product_id becomes a REAL row on the products spine
    # here, through the ONE product door, born provisional: inactive, no selling
    # price, catalog_status DRAFT. That keeps product_id the single join key for
    # receiving, the stock mint, the invoice and the 3-way match, instead of
    # forking identity into a second placeholder system.
    product_repo = get_product_repository()
    for it in po.items:
        if it.new_product is None:
            continue
        np = it.new_product
        try:
            created = _pm.create_via_door(
                {
                    "category": np.category,
                    "brand": np.brand,
                    "model": np.model,
                    "colour": np.colour,
                    "size": np.size,
                    "mrp": np.mrp,
                    # The PO rate is the PROVISIONAL cost (ruling 10); the
                    # purchase invoice corrects it to the actual one (ruling 12).
                    "cost_price": it.unit_price or None,
                    "as_draft": True,
                    "provisional": True,
                },
                source="FORM",
                actor=current_user.get("user_id"),
                actor_name=current_user.get("username"),
                product_repo=product_repo,
                audit_repo=get_audit_repository(),
                db=_get_db(),
            )
        except _pm.ProductMasterError as err:
            # An identical brand+model+colour+size already exists: reuse it
            # rather than refusing the order or minting a twin. The buyer has
            # just typed a description of a product we already know.
            if err.status == 409 and (err.conflict or {}).get("product_id"):
                it.product_id = err.conflict["product_id"]
                it.product_name = it.product_name or err.conflict.get("name")
                it.sku = it.sku or err.conflict.get("sku")
                it.new_product = None
                continue
            raise HTTPException(
                status_code=err.status,
                detail={
                    "code": "NEW_PRODUCT_INVALID",
                    "message": err.message,
                    "field": err.field,
                },
            ) from err
        it.product_id = created.get("product_id")
        it.product_name = (
            it.product_name or created.get("name") or f"{np.brand} {np.model}".strip()
        )
        it.sku = created.get("sku")
        it.new_product = None

    # Hub Phase 2: every PO line must reference a REAL catalogued product on the
    # `products` spine. This rejects a fabricated / placeholder id (e.g. the UI's
    # old `new-<timestamp>` id) at PO creation, so a PO can never carry a line
    # that GRN would later mint as ghost stock. Gated behind pm.po_catalog_gate
    # (DARK by default) so the existing free-text Create-PO form keeps working
    # until the Buy Desk picker ships. Fail-soft when no product repo. A line
    # that arrived as a typed-in new product has just been given a real id
    # above, so it passes this gate like any other.
    if product_repo is not None and _po_catalog_gate_on():
        unknown = [
            it.product_id
            for it in po.items
            if product_repo.find_by_id(it.product_id) is None
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

    # Who supplies whom decides CGST+SGST vs IGST (owner: "GST should be
    # calculated according to interstate or intrastate as per GST norms").
    # Read the delivery store -- with 3 entities over 4 GSTINs in 2 states,
    # "our state" is never a constant.
    _, store_doc = po_gst_context(po.delivery_store_id, None)

    # Per-line GST + place-of-supply split: ONE shared computation, the same
    # one both automatic PO doors call (see build_po_gst). Products are fetched
    # ONCE here and reused for the cost promote below.
    products = {}
    if product_repo is not None:
        for it in po.items:
            if it.product_id not in products:
                products[it.product_id] = product_repo.find_by_id(it.product_id)
    computed = build_po_gst(
        # A typed-in new product was minted onto the spine above and its line
        # given a real product_id; the spent `new_product: None` payload must
        # not ride through **line onto the stored item.
        [it.model_dump(exclude={"new_product"}) for it in po.items],
        products.get,
        vendor if vendor_repo is not None else None,
        store_doc,
    )
    stored_items = computed["items"]
    subtotal = computed["subtotal"]
    tax = computed["tax"]
    total = computed["total"]
    gst_summary = computed["gst_summary"]
    parties = computed["parties"]
    interstate = computed["interstate"]
    gst_warnings = computed["warnings"]

    # Owner ruling 2026-08-26: the rate typed on the PO IS the cost, so raising
    # the PO finishes the cataloguing. Done on CREATE, not on send: the buyer
    # has agreed the price the moment the line is saved, a draft PO may never be
    # sent, and the next of 40 lines should already see the product as costed.
    # Never overwrites an existing cost.
    cost_filled = []
    for item in po.items:
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

    if po_repo is not None:
        po_repo.create(
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

    # Audit the cost figures this PO wrote onto the product spine -- cost feeds
    # margin and valuation, so "who set this cost and from where" must be
    # answerable. Fail-soft: an audit failure never un-creates the PO.
    if cost_filled:
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
        "message": "Purchase order created",
    }
