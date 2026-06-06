"""
IMS 2.0 - Reports Router
=========================
Real database queries for dashboard and reports
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
from datetime import date, datetime, timedelta
from ..utils.ist import now_ist, now_ist_naive, fy_start_year_ist
from calendar import monthrange
from .auth import get_current_user, require_roles
from ..utils.dates import to_date_str
from ..dependencies import (
    get_order_repository,
    get_stock_repository,
    get_customer_repository,
    get_task_repository,
    get_attendance_repository,
    get_audit_repository,
    get_eye_test_repository,
    get_db,
    validate_store_access,
)

router = APIRouter()

# Roles allowed to view financial reports (P&L, GST returns, outstanding,
# margins, discount analysis). Mirrors the frontend Reports route guard;
# SUPERADMIN auto-passes. NOTE: /dashboard, /targets and the operational
# reports stay OPEN — the Hub uses /dashboard + /targets for every role.
_REPORT_FINANCE_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")


# ============================================================================
# Order aggregation helpers (shared across the sales reports)
# ============================================================================
# Audit pass (2026-05) caught two bugs in every /sales/* endpoint:
#   1. Date filter was `"created_at": {"$gte": dt.isoformat()}` — a
#      string compared against a Mongo Date field never matches, so
#      every aggregation returned 0 rows (and 0 revenue) even when
#      real orders existed.
#   2. Field names: orders stamp `grand_total` / `total_discount` /
#      `tax_amount`, but the loops summed `final_amount` / `total_amount`
#      / `discount_amount` (legacy names that orders.py never used). So
#      even when the date filter happened to match (e.g. with seeded
#      mock data that had ISO-string created_at), the totals were 0.
#
# Items also stamped `item_total` and `unit_price`, but the
# `/sales/by-category` loop summed `item.total` / `item.price` —
# different bug, same root cause (drift).
#
# These helpers centralise the correct field names and filter shapes
# so future endpoints can't drift.


def _orders_in_window(
    order_repo,
    *,
    store_id: Optional[str],
    start_dt: datetime,
    end_dt: datetime,
) -> list:
    """Fetch non-cancelled, non-DRAFT orders for a (store, datetime
    window). Filter is a real Mongo Date range — passes through to
    `order_repo.find_many` which preserves the datetime objects."""
    flt: dict = {
        "created_at": {"$gte": start_dt, "$lte": end_dt},
        "status": {"$nin": ["CANCELLED", "DRAFT"]},
    }
    if store_id:
        flt["store_id"] = store_id
    try:
        # BUG-061: limit=0 returns ALL matching orders. The default limit=100
        # silently truncated every aggregation that feeds off this helper
        # (~15 sales/profit/discount/footfall reports) -> understated totals.
        return order_repo.find_many(flt, limit=0) or []
    except Exception:
        return []


def _order_revenue(order: dict) -> float:
    """Single-source-of-truth read of an order's billable amount.
    Falls through legacy field names so older docs from before the
    grand_total rename don't silently zero out."""
    for k in ("grand_total", "final_amount", "total_amount", "total"):
        v = order.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def _order_discount(order: dict) -> float:
    for k in ("total_discount", "discount_amount", "discount"):
        v = order.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def _order_tax(order: dict) -> float:
    for k in ("tax_amount", "total_tax", "tax"):
        v = order.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def _item_revenue(item: dict) -> float:
    """Per-line revenue (after item-level discount, before cart-level
    discount). orders.py stamps `item_total`; legacy docs may have
    `total` or `price * quantity`."""
    for k in ("item_total", "total"):
        v = item.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    try:
        unit = float(item.get("unit_price") or item.get("price") or 0)
        qty = float(item.get("quantity") or 1)
        return unit * qty
    except (TypeError, ValueError):
        return 0.0


def _summarise_orders(orders: list) -> dict:
    """Bog-standard summary envelope used by /sales/summary + /sales/growth."""
    total_sales = round(sum(_order_revenue(o) for o in orders), 2)
    total_tax = round(sum(_order_tax(o) for o in orders), 2)
    total_discount = round(sum(_order_discount(o) for o in orders), 2)
    n = len(orders)
    return {
        "total_sales": total_sales,
        "total_orders": n,
        "avg_order_value": round(total_sales / n, 2) if n else 0.0,
        "total_tax": total_tax,
        "total_discount": total_discount,
    }


def _daily_trend(orders: list) -> list:
    """Group orders by date_str (or created_at[:10] fallback). Returns
    sorted-asc list of {date, sales, orders}."""
    by_day: dict = {}
    for o in orders:
        ds = o.get("date_str")
        if not ds:
            ca = o.get("created_at")
            if isinstance(ca, datetime):
                ds = ca.date().isoformat()
            elif isinstance(ca, str) and len(ca) >= 10:
                ds = ca[:10]
        if not ds:
            continue
        slot = by_day.setdefault(ds, {"date": ds, "sales": 0.0, "orders": 0})
        slot["sales"] += _order_revenue(o)
        slot["orders"] += 1
    out = list(by_day.values())
    for s in out:
        s["sales"] = round(s["sales"], 2)
    return sorted(out, key=lambda x: x["date"])


def _category_breakdown(orders: list) -> list:
    """Sum revenue + units per category from order items."""
    by_cat: dict = {}
    total = 0.0
    for o in orders:
        for it in o.get("items") or []:
            cat = it.get("category") or it.get("item_type") or "Other"
            slot = by_cat.setdefault(cat, {"category": cat, "sales": 0.0, "units": 0})
            line_rev = _item_revenue(it)
            slot["sales"] += line_rev
            slot["units"] += int(it.get("quantity") or 1)
            total += line_rev
    out = list(by_cat.values())
    for s in out:
        s["sales"] = round(s["sales"], 2)
        s["percentage"] = round(100.0 * s["sales"] / total, 2) if total else 0.0
    return sorted(out, key=lambda x: -x["sales"])


@router.get("")
@router.get("/")
async def get_reports_root():
    """Root endpoint for available reports"""
    return {
        "module": "reports",
        "status": "active",
        "message": "reports overview endpoint ready",
    }


