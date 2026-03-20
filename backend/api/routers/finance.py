# Finance & Accounting Router — _get_db() pattern (matches working routers)

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from .auth import get_current_user

router = APIRouter(prefix="/finance", tags=["finance"])

def _get_db():
    from database.connection import get_db
    return get_db().db


# === Revenue Tracking ===

@router.get("/revenue")
async def get_revenue(
    period: str = Query("month", pattern="^(day|week|month|year)$"),
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    now = datetime.utcnow()

    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(month=4 if now.month >= 4 else 4, day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month < 4:
            start = start.replace(year=now.year - 1)

    match = {"created_at": {"$gte": start.isoformat()}}
    if store_id:
        match["store_id"] = store_id

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": None,
            "total_revenue": {"$sum": "$total"},
            "total_orders": {"$sum": 1},
            "total_tax": {"$sum": "$tax_amount"},
            "total_discount": {"$sum": {"$ifNull": ["$discount_amount", 0]}},
        }}
    ]
    result = list(db.get_collection("orders").aggregate(pipeline))
    current = result[0] if result else {"total_revenue": 0, "total_orders": 0, "total_tax": 0, "total_discount": 0}

    # Previous period for MoM/YoY
    if period == "month":
        prev_start = (start - timedelta(days=1)).replace(day=1)
        prev_match = {"created_at": {"$gte": prev_start.isoformat(), "$lt": start.isoformat()}}
        if store_id:
            prev_match["store_id"] = store_id
        prev_result = list(db.get_collection("orders").aggregate([{"$match": prev_match}, {"$group": {"_id": None, "total_revenue": {"$sum": "$total"}}}]))
        prev_revenue = prev_result[0]["total_revenue"] if prev_result else 0
        mom_growth = ((current["total_revenue"] - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else 0
    else:
        mom_growth = 0

    return {
        "total_revenue": current["total_revenue"],
        "total_orders": current["total_orders"],
        "total_tax": current["total_tax"],
        "total_discount": current["total_discount"],
        "avg_order_value": current["total_revenue"] / current["total_orders"] if current["total_orders"] > 0 else 0,
        "mom_growth": round(mom_growth, 1),
        "period": period,
    }


# === Profit & Loss ===

@router.get("/pnl")
async def get_pnl(
    store_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    match = {}
    if store_id:
        match["store_id"] = store_id
    if from_date:
        match.setdefault("created_at", {})["$gte"] = from_date
    if to_date:
        match.setdefault("created_at", {})["$lte"] = to_date

    # Revenue
    rev_pipeline = [{"$match": match}, {"$group": {"_id": None, "revenue": {"$sum": "$total"}, "tax": {"$sum": "$tax_amount"}}}]
    rev = list(db.get_collection("orders").aggregate(rev_pipeline))
    revenue = rev[0]["revenue"] if rev else 0
    tax = rev[0]["tax"] if rev else 0

    # Expenses
    exp_match = {}
    if store_id:
        exp_match["store_id"] = store_id
    if from_date:
        exp_match.setdefault("date", {})["$gte"] = from_date
    if to_date:
        exp_match.setdefault("date", {})["$lte"] = to_date
    exp_pipeline = [{"$match": exp_match}, {"$group": {"_id": "$category", "amount": {"$sum": "$amount"}}}]
    expenses = list(db.get_collection("expenses").aggregate(exp_pipeline))
    total_expenses = sum(e["amount"] for e in expenses)

    # COGS estimate (60% of revenue for optical retail)
    cogs = revenue * 0.6
    gross_profit = revenue - cogs
    net_profit = gross_profit - total_expenses

    return {
        "revenue": revenue,
        "cogs": cogs,
        "gross_profit": gross_profit,
        "gross_margin": round(gross_profit / revenue * 100, 1) if revenue > 0 else 0,
        "expenses": {e["_id"]: e["amount"] for e in expenses},
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "net_margin": round(net_profit / revenue * 100, 1) if revenue > 0 else 0,
        "tax_collected": tax,
    }


# === GST Management ===

@router.get("/gst/summary")
async def get_gst_summary(
    month: Optional[int] = None,
    year: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    now = datetime.utcnow()
    m = month or now.month
    y = year or now.year

    start = datetime(y, m, 1)
    if m == 12:
        end = datetime(y + 1, 1, 1)
    else:
        end = datetime(y, m + 1, 1)

    # GST collected (from sales)
    sales_match = {"created_at": {"$gte": start.isoformat(), "$lt": end.isoformat()}}
    collected = list(db.get_collection("orders").aggregate([
        {"$match": sales_match},
        {"$group": {"_id": None, "total_tax": {"$sum": "$tax_amount"}, "total_sales": {"$sum": "$total"}}}
    ]))
    gst_collected = collected[0]["total_tax"] if collected else 0

    # GST paid (input credit from purchases)
    purchase_match = {"date": {"$gte": start.isoformat(), "$lt": end.isoformat()}}
    paid = list(db.get_collection("purchase_orders").aggregate([
        {"$match": purchase_match},
        {"$group": {"_id": None, "total_tax": {"$sum": {"$ifNull": ["$tax_amount", 0]}}}}
    ]))
    gst_paid = paid[0]["total_tax"] if paid else 0

    cgst = gst_collected / 2
    sgst = gst_collected / 2
    net_payable = gst_collected - gst_paid

    # Filing status
    gstr1_due = datetime(y, m + 1 if m < 12 else 1, 11)
    gstr3b_due = datetime(y, m + 1 if m < 12 else 1, 20)

    return {
        "month": m, "year": y,
        "gst_collected": gst_collected,
        "cgst": cgst, "sgst": sgst,
        "gst_input_credit": gst_paid,
        "net_gst_payable": net_payable,
        "gstr1_due_date": gstr1_due.isoformat(),
        "gstr3b_due_date": gstr3b_due.isoformat(),
        "gstr1_filed": False,
        "gstr3b_filed": False,
    }


# === Outstanding Receivables ===

@router.get("/outstanding")
async def get_outstanding(
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    match = {"payment_status": {"$in": ["unpaid", "partial", "credit"]}}
    if store_id:
        match["store_id"] = store_id

    orders = list(db.get_collection("orders").find(match, {"_id": 0, "order_id": 1, "customer_name": 1, "customer_phone": 1, "total": 1, "amount_paid": 1, "created_at": 1}))

    now = datetime.utcnow()
    buckets = {"0_30": 0, "31_60": 0, "61_90": 0, "90_plus": 0}
    items = []

    for o in orders:
        balance = o.get("total", 0) - o.get("amount_paid", 0)
        if balance <= 0:
            continue
        created = datetime.fromisoformat(o.get("created_at", now.isoformat()))
        days = (now - created).days

        if days <= 30: buckets["0_30"] += balance
        elif days <= 60: buckets["31_60"] += balance
        elif days <= 90: buckets["61_90"] += balance
        else: buckets["90_plus"] += balance

        items.append({
            "order_id": o.get("order_id"),
            "customer_name": o.get("customer_name", "Unknown"),
            "customer_phone": o.get("customer_phone", ""),
            "amount": balance,
            "days_overdue": days,
        })

    return {"buckets": buckets, "total_outstanding": sum(buckets.values()), "items": sorted(items, key=lambda x: -x["days_overdue"])}


# === Vendor Payments ===

@router.get("/vendor-payments")
async def get_vendor_payments(current_user: dict = Depends(get_current_user)):
    db = _get_db()
    vendors = list(db.get_collection("vendors").find({}, {"_id": 0, "vendor_id": 1, "name": 1}))
    result = []
    for v in vendors:
        pos = list(db.get_collection("purchase_orders").find({"vendor_id": v["vendor_id"]}, {"_id": 0, "total": 1, "payment_status": 1}))
        total = sum(p.get("total", 0) for p in pos)
        paid = sum(p.get("total", 0) for p in pos if p.get("payment_status") == "paid")
        result.append({"vendor_id": v["vendor_id"], "vendor_name": v["name"], "total_orders": total, "total_paid": paid, "balance": total - paid})
    return result


# === Cash Flow ===

@router.get("/cash-flow")
async def get_cash_flow(
    period: str = Query("month"),
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    now = datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Inflows (from orders)
    inflow = list(db.get_collection("orders").aggregate([
        {"$match": {"created_at": {"$gte": start.isoformat()}, "payment_status": "paid"}},
        {"$group": {"_id": None, "total": {"$sum": "$total"}}}
    ]))
    total_inflow = inflow[0]["total"] if inflow else 0

    # Outflows (expenses + purchase orders)
    exp_out = list(db.get_collection("expenses").aggregate([
        {"$match": {"date": {"$gte": start.isoformat()}, "status": "approved"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
    ]))
    po_out = list(db.get_collection("purchase_orders").aggregate([
        {"$match": {"date": {"$gte": start.isoformat()}, "payment_status": "paid"}},
        {"$group": {"_id": None, "total": {"$sum": "$total"}}}
    ]))
    total_outflow = (exp_out[0]["total"] if exp_out else 0) + (po_out[0]["total"] if po_out else 0)

    return {
        "period": period,
        "inflows": total_inflow,
        "outflows": total_outflow,
        "net_cash_flow": total_inflow - total_outflow,
        "expense_outflow": exp_out[0]["total"] if exp_out else 0,
        "purchase_outflow": po_out[0]["total"] if po_out else 0,
    }


# === Period Lock ===

@router.post("/period-lock")
async def lock_period(
    month: int, year: int,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    if "SUPERADMIN" not in current_user.get("roles", []) and "ADMIN" not in current_user.get("roles", []):
        raise HTTPException(403, "Only admin/superadmin can lock periods")

    existing = db.get_collection("period_locks").find_one({"month": month, "year": year})
    if existing:
        raise HTTPException(400, f"Period {month}/{year} is already locked")

    db.get_collection("period_locks").insert_one({
        "month": month, "year": year,
        "locked_by": current_user.get("user_id"),
        "locked_at": datetime.utcnow().isoformat(),
    })
    return {"message": f"Period {month}/{year} locked", "month": month, "year": year}

@router.get("/period-locks")
async def get_period_locks(current_user: dict = Depends(get_current_user)):
    db = _get_db()
    locks = list(db.get_collection("period_locks").find({}, {"_id": 0}))
    return locks


# === Budget ===

@router.get("/budget")
async def get_budget(
    mode: str = Query("full", pattern="^(full|survival)$"),
    month: Optional[int] = None,
    year: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    now = datetime.utcnow()
    m = month or now.month
    y = year or now.year

    budget = db.get_collection("budgets").find_one({"month": m, "year": y, "mode": mode}, {"_id": 0})
    if not budget:
        # Default budget structure
        budget = {
            "month": m, "year": y, "mode": mode,
            "categories": {
                "rent": {"budget": 50000, "actual": 0},
                "salaries": {"budget": 200000, "actual": 0},
                "utilities": {"budget": 15000, "actual": 0},
                "marketing": {"budget": 30000, "actual": 0},
                "inventory": {"budget": 500000, "actual": 0},
                "miscellaneous": {"budget": 20000, "actual": 0},
            }
        }

    # Fill actuals from expenses
    start = datetime(y, m, 1)
    end = datetime(y, m + 1 if m < 12 else 1, 1) if m < 12 else datetime(y + 1, 1, 1)
    actuals = list(db.get_collection("expenses").aggregate([
        {"$match": {"date": {"$gte": start.isoformat(), "$lt": end.isoformat()}, "status": "approved"}},
        {"$group": {"_id": "$category", "total": {"$sum": "$amount"}}}
    ]))
    for a in actuals:
        cat = a["_id"].lower() if a["_id"] else "miscellaneous"
        if cat in budget.get("categories", {}):
            budget["categories"][cat]["actual"] = a["total"]

    return budget


# === Reconciliation ===

@router.get("/reconciliation")
async def get_reconciliation(current_user: dict = Depends(get_current_user)):
    db = _get_db()
    # Inter-store transfers needing reconciliation
    pending = list(db.get_collection("stock_transfers").find(
        {"status": {"$in": ["shipped", "in_transit"]}},
        {"_id": 0, "transfer_id": 1, "from_store": 1, "to_store": 1, "items": 1, "created_at": 1}
    ).limit(50))

    return {
        "pending_transfers": len(pending),
        "transfers": pending,
    }
