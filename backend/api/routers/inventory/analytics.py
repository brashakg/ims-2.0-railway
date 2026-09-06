"""Sell-through, brand insights and overstock analysis."""

from ._shared import (
    Any,
    Depends,
    Dict,
    HTTPException,
    List,
    Optional,
    Query,
    _SOLD_STATUSES,
    _on_hand_status_clause,
    datetime,
    get_current_user,
    logger,
    resolve_store_scope,
    router,
    timedelta,
)
from .helpers import (
    _get_db,
    _on_hand_by_product,
)

# ============================================================================
# 5. SELL-THROUGH % BY BRAND GROUP
# ============================================================================


@router.get("/sell-through-analysis")
async def get_sell_through_analysis(
    days: int = Query(30, ge=1, le=365),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Get sell-through rate per brand.
    Sell-through = units sold / units stocked * 100
    GET /inventory/sell-through-analysis?days=30
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        orders_coll = db.get_collection("orders")
        stock_coll = db.get_collection("stock_units")
        products_coll = db.get_collection("products")

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Store-scope: the endpoint accepted ?store_id but ignored it and
        # aggregated across ALL stores. Resolve the caller's scope (None = all
        # stores for HQ roles; the caller's OWN store for store-level roles) and
        # apply it to both the sales and the stock side.
        active_store = resolve_store_scope(store_id, current_user)
        _order_q = {
            "created_at": {"$gte": cutoff_date},
            "status": {"$in": _SOLD_STATUSES},
        }
        if active_store:
            _order_q["store_id"] = active_store

        # PERF: this endpoint used to run products.find_one PER ORDER ITEM and
        # PER PHYSICAL STOCK UNIT (thousands of round-trips on a live store).
        # Same batched shape as /brand-insights below: one orders pass + one
        # stock pass collect quantities per product_id, then ONE products
        # query maps product_id -> brand and the totals fold to brand level.
        # The old code resolved each id with find_one({"_id": pid}); _id is
        # unique, so a single {"_id": {"$in": [...]}} fetch resolves exactly
        # the same docs (a pid with no products doc stays skipped, as before).

        # Pass 1: units sold per product from completed orders
        sold_by_pid: Dict[str, Any] = {}
        orders = orders_coll.find(_order_q)
        for order in orders:
            for item in order.get("items", []):
                product_id = item.get("product_id")
                if product_id is None:
                    continue
                qty = item.get("quantity", 0)
                sold_by_pid[product_id] = sold_by_pid.get(product_id, 0) + qty

        # Pass 2: units on hand per product (same store-scope as sales above)
        stocked_by_pid: Dict[str, Any] = {}
        stocks = stock_coll.find({"store_id": active_store} if active_store else {})
        for stock in stocks:
            product_id = stock.get("product_id")
            if product_id is None:
                continue
            # One serialized stock row == one physical unit; a row with no
            # `quantity` field still represents one unit on hand.
            qty = stock.get("quantity", 1)
            stocked_by_pid[product_id] = stocked_by_pid.get(product_id, 0) + qty

        # ONE products lookup for every product seen on either side.
        all_pids = list({*sold_by_pid, *stocked_by_pid})
        brand_by_pid: Dict[str, Any] = {}
        if all_pids:
            for product in products_coll.find(
                {"_id": {"$in": all_pids}}, {"brand": 1}
            ):
                brand_by_pid[product["_id"]] = product.get("brand", "Unknown")

        # Fold product totals to brand level (unresolved pids skipped, exactly
        # as the per-item find_one behaved when it found no product).
        sales_by_brand = {}
        for pid, qty in sold_by_pid.items():
            if pid in brand_by_pid:
                brand = brand_by_pid[pid]
                sales_by_brand[brand] = sales_by_brand.get(brand, 0) + qty

        stock_by_brand = {}
        for pid, qty in stocked_by_pid.items():
            if pid in brand_by_pid:
                brand = brand_by_pid[pid]
                stock_by_brand[brand] = stock_by_brand.get(brand, 0) + qty

        # Calculate sell-through %
        brands = set(list(sales_by_brand.keys()) + list(stock_by_brand.keys()))
        results = []

        for brand in brands:
            units_sold = sales_by_brand.get(brand, 0)
            units_stocked = stock_by_brand.get(brand, 0)
            sell_through = (
                (units_sold / max(units_stocked, 1)) * 100 if units_stocked > 0 else 0
            )

            results.append(
                {
                    "brand": brand,
                    "units_sold": units_sold,
                    "units_stocked": units_stocked,
                    "sell_through_percent": round(sell_through, 2),
                }
            )

        return {
            "period_days": days,
            "brands": sorted(
                results, key=lambda x: x["sell_through_percent"], reverse=True
            ),
        }

    except Exception as e:
        logger.error(f"get_sell_through_analysis error: {e}")
        raise HTTPException(status_code=500, detail="Error calculating sell-through")


# ============================================================================
# 5b. BRAND-WISE INVENTORY INSIGHTS (Inventory > Insights > Brands)
# ============================================================================


@router.get("/brand-insights")
async def get_brand_insights(
    days: int = Query(30, ge=1, le=365),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Brand-wise KPI rollup: on-hand units, stock value (offer-price basis,
    mrp fallback), units sold + revenue over the window, sell-through % and
    days of cover -- KPI math shared with collection_insights so the Brands
    and Collections insights tabs agree.

    GET /inventory/brand-insights?days=30

    Unlike /sell-through-analysis this does NO per-item product lookups:
    ONE projected products scan (brand + prices), ONE stock_units rollup
    (_on_hand_by_product: canonical ON_HAND/EXCLUDED status conventions) and
    ONE orders aggregation (qty/item_total fields as in
    collection_insights._movement_pipeline). Blank brands fold to "Unknown".
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    # Resolve the caller's store reach BEFORE the catch-all try so a
    # legitimate 403 from validate_store_access is not swallowed into a 500.
    active_store = resolve_store_scope(store_id, current_user)

    try:
        from ...services import brand_insights as _bi

        # ONE projected spine scan: brand + unit pricing for every product.
        product_docs = list(
            db.get_collection("products").find(
                {},
                {"_id": 1, "product_id": 1, "brand": 1, "offer_price": 1, "mrp": 1},
            )
        )

        # On-hand rollup over every known pid (both id conventions), reusing
        # the canonical on-hand status allowlist/exclusions.
        pids: List[str] = []
        seen: set = set()
        for doc in product_docs:
            for key in (doc.get("product_id"), doc.get("_id")):
                if key is None:
                    continue
                pid = str(key)
                if pid and pid not in seen:
                    pids.append(pid)
                    seen.add(pid)
        on_hand = _on_hand_by_product(db, pids, active_store)

        # ONE pass over the window's sold orders: units + line revenue per
        # product. Same field conventions as the collections movement math
        # (qty | quantity | 1 for units; item_total for line revenue).
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        order_match: Dict = {
            "created_at": {"$gte": cutoff_date},
            "status": {"$in": _SOLD_STATUSES},
        }
        if active_store:
            order_match["store_id"] = active_store
        qty_expr = {"$ifNull": ["$items.qty", {"$ifNull": ["$items.quantity", 1]}]}
        sales_by_pid: Dict[str, Dict] = {}
        try:
            sales_rows = db.get_collection("orders").aggregate(
                [
                    {"$match": order_match},
                    {"$unwind": "$items"},
                    {
                        "$group": {
                            "_id": "$items.product_id",
                            "units": {"$sum": qty_expr},
                            "revenue": {"$sum": {"$ifNull": ["$items.item_total", 0]}},
                        }
                    },
                ]
            )
            for row in sales_rows:
                if not isinstance(row, dict):
                    continue
                pid = row.get("_id")
                units = row.get("units")
                # Defend against the mock aggregate stub (echoes raw docs):
                # a real group row has a scalar _id and a numeric units sum.
                if not pid or isinstance(pid, dict) or not isinstance(units, (int, float)):
                    continue
                sales_by_pid[str(pid)] = {
                    "units": int(units or 0),
                    "revenue": float(row.get("revenue") or 0),
                }
        except Exception as agg_exc:  # noqa: BLE001 - fail-soft to zero sales
            logger.warning(f"brand-insights sales aggregation failed: {agg_exc}")

        rows = _bi.fold_brand_rows(product_docs, on_hand, sales_by_pid, days)
        return {"period_days": days, "store_id": active_store, "brands": rows}

    except Exception as e:
        logger.error(f"get_brand_insights error: {e}")
        raise HTTPException(status_code=500, detail="Error calculating brand insights")


# ============================================================================
# 6. STOCK DUMP ANALYSIS (OVERSTOCK)
# ============================================================================


@router.get("/overstock-analysis")
async def get_overstock_analysis(
    overstocking_threshold: float = Query(3.0, ge=1.0),
    days: int = Query(30, ge=1, le=365),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Flag overstocked items: current_stock > threshold * avg_monthly_sales
    GET /inventory/overstock-analysis?overstocking_threshold=3.0&days=30
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        orders_coll = db.get_collection("orders")
        stock_coll = db.get_collection("stock_units")
        products_coll = db.get_collection("products")

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Store-scope: honour the caller's reach (None = all stores for HQ roles;
        # own store for store-level) instead of ignoring ?store_id and reading
        # every store's sales + stock.
        active_store = resolve_store_scope(store_id, current_user)
        _order_q = {
            "created_at": {"$gte": cutoff_date},
            "status": {"$in": _SOLD_STATUSES},
        }
        if active_store:
            _order_q["store_id"] = active_store

        # Get sales volume by product
        sales_by_product = {}
        orders = orders_coll.find(_order_q)

        for order in orders:
            for item in order.get("items", []):
                product_id = item.get("product_id")
                qty = item.get("quantity", 0)
                sales_by_product[product_id] = sales_by_product.get(product_id, 0) + qty

        # Calculate average monthly sales
        months = max(days / 30, 1)
        avg_monthly_sales = {pid: qty / months for pid, qty in sales_by_product.items()}

        # Roll up on-hand per product. The serialized model stores ONE row per
        # physical unit, so we must aggregate (count) rows -- iterating raw rows
        # one-by-one compared a single unit against the threshold (which never
        # flags) and emitted a duplicate entry per unit. $ifNull counts legacy
        # rows that predate the `quantity` field as one unit each.
        stock_match = dict(_on_hand_status_clause(include_reserved=True))
        if active_store:
            stock_match["store_id"] = active_store
        stock_rows = list(
            stock_coll.aggregate(
                [
                    {"$match": stock_match},
                    {
                        "$group": {
                            "_id": "$product_id",
                            "qty": {"$sum": {"$ifNull": ["$quantity", 1]}},
                        }
                    },
                ]
            )
        )

        # Identify overstock at the PRODUCT level.
        overstocked = []
        for row in stock_rows:
            product_id = str(row.get("_id"))
            current_qty = int(row.get("qty", 0) or 0)
            avg_monthly = avg_monthly_sales.get(product_id, 0)

            # Flag if current > threshold * average
            if current_qty > (overstocking_threshold * avg_monthly):
                product = products_coll.find_one({"_id": product_id})
                months_of_stock = current_qty / max(avg_monthly, 1)

                if product:
                    brand = product.get("brand", "")
                    model = product.get("model", "")
                    product_name = (
                        product.get("name")
                        or f"{brand} {model}".strip()
                        or product.get("sku", "")
                        or "Unknown"
                    )
                    sku = product.get("sku", "")
                else:
                    product_name = "Unknown"
                    sku = ""

                overstocked.append(
                    {
                        "product_id": product_id,
                        "product_name": product_name,
                        "sku": sku,
                        "current_stock": current_qty,
                        "avg_monthly_sales": round(avg_monthly, 2),
                        "months_of_stock": round(months_of_stock, 1),
                        "overstock_multiple": round(
                            current_qty / max(avg_monthly, 1), 2
                        ),
                    }
                )

        return {
            "threshold_multiple": overstocking_threshold,
            "analysis_period_days": days,
            "total_overstocked": len(overstocked),
            "items": sorted(
                overstocked, key=lambda x: x["months_of_stock"], reverse=True
            )[:50],
        }

    except Exception as e:
        logger.error(f"get_overstock_analysis error: {e}")
        raise HTTPException(status_code=500, detail="Error analyzing overstock")
