"""
IMS 2.0 - Analytics V2 Router
===============================
Advanced analytics and feature endpoints:
10. Discount Analysis Dashboard
11. Demand Forecasting (SUPERADMIN)
12. Dead Stock Identification
13. Loyalty Program Engine
14. Contact Lens Subscription
18. Eye Camp Management
19. Family Package Deals
20. Staff Performance Gamification
22. Customer Churn Prediction (SUPERADMIN)
23. Fraud/Theft Anomaly Detection (SUPERADMIN)
25. Vendor Margin Insights (SUPERADMIN)
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, timedelta
import uuid

from .auth import get_current_user
from ..dependencies import get_db as _dep_get_db
from ..services.notification_service import send_notification

router = APIRouter()


def _get_db():
    try:
        from database.connection import get_db
        return get_db().db
    except Exception:
        return _dep_get_db()


def _parse_date(d: Optional[str]) -> Optional[datetime]:
    """Parse an ISO date string to datetime, returning None on failure."""
    if not d:
        return None
    try:
        return datetime.fromisoformat(d.replace("Z", "+00:00"))
    except Exception:
        return None


def _safe_div(a, b, ndigits=2):
    return round(a / b, ndigits) if b else 0


# ============================================================================
# SCHEMAS
# ============================================================================

class LoyaltyEarnRequest(BaseModel):
    customer_id: str
    order_id: str
    amount: float

class LoyaltyRedeemRequest(BaseModel):
    customer_id: str
    points: int
    redemption_type: str = "discount"

class EyeCampCreateRequest(BaseModel):
    name: str
    date: str
    location: str
    type: str = "community"  # school / corporate / community
    target_attendees: int = 0
    staff_assigned: List[str] = []

class EyeCampUpdateRequest(BaseModel):
    actual_attendees: Optional[int] = None
    leads_captured: Optional[int] = None
    conversions: Optional[int] = None
    notes: Optional[str] = None


# ============================================================================
# 10. DISCOUNT ANALYSIS DASHBOARD
# ============================================================================

@router.get("/discount-analysis")
async def discount_analysis(
    store_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Analyze discount patterns across orders."""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {
            "total_discount_amount": 0, "avg_discount_pct": 0,
            "by_staff": [], "excessive_discounts": [],
            "period_comparison": {"current": 0, "previous": 0, "change_pct": 0},
        }

    query: dict = {"store_id": active_store}
    now = datetime.now()
    dt_from = _parse_date(date_from)
    dt_to = _parse_date(date_to)

    if dt_from or dt_to:
        date_filter: dict = {}
        if dt_from:
            date_filter["$gte"] = dt_from.isoformat()
        if dt_to:
            date_filter["$lte"] = dt_to.isoformat()
        query["created_at"] = date_filter

    orders = list(db.get_collection("orders").find(query).limit(5000))

    total_discount = 0.0
    discount_pcts: list = []
    staff_map: dict = {}
    excessive: list = []

    for o in orders:
        disc_amt = float(o.get("discount_amount", 0) or 0)
        total_amt = float(o.get("total_amount", 0) or o.get("grand_total", 0) or 0)
        disc_pct = _safe_div(disc_amt * 100, total_amt + disc_amt) if disc_amt > 0 else 0

        total_discount += disc_amt
        if disc_amt > 0:
            discount_pcts.append(disc_pct)

        staff_id = o.get("sales_staff_id") or o.get("created_by") or "unknown"
        staff_name = o.get("sales_staff_name") or o.get("created_by_name") or staff_id
        if staff_id not in staff_map:
            staff_map[staff_id] = {"name": staff_name, "total_discount": 0, "pcts": [], "order_count": 0}
        if disc_amt > 0:
            staff_map[staff_id]["total_discount"] += disc_amt
            staff_map[staff_id]["pcts"].append(disc_pct)
            staff_map[staff_id]["order_count"] += 1

        if disc_pct > 15:
            excessive.append({
                "order_id": o.get("order_id", str(o.get("_id", ""))),
                "discount_pct": round(disc_pct, 2),
                "staff_name": staff_name,
            })

    by_staff = [
        {
            "name": v["name"],
            "total_discount": round(v["total_discount"], 2),
            "avg_pct": _safe_div(sum(v["pcts"]), len(v["pcts"])),
            "order_count": v["order_count"],
        }
        for v in staff_map.values() if v["order_count"] > 0
    ]
    by_staff.sort(key=lambda x: x["total_discount"], reverse=True)

    # Period comparison: compare to same-length previous period
    current_total = total_discount
    period_days = (dt_to - dt_from).days if dt_from and dt_to else 30
    prev_from = (dt_from or (now - timedelta(days=period_days))) - timedelta(days=period_days)
    prev_to = dt_from or (now - timedelta(days=period_days))
    prev_query: dict = {
        "store_id": active_store,
        "created_at": {"$gte": prev_from.isoformat(), "$lte": prev_to.isoformat()},
    }
    prev_orders = list(db.get_collection("orders").find(prev_query).limit(5000))
    previous_total = sum(float(o.get("discount_amount", 0) or 0) for o in prev_orders)
    change_pct = _safe_div((current_total - previous_total) * 100, previous_total) if previous_total else 0

    return {
        "total_discount_amount": round(total_discount, 2),
        "avg_discount_pct": _safe_div(sum(discount_pcts), len(discount_pcts)) if discount_pcts else 0,
        "by_staff": by_staff,
        "excessive_discounts": excessive[:50],
        "period_comparison": {
            "current": round(current_total, 2),
            "previous": round(previous_total, 2),
            "change_pct": round(change_pct, 2),
        },
    }


