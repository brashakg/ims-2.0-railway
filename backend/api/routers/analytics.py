"""
Enterprise Analytics Router
Provides comprehensive analytics and business intelligence endpoints
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)
from .auth import get_current_user, require_roles
from ..utils.dates import to_date_str
from ..dependencies import (
    get_order_repository,
    get_stock_repository,
    get_customer_repository,
    get_task_repository,
    get_product_repository,
    validate_store_access,
    user_store_scope,
    get_store_repository,
)

router = APIRouter(prefix="", tags=["Analytics"])

# Roles allowed to read enterprise analytics / KPI dashboards. These surface
# cross-store revenue, margins, cash-register and customer intelligence -- the
# same management tier that reports.py gates its financial reports behind
# (_REPORT_FINANCE_ROLES). Previously EVERY authenticated user (down to
# workshop staff) could read enterprise KPIs. SUPERADMIN auto-passes inside
# require_roles. Additive gate only; per-store_id scoping is a separate item.
_ANALYTICS_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")


# ============================================================================
# Order field normalizer (DB stores camelCase, analytics expects snake_case)
# ============================================================================


def _norm_order(o: dict) -> dict:
    """Normalize order fields from camelCase to snake_case for analytics"""
    raw_date = o.get("created_at") or o.get("createdAt", "")
    # Convert datetime to string if needed
    if isinstance(raw_date, datetime):
        raw_date = raw_date.isoformat()
    return {
        "store_id": o.get("store_id") or o.get("storeId", ""),
        "customer_id": o.get("customer_id") or o.get("customerId", ""),
        "created_at": raw_date,
        # Prefer the post-discount billable total. orders.py stamps
        # `grand_total` (snake); fall through camelCase + legacy total_amount.
        # `subtotal` is the PRE-discount gross (over-counts revenue) so it is
        # the LAST resort only -- the previous order put it ahead of the real
        # grand_total and silently inflated revenue for discounted orders.
        "total_amount": _safe_float(
            o.get("grand_total")
            or o.get("grandTotal")
            or o.get("total_amount")
            or o.get("totalAmount")
            or o.get("subtotal")
        ),
        "status": o.get("status") or o.get("orderStatus", ""),
        "items": [
            {
                "product_id": it.get("product_id") or it.get("productId", ""),
                "product_name": it.get("product_name")
                or it.get("productName", it.get("name", "Unknown")),
                "quantity": _safe_int(it.get("quantity")),
                "unit_price": _safe_float(it.get("unit_price") or it.get("unitPrice")),
                "total_amount": _safe_float(
                    it.get("total_amount")
                    or it.get("finalPrice")
                    or it.get("unitPrice")
                ),
                "cost_price": _safe_float(it.get("cost_price") or it.get("costPrice")),
                "sku": it.get("sku", ""),
            }
            for it in (o.get("items") or [])
        ],
    }


def _norm_orders(orders: list) -> list:
    """Normalize a list of orders"""
    return [_norm_order(o) for o in (orders or [])]


def _filter_orders_by_date(orders: list, start: datetime, end: datetime) -> list:
    """Safely filter orders by date range"""
    result = []
    for o in orders:
        dt = _safe_parse_date(o.get("created_at", ""))
        if dt and start <= dt <= end:
            result.append(o)
    return result


def _fetch_orders_in_window(
    order_repo,
    *,
    store_id: Optional[str],
    start: datetime,
    end: datetime,
) -> list:
    """Date-bounded order fetch that pushes the window into Mongo and is NOT
    capped at 500 (find_by_store) / 100 (find_many default).

    `order_repo.find_by_store` filters with an `.isoformat()` STRING against a
    BSON Date (never matches) AND hard-caps at 500 rows -- so store totals
    silently dropped every order past the 500th and, for any non-string
    created_at, returned 0. This queries with real datetime objects and
    `limit=0` (no cap), then normalizes. `store_id=None` => all stores.

    Falls back to the repo's find_many(filter) when the underlying collection
    isn't reachable (mock/stub mode) so it degrades the same as before.
    """
    if order_repo is None:
        return []

    flt: dict = {
        # Real datetime objects -- a BSON Date range that actually matches.
        "created_at": {"$gte": start, "$lte": end},
    }
    if store_id:
        flt["store_id"] = store_id
    try:
        # limit=0 -> no cap (see BaseRepository.find_many: falsy limit skips
        # the .limit() call). A store/period total must not be truncated.
        return _norm_orders(order_repo.find_many(flt, limit=0))
    except Exception:
        # Defensive: never 500 a dashboard on a query hiccup.
        try:
            return _norm_orders(order_repo.find_many(flt, limit=0))
        except Exception:
            return []


# ============================================================================
# Inventory enrichment (product master join)
# ============================================================================
# Serialized stock rows (the `stock` / stock_units collection) carry only
# product_id / store_id / barcode / quantity / status — NOT category, sku,
# name, or cost_price. Those live on the `products` master. /reports already
# joins the master (which is why /reports/inventory/valuation values a unit at
# its cost_price and labels it FRAME); inventory-intelligence read them straight
# off the stock doc and therefore reported value 0.0 and blank sku/name.
# This builds a product_id -> {sku, name, category, cost_price, mrp} map in one
# pass so each stock row can be enriched the same way /reports does.


def _build_product_master_map(stock_rows: list) -> dict:
    """Return {product_id: {sku, name, category, cost_price, mrp}} for every
    product_id present in `stock_rows`, sourced from the product master.

    Falls back to catalog_products for catalog-only products (the convergence
    spine) using the same helper the inventory/orders paths use, so a Cartier
    that only exists in the catalog still resolves. Fail-soft: a product that
    cannot be resolved simply yields an empty dict for that id.

    PERF: batched. This used to be a per-distinct-pid find_by_id plus a
    per-miss 3-way $or catalog find_one (the same N+1 as the reports-side
    _stock_category_map). Now it is ONE products `$in` query for every id,
    then ONE catalog_products query for the ids the master does not know at
    all. Resolution semantics are unchanged (master hit never falls through
    to the catalog). Repos whose collection cannot batch (e.g. test fakes
    that only implement find_by_id) fall back to the original per-id loop."""
    pids = {
        str(r.get("product_id"))
        for r in (stock_rows or [])
        if r.get("product_id") is not None
    }
    out: dict = {}
    if not pids:
        return out

    product_repo = get_product_repository()
    catalog_resolver = None
    catalog_coll_getter = None
    try:
        from .orders import _get_catalog_collection as catalog_coll_getter
        from .orders import _resolve_catalog_product_doc as catalog_resolver
    except Exception:  # noqa: BLE001
        catalog_resolver = None
        catalog_coll_getter = None

    ordered_pids = sorted(pids)
    resolved: dict = {}  # pid -> flat product dict (master OR mapped catalog)

    # -- Pass 1: product master, ONE $in query ------------------------------
    if product_repo is not None:
        batched = False
        coll = getattr(product_repo, "collection", None)
        if coll is not None:
            try:
                master_hits: dict = {}
                for doc in coll.find(
                    {"product_id": {"$in": ordered_pids}},
                    {
                        "product_id": 1,
                        "sku": 1,
                        "name": 1,
                        "model": 1,
                        "category": 1,
                        "cost_price": 1,
                        "mrp": 1,
                    },
                ):
                    pid = str(doc.get("product_id"))
                    # First doc in natural order wins == find_one semantics.
                    if pid not in master_hits:
                        master_hits[pid] = doc
                resolved.update(master_hits)
                batched = True
            except Exception:  # noqa: BLE001
                batched = False
        if not batched:
            # Per-id fallback (mock/fake collections that cannot batch).
            for pid in ordered_pids:
                try:
                    product = product_repo.find_by_id(pid)
                except Exception:  # noqa: BLE001
                    product = None
                if product:
                    resolved[pid] = product

    # -- Pass 2: catalog fallback for ids the master has NO doc for ---------
    # (_resolve_catalog_product_doc returns None for a falsy pid, so blank
    # ids are excluded up front exactly as before.) Each catalog hit is
    # flattened to the same fields _resolve_catalog_product_doc surfaces
    # (title->name, nested pricing -> flat cost_price/mrp).
    misses = [pid for pid in ordered_pids if pid and pid not in resolved]
    if misses and catalog_resolver is not None:
        batched = False
        try:
            coll = catalog_coll_getter() if catalog_coll_getter is not None else None
        except Exception:  # noqa: BLE001
            coll = None
        if coll is not None:
            try:
                catalog_hits: dict = {}
                miss_set = set(misses)
                for doc in coll.find(
                    {
                        "$or": [
                            {"id": {"$in": misses}},
                            {"sku": {"$in": misses}},
                            {"_id": {"$in": misses}},
                        ]
                    },
                    {
                        "id": 1,
                        "sku": 1,
                        "title": 1,
                        "name": 1,
                        "category": 1,
                        "pricing": 1,
                    },
                ):
                    pricing = doc.get("pricing") or {}
                    flat = {
                        "sku": doc.get("sku"),
                        "name": doc.get("title") or doc.get("name"),
                        "model": doc.get("title") or doc.get("name"),
                        "category": doc.get("category"),
                        "cost_price": pricing.get("cost_price"),
                        "mrp": pricing.get("mrp"),
                    }
                    # First doc in natural order matching the pid by ANY of
                    # the three keys wins == the old per-pid $or find_one.
                    for key in (doc.get("id"), doc.get("sku"), doc.get("_id")):
                        if key in miss_set and key not in catalog_hits:
                            catalog_hits[key] = flat
                resolved.update(catalog_hits)
                batched = True
            except Exception:  # noqa: BLE001
                batched = False
        if not batched:
            for pid in misses:
                try:
                    product = catalog_resolver(pid)
                except Exception:  # noqa: BLE001
                    product = None
                if product:
                    resolved[pid] = product

    for pid in pids:
        product = resolved.get(pid)
        if not product:
            continue
        out[pid] = {
            "sku": product.get("sku") or "",
            "name": product.get("name") or product.get("model") or "",
            "category": product.get("category") or "Other",
            "cost_price": _safe_float(product.get("cost_price")),
            "mrp": _safe_float(product.get("mrp")),
        }
    return out


def _stock_unit_value(row: dict, master: dict) -> float:
    """Per-row inventory value = quantity * cost_price. cost_price is resolved
    from the product master (authoritative, mirrors /reports). Falls back to a
    cost_price stamped on the stock row itself for any legacy doc that carries
    one, then to unit_price, so the value is never silently 0 when a cost is
    known somewhere."""
    qty = _safe_int(row.get("quantity"))
    pid = str(row.get("product_id")) if row.get("product_id") is not None else ""
    cost = _safe_float((master.get(pid) or {}).get("cost_price"))
    if cost <= 0:
        cost = _safe_float(row.get("cost_price")) or _safe_float(row.get("unit_price"))
    return qty * cost


# ============================================================================
# Types
# ============================================================================


class KPIData:
    """Key Performance Indicator Data"""

    pass


# ============================================================================
# Helper Functions
# ============================================================================


def get_date_range(period: str) -> tuple[datetime, datetime]:
    """Get start and end date for a given period"""
    end_date = datetime.now()

    if period == "today":
        start_date = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = end_date - timedelta(days=7)
    elif period == "month":
        start_date = end_date - timedelta(days=30)
    elif period == "quarter":
        start_date = end_date - timedelta(days=90)
    elif period == "year":
        start_date = end_date - timedelta(days=365)
    else:
        start_date = end_date - timedelta(days=30)  # Default to month

    return start_date, end_date


def _safe_parse_date(date_val: Any) -> Optional[datetime]:
    """Safely parse a date value — handles strings, datetime objects, and None"""
    if date_val is None:
        return None
    # Already a datetime
    if isinstance(date_val, datetime):
        return date_val.replace(tzinfo=None)  # Strip timezone for comparison
    if not isinstance(date_val, str):
        return None
    try:
        # Remove timezone info for naive comparison
        clean = date_val.replace("Z", "").replace("+00:00", "")
        return datetime.fromisoformat(clean)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> int:
    """Safely convert to int"""
    try:
        return int(float(val)) if val else 0
    except (ValueError, TypeError):
        return 0


def _safe_float(val: Any) -> float:
    """Safely convert to float"""
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0


# Statuses that do NOT represent real revenue.  CANCELLED orders were never
# fulfilled; DRAFT orders were never confirmed.  Including them inflated the
# dashboard revenue/order-count metrics (RPT-1).
_EXCLUDED_ORDER_STATUSES = frozenset({"CANCELLED", "DRAFT"})


def _is_billable(order: dict) -> bool:
    """Return True when the order represents real revenue (not cancelled/draft)."""
    status = str(order.get("status") or "").upper()
    return status not in _EXCLUDED_ORDER_STATUSES


def calculate_metrics_for_period(
    order_repo, store_id: Optional[str], start_date: datetime, end_date: datetime
) -> Dict[str, Any]:
    """Calculate all metrics for a given period.

    [RPT-1] Excludes CANCELLED and DRAFT orders from revenue and order counts.
    [RPT-2] Uses _fetch_orders_in_window (Mongo-side datetime filter, limit=0)
            instead of find_by_store (in-memory string filter, 500-row cap).
    """

    if order_repo is None:
        return {
            "total_revenue": 0.0,
            "total_orders": 0,
            "avg_order_value": 0.0,
            "revenue_change": 0.0,
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
        }

    # Date-bounded, uncapped fetch pushed into Mongo (replaces find_by_store
    # which string-filtered a BSON Date and hard-capped at 500 rows).
    prev_start = start_date - (end_date - start_date)
    orders = [
        o
        for o in _fetch_orders_in_window(
            order_repo, store_id=store_id, start=start_date, end=end_date
        )
        if _is_billable(o)
    ]
    prev_orders = [
        o
        for o in _fetch_orders_in_window(
            order_repo, store_id=store_id, start=prev_start, end=start_date
        )
        if _is_billable(o)
    ]

    # Calculate metrics
    total_revenue = sum(_safe_float(o.get("total_amount")) for o in orders)
    total_orders = len(orders)
    avg_order_value = total_revenue / total_orders if total_orders > 0 else 0

    prev_revenue = sum(_safe_float(o.get("total_amount")) for o in prev_orders)

    revenue_change = (
        ((total_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else 0
    )

    return {
        "total_revenue": float(total_revenue),
        "total_orders": total_orders,
        "avg_order_value": float(avg_order_value),
        "revenue_change": float(revenue_change),
        "period_start": start_date.isoformat(),
        "period_end": end_date.isoformat(),
    }


# ============================================================================
# Endpoints
# ============================================================================


@router.get("")
@router.get("/")
async def get_analytics_root():
    """Root endpoint for analytics dashboard summary"""
    return {
        "module": "analytics",
        "status": "active",
        "message": "analytics summary endpoint ready",
    }


@router.get("/dashboard-summary")
async def get_dashboard_summary(
    current_user: dict = Depends(require_roles(*_ANALYTICS_ROLES)),
    period: str = Query("month", pattern="^(today|week|month|quarter|year)$"),
    store_id: Optional[str] = Query(None),
):
    """
    Get comprehensive dashboard summary with all KPI data
    Returns: Revenue, Orders, Margin, Inventory, Customer metrics
    """
    try:
        start_date, end_date = get_date_range(period)
        # BUG-062: 403 on cross-store access; validate_store_access returns the
        # caller's active store when store_id is omitted (admins/area-mgrs pass).
        store_id = (
            validate_store_access(store_id, current_user)
            or current_user.get("active_store_id")
        )
        if not store_id:
            raise HTTPException(
                status_code=422,
                detail="store could not be resolved; pass store_id or ensure your token carries an active_store_id",
            )

        order_repo = get_order_repository()
        stock_repo = get_stock_repository()
        customer_repo = get_customer_repository()

        metrics = calculate_metrics_for_period(
            order_repo, store_id, start_date, end_date
        )

        # Get inventory metrics
        inventory = (
            stock_repo.find_many({"store_id": store_id}, limit=0)
            if stock_repo is not None
            else []
        )

        total_inventory_value = sum(
            _safe_int(i.get("quantity")) * _safe_float(i.get("unit_price"))
            for i in inventory
        )
        low_stock_items = len(
            [
                i
                for i in inventory
                if _safe_int(i.get("quantity")) <= _safe_int(i.get("reorder_point"))
            ]
        )
        out_of_stock = len([i for i in inventory if _safe_int(i.get("quantity")) == 0])

        # Get customer metrics
        customers = (
            customer_repo.find_many(
                {
                    "$or": [
                        {"preferred_store_id": store_id},
                        {"home_store_id": store_id},
                        {"primary_store_id": store_id},
                        {"store_id": store_id},
                    ]
                },
                limit=0,
            )
            if customer_repo is not None
            else []
        )

        new_customers = len(
            [
                c
                for c in customers
                if to_date_str(c.get("created_at")) >= start_date.date().isoformat()
            ]
        )

        return {
            "period": period,
            "timestamp": datetime.now().isoformat(),
            # Revenue metrics
            "total_revenue": metrics["total_revenue"],
            "revenue_change": metrics["revenue_change"],
            "avg_order_value": metrics["avg_order_value"],
            "total_orders": metrics["total_orders"],
            # Gross margin needs per-line-item COGS (order items don't carry
            # cost yet). Return null rather than a fabricated constant — the
            # frontend renders "—" for null.
            "gross_margin_percent": None,
            # margin_target: read from settings; null until a target is
            # configured (never emit a hardcoded 42 as a real KPI).
            "margin_target": None,
            # Inventory metrics
            "inventory_value": float(total_inventory_value),
            "low_stock_items": low_stock_items,
            "out_of_stock_items": out_of_stock,
            "inventory_turnover_ratio": None,  # needs COGS + avg inventory
            # Customer metrics
            "total_customers": len(customers),
            "new_customers": new_customers,
            "customer_acquisition_rate": new_customers,
            # Optical-specific metrics
            "prescription_renewals_pending": None,  # not computed yet (no fabricated value)
            # Performance indicators
            "stores_count": 1,
        }

    except HTTPException:
        # Propagate validate_store_access's 403 (cross-store) — don't mask it as 500.
        raise
    except Exception:
        logger.exception("Dashboard summary calculation failed")
        raise HTTPException(
            status_code=500,
            detail="Could not calculate the dashboard summary - try again or contact support",
        )


@router.get("/revenue-trends")
async def get_revenue_trends(
    current_user: dict = Depends(require_roles(*_ANALYTICS_ROLES)),
    period: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    days: int = Query(30, ge=7, le=365),
    store_id: Optional[str] = Query(None),
):
    """
    Get revenue trend data for charting
    Returns: Time-series revenue data with YoY comparison
    """
    try:
        # BUG-062: 403 on cross-store access; validate_store_access returns the
        # caller's active store when store_id is omitted (admins/area-mgrs pass).
        store_id = (
            validate_store_access(store_id, current_user)
            or current_user.get("active_store_id")
        )
        if not store_id:
            raise HTTPException(
                status_code=422,
                detail="store could not be resolved; pass store_id or ensure your token carries an active_store_id",
            )
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        order_repo = get_order_repository()
        if order_repo is None:
            return {"period": period, "days": days, "data": []}

        # Use the unbounded date-pushed helper (not the 500-row-capped
        # find_by_store with a string-vs-BSON-Date mismatch).
        prev_start = start_date - timedelta(days=days)
        prev_end = start_date

        current_period = [
            o
            for o in _fetch_orders_in_window(
                order_repo, store_id=store_id, start=start_date, end=end_date
            )
            if _is_billable(o)
        ]

        # Get previous period for YoY comparison
        previous_period = [
            o
            for o in _fetch_orders_in_window(
                order_repo, store_id=store_id, start=prev_start, end=prev_end
            )
            if _is_billable(o)
        ]

        # Group by period
        def group_by_period(orders: List[Dict[str, Any]], period_type: str):
            grouped = {}
            for order in orders:
                created_at = _safe_parse_date(order.get("created_at"))
                if not created_at:
                    continue
                if period_type == "daily":
                    key = created_at.date().isoformat()
                elif period_type == "weekly":
                    week_start = created_at - timedelta(days=created_at.weekday())
                    key = week_start.date().isoformat()
                else:  # monthly
                    key = created_at.strftime("%Y-%m")

                if key not in grouped:
                    grouped[key] = 0
                grouped[key] += _safe_float(order.get("total_amount"))

            return grouped

        current_grouped = group_by_period(current_period, period)
        previous_grouped = group_by_period(previous_period, period)

        # Build timeline
        timeline_data = []
        current_date = start_date

        while current_date <= end_date:
            if period == "daily":
                key = current_date.date().isoformat()
                next_date = current_date + timedelta(days=1)
            elif period == "weekly":
                week_start = current_date - timedelta(days=current_date.weekday())
                key = week_start.date().isoformat()
                next_date = current_date + timedelta(days=7)
            else:  # monthly
                key = current_date.strftime("%Y-%m")
                if current_date.month == 12:
                    next_date = current_date.replace(
                        year=current_date.year + 1, month=1
                    )
                else:
                    next_date = current_date.replace(month=current_date.month + 1)

            timeline_data.append(
                {
                    "label": key,
                    "value": float(current_grouped.get(key, 0)),
                    "previous_value": float(previous_grouped.get(key, 0)),
                }
            )

            current_date = next_date

        return {
            "period": period,
            "days": days,
            "current_period_start": start_date.isoformat(),
            "current_period_end": end_date.isoformat(),
            "comparison_period_start": prev_start.isoformat(),
            "comparison_period_end": prev_end.isoformat(),
            "data": timeline_data,
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Revenue trends fetch failed")
        raise HTTPException(
            status_code=500,
            detail="Could not load revenue trends - try again or contact support",
        )


@router.get("/store-performance")
async def get_store_performance(
    current_user: dict = Depends(require_roles(*_ANALYTICS_ROLES)),
    period: str = Query("month", pattern="^(today|week|month|quarter|year)$"),
):
    """
    Get performance metrics for all stores (for multi-location admins)
    Returns: Store-wise revenue, orders, margins, staff, inventory metrics
    """
    try:
        start_date, end_date = get_date_range(period)

        order_repo = get_order_repository()
        stock_repo = get_stock_repository()
        store_repo = get_store_repository()

        # Date-bounded, all-store fetch pushed into Mongo (no 100/500 cap).
        # The previous find_many({}) capped at 100 arbitrary recent rows AND
        # carried no date filter, so store totals dropped most orders.
        #
        # [RPT-1] Exclude CANCELLED / DRAFT here at the source so every
        # downstream loop (store grouping, revenue, order count, AOV, prev
        # comparison) sees only billable revenue. The sibling endpoints
        # (dashboard-summary / revenue-trends / enterprise-kpis) already do
        # this; store-performance previously summed every status and reported
        # cancelled grandTotals as revenue (e.g. 8 CANCELLED summing 7570).
        prev_start = start_date - (end_date - start_date)
        window_orders = [
            o
            for o in _fetch_orders_in_window(
                order_repo, store_id=None, start=start_date, end=end_date
            )
            if _is_billable(o)
        ]
        prev_window_orders = [
            o
            for o in _fetch_orders_in_window(
                order_repo, store_id=None, start=prev_start, end=start_date
            )
            if _is_billable(o)
        ]

        # RPT-6: build a store_id -> real store_name lookup so the response
        # never emits synthetic "Store store-001" labels.
        store_name_cache: dict = {}
        if store_repo is not None:
            try:
                all_stores = store_repo.find_many({}, limit=0)
                for s in all_stores:
                    sid = s.get("store_id") or s.get("_id") or s.get("id")
                    sname = (
                        s.get("name")
                        or s.get("store_name")
                        or s.get("display_name")
                    )
                    if sid and sname:
                        store_name_cache[str(sid)] = sname
            except Exception:
                pass  # fail-soft; names fall back below

        def _store_name(sid: str) -> str:
            return store_name_cache.get(sid) or sid

        # Store-scope (BUG: cross-store leak). _ANALYTICS_ROLES includes the
        # single-store STORE_MANAGER + ACCOUNTANT; without this they received
        # EVERY store's revenue/orders/stock_value. Cross-store roles (ADMIN/
        # AREA_MANAGER/SUPERADMIN) keep the all-store view; single-store roles
        # see only their own store(s). Mirrors the sibling analytics endpoints'
        # validate_store_access scoping.
        is_cross, allowed_stores = user_store_scope(current_user)

        # Group by store (union of both windows so a store with only prior
        # sales still appears).
        stores = {}
        for order in list(window_orders) + list(prev_window_orders):
            store_id = order.get("store_id") or "store-001"
            if not is_cross and store_id not in allowed_stores:
                continue  # never group a store the caller may not see
            if store_id not in stores:
                stores[store_id] = {
                    "store_id": store_id,
                    "store_name": _store_name(store_id),
                }

        store_metrics = []

        for store_id, store_info in stores.items():
            # Orders for this store in the current window.
            orders = [o for o in window_orders if o.get("store_id") == store_id]

            revenue = sum(_safe_float(o.get("total_amount")) for o in orders)
            order_count = len(orders)
            avg_order_value = revenue / order_count if order_count > 0 else 0

            # Previous period comparison (same-length window before start).
            prev_orders = [
                o for o in prev_window_orders if o.get("store_id") == store_id
            ]

            prev_revenue = sum(_safe_float(o.get("total_amount")) for o in prev_orders)
            revenue_change = (
                ((revenue - prev_revenue) / prev_revenue * 100)
                if prev_revenue > 0
                else 0
            )

            # Inventory metrics
            inventory = (
                stock_repo.find_many({"store_id": store_id}, limit=0)
                if stock_repo is not None
                else []
            )

            stock_value = sum(
                _safe_int(i.get("quantity")) * _safe_float(i.get("unit_price"))
                for i in inventory
            )

            # Metrics calculation. margin_percent / revenue_per_sqft /
            # staff_count are null rather than fabricated — margin needs
            # per-item COGS, sqft isn't stored, and staff headcount isn't
            # sourced here. (Were hardcoded 40.5 / revenue/1000 / 10.)
            store_metrics.append(
                {
                    "store_id": store_id,
                    "store_name": store_info.get("store_name", store_id),
                    "revenue": float(revenue),
                    "orders": order_count,
                    "avg_order_value": float(avg_order_value),
                    "margin_percent": None,
                    "stock_value": float(stock_value),
                    "staff_count": None,
                    "revenue_per_sqft": None,
                    "revenue_trend": float(revenue_change),
                }
            )

        return {
            "period": period,
            "timestamp": datetime.now().isoformat(),
            "stores": store_metrics,
            "summary": {
                "total_stores": len(stores),
                "total_revenue": sum(s["revenue"] for s in store_metrics),
                "avg_revenue_per_store": (
                    sum(s["revenue"] for s in store_metrics) / len(stores)
                    if stores
                    else 0
                ),
                "avg_margin": None,  # needs per-item COGS (was fabricated 40.5)
            },
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Store performance fetch failed")
        raise HTTPException(
            status_code=500,
            detail="Could not load store performance - try again or contact support",
        )


@router.get("/inventory-intelligence")
async def get_inventory_intelligence(
    current_user: dict = Depends(require_roles(*_ANALYTICS_ROLES)),
    store_id: Optional[str] = Query(None),
):
    """
    Get inventory intelligence: low stock, dead stock, fast-moving items
    """
    try:
        # BUG-062: 403 on cross-store access; validate_store_access returns the
        # caller's active store when store_id is omitted (admins/area-mgrs pass).
        store_id = (
            validate_store_access(store_id, current_user)
            or current_user.get("active_store_id")
        )
        if not store_id:
            raise HTTPException(
                status_code=422,
                detail="store could not be resolved; pass store_id or ensure your token carries an active_store_id",
            )

        stock_repo = get_stock_repository()
        inventory = (
            stock_repo.find_many({"store_id": store_id}, limit=0)
            if stock_repo is not None
            else []
        )

        # [RPT-2] sku / name / value live on the product master, not the stock
        # doc. Build the join once so every item + every total below reads real
        # values instead of "" / 0.0. Mirrors /reports/inventory/valuation.
        master = _build_product_master_map(inventory)

        def _sku(row: dict) -> str:
            pid = str(row.get("product_id")) if row.get("product_id") is not None else ""
            return (master.get(pid) or {}).get("sku") or row.get("sku") or ""

        def _name(row: dict) -> str:
            pid = str(row.get("product_id")) if row.get("product_id") is not None else ""
            return (master.get(pid) or {}).get("name") or row.get("name") or ""

        def _value(row: dict) -> float:
            return _stock_unit_value(row, master)

        # Categorize items
        # Low stock: quantity at or below reorder point
        low_stock = [
            i
            for i in inventory
            if _safe_int(i.get("quantity")) <= _safe_int(i.get("reorder_point"))
            and _safe_int(i.get("quantity")) > 0
        ]

        # Dead stock: items with zero recent sales (last_sold_at > 90 days ago or never)
        # OR items with quantity but zero sales velocity
        from datetime import datetime, timedelta

        ninety_days_ago = datetime.utcnow() - timedelta(days=90)
        dead_stock = []
        for i in inventory:
            qty = _safe_int(i.get("quantity"))
            if qty <= 0:
                continue  # No stock = nothing to worry about
            last_sold = i.get("last_sold_at") or i.get("last_sale_date")
            sales_velocity = _safe_float(i.get("sales_velocity", 0))
            # Dead if: no last_sold date, or last_sold > 90 days, or zero velocity with stock
            if last_sold is None:
                dead_stock.append(i)
            elif isinstance(last_sold, str):
                try:
                    last_sold_dt = datetime.fromisoformat(
                        last_sold.replace("Z", "+00:00")
                    )
                    if last_sold_dt < ninety_days_ago:
                        dead_stock.append(i)
                except (ValueError, TypeError):
                    if sales_velocity == 0 and qty > 0:
                        dead_stock.append(i)
            elif sales_velocity == 0 and qty > 0:
                dead_stock.append(i)

        # Fast-moving: items selling faster than reorder point restocks them
        # High velocity = sales_velocity > 0, or quantity dropping fast relative to reorder point
        fast_moving = [
            i
            for i in inventory
            if _safe_float(i.get("sales_velocity", 0)) > 0
            and _safe_int(i.get("quantity")) <= _safe_int(i.get("reorder_point")) * 1.5
        ]

        return {
            "low_stock": {
                "count": len(low_stock),
                "items": [
                    {
                        "sku": _sku(i),
                        "name": _name(i),
                        "quantity": i.get("quantity", 0),
                        "reorder_point": i.get("reorder_point", 0),
                    }
                    for i in low_stock[:10]
                ],
                "total_value": sum(_value(i) for i in low_stock),
            },
            "dead_stock": {
                "count": len(dead_stock),
                "items": [
                    {
                        "sku": _sku(i),
                        "name": _name(i),
                        "quantity": i.get("quantity", 0),
                        "value": _value(i),
                    }
                    for i in dead_stock[:10]
                ],
                "total_value": sum(_value(i) for i in dead_stock),
            },
            "fast_moving": {
                "count": len(fast_moving),
                "items": [
                    {
                        "sku": _sku(i),
                        "name": _name(i),
                        "quantity": i.get("quantity", 0),
                        "velocity": "high",
                    }
                    for i in fast_moving[:10]
                ],
            },
            "total_inventory": {
                "items": len(inventory),
                "value": sum(_value(i) for i in inventory),
            },
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Inventory intelligence fetch failed")
        raise HTTPException(
            status_code=500,
            detail="Could not load inventory intelligence - try again or contact support",
        )


@router.get("/customer-insights")
async def get_customer_insights(
    current_user: dict = Depends(require_roles(*_ANALYTICS_ROLES)),
    period: str = Query("month", pattern="^(today|week|month|quarter|year)$"),
    store_id: Optional[str] = Query(None),
):
    """
    Get customer insights: composition, top customers, lifetime value
    """
    try:
        start_date, end_date = get_date_range(period)
        # BUG-062: 403 on cross-store access; validate_store_access returns the
        # caller's active store when store_id is omitted (admins/area-mgrs pass).
        store_id = (
            validate_store_access(store_id, current_user)
            or current_user.get("active_store_id")
        )
        if not store_id:
            raise HTTPException(
                status_code=422,
                detail="store could not be resolved; pass store_id or ensure your token carries an active_store_id",
            )

        customer_repo = get_customer_repository()
        order_repo = get_order_repository()

        # Total customers
        all_customers = (
            customer_repo.find_many(
                {
                    "$or": [
                        {"preferred_store_id": store_id},
                        {"home_store_id": store_id},
                        {"primary_store_id": store_id},
                        {"store_id": store_id},
                    ]
                },
                limit=0,
            )
            if customer_repo is not None
            else []
        )

        # New customers this period
        new_customers = [
            c
            for c in all_customers
            if to_date_str(c.get("created_at")) >= start_date.date().isoformat()
        ]
        returning_customers = [
            c
            for c in all_customers
            if to_date_str(c.get("created_at")) < start_date.date().isoformat()
        ]

        # Top customers by spend — use the unbounded date-pushed helper
        # (not the 500-row-capped find_by_store with BSON-Date mismatch).
        #
        # [RPT-1] Exclude CANCELLED / DRAFT so a cancelled order never inflates
        # a customer's spend / order count (and therefore the top-customer
        # ranking + avg_customer_lifetime_value). Mirrors the sibling
        # revenue endpoints; matches /reports which already $nin's these.
        orders = (
            [
                o
                for o in _fetch_orders_in_window(
                    order_repo, store_id=store_id, start=start_date, end=end_date
                )
                if _is_billable(o)
            ]
            if order_repo is not None
            else []
        )

        customer_spend = {}
        for order in orders:
            customer_id = order.get("customer_id")
            if customer_id:
                if customer_id not in customer_spend:
                    customer_spend[customer_id] = {"spend": 0, "orders": 0}
                customer_spend[customer_id]["spend"] += _safe_float(
                    order.get("total_amount")
                )
                customer_spend[customer_id]["orders"] += 1

        top_customers_raw = sorted(
            [{"customer_id": cid, **data} for cid, data in customer_spend.items()],
            key=lambda x: x["spend"],
            reverse=True,
        )[:10]

        # RPT-5: join customer name so top-customers never shows "Unknown".
        # Build a set of IDs then do one find_many with an $in filter to avoid
        # N individual round-trips. Fall back to "Unknown" only when the record
        # genuinely can't be found (deleted customer, test data, etc.).
        cid_set = [c["customer_id"] for c in top_customers_raw]
        name_map: dict = {}
        if customer_repo is not None and cid_set:
            try:
                cust_docs = customer_repo.find_many(
                    {"customer_id": {"$in": cid_set}},
                    limit=len(cid_set) + 5,
                )
                for doc in cust_docs:
                    cid = doc.get("customer_id")
                    raw_name = (
                        doc.get("name")
                        or doc.get("full_name")
                        or (
                            f"{doc.get('first_name', '')} {doc.get('last_name', '')}".strip()
                            or None
                        )
                    )
                    if cid and raw_name:
                        name_map[cid] = raw_name
            except Exception:
                pass  # fail-soft; names default to customer_id

        top_customers = [
            {
                **c,
                "name": name_map.get(c["customer_id"]) or c["customer_id"],
            }
            for c in top_customers_raw
        ]

        return {
            "period": period,
            "total_customers": len(all_customers),
            "new_customers": len(new_customers),
            "returning_customers": len(returning_customers),
            "retention_rate": (
                (len(returning_customers) / len(all_customers) * 100)
                if all_customers
                else 0
            ),
            "acquisition_rate": len(new_customers),
            "top_customers": top_customers,
            "avg_customer_lifetime_value": (
                sum(c["spend"] for c in top_customers) / len(top_customers)
                if top_customers
                else 0
            ),
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Customer insights fetch failed")
        raise HTTPException(
            status_code=500,
            detail="Could not load customer insights - try again or contact support",
        )


# ============================================================================
# ENTERPRISE PHASE 6: COMPREHENSIVE KPI ENDPOINT
# ============================================================================


@router.get("/enterprise-kpis")
async def get_enterprise_kpis(
    current_user: dict = Depends(require_roles(*_ANALYTICS_ROLES)),
    period: str = Query("today", pattern="^(today|week|month|year)$"),
    store_id: Optional[str] = Query(None),
):
    """
    Comprehensive enterprise KPI dashboard with SAP/Power BI style metrics
    Returns: Revenue, margins, footfall, inventory, products, cash register, store comparison
    """
    try:
        # BUG-062: 403 on cross-store access; validate_store_access returns the
        # caller's active store when store_id is omitted (admins/area-mgrs pass).
        store_id = (
            validate_store_access(store_id, current_user)
            or current_user.get("active_store_id")
        )
        if not store_id:
            raise HTTPException(
                status_code=422,
                detail="store could not be resolved; pass store_id or ensure your token carries an active_store_id",
            )
        start_date, end_date = get_date_range(period)

        # Get repositories
        order_repo = get_order_repository()
        stock_repo = get_stock_repository()
        customer_repo = get_customer_repository()

        if order_repo is None:
            raise HTTPException(status_code=500, detail="Database connection failed")

        # ===== REVENUE METRICS =====
        # Date-bounded fetch pushed into Mongo (no 500 cap, real datetime
        # filter). The previous find_by_store() string-filtered a BSON Date
        # (never matched) and truncated at 500 orders.
        prev_start = start_date - (end_date - start_date)
        prev_end = start_date
        # [RPT-1] Exclude CANCELLED and DRAFT so only real fulfilled revenue counts.
        current_orders = [
            o
            for o in _fetch_orders_in_window(
                order_repo, store_id=store_id, start=start_date, end=end_date
            )
            if _is_billable(o)
        ]
        prev_orders = [
            o
            for o in _fetch_orders_in_window(
                order_repo, store_id=store_id, start=prev_start, end=prev_end
            )
            if _is_billable(o)
        ]

        total_revenue = sum(_safe_float(o.get("total_amount")) for o in current_orders)
        total_orders_count = len(current_orders)

        prev_revenue = sum(_safe_float(o.get("total_amount")) for o in prev_orders)
        revenue_change = (
            ((total_revenue - prev_revenue) / prev_revenue * 100)
            if prev_revenue > 0
            else 0
        )

        # ===== MARGIN METRICS =====
        # Calculate from order line items
        total_cost = sum(
            _safe_float(item.get("quantity")) * _safe_float(item.get("cost_price"))
            for order in current_orders
            for item in order.get("items", [])
        )
        total_cogs = total_cost
        gross_profit = total_revenue - total_cogs
        gross_margin_percent = (
            (gross_profit / total_revenue * 100) if total_revenue > 0 else 0
        )

        # [RPT-3] Net margin: opex is not yet tracked per-store in the DB.
        # Return None rather than fabricating a 10% placeholder -- the
        # frontend renders "—" for null, which is honest.  gross_margin is
        # still computed from real COGS on the line items; net_margin is null
        # until opex is wired (see the FIND-7 / dual-mode budgeting backlog).
        net_profit = None
        net_margin_percent = None

        # ===== TRANSACTION METRICS =====
        avg_transaction_value = (
            total_revenue / total_orders_count if total_orders_count > 0 else 0
        )
        customer_footfall = len(
            set(o.get("customer_id") for o in current_orders if o.get("customer_id"))
        )

        # ===== INVENTORY METRICS =====
        inventory = (
            stock_repo.find_many({"store_id": store_id}, limit=0)
            if stock_repo is not None
            else []
        )

        # Inventory turnover: COGS / avg-inventory-value.
        # A true average needs a beginning-of-period snapshot which is not stored;
        # dividing current value by 2 as a "simplified average" fabricates a number.
        # Return null with a note rather than emit a misleading KPI.
        inventory_turnover = None  # null until beginning-period snapshot is available

        low_stock_count = len(
            [
                i
                for i in inventory
                if _safe_int(i.get("quantity")) <= _safe_int(i.get("reorder_point"))
            ]
        )

        # ===== TOP 5 PRODUCTS =====
        product_sales = {}
        for order in current_orders:
            for item in order.get("items", []):
                product_id = item.get("product_id", "unknown")
                if product_id not in product_sales:
                    product_sales[product_id] = {
                        "name": item.get("product_name", "Unknown"),
                        "units": 0,
                        "revenue": 0.0,
                        "sku": item.get("sku", ""),
                    }
                product_sales[product_id]["units"] += _safe_int(item.get("quantity"))
                product_sales[product_id]["revenue"] += _safe_float(
                    item.get("total_amount")
                )

        top_products = sorted(
            [{"product_id": pid, **data} for pid, data in product_sales.items()],
            key=lambda x: x["revenue"],
            reverse=True,
        )[:5]

        # ===== CASH REGISTER SUMMARY =====
        # Opening balance (from previous day closing or default 0)
        # No cash-register opening balance or per-period expense source is
        # wired here yet — use 0 rather than the old fabricated 5000 / 500 so
        # closing_balance reflects real sales only.
        opening_balance = 0.0
        sales_amount = total_revenue
        expenses_amount = 0.0
        closing_balance = opening_balance + sales_amount - expenses_amount

        # ===== STORE COMPARISON (if applicable) =====
        # Date-bounded to the SELECTED period window (all stores, no cap).
        # Previously this pulled find_many({}) capped at 100 rows and only
        # tallied same-day orders regardless of the chosen period.
        all_store_orders = [
            o
            for o in _fetch_orders_in_window(
                order_repo, store_id=None, start=start_date, end=end_date
            )
            if _is_billable(o)
        ]
        store_comparison = []
        stores_by_id = {}

        # Same cross-store leak as /store-performance: single-store roles
        # (STORE_MANAGER/ACCOUNTANT) must not see other stores in the ranking.
        kpi_is_cross, kpi_allowed = user_store_scope(current_user)
        for order in all_store_orders:
            order_store_id = order.get("store_id")
            if not order_store_id:
                continue  # skip orders with no store_id rather than attributing to a phantom store
            if not kpi_is_cross and order_store_id not in kpi_allowed:
                continue
            if order_store_id not in stores_by_id:
                stores_by_id[order_store_id] = {
                    "store_id": order_store_id,
                    "revenue": 0.0,
                    "orders": 0,
                }
            stores_by_id[order_store_id]["revenue"] += _safe_float(
                order.get("total_amount")
            )
            stores_by_id[order_store_id]["orders"] += 1

        store_comparison = sorted(
            list(stores_by_id.values()), key=lambda x: x["revenue"], reverse=True
        )[:5]

        return {
            "period": period,
            "timestamp": datetime.now().isoformat(),
            "store_id": store_id,
            # Revenue metrics
            "revenue": {
                "total": float(total_revenue),
                "change_percent": float(revenue_change),
                "avg_transaction_value": float(avg_transaction_value),
                "total_orders": total_orders_count,
            },
            # Margin metrics.
            # gross_margin is computed from real line-item COGS.
            # net_margin is null (not a fabricated constant) because per-store
            # opex is not yet recorded in the DB -- [RPT-3].
            "margins": {
                "gross_margin_percent": float(gross_margin_percent),
                "net_margin_percent": net_margin_percent,
                "gross_profit": float(gross_profit),
                "net_profit": net_profit,
                "net_margin_note": (
                    "Operating expenses not yet tracked per store; "
                    "net margin is unavailable until opex is wired."
                ),
            },
            # Customer metrics
            "customers": {
                "footfall": customer_footfall,
                "avg_order_value": float(avg_transaction_value),
            },
            # Inventory metrics
            "inventory": {
                # turnover_ratio is null: needs a beginning-period snapshot to
                # compute a real average; emitting a fabricated number is worse
                # than null (frontend renders "—").
                "turnover_ratio": inventory_turnover,
                "low_stock_items": low_stock_count,
                "total_items": len(inventory),
            },
            # Top products
            "top_products": top_products,
            # Cash register
            "cash_register": {
                "opening_balance": float(opening_balance),
                "sales": float(sales_amount),
                "expenses": float(expenses_amount),
                "closing_balance": float(closing_balance),
            },
            # Store comparison
            "store_comparison": store_comparison,
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Enterprise KPIs fetch failed")
        raise HTTPException(
            status_code=500,
            detail="Could not load enterprise KPIs - try again or contact support",
        )
