"""Unified stock alerts (feeds StockAlertsOverview.tsx)."""

from ._shared import (
    Depends,
    Dict,
    List,
    Optional,
    Query,
    _SOLD_STATUSES,
    _reorder_disabled,
    datetime,
    get_current_user,
    get_stock_repository,
    logger,
    router,
    timedelta,
    validate_store_access,
)
from .helpers import (
    _get_db,
    _had_the_window,
    _parse_expiry,  # generic stored-timestamp parser (ISO / date / datetime)
)

# ============================================================================
# 7. UNIFIED STOCK ALERTS  (feeds StockAlertsOverview.tsx)
# ============================================================================
#
# Replaces the old hardcoded mock list (Vogue Cat Eye / Prada Baroque / etc.)
# the component used to render. Computes real, actionable alerts from the
# catalogue and each product's AVAILABLE units at the store (the low-stock
# list's own count, StockRepository.available_by_product) joined to
# `orders.items` by barcode for sales velocity.
#
# Each product yields AT MOST ONE alert, chosen by priority:
#   REORDER_ALERT > LOW_STOCK (selling low) > DEAD_STOCK
#     > LOW_STOCK (on the low-stock list) > OVERSTOCK > FAST_MOVING
# so a fast seller about to run out is a REORDER, not also a FAST_MOVING.
#
# NOTE on order status: TechCherry historic orders are stamped status
# "DELIVERED" (uppercase); live IMS orders use mixed case. We match a broad
# set of "sold" statuses so imported sales actually count. The same
# _SOLD_STATUSES set is now also used by /non-moving, /overstock-analysis
# and /sell-through-analysis (previously lowercase-only, so they silently
# missed every imported sale).
#
# _SOLD_STATUSES is defined near the top of the ADVANCED INVENTORY FEATURES
# section (before its first use in get_non_moving_stock) so it is always
# initialised before any caller runs.


def _empty_alert_stats() -> dict:
    return {
        "totalAlerts": 0,
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "deadStockValue": 0,
        "recommendedRestockValue": 0,
    }


def _summarise_alert_stats(alerts: List[dict]) -> dict:
    """Roll up an alert list into the AlertStats shape the frontend expects."""
    stats = _empty_alert_stats()
    stats["totalAlerts"] = len(alerts)
    for a in alerts:
        sev = str(a.get("severity", "LOW")).lower()
        if sev in ("critical", "high", "medium", "low"):
            stats[sev] += 1
        impact = a.get("costImpact", 0) or 0
        if a.get("alertType") == "DEAD_STOCK":
            stats["deadStockValue"] += impact
        elif a.get("alertType") in ("REORDER_ALERT", "LOW_STOCK"):
            stats["recommendedRestockValue"] += impact
    stats["deadStockValue"] = round(stats["deadStockValue"], 2)
    stats["recommendedRestockValue"] = round(stats["recommendedRestockValue"], 2)
    return stats


def _low_stock(base, stock, cost, velocity, lead_time_days, suggestions_off, action):
    """Fill `base` as a LOW_STOCK alert. No suggested restock qty when
    auto-reorder is off or nothing has sold (nothing to size an order by)."""
    recommended = (
        0
        if suggestions_off or velocity <= 0
        else max(int(round(velocity * lead_time_days * 2 - stock)), 1)
    )
    base.update(
        {
            "alertType": "LOW_STOCK",
            "severity": "MEDIUM",
            "recommendedOrder": recommended,
            "costImpact": round(recommended * cost, 2),
            "actionRequired": action,
        }
    )
    return base