# ============================================================================
# 11. DEMAND FORECASTING (SUPERADMIN)
# ============================================================================

@router.get("/demand-forecast")
async def demand_forecast(
    store_id: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Predict product demand based on recent sales velocity. SUPERADMIN only."""
    if "SUPERADMIN" not in current_user.get("roles", []):
        raise HTTPException(status_code=403, detail="Superadmin access required")

    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"forecasts": []}

    now = datetime.now()
    ninety_days_ago = (now - timedelta(days=90)).isoformat()

    order_query: dict = {"store_id": active_store, "created_at": {"$gte": ninety_days_ago}}
    orders = list(db.get_collection("orders").find(order_query).limit(10000))

    # Tally sales per product
    product_sales: dict = {}
    for o in orders:
        items = o.get("items") or o.get("order_items") or []
        for item in items:
            pid = item.get("product_id", "")
            if not pid:
                continue
            cat = item.get("category", "")
            if category and cat.lower() != category.lower():
                continue
            if pid not in product_sales:
                product_sales[pid] = {
                    "product_name": item.get("product_name") or item.get("name", ""),
                    "brand": item.get("brand", ""),
                    "category": cat,
                    "qty": 0,
                    "daily_counts": {},
                }
            qty = int(item.get("quantity", 1) or 1)
            product_sales[pid]["qty"] += qty
            day_key = (o.get("created_at") or "")[:10]
            product_sales[pid]["daily_counts"][day_key] = product_sales[pid]["daily_counts"].get(day_key, 0) + qty

    # Get current stock levels
    products_coll = db.get_collection("products")

    # Sort by total qty, take top 20
    top_products = sorted(product_sales.items(), key=lambda x: x[1]["qty"], reverse=True)[:20]

    forecasts = []
    for pid, data in top_products:
        avg_daily = _safe_div(data["qty"], 90)
        predicted_30 = round(avg_daily * 30)

        # Determine trend: compare first 45 days vs last 45 days
        midpoint = (now - timedelta(days=45)).strftime("%Y-%m-%d")
        first_half = sum(v for k, v in data["daily_counts"].items() if k < midpoint)
        second_half = sum(v for k, v in data["daily_counts"].items() if k >= midpoint)
        if second_half > first_half * 1.15:
            trend = "increasing"
        elif second_half < first_half * 0.85:
            trend = "decreasing"
        else:
            trend = "stable"

        # Lookup current stock
        prod_doc = products_coll.find_one({"product_id": pid}) or {}
        current_stock = int(prod_doc.get("quantity", 0) or prod_doc.get("stock", 0) or 0)
        reorder = max(0, predicted_30 - current_stock)

        forecasts.append({
            "product_id": pid,
            "product_name": data["product_name"],
            "brand": data["brand"],
            "avg_daily_sales": round(avg_daily, 2),
            "trend": trend,
            "predicted_30_day": predicted_30,
            "current_stock": current_stock,
            "reorder_recommended": reorder,
        })

    return {"forecasts": forecasts}


# ============================================================================
# 12. DEAD STOCK IDENTIFICATION
# ============================================================================

@router.get("/dead-stock")
async def dead_stock(
    store_id: Optional[str] = Query(None),
    days_threshold: int = Query(90),
    current_user: dict = Depends(get_current_user),
):
    """Identify products with zero or declining sales."""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"dead_stock": [], "total_value": 0, "total_skus": 0}

    now = datetime.now()
    threshold_date = (now - timedelta(days=days_threshold)).isoformat()

    # Get all products for this store
    products = list(db.get_collection("products").find({"store_id": active_store}).limit(5000))

    # Get orders in the threshold period to find which products sold
    orders = list(db.get_collection("orders").find({
        "store_id": active_store,
        "created_at": {"$gte": threshold_date},
    }).limit(10000))

    sold_product_ids: set = set()
    last_sold_map: dict = {}
    for o in orders:
        items = o.get("items") or o.get("order_items") or []
        for item in items:
            pid = item.get("product_id", "")
            if pid:
                sold_product_ids.add(pid)
                order_date = o.get("created_at", "")
                if pid not in last_sold_map or order_date > last_sold_map[pid]:
                    last_sold_map[pid] = order_date

    # Also check older orders for last_sold_date of dead stock
    if products:
        unsold_pids = [p.get("product_id", "") for p in products if p.get("product_id", "") not in sold_product_ids]
        if unsold_pids:
            older_orders = list(db.get_collection("orders").find({
                "store_id": active_store,
                "items.product_id": {"$in": unsold_pids[:200]},
            }).sort("created_at", -1).limit(5000))
            for o in older_orders:
                items = o.get("items") or o.get("order_items") or []
                for item in items:
                    pid = item.get("product_id", "")
                    if pid and pid not in last_sold_map:
                        last_sold_map[pid] = o.get("created_at", "")

    dead_items = []
    total_value = 0.0
    for p in products:
        pid = p.get("product_id", "")
        if pid in sold_product_ids:
            continue

        qty = int(p.get("quantity", 0) or p.get("stock", 0) or 0)
        if qty <= 0:
            continue

        cost = float(p.get("cost_price", 0) or 0)
        value = qty * cost
        total_value += value

        last_sold = last_sold_map.get(pid)
        if last_sold:
            try:
                last_dt = datetime.fromisoformat(last_sold.replace("Z", "+00:00"))
                days_since = (now - last_dt).days
            except Exception:
                days_since = days_threshold + 1
        else:
            days_since = 999

        # Suggestion logic
        if days_since > 365:
            suggestion = "return_to_vendor"
        elif days_since > 180:
            suggestion = "clearance_sale"
        else:
            suggestion = "transfer_to_other_store"

        dead_items.append({
            "product_id": pid,
            "name": p.get("name", p.get("product_name", "")),
            "brand": p.get("brand", ""),
            "qty": qty,
            "cost_value": round(value, 2),
            "last_sold_date": last_sold,
            "days_since_last_sale": days_since,
            "suggestion": suggestion,
        })

    dead_items.sort(key=lambda x: x["cost_value"], reverse=True)

    return {
        "dead_stock": dead_items[:100],
        "total_value": round(total_value, 2),
        "total_skus": len(dead_items),
    }


# ============================================================================
# 13. LOYALTY PROGRAM ENGINE
# ============================================================================

LOYALTY_TIERS = [
    {"name": "Bronze", "min_spend": 0, "max_spend": 9999},
    {"name": "Silver", "min_spend": 10000, "max_spend": 24999},
    {"name": "Gold", "min_spend": 25000, "max_spend": 49999},
    {"name": "Platinum", "min_spend": 50000, "max_spend": 99999},
    {"name": "Diamond", "min_spend": 100000, "max_spend": float("inf")},
]


@router.get("/loyalty/tiers")
async def loyalty_tiers(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get loyalty tier distribution for the store."""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"tiers": [], "total_customers": 0, "total_points_circulation": 0}

    customers = list(db.get_collection("customers").find({"store_id": active_store}).limit(10000))

    tier_counts = {t["name"]: {"customer_count": 0, "total_points": 0} for t in LOYALTY_TIERS}
    total_points = 0

    # Calculate points redeemed this month
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    for c in customers:
        total_spend = float(c.get("total_spend", 0) or c.get("lifetime_value", 0) or 0)
        points = int(c.get("loyalty_points", 0) or 0)
        total_points += points

        for t in LOYALTY_TIERS:
            if t["min_spend"] <= total_spend <= t["max_spend"]:
                tier_counts[t["name"]]["customer_count"] += 1
                tier_counts[t["name"]]["total_points"] += points
                break

    tiers_out = [
        {
            "name": t["name"],
            "min_spend": t["min_spend"],
            "customer_count": tier_counts[t["name"]]["customer_count"],
            "total_points": tier_counts[t["name"]]["total_points"],
        }
        for t in LOYALTY_TIERS
    ]

    return {
        "tiers": tiers_out,
        "total_customers": len(customers),
        "total_points_circulation": total_points,
    }


