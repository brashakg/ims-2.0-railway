# Finance & Accounting Router — _get_db() pattern (matches working routers)

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from .auth import get_current_user
from ..services import ap_engine, cashflow

# Mounted at /api/v1/finance in main.py. NO internal prefix: the earlier
# prefix="/finance" double-prefixed every path to /api/v1/finance/finance/*,
# which the frontend financeApi (it calls /finance/*) never hit — so the whole
# Finance dashboard 404'd. Dropping it aligns the routes with the client.
router = APIRouter(tags=["finance"])


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


def pnl_by_category(orders, cost_by_product: dict) -> list:
    """Revenue + COGS per product category (item_type), from order line items.
    Pure. 60%-of-line COGS fallback when a product's cost is unknown."""
    acc: dict = {}
    for o in orders:
        for it in (o.get("items") or []):
            cat = it.get("item_type") or it.get("category") or "OTHER"
            qty = it.get("quantity", 1) or 1
            rev = float(it.get("total", 0) or 0)
            d = acc.setdefault(cat, {"revenue": 0.0, "cogs": 0.0})
            d["revenue"] += rev
            cost = cost_by_product.get(it.get("product_id"))
            d["cogs"] += (float(cost) * qty) if cost is not None else rev * 0.6
    rows = []
    for cat, d in acc.items():
        r, c = round(d["revenue"], 2), round(d["cogs"], 2)
        rows.append({
            "category": cat, "revenue": r, "cogs": c,
            "gross_profit": round(r - c, 2),
            "gross_margin": round((r - c) / r * 100, 1) if r > 0 else 0,
        })
    return sorted(rows, key=lambda x: -x["revenue"])


def is_period_locked(db, month, year) -> bool:
    """True if the accounting period has been locked (closed)."""
    try:
        return db.get_collection("period_locks").find_one(
            {"month": int(month), "year": int(year)}
        ) is not None
    except Exception:
        return False


def _payroll_by_store(db, from_date, to_date) -> dict:
    """store_id -> payroll cost-to-company over the months in the range."""
    out: dict = {}
    try:
        months = _months_in_range(from_date, to_date)
        if not months:
            return out
        q = {"$or": [{"year": y, "month": m} for (y, m) in months]}
        for r in db.get_collection("payroll").find(q):
            sid = r.get("store_id")
            bd = r.get("breakdown") or {}
            out[sid] = out.get(sid, 0) + (bd.get("ctc_cost", r.get("net_salary", 0)) or 0)
    except Exception:
        pass
    return out


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
    """Per-vendor accounts-payable summary from REAL bills / payments / debit
    notes (via ap_engine). `balance` is the true outstanding payable; PO totals
    are kept only as context. Sorted by largest payable first."""
    db = _get_db()
    vendors = list(
        db.get_collection("vendors").find(
            {}, {"_id": 0, "vendor_id": 1, "legal_name": 1, "trade_name": 1, "name": 1}
        )
    )

    def _grouped(coll):
        out: dict = {}
        for row in db.get_collection(coll).find({}, {"_id": 0}):
            out.setdefault(row.get("vendor_id"), []).append(row)
        return out

    bills_by_v = _grouped("vendor_bills")
    pays_by_v = _grouped("vendor_payments")
    dn_by_v = _grouped("vendor_debit_notes")

    def _po_total(p):
        return float(p.get("total_amount") or p.get("total") or 0)

    result = []
    for v in vendors:
        vid = v["vendor_id"]
        led = ap_engine.build_ledger(
            bills_by_v.get(vid, []), pays_by_v.get(vid, []), dn_by_v.get(vid, [])
        )
        pos = list(
            db.get_collection("purchase_orders").find(
                {"vendor_id": vid}, {"_id": 0, "total_amount": 1, "total": 1}
            )
        )
        po_total = round(sum(_po_total(p) for p in pos), 2)
        result.append(
            {
                "vendor_id": vid,
                "vendor_name": v.get("legal_name")
                or v.get("trade_name")
                or v.get("name")
                or vid,
                "po_total": po_total,
                "total_orders": po_total,  # back-compat alias for the old key
                "total_billed": led["total_billed"],
                "total_paid": led["total_paid"],
                "total_tds": led["total_tds"],
                "total_debit_notes": led["total_debit_notes"],
                "balance": led["closing_balance"],
            }
        )
    return sorted(result, key=lambda r: -r["balance"])


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
    # Real cash paid to vendors this period (vendor_payments). AP is org-level,
    # so only fold it in for the org/owner view (no specific store selected) to
    # avoid double-attributing HQ payments to one store.
    vendor_payment_outflow = 0.0
    if not active_store:
        try:
            vp = list(
                db.get_collection("vendor_payments").aggregate(
                    [
                        {"$match": {"payment_date": {"$gte": start.date().isoformat()}}},
                        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
                    ]
                )
            )
            vendor_payment_outflow = round(vp[0]["total"], 2) if vp else 0.0
        except Exception:
            vendor_payment_outflow = 0.0

    expense_outflow = exp_out[0]["total"] if exp_out else 0
    purchase_outflow = po_out[0]["total"] if po_out else 0
    total_outflow = expense_outflow + purchase_outflow + vendor_payment_outflow

    return {
        "period": period,
        "inflows": total_inflow,
        "outflows": total_outflow,
        "net_cash_flow": total_inflow - total_outflow,
        "expense_outflow": expense_outflow,
        "purchase_outflow": purchase_outflow,
        "vendor_payment_outflow": vendor_payment_outflow,
    }


