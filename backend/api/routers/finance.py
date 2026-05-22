# Finance & Accounting Router — _get_db() pattern (matches working routers)

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from .auth import get_current_user

router = APIRouter(prefix="/finance", tags=["finance"])


def _get_db():
    from database.connection import get_db

    return get_db().db


# ── Field/status tolerance ────────────────────────────────────────────────
# Orders store `grand_total` (not `total`), `tax_amount`, `discount_total`, and
# UPPERCASE payment_status ("UNPAID"/"PARTIAL"/"PAID"); expenses use UPPERCASE
# status ("APPROVED"). The original finance queries summed `$total` and matched
# lowercase, so revenue / receivables / cash-flow all read as zero.
PAID_STATUSES = ["PAID", "paid"]
UNPAID_STATUSES = ["UNPAID", "PARTIAL", "CREDIT", "unpaid", "partial", "credit"]
APPROVED_STATUSES = ["APPROVED", "approved"]

# Aggregation expressions tolerant of legacy field names.
_REVENUE_EXPR = {"$ifNull": ["$grand_total", {"$ifNull": ["$total", 0]}]}
_TAX_EXPR = {"$ifNull": ["$tax_amount", {"$ifNull": ["$tax_total", 0]}]}
_DISCOUNT_EXPR = {"$ifNull": ["$discount_total", {"$ifNull": ["$discount_amount", 0]}]}


def _order_total(o: dict) -> float:
    v = o.get("grand_total")
    if v is None:
        v = o.get("total", 0)
    return float(v or 0)


def compute_cogs(orders, cost_by_product: dict, fallback_rate: float = 0.0) -> float:
    """Real COGS: sum cost_price * qty over order line items. Falls back to
    fallback_rate * line-total only when a product's cost is unknown. Pure."""
    cogs = 0.0
    for o in orders:
        for it in (o.get("items") or []):
            pid = it.get("product_id")
            qty = it.get("quantity", 1) or 1
            cost = cost_by_product.get(pid) if pid else None
            if cost is not None:
                cogs += float(cost) * float(qty)
            elif fallback_rate:
                cogs += float(it.get("total", 0) or 0) * fallback_rate
    return round(cogs, 2)


def _cost_by_product(db) -> dict:
    """product_id (and _id) -> cost_price, for COGS. Keyed both ways because
    imported orders may reference a product by its Mongo _id."""
    out: dict = {}
    try:
        for p in db.get_collection("products").find({}, {"product_id": 1, "cost_price": 1}):
            cp = p.get("cost_price")
            if cp is None:
                continue
            try:
                val = float(cp)
            except Exception:
                continue
            if p.get("product_id"):
                out[p["product_id"]] = val
            if p.get("_id") is not None:
                out[str(p["_id"])] = val
    except Exception:
        pass
    return out


def _months_in_range(from_date, to_date):
    """(year, month) tuples overlapping an ISO date range; current month if open."""
    def _parse(s):
        try:
            return datetime.fromisoformat(s[:10])
        except Exception:
            return None

    start = _parse(from_date) if from_date else None
    end = _parse(to_date) if to_date else None
    now = datetime.utcnow()
    if not start and not end:
        return [(now.year, now.month)]
    start = start or end
    end = end or start
    months, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month) and len(months) < 36:
        months.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


def _payroll_cost(db, store_id, from_date, to_date) -> float:
    """Cost-to-company payroll for the months overlapping the P&L range."""
    try:
        months = _months_in_range(from_date, to_date)
        if not months:
            return 0.0
        q: dict = {"$or": [{"year": y, "month": m} for (y, m) in months]}
        if store_id:
            q["store_id"] = store_id
        total = 0.0
        for r in db.get_collection("payroll").find(q):
            bd = r.get("breakdown") or {}
            total += bd.get("ctc_cost", r.get("net_salary", 0)) or 0
        return round(total, 2)
    except Exception:
        return 0.0