@router.post("/loyalty/earn")
async def loyalty_earn(
    req: LoyaltyEarnRequest,
    current_user: dict = Depends(get_current_user),
):
    """Award loyalty points (1 point per Rs.100 spent)."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    points_earned = int(req.amount / 100)
    if points_earned <= 0:
        return {"message": "Amount too low for points", "points_earned": 0}

    result = db.get_collection("customers").update_one(
        {"customer_id": req.customer_id},
        {"$inc": {"loyalty_points": points_earned}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Log the earn event
    db.get_collection("loyalty_transactions").insert_one({
        "transaction_id": f"LTX-{uuid.uuid4().hex[:8].upper()}",
        "customer_id": req.customer_id,
        "order_id": req.order_id,
        "type": "earn",
        "points": points_earned,
        "amount": req.amount,
        "created_at": datetime.now().isoformat(),
        "created_by": current_user.get("user_id", "unknown"),
    })

    return {"message": f"{points_earned} points awarded", "points_earned": points_earned}


@router.post("/loyalty/redeem")
async def loyalty_redeem(
    req: LoyaltyRedeemRequest,
    current_user: dict = Depends(get_current_user),
):
    """Redeem loyalty points. 100 points = Rs.100 discount."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    customer = db.get_collection("customers").find_one({"customer_id": req.customer_id})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    current_points = int(customer.get("loyalty_points", 0) or 0)
    if req.points > current_points:
        raise HTTPException(status_code=400, detail=f"Insufficient points. Available: {current_points}")

    discount_value = req.points  # 100 points = Rs.100

    db.get_collection("customers").update_one(
        {"customer_id": req.customer_id},
        {"$inc": {"loyalty_points": -req.points}},
    )

    db.get_collection("loyalty_transactions").insert_one({
        "transaction_id": f"LTX-{uuid.uuid4().hex[:8].upper()}",
        "customer_id": req.customer_id,
        "type": "redeem",
        "points": -req.points,
        "discount_value": discount_value,
        "redemption_type": req.redemption_type,
        "created_at": datetime.now().isoformat(),
        "created_by": current_user.get("user_id", "unknown"),
    })

    return {
        "message": f"{req.points} points redeemed for Rs.{discount_value} discount",
        "points_redeemed": req.points,
        "discount_value": discount_value,
        "remaining_points": current_points - req.points,
    }