# === Owner cash-flow dashboard + forecast (ADMIN / ACCOUNTANT) ===


def _require_finance_admin(current_user: dict) -> None:
    """Org-wide financials are owner/accountant material."""
    roles = current_user.get("roles", []) or []
    if not any(r in roles for r in ("SUPERADMIN", "ADMIN", "ACCOUNTANT")):
        raise HTTPException(
            status_code=403, detail="Owner financials require ADMIN / ACCOUNTANT"
        )


def _ap_rows(db):
    """(outstanding bills, all payments, all debit notes) for AP math."""
    try:
        bills = list(
            db.get_collection("vendor_bills").find(
                {"status": {"$ne": "PAID"}}, {"_id": 0}
            )
        )
        payments = list(db.get_collection("vendor_payments").find({}, {"_id": 0}))
        dn = list(db.get_collection("vendor_debit_notes").find({}, {"_id": 0}))
    except Exception:
        bills, payments, dn = [], [], []
    return bills, payments, dn


def _ar_aging(db, now: datetime) -> dict:
    """Customer receivables aged by order age (mirror of /outstanding)."""
    buckets = {"0_30": 0.0, "31_60": 0.0, "61_90": 0.0, "90_plus": 0.0}
    total = 0.0
    try:
        orders = list(
            db.get_collection("orders").find(
                {"payment_status": {"$in": UNPAID_STATUSES}},
                {
                    "_id": 0,
                    "grand_total": 1,
                    "total": 1,
                    "amount_paid": 1,
                    "created_at": 1,
                },
            )
        )
    except Exception:
        orders = []
    for o in orders:
        bal = _order_total(o) - float(o.get("amount_paid", 0) or 0)
        if bal <= 0:
            continue
        created = ap_engine.parse_date(o.get("created_at")) or now
        days = (now - created).days
        if days <= 30:
            buckets["0_30"] += bal
        elif days <= 60:
            buckets["31_60"] += bal
        elif days <= 90:
            buckets["61_90"] += bal
        else:
            buckets["90_plus"] += bal
        total += bal
    buckets = {k: round(v, 2) for k, v in buckets.items()}
    overdue = round(buckets["31_60"] + buckets["61_90"] + buckets["90_plus"], 2)
    return {"total": round(total, 2), "buckets": buckets, "overdue": overdue}


def _agg_sum(db, coll: str, match: dict, expr) -> float:
    try:
        r = list(
            db.get_collection(coll).aggregate(
                [{"$match": match}, {"$group": {"_id": None, "total": {"$sum": expr}}}]
            )
        )
        return round(r[0]["total"], 2) if r else 0.0
    except Exception:
        return 0.0


