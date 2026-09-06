"""Offer tally / promotions report and the non-moving stock report."""

from fastapi import Depends, Query
from typing import Dict, Optional
from datetime import date, datetime, timedelta
from ...utils.ist import (
    ist_day_start_utc,
)
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_db,
    validate_store_access,
)
from ._shared import (
    _REPORT_FINANCE_ROLES,
    router,
)

# ============================================================================
# OFFER TALLY / PROMOTIONS REPORT (F11)
# ============================================================================
# "Which promos fired, how much did they give away, and what did that do to
# margin?" Aggregates the immutable promo_applications audit collection by
# promo over a date window. Margin uses cost_at_sale when present and ESTIMATES
# at 60% otherwise -- estimated rows are flagged so the owner never mistakes
# estimated margin for real margin (F11 business rule). Empty + fail-soft when
# the engine is dark / no applications exist.
@router.get("/promotions")
async def promotions_report(
    start_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_REPORT_FINANCE_ROLES)),
):
    """Offer Tally: per-promo fired-count, total discount given, estimated COGS,
    net margin impact, and an estimated-COGS flag. ADMIN/AREA/STORE/ACCOUNTANT."""
    db = get_db().db if get_db() else None
    empty = {
        "summary": {
            "total_discount_given": 0.0,
            "orders_with_promos": 0,
            "promos_fired": 0,
            "net_margin_impact": 0.0,
            "any_cogs_estimated": False,
        },
        "promos": [],
        "start_date": start_date,
        "end_date": end_date,
    }
    if db is None:
        return empty

    # Store-scope: non-HQ callers are pinned to a store they can access.
    active_store = None
    if store_id:
        active_store = validate_store_access(store_id, current_user)
    elif not (set(current_user.get("roles", [])) & {"SUPERADMIN", "ADMIN"}):
        active_store = current_user.get("active_store_id")

    flt: dict = {}
    if active_store:
        flt["store_id"] = active_store
    # applied_at IS an ISO string (promotions.py writes _now_iso() ==
    # datetime.now().isoformat(), a NAIVE-UTC string on the UTC box), so a
    # STRING bound is the right shape -- the frame was the bug, not the type.
    # BUG-104, BOUND rule: start/end are IST calendar days the caller typed;
    # 'T00:00:00' bounds compared the stored naive-UTC strings at UTC
    # midnight, dropping every promo fired 00:00-05:30 IST on the first day
    # and claiming the same window after the last. The IST day boundary
    # expressed in the stored naive-UTC frame is ist_day_start_utc, emitted
    # as an isoformat string so it stays lexically comparable.
    if start_date:
        try:
            _sd = date.fromisoformat(str(start_date)[:10])
            flt["applied_at"] = {"$gte": ist_day_start_utc(_sd).isoformat()}
        except ValueError:
            flt["applied_at"] = {"$gte": f"{start_date}T00:00:00"}
    if end_date:
        flt.setdefault("applied_at", {})
        try:
            _ed = date.fromisoformat(str(end_date)[:10])
            flt["applied_at"]["$lt"] = ist_day_start_utc(
                _ed + timedelta(days=1)
            ).isoformat()
        except ValueError:
            flt["applied_at"]["$lte"] = f"{end_date}T23:59:59"

    try:
        apps = list(db.get_collection("promo_applications").find(flt))
    except Exception:  # noqa: BLE001
        return empty
    for a in apps:
        a.pop("_id", None)

    # Aggregate per promo_id.
    per_promo: Dict[str, dict] = {}
    total_discount = 0.0
    total_margin = 0.0
    any_estimated = False
    order_ids: set = set()
    for app in apps:
        order_ids.add(app.get("order_id"))
        disc = float(app.get("total_discount_given") or 0.0)
        total_discount += disc
        total_margin += float(app.get("net_margin_after_promo") or -disc)
        if app.get("cogs_is_estimated"):
            any_estimated = True
        for ap in app.get("applied_promos") or []:
            pid = ap.get("promo_id") or "unknown"
            row = per_promo.setdefault(
                pid,
                {
                    "promo_id": pid,
                    "promo_name": ap.get("promo_name") or pid,
                    "promo_type": ap.get("promo_type"),
                    "orders_count": 0,
                    "total_discount_given": 0.0,
                    "estimated_cogs": 0.0,
                    "net_margin_after_promo": 0.0,
                    "cogs_is_estimated": False,
                },
            )
            row["orders_count"] += 1
            row["promo_name"] = ap.get("promo_name") or row["promo_name"]
            row["total_discount_given"] += float(ap.get("discount_given") or 0.0)
        # Apportion the application's COGS/margin estimate across its promos.
        promos_here = app.get("applied_promos") or []
        if promos_here:
            est_cogs_each = float(app.get("estimated_cogs") or 0.0) / len(promos_here)
            for ap in promos_here:
                pid = ap.get("promo_id") or "unknown"
                row = per_promo.get(pid)
                if row:
                    row["estimated_cogs"] += est_cogs_each
                    row["net_margin_after_promo"] -= float(
                        ap.get("discount_given") or 0.0
                    )
                    if app.get("cogs_is_estimated"):
                        row["cogs_is_estimated"] = True

    promos_out = []
    for row in per_promo.values():
        row["total_discount_given"] = round(row["total_discount_given"], 2)
        row["estimated_cogs"] = round(row["estimated_cogs"], 2)
        row["net_margin_after_promo"] = round(row["net_margin_after_promo"], 2)
        promos_out.append(row)
    promos_out.sort(key=lambda r: r["total_discount_given"], reverse=True)

    return {
        "summary": {
            "total_discount_given": round(total_discount, 2),
            "orders_with_promos": len([o for o in order_ids if o]),
            "promos_fired": len(promos_out),
            "net_margin_impact": round(total_margin, 2),
            "any_cogs_estimated": any_estimated,
        },
        "promos": promos_out,
        "start_date": start_date,
        "end_date": end_date,
    }


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
                    "status": {"$nin": ["CANCELLED", "DRAFT", "HISTORICAL"]},
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