# ============================================================================
# 14. CONTACT LENS SUBSCRIPTION
# ============================================================================

@router.get("/cl-subscriptions")
async def cl_subscriptions(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get contact lens subscription / reorder schedule."""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"subscriptions": []}

    now = datetime.now()

    # Find orders with contact lens items
    orders = list(db.get_collection("orders").find({
        "store_id": active_store,
    }).sort("created_at", -1).limit(10000))

    customer_cl: dict = {}
    for o in orders:
        items = o.get("items") or o.get("order_items") or []
        for item in items:
            cat = (item.get("category", "") or "").lower()
            name = (item.get("product_name", "") or item.get("name", "") or "").lower()
            if "contact" in cat or "contact" in name or "lens" in cat:
                cid = o.get("customer_id", "")
                if cid and cid not in customer_cl:
                    customer_cl[cid] = {
                        "last_purchase_date": o.get("created_at", ""),
                        "lens_type": item.get("product_name") or item.get("name", "Contact Lens"),
                    }

    subscriptions = []
    cust_coll = db.get_collection("customers")
    for cid, cl_data in customer_cl.items():
        customer = cust_coll.find_one({"customer_id": cid}) or {}
        last_date = cl_data["last_purchase_date"]
        try:
            last_dt = datetime.fromisoformat(last_date.replace("Z", "+00:00")) if last_date else now
        except Exception:
            last_dt = now

        # Typical CL reorder: 90 days for monthly, 30 for daily disposable
        reorder_due = last_dt + timedelta(days=90)
        if reorder_due < now:
            status = "overdue"
        elif reorder_due < now + timedelta(days=14):
            status = "due_soon"
        else:
            status = "active"

        subscriptions.append({
            "customer_id": cid,
            "name": customer.get("name", ""),
            "phone": customer.get("mobile", ""),
            "last_purchase_date": last_date,
            "lens_type": cl_data["lens_type"],
            "reorder_due_date": reorder_due.isoformat(),
            "status": status,
        })

    subscriptions.sort(key=lambda x: x["reorder_due_date"])

    return {"subscriptions": subscriptions}


@router.post("/cl-subscription/reminder/{customer_id}")
async def cl_subscription_reminder(
    customer_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Send contact lens reorder reminder."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    customer = db.get_collection("customers").find_one({"customer_id": customer_id})
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    store_id = current_user.get("active_store_id", "")
    store = db.get_collection("stores").find_one({"store_id": store_id}) or {}

    result = await send_notification(
        store_id=store_id,
        customer_id=customer_id,
        customer_phone=customer.get("mobile", ""),
        customer_name=customer.get("name", "Customer"),
        template_id="CL_REORDER_REMINDER",
        channel="WHATSAPP",
        variables={
            "store_name": store.get("name", "Better Vision"),
            "store_phone": store.get("phone", ""),
        },
        category="REMINDER",
        triggered_by=current_user.get("user_id", "unknown"),
        related_entity_type="cl_subscription",
        related_entity_id=customer_id,
    )
    return {"message": "Contact lens reorder reminder sent", "notification": result}


# ============================================================================
# 18. EYE CAMP MANAGEMENT
# ============================================================================

@router.get("/eye-camps")
async def list_eye_camps(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List eye camps for the store."""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"eye_camps": [], "total": 0}

    camps = list(db.get_collection("eye_camps").find({"store_id": active_store}).sort("date", -1).limit(200))
    for c in camps:
        c.pop("_id", None)

    return {"eye_camps": camps, "total": len(camps)}