@router.get("/dashboard")
async def dashboard_stats(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get dashboard statistics for a store - fetched from database"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"

    order_repo = get_order_repository()
    stock_repo = get_stock_repository()
    customer_repo = get_customer_repository()
    task_repo = get_task_repository()

    # Get today's date range (IST business day, not UTC box clock)
    today = now_ist().replace(hour=0, minute=0, second=0, microsecond=0)
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    # Initialize stats
    total_sales = 0
    pending_orders = 0
    ready_orders = 0
    low_stock_items = 0
    today_orders = 0
    today_deliveries = 0
    today_new_customers = 0
    payments_received = 0
    # Today-vs-yesterday sales for the real `change` delta (see below).
    today_sales = 0.0
    yesterday_sales = 0.0

    # Fetch orders data
    if order_repo is not None:
        # Get all orders for store
        all_orders = order_repo.find_by_store(active_store)

        # Calculate totals
        for order in all_orders:
            status = order.get("status", "")
            order_date = to_date_str(order.get("created_at"))

            # Today's orders
            if order_date == today_str:
                today_orders += 1
                payments_received += order.get("amount_paid", 0)

            # Status-based counts
            if status == "CONFIRMED" or status == "PROCESSING":
                pending_orders += 1
            elif status == "READY":
                ready_orders += 1
            elif status == "DELIVERED" and order_date == today_str:
                today_deliveries += 1

            # Total sales (completed orders)
            if status not in ["CANCELLED", "DRAFT"]:
                rev = _order_revenue(order)
                total_sales += rev
                if order_date == today_str:
                    today_sales += rev
                elif order_date == yesterday_str:
                    yesterday_sales += rev

    # Fetch inventory data
    if stock_repo is not None:
        low_stock = stock_repo.find_low_stock(active_store, threshold=5)
        low_stock_items = len(low_stock) if low_stock else 0

    # Fetch customer data
    if customer_repo is not None:
        # Count customers created today
        all_customers = customer_repo.find_many({"store_id": active_store}, limit=0)
        for customer in all_customers:
            created_date = to_date_str(customer.get("created_at"))
            if created_date == today_str:
                today_new_customers += 1

    # Fetch task/appointment data
    open_tasks = 0
    if task_repo is not None:
        task_summary = task_repo.get_task_summary(active_store)
        if task_summary:
            open_tasks = task_summary.get("OPEN", 0) + task_summary.get(
                "IN_PROGRESS", 0
            )

    # Real today-vs-yesterday sales change (same delta math as
    # /sales/comparison + /sales/growth). null when yesterday had no sales
    # to compare against -- the frontend renders "-" for null rather than a
    # fabricated 12.5%.
    if yesterday_sales > 0:
        change = round((today_sales - yesterday_sales) / yesterday_sales * 100, 2)
    else:
        change = None

    return {
        "totalSales": total_sales,
        "change": change,
        "pendingOrders": pending_orders,
        "urgentOrders": ready_orders,
        "appointmentsToday": open_tasks,
        "upcomingAppointments": 0,
        "lowStockItems": low_stock_items,
        "todaySummary": {
            "totalOrders": today_orders,
            "deliveries": today_deliveries,
            "eyeTests": 0,
            "newCustomers": today_new_customers,
            "paymentsReceived": payments_received,
        },
    }


@router.get("/inventory")
async def inventory_report(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get inventory report for a store - fetched from database"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    stock_repo = get_stock_repository()

    if stock_repo is not None:
        all_stock = stock_repo.find_many({"store_id": active_store}, limit=0)
        low_stock = stock_repo.find_low_stock(active_store, threshold=5)

        total_items = len(all_stock)
        total_value = sum(
            (s.get("quantity", 0) * s.get("cost_price", 0)) for s in all_stock
        )
        low_stock_count = len(low_stock) if low_stock else 0
        out_of_stock = len([s for s in all_stock if s.get("quantity", 0) <= 0])

        # Group by category
        categories = {}
        for item in all_stock:
            cat = item.get("category", "Other")
            if cat not in categories:
                categories[cat] = {"name": cat, "count": 0, "value": 0}
            categories[cat]["count"] += 1
            categories[cat]["value"] += item.get("quantity", 0) * item.get(
                "cost_price", 0
            )

        return {
            "totalItems": total_items,
            "totalValue": round(total_value, 2),
            "lowStock": low_stock_count,
            "outOfStock": out_of_stock,
            "categories": list(categories.values()),
        }

    return {
        "totalItems": 0,
        "totalValue": 0,
        "lowStock": 0,
        "outOfStock": 0,
        "categories": [],
    }


@router.get("/sales/summary")
async def sales_summary(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Sales summary + daily trend + category breakdown for the
    requested window. Single endpoint that the frontend Reports page
    reads off the same response."""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()
    empty = {
        "summary": {
            "total_sales": 0,
            "total_orders": 0,
            "avg_order_value": 0,
            "total_tax": 0,
            "total_discount": 0,
        },
        "dailyTrend": [],
        "categoryBreakdown": [],
    }
    if order_repo is None:
        return empty

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )
    return {
        "summary": _summarise_orders(orders),
        "dailyTrend": _daily_trend(orders),
        "categoryBreakdown": _category_breakdown(orders),
    }


@router.get("/sales/daily")
async def daily_sales(
    store_id: Optional[str] = Query(None),
    days: int = Query(30),
    current_user: dict = Depends(get_current_user),
):
    """Daily sales for the last N days (chart data)."""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()
    if order_repo is None:
        return {"data": []}
    end_dt = now_ist_naive()
    start_dt = end_dt - timedelta(days=days)
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=start_dt,
        end_dt=end_dt,
    )
    return {"data": _daily_trend(orders)}


@router.get("/sales/by-salesperson")
async def sales_by_salesperson(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Get sales grouped by salesperson (management report; store-scoped)."""
    active_store = validate_store_access(store_id, current_user)
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": []}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    # Datetime objects, NOT .isoformat() strings -- created_at is a BSON Date
    # so a string filter never matched and this report came back empty.
    orders = order_repo.find_many(
        {
            "store_id": active_store,
            "created_at": {"$gte": from_dt, "$lte": to_dt},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        },
        limit=0,
    )

    # Group by salesperson
    by_person = {}
    for order in orders:
        person = order.get("sales_person_id") or order.get("created_by") or "Unknown"
        person_name = order.get("sales_person_name", person)
        if person not in by_person:
            by_person[person] = {
                "id": person,
                "name": person_name,
                "sales": 0,
                "orders": 0,
            }
        # Use the canonical revenue reader (grand_total first) so the legacy
        # final_amount/total_amount-only sum doesn't zero out modern orders.
        by_person[person]["sales"] += _order_revenue(order)
        by_person[person]["orders"] += 1

    return {"data": list(by_person.values())}


@router.get("/sales/by-category")
async def sales_by_category(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Sales grouped by product category for the requested window."""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()
    if order_repo is None:
        return {"data": []}
    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )
    return {"data": _category_breakdown(orders)}


# ============================================================================
# INVENTORY REPORTS
# ============================================================================


@router.get("/inventory/summary")
async def inventory_summary(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get inventory summary"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    stock_repo = get_stock_repository()

    if stock_repo is None:
        return {
            "summary": {
                "total_items": 0,
                "total_quantity": 0,
                "total_value": 0,
                "low_stock_count": 0,
                "out_of_stock_count": 0,
            }
        }

    # Get all stock
    all_stock = stock_repo.find_many({"store_id": active_store}, limit=0)
    low_stock = stock_repo.find_low_stock(active_store, threshold=5)

    total_value = sum(
        (s.get("quantity", 0) * s.get("cost_price", 0)) for s in all_stock
    )

    out_of_stock = [s for s in all_stock if s.get("quantity", 0) <= 0]

    return {
        "summary": {
            "total_items": len(all_stock),
            "total_quantity": sum(s.get("quantity", 0) for s in all_stock),
            "total_value": round(total_value, 2),
            "low_stock_count": len(low_stock) if low_stock else 0,
            "out_of_stock_count": len(out_of_stock),
        }
    }


@router.get("/inventory/valuation")
async def inventory_valuation(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Get inventory valuation by category (management report; store-scoped)."""
    active_store = validate_store_access(store_id, current_user)
    stock_repo = get_stock_repository()

    if stock_repo is None:
        return {"valuation": {"by_category": [], "total": 0}}

    all_stock = stock_repo.find_many({"store_id": active_store}, limit=0)

    # Group by category
    by_category = {}
    for item in all_stock:
        category = item.get("category", "Other")
        if category not in by_category:
            by_category[category] = {"category": category, "quantity": 0, "value": 0}
        by_category[category]["quantity"] += item.get("quantity", 0)
        by_category[category]["value"] += item.get("quantity", 0) * item.get(
            "cost_price", 0
        )

    total = sum(c["value"] for c in by_category.values())

    return {
        "valuation": {
            "by_category": list(by_category.values()),
            "total": round(total, 2),
        }
    }


@router.get("/inventory/tax-code-audit")
async def tax_code_audit(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Go-live readiness check: flag every product whose stored HSN code or GST
    rate disagrees with the canonical table for its category.

    Why: products may have been bulk-loaded with the wrong tax code (the classic
    case is a sunglass tagged 5% when non-corrective sunglasses are 18%, or a
    blank/unknown category that silently falls back to 5%). POS bills whatever is
    on the product, so a wrong code here means wrong GST on every sale of it.
    This is READ-ONLY — it never edits a product; it produces the worklist the
    catalog manager fixes before the first live invoice.

    A product is flagged when, for its category:
      * `category` is blank/unknown (not in the canonical table) -> needs a
        category before it can be tax-checked at all; or
      * stored `gst_rate` != the canonical rate for that category; or
      * stored `hsn_code` != the canonical 6-digit HSN for that category
        (a 4-digit prefix of the canonical code is accepted: small businesses
        <=Rs 5 Cr may legitimately use 4-digit HSNs).

    Response:
        {
          "data": [ { product_id, sku, name, category, stored_hsn, stored_gst,
                      expected_hsn, expected_gst, issues: [str], severity } ],
          "summary": { total_products, flagged, ok, by_issue: {...},
                       uncategorized, gst_mismatch, hsn_mismatch },
        }
    """
    from ..services.gst_rates import (
        GST_CATEGORY_TABLE,
        gst_rate_for_category,
        hsn_for_category,
    )

    db = get_db()
    empty = {
        "data": [],
        "summary": {
            "total_products": 0,
            "flagged": 0,
            "ok": 0,
            "uncategorized": 0,
            "gst_mismatch": 0,
            "hsn_mismatch": 0,
        },
    }
    if db is None or not getattr(db, "is_connected", True):
        return empty

    products = db.get_collection("products")
    if products is None:
        return empty

    query: Dict[str, Any] = {"is_active": {"$ne": False}}
    if store_id:
        # Products are catalog-global, but some deployments scope by store; honour
        # it when present without excluding global rows.
        query = {
            "is_active": {"$ne": False},
            "$or": [
                {"store_id": store_id},
                {"store_id": {"$exists": False}},
                {"store_id": None},
            ],
        }

    data = []
    total = 0
    gst_mismatch = 0
    hsn_mismatch = 0
    uncategorized = 0

    for p in products.find(query):
        total += 1
        category = (p.get("category") or "").strip()
        stored_hsn = str(p.get("hsn_code") or "").strip()
        stored_gst = p.get("gst_rate")

        issues = []
        known = bool(category) and category.strip().upper() in GST_CATEGORY_TABLE
        expected_gst = gst_rate_for_category(category) if known else None
        expected_hsn = hsn_for_category(category) if known else None

        if not known:
            uncategorized += 1
            issues.append(
                "Blank or unrecognized category — set a category so the tax code "
                "can be verified."
            )
        else:
            # GST rate mismatch (tolerate float vs int: 5 == 5.0).
            try:
                stored_gst_f = float(stored_gst) if stored_gst is not None else None
            except (TypeError, ValueError):
                stored_gst_f = None
            if stored_gst_f is None:
                gst_mismatch += 1
                issues.append(f"No GST rate set (expected {expected_gst}%).")
            elif abs(stored_gst_f - float(expected_gst)) > 0.001:
                gst_mismatch += 1
                issues.append(
                    f"GST rate {stored_gst_f}% does not match the {expected_gst}% "
                    f"expected for {category}."
                )

            # HSN mismatch — accept a 4-digit prefix of the canonical 6-digit code.
            if expected_hsn:
                if not stored_hsn:
                    hsn_mismatch += 1
                    issues.append(f"No HSN code set (expected {expected_hsn}).")
                elif stored_hsn != expected_hsn and not expected_hsn.startswith(
                    stored_hsn
                ):
                    hsn_mismatch += 1
                    issues.append(
                        f"HSN {stored_hsn} does not match the expected "
                        f"{expected_hsn} for {category}."
                    )

        if issues:
            # Severity: a wrong/blank GST rate is the costly one (bills wrong tax);
            # an HSN-only or category issue is high but slightly less urgent.
            has_gst_issue = any("GST" in i for i in issues)
            data.append(
                {
                    "product_id": p.get("product_id") or str(p.get("_id", "")),
                    "sku": p.get("sku") or p.get("barcode") or "",
                    "name": p.get("name") or p.get("product_name") or "",
                    "category": category or None,
                    "stored_hsn": stored_hsn or None,
                    "stored_gst": stored_gst,
                    "expected_hsn": expected_hsn,
                    "expected_gst": expected_gst,
                    "issues": issues,
                    "severity": "CRITICAL" if has_gst_issue else "HIGH",
                }
            )

    # Worst first: CRITICAL (wrong GST) above HIGH (HSN/category only).
    data.sort(key=lambda r: (r["severity"] != "CRITICAL", r["category"] or ""))

    flagged = len(data)
    return {
        "data": data,
        "summary": {
            "total_products": total,
            "flagged": flagged,
            "ok": total - flagged,
            "uncategorized": uncategorized,
            "gst_mismatch": gst_mismatch,
            "hsn_mismatch": hsn_mismatch,
        },
    }


@router.get("/clinical/eye-tests")
async def eye_test_report(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Eye test count report (by optometrist) for a date range.

    Queries the ``eye_tests`` collection via EyeTestRepository using the
    COMPLETED status + test_date string range (same lexicographic strategy
    as the Test-History page).  Returns each test record for the FE plus
    an aggregation by optometrist and a grand total.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    test_repo = get_eye_test_repository()
    if test_repo is None:
        return {"data": [], "by_optometrist": [], "total": 0}

    tests = test_repo.get_store_tests_in_range(
        store_id=active_store,
        from_date=from_date.isoformat(),
        to_date=to_date.isoformat(),
        status="COMPLETED",
        limit=1000,
    )

    by_optometrist: dict = {}
    for t in tests:
        optom_id = t.get("optometrist_id") or t.get("assigned_to") or "Unknown"
        optom_name = t.get("optometrist_name") or t.get("assigned_to_name") or optom_id
        if optom_id not in by_optometrist:
            by_optometrist[optom_id] = {
                "optometrist_id": optom_id,
                "optometrist_name": optom_name,
                "test_count": 0,
            }
        by_optometrist[optom_id]["test_count"] += 1

    return {
        "data": tests,
        "by_optometrist": list(by_optometrist.values()),
        "total": len(tests),
    }


# ============================================================================
# HR REPORTS
# ============================================================================


@router.get("/hr/attendance")
async def attendance_report(
    store_id: Optional[str] = Query(None),
    year: int = Query(...),
    month: int = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get attendance report for month"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    attendance_repo = get_attendance_repository()

    if attendance_repo is None:
        return {"data": [], "summary": {"total_present": 0, "total_absent": 0}}

    # Get attendance for month
    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)

    records = attendance_repo.find_many(
        {
            "store_id": active_store,
            "date": {
                "$gte": start_date.isoformat()[:10],
                "$lt": end_date.isoformat()[:10],
            },
        },
        limit=0,
    )

    return {
        "data": records,
        "summary": {
            "total_present": len([r for r in records if r.get("status") == "PRESENT"]),
            "total_absent": len([r for r in records if r.get("status") == "ABSENT"]),
        },
    }


# ============================================================================
# FINANCE REPORTS
# ============================================================================


@router.get("/finance/outstanding")
async def outstanding_report(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Get outstanding payments report"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": [], "total_outstanding": 0}

    # Get orders with balance due
    orders = order_repo.find_many(
        {
            "store_id": active_store,
            "balance_due": {"$gt": 0},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        },
        limit=0,
    )

    outstanding_data = []
    total = 0
    now = now_ist_naive()
    aging_buckets = {"0_30": 0.0, "31_60": 0.0, "61_90": 0.0, "90_plus": 0.0}

    for order in orders:
        balance = order.get("balance_due", 0)
        if balance > 0:
            total += balance

            # Calculate aging bucket from order creation date
            created_str = order.get("created_at", "")
            days_old = 0
            try:
                if isinstance(created_str, str) and created_str:
                    created_dt = datetime.fromisoformat(
                        created_str.replace("Z", "+00:00").replace("+00:00", "")
                    )
                    days_old = (now - created_dt).days
                elif isinstance(created_str, datetime):
                    days_old = (now - created_str).days
            except (ValueError, TypeError):
                days_old = 0

            if days_old <= 30:
                bucket = "0-30 days"
                aging_buckets["0_30"] += balance
            elif days_old <= 60:
                bucket = "31-60 days"
                aging_buckets["31_60"] += balance
            elif days_old <= 90:
                bucket = "61-90 days"
                aging_buckets["61_90"] += balance
            else:
                bucket = "90+ days"
                aging_buckets["90_plus"] += balance

            outstanding_data.append(
                {
                    "order_id": order.get("order_id"),
                    "order_number": order.get("order_number"),
                    "customer_name": order.get("customer_name"),
                    "customer_phone": order.get("customer_phone"),
                    "total_amount": order.get("final_amount", 0),
                    "paid_amount": order.get("paid_amount", 0),
                    "balance_due": balance,
                    "created_at": order.get("created_at"),
                    "days_outstanding": days_old,
                    "aging_bucket": bucket,
                }
            )

    # Sort by age (oldest first)
    outstanding_data.sort(key=lambda x: x.get("days_outstanding", 0), reverse=True)

    return {
        "data": outstanding_data,
        "total_outstanding": total,
        "aging_summary": {
            "0-30 days": round(aging_buckets["0_30"], 2),
            "31-60 days": round(aging_buckets["31_60"], 2),
            "61-90 days": round(aging_buckets["61_90"], 2),
            "90+ days": round(aging_buckets["90_plus"], 2),
        },
        "count_by_aging": {
            "0-30 days": sum(
                1 for d in outstanding_data if d["aging_bucket"] == "0-30 days"
            ),
            "31-60 days": sum(
                1 for d in outstanding_data if d["aging_bucket"] == "31-60 days"
            ),
            "61-90 days": sum(
                1 for d in outstanding_data if d["aging_bucket"] == "61-90 days"
            ),
            "90+ days": sum(
                1 for d in outstanding_data if d["aging_bucket"] == "90+ days"
            ),
        },
    }


@router.get("/finance/gst")
async def gst_report(
    from_date: date = Query(...),
    to_date: date = Query(...),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """GST report for a date range.

    Orders persist `grand_total` + `tax_amount` (and per-line taxable/tax),
    NOT `cgst_amount` / `sgst_amount` / `igst_amount` / `taxable_amount` /
    `final_amount` -- those legacy field names never landed, so the old loop
    summed all-zeros. Taxable is derived as grand_total - tax_amount (via
    _order_taxable_and_tax) and the total tax is split CGST/SGST (intra-state)
    vs IGST (inter-state) by comparing the store's home state with the
    customer's state -- the same rule GSTR-1 / GSTR-3B use.

    The `created_at` filter uses real datetime objects: BaseRepository writes
    `created_at` as a BSON datetime, so the previous `.isoformat()` STRING
    filter never matched a single order.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {
            "data": [],
            "summary": {
                "total_cgst": 0,
                "total_sgst": 0,
                "total_igst": 0,
                "total_taxable": 0,
                "total_tax": 0,
            },
        }

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    orders = order_repo.find_many(
        {
            "store_id": active_store,
            # Datetime objects, NOT .isoformat() strings -- created_at is a
            # BSON datetime and a string comparison never matches.
            "created_at": {"$gte": from_dt, "$lte": to_dt},
            "status": {"$nin": ["CANCELLED", "DRAFT", "cancelled", "draft"]},
        },
        limit=0,  # 0 -> no cap: a GST report must include every invoice.
    )

    # Resolve the store's home state + a customer_id -> state map so we can
    # split intra-state (CGST+SGST) from inter-state (IGST) tax, matching
    # the GSTR-1 / GSTR-3B logic.
    store_state = ""
    cust_state_map: dict = {}
    raw_db = _get_raw_db()
    if raw_db is not None:
        try:
            store_doc = raw_db["stores"].find_one({"store_id": active_store})
            if store_doc:
                store_state = str(store_doc.get("state", "") or "")
        except Exception:
            pass
        try:
            for cust in raw_db["customers"].find({}, {"customer_id": 1, "state": 1}):
                cust_state_map[str(cust.get("customer_id", ""))] = str(
                    cust.get("state", "") or ""
                )
        except Exception:
            pass

    rows = []
    total_cgst = 0.0
    total_sgst = 0.0
    total_igst = 0.0
    total_taxable = 0.0

    for o in orders:
        taxable, tax = _order_taxable_and_tax(o)
        customer_state = (
            cust_state_map.get(str(o.get("customer_id", ""))) or store_state
        )
        is_inter_state = bool(
            store_state
            and customer_state
            and store_state.strip().lower() != customer_state.strip().lower()
        )
        if is_inter_state:
            cgst = sgst = 0.0
            igst = round(tax, 2)
        else:
            cgst = sgst = round(tax / 2, 2)
            igst = 0.0

        total_cgst += cgst
        total_sgst += sgst
        total_igst += igst
        total_taxable += taxable

        rows.append(
            {
                "order_number": o.get("order_number") or o.get("order_id"),
                # created_at is a BSON datetime; str(...)[:10] -> 'YYYY-MM-DD'
                # and is also safe for legacy ISO-string rows.
                "date": str(o.get("created_at", ""))[:10],
                "taxable_amount": taxable,
                "cgst": cgst,
                "sgst": sgst,
                "igst": igst,
                "total": float(o.get("grand_total", o.get("total_amount", 0)) or 0),
            }
        )

    return {
        "data": rows,
        "summary": {
            "total_taxable": round(total_taxable, 2),
            "total_cgst": round(total_cgst, 2),
            "total_sgst": round(total_sgst, 2),
            "total_igst": round(total_igst, 2),
            "total_tax": round(total_cgst + total_sgst + total_igst, 2),
        },
    }


# ============================================================================
# TASK REPORTS
# ============================================================================


@router.get("/tasks/summary")
async def task_summary(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get task summary"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    task_repo = get_task_repository()

    if task_repo is None:
        return {
            "summary": {
                "open": 0,
                "in_progress": 0,
                "completed": 0,
                "overdue": 0,
            }
        }

    summary = task_repo.get_task_summary(active_store)
    overdue_count = task_repo.get_overdue_count(active_store)

    return {
        "summary": {
            "open": summary.get("OPEN", 0) if summary else 0,
            "in_progress": summary.get("IN_PROGRESS", 0) if summary else 0,
            "completed": summary.get("COMPLETED", 0) if summary else 0,
            "overdue": overdue_count or 0,
        }
    }


# ============================================================================
# SALES COMPARISON & GROWTH REPORTS
# ============================================================================


@router.get("/sales/comparison")
async def sales_comparison(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    period_type: str = Query("daily"),  # daily, monthly, yearly
    current_user: dict = Depends(get_current_user),
):
    """Daily/Monthly/Yearly sales comparison (current vs previous period)"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"current_period": {}, "previous_period": {}, "comparison": {}}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    # Calculate period difference
    period_days = (to_date - from_date).days
    prev_from_date = from_date - timedelta(days=period_days + 1)
    prev_to_date = from_date - timedelta(days=1)

    prev_from_dt = datetime.combine(prev_from_date, datetime.min.time())
    prev_to_dt = datetime.combine(prev_to_date, datetime.max.time())

    current_orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )
    prev_orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=prev_from_dt,
        end_dt=prev_to_dt,
    )

    current_sales = sum(_order_revenue(o) for o in current_orders)
    prev_sales = sum(_order_revenue(o) for o in prev_orders)

    change = ((current_sales - prev_sales) / prev_sales * 100) if prev_sales > 0 else 0

    return {
        "current_period": {
            "sales": round(current_sales, 2),
            "orders": len(current_orders),
            "avg_order_value": (
                round(current_sales / len(current_orders), 2) if current_orders else 0
            ),
        },
        "previous_period": {
            "sales": round(prev_sales, 2),
            "orders": len(prev_orders),
            "avg_order_value": (
                round(prev_sales / len(prev_orders), 2) if prev_orders else 0
            ),
        },
        "comparison": {
            "sales_change_percent": round(change, 2),
            "sales_change_amount": round(current_sales - prev_sales, 2),
            "order_change": len(current_orders) - len(prev_orders),
        },
    }


@router.get("/sales/growth")
async def sales_growth(
    store_id: Optional[str] = Query(None),
    year: int = Query(...),
    month: int = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """MoM (Month-over-Month) and YoY (Year-over-Year) growth percentages"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"current_month": {}, "mom_growth": {}, "yoy_growth": {}}

    # Current month
    current_start = datetime(year, month, 1)
    if month == 12:
        current_end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
    else:
        current_end = datetime(year, month + 1, 1) - timedelta(seconds=1)

    # Previous month (MoM)
    if month == 1:
        mom_start = datetime(year - 1, 12, 1)
        mom_end = datetime(year, 1, 1) - timedelta(seconds=1)
    else:
        mom_start = datetime(year, month - 1, 1)
        mom_end = datetime(year, month, 1) - timedelta(seconds=1)

    # Previous year (YoY)
    yoy_start = datetime(year - 1, month, 1)
    if month == 12:
        yoy_end = datetime(year, 1, 1) - timedelta(seconds=1)
    else:
        yoy_end = datetime(year - 1, month + 1, 1) - timedelta(seconds=1)

    current_orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=current_start,
        end_dt=current_end,
    )
    mom_orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=mom_start,
        end_dt=mom_end,
    )
    yoy_orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=yoy_start,
        end_dt=yoy_end,
    )

    current_sales = sum(_order_revenue(o) for o in current_orders)
    mom_sales = sum(_order_revenue(o) for o in mom_orders)
    yoy_sales = sum(_order_revenue(o) for o in yoy_orders)

    mom_growth = ((current_sales - mom_sales) / mom_sales * 100) if mom_sales > 0 else 0
    yoy_growth = ((current_sales - yoy_sales) / yoy_sales * 100) if yoy_sales > 0 else 0

    return {
        "current_month": {
            "sales": round(current_sales, 2),
            "orders": len(current_orders),
        },
        "mom_growth": {
            "percent": round(mom_growth, 2),
            "previous_month_sales": round(mom_sales, 2),
        },
        "yoy_growth": {
            "percent": round(yoy_growth, 2),
            "previous_year_sales": round(yoy_sales, 2),
        },
    }


# ============================================================================
# PROFIT & DISCOUNT REPORTS
# ============================================================================


@router.get("/profit/by-category")
async def profit_by_category(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Profit by product category"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": [], "total_profit": 0}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    # Datetime objects, NOT .isoformat() strings -- created_at is a BSON Date
    # so a string filter never matched and this report came back empty.
    orders = order_repo.find_many(
        {
            "store_id": active_store,
            "created_at": {"$gte": from_dt, "$lte": to_dt},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        },
        limit=0,
    )

    profit_by_cat = {}
    for order in orders:
        for item in order.get("items", []):
            category = item.get("category", "Other")
            if category not in profit_by_cat:
                profit_by_cat[category] = {
                    "category": category,
                    "revenue": 0,
                    "cost": 0,
                    "profit": 0,
                    "margin_percent": 0,
                }
            # _item_revenue reads item_total first (the field orders.py
            # actually stamps), falling back to legacy total / price*qty.
            selling_price = _item_revenue(item)
            cost_price = item.get("cost_price", 0) * item.get("quantity", 1)
            profit = selling_price - cost_price

            profit_by_cat[category]["revenue"] += selling_price
            profit_by_cat[category]["cost"] += cost_price
            profit_by_cat[category]["profit"] += profit

    # Calculate margin percentages
    for cat in profit_by_cat.values():
        if cat["revenue"] > 0:
            cat["margin_percent"] = round((cat["profit"] / cat["revenue"] * 100), 2)

    total_profit = sum(c["profit"] for c in profit_by_cat.values())

    return {
        "data": list(profit_by_cat.values()),
        "total_profit": round(total_profit, 2),
    }


@router.get("/profit/by-store")
async def profit_by_store(
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Profit by store (if multi-store)"""
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": [], "total_profit": 0}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    # Datetime objects, NOT .isoformat() strings -- created_at is a BSON Date
    # so a string filter never matched and this report came back empty.
    orders = order_repo.find_many(
        {
            "created_at": {"$gte": from_dt, "$lte": to_dt},
            "status": {"$nin": ["CANCELLED", "DRAFT"]},
        },
        limit=0,
    )

    profit_by_st = {}
    for order in orders:
        store = order.get("store_id", "Unknown")
        if store not in profit_by_st:
            profit_by_st[store] = {
                "store_id": store,
                "revenue": 0,
                "cost": 0,
                "profit": 0,
                "orders": 0,
            }
        profit_by_st[store]["orders"] += 1
        # Canonical revenue reader (grand_total first).
        order_amount = _order_revenue(order)
        profit_by_st[store]["revenue"] += order_amount

        # Calculate cost from items
        cost = sum(
            item.get("cost_price", 0) * item.get("quantity", 1)
            for item in order.get("items", [])
        )
        profit_by_st[store]["cost"] += cost
        profit_by_st[store]["profit"] += order_amount - cost

    total_profit = sum(s["profit"] for s in profit_by_st.values())

    return {
        "data": list(profit_by_st.values()),
        "total_profit": round(total_profit, 2),
    }


@router.get("/discount/analysis")
async def discount_analysis(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Discount average by category and store"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"by_category": [], "by_store": [], "summary": {}}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )

    # Aggregate per-category: sum item-level discount_amount + line
    # revenue. The avg_discount_percent is per-category (line-discount
    # / pre-discount line revenue), not the previous tortured formula
    # which divided by (total_discount + per-category-share-of-revenue).
    by_category: dict = {}
    total_discount = 0.0
    total_revenue = 0.0

    for order in orders:
        # Cart-level discount is split proportionally across items by
        # taxable value when the order was created. Reading per-item
        # `discount_amount` already reflects the item-level discount
        # only; the order's `total_discount` includes cart-level too.
        for item in order.get("items", []):
            category = item.get("category") or item.get("item_type") or "Other"
            if category not in by_category:
                by_category[category] = {
                    "category": category,
                    "total_discount": 0.0,
                    "total_revenue": 0.0,
                    "total_items": 0,
                    "avg_discount_percent": 0.0,
                }
            item_discount = float(
                item.get("discount_amount") or item.get("discount") or 0
            )
            line_revenue = _item_revenue(item)
            by_category[category]["total_discount"] += item_discount
            by_category[category]["total_revenue"] += line_revenue
            by_category[category]["total_items"] += int(item.get("quantity") or 1)
            total_discount += item_discount
            total_revenue += line_revenue

    for cat in by_category.values():
        # Pre-discount line revenue = post-discount + discount itself
        gross = cat["total_revenue"] + cat["total_discount"]
        cat["avg_discount_percent"] = (
            round(cat["total_discount"] / gross * 100, 2) if gross > 0 else 0.0
        )
        cat["total_discount"] = round(cat["total_discount"], 2)
        cat["total_revenue"] = round(cat["total_revenue"], 2)

    gross_total = total_revenue + total_discount
    return {
        "by_category": sorted(by_category.values(), key=lambda c: -c["total_discount"]),
        "summary": {
            "total_discount": round(total_discount, 2),
            "total_revenue": round(total_revenue, 2),
            "discount_percent": (
                round(total_discount / gross_total * 100, 2) if gross_total > 0 else 0.0
            ),
        },
    }


# ============================================================================
# STAFF & CLINICAL REPORTS
# ============================================================================


@router.get("/staff/ranking")
async def staff_ranking(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Staff performance ranking (sales, orders, avg bill); store-scoped."""
    active_store = validate_store_access(store_id, current_user)
    order_repo = get_order_repository()

    if order_repo is None:
        return {"data": []}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )

    staff_data = {}
    for order in orders:
        staff_id = order.get("sales_person_id") or order.get("created_by") or "Unknown"
        staff_name = order.get("sales_person_name", staff_id)
        if staff_id not in staff_data:
            staff_data[staff_id] = {
                "staff_id": staff_id,
                "staff_name": staff_name,
                "total_sales": 0,
                "order_count": 0,
                "avg_bill": 0,
            }
        staff_data[staff_id]["total_sales"] += _order_revenue(order)
        staff_data[staff_id]["order_count"] += 1

    for staff in staff_data.values():
        if staff["order_count"] > 0:
            staff["avg_bill"] = round(staff["total_sales"] / staff["order_count"], 2)

    # Sort by total sales descending
    ranked = sorted(staff_data.values(), key=lambda x: x["total_sales"], reverse=True)

    return {"data": ranked}


# ============================================================================
# WORKSHOP & STOCK REPORTS
# ============================================================================


@router.get("/workshop/pending-jobs")
async def pending_workshop_jobs(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Pending workshop jobs report — Phase 6.4.

    Rewritten to query the `workshop_jobs` collection via WorkshopJobRepository
    (the previous implementation used the generic `tasks` collection with
    task_type='workshop_job', which the real workshop flow never populates).

    Response shape:
        {
            "data": [  # one row per pending job, sorted by age desc
                { "job_id", "job_number", "order_id", "status",
                  "technician_id", "expected_date", "created_at",
                  "age_days", "aging_bucket" } ],
            "summary": {
                "total_pending": int,
                "overdue": int,
                "by_aging_bucket": {"0-3d": n, "3-7d": n, "7+d": n},
                "by_technician": [ {"technician_id", "count"} ],
            },
        }

    Aging buckets are computed from created_at. `age_days` is the number of
    days the job has been sitting in PENDING or IN_PROGRESS. A job whose
    `expected_date` has passed is counted as overdue regardless of age.
    """
    from database.repositories.workshop_repository import WorkshopJobRepository  # lazy
    from ..dependencies import get_db as _get_db

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    db = _get_db()
    if db is None or not getattr(db, "is_connected", True):
        return {
            "data": [],
            "summary": {
                "total_pending": 0,
                "overdue": 0,
                "by_aging_bucket": {"0-3d": 0, "3-7d": 0, "7+d": 0},
                "by_technician": [],
            },
        }

    repo = WorkshopJobRepository(db.get_collection("workshop_jobs"))
    jobs = repo.find_pending(
        active_store
    )  # PENDING + IN_PROGRESS, sorted by expected_date

    now = now_ist_naive()
    data = []
    bucket_counts = {"0-3d": 0, "3-7d": 0, "7+d": 0}
    tech_counts = {}
    overdue_count = 0

    for job in jobs:
        created = job.get("created_at")
        expected = job.get("expected_date")

        # Age in days from created_at. Defensive parse for str or datetime.
        age_days = None
        if created:
            try:
                cr_dt = (
                    created
                    if isinstance(created, datetime)
                    else datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                )
                # Normalize tz — the stored timestamps are naive in dev but
                # may be tz-aware in prod Mongo. Strip tz for the subtraction.
                if cr_dt.tzinfo is not None:
                    cr_dt = cr_dt.replace(tzinfo=None)
                age_days = max(0, (now - cr_dt).days)
            except (ValueError, TypeError):
                age_days = None

        if age_days is None:
            bucket = "0-3d"  # unknown age — treat as fresh so we don't panic-escalate
        elif age_days < 3:
            bucket = "0-3d"
        elif age_days < 7:
            bucket = "3-7d"
        else:
            bucket = "7+d"
        bucket_counts[bucket] += 1

        # Overdue check — expected_date is in the past
        is_overdue = False
        if expected:
            try:
                exp_dt = (
                    expected
                    if isinstance(expected, datetime)
                    else datetime.fromisoformat(str(expected).replace("Z", "+00:00"))
                )
                if exp_dt.tzinfo is not None:
                    exp_dt = exp_dt.replace(tzinfo=None)
                if exp_dt < now:
                    is_overdue = True
                    overdue_count += 1
            except (ValueError, TypeError):
                pass

        tech = job.get("technician_id") or "unassigned"
        tech_counts[tech] = tech_counts.get(tech, 0) + 1

        data.append(
            {
                "job_id": job.get("job_id") or str(job.get("_id", "")),
                "job_number": job.get("job_number"),
                "order_id": job.get("order_id"),
                "status": job.get("status"),
                "technician_id": job.get("technician_id"),
                "expected_date": (
                    expected.isoformat() if isinstance(expected, datetime) else expected
                ),
                "created_at": (
                    created.isoformat() if isinstance(created, datetime) else created
                ),
                "age_days": age_days,
                "aging_bucket": bucket,
                "is_overdue": is_overdue,
            }
        )

    # Sort: oldest first, with overdue jumping to the top regardless of age.
    data.sort(key=lambda r: (not r["is_overdue"], -(r["age_days"] or 0)))

    by_technician = sorted(
        [{"technician_id": t, "count": c} for t, c in tech_counts.items()],
        key=lambda r: -r["count"],
    )

    return {
        "data": data,
        "summary": {
            "total_pending": len(data),
            "overdue": overdue_count,
            "by_aging_bucket": bucket_counts,
            "by_technician": by_technician,
        },
    }


@router.get("/stock/count")
async def daily_stock_count(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Daily stock count report"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    stock_repo = get_stock_repository()

    if stock_repo is None:
        return {"data": [], "summary": {}}

    all_stock = stock_repo.find_many({"store_id": active_store}, limit=0)

    by_category = {}
    total_items = 0
    total_value = 0

    for item in all_stock:
        category = item.get("category", "Other")
        if category not in by_category:
            by_category[category] = {
                "category": category,
                "item_count": 0,
                "total_quantity": 0,
                "total_value": 0,
            }
        by_category[category]["item_count"] += 1
        by_category[category]["total_quantity"] += item.get("quantity", 0)
        by_category[category]["total_value"] += item.get("quantity", 0) * item.get(
            "cost_price", 0
        )
        total_items += 1
        total_value += item.get("quantity", 0) * item.get("cost_price", 0)

    return {
        "data": list(by_category.values()),
        "summary": {
            "total_items": total_items,
            "total_value": round(total_value, 2),
            "total_quantity": sum(item.get("quantity", 0) for item in all_stock),
        },
    }


# ============================================================================
# FINANCE & CUSTOMER REPORTS
# ============================================================================


@router.get("/finance/expense-vs-revenue")
async def expense_vs_revenue(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Expense vs revenue comparison"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()

    if order_repo is None:
        return {"revenue": 0, "cost": 0, "profit": 0, "margin_percent": 0}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )

    revenue = sum(_order_revenue(o) for o in orders)
    cost = 0.0

    for order in orders:
        for item in order.get("items", []):
            try:
                unit_cost = float(item.get("cost_price") or 0)
                qty = float(item.get("quantity") or 1)
                cost += unit_cost * qty
            except (TypeError, ValueError):
                continue

    profit = revenue - cost
    margin_percent = (profit / revenue * 100) if revenue > 0 else 0

    return {
        "revenue": round(revenue, 2),
        "cost": round(cost, 2),
        "profit": round(profit, 2),
        "margin_percent": round(margin_percent, 2),
    }


@router.get("/customers/acquisition")
async def customer_acquisition(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Customer acquisition/retention report"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    customer_repo = get_customer_repository()
    order_repo = get_order_repository()

    if customer_repo is None:
        return {
            "new_customers": 0,
            "returning_customers": 0,
            "total_customers": 0,
            "retention_percent": 0,
        }

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())

    # Get all customers (small enough N to walk in-process)
    all_customers = customer_repo.find_many({"store_id": active_store}, limit=0) or []

    # New customers — created_at within window. Mongo stamps `created_at`
    # as a real datetime, but legacy seeds may have it as ISO string.
    def _in_window(ca) -> bool:
        if isinstance(ca, datetime):
            return from_dt <= ca <= to_dt
        if isinstance(ca, str) and len(ca) >= 10:
            return from_dt.date().isoformat() <= ca[:10] <= to_dt.date().isoformat()
        return False

    new_customers = len([c for c in all_customers if _in_window(c.get("created_at"))])

    # Returning customers: placed >1 order in the window.
    returning_customers = 0
    total_buyers = 0
    if order_repo:
        orders = _orders_in_window(
            order_repo,
            store_id=active_store,
            start_dt=from_dt,
            end_dt=to_dt,
        )
        repeat_customers: dict = {}
        for order in orders:
            cust_id = order.get("customer_id")
            if cust_id:
                repeat_customers[cust_id] = repeat_customers.get(cust_id, 0) + 1
        total_buyers = len(repeat_customers)
        returning_customers = sum(1 for n in repeat_customers.values() if n > 1)

    # Retention% = returning customers as a share of all unique buyers
    # in the window. The previous formula divided by `new_customers`,
    # which produced > 100% whenever a returning buyer wasn't also a
    # new signup.
    retention_percent = (
        round(returning_customers / total_buyers * 100, 2) if total_buyers > 0 else 0.0
    )

    return {
        "new_customers": new_customers,
        "returning_customers": returning_customers,
        "total_customers": len(all_customers),
        "retention_percent": retention_percent,
    }


@router.get("/inventory/brand-sellthrough")
async def brand_sellthrough(
    store_id: Optional[str] = Query(None),
    from_date: date = Query(...),
    to_date: date = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Brand-wise sell-through report"""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    order_repo = get_order_repository()
    stock_repo = get_stock_repository()

    if order_repo is None:
        return {"data": [], "summary": {}}

    from_dt = datetime.combine(from_date, datetime.min.time())
    to_dt = datetime.combine(to_date, datetime.max.time())
    orders = _orders_in_window(
        order_repo,
        store_id=active_store,
        start_dt=from_dt,
        end_dt=to_dt,
    )

    # Track brand sales (uses _item_revenue helper to handle the
    # current item_total/unit_price schema + legacy fall-throughs).
    by_brand = {}
    for order in orders:
        for item in order.get("items", []):
            brand = item.get("brand", "Unbranded")
            if brand not in by_brand:
                by_brand[brand] = {
                    "brand": brand,
                    "quantity_sold": 0,
                    "revenue": 0,
                    "avg_price": 0,
                    "sellthrough_percent": 0,
                }
            by_brand[brand]["quantity_sold"] += int(item.get("quantity") or 1)
            by_brand[brand]["revenue"] += _item_revenue(item)

    # Calculate average price
    for brand in by_brand.values():
        if brand["quantity_sold"] > 0:
            brand["avg_price"] = round(brand["revenue"] / brand["quantity_sold"], 2)

    # Get current stock by brand
    if stock_repo:
        current_stock = stock_repo.find_many({"store_id": active_store}, limit=0)
        by_brand_stock = {}
        for item in current_stock:
            brand = item.get("brand", "Unbranded")
            if brand not in by_brand_stock:
                by_brand_stock[brand] = 0
            by_brand_stock[brand] += item.get("quantity", 0)

        # Calculate sell-through percent
        for brand in by_brand.values():
            if brand["brand"] in by_brand_stock:
                total_stock = brand["quantity_sold"] + by_brand_stock[brand["brand"]]
                brand["sellthrough_percent"] = (
                    round((brand["quantity_sold"] / total_stock * 100), 2)
                    if total_stock > 0
                    else 0
                )

    return {
        "data": list(by_brand.values()),
        "summary": {
            "total_brands": len(by_brand),
            "total_quantity_sold": sum(b["quantity_sold"] for b in by_brand.values()),
            "total_revenue": round(sum(b["revenue"] for b in by_brand.values()), 2),
        },
    }


# ============================================================================
# SALES TARGETS ENDPOINT
# ============================================================================


@router.get("/targets")
async def get_targets(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Get sales targets for a store (daily and monthly).
    Returns configurable defaults or targets from database.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"

    # Default targets
    targets = {
        "store_id": active_store,
        "daily_target": 50000,
        "monthly_target": 1500000,
        "currency": "INR",
        "period": now_ist().strftime("%Y-%m"),
        "created_at": datetime.now().isoformat(),
    }

    # Fetch from targets collection in database if available
    try:
        db = get_db()
        if db:
            targets_coll = db.get_collection("targets")
            stored = targets_coll.find_one({"store_id": active_store})
            if stored:
                stored.pop("_id", None)
                targets.update(stored)
    except Exception:
        pass  # Fall back to defaults

    return targets


# ============================================================================
# GST RETURNS - GSTR-1 (Outward Supplies)
# ============================================================================


def _get_raw_db():
    """Get raw MongoDB database object for aggregation queries."""
    try:
        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:
        pass
    return None


def _order_taxable_and_tax(order: dict) -> tuple:
    """Derive (taxable_value, total_tax) for GST returns from an order doc.

    Persisted order docs carry `subtotal` (the PRE-cart-discount GROSS sum,
    NOT the taxable base), `tax_amount` (total GST), and `grand_total` (what
    the customer actually pays). `orders._compute_per_category_gst` guarantees
    `taxable + tax == grand_total` in BOTH inclusive and exclusive modes, so
    the correct GST taxable value is `grand_total - tax_amount` -- NOT
    `subtotal`, which overstates when a cart discount applies or under
    inclusive pricing.

    Real orders have NO top-level `taxable` / `taxable_amount` field (those
    legacy names never landed) -- reading them returned 0 and zeroed out every
    GSTR-1 / GSTR-3B / GST-report taxable value. This was the bug. Resolution
    order (first usable signal wins):
      Tax total: explicit top-level `tax` (authoritative when present) ->
        `tax_amount` (what orders.py persists) -> `tax_total`.
      Taxable:
        1. Explicit top-level `taxable` / `taxable_amount` if present -- kept
           for backward compatibility with any doc / fixture that stamps them;
           for a well-formed order this equals grand_total - tax.
        2. grand_total - tax (the canonical derivation for real orders).
        3. Per-line `taxable_value` / `tax_amount` sums when the top-level
           totals are absent (e.g. partial legacy rows).
    """

    def _f(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    # Total GST. An explicit top-level `tax` (the sibling of an explicit
    # `taxable`) is authoritative when present; real orders don't carry it and
    # fall through to `tax_amount` (what orders.py persists) then `tax_total`.
    if order.get("tax") is not None:
        total_tax = _f(order.get("tax"))
    elif order.get("tax_amount") is not None:
        total_tax = _f(order.get("tax_amount"))
    else:
        total_tax = _f(order.get("tax_total"))

    # (1) Explicit top-level taxable wins when present (legacy / fixtures).
    if order.get("taxable") is not None or order.get("taxable_amount") is not None:
        explicit = order.get("taxable")
        if explicit is None:
            explicit = order.get("taxable_amount")
        return round(_f(explicit), 2), round(total_tax, 2)

    grand_total = (
        _f(order.get("grand_total"))
        if order.get("grand_total") is not None
        else _f(order.get("total_amount"))
    )

    # Per-line fallback data.
    line_taxable = 0.0
    line_tax = 0.0
    has_line_data = False
    for it in order.get("items") or []:
        if not isinstance(it, dict):
            continue
        if it.get("taxable_value") is not None or it.get("tax_amount") is not None:
            has_line_data = True
            line_taxable += _f(it.get("taxable_value"))
            line_tax += _f(it.get("tax_amount"))

    # (2) Canonical derivation: taxable = grand_total - tax_amount.
    if grand_total > 0:
        # If top-level tax is missing but the lines carry it, trust the lines.
        if total_tax <= 0 and has_line_data and line_tax > 0:
            total_tax = line_tax
        return round(grand_total - total_tax, 2), round(total_tax, 2)

    # (3) No grand_total -> per-line sums.
    if has_line_data:
        return round(line_taxable, 2), round(line_tax, 2)

    return 0.0, round(total_tax, 2)


def _b2cs_rate_lines(items, order_taxable, order_tax):
    """NEW-GST-B2CS-HSN: split a consumer (B2CS) order's lines into
    ``(gst_rate, taxable, tax)`` tuples, one per line, so an invoice that mixes
    GST rates (e.g. a 5% frame + an 18% sunglass) lands in the correct per-rate
    B2CS bucket instead of being lumped under the first line's rate.

    Each line's rate is ``item.gst_rate`` when present, else the canonical
    category rate (api.services.gst_rates). Taxable is derived GST-exclusively
    from the line gross ``item_total`` (Indian retail prices are GST-inclusive):
    ``taxable = item_total * 100 / (100 + rate)``. When no usable line items
    exist, fall back to a single line carrying the order-level totals so nothing
    is dropped.
    """
    from api.services.gst_rates import GST_CATEGORY_TABLE, _normalize_category

    lines = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        try:
            gross = float(it.get("item_total") or 0) or 0.0
        except (TypeError, ValueError):
            gross = 0.0
        if gross <= 0:
            continue
        rate = it.get("gst_rate")
        if rate is None:
            entry = GST_CATEGORY_TABLE.get(_normalize_category(it.get("category")))
            rate = entry[1] if entry else 5.0
        try:
            rate = int(round(float(rate)))
        except (TypeError, ValueError):
            rate = 5
        taxable = round(gross * 100.0 / (100.0 + rate), 2)
        lines.append((rate, taxable, round(gross - taxable, 2)))

    if not lines:
        # No usable line items -> keep the order-level totals under one bucket.
        return [(5, round(order_taxable, 2), round(order_tax, 2))]
    return lines


def _compute_gstr1(month: str, active_store: str) -> dict:
    """Compute the IMS GSTR-1 report dict for a (month, store).

    Extracted from the `/gstr1` endpoint so both the JSON-report endpoint
    and the GSTN portal-export endpoint can share the SAME aggregation
    (no duplicated query logic). Pure-ish: reads MongoDB, returns a dict.
    Raises HTTPException(400) only on a malformed `month`.

    Classifies invoices into:
      - B2B  : orders where the customer has a GSTIN on file
      - B2CL : orders to consumers with invoice value > 250000
      - B2CS : consolidated summary of remaining consumer invoices

    Field-name fixes:
      - Reads `grand_total` (not legacy `total_amount`) and `tax_amount`
        (total GST) off the order; there is NO top-level `taxable` /
        `taxable_amount` field, so the taxable value is derived as
        grand_total - tax_amount (with a per-line `taxable_value` fallback)
        via _order_taxable_and_tax. orders._compute_per_category_gst
        guarantees taxable + tax == grand_total in inclusive AND exclusive
        modes, so this is exact.
      - Stores carry their own `state` + `gstin` in the `stores`
        collection — used to derive intra-state (CGST+SGST) vs
        inter-state (IGST) splits and to fill the GSTIN/legalName
        header. When the store row is absent, fallback is single-state
        chain assumption (all sales intra-state, tax split 50/50).

    Returns empty lists/summaries when no data exists for the period.
    Validation report (`validation`) flags B2B invoices missing GSTIN
    so the CA can fix them before downloading.
    """
    # Parse month to date range
    try:
        year, mon = int(month[:4]), int(month[5:7])
        _, last_day = monthrange(year, mon)
        from_dt = datetime(year, mon, 1, 0, 0, 0)
        to_dt = datetime(year, mon, last_day, 23, 59, 59)
    except Exception:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")

    b2b: list = []
    b2cl: list = []
    b2cs_map: dict = {}
    validation_issues: list = []

    # Store header (gstin + legalName + home state for intra/inter split)
    store_gstin = ""
    store_legal_name = ""
    store_state = ""

    db = _get_raw_db()
    if db is not None:
        try:
            stores_col = db["stores"]
            store_doc = stores_col.find_one({"store_id": active_store})
            if store_doc:
                store_gstin = str(store_doc.get("gstin", "") or "")
                store_legal_name = str(
                    store_doc.get("store_name") or store_doc.get("name", "") or ""
                )
                store_state = str(store_doc.get("state", "") or "")
        except Exception:
            pass

        try:
            orders_col = db["orders"]
            customers_col = db["customers"]

            # Build a lookup: customer_id -> {gstin, name, state}
            cust_map: dict = {}
            try:
                for cust in customers_col.find(
                    {},
                    {"customer_id": 1, "gstin": 1, "name": 1, "state": 1},
                ):
                    cust_map[str(cust.get("customer_id", ""))] = {
                        "gstin": str(cust.get("gstin", "") or ""),
                        "name": str(cust.get("name", "") or ""),
                        "state": str(cust.get("state", "") or ""),
                    }
            except Exception:
                pass

            # Fetch completed orders in the date range. Filter uses real
            # datetime objects to match BaseRepository which writes
            # `created_at` as a Date — comparing a Date to an ISO string
            # silently never matches (this was the original GSTR bug).
            query = {
                "store_id": active_store,
                "status": {"$nin": ["CANCELLED", "DRAFT", "cancelled", "draft"]},
                "created_at": {"$gte": from_dt, "$lte": to_dt},
            }

            for order in orders_col.find(query):
                cust_id = str(order.get("customer_id", ""))
                cust_info = cust_map.get(cust_id, {})
                customer_gstin = cust_info.get("gstin", "")
                customer_state = cust_info.get("state", "") or store_state
                customer_name = (
                    cust_info.get("name", "")
                    or order.get("customer_name", "")
                    or "Walk-in Customer"
                )

                # PRIMARY field map. Orders persist `grand_total` (what the
                # customer pays) + `tax_amount` (total GST); there is NO
                # top-level `taxable` / `taxable_amount` field, so the taxable
                # value is derived as grand_total - tax_amount (with a per-line
                # taxable_value fallback). See _order_taxable_and_tax.
                invoice_value = float(
                    order.get("grand_total", order.get("total_amount", 0)) or 0
                )
                taxable_value, total_tax = _order_taxable_and_tax(order)

                # Intra vs inter-state split. We don't have CGST/SGST/IGST
                # split on the order doc, so derive it from store_state vs
                # customer_state. When customer_state is empty (walk-in
                # without state on file), assume same as store (intra).
                is_inter_state = bool(
                    store_state
                    and customer_state
                    and store_state.strip().lower() != customer_state.strip().lower()
                )
                if is_inter_state:
                    igst = round(total_tax, 2)
                    cgst = 0.0
                    sgst = 0.0
                else:
                    cgst = round(total_tax / 2, 2)
                    sgst = round(total_tax / 2, 2)
                    igst = 0.0

                bill_number = order.get(
                    "invoice_number",
                    order.get("bill_number", order.get("order_number", ""))
                ) or order.get("order_id", "")
                created_raw = order.get("created_at", "")
                invoice_date = str(created_raw)[:10] if created_raw else month + "-01"
                place_of_supply = customer_state or store_state or "Unknown"

                # HSN: pull from the first line item if available; fallback
                # to 9004 (frames/lenses default per CBIC). GSTR-1 row-level
                # HSN is acceptable; section 12 HSN summary is computed
                # separately below.
                hsn_code = "9004"
                gst_rate_dominant = 5
                items = order.get("items") or []
                if items:
                    first = items[0] if isinstance(items[0], dict) else {}
                    if first.get("hsn_code"):
                        hsn_code = str(first.get("hsn_code"))
                    if first.get("gst_rate") is not None:
                        try:
                            gst_rate_dominant = int(first.get("gst_rate"))
                        except Exception:
                            pass

                base_invoice = {
                    "invoiceNumber": bill_number,
                    "invoiceDate": invoice_date,
                    "customerName": customer_name,
                    "placeOfSupply": place_of_supply,
                    "invoiceValue": round(invoice_value, 2),
                    "taxableValue": round(taxable_value, 2),
                    "cgst": cgst,
                    "sgst": sgst,
                    "igst": igst,
                    "totalTax": round(total_tax, 2),
                    "hsnCode": hsn_code,
                    "gstRate": gst_rate_dominant,
                }

                if customer_gstin:
                    # B2B: registered business with GSTIN
                    b2b.append(
                        {
                            **base_invoice,
                            "customerGSTIN": customer_gstin,
                            "customerState": customer_state or store_state,
                        }
                    )
                elif invoice_value > 250000:
                    # B2CL: large consumer invoice (> ₹2.5L)
                    b2cl.append(
                        {
                            **base_invoice,
                            "customerState": customer_state or store_state,
                        }
                    )
                    # An "out-of-state" B2CL with no customer_state is
                    # technically required to have one — flag it.
                    if invoice_value > 250000 and not customer_state:
                        validation_issues.append(
                            {
                                "level": "warn",
                                "invoice": bill_number,
                                "issue": "B2CL invoice missing customer state",
                            }
                        )
                else:
                    # B2CS: consolidate by (place_of_supply, gst_rate). NEW-GST-B2CS-HSN:
                    # an invoice can mix GST rates (e.g. a 5% frame + an 18%
                    # sunglass); split each line into the correct rate bucket
                    # instead of lumping the whole invoice under the first line's
                    # rate. Tax is split CGST/SGST (intra) or IGST (inter) per line.
                    for rate, line_taxable, line_tax in _b2cs_rate_lines(
                        items, taxable_value, total_tax
                    ):
                        key = f"{place_of_supply}|{rate}"
                        if key not in b2cs_map:
                            b2cs_map[key] = {
                                "placeOfSupply": place_of_supply,
                                "gstRate": rate,
                                "taxableValue": 0.0,
                                "cgst": 0.0,
                                "sgst": 0.0,
                                "igst": 0.0,
                                "totalTax": 0.0,
                            }
                        b2cs_map[key]["taxableValue"] += line_taxable
                        b2cs_map[key]["totalTax"] += line_tax
                        if is_inter_state:
                            b2cs_map[key]["igst"] += line_tax
                        else:
                            b2cs_map[key]["cgst"] += round(line_tax / 2, 2)
                            b2cs_map[key]["sgst"] += round(line_tax / 2, 2)

                # Validation: B2B without GSTIN — caught by the absence
                # of customer_gstin above. Add an explicit warning for
                # high-value invoices missing it.
                if invoice_value > 250000 and not customer_gstin:
                    validation_issues.append(
                        {
                            "level": "info",
                            "invoice": bill_number,
                            "issue": "Invoice > ₹2.5L without customer GSTIN — confirm B2C status",
                        }
                    )

        except Exception:
            pass

    b2cs = [
        {
            **v,
            "taxableValue": round(v["taxableValue"], 2),
            "cgst": round(v["cgst"], 2),
            "sgst": round(v["sgst"], 2),
            "igst": round(v["igst"], 2),
            "totalTax": round(v["totalTax"], 2),
        }
        for v in b2cs_map.values()
    ]

    # CDNR (Credit/Debit Notes Register) and HSN summary
    cdnr: list = []
    hsn_by_rate: dict = {}

    if db is not None:
        # Process credit notes from returns/refunds in credit_note_ledger
        try:
            ledger_col = db.get_collection("credit_note_ledger") or db["credit_note_ledger"]
            if ledger_col is not None:
                # Query credit notes issued in the period
                ledger_query = {
                    "store_id": active_store,
                    "type": "ISSUED",
                    "created_at": {"$gte": from_dt.isoformat(), "$lte": to_dt.isoformat()},
                }
                # Allow for date stored as datetime or ISO string
                try:
                    for entry in ledger_col.find(ledger_query):
                        if not isinstance(entry, dict):
                            continue
                        # Credit notes reduce GST liability
                        cust_id = str(entry.get("customer_id", ""))
                        cust_info = cust_map.get(cust_id, {})
                        cust_gstin = cust_info.get("gstin", "")
                        cust_state = cust_info.get("state", "") or store_state

                        # Derive tax from gross/net refund (net = gross - tax)
                        gross_refund = float(entry.get("gross_refund") or 0.0)
                        net_refund = float(entry.get("net_refund", entry.get("amount", 0)) or 0.0)
                        cn_tax = round(gross_refund - net_refund, 2) if gross_refund > 0 else 0.0

                        # Split CGST/SGST vs IGST by comparing states
                        is_inter = bool(
                            store_state
                            and cust_state
                            and store_state.strip().lower() != cust_state.strip().lower()
                        )
                        if is_inter:
                            cn_igst = round(cn_tax, 2)
                            cn_cgst = 0.0
                            cn_sgst = 0.0
                        else:
                            cn_cgst = round(cn_tax / 2, 2)
                            cn_sgst = round(cn_tax / 2, 2)
                            cn_igst = 0.0

                        # Reference to the return (e.g., RET-250415-ABC123)
                        ref = str(entry.get("ref", entry.get("entry_id", "")) or "")
                        cn_date = str(entry.get("created_at", ""))[:10] if entry.get("created_at") else month + "-01"

                        # Assume credit notes use the same HSN/rate as the items returned
                        cn_hsn = "9004"
                        cn_rate = 18
                        cn_place = cust_state or store_state or "Unknown"

                        cn_entry = {
                            "refReference": ref,
                            "creditNoteDate": cn_date,
                            "customerId": cust_id,
                            "customerName": cust_info.get("name", ""),
                            "customerGSTIN": cust_gstin,
                            "customerState": cn_place,
                            "placeOfSupply": cn_place,
                            "grossValue": round(gross_refund, 2),
                            "taxableValue": round(net_refund, 2),
                            "cgst": cn_cgst,
                            "sgst": cn_sgst,
                            "igst": cn_igst,
                            "taxValue": round(cn_tax, 2),
                            "hsnCode": cn_hsn,
                            "gstRate": cn_rate,
                        }
                        cdnr.append(cn_entry)
                except Exception:
                    pass
        except Exception:
            pass

    # Build HSN summary by aggregating sales and deducting credit notes
    for inv in b2b + b2cl:
        if not isinstance(inv, dict):
            continue
        hsn = str(inv.get("hsnCode", "9004"))
        rate = float(inv.get("gstRate", 0))
        key = f"{hsn}|{rate}"
        if key not in hsn_by_rate:
            hsn_by_rate[key] = {
                "hsnCode": hsn,
                "gstRate": int(rate),
                "taxableValue": 0.0,
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 0.0,
            }
        hsn_by_rate[key]["taxableValue"] += float(inv.get("taxableValue", 0))
        hsn_by_rate[key]["cgst"] += float(inv.get("cgst", 0))
        hsn_by_rate[key]["sgst"] += float(inv.get("sgst", 0))
        hsn_by_rate[key]["igst"] += float(inv.get("igst", 0))

    for b2cs_row in b2cs:
        if not isinstance(b2cs_row, dict):
            continue
        hsn = "9004"
        rate = float(b2cs_row.get("gstRate", 0))
        key = f"{hsn}|{rate}"
        if key not in hsn_by_rate:
            hsn_by_rate[key] = {
                "hsnCode": hsn,
                "gstRate": int(rate),
                "taxableValue": 0.0,
                "cgst": 0.0,
                "sgst": 0.0,
                "igst": 0.0,
            }
        hsn_by_rate[key]["taxableValue"] += float(b2cs_row.get("taxableValue", 0))
        hsn_by_rate[key]["cgst"] += float(b2cs_row.get("cgst", 0))
        hsn_by_rate[key]["sgst"] += float(b2cs_row.get("sgst", 0))
        hsn_by_rate[key]["igst"] += float(b2cs_row.get("igst", 0))

    for cn in cdnr:
        if not isinstance(cn, dict):
            continue
        hsn = str(cn.get("hsnCode", "9004"))
        rate = float(cn.get("gstRate", 0))
        key = f"{hsn}|{rate}"
        if key in hsn_by_rate:
            hsn_by_rate[key]["taxableValue"] -= float(cn.get("taxableValue", 0))
            hsn_by_rate[key]["cgst"] -= float(cn.get("cgst", 0))
            hsn_by_rate[key]["sgst"] -= float(cn.get("sgst", 0))
            hsn_by_rate[key]["igst"] -= float(cn.get("igst", 0))

    hsn_summary = [
        {
            "hsnCode": v["hsnCode"],
            "gstRate": v["gstRate"],
            "taxableValue": round(max(0, v["taxableValue"]), 2),
            "cgst": round(max(0, v["cgst"]), 2),
            "sgst": round(max(0, v["sgst"]), 2),
            "igst": round(max(0, v["igst"]), 2),
        }
        for v in hsn_by_rate.values()
        if v["taxableValue"] > 0 or v["cgst"] > 0 or v["sgst"] > 0 or v["igst"] > 0
    ]

    total_invoices = len(b2b) + len(b2cl) + len(b2cs_map)
    total_taxable = (
        sum(i["taxableValue"] for i in b2b)
        + sum(i["taxableValue"] for i in b2cl)
        + sum(v["taxableValue"] for v in b2cs)
    )
    total_tax = (
        sum(i["totalTax"] for i in b2b)
        + sum(i["totalTax"] for i in b2cl)
        + sum(v["totalTax"] for v in b2cs)
    )

    return {
        "period": month,
        "gstin": store_gstin,
        "legalName": store_legal_name,
        "storeState": store_state,
        "totalInvoices": total_invoices,
        "totalTaxableValue": round(total_taxable, 2),
        "totalTax": round(total_tax, 2),
        "b2b": b2b,
        "b2cl": b2cl,
        "b2cs": b2cs,
        "cdnr": cdnr,
        "hsnSummary": hsn_summary,
        "validation": {
            "ok": len(validation_issues) == 0,
            "issueCount": len(validation_issues),
            "issues": validation_issues[:50],
        },
    }


@router.get("/gstr1")
async def gstr1_report(
    month: str = Query(..., description="Tax period in YYYY-MM format"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """GSTR-1 report (IMS internal shape). See _compute_gstr1 for details."""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    return _compute_gstr1(month, active_store)


@router.get("/gstr1/gstn-json")
async def gstr1_gstn_json(
    month: str = Query(..., description="Tax period in YYYY-MM format"),
    year: Optional[int] = Query(
        None, description="Optional year; combined with `month` as a number when given"
    ),
    store_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(
        None, description="Reserved — entity-level rollup not yet wired; store_id wins"
    ),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """GSTR-1 shaped for the GST portal's offline upload tool.

    Reuses _compute_gstr1 (no duplicated aggregation) and runs the pure
    mapping in services/gstn_export.py. The accountant uploads the
    resulting JSON via gst.gov.in -> Returns Offline Tool -> Import.

    `month` accepts the IMS canonical "YYYY-MM". For convenience a numeric
    `month` (1-12) plus `year` is also accepted and normalised. `entity_id`
    is accepted for forward-compat but store_id remains the resolution key.
    """
    from ..services.gstn_export import to_gstr1_json

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    period = _normalise_period(month, year)
    data = _compute_gstr1(period, active_store)
    try:
        return to_gstr1_json(data, gstin=data.get("gstin", ""), period=period)
    except Exception:
        # Fail soft: never 500 on a shaping bug — return an empty skeleton.
        return to_gstr1_json({}, gstin="", period=period)


# ============================================================================
# GST RETURNS - GSTR-3B (Summary Return)
# ============================================================================


def _itc_from_vendor_bills(db, active_store, year, mon, last_day):
    """BUG-138: ITC available for a month from recorded PURCHASE INVOICES
    (vendor_bills cgst/sgst/igst_total), scoped to the store's entity. Returns
    (igst, cgst, sgst). The old code summed the `grns` collection -- quantity-only
    with NO tax fields -- so ITC was always 0 and the business over-paid GST.
    invoice_date/bill_date are ISO-date STRINGS, matched with string month bounds.
    Fail-soft -> (0.0, 0.0, 0.0)."""
    if db is None:
        return 0.0, 0.0, 0.0
    try:
        entity_id = None
        try:
            _srow = db["stores"].find_one({"store_id": active_store}, {"entity_id": 1})
            entity_id = (_srow or {}).get("entity_id")
        except Exception:
            entity_id = None
        month_lo = f"{year:04d}-{mon:02d}-01"
        month_hi = f"{year:04d}-{mon:02d}-{last_day:02d}T23:59:59"
        vb_match: dict = {
            "status": {"$nin": ["CANCELLED", "cancelled", "VOID", "voided"]},
            "itc_eligible": {"$ne": False},
            "$or": [
                {"invoice_date": {"$gte": month_lo, "$lte": month_hi}},
                {"bill_date": {"$gte": month_lo, "$lte": month_hi}},
            ],
        }
        if entity_id:
            vb_match["recipient_entity_id"] = entity_id
        pipeline = [
            {"$match": vb_match},
            {
                "$group": {
                    "_id": None,
                    "igst": {"$sum": "$igst_total"},
                    "cgst": {"$sum": "$cgst_total"},
                    "sgst": {"$sum": "$sgst_total"},
                }
            },
        ]
        res = list(db["vendor_bills"].aggregate(pipeline))
        if res:
            a = res[0]
            return (
                float(a.get("igst", 0.0) or 0.0),
                float(a.get("cgst", 0.0) or 0.0),
                float(a.get("sgst", 0.0) or 0.0),
            )
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def _compute_gstr3b(month: str, active_store: str) -> dict:
    """Compute the IMS GSTR-3B report dict for a (month, store).

    Extracted from the `/gstr3b` endpoint so the JSON-report endpoint and
    the GSTN portal-export endpoint share the SAME aggregation. Reads
    MongoDB, returns a dict. Raises HTTPException(400) on a malformed
    `month`.

    Table 3.1 - Outward taxable supplies: derived from completed sales invoices.
    Table 4   - ITC available: derived from recorded purchase invoices
                (vendor_bills cgst/sgst/igst_total), scoped to the store's entity.
                Returns zeros when no purchase data is present.
    Table 6.1 - Payment of tax: net cash liability = output tax - ITC.
    Returns all-zero figures when no data exists for the period.
    """
    try:
        year, mon = int(month[:4]), int(month[5:7])
        _, last_day = monthrange(year, mon)
        from_dt = datetime(year, mon, 1, 0, 0, 0)
        to_dt = datetime(year, mon, last_day, 23, 59, 59)
    except Exception:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")

    # Output tax accumulators
    out_igst = 0.0
    out_cgst = 0.0
    out_sgst = 0.0
    out_taxable = 0.0

    # ITC accumulators (from purchase GRNs)
    itc_igst = 0.0
    itc_cgst = 0.0
    itc_sgst = 0.0

    store_gstin = ""
    store_legal_name = ""
    store_state = ""

    db = _get_raw_db()
    if db is not None:
        try:
            stores_col = db["stores"]
            store_doc = stores_col.find_one({"store_id": active_store})
            if store_doc:
                store_gstin = str(store_doc.get("gstin", "") or "")
                store_legal_name = str(
                    store_doc.get("store_name") or store_doc.get("name", "") or ""
                )
                store_state = str(store_doc.get("state", "") or "")
        except Exception:
            pass

        try:
            # --- Output tax from orders (Phase I-5 field-name fix).
            #     Orders stamp `taxable` (taxable value) and `tax`
            #     (total GST), via _compute_per_category_gst — there are
            #     no `taxable_amount` / `cgst_amount` etc. fields. We
            #     split tax by deriving intra/inter from store vs
            #     customer state, in the same loop as GSTR-1 above.
            orders_col = db["orders"]
            customers_col = db["customers"]

            cust_state_map: dict = {}
            try:
                for cust in customers_col.find({}, {"customer_id": 1, "state": 1}):
                    cust_state_map[str(cust.get("customer_id", ""))] = str(
                        cust.get("state", "") or ""
                    )
            except Exception:
                pass

            for order in orders_col.find(
                {
                    "store_id": active_store,
                    "status": {"$nin": ["CANCELLED", "DRAFT", "cancelled", "draft"]},
                    "created_at": {"$gte": from_dt, "$lte": to_dt},
                }
            ):
                # Orders carry `tax_amount` + `grand_total`, NOT `taxable` /
                # `taxable_amount`; derive taxable = grand_total - tax_amount.
                taxable, tax = _order_taxable_and_tax(order)
                if taxable <= 0 and tax <= 0:
                    continue
                out_taxable += taxable

                cust_id = str(order.get("customer_id", ""))
                customer_state = cust_state_map.get(cust_id, "") or store_state
                is_inter_state = bool(
                    store_state
                    and customer_state
                    and store_state.strip().lower() != customer_state.strip().lower()
                )
                if is_inter_state:
                    out_igst += tax
                else:
                    out_cgst += tax / 2
                    out_sgst += tax / 2
        except Exception:
            pass

        # BUG-138: ITC from recorded purchase invoices (vendor_bills), not the
        # qty-only `grns` collection that made ITC always 0 (-> GST over-paid).
        itc_igst, itc_cgst, itc_sgst = _itc_from_vendor_bills(
            db, active_store, year, mon, last_day
        )

    # Net cash liability = output tax - ITC (floor at 0 per component)
    cash_igst = max(0.0, out_igst - itc_igst)
    cash_cgst = max(0.0, out_cgst - itc_cgst)
    cash_sgst = max(0.0, out_sgst - itc_sgst)

    def _r(v: float) -> float:
        return round(v, 2)

    return {
        "period": month,
        "gstin": store_gstin,
        "legalName": store_legal_name,
        "storeState": store_state,
        "outwardTaxableValue": _r(out_taxable),
        "outwardTaxableSupplies": {
            "integratedTax": _r(out_igst),
            "centralTax": _r(out_cgst),
            "stateTax": _r(out_sgst),
            "cess": 0.0,
        },
        "zeroRatedValue": 0.0,
        "zeroRatedSupplies": {
            "integratedTax": 0.0,
            "centralTax": 0.0,
            "stateTax": 0.0,
            "cess": 0.0,
        },
        "itcAvailable": {
            "integratedTax": _r(itc_igst),
            "centralTax": _r(itc_cgst),
            "stateTax": _r(itc_sgst),
            "cess": 0.0,
        },
        "exemptSupplies": 0.0,
        "taxPayable": {
            "integratedTax": _r(out_igst),
            "centralTax": _r(out_cgst),
            "stateTax": _r(out_sgst),
            "cess": 0.0,
        },
        "itcUtilized": {
            "integratedTax": _r(itc_igst),
            "centralTax": _r(itc_cgst),
            "stateTax": _r(itc_sgst),
            "cess": 0.0,
        },
        "taxPaidCash": {
            "integratedTax": _r(cash_igst),
            "centralTax": _r(cash_cgst),
            "stateTax": _r(cash_sgst),
            "cess": 0.0,
        },
        "interest": {
            "integratedTax": 0.0,
            "centralTax": 0.0,
            "stateTax": 0.0,
            "cess": 0.0,
        },
        "lateFee": 0.0,
    }


def _normalise_period(month: str, year: Optional[int] = None) -> str:
    """Normalise the (month, year) query params into IMS canonical "YYYY-MM".

    Accepts:
      - month="YYYY-MM"           -> returned as-is
      - month="MM" or "M" + year  -> "{year}-{MM}"
      - month="MMYYYY"            -> "{YYYY}-{MM}"
    Falls back to returning `month` unchanged so the downstream parser can
    raise a clean 400 on genuinely bad input.
    """
    m = (month or "").strip()
    if len(m) >= 7 and m[4] == "-":
        return m  # already YYYY-MM[-DD]
    if year is not None and m.isdigit() and 1 <= int(m) <= 12:
        return f"{int(year):04d}-{int(m):02d}"
    if len(m) == 6 and m.isdigit():  # MMYYYY
        return f"{m[2:]}-{m[:2]}"
    return m


@router.get("/gstr3b")
async def gstr3b_report(
    month: str = Query(..., description="Tax period in YYYY-MM format"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """GSTR-3B summary return (IMS internal shape). See _compute_gstr3b."""
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    return _compute_gstr3b(month, active_store)


@router.get("/gstr3b/gstn-json")
async def gstr3b_gstn_json(
    month: str = Query(..., description="Tax period in YYYY-MM format"),
    year: Optional[int] = Query(
        None, description="Optional year; combined with `month` as a number when given"
    ),
    store_id: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(
        None, description="Reserved — entity-level rollup not yet wired; store_id wins"
    ),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """GSTR-3B shaped for the GST portal's offline upload tool.

    Reuses _compute_gstr3b (no duplicated aggregation) and runs the pure
    mapping in services/gstn_export.py.
    """
    from ..services.gstn_export import to_gstr3b_json

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    period = _normalise_period(month, year)
    data = _compute_gstr3b(period, active_store)
    try:
        return to_gstr3b_json(data, gstin=data.get("gstin", ""), period=period)
    except Exception:
        return to_gstr3b_json({}, gstin="", period=period)


# ============================================================================
# NON-MOVING STOCK REPORT (Phase 6.3)
# ============================================================================
# "Which SKUs are tying up cash without turning over?" — core question for
# an optical retailer doing monthly clearance decisions. Anything that
# hasn't sold in 90+ days is a candidate for discount, transfer, or return.
# Finance dashboards traditionally pull this as "dead stock" aging.


@router.get("/inventory/non-moving-stock")
async def non_moving_stock(
    store_id: Optional[str] = Query(None),
    days: int = Query(
        90, ge=1, le=365, description="Products with no sale in the last N days"
    ),
    limit: int = Query(200, ge=1, le=1000),
    current_user: dict = Depends(get_current_user),
):
    """
    Products that haven't sold in the last N days (default 90).

    Returns one row per stale product, sorted by most stale first. A
    product that has NEVER sold is surfaced at the top with
    `never_sold: true` and `days_since_sold: null`. `last_sold_at` is the
    ISO timestamp of the most recent non-cancelled order containing the
    product.

    Response shape:
        {
            "data": [ {product_id, sku, brand, model, category, mrp,
                        last_sold_at, days_since_sold, never_sold,
                        total_sold_all_time} ... ],
            "count": int,
            "as_of": ISO timestamp,
            "days_threshold": int,
            "store_id": str,
        }

    Edge cases:
      - DB unavailable -> returns empty data + 0 count. Does not raise.
      - Product has sale timestamp in a format we can't parse -> treated
        as never_sold (conservative: surface it rather than hide it).
      - `days=1` gives you yesterday's dead pile; `days=365` gives you
        the full year of no-movement.
    """
    from datetime import timezone  # local import, keeps module-top imports minimal

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    db = get_db()

    if db is None or not getattr(db, "is_connected", True):
        return {
            "data": [],
            "count": 0,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "days_threshold": days,
            "store_id": active_store,
        }

    # 1. Build per-product sales summary (last_sold_at + total units sold)
    # from the orders collection using a single aggregation.
    sales_map = {}
    try:
        orders_coll = db.get_collection("orders")
        pipeline = [
            {
                "$match": {
                    "store_id": active_store,
                    "status": {"$nin": ["CANCELLED", "DRAFT"]},
                }
            },
            {"$unwind": "$items"},
            {
                "$group": {
                    "_id": "$items.product_id",
                    "last_sold_at": {"$max": "$created_at"},
                    "total_sold": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
                }
            },
        ]
        for doc in orders_coll.aggregate(pipeline):
            pid = doc.get("_id")
            if pid:
                sales_map[str(pid)] = {
                    "last_sold_at": doc.get("last_sold_at"),
                    "total_sold": doc.get("total_sold") or 0,
                }
    except Exception:
        # If aggregation fails (e.g., no orders collection yet), treat
        # sales_map as empty and every product falls into "never_sold".
        sales_map = {}

    # 2. Walk active products, classify each as "stale" or not.
    try:
        products_coll = db.get_collection("products")
        products = list(products_coll.find({"is_active": True}))
    except Exception:
        products = []

    now = datetime.now(timezone.utc)
    results = []
    for p in products:
        pid = str(p.get("product_id") or p.get("_id") or "")
        s = sales_map.get(pid, {})
        last_sold_at = s.get("last_sold_at")
        total_sold = s.get("total_sold", 0)

        days_since = None
        never_sold = last_sold_at is None
        if last_sold_at is not None:
            try:
                # Mongo may return a datetime or an ISO string depending on
                # how the order was inserted. Handle both.
                if isinstance(last_sold_at, datetime):
                    last_dt = last_sold_at
                else:
                    last_dt = datetime.fromisoformat(
                        str(last_sold_at).replace("Z", "+00:00")
                    )
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                days_since = (now - last_dt).days
            except (ValueError, TypeError):
                days_since = None
                never_sold = True

        if never_sold or (days_since is not None and days_since >= days):
            results.append(
                {
                    "product_id": pid or None,
                    "sku": p.get("sku"),
                    "brand": p.get("brand"),
                    "model": p.get("model"),
                    "category": p.get("category"),
                    "mrp": p.get("mrp") or 0,
                    "last_sold_at": (
                        last_sold_at
                        if isinstance(last_sold_at, str)
                        else (
                            last_sold_at.isoformat()
                            if isinstance(last_sold_at, datetime)
                            else None
                        )
                    ),
                    "days_since_sold": days_since,
                    "never_sold": never_sold,
                    "total_sold_all_time": total_sold,
                }
            )

    # 3. Sort — never-sold first (infinite staleness), then by days desc.
    results.sort(
        key=lambda r: (
            0 if r["never_sold"] else 1,
            -(r["days_since_sold"] or 0),
        )
    )
    results = results[:limit]

    return {
        "data": results,
        "count": len(results),
        "as_of": now.isoformat(),
        "days_threshold": days,
        "store_id": active_store,
    }


# ============================================================================
# TechCherry R1 — Net-new analytics dimensions
# ============================================================================
# Footfall Audit · Price Band Analysis · Lens Deep Dive · Seasonality.
# Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.
#
# These four endpoints fill the only TechCherry sections IMS didn't already
# have. All compute live off Mongo — no XLS importer, no caching layer.
#
# Conventions match the existing /reports/* endpoints:
#   - store_id query param, fall back to user.active_store_id, fall back to "store-001"
#   - Period: year/month for monthly, years_back for multi-year
#   - Response always wraps the payload in a dict with store_id echoed
#   - Empty results never raise — return zeroed structures so the UI
#     can render the cards without conditional logic.


from collections import defaultdict


def _fy_of(dt: datetime) -> str:
    """Indian financial year: Apr 1 - Mar 31. Returns 'FY24-25' style."""
    yr = dt.year
    if dt.month >= 4:
        return f"FY{yr % 100:02d}-{(yr + 1) % 100:02d}"
    return f"FY{(yr - 1) % 100:02d}-{yr % 100:02d}"


_PRICE_BANDS: list[tuple[str, float, float]] = [
    ("<1K", 0, 1000),
    ("1K-2.5K", 1000, 2500),
    ("2.5K-5K", 2500, 5000),
    ("5K-10K", 5000, 10000),
    ("10K-15K", 10000, 15000),
    ("15K-20K", 15000, 20000),
    ("20K-30K", 20000, 30000),
    ("30K-50K", 30000, 50000),
    ("50K-75K", 50000, 75000),
    ("75K-1.5L", 75000, 150000),
    ("1.5L+", 150000, float("inf")),
]
_PRICE_BAND_NAMES: list[str] = [b[0] for b in _PRICE_BANDS]


def _price_band_of(amount: float) -> str:
    """Bucket an amount (₹) into one of the 11 _PRICE_BANDS."""
    for name, lo, hi in _PRICE_BANDS:
        if lo <= amount < hi:
            return name
    return "1.5L+"


def _order_created_at(order: dict) -> Optional[datetime]:
    """Parse created_at as datetime, tolerating both datetime objects and
    ISO strings (some legacy docs store the latter)."""
    ca = order.get("created_at")
    if isinstance(ca, datetime):
        return ca
    if isinstance(ca, str) and ca:
        try:
            return datetime.fromisoformat(ca.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _order_net(order: dict) -> float:
    """Revenue net of tax (the band-relevant figure). Falls back to
    gross revenue if tax_total is unknown."""
    return _order_revenue(order) - _order_tax(order)


# ----------------------------------------------------------------------------
# R1.1 Footfall Audit
# ----------------------------------------------------------------------------


def _month_iter(start: datetime, end: datetime):
    """Yield (year, month) tuples from start month through end month inclusive."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            m, y = 1, y + 1


@router.get("/walkouts/footfall-audit")
async def footfall_audit(
    store_id: Optional[str] = Query(None),
    months_back: int = Query(12, ge=1, le=36),
    current_user: dict = Depends(get_current_user),
):
    """Cross-reference walk-in counters / walkouts / orders. Surfaces
    hidden sales (orders without a corresponding walkout entry).

    Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.1.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    db = get_db()

    now = now_ist_naive()
    start = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
    for _ in range(months_back - 1):
        start = (start - timedelta(days=1)).replace(day=1)

    # Fetch all the inputs in three queries.
    walkin_counters = []
    walkouts = []
    if db is not None:
        try:
            walkin_counters = list(
                db.get_collection("walk_in_counters").find(
                    {
                        "store_id": active_store,
                        "date_str": {"$gte": start.date().isoformat()},
                    },
                )
            )
        except Exception:
            walkin_counters = []
        try:
            walkouts = list(
                db.get_collection("walkouts").find(
                    {
                        "store_id": active_store,
                        "date_str": {"$gte": start.date().isoformat()},
                        "deleted_at": None,
                    },
                )
            )
        except Exception:
            walkouts = []

    order_repo = get_order_repository()
    orders = (
        _orders_in_window(
            order_repo,
            store_id=active_store,
            start_dt=start,
            end_dt=now,
        )
        if order_repo is not None
        else []
    )

    # Index by YYYY-MM
    walkins_by_month: dict = defaultdict(int)
    for w in walkin_counters:
        ds = w.get("date_str") or ""
        if len(ds) >= 7:
            walkins_by_month[ds[:7]] += int(w.get("total") or 0)

    walkouts_total_by_month: dict = defaultdict(int)
    walkouts_conv_by_month: dict = defaultdict(int)
    for w in walkouts:
        ds = w.get("date_str") or ""
        if len(ds) < 7:
            continue
        walkouts_total_by_month[ds[:7]] += 1
        if (w.get("result") or "").upper() == "CONVERTED":
            walkouts_conv_by_month[ds[:7]] += 1

    orders_by_month: dict = defaultdict(int)
    for o in orders:
        ca = _order_created_at(o)
        if ca is None:
            continue
        orders_by_month[ca.strftime("%Y-%m")] += 1

    months = []
    rolling = {
        "walkins_total": 0,
        "walkouts_total": 0,
        "walkouts_converted": 0,
        "orders_total": 0,
    }
    for y, m in _month_iter(start, now):
        key = f"{y:04d}-{m:02d}"
        wi = walkins_by_month.get(key, 0)
        wo = walkouts_total_by_month.get(key, 0)
        wc = walkouts_conv_by_month.get(key, 0)
        ot = orders_by_month.get(key, 0)
        hidden = max(0, ot - wc)
        months.append(
            {
                "month": key,
                "walkins_total": wi,
                "walkouts_total": wo,
                "walkouts_converted": wc,
                "orders_total": ot,
                "hidden_sales": hidden,
                "hidden_sales_pct": round(hidden / ot, 3) if ot else 0.0,
                "staff_reported_conversion_pct": round(wc / wi, 3) if wi else 0.0,
                "true_conversion_pct": round(ot / wi, 3) if wi else 0.0,
            }
        )
        rolling["walkins_total"] += wi
        rolling["walkouts_total"] += wo
        rolling["walkouts_converted"] += wc
        rolling["orders_total"] += ot

    rolling_hidden = max(0, rolling["orders_total"] - rolling["walkouts_converted"])
    rolling.update(
        {
            "hidden_sales": rolling_hidden,
            "hidden_sales_pct": (
                round(rolling_hidden / rolling["orders_total"], 3)
                if rolling["orders_total"]
                else 0.0
            ),
            "staff_reported_conversion_pct": (
                round(rolling["walkouts_converted"] / rolling["walkins_total"], 3)
                if rolling["walkins_total"]
                else 0.0
            ),
            "true_conversion_pct": (
                round(rolling["orders_total"] / rolling["walkins_total"], 3)
                if rolling["walkins_total"]
                else 0.0
            ),
        }
    )

    return {
        "store_id": active_store,
        "period_start": start.date().isoformat(),
        "period_end": now.date().isoformat(),
        "months": months,
        "rolling": rolling,
    }


# ----------------------------------------------------------------------------
# R1.2 Price Band Analysis
# ----------------------------------------------------------------------------


@router.get("/sales/price-bands")
async def sales_price_bands(
    store_id: Optional[str] = Query(None),
    fy_count: int = Query(3, ge=1, le=10),
    trend_bands: int = Query(4, ge=1, le=11),
    current_user: dict = Depends(get_current_user),
):
    """Segment invoices by net amount into 11 bands. Track movement
    between bands across financial years (premiumization signal).

    Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.2.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    order_repo = get_order_repository()
    now = now_ist_naive()
    # Start of FY (fy_count - 1) years before the current FY.
    current_fy_start_year = fy_start_year_ist()
    start_year = current_fy_start_year - (fy_count - 1)
    start = datetime(start_year, 4, 1)
    orders = (
        _orders_in_window(
            order_repo,
            store_id=active_store,
            start_dt=start,
            end_dt=now,
        )
        if order_repo is not None
        else []
    )

    # Tag each order with FY + net amount + band + month
    enriched: list = []
    for o in orders:
        ca = _order_created_at(o)
        if ca is None:
            continue
        net = _order_net(o)
        enriched.append(
            {
                "fy": _fy_of(ca),
                "month": ca.strftime("%Y-%m"),
                "net": net,
                "band": _price_band_of(net),
                "customer_id": o.get("customer_id"),
            }
        )

    # by_fy: per-FY counts + revenue + ATV per band
    fy_band_agg: dict = defaultdict(
        lambda: {b: {"invoices": 0, "revenue": 0.0} for b in _PRICE_BAND_NAMES}
    )
    fy_order: list = []
    for e in enriched:
        if e["fy"] not in fy_band_agg:
            fy_order.append(e["fy"])
        slot = fy_band_agg[e["fy"]][e["band"]]
        slot["invoices"] += 1
        slot["revenue"] += e["net"]

    by_fy = []
    for fy in sorted(set(e["fy"] for e in enriched)):
        invoices = [fy_band_agg[fy][b]["invoices"] for b in _PRICE_BAND_NAMES]
        revenue = [round(fy_band_agg[fy][b]["revenue"], 2) for b in _PRICE_BAND_NAMES]
        atv = [
            (
                round(fy_band_agg[fy][b]["revenue"] / fy_band_agg[fy][b]["invoices"], 2)
                if fy_band_agg[fy][b]["invoices"]
                else 0.0
            )
            for b in _PRICE_BAND_NAMES
        ]
        by_fy.append(
            {
                "fy": fy,
                "invoices_by_band": invoices,
                "revenue_by_band": revenue,
                "atv_by_band": atv,
            }
        )

    # Monthly trend per band — top `trend_bands` by total revenue across the whole period
    band_totals: dict = defaultdict(float)
    for e in enriched:
        band_totals[e["band"]] += e["net"]
    top_bands = sorted(_PRICE_BAND_NAMES, key=lambda b: -band_totals[b])[:trend_bands]

    monthly_per_band: dict = {
        b: defaultdict(lambda: {"revenue": 0.0, "invoices": 0}) for b in top_bands
    }
    for e in enriched:
        if e["band"] in monthly_per_band:
            monthly_per_band[e["band"]][e["month"]]["revenue"] += e["net"]
            monthly_per_band[e["band"]][e["month"]]["invoices"] += 1

    monthly_trend_by_band = {}
    for b in top_bands:
        rows = sorted(monthly_per_band[b].items())
        monthly_trend_by_band[b] = [
            {"month": m, "revenue": round(d["revenue"], 2), "invoices": d["invoices"]}
            for m, d in rows
        ]

    # Movement summary: for customers who appear in BOTH the current FY and the
    # immediately-preceding FY, compute median band index in each and compare.
    movement_summary = {
        "premiumized_pct": 0.0,
        "stable_pct": 0.0,
        "downgraded_pct": 0.0,
        "compared_customers": 0,
    }
    if fy_count >= 2:
        sorted_fys = sorted(set(e["fy"] for e in enriched))
        if len(sorted_fys) >= 2:
            cur_fy, prev_fy = sorted_fys[-1], sorted_fys[-2]
            cust_bands_cur: dict = defaultdict(list)
            cust_bands_prev: dict = defaultdict(list)
            for e in enriched:
                if not e["customer_id"]:
                    continue
                idx = _PRICE_BAND_NAMES.index(e["band"])
                if e["fy"] == cur_fy:
                    cust_bands_cur[e["customer_id"]].append(idx)
                elif e["fy"] == prev_fy:
                    cust_bands_prev[e["customer_id"]].append(idx)
            common = set(cust_bands_cur) & set(cust_bands_prev)
            up = same = down = 0
            for cid in common:
                cur_med = sorted(cust_bands_cur[cid])[len(cust_bands_cur[cid]) // 2]
                prev_med = sorted(cust_bands_prev[cid])[len(cust_bands_prev[cid]) // 2]
                if cur_med > prev_med:
                    up += 1
                elif cur_med < prev_med:
                    down += 1
                else:
                    same += 1
            n = len(common)
            if n:
                movement_summary = {
                    "premiumized_pct": round(up / n, 3),
                    "stable_pct": round(same / n, 3),
                    "downgraded_pct": round(down / n, 3),
                    "compared_customers": n,
                }

    return {
        "store_id": active_store,
        "bands": _PRICE_BAND_NAMES,
        "fy_count": fy_count,
        "by_fy": by_fy,
        "trend_bands": top_bands,
        "monthly_trend_by_band": monthly_trend_by_band,
        "movement_summary": movement_summary,
        "total_orders": len(enriched),
    }


# ----------------------------------------------------------------------------
# R1.3 Lens Deep Dive
# ----------------------------------------------------------------------------


_LENS_ITEM_TYPES = {"LENS", "OPTICAL_LENS", "CONTACT_LENS", "COLORED_CONTACT_LENS"}


@router.get("/sales/lens-deep-dive")
async def sales_lens_deep_dive(
    store_id: Optional[str] = Query(None),
    months_back: int = Query(12, ge=1, le=36),
    current_user: dict = Depends(get_current_user),
):
    """Breakdown of lens line items by brand / type / coating / refractive index.

    Joins order items where item_type in _LENS_ITEM_TYPES against the products
    collection to pull `brand` + `attributes.{lens_type,coating,refractive_index}`.

    Surfaces `parse_rate` so users can see how clean their catalog metadata is.

    Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.3.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    order_repo = get_order_repository()
    db = get_db()

    now = now_ist_naive()
    start = now - timedelta(days=30 * months_back)
    orders = (
        _orders_in_window(
            order_repo,
            store_id=active_store,
            start_dt=start,
            end_dt=now,
        )
        if order_repo is not None
        else []
    )

    # Collect lens line items
    lens_items: list = []
    product_ids: set = set()
    for o in orders:
        for it in o.get("items") or []:
            t = (it.get("item_type") or it.get("category") or "").upper()
            if t in _LENS_ITEM_TYPES:
                lens_items.append(
                    {
                        "item_type": t,
                        "product_id": it.get("product_id"),
                        "quantity": int(it.get("quantity") or 1),
                        "revenue": _item_revenue(it),
                    }
                )
                if it.get("product_id"):
                    product_ids.add(it["product_id"])

    # Fetch products in one query, build lookup
    products_by_id: dict = {}
    if db is not None and product_ids:
        try:
            cursor = db.get_collection("products").find(
                {"product_id": {"$in": list(product_ids)}}
            )
            for p in cursor:
                products_by_id[p["product_id"]] = p
        except Exception:
            products_by_id = {}

    # Aggregate
    by_brand: dict = defaultdict(lambda: {"units": 0, "revenue": 0.0})
    by_type: dict = defaultdict(lambda: {"units": 0, "revenue": 0.0})
    by_coating: dict = defaultdict(lambda: {"units": 0, "revenue": 0.0})
    by_index: dict = defaultdict(lambda: {"units": 0, "revenue": 0.0})
    total_units = 0
    total_revenue = 0.0
    parsed_units = 0  # units where at least lens_type was identified
    contact_units = 0
    contact_revenue = 0.0

    for it in lens_items:
        qty, rev = it["quantity"], it["revenue"]
        total_units += qty
        total_revenue += rev
        prod = products_by_id.get(it["product_id"]) if it["product_id"] else None
        brand = (prod or {}).get("brand") or "Unknown"
        attrs = (prod or {}).get("attributes") or {}
        ltype = attrs.get("lens_type") or attrs.get("type")
        coating = attrs.get("coating")
        index = attrs.get("refractive_index") or attrs.get("index")

        if it["item_type"] in ("CONTACT_LENS", "COLORED_CONTACT_LENS"):
            contact_units += qty
            contact_revenue += rev
        by_brand[brand]["units"] += qty
        by_brand[brand]["revenue"] += rev
        if ltype:
            parsed_units += qty
            by_type[ltype]["units"] += qty
            by_type[ltype]["revenue"] += rev
        if coating:
            by_coating[coating]["units"] += qty
            by_coating[coating]["revenue"] += rev
        if index:
            by_index[str(index)]["units"] += qty
            by_index[str(index)]["revenue"] += rev

    def _materialize(agg: dict) -> list:
        rows = [
            {"key": k, "units": v["units"], "revenue": round(v["revenue"], 2)}
            for k, v in agg.items()
        ]
        return sorted(rows, key=lambda r: -r["revenue"])

    return {
        "store_id": active_store,
        "period_start": start.date().isoformat(),
        "period_end": now.date().isoformat(),
        "totals": {
            "lens_units": total_units,
            "lens_revenue": round(total_revenue, 2),
            "atv": round(total_revenue / total_units, 2) if total_units else 0.0,
            "contact_lens_units": contact_units,
            "contact_lens_revenue": round(contact_revenue, 2),
        },
        "by_brand": [
            {
                "brand": r["key"],
                "units": r["units"],
                "revenue": r["revenue"],
                "share": (
                    round(r["revenue"] / total_revenue, 3) if total_revenue else 0.0
                ),
            }
            for r in _materialize(by_brand)
        ],
        "by_type": [
            {"type": r["key"], "units": r["units"], "revenue": r["revenue"]}
            for r in _materialize(by_type)
        ],
        "by_coating": [
            {"coating": r["key"], "units": r["units"], "revenue": r["revenue"]}
            for r in _materialize(by_coating)
        ],
        "by_refractive_index": [
            {"index": r["key"], "units": r["units"], "revenue": r["revenue"]}
            for r in _materialize(by_index)
        ],
        "parse_rate": round(parsed_units / total_units, 3) if total_units else 0.0,
        "metadata_pending": parsed_units == 0 and total_units > 0,
    }


# ----------------------------------------------------------------------------
# R1.4 Seasonality
# ----------------------------------------------------------------------------


@router.get("/sales/seasonality")
async def sales_seasonality(
    store_id: Optional[str] = Query(None),
    years_back: int = Query(2, ge=1, le=10),
    current_user: dict = Depends(get_current_user),
):
    """Day-of-week × month-of-year aggregation. Identifies peak and trough
    days/months and computes peak DOW lift over the average DOW.

    Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.4.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    order_repo = get_order_repository()
    now = now_ist_naive()
    start = now - timedelta(days=365 * years_back)
    orders = (
        _orders_in_window(
            order_repo,
            store_id=active_store,
            start_dt=start,
            end_dt=now,
        )
        if order_repo is not None
        else []
    )

    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    moy_names = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    dow_agg = {name: {"invoices": 0, "revenue": 0.0} for name in dow_names}
    moy_agg = {name: {"invoices": 0, "revenue": 0.0} for name in moy_names}

    for o in orders:
        ca = _order_created_at(o)
        if ca is None:
            continue
        rev = _order_revenue(o)
        dow_agg[dow_names[ca.weekday()]]["invoices"] += 1
        dow_agg[dow_names[ca.weekday()]]["revenue"] += rev
        moy_agg[moy_names[ca.month - 1]]["invoices"] += 1
        moy_agg[moy_names[ca.month - 1]]["revenue"] += rev

    def _materialize_row(name: str, slot: dict, key: str) -> dict:
        n = slot["invoices"]
        return {
            key: name,
            "invoices": n,
            "revenue": round(slot["revenue"], 2),
            "atv": round(slot["revenue"] / n, 2) if n else 0.0,
        }

    dow_out = [_materialize_row(n, dow_agg[n], "dow") for n in dow_names]
    moy_out = [_materialize_row(n, moy_agg[n], "month") for n in moy_names]

    nonzero_dow = [d for d in dow_out if d["revenue"] > 0]
    nonzero_moy = [d for d in moy_out if d["revenue"] > 0]
    peak_dow = max(nonzero_dow, key=lambda d: d["revenue"], default=None)
    trough_dow = min(nonzero_dow, key=lambda d: d["revenue"], default=None)
    peak_moy = max(nonzero_moy, key=lambda d: d["revenue"], default=None)
    trough_moy = min(nonzero_moy, key=lambda d: d["revenue"], default=None)
    avg_dow_rev = sum(d["revenue"] for d in dow_out) / 7
    peak_lift = (
        peak_dow["revenue"] / avg_dow_rev - 1.0 if peak_dow and avg_dow_rev else 0.0
    )

    return {
        "store_id": active_store,
        "years_back": years_back,
        "day_of_week": dow_out,
        "month_of_year": moy_out,
        "peak_dow": peak_dow["dow"] if peak_dow else None,
        "trough_dow": trough_dow["dow"] if trough_dow else None,
        "peak_month": peak_moy["month"] if peak_moy else None,
        "trough_month": trough_moy["month"] if trough_moy else None,
        "peak_dow_lift_pct": round(peak_lift, 3),
        "total_orders": len(orders),
    }


# ----------------------------------------------------------------------------
# R2 — Purchase Recommendations (TechCherry port)
#
# Spec: docs/TECHCHERRY_PORT_SCOPE.md §6.
# Algo:  per-SKU velocity over the last 90 days × current stock × reorder
# point → suggested order qty + ranked revenue/margin impact.
# ----------------------------------------------------------------------------

# Optical retail buying cycle defaults — tunable via query params.
# Lead time + reorder cycle = 60 days of cover the buyer wants on shelf.
_DEFAULT_LEAD_TIME_DAYS = 30  # typical for frame imports / vendor cycles
_DEFAULT_REORDER_CYCLE_DAYS = 30  # monthly purchase cadence
_DEFAULT_SAFETY_BUFFER_DAYS = 7  # ~1 week extra to absorb demand spikes


def _confidence_for(velocity_90d: int) -> str:
    """High/Medium/Low confidence band based on demand signal strength.
    Below 5 units in 90 days = noise; 5-29 = soft signal; 30+ = robust."""
    if velocity_90d >= 30:
        return "HIGH"
    if velocity_90d >= 5:
        return "MEDIUM"
    return "LOW"


@router.get("/purchase/recommendations")
async def purchase_recommendations(
    store_id: Optional[str] = Query(
        None, description="Defaults to current user's active store"
    ),
    lookback_days: int = Query(90, ge=14, le=365, description="Sales velocity window"),
    lead_time_days: int = Query(
        _DEFAULT_LEAD_TIME_DAYS,
        ge=0,
        le=180,
        description="Vendor lead time used in suggested-qty math",
    ),
    reorder_cycle_days: int = Query(
        _DEFAULT_REORDER_CYCLE_DAYS,
        ge=7,
        le=180,
        description="How often the buyer re-evaluates purchases",
    ),
    safety_buffer_days: int = Query(
        _DEFAULT_SAFETY_BUFFER_DAYS,
        ge=0,
        le=30,
        description="Extra cover above lead+cycle to absorb spikes",
    ),
    min_velocity: int = Query(
        2,
        ge=0,
        le=100,
        description="Skip SKUs that sold fewer than this many units in the window (noise filter)",
    ),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
):
    """Ranked list of products the buyer should reorder.

    Combines per-SKU sales velocity (last `lookback_days`) with current
    stock and reorder point. For each SKU the algo computes the desired
    cover (lead time + cycle + safety buffer days × daily velocity) and
    suggests an order qty if current stock is below that.

    Ranking key is `gap_units × avg_selling_price` so the highest
    revenue-at-risk SKUs surface first — that's where the buyer's time
    matters most.

    Spec: docs/TECHCHERRY_PORT_SCOPE.md §6.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    db = get_db()
    if db is None:
        return {
            "recommendations": [],
            "summary": {
                "total_recommendations": 0,
                "total_suggested_units": 0,
                "estimated_revenue_at_risk": 0.0,
                "estimated_purchase_cost": 0.0,
            },
            "params": {
                "store_id": active_store,
                "lookback_days": lookback_days,
                "lead_time_days": lead_time_days,
                "reorder_cycle_days": reorder_cycle_days,
                "safety_buffer_days": safety_buffer_days,
                "cover_days_total": lead_time_days
                + reorder_cycle_days
                + safety_buffer_days,
                "min_velocity": min_velocity,
            },
            "as_of": datetime.now().isoformat(),
        }

    now = now_ist_naive()
    cutoff = now - timedelta(days=lookback_days)
    cover_days = lead_time_days + reorder_cycle_days + safety_buffer_days

    # 1. Sales velocity per product over the lookback window.
    sku_stats: dict = {}
    try:
        orders_coll = db.get_collection("orders")
        pipeline = [
            {
                "$match": {
                    "store_id": active_store,
                    "status": {"$nin": ["CANCELLED", "DRAFT"]},
                    "created_at": {"$gte": cutoff},
                }
            },
            {"$unwind": "$items"},
            {
                "$group": {
                    "_id": "$items.product_id",
                    "units_sold": {"$sum": {"$ifNull": ["$items.quantity", 1]}},
                    "revenue": {
                        "$sum": {"$ifNull": ["$items.item_total", "$items.total"]}
                    },
                    "avg_price": {"$avg": "$items.unit_price"},
                    "sample_name": {"$first": "$items.product_name"},
                    "sample_brand": {"$first": "$items.brand"},
                    "sample_category": {"$first": "$items.category"},
                }
            },
            {"$match": {"units_sold": {"$gte": min_velocity}}},
        ]
        for doc in orders_coll.aggregate(pipeline):
            pid = doc.get("_id")
            if not pid:
                continue
            sku_stats[str(pid)] = {
                "units_sold": int(doc.get("units_sold") or 0),
                "revenue": float(doc.get("revenue") or 0),
                "avg_price": float(doc.get("avg_price") or 0),
                "sample_name": doc.get("sample_name") or "",
                "sample_brand": doc.get("sample_brand") or "",
                "sample_category": doc.get("sample_category") or "",
            }
    except Exception as e:
        # Aggregation can fail on empty/unindexed collections — degrade
        # to empty rec list, never raise.
        return {
            "recommendations": [],
            "summary": {
                "total_recommendations": 0,
                "total_suggested_units": 0,
                "estimated_revenue_at_risk": 0.0,
                "estimated_purchase_cost": 0.0,
            },
            "params": {
                "store_id": active_store,
                "lookback_days": lookback_days,
                "lead_time_days": lead_time_days,
                "reorder_cycle_days": reorder_cycle_days,
                "safety_buffer_days": safety_buffer_days,
                "cover_days_total": cover_days,
                "min_velocity": min_velocity,
            },
            "error": f"aggregation failed: {type(e).__name__}",
            "as_of": now.isoformat(),
        }

    if not sku_stats:
        return {
            "recommendations": [],
            "summary": {
                "total_recommendations": 0,
                "total_suggested_units": 0,
                "estimated_revenue_at_risk": 0.0,
                "estimated_purchase_cost": 0.0,
            },
            "params": {
                "store_id": active_store,
                "lookback_days": lookback_days,
                "lead_time_days": lead_time_days,
                "reorder_cycle_days": reorder_cycle_days,
                "safety_buffer_days": safety_buffer_days,
                "cover_days_total": cover_days,
                "min_velocity": min_velocity,
            },
            "as_of": now.isoformat(),
        }

    # 2. Pull product master rows for the SKUs that have movement so we
    # know current stock + reorder point + cost + selling price.
    from bson import ObjectId  # local import; only needed here

    product_ids = list(sku_stats.keys())
    object_ids: list = []
    string_ids: list = []
    for pid in product_ids:
        try:
            object_ids.append(ObjectId(pid))
        except Exception:
            string_ids.append(pid)

    products: dict = {}
    try:
        prod_coll = db.get_collection("products")
        flt = {"$or": []}
        if object_ids:
            flt["$or"].append({"_id": {"$in": object_ids}})
        if string_ids:
            flt["$or"].append({"_id": {"$in": string_ids}})
            flt["$or"].append({"product_id": {"$in": string_ids}})
            flt["$or"].append({"sku": {"$in": string_ids}})
        if flt["$or"]:
            for p in prod_coll.find(flt):
                pid = str(p.get("_id"))
                products[pid] = p
                # Allow lookup by alternate id fields too.
                if p.get("product_id"):
                    products[str(p["product_id"])] = p
                if p.get("sku"):
                    products[str(p["sku"])] = p
    except Exception:
        products = {}

    # 3. Build per-SKU recommendation rows.
    recs: list = []
    for pid, stats in sku_stats.items():
        prod = products.get(pid, {})
        velocity_90d = stats["units_sold"]
        daily_v = velocity_90d / float(lookback_days) if lookback_days else 0.0
        desired_cover = round(daily_v * cover_days)
        current_stock = int(
            prod.get("stock_quantity")
            or prod.get("quantity")
            or prod.get("current_stock")
            or 0
        )
        reorder_point = int(prod.get("reorder_point") or 0)
        gap_units = max(0, desired_cover - current_stock)
        if gap_units <= 0 and current_stock > reorder_point:
            # No buying needed — skip.
            continue
        # If reorder_point breached even when desired_cover would tolerate
        # current stock, still recommend a minimum top-up of (reorder_point - current_stock).
        suggested_qty = max(
            gap_units,
            reorder_point - current_stock if reorder_point > current_stock else 0,
        )
        if suggested_qty <= 0:
            continue

        avg_price = float(
            prod.get("offer_price") or prod.get("mrp") or stats["avg_price"] or 0.0
        )
        cost_price = float(prod.get("cost_price") or prod.get("landed_cost") or 0.0)
        unit_margin = max(0.0, avg_price - cost_price)
        est_revenue_impact = round(suggested_qty * avg_price, 2)
        est_purchase_cost = round(suggested_qty * cost_price, 2)
        est_margin = round(suggested_qty * unit_margin, 2)

        category = prod.get("category") or stats["sample_category"] or "OTHER"
        brand = prod.get("brand") or stats["sample_brand"] or ""
        name = (
            prod.get("name") or prod.get("product_name") or stats["sample_name"] or ""
        )

        recs.append(
            {
                "product_id": pid,
                "name": name,
                "brand": brand,
                "category": category,
                "velocity_90d": velocity_90d,
                "daily_velocity": round(daily_v, 2),
                "current_stock": current_stock,
                "reorder_point": reorder_point,
                "desired_cover": desired_cover,
                "gap_units": gap_units,
                "suggested_order_qty": suggested_qty,
                "avg_selling_price": round(avg_price, 2),
                "cost_price": round(cost_price, 2),
                "unit_margin": round(unit_margin, 2),
                "estimated_revenue_impact": est_revenue_impact,
                "estimated_purchase_cost": est_purchase_cost,
                "estimated_margin": est_margin,
                "confidence": _confidence_for(velocity_90d),
                "reason": (
                    f"Sold {velocity_90d} in {lookback_days}d "
                    f"(~{round(daily_v, 1)}/day). Stock {current_stock}, "
                    f"reorder at {reorder_point}. Buy {suggested_qty} to cover "
                    f"{cover_days} days."
                ),
            }
        )

    # 4. Rank by revenue-at-risk × confidence weight.
    confidence_weight = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.4}
    recs.sort(
        key=lambda r: r["estimated_revenue_impact"]
        * confidence_weight[r["confidence"]],
        reverse=True,
    )
    recs = recs[:limit]

    summary = {
        "total_recommendations": len(recs),
        "total_suggested_units": sum(r["suggested_order_qty"] for r in recs),
        "estimated_revenue_at_risk": round(
            sum(r["estimated_revenue_impact"] for r in recs), 2
        ),
        "estimated_purchase_cost": round(
            sum(r["estimated_purchase_cost"] for r in recs), 2
        ),
        "estimated_margin": round(sum(r["estimated_margin"] for r in recs), 2),
    }

    # By-category roll-up — useful for the UI's category badges.
    by_category: dict = {}
    for r in recs:
        slot = by_category.setdefault(
            r["category"],
            {
                "category": r["category"],
                "count": 0,
                "suggested_units": 0,
                "estimated_revenue_impact": 0.0,
            },
        )
        slot["count"] += 1
        slot["suggested_units"] += r["suggested_order_qty"]
        slot["estimated_revenue_impact"] += r["estimated_revenue_impact"]
    by_category_list = sorted(
        (
            {**v, "estimated_revenue_impact": round(v["estimated_revenue_impact"], 2)}
            for v in by_category.values()
        ),
        key=lambda x: x["estimated_revenue_impact"],
        reverse=True,
    )

    return {
        "recommendations": recs,
        "by_category": by_category_list,
        "summary": summary,
        "params": {
            "store_id": active_store,
            "lookback_days": lookback_days,
            "lead_time_days": lead_time_days,
            "reorder_cycle_days": reorder_cycle_days,
            "safety_buffer_days": safety_buffer_days,
            "cover_days_total": cover_days,
            "min_velocity": min_velocity,
        },
        "as_of": now.isoformat(),
    }


# ----------------------------------------------------------------------------
# R3 — Growth Blueprint (LLM-narrated synthesis of R1 + R2)
#
# Spec: docs/TECHCHERRY_PORT_SCOPE.md §7.
# Calls JARVIS's pluggable LLM provider with a structured prompt that
# bundles every R1+R2 endpoint output. Returns a 12-section consultant-
# style narrative the operator can read on /reports/blueprint or print.
# ----------------------------------------------------------------------------

_BLUEPRINT_SECTIONS = [
    "Where the business stands today",
    "Revenue trajectory — ATV-driven or footfall-driven?",
    "Premiumization proof — price band movement",
    "Staff honest assessment",
    "Footfall integrity — hidden sales quantified",
    "Lens business as profit engine",
    "Discount discipline",
    "Growth levers (ranked by ₹ impact)",
    "Revenue projections (conservative 3-year)",
    "Quick wins (zero-cost)",
    "Top 10 actions",
    "Competitive positioning",
]

_BLUEPRINT_SYSTEM_PROMPT = """You are JARVIS acting as a senior retail-strategy consultant to the Superadmin of Better Vision + WizOpt — a premium optical retail chain in India.

You have been handed a structured brief containing every analytics output the chain has on record:
  - Footfall audit (walk-in vs walkout vs orders, hidden sales)
  - Price band shift (premiumization signal)
  - Lens deep-dive (the profit engine)
  - Seasonality (day-of-week + month-of-year)
  - Purchase recommendations (what to reorder)

Your job: produce a 12-section **Growth Blueprint** that synthesises this into a strategic narrative. Each section must be GROUNDED in the numbers — quote specific ₹ values, percentages, or counts from the brief. Where the data is empty or zero, say so plainly and explain what to track to fill the gap. Do NOT fabricate numbers.

## Tone
- Senior consultant brief, not a marketing deck
- Direct, honest assessments — including bad news
- Indian Rupee formatting (₹, L for Lakh, Cr for Crore)
- Markdown for structure (## headings, bullet lists, **bold** for ₹ figures)
- 1500–2500 words total

## Required sections (exactly these, in this order):

{sections_list}

Each section is one or two short paragraphs followed by 3–5 bullet points where appropriate. Section 8 (Growth levers) MUST be a numbered list with ₹ impact per lever. Section 11 (Top 10 actions) MUST be exactly 10 bullets, each starting with an imperative verb (Audit / Train / Reorder / Run / Track…).

End with: a single line stating the report's confidence level (HIGH / MEDIUM / LOW) and the most important caveat (e.g. "limited by 3-month order history" or "footfall data sparse for 2 stores"). Do NOT add any other closing text — no "let me know if…", no signature.
"""


async def _r3_assemble_inputs(
    store_id: Optional[str], current_user: dict
) -> Dict[str, Any]:
    """Call the R1+R2 endpoints internally and bundle their outputs.
    Each call is wrapped — a single failing report shouldn't take down
    the whole blueprint. Returns a dict the LLM prompt can format."""
    inputs: Dict[str, Any] = {}
    # Direct function calls to avoid an HTTP round-trip back into ourselves
    try:
        inputs["footfall_audit"] = await footfall_audit(  # type: ignore[arg-type]
            store_id=store_id,
            months_back=12,
            current_user=current_user,
        )
    except Exception as e:
        inputs["footfall_audit"] = {"error": str(e)}
    try:
        inputs["price_bands"] = await sales_price_bands(  # type: ignore[arg-type]
            store_id=store_id,
            fy_count=3,
            current_user=current_user,
        )
    except Exception as e:
        inputs["price_bands"] = {"error": str(e)}
    try:
        inputs["lens_deep_dive"] = await sales_lens_deep_dive(  # type: ignore[arg-type]
            store_id=store_id,
            months_back=12,
            current_user=current_user,
        )
    except Exception as e:
        inputs["lens_deep_dive"] = {"error": str(e)}
    try:
        inputs["seasonality"] = await sales_seasonality(  # type: ignore[arg-type]
            store_id=store_id,
            years_back=2,
            current_user=current_user,
        )
    except Exception as e:
        inputs["seasonality"] = {"error": str(e)}
    try:
        inputs["purchase_recommendations"] = await purchase_recommendations(  # type: ignore[arg-type]
            store_id=store_id,
            lookback_days=90,
            lead_time_days=30,
            reorder_cycle_days=30,
            safety_buffer_days=7,
            min_velocity=2,
            limit=50,
            current_user=current_user,
        )
    except Exception as e:
        inputs["purchase_recommendations"] = {"error": str(e)}
    # Plus a summary overview so the blueprint has total revenue context
    try:
        from .jarvis import JarvisAnalyticsEngine  # local import to avoid cycle

        inputs["overview"] = JarvisAnalyticsEngine.get_business_overview()
    except Exception as e:
        inputs["overview"] = {"error": str(e)}
    return inputs


@router.get(
    "/blueprint",
    summary="Growth Blueprint — JARVIS-narrated consultant synthesis of R1+R2",
    description=(
        "Calls JARVIS's LLM provider with every R1+R2 analytics output "
        "bundled as context and asks for a 12-section consultant-style "
        "narrative. Cost-sensitive — local OSS or Claude Haiku is the "
        "default; Opus is opt-in via `model_id=claude-opus`. "
        "Cached per (store, month) for 24h so refreshes don't incur "
        "extra LLM cost during the same business day."
    ),
)
async def growth_blueprint(
    store_id: Optional[str] = Query(None),
    model_id: Optional[str] = Query(
        None, description="LLM model id (local/claude/claude-opus). None = default."
    ),
    nocache: bool = Query(False, description="Bypass per-(store,month) cache"),
    current_user: dict = Depends(get_current_user),
):
    """Generate (or fetch from cache) the Growth Blueprint."""
    import json as _json
    from agents import llm_provider  # local import — agents pkg may be optional

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    month_key = now_ist().strftime("%Y-%m")
    cache_key = f"{active_store}::{month_key}::{model_id or 'default'}"
    db = get_db()

    # 1. Cache lookup — 24h TTL on per-(store, month, model) key
    if not nocache and db is not None:
        try:
            cache_col = db.get_collection("report_blueprints")
            existing = cache_col.find_one({"cache_key": cache_key})
            if existing:
                age_hours = (
                    (
                        datetime.now() - existing.get("generated_at", datetime.min)
                    ).total_seconds()
                    / 3600
                    if isinstance(existing.get("generated_at"), datetime)
                    else 999
                )
                if age_hours <= 24:
                    return {
                        "narrative_markdown": existing.get("narrative_markdown", ""),
                        "sections": _BLUEPRINT_SECTIONS,
                        "model_used": existing.get("model_used"),
                        "store_id": active_store,
                        "month": month_key,
                        "generated_at": existing.get("generated_at"),
                        "from_cache": True,
                        "cache_age_hours": round(age_hours, 1),
                    }
        except Exception:
            pass

    # 2. Pull the inputs
    inputs = await _r3_assemble_inputs(active_store, current_user)

    # 3. Compose the user prompt with the analytics brief
    sections_list = "\n".join(f"{i+1}. {s}" for i, s in enumerate(_BLUEPRINT_SECTIONS))
    # .replace() rather than .format() — defensive against future edits
    # adding curly-braced literals (e.g. JSON shape examples) to the
    # prompt. .format() would treat them as missing placeholders.
    system = _BLUEPRINT_SYSTEM_PROMPT.replace("{sections_list}", sections_list)
    user_msg = (
        f"Store: {active_store}\n"
        f"As-of: {now_ist().strftime('%Y-%m-%d %H:%M IST')}\n\n"
        f"Produce the full 12-section Growth Blueprint grounded in the "
        f"BUSINESS DATA below."
    )

    # 4. Call LLM (defence-in-depth: scrub_level='customer' so any
    # accidentally-included customer PII gets redacted; staff/vendor
    # data is preserved)
    if not llm_provider.any_available():
        return {
            "narrative_markdown": (
                "# Growth Blueprint unavailable\n\n"
                "No LLM provider is configured on this deployment. "
                "Set `ANTHROPIC_API_KEY` (Claude) and retry."
            ),
            "sections": _BLUEPRINT_SECTIONS,
            "model_used": None,
            "store_id": active_store,
            "month": month_key,
            "generated_at": datetime.now().isoformat(),
            "from_cache": False,
            "error": "no LLM provider configured",
        }

    try:
        narrative = await llm_provider.complete(
            system,
            user_msg,
            model_id=model_id,
            business_data=inputs,
            max_tokens=4096,
            timeout=180.0,
            scrub_level="customer",
            context_budget=48000,
        )
    except Exception as e:
        return {
            "narrative_markdown": (
                f"# Growth Blueprint generation failed\n\n"
                f"LLM call raised: `{type(e).__name__}: {e}`\n\n"
                "Try again, or switch to a different model."
            ),
            "sections": _BLUEPRINT_SECTIONS,
            "model_used": model_id,
            "store_id": active_store,
            "month": month_key,
            "generated_at": datetime.now().isoformat(),
            "from_cache": False,
            "error": f"{type(e).__name__}: {e}",
        }

    if not narrative:
        return {
            "narrative_markdown": (
                "# Growth Blueprint generation returned empty\n\n"
                "The LLM did not return a response. This usually means a "
                "timeout or rate limit. Try again in a minute, or switch "
                "to a different model."
            ),
            "sections": _BLUEPRINT_SECTIONS,
            "model_used": model_id,
            "store_id": active_store,
            "month": month_key,
            "generated_at": datetime.now().isoformat(),
            "from_cache": False,
            "error": "empty LLM response",
        }

    model_used = model_id or llm_provider.default_model_id() or "default"
    generated_at = datetime.now()

    # 5. Persist to cache
    if db is not None:
        try:
            cache_col = db.get_collection("report_blueprints")
            cache_col.update_one(
                {"cache_key": cache_key},
                {
                    "$set": {
                        "cache_key": cache_key,
                        "store_id": active_store,
                        "month": month_key,
                        "model_used": model_used,
                        "narrative_markdown": narrative,
                        "generated_at": generated_at,
                        "inputs_meta": {
                            "footfall_months": (inputs.get("footfall_audit") or {})
                            .get("rolling", {})
                            .get("orders_total"),
                            "purchase_recs_count": (
                                inputs.get("purchase_recommendations") or {}
                            )
                            .get("summary", {})
                            .get("total_recommendations"),
                            "price_bands_fy_count": len(
                                (inputs.get("price_bands") or {}).get("bands", [])
                            ),
                        },
                    }
                },
                upsert=True,
            )
        except Exception:
            pass  # Cache write failure is non-fatal

    return {
        "narrative_markdown": narrative,
        "sections": _BLUEPRINT_SECTIONS,
        "model_used": model_used,
        "store_id": active_store,
        "month": month_key,
        "generated_at": generated_at.isoformat(),
        "from_cache": False,
    }


# ============================================================================
# Day-End Close (cash-drawer reconciliation persistence)
# ============================================================================
# The Day-End Closing Report (frontend reports/DayEndReport.tsx) lets a store
# reconcile the physical cash drawer against system cash and "Close Day". That
# action previously only flipped a local React flag (no persistence), so a page
# refresh lost the close and there was no audit record. These endpoints persist
# one immutable close per (store_id, date) and audit it.
#
# SYSTEM_INTENT: Audit Everything + Fail Loudly -> closing a day is a recorded,
# idempotent event; a second close of the same day is rejected (409), not
# silently re-written.

_DAY_END_CLOSE_ROLES = (
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "ACCOUNTANT",
    "SALES_CASHIER",
    "CASHIER",
)


class DayEndCloseBody(BaseModel):
    """Body for POST /reports/day-end-close. closing_cash = physically counted
    cash in the drawer; system_cash = cash the POS expects. variance is derived
    server-side (never trusted from the client)."""

    date: str = Field(..., description="Business date being closed (YYYY-MM-DD)")
    store_id: Optional[str] = Field(
        None, description="Store; defaults to the user's active store"
    )
    closing_cash: float = Field(0.0, description="Physically counted cash in drawer")
    system_cash: float = Field(0.0, description="System-expected cash (from POS)")
    notes: Optional[str] = Field(None, max_length=2000)


def _day_end_doc_public(doc: dict) -> dict:
    """Strip the Mongo _id for a JSON-safe response."""
    if not doc:
        return {}
    out = dict(doc)
    out.pop("_id", None)
    return out


@router.get("/day-end-close")
async def get_day_end_close(
    date: str = Query(..., description="Business date (YYYY-MM-DD)"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_DAY_END_CLOSE_ROLES)),
):
    """Return the day-end close status for (store_id, date). `closed` is False
    with `close: null` when the day hasn't been closed yet (honest empty state,
    not a fabricated record)."""
    sid = validate_store_access(store_id, current_user)
    db = get_db()
    if db is None or not getattr(db, "is_connected", False):
        # No DB -> we genuinely don't know; report not-closed rather than fake.
        return {"closed": False, "store_id": sid, "date": date, "close": None}

    doc = db.get_collection("day_end_closes").find_one({"store_id": sid, "date": date})
    return {
        "closed": bool(doc),
        "store_id": sid,
        "date": date,
        "close": _day_end_doc_public(doc) if doc else None,
    }


@router.post("/day-end-close")
async def create_day_end_close(
    body: DayEndCloseBody,
    current_user: dict = Depends(require_roles(*_DAY_END_CLOSE_ROLES)),
):
    """Persist a day-end cash-drawer close. Idempotent per (store_id, date): a
    repeat close of an already-closed day returns 409 (the existing close is in
    the error so the UI can surface it). Variance is computed server-side."""
    from fastapi import HTTPException

    sid = validate_store_access(body.store_id, current_user)
    if not sid:
        raise HTTPException(
            status_code=400, detail="No store selected for day-end close"
        )
    db = get_db()
    if db is None or not getattr(db, "is_connected", False):
        raise HTTPException(status_code=503, detail="Database not available")

    closes = db.get_collection("day_end_closes")
    existing = closes.find_one({"store_id": sid, "date": body.date})
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Day {body.date} already closed for store {sid}",
                "close": _day_end_doc_public(existing),
            },
        )

    variance = round(float(body.closing_cash) - float(body.system_cash), 2)
    now = datetime.utcnow()
    doc = {
        "store_id": sid,
        "date": body.date,
        "closing_cash": round(float(body.closing_cash), 2),
        "system_cash": round(float(body.system_cash), 2),
        "variance": variance,
        "notes": (body.notes or "").strip() or None,
        "closed_by": current_user.get("user_id"),
        "closed_at": now.isoformat(),
    }

    try:
        closes.insert_one(dict(doc))
    except Exception as e:  # pragma: no cover - surfaced as 500 by FastAPI
        # A duplicate-key race (two closes at once) lands here too; re-read and
        # report the winner as a 409 rather than a 500.
        winner = closes.find_one({"store_id": sid, "date": body.date})
        if winner:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"Day {body.date} already closed for store {sid}",
                    "close": _day_end_doc_public(winner),
                },
            )
        raise HTTPException(
            status_code=500, detail=f"Failed to record day-end close: {e}"
        )

    # Audit (fail-soft: an audit hiccup must not undo the business record).
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "DAY_END_CLOSED",
                    "entity_type": "day_end_close",
                    "entity_id": f"{sid}:{body.date}",
                    "store_id": sid,
                    "user_id": current_user.get("user_id"),
                    "severity": "WARNING" if variance != 0 else "INFO",
                    "details": {
                        "date": body.date,
                        "closing_cash": doc["closing_cash"],
                        "system_cash": doc["system_cash"],
                        "variance": variance,
                    },
                }
            )
    except Exception:
        pass

    return {
        "closed": True,
        "store_id": sid,
        "date": body.date,
        "close": _day_end_doc_public(doc),
    }