@router.get("/owner-dashboard")
async def owner_dashboard(current_user: dict = Depends(get_current_user)):
    """CEO/owner financial snapshot: receivables (AR) vs payables (AP), net
    working-capital position, this-month cash movement, and alerts. Org-wide,
    ADMIN / ACCOUNTANT only."""
    _require_finance_admin(current_user)
    db = _get_db()
    now = datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    ar = _ar_aging(db, now)

    bills, payments, dn = _ap_rows(db)
    ap = ap_engine.build_aging(bills, payments, dn)
    ap_overdue = round(ap["total_outstanding"] - ap["buckets"]["current"], 2)
    due_7d = 0.0
    due_30d = 0.0
    for it in ap["items"]:
        due = ap_engine.parse_date(it.get("due_date"))
        if due is None:
            continue
        delta = (due.date() - now.date()).days
        if delta <= 7:
            due_7d += it["outstanding"]
        if delta <= 30:
            due_30d += it["outstanding"]
    due_7d = round(due_7d, 2)
    due_30d = round(due_30d, 2)

    revenue = _agg_sum(
        db,
        "orders",
        {
            "created_at": {"$gte": start.isoformat()},
            "payment_status": {"$in": PAID_STATUSES},
        },
        _REVENUE_EXPR,
    )
    expenses = _agg_sum(
        db,
        "expenses",
        {
            "date": {"$gte": start.isoformat()},
            "status": {"$in": ["APPROVED", "PAID", "approved", "paid"]},
        },
        "$amount",
    )
    vpaid = _agg_sum(
        db,
        "vendor_payments",
        {"payment_date": {"$gte": start.date().isoformat()}},
        "$amount",
    )

    alerts = []
    if ap_overdue > 0:
        alerts.append(
            {"level": "warning", "message": f"Rs {ap_overdue:.0f} of vendor payables overdue"}
        )
    if due_7d > 0:
        alerts.append(
            {"level": "info", "message": f"Rs {due_7d:.0f} of vendor bills due within 7 days"}
        )
    if ar["overdue"] > 0:
        alerts.append(
            {
                "level": "warning",
                "message": f"Rs {ar['overdue']:.0f} of receivables overdue 30+ days",
            }
        )

    return {
        "as_of": now.date().isoformat(),
        "receivables": ar,
        "payables": {
            "total": ap["total_outstanding"],
            "buckets": ap["buckets"],
            "overdue": ap_overdue,
            "due_7d": due_7d,
            "due_30d": due_30d,
            "unallocated_credits": ap["unallocated_credits"],
        },
        "net_position": round(ar["total"] - ap["total_outstanding"], 2),
        "this_month": {
            "revenue": revenue,
            "expenses": expenses,
            "vendor_payments": vpaid,
            "net_cash_flow": round(revenue - expenses - vpaid, 2),
        },
        "alerts": alerts,
    }