@router.post("/eye-camps")
async def create_eye_camp(
    req: EyeCampCreateRequest,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Create a new eye camp."""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    camp_id = f"CAMP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    camp = {
        "camp_id": camp_id,
        "store_id": active_store,
        "name": req.name,
        "date": req.date,
        "location": req.location,
        "type": req.type,
        "target_attendees": req.target_attendees,
        "staff_assigned": req.staff_assigned,
        "actual_attendees": None,
        "leads_captured": None,
        "conversions": None,
        "notes": None,
        "status": "planned",
        "created_at": datetime.now().isoformat(),
        "created_by": current_user.get("user_id", "unknown"),
    }

    db.get_collection("eye_camps").insert_one(camp)
    camp.pop("_id", None)

    return {"message": "Eye camp created", "eye_camp": camp}


@router.patch("/eye-camps/{camp_id}")
async def update_eye_camp(
    camp_id: str,
    req: EyeCampUpdateRequest,
    current_user: dict = Depends(get_current_user),
):
    """Update eye camp results."""
    db = _get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database unavailable")

    update_fields: dict = {"updated_at": datetime.now().isoformat()}
    if req.actual_attendees is not None:
        update_fields["actual_attendees"] = req.actual_attendees
    if req.leads_captured is not None:
        update_fields["leads_captured"] = req.leads_captured
    if req.conversions is not None:
        update_fields["conversions"] = req.conversions
    if req.notes is not None:
        update_fields["notes"] = req.notes

    if req.actual_attendees is not None:
        update_fields["status"] = "completed"

    result = db.get_collection("eye_camps").update_one(
        {"camp_id": camp_id},
        {"$set": update_fields},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Eye camp not found")

    return {"message": "Eye camp updated", "camp_id": camp_id}


# ============================================================================
# 19. FAMILY PACKAGE DEALS
# ============================================================================

@router.get("/family-deals")
async def family_deals(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Identify families with multiple members needing eyewear."""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"families": []}

    customers = list(db.get_collection("customers").find({"store_id": active_store}).limit(10000))
    rx_coll = db.get_collection("prescriptions")

    # Group by phone or family linkage
    phone_groups: dict = {}
    for c in customers:
        phone = c.get("mobile", "") or c.get("phone", "")
        if not phone or len(phone) < 10:
            continue
        phone_key = phone[-10:]  # last 10 digits
        if phone_key not in phone_groups:
            phone_groups[phone_key] = []
        phone_groups[phone_key].append(c)

    families = []
    for phone, members in phone_groups.items():
        if len(members) < 2:
            continue

        members_with_rx = 0
        total_potential = 0
        for m in members:
            cid = m.get("customer_id", "")
            rx = rx_coll.find_one({"customer_id": cid}) if cid else None
            if rx:
                members_with_rx += 1
                total_potential += 3000  # average frame + lens value

        if members_with_rx < 2:
            continue

        primary = members[0]
        if members_with_rx >= 4:
            deal = "Premium Family Pack - 25% off"
        elif members_with_rx >= 3:
            deal = "Family Bundle - 20% off"
        else:
            deal = "Duo Deal - 15% off for 2 pairs"

        families.append({
            "customer_id": primary.get("customer_id", ""),
            "customer_name": primary.get("name", ""),
            "family_size": len(members),
            "members_with_rx": members_with_rx,
            "total_potential_value": total_potential,
            "suggested_deal": deal,
        })

    families.sort(key=lambda x: x["members_with_rx"], reverse=True)

    return {"families": families}


