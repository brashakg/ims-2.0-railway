"""Non-moving stock identification (and the shared sold-status set)."""

from ._shared import (
    Depends,
    HTTPException,
    Optional,
    Query,
    _SOLD_STATUSES,
    _on_hand_status_clause,
    datetime,
    get_current_user,
    logger,
    router,
    timedelta,
    validate_store_access,
)
from .helpers import (
    _get_db,
)

# ============================================================================
# ADVANCED INVENTORY FEATURES (IMS 2.0)
# ============================================================================

# ============================================================================
# 1. NON-MOVING STOCK IDENTIFICATION
# ============================================================================


@router.get("/non-moving")
async def get_non_moving_stock(
    days: int = Query(90, ge=1, le=365),
    category: Optional[str] = Query(None),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Identify products with 0 sales in the last N days.
    GET /inventory/non-moving?days=90
    Scoped to the active store unless an explicit store_id is supplied.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")

    try:
        products_coll = db.get_collection("products")
        orders_coll = db.get_collection("orders")
        stock_coll = db.get_collection("stock_units")

        # Get all products (optionally filtered by category). Normalise short
        # codes / plurals to the canonical value the docs store (fail-open).
        if category:
            from ...services.product_master import resolve_category

            category = resolve_category(category) or category
        query = {} if not category else {"category": category}
        products = list(products_coll.find(query, {"_id": 1, "name": 1, "sku": 1}))

        # Get products with sales in last N days (at the active store)
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        sold_products = set()

        orders_filter = {
            "created_at": {"$gte": cutoff_date},
            "status": {"$in": _SOLD_STATUSES},
        }
        if active_store:
            orders_filter["store_id"] = active_store
        orders = orders_coll.find(orders_filter, {"items": 1})

        for order in orders:
            for item in order.get("items", []):
                sold_products.add(item.get("product_id"))

        # Find non-moving products
        non_moving = []
        for product in products:
            product_id = str(product.get("_id"))
            if product_id not in sold_products:
                # Count ONLY on-hand units -- counting ALL stock_units rows
                # let SOLD units inflate current_stock (10 sold, 0 available
                # showed 10). The PHYSICAL question, through the shared clause:
                # this reader used to carry its own four-spelling list, which
                # is how a lowercase `reserved` unit was stock here and gone to
                # the count.
                stock_filter = {
                    "product_id": product_id,
                    **_on_hand_status_clause(include_reserved=True),
                }
                if active_store:
                    stock_filter["store_id"] = active_store
                stock = stock_coll.find(stock_filter)
                # One serialized stock row == one physical unit; rows with no
                # `quantity` field still count as one unit on hand.
                total_qty = sum(s.get("quantity", 1) for s in stock)

                # Get last sold date (at the active store)
                last_order_filter = {"items.product_id": product_id}
                if active_store:
                    last_order_filter["store_id"] = active_store
                last_order = orders_coll.find_one(
                    last_order_filter,
                    {"created_at": 1},
                    sort=[("created_at", -1)],
                )

                # BUG FIX: days_since_sale was always set to the query
                # parameter `days` instead of the actual days elapsed since
                # the last sale. Products with a last_sold_date showed the
                # wrong staleness figure in the non-moving report.
                last_sold_dt = None
                if last_order:
                    raw_date = last_order.get("created_at")
                    if isinstance(raw_date, datetime):
                        last_sold_dt = raw_date
                    elif isinstance(raw_date, str):
                        try:
                            last_sold_dt = datetime.fromisoformat(
                                raw_date.replace("Z", "+00:00").split("+")[0]
                            )
                        except (ValueError, TypeError):
                            pass
                if last_sold_dt is not None:
                    actual_days_since = (datetime.utcnow() - last_sold_dt).days
                else:
                    actual_days_since = None  # never sold

                non_moving.append(
                    {
                        "product_id": product_id,
                        "name": product.get("name", ""),
                        "sku": product.get("sku", ""),
                        "current_stock": total_qty,
                        "last_sold_date": (
                            last_sold_dt.isoformat() if last_sold_dt else None
                        ),
                        "days_since_sale": actual_days_since,
                    }
                )

        return {
            "total": len(non_moving),
            "days_threshold": days,
            "products": sorted(
                non_moving, key=lambda x: x["current_stock"], reverse=True
            )[:100],
        }

    except Exception as e:
        logger.error(f"get_non_moving_stock error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching non-moving stock")
