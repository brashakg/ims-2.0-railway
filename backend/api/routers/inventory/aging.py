"""Stock aging / non-moving report."""

from ._shared import (
    Depends,
    Optional,
    Query,
    _on_hand_status_clause,
    datetime,
    get_current_user,
    get_product_repository,
    get_stock_repository,
    router,
    timedelta,
    validate_store_access,
)
from .helpers import _had_the_window, _parse_expiry
from database.repositories.product_repository import group_with_oldest_arrival

# ============================================================================
# STOCK AGING / NON-MOVING REPORT
# ============================================================================

# ponytail: one fixed grace (the first age bucket); a per-category grace is a
# setting when a slow-selling category needs longer.
_AGING_GRACE_DAYS = 30


@router.get("/aging")
async def get_stock_aging_report(
    store_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    classification: Optional[str] = Query(None, description="A, B, or C"),
    min_days: Optional[int] = Query(None, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """
    Stock aging report — calculates days in stock, turnover rate,
    and ABC classification for each product in the store.
    Uses real stock + order data from MongoDB.
    """
    stock_repo = get_stock_repository()
    product_repo = get_product_repository()
    active_store = validate_store_access(store_id, current_user)

    # Category-filter fix: normalise short codes / plurals to canonical.
    if category:
        from ...services.product_master import resolve_category

        category = resolve_category(category) or category

    if stock_repo is None or product_repo is None:
        return {"products": [], "summary": {}}

    now = datetime.utcnow()
    thirty_days_ago = now - timedelta(days=30)
    ninety_days_ago = now - timedelta(days=90)

    # 1. Get all on-hand stock grouped by product. Aging is the PHYSICAL
    # question (a reserved frame is still ageing on this shelf), so it reads
    # the shared clause with RESERVED on -- never its own status list.
    stock_pipeline = [
        {
            "$match": {
                "store_id": active_store,
                **_on_hand_status_clause(include_reserved=True),
            }
        },
        *group_with_oldest_arrival(
            {
                "_id": "$product_id",
                "quantity": {"$sum": 1},
                "total_value": {"$sum": {"$ifNull": ["$mrp", 0]}},
            }
        ),
    ]
    stock_groups = stock_repo.aggregate(stock_pipeline)

    if not stock_groups:
        return {
            "products": [],
            "summary": {
                "total": 0,
                "classA": 0,
                "classB": 0,
                "classC": 0,
                "slowMovingValue": 0,
                "averageAge": 0,
            },
        }

    # 2. Get sold items in last 30 and 90 days for turnover calculation
    sold_30d_pipeline = [
        {
            "$match": {
                "store_id": active_store,
                "status": "SOLD",
                "sold_at": {"$gte": thirty_days_ago},
            }
        },
        {"$group": {"_id": "$product_id", "sales_30d": {"$sum": 1}}},
    ]
    sold_90d_pipeline = [
        {
            "$match": {
                "store_id": active_store,
                "status": "SOLD",
                "sold_at": {"$gte": ninety_days_ago},
            }
        },
        {"$group": {"_id": "$product_id", "sales_90d": {"$sum": 1}}},
    ]
    last_sale_pipeline = [
        {"$match": {"store_id": active_store, "status": "SOLD"}},
        {"$group": {"_id": "$product_id", "last_sale": {"$max": "$sold_at"}}},
    ]

    sales_30d = {
        r["_id"]: r["sales_30d"] for r in stock_repo.aggregate(sold_30d_pipeline)
    }
    sales_90d = {
        r["_id"]: r["sales_90d"] for r in stock_repo.aggregate(sold_90d_pipeline)
    }
    last_sales = {
        r["_id"]: r["last_sale"] for r in stock_repo.aggregate(last_sale_pipeline)
    }

    # 3. Enrich with product details and calculate metrics
    products = []
    for sg in stock_groups:
        pid = sg["_id"]
        product = product_repo.find_by_id(pid)
        if not product:
            # Catalog-only products are not in the products spine; fall back to
            # catalog_products using the helper from orders.py (reuse, not reimpl).
            try:
                from ..orders import _resolve_catalog_product_doc
                product = _resolve_catalog_product_doc(pid)
            except Exception:
                product = None
        if not product:
            continue

        if category and product.get("category", "") != category:
            continue

        qty = sg.get("quantity", 0)
        oldest = _parse_expiry(sg.get("oldest"))  # None = unknown
        # created_at is naive datetime.now() while `now` is utcnow(): on a box
        # east of UTC this morning's receipt reads as -1 days (audit F54).
        # Age is never negative. Unknown age is legacy stock, so old (the
        # verdict below says so too): no day count to show (None), oldest
        # bucket, never cut by min_days.
        days_in_stock = max(0, (now - oldest).days) if oldest else None

        s30 = sales_30d.get(pid, 0)
        s90 = sales_90d.get(pid, 0)
        last_sale = last_sales.get(pid)

        # Turnover rate (annualized from 90-day sales)
        turnover = (s90 / max(qty, 1)) * (365 / 90) if qty > 0 else 0

        # ABC classification based on turnover. Stock younger than the grace
        # window that has not sold yet has not had its chance: no verdict
        # (NEW), never "Slow Mover - consider discount/return" on the morning
        # it arrived (audit F54).
        if not _had_the_window(oldest, now, _AGING_GRACE_DAYS) and s90 == 0:
            cls = "NEW"
        elif turnover >= 4:
            cls = "A"
        elif turnover >= 1.5:
            cls = "B"
        else:
            cls = "C"

        # Age category
        if days_in_stock is None:
            age_cat = "180+"
        elif days_in_stock <= 30:
            age_cat = "0-30"
        elif days_in_stock <= 60:
            age_cat = "31-60"
        elif days_in_stock <= 90:
            age_cat = "61-90"
        elif days_in_stock <= 180:
            age_cat = "91-180"
        else:
            age_cat = "180+"

        mrp = product.get("mrp", 0) or 0
        value = qty * mrp

        if classification and cls != classification:
            continue
        if min_days is not None and days_in_stock is not None and days_in_stock < min_days:
            continue

        products.append(
            {
                "id": pid,
                "sku": product.get("sku", ""),
                "name": product.get("name", product.get("model", "")),
                "brand": product.get("brand", ""),
                "category": product.get("category", ""),
                "quantity": qty,
                "value": round(value, 2),
                "daysInStock": days_in_stock,
                "lastSaleDate": (
                    last_sale.isoformat()
                    if isinstance(last_sale, datetime)
                    else last_sale
                ),
                "salesLast30Days": s30,
                "salesLast90Days": s90,
                "turnoverRate": round(turnover, 1),
                "classification": cls,
                "ageCategory": age_cat,
            }
        )

    def _age(p):  # unknown age = oldest
        return float("inf") if p["daysInStock"] is None else p["daysInStock"]

    # Sort: Slow movers first (C, then B, then A, then NEW), then by days in stock desc
    cls_order = {"C": 0, "B": 1, "A": 2, "NEW": 3}
    products.sort(key=lambda p: (cls_order.get(p["classification"], 1), -_age(p)))

    # Summary stats
    total = len(products)
    class_a = sum(1 for p in products if p["classification"] == "A")
    class_b = sum(1 for p in products if p["classification"] == "B")
    class_c = sum(1 for p in products if p["classification"] == "C")
    slow_value = sum(p["value"] for p in products if p["classification"] == "C")
    known = [p["daysInStock"] for p in products if p["daysInStock"] is not None]
    avg_age = sum(known) / max(len(known), 1)

    return {
        "products": products,
        "summary": {
            "total": total,
            "classA": class_a,
            "classB": class_b,
            "classC": class_c,
            "slowMovingValue": round(slow_value, 2),
            "averageAge": round(avg_age, 1),
            "oldStockCount": sum(1 for p in products if _age(p) > 90),
        },
    }