# ============================================================================
# 20. STAFF PERFORMANCE GAMIFICATION
# ============================================================================

@router.get("/staff-leaderboard")
async def staff_leaderboard(
    store_id: Optional[str] = Query(None),
    period: Optional[str] = Query("month"),
    current_user: dict = Depends(get_current_user),
):
    """Staff performance leaderboard with gamification."""
    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"leaderboard": [], "period": period}

    now = datetime.now()
    if period == "today":
        from_date = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    elif period == "week":
        from_date = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    else:
        from_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    orders = list(db.get_collection("orders").find({
        "store_id": active_store,
        "created_at": {"$gte": from_date},
    }).limit(10000))

    staff_stats: dict = {}
    for o in orders:
        sid = o.get("sales_staff_id") or o.get("created_by") or "unknown"
        sname = o.get("sales_staff_name") or o.get("created_by_name") or sid
        if sid not in staff_stats:
            staff_stats[sid] = {"name": sname, "sales_count": 0, "revenue": 0, "with_addons": 0}
        staff_stats[sid]["sales_count"] += 1
        staff_stats[sid]["revenue"] += float(o.get("total_amount", 0) or o.get("grand_total", 0) or 0)

        # Check for upsells / add-ons
        items = o.get("items") or o.get("order_items") or []
        if len(items) > 1:
            staff_stats[sid]["with_addons"] += 1

    leaderboard = []
    for sid, stats in staff_stats.items():
        avg_txn = _safe_div(stats["revenue"], stats["sales_count"])
        upsell_rate = _safe_div(stats["with_addons"] * 100, stats["sales_count"])

        leaderboard.append({
            "staff_id": sid,
            "name": stats["name"],
            "sales_count": stats["sales_count"],
            "revenue": round(stats["revenue"], 2),
            "avg_txn": round(avg_txn, 2),
            "upsell_rate": round(upsell_rate, 1),
            "rank": 0,
            "badge": "",
        })

    leaderboard.sort(key=lambda x: x["revenue"], reverse=True)

    # Assign ranks and badges
    for i, entry in enumerate(leaderboard):
        entry["rank"] = i + 1
        if i == 0:
            entry["badge"] = "Champion"
        elif i == 1:
            entry["badge"] = "Star Performer"
        elif i == 2:
            entry["badge"] = "Rising Star"
        elif entry["upsell_rate"] > 50:
            entry["badge"] = "Upsell Master"
        else:
            entry["badge"] = "Team Player"

    return {"leaderboard": leaderboard, "period": period}


# ============================================================================
# 22. CUSTOMER CHURN PREDICTION (SUPERADMIN)
# ============================================================================