def gst_reconciliation(orders, purchases, store_to_entity: dict, entity_names: dict = None) -> dict:
    """Group GST output (orders) vs input credit (purchases) by legal entity.
    CGST/SGST split intra-state (tax/2 each); IGST not modelled (orders don't
    capture inter-state). Pure."""
    entity_names = entity_names or {}
    acc: dict = {}

    def _ent(store_id):
        return store_to_entity.get(store_id) or "_unassigned"

    for o in orders:
        eid = _ent(o.get("store_id"))
        tax = float(o.get("tax_amount") or o.get("tax_total") or 0)
        acc.setdefault(eid, {"collected": 0.0, "input_credit": 0.0})["collected"] += tax
    for p in purchases:
        eid = _ent(p.get("delivery_store_id") or p.get("store_id"))
        tax = float(p.get("tax_amount") or 0)
        acc.setdefault(eid, {"collected": 0.0, "input_credit": 0.0})["input_credit"] += tax

    entities, tot_c, tot_i = [], 0.0, 0.0
    for eid, d in acc.items():
        c, i = round(d["collected"], 2), round(d["input_credit"], 2)
        tot_c += c
        tot_i += i
        entities.append({
            "entity_id": eid,
            "entity_name": entity_names.get(eid, eid),
            "gst_collected": c,
            "cgst": round(c / 2, 2),
            "sgst": round(c / 2, 2),
            "input_credit": i,
            "net_payable": round(c - i, 2),
        })
    return {
        "entities": sorted(entities, key=lambda e: -e["gst_collected"]),
        "total_collected": round(tot_c, 2),
        "total_input_credit": round(tot_i, 2),
        "total_net_payable": round(tot_c - tot_i, 2),
    }