@router.get("/cash-flow-forecast")
async def cash_flow_forecast(
    days: int = Query(90, ge=7, le=365),
    opening_cash: float = Query(0.0),
    collection_lag_days: int = Query(15, ge=0, le=120),
    recurring_monthly_outflow: float = Query(0.0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """Weekly cash-flow projection. Inflows = unpaid orders projected to a
    collection date (created + collection_lag_days); outflows = outstanding
    vendor bills on their due date + a recurring monthly estimate (avg of the
    last 3 months' expenses plus an owner-supplied recurring_monthly_outflow,
    e.g. payroll). Surfaces the lowest projected balance as a cash-crunch
    warning. ADMIN / ACCOUNTANT only."""
    _require_finance_admin(current_user)
    db = _get_db()
    now = datetime.utcnow()

    # Inflows from AR.
    inflow_events = []
    try:
        orders = list(
            db.get_collection("orders").find(
                {"payment_status": {"$in": UNPAID_STATUSES}},
                {
                    "_id": 0,
                    "grand_total": 1,
                    "total": 1,
                    "amount_paid": 1,
                    "created_at": 1,
                },
            )
        )
    except Exception:
        orders = []
    for o in orders:
        bal = _order_total(o) - float(o.get("amount_paid", 0) or 0)
        if bal <= 0:
            continue
        created = ap_engine.parse_date(o.get("created_at")) or now
        coll_date = created + timedelta(days=collection_lag_days)
        inflow_events.append({"date": coll_date.date().isoformat(), "amount": bal})

    # Outflows from AP (real due dates).
    bills, payments, dn = _ap_rows(db)
    ap = ap_engine.build_aging(bills, payments, dn)
    outflow_events = [
        {"date": it.get("due_date"), "amount": it["outstanding"]} for it in ap["items"]
    ]

    # Recurring monthly outflow estimate.
    monthly_expense_est = 0.0
    try:
        three_mo_ago = (now - timedelta(days=90)).isoformat()
        r = list(
            db.get_collection("expenses").aggregate(
                [
                    {
                        "$match": {
                            "date": {"$gte": three_mo_ago},
                            "status": {"$in": ["APPROVED", "PAID", "approved", "paid"]},
                        }
                    },
                    {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
                ]
            )
        )
        monthly_expense_est = round(r[0]["total"] / 3.0, 2) if r else 0.0
    except Exception:
        monthly_expense_est = 0.0
    recurring_monthly = round(monthly_expense_est + recurring_monthly_outflow, 2)
    if recurring_monthly > 0:
        m, y = now.month, now.year
        for _ in range((days // 28) + 1):
            if m == 12:
                m, y = 1, y + 1
            else:
                m += 1
            event_date = datetime(y, m, 1)
            if 0 <= (event_date.date() - now.date()).days <= days:
                outflow_events.append(
                    {
                        "date": event_date.date().isoformat(),
                        "amount": recurring_monthly,
                        "label": "Recurring (expenses/payroll est.)",
                    }
                )

    forecast = cashflow.build_forecast(
        opening_cash, inflow_events, outflow_events, now.date().isoformat(), days
    )
    forecast["assumptions"] = {
        "collection_lag_days": collection_lag_days,
        "monthly_expense_estimate": monthly_expense_est,
        "recurring_monthly_outflow_input": recurring_monthly_outflow,
        "recurring_monthly_total": recurring_monthly,
    }
    return forecast


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


# === P&L breakdowns + period status ===


@router.get("/pnl/by-store")
async def get_pnl_by_store(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """P&L (revenue - COGS - approved expenses - payroll) per store."""
    db = _get_db()
    s2e, _ = _store_maps(db)
    store_ids = [sid for sid, eid in s2e.items() if eid == entity_id] if entity_id else None

    match: dict = {}
    if store_ids is not None:
        match["store_id"] = {"$in": store_ids}
    if from_date:
        match.setdefault("created_at", {})["$gte"] = from_date
    if to_date:
        match.setdefault("created_at", {})["$lte"] = to_date

    rev = list(db.get_collection("orders").aggregate(
        [{"$match": match}, {"$group": {"_id": "$store_id", "revenue": {"$sum": _REVENUE_EXPR}}}]
    ))
    rev_by_store = {r["_id"]: r["revenue"] for r in rev}

    cost_map = _cost_by_product(db)
    cogs_by_store: dict = {}
    for o in db.get_collection("orders").find(match, {"_id": 0, "store_id": 1, "items": 1}):
        sid = o.get("store_id")
        cogs_by_store[sid] = cogs_by_store.get(sid, 0) + compute_cogs([o], cost_map, fallback_rate=0.6)

    exp_match: dict = {"status": {"$in": ["APPROVED", "PAID", "approved", "paid"]}}
    if store_ids is not None:
        exp_match["store_id"] = {"$in": store_ids}
    if from_date:
        exp_match.setdefault("date", {})["$gte"] = from_date
    if to_date:
        exp_match.setdefault("date", {})["$lte"] = to_date
    exp = list(db.get_collection("expenses").aggregate(
        [{"$match": exp_match}, {"$group": {"_id": "$store_id", "amt": {"$sum": "$amount"}}}]
    ))
    exp_by_store = {e["_id"]: e["amt"] for e in exp}

    pay_by_store = _payroll_by_store(db, from_date, to_date)

    rows = []
    for sid in set(rev_by_store) | set(cogs_by_store) | set(exp_by_store) | set(pay_by_store):
        r = round(rev_by_store.get(sid, 0), 2)
        c = round(cogs_by_store.get(sid, 0), 2)
        e = round(exp_by_store.get(sid, 0), 2)
        p = round(pay_by_store.get(sid, 0), 2)
        net = round(r - c - e - p, 2)
        rows.append({
            "store_id": sid, "entity_id": s2e.get(sid),
            "revenue": r, "cogs": c, "expenses": e, "payroll": p,
            "net_profit": net, "net_margin": round(net / r * 100, 1) if r > 0 else 0,
        })
    return {
        "stores": sorted(rows, key=lambda x: -x["revenue"]),
        "total_net": round(sum(x["net_profit"] for x in rows), 2),
    }


@router.get("/pnl/by-category")
async def get_pnl_by_category(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Revenue + COGS + gross profit per product category."""
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

    orders = list(db.get_collection("orders").find(match, {"_id": 0, "items": 1}))
    cats = pnl_by_category(orders, _cost_by_product(db))
    return {
        "categories": cats,
        "total_revenue": round(sum(c["revenue"] for c in cats), 2),
        "total_gross_profit": round(sum(c["gross_profit"] for c in cats), 2),
    }


@router.get("/period-status")
async def get_period_status(
    month: int,
    year: int,
    current_user: dict = Depends(get_current_user),
):
    """Whether an accounting period is locked (for the UI to disable edits)."""
    db = _get_db()
    return {"month": month, "year": year, "locked": is_period_locked(db, month, year)}