@router.get("/churn-prediction")
async def churn_prediction(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Predict customer churn based on purchase recency. SUPERADMIN only."""
    if "SUPERADMIN" not in current_user.get("roles", []):
        raise HTTPException(status_code=403, detail="Superadmin access required")

    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"at_risk": [], "summary": {"at_risk_count": 0, "churned_count": 0, "total_at_risk_value": 0}}

    now = datetime.now()
    customers = list(db.get_collection("customers").find({"store_id": active_store}).limit(10000))

    at_risk_list = []
    churned_count = 0
    at_risk_count = 0
    total_value = 0.0

    for c in customers:
        last_purchase = c.get("last_purchase_date") or c.get("last_visit_date") or c.get("updated_at")
        if not last_purchase:
            continue

        try:
            if isinstance(last_purchase, str):
                last_dt = datetime.fromisoformat(last_purchase.replace("Z", "+00:00"))
            else:
                last_dt = last_purchase
        except Exception:
            continue

        days_inactive = (now - last_dt).days
        if days_inactive < 180:
            continue

        ltv = float(c.get("total_spend", 0) or c.get("lifetime_value", 0) or 0)

        # Calculate churn probability
        if days_inactive >= 365:
            status = "churned"
            churn_prob = min(0.95, 0.7 + (days_inactive - 365) / 1000)
            churned_count += 1
        elif days_inactive >= 180:
            status = "at_risk"
            churn_prob = 0.3 + (days_inactive - 180) / 600
            at_risk_count += 1
        else:
            continue

        total_value += ltv

        at_risk_list.append({
            "customer_id": c.get("customer_id", ""),
            "name": c.get("name", ""),
            "phone": c.get("mobile", ""),
            "last_purchase_date": last_purchase if isinstance(last_purchase, str) else last_purchase.isoformat(),
            "days_inactive": days_inactive,
            "lifetime_value": round(ltv, 2),
            "churn_probability": round(churn_prob, 2),
            "status": status,
        })

    at_risk_list.sort(key=lambda x: x["lifetime_value"], reverse=True)

    return {
        "at_risk": at_risk_list[:200],
        "summary": {
            "at_risk_count": at_risk_count,
            "churned_count": churned_count,
            "total_at_risk_value": round(total_value, 2),
        },
    }


# ============================================================================
# 23. FRAUD / THEFT ANOMALY DETECTION (SUPERADMIN)
# ============================================================================

@router.get("/anomaly-detection")
async def anomaly_detection(
    store_id: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Detect fraud and theft anomalies in orders. SUPERADMIN only."""
    if "SUPERADMIN" not in current_user.get("roles", []):
        raise HTTPException(status_code=403, detail="Superadmin access required")

    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"anomalies": [], "summary": {"total_anomalies": 0, "critical_count": 0}}

    now = datetime.now()
    dt_from = _parse_date(date_from) or (now - timedelta(days=30))
    dt_to = _parse_date(date_to) or now

    orders = list(db.get_collection("orders").find({
        "store_id": active_store,
        "created_at": {"$gte": dt_from.isoformat(), "$lte": dt_to.isoformat()},
    }).limit(10000))

    anomalies = []

    # Track per-staff metrics
    staff_voids: dict = {}
    staff_refunds: dict = {}
    staff_discounts: dict = {}
    daily_orders: dict = {}

    for o in orders:
        sid = o.get("sales_staff_id") or o.get("created_by") or "unknown"
        sname = o.get("sales_staff_name") or o.get("created_by_name") or sid
        status = (o.get("status", "") or "").lower()
        oid = o.get("order_id", str(o.get("_id", "")))
        order_date = (o.get("created_at", "") or "")[:10]

        # 1. Excessive voids / cancellations
        if status in ("void", "voided", "cancelled", "canceled"):
            staff_voids.setdefault(sid, {"name": sname, "count": 0, "order_ids": []})
            staff_voids[sid]["count"] += 1
            staff_voids[sid]["order_ids"].append(oid)

        # 2. Unusual refund patterns
        if status in ("refunded", "refund"):
            staff_refunds.setdefault(sid, {"name": sname, "count": 0, "total": 0, "order_ids": []})
            staff_refunds[sid]["count"] += 1
            staff_refunds[sid]["total"] += float(o.get("total_amount", 0) or 0)
            staff_refunds[sid]["order_ids"].append(oid)

        # 3. Excessive discounts
        disc_amt = float(o.get("discount_amount", 0) or 0)
        total_amt = float(o.get("total_amount", 0) or o.get("grand_total", 0) or 0)
        if disc_amt > 0:
            disc_pct = _safe_div(disc_amt * 100, total_amt + disc_amt)
            if disc_pct > 20:
                staff_discounts.setdefault(sid, {"name": sname, "orders": []})
                staff_discounts[sid]["orders"].append({"order_id": oid, "disc_pct": disc_pct})

        # 4. Same-day void-and-recreate patterns
        day_key = f"{sid}_{order_date}"
        daily_orders.setdefault(day_key, {"sid": sid, "name": sname, "date": order_date, "statuses": []})
        daily_orders[day_key]["statuses"].append(status)

    # Generate anomaly records
    for sid, data in staff_voids.items():
        if data["count"] >= 3:
            anomalies.append({
                "type": "excessive_voids",
                "severity": "critical" if data["count"] >= 5 else "warning",
                "staff_id": sid,
                "staff_name": data["name"],
                "description": f"{data['count']} voided/cancelled orders",
                "order_ids": data["order_ids"][:10],
                "date": dt_from.strftime("%Y-%m-%d") + " to " + dt_to.strftime("%Y-%m-%d"),
            })

    for sid, data in staff_refunds.items():
        if data["count"] >= 3:
            anomalies.append({
                "type": "unusual_refunds",
                "severity": "critical" if data["count"] >= 5 else "warning",
                "staff_id": sid,
                "staff_name": data["name"],
                "description": f"{data['count']} refunds totaling Rs.{round(data['total'], 2)}",
                "order_ids": data["order_ids"][:10],
                "date": dt_from.strftime("%Y-%m-%d") + " to " + dt_to.strftime("%Y-%m-%d"),
            })

    for sid, data in staff_discounts.items():
        if len(data["orders"]) >= 2:
            anomalies.append({
                "type": "excessive_discounts",
                "severity": "warning",
                "staff_id": sid,
                "staff_name": data["name"],
                "description": f"{len(data['orders'])} orders with >20% discount",
                "order_ids": [o["order_id"] for o in data["orders"][:10]],
                "date": dt_from.strftime("%Y-%m-%d") + " to " + dt_to.strftime("%Y-%m-%d"),
            })

    # Same-day void-and-recreate
    for key, data in daily_orders.items():
        statuses = data["statuses"]
        voided = sum(1 for s in statuses if s in ("void", "voided", "cancelled", "canceled"))
        active = sum(1 for s in statuses if s not in ("void", "voided", "cancelled", "canceled", "refunded", "refund"))
        if voided >= 1 and active >= 1 and voided + active >= 3:
            anomalies.append({
                "type": "void_and_recreate",
                "severity": "critical",
                "staff_id": data["sid"],
                "staff_name": data["name"],
                "description": f"Same-day void ({voided}) and create ({active}) pattern on {data['date']}",
                "order_ids": [],
                "date": data["date"],
            })

    critical_count = sum(1 for a in anomalies if a["severity"] == "critical")

    return {
        "anomalies": anomalies,
        "summary": {
            "total_anomalies": len(anomalies),
            "critical_count": critical_count,
        },
    }