def _build_stock_alert(
    product: dict,
    sold_30: float,
    last_sale: Optional[datetime],
    now: datetime,
    dead_days: int,
    lead_time_days: int,
    low_stock: bool = False,
    stocked_since: Optional[datetime] = None,
) -> Optional[dict]:
    """Pure classifier — given a product doc plus its sales signals, return a
    single frontend-shaped (camelCase) StockAlert dict, or None if the product
    warrants no alert. No DB access, so it is fully unit-testable.

    ``low_stock``: the product is on GET /inventory/low-stock (the ONE low-stock
    rule, StockRepository.find_low_stock) -- Alerts says LOW_STOCK for it too
    unless a stronger verdict (REORDER, selling low, DEAD_STOCK) applies
    (audit F48). ``stocked_since``: when the
    oldest unit on the shelf arrived; stock younger than the dead-stock window
    has not had the chance to sell and is never called dead. None = unknown
    (legacy rows) = eligible, as before.
    """
    stock = int(product.get("stock_quantity", 0) or 0)
    cost = float(product.get("cost_price", 0) or 0)
    reorder_point = int(product.get("reorder_point", 0) or 0)
    # Owner decision (2026-07-04): reorder_quantity <= 0 (the new -1 default)
    # means auto-reorder is DISABLED for this product -- never emit a
    # REORDER_ALERT / restock suggestion for it. Informational alerts
    # (LOW_STOCK without a suggested qty, DEAD_STOCK, OVERSTOCK, FAST_MOVING)
    # still apply. See api/services/reorder_policy.py.
    reorder_suggestions_off = _reorder_disabled(product)

    velocity = (sold_30 or 0) / 30.0  # units/day from the last 30 days
    days_without_movement = (now - last_sale).days if last_sale else None
    projected = (stock / velocity) if velocity > 0 else None

    sku = product.get("sku") or product.get("barcode") or ""
    base = {
        "id": product.get("barcode") or sku or product.get("name", ""),
        "sku": sku,
        "productName": product.get("name", ""),
        "brand": product.get("brand", ""),
        "category": product.get("category", ""),
        "currentStock": stock,
        "reorderPoint": reorder_point,
        "safetyStock": 0,
        "projectedDaysToStockout": round(projected, 1) if projected is not None else 0,
        "lastMovementDate": (
            last_sale.isoformat() if isinstance(last_sale, datetime) else None
        ),
        "daysWithoutMovement": days_without_movement,
        "salesVelocity": round(velocity, 3),
        "recommendedOrder": 0,
        "costImpact": 0,
    }

    # 1. REORDER_ALERT — sells AND will run out within the reorder lead time
    #    (or is already at/below an explicit reorder point, or out of stock
    #     while still selling).
    out_of_stock_but_selling = stock <= 0 and velocity > 0
    below_reorder_point = reorder_point > 0 and stock <= reorder_point and velocity > 0
    runs_out_soon = projected is not None and projected <= lead_time_days
    if not reorder_suggestions_off and (
        out_of_stock_but_selling or below_reorder_point or runs_out_soon
    ):
        target = velocity * lead_time_days * 2  # cover 2x lead time
        recommended = max(int(round(target - stock)), 1)
        if stock <= 0 or (projected is not None and projected <= lead_time_days / 2):
            severity = "CRITICAL"
        else:
            severity = "HIGH"
        base.update(
            {
                "alertType": "REORDER_ALERT",
                "severity": severity,
                "recommendedOrder": recommended,
                "costImpact": round(recommended * cost, 2),
                "actionRequired": (
                    f"Out of stock - reorder {recommended} units now"
                    if stock <= 0
                    else f"~{int(projected)} days of stock left - reorder {recommended} units"
                ),
            }
        )
        return base

    # 2. LOW_STOCK — sells, getting low, but not yet reorder-critical.
    # When auto-reorder is disabled the alert stays (it is informational)
    # but with NO suggested restock qty (recommendedOrder 0, costImpact 0).
    if velocity > 0 and projected is not None and projected <= lead_time_days * 2:
        return _low_stock(
            base, stock, cost, velocity, lead_time_days, reorder_suggestions_off,
            f"Stock running low (~{int(projected)} days left)",
        )

    # 3. DEAD_STOCK — has stock but no movement in the dead-stock window, and
    # the stock has been on the shelf for that whole window.
    is_dead = stock > 0 and _had_the_window(stocked_since, now, dead_days) and (
        last_sale is None
        or (days_without_movement is not None and days_without_movement >= dead_days)
    )
    if is_dead:
        impact = round(stock * cost, 2)
        if impact >= 50000:
            severity = "CRITICAL"
        elif impact >= 20000:
            severity = "HIGH"
        elif impact >= 5000:
            severity = "MEDIUM"
        else:
            severity = "LOW"
        base.update(
            {
                "alertType": "DEAD_STOCK",
                "severity": severity,
                "costImpact": impact,
                "actionRequired": (
                    f"No recorded sales - {stock} units of capital tied up"
                    if last_sale is None
                    else f"No sales in {days_without_movement} days - consider clearance"
                ),
            }
        )
        return base

    # 3b. LOW_STOCK — on the low-stock list, so Alerts says so too. After
    # DEAD_STOCK: 5 or fewer units covers most frame SKUs, and an unsold frame
    # that has sat out the window is dead stock, not something to reorder.
    if low_stock:
        return _low_stock(
            base, stock, cost, velocity, lead_time_days, reorder_suggestions_off,
            f"Only {stock} left - at or below the low-stock level",
        )

    # 4/5. OVERSTOCK vs FAST_MOVING (both require active selling)
    if stock > 0 and velocity > 0:
        months_of_stock = stock / (velocity * 30.0)
        if months_of_stock >= 6:
            excess = max(int(round(stock - velocity * 30 * 3)), 0)  # beyond 3mo cover
            base.update(
                {
                    "alertType": "OVERSTOCK",
                    "severity": "MEDIUM" if months_of_stock >= 12 else "LOW",
                    "costImpact": round(excess * cost, 2),
                    "actionRequired": (
                        f"~{months_of_stock:.0f} months of stock on hand "
                        f"- {excess} units excess"
                    ),
                }
            )
            return base
        if velocity >= 0.5:  # ~15+ units/month and healthy cover = strong seller
            base.update(
                {
                    "alertType": "FAST_MOVING",
                    "severity": "LOW",
                    "actionRequired": (
                        f"Strong seller (~{velocity * 30:.0f} units/month) "
                        f"- keep well stocked"
                    ),
                }
            )
            return base

    return None