def _store_maps(db):
    """Return (store_id -> entity_id, entity_id -> entity_name)."""
    s2e, enames = {}, {}
    try:
        for s in db.get_collection("stores").find({}, {"_id": 0, "store_id": 1, "entity_id": 1}):
            if s.get("store_id"):
                s2e[s["store_id"]] = s.get("entity_id")
        for e in db.get_collection("entities").find({}, {"_id": 0, "entity_id": 1, "name": 1}):
            enames[e.get("entity_id")] = e.get("name")
    except Exception:
        pass
    return s2e, enames


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
        start = now.replace(
            month=4 if now.month >= 4 else 4,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        if now.month < 4:
            start = start.replace(year=now.year - 1)

    match = {"created_at": {"$gte": start.isoformat()}}
    if store_id:
        match["store_id"] = store_id

    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "total_revenue": {"$sum": _REVENUE_EXPR},
                "total_orders": {"$sum": 1},
                "total_tax": {"$sum": _TAX_EXPR},
                "total_discount": {"$sum": _DISCOUNT_EXPR},
            }
        },
    ]
    result = list(db.get_collection("orders").aggregate(pipeline))
    current = (
        result[0]
        if result
        else {
            "total_revenue": 0,
            "total_orders": 0,
            "total_tax": 0,
            "total_discount": 0,
        }
    )

    # Previous period for MoM/YoY
    if period == "month":
        prev_start = (start - timedelta(days=1)).replace(day=1)
        prev_match = {
            "created_at": {"$gte": prev_start.isoformat(), "$lt": start.isoformat()}
        }
        if store_id:
            prev_match["store_id"] = store_id
        prev_result = list(
            db.get_collection("orders").aggregate(
                [
                    {"$match": prev_match},
                    {"$group": {"_id": None, "total_revenue": {"$sum": _REVENUE_EXPR}}},
                ]
            )
        )
        prev_revenue = prev_result[0]["total_revenue"] if prev_result else 0
        mom_growth = (
            ((current["total_revenue"] - prev_revenue) / prev_revenue * 100)
            if prev_revenue > 0
            else 0
        )
    else:
        mom_growth = 0

    return {
        "total_revenue": current["total_revenue"],
        "total_orders": current["total_orders"],
        "total_tax": current["total_tax"],
        "total_discount": current["total_discount"],
        "avg_order_value": (
            current["total_revenue"] / current["total_orders"]
            if current["total_orders"] > 0
            else 0
        ),
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
    rev_pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "revenue": {"$sum": _REVENUE_EXPR},
                "tax": {"$sum": _TAX_EXPR},
            }
        },
    ]
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
    exp_match["status"] = {"$in": ["APPROVED", "PAID", "approved", "paid"]}
    exp_pipeline = [
        {"$match": exp_match},
        {"$group": {"_id": "$category", "amount": {"$sum": "$amount"}}},
    ]
    expenses = list(db.get_collection("expenses").aggregate(exp_pipeline))
    total_expenses = sum(e["amount"] for e in expenses)

    # Real COGS from product cost_price (fallback 60% of line total if a
    # product's cost is unknown).
    cost_map = _cost_by_product(db)
    period_orders = list(
        db.get_collection("orders").find(match, {"_id": 0, "items": 1})
    )
    cogs = compute_cogs(period_orders, cost_map, fallback_rate=0.6)
    gross_profit = revenue - cogs

    # Payroll cost-to-company for the period's months.
    payroll_cost = _payroll_cost(db, store_id, from_date, to_date)
    net_profit = gross_profit - total_expenses - payroll_cost

    return {
        "revenue": revenue,
        "cogs": round(cogs, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_margin": round(gross_profit / revenue * 100, 1) if revenue > 0 else 0,
        "expenses": {e["_id"]: e["amount"] for e in expenses},
        "total_expenses": total_expenses,
        "payroll_cost": payroll_cost,
        "net_profit": round(net_profit, 2),
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
    collected = list(
        db.get_collection("orders").aggregate(
            [
                {"$match": sales_match},
                {
                    "$group": {
                        "_id": None,
                        "total_tax": {"$sum": "$tax_amount"},
                        "total_sales": {"$sum": "$total"},
                    }
                },
            ]
        )
    )
    gst_collected = collected[0]["total_tax"] if collected else 0

    # GST paid (input credit from purchases)
    purchase_match = {"date": {"$gte": start.isoformat(), "$lt": end.isoformat()}}
    paid = list(
        db.get_collection("purchase_orders").aggregate(
            [
                {"$match": purchase_match},
                {
                    "$group": {
                        "_id": None,
                        "total_tax": {"$sum": {"$ifNull": ["$tax_amount", 0]}},
                    }
                },
            ]
        )
    )
    gst_paid = paid[0]["total_tax"] if paid else 0

    cgst = gst_collected / 2
    sgst = gst_collected / 2
    net_payable = gst_collected - gst_paid

    # Filing status
    gstr1_due = datetime(y, m + 1 if m < 12 else 1, 11)
    gstr3b_due = datetime(y, m + 1 if m < 12 else 1, 20)

    return {
        "month": m,
        "year": y,
        "gst_collected": gst_collected,
        "cgst": cgst,
        "sgst": sgst,
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
    match = {"payment_status": {"$in": UNPAID_STATUSES}}
    if store_id:
        match["store_id"] = store_id

    orders = list(
        db.get_collection("orders").find(
            match,
            {
                "_id": 0,
                "order_id": 1,
                "customer_name": 1,
                "customer_phone": 1,
                "total": 1,
                "grand_total": 1,
                "amount_paid": 1,
                "created_at": 1,
            },
        )
    )

    now = datetime.utcnow()
    buckets = {"0_30": 0, "31_60": 0, "61_90": 0, "90_plus": 0}
    items = []

    for o in orders:
        balance = _order_total(o) - (o.get("amount_paid", 0) or 0)
        if balance <= 0:
            continue
        created = datetime.fromisoformat(o.get("created_at", now.isoformat()))
        days = (now - created).days

        if days <= 30:
            buckets["0_30"] += balance
        elif days <= 60:
            buckets["31_60"] += balance
        elif days <= 90:
            buckets["61_90"] += balance
        else:
            buckets["90_plus"] += balance

        items.append(
            {
                "order_id": o.get("order_id"),
                "customer_name": o.get("customer_name", "Unknown"),
                "customer_phone": o.get("customer_phone", ""),
                "amount": balance,
                "days_overdue": days,
            }
        )

    return {
        "buckets": buckets,
        "total_outstanding": sum(buckets.values()),
        "items": sorted(items, key=lambda x: -x["days_overdue"]),
    }


# === Vendor Payments ===


@router.get("/vendor-payments")
async def get_vendor_payments(current_user: dict = Depends(get_current_user)):
    db = _get_db()
    vendors = list(
        db.get_collection("vendors").find(
            {}, {"_id": 0, "vendor_id": 1, "legal_name": 1, "trade_name": 1, "name": 1}
        )
    )

    def _po_total(p):
        return float(p.get("total_amount") or p.get("total") or 0)

    paid_set = {"PAID", "paid"}
    result = []
    for v in vendors:
        pos = list(
            db.get_collection("purchase_orders").find(
                {"vendor_id": v["vendor_id"]},
                {"_id": 0, "total_amount": 1, "total": 1, "payment_status": 1},
            )
        )
        total = sum(_po_total(p) for p in pos)
        paid = sum(_po_total(p) for p in pos if p.get("payment_status") in paid_set)
        result.append(
            {
                "vendor_id": v["vendor_id"],
                "vendor_name": v.get("legal_name") or v.get("trade_name") or v.get("name") or v["vendor_id"],
                "total_orders": total,
                "total_paid": paid,
                "balance": total - paid,
            }
        )
    return result


# === Cash Flow ===


@router.get("/cash-flow")
async def get_cash_flow(
    period: str = Query("month"),
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    now = datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    active_store = store_id or current_user.get("active_store_id")

    # Inflows (from orders) — scoped to the active store
    inflow_match = {
        "created_at": {"$gte": start.isoformat()},
        "payment_status": {"$in": PAID_STATUSES},
    }
    if active_store:
        inflow_match["store_id"] = active_store
    inflow = list(
        db.get_collection("orders").aggregate(
            [
                {"$match": inflow_match},
                {"$group": {"_id": None, "total": {"$sum": _REVENUE_EXPR}}},
            ]
        )
    )
    total_inflow = inflow[0]["total"] if inflow else 0

    # Outflows (expenses + purchase orders) — scoped to the active store.
    # NOTE: POs store the store as `delivery_store_id`, expenses as `store_id`.
    exp_match = {"date": {"$gte": start.isoformat()}, "status": {"$in": ["APPROVED", "PAID", "approved", "paid"]}}
    if active_store:
        exp_match["store_id"] = active_store
    exp_out = list(
        db.get_collection("expenses").aggregate(
            [
                {"$match": exp_match},
                {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
            ]
        )
    )
    po_match = {
        "date": {"$gte": start.isoformat()},
        "payment_status": {"$in": PAID_STATUSES},
    }
    if active_store:
        po_match["delivery_store_id"] = active_store
    po_out = list(
        db.get_collection("purchase_orders").aggregate(
            [
                {"$match": po_match},
                {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$total_amount", {"$ifNull": ["$total", 0]}]}}}},
            ]
        )
    )
    total_outflow = (exp_out[0]["total"] if exp_out else 0) + (
        po_out[0]["total"] if po_out else 0
    )

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
    month: int,
    year: int,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    if "SUPERADMIN" not in current_user.get(
        "roles", []
    ) and "ADMIN" not in current_user.get("roles", []):
        raise HTTPException(403, "Only admin/superadmin can lock periods")

    existing = db.get_collection("period_locks").find_one(
        {"month": month, "year": year}
    )
    if existing:
        raise HTTPException(400, f"Period {month}/{year} is already locked")

    db.get_collection("period_locks").insert_one(
        {
            "month": month,
            "year": year,
            "locked_by": current_user.get("user_id"),
            "locked_at": datetime.utcnow().isoformat(),
        }
    )
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

    budget = db.get_collection("budgets").find_one(
        {"month": m, "year": y, "mode": mode}, {"_id": 0}
    )
    if not budget:
        # Default budget structure
        budget = {
            "month": m,
            "year": y,
            "mode": mode,
            "categories": {
                "rent": {"budget": 50000, "actual": 0},
                "salaries": {"budget": 200000, "actual": 0},
                "utilities": {"budget": 15000, "actual": 0},
                "marketing": {"budget": 30000, "actual": 0},
                "inventory": {"budget": 500000, "actual": 0},
                "miscellaneous": {"budget": 20000, "actual": 0},
            },
        }

    # Fill actuals from expenses
    start = datetime(y, m, 1)
    end = datetime(y, m + 1 if m < 12 else 1, 1) if m < 12 else datetime(y + 1, 1, 1)
    actuals = list(
        db.get_collection("expenses").aggregate(
            [
                {
                    "$match": {
                        "date": {"$gte": start.isoformat(), "$lt": end.isoformat()},
                        "status": {"$in": ["APPROVED", "PAID", "approved", "paid"]},
                    }
                },
                {"$group": {"_id": "$category", "total": {"$sum": "$amount"}}},
            ]
        )
    )
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
    pending = list(
        db.get_collection("stock_transfers")
        .find(
            {"status": {"$in": ["shipped", "in_transit"]}},
            {
                "_id": 0,
                "transfer_id": 1,
                "from_store": 1,
                "to_store": 1,
                "items": 1,
                "created_at": 1,
            },
        )
        .limit(50)
    )

    return {
        "pending_transfers": len(pending),
        "transfers": pending,
    }


# === GST Reconciliation (per entity) + Tally export ===


@router.get("/gst/reconciliation")
async def get_gst_reconciliation(
    month: Optional[int] = None,
    year: Optional[int] = None,
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """GST output (sales tax) vs input credit (purchase tax), grouped by entity.
    You file the actual returns through Tally; this is the cross-check."""
    db = _get_db()
    now = datetime.utcnow()
    m = month or now.month
    y = year or now.year
    start = datetime(y, m, 1)
    end = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)

    s2e, enames = _store_maps(db)
    store_ids = None
    if entity_id:
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]

    o_match = {"created_at": {"$gte": start.isoformat(), "$lt": end.isoformat()}}
    if store_ids is not None:
        o_match["store_id"] = {"$in": store_ids}
    orders = list(
        db.get_collection("orders").find(
            o_match, {"_id": 0, "store_id": 1, "tax_amount": 1, "tax_total": 1}
        )
    )

    p_match = {"date": {"$gte": start.isoformat(), "$lt": end.isoformat()}}
    if store_ids is not None:
        p_match["delivery_store_id"] = {"$in": store_ids}
    purchases = list(
        db.get_collection("purchase_orders").find(
            p_match, {"_id": 0, "delivery_store_id": 1, "store_id": 1, "tax_amount": 1}
        )
    )

    recon = gst_reconciliation(orders, purchases, s2e, enames)
    recon.update(
        {"month": m, "year": y, "note": "CGST/SGST split intra-state; file via Tally."}
    )
    return recon


@router.get("/tally/sales-jv")
async def get_tally_sales_jv(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Tally sales-voucher XML for a period + scope, ready to import into Tally."""
    db = _get_db()
    match: dict = {}
    store_ids = None
    if store_id:
        store_ids = [store_id]
    elif entity_id:
        s2e, _ = _store_maps(db)
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]
    if store_ids is not None:
        match["store_id"] = {"$in": store_ids}
    if from_date:
        match.setdefault("created_at", {})["$gte"] = from_date
    if to_date:
        match.setdefault("created_at", {})["$lte"] = to_date

    orders = list(db.get_collection("orders").find(match, {"_id": 0}))
    # Orders persist only total tax; split intra-state CGST/SGST = tax/2 and set
    # the taxable subtotal so each voucher balances (taxable + cgst + sgst = grand).
    for o in orders:
        tax = float(o.get("tax_amount") or o.get("tax_total") or 0)
        grand = float(o.get("grand_total") or o.get("total") or 0)
        o["cgst_amount"] = round(tax / 2, 2)
        o["sgst_amount"] = round(tax / 2, 2)
        o["subtotal"] = round(grand - tax, 2)
        o["grand_total"] = grand

    store_meta = {}
    if store_id:
        s = db.get_collection("stores").find_one({"store_id": store_id}) or {}
        store_meta = {
            "store_id": store_id,
            "store_code": s.get("store_code"),
            "store_name": s.get("store_name"),
        }

    from agents.nexus_providers import tally_build_day_voucher_xml

    xml = tally_build_day_voucher_xml(orders, store_meta)
    fname = f"sales_jv_{(from_date or 'all')[:10]}_{(to_date or 'all')[:10]}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