# ============================================================================
# 25. VENDOR MARGIN INSIGHTS (SUPERADMIN)
# ============================================================================

@router.get("/vendor-margins")
async def vendor_margins(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Analyze vendor/brand margins. SUPERADMIN only."""
    if "SUPERADMIN" not in current_user.get("roles", []):
        raise HTTPException(status_code=403, detail="Superadmin access required")

    active_store = store_id or current_user.get("active_store_id", "")
    db = _get_db()
    if not db:
        return {"vendors": [], "best_margin_products": [], "worst_margin_products": []}

    products = list(db.get_collection("products").find({"store_id": active_store}).limit(10000))

    vendor_map: dict = {}
    all_margins: list = []

    for p in products:
        cost = float(p.get("cost_price", 0) or 0)
        sell = float(p.get("selling_price", 0) or p.get("mrp", 0) or 0)
        if cost <= 0 or sell <= 0:
            continue

        margin_pct = round((sell - cost) / sell * 100, 2)
        vendor = p.get("vendor_name") or p.get("vendor", "") or p.get("supplier", "") or "Unknown"
        brand = p.get("brand", "") or "Unknown"
        vkey = f"{vendor}|{brand}"

        if vkey not in vendor_map:
            vendor_map[vkey] = {
                "vendor_name": vendor,
                "brand": brand,
                "total_revenue": 0,
                "total_cost": 0,
                "product_count": 0,
                "margins": [],
            }
        vendor_map[vkey]["total_revenue"] += sell
        vendor_map[vkey]["total_cost"] += cost
        vendor_map[vkey]["product_count"] += 1
        vendor_map[vkey]["margins"].append(margin_pct)

        all_margins.append({
            "product_id": p.get("product_id", ""),
            "name": p.get("name", p.get("product_name", "")),
            "brand": brand,
            "cost_price": cost,
            "selling_price": sell,
            "margin_pct": margin_pct,
        })

    vendors_out = [
        {
            "vendor_name": v["vendor_name"],
            "brand": v["brand"],
            "avg_margin_pct": _safe_div(sum(v["margins"]), len(v["margins"])),
            "total_revenue": round(v["total_revenue"], 2),
            "total_cost": round(v["total_cost"], 2),
            "product_count": v["product_count"],
        }
        for v in vendor_map.values()
    ]
    vendors_out.sort(key=lambda x: x["avg_margin_pct"], reverse=True)

    all_margins.sort(key=lambda x: x["margin_pct"], reverse=True)
    best = all_margins[:10]
    worst = all_margins[-10:][::-1] if len(all_margins) >= 10 else all_margins[::-1]

    return {
        "vendors": vendors_out,
        "best_margin_products": best,
        "worst_margin_products": worst,
    }