def _aggregate_sales_by_barcode(orders_coll, active_store, thirty_cutoff):
    """Return (sales_30, last_sales) dicts keyed by item barcode.
    sales_30: units sold in the last 30 days. last_sales: all-time last sale
    datetime per barcode. Order items link to products by barcode."""
    match: Dict = {"status": {"$in": _SOLD_STATUSES}}
    if active_store:
        match["store_id"] = active_store

    thirty_pipeline = [
        {"$match": {**match, "created_at": {"$gte": thirty_cutoff}}},
        {"$unwind": "$items"},
        {
            "$group": {
                "_id": "$items.barcode",
                "qty": {"$sum": {"$ifNull": ["$items.quantity", 0]}},
            }
        },
    ]
    last_sale_pipeline = [
        {"$match": match},
        {"$unwind": "$items"},
        {"$group": {"_id": "$items.barcode", "last": {"$max": "$created_at"}}},
    ]

    sales_30 = {
        r["_id"]: r["qty"]
        for r in orders_coll.aggregate(thirty_pipeline)
        if r.get("_id")
    }
    last_sales = {
        r["_id"]: r["last"]
        for r in orders_coll.aggregate(last_sale_pipeline)
        if r.get("_id")
    }
    return sales_30, last_sales


@router.get("/alerts")
async def get_stock_alerts(
    store_id: Optional[str] = Query(None),
    dead_days: int = Query(90, ge=7, le=365),
    lead_time_days: int = Query(14, ge=1, le=90),
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(get_current_user),
):
    """
    Unified, actionable stock alerts for the Inventory > Alerts tab.
    GET /inventory/alerts?dead_days=90&lead_time_days=14

    Returns { alerts: StockAlert[], stats: AlertStats } shaped exactly for
    StockAlertsOverview.tsx. Fail-soft: any DB issue returns an empty
    envelope so the UI shows its clean "No Alerts" state rather than 500ing.
    """
    db = _get_db()
    if db is None:
        return {"alerts": [], "stats": _empty_alert_stats()}

    active_store = validate_store_access(store_id, current_user)

    try:
        products_coll = db.get_collection("products")
        orders_coll = db.get_collection("orders")

        now = datetime.utcnow()
        thirty_cutoff = now - timedelta(days=30)

        # The catalogue is shared; stock is per store and lives in
        # stock_units. Filtering products by products.store_id and reading the
        # legacy products.stock_quantity field made every alert vanish once
        # stock moved to the ledger (audit F48: "No Alerts" beside LOW STOCK 1).
        # Inactive products are read too: one still on the shelf is on the
        # low-stock list, so it is scored here (see the loop below).
        products = list(
            products_coll.find(
                {},
                {
                    "_id": 0,
                    "product_id": 1,
                    "is_active": 1,
                    "name": 1,
                    "brand": 1,
                    "category": 1,
                    "barcode": 1,
                    "sku": 1,
                    "mrp": 1,
                    "offer_price": 1,
                    "cost_price": 1,
                    "stock_quantity": 1,
                    "reorder_point": 1,
                    "reorder_quantity": 1,
                },
            )
        )

        sales_30, last_sales = _aggregate_sales_by_barcode(
            orders_coll, active_store, thirty_cutoff
        )

        # Units per product at this store and when the oldest arrived, from
        # the SAME rows the low-stock list cuts at its threshold
        # (StockRepository.available_by_product / find_low_stock), so Alerts
        # and Low stock count the same units and cannot disagree (audit F48).
        on_hand: Dict[str, dict] = {}
        low_ids: set = set()
        stock_repo = get_stock_repository()
        if active_store and stock_repo is not None:
            on_hand = {
                str(r["_id"]): r
                for r in stock_repo.available_by_product(active_store)
                if r.get("_id")
            }
            low_ids = {
                str(r.get("_id")) for r in stock_repo.find_low_stock(active_store)
            }

        alerts: List[dict] = []
        for p in products:
            barcode = p.get("barcode") or p.get("sku") or ""
            pid = str(p.get("product_id") or "")
            # A discontinued (inactive) product is scored only while it still
            # has units here -- the low-stock list counts units whatever the
            # catalogue flag, and the two screens must agree (audit F48).
            if p.get("is_active") is False and pid not in on_hand:
                continue
            units = on_hand.get(pid) or {}
            alert = _build_stock_alert(
                {**p, "stock_quantity": int(units.get("quantity") or 0)},
                sold_30=sales_30.get(barcode, 0),
                last_sale=last_sales.get(barcode),
                now=now,
                dead_days=dead_days,
                lead_time_days=lead_time_days,
                low_stock=pid in low_ids,
                stocked_since=_parse_expiry(units.get("oldest")),
            )
            if alert:
                alerts.append(alert)

        sev_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        alerts.sort(
            key=lambda a: (
                sev_rank.get(a.get("severity", "LOW"), 4),
                -(a.get("costImpact", 0) or 0),
            )
        )
        alerts = alerts[:limit]

        return {"alerts": alerts, "stats": _summarise_alert_stats(alerts)}

    except Exception as e:
        logger.error(f"get_stock_alerts error: {e}")
        return {"alerts": [], "stats": _empty_alert_stats()}
