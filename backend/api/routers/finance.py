# Finance & Accounting Router — _get_db() pattern (matches working routers)

import calendar
import csv
import io
import uuid
from datetime import datetime, timedelta, date
from ..utils.ist import (
    now_ist,
    now_ist_naive,
    ist_today,
    ist_day_start_utc,
    fy_start_year_ist,
)
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel, Field
from .auth import get_current_user
from ..dependencies import validate_store_access
from ..services import ap_engine, cashflow, itc_reconcile, cash_register, csv_safe
from ..services import survival_cashflow
from ..services.cost_mask import can_see_cost
from ..services.cache import cache
from ..services import ticker_service, policy_engine
from ..services import je_service

# Mounted at /api/v1/finance in main.py. NO internal prefix: the earlier
# prefix="/finance" double-prefixed every path to /api/v1/finance/finance/*,
# which the frontend financeApi (it calls /finance/*) never hit — so the whole
# Finance dashboard 404'd. Dropping it aligns the routes with the client.
router = APIRouter(tags=["finance"])

# Separate router for the F34 target-ticker GET. The main finance_router is
# mounted in main.py behind a router-level require_roles(*_FINANCE_ROLES) gate
# (ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT + SUPERADMIN), which would 403
# store-floor staff. The ticker GET must be reachable by EVERY authenticated
# role (the data is privacy-stratified server-side, not gated at the router),
# so it lives on this ungated router -- mounted WITHOUT the finance role gate.
# The settings POST stays on `router` (it is SUPERADMIN/ADMIN only, a subset of
# the finance gate).
ticker_router = APIRouter(tags=["finance"])


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

# Order lifecycle: DRAFT -> CONFIRMED -> ... -> DELIVERED, plus terminal
# CANCELLED (see orders.OrderStatus). A DRAFT was never booked and a CANCELLED
# was reversed -- NEITHER is real revenue/tax/GST liability. Every financial
# aggregation MUST exclude them, matching the convention used throughout
# reports.py (`status: {"$nin": ["CANCELLED", "DRAFT"]}`). The original finance
# queries had NO status filter, so cancelled + still-draft orders inflated
# revenue, P&L, GST collected, and cash inflow. Lowercase variants tolerated.
_EXCLUDED_ORDER_STATUSES = ["CANCELLED", "DRAFT", "cancelled", "draft"]
_REAL_ORDER_STATUS_FILTER = {"$nin": _EXCLUDED_ORDER_STATUSES}


def _parse_range_dt(s, *, end: bool = False) -> Optional[datetime]:
    """Parse a 'YYYY-MM-DD' (or ISO) query string into a datetime suitable for
    a Mongo range bound on `created_at`.

    Orders persist `created_at` as a BSON *datetime* (BaseRepository
    `_add_timestamps` -> datetime.now()), so a bare 'YYYY-MM-DD' STRING bound
    never matches -- a string-vs-datetime comparison in Mongo silently returns
    nothing. This was the bug behind every date-ranged finance figure reading
    zero. We convert to a datetime so the comparison is apples-to-apples; an
    `end` date-only bound expands to 23:59:59.999999 so the whole final day is
    inclusive. Returns None when the input is empty / unparseable (caller then
    omits that bound).

    For date-only inputs (YYYY-MM-DD), treats them as IST business days
    (matching the other IST-swept paths in this router). Converts IST midnight
    to the equivalent naive-UTC instant for comparison with created_at.
    """
    if s is None:
        return None
    if isinstance(s, datetime):
        return s
    txt = str(s).strip()
    if not txt:
        return None
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        # Drop tz so it compares with the naive datetimes orders are stored as.
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
    except ValueError:
        try:
            # Date-only input: parse as an IST business day and convert to UTC.
            # ist_day_start_utc gives us IST midnight as a naive-UTC instant,
            # which is the correct >= bound for range-filtering created_at.
            parsed_date = datetime.fromisoformat(txt[:10]).date()
            dt = ist_day_start_utc(parsed_date)
        except ValueError:
            return None
    # A date-only end bound covers the entire day in IST.
    # ist_day_start_utc already gives IST midnight in UTC; for the end bound,
    # add 23:59:59.999999 in IST time (which is 23:59:59.999999 in UTC, since
    # ist_day_start_utc is already the UTC equivalent of IST midnight).
    if len(txt) <= 10 and end:
        return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


def _apply_created_at_range(match: dict, from_date, to_date) -> dict:
    """Add a datetime `created_at` range to a Mongo match dict (in place).

    Used by every date-ranged finance order query so they all compare
    datetime-to-datetime against the BSON `created_at`. No-ops a bound when its
    date is missing/unparseable.
    """
    lo = _parse_range_dt(from_date, end=False)
    hi = _parse_range_dt(to_date, end=True)
    if lo is not None:
        match.setdefault("created_at", {})["$gte"] = lo
    if hi is not None:
        match.setdefault("created_at", {})["$lte"] = hi
    return match


def _order_total(o: dict) -> float:
    v = o.get("grand_total")
    if v is None:
        v = o.get("total", 0)
    return float(v or 0)


def _item_cost(it: dict, cost_by_product: dict):
    """Resolve the unit cost for an order line. Prefers the snapshot
    item.cost_at_sale (frozen at order create time) so historical P&L
    doesn't drift when products.cost_price is edited after the sale.
    Falls back to the live products.cost_price (for orders booked
    before the snapshot was introduced). Returns None if unknown."""
    snap = it.get("cost_at_sale")
    if snap is not None:
        try:
            return float(snap)
        except (TypeError, ValueError):
            pass
    pid = it.get("product_id")
    cost = cost_by_product.get(pid) if pid else None
    if cost is None:
        return None
    try:
        return float(cost)
    except (TypeError, ValueError):
        return None


def compute_cogs(orders, cost_by_product: dict, fallback_rate: float = 0.0) -> float:
    """Real COGS: sum unit cost * qty over order line items. Prefers each
    line's cost_at_sale snapshot; falls back to the live product
    cost_price; finally falls back to fallback_rate * line-total when the
    cost is unknown. Pure."""
    cogs = 0.0
    for o in orders:
        for it in o.get("items") or []:
            qty = it.get("quantity", 1) or 1
            cost = _item_cost(it, cost_by_product)
            if cost is not None:
                cogs += cost * float(qty)
            elif fallback_rate:
                cogs += float(it.get("total", 0) or 0) * fallback_rate
    return round(cogs, 2)


def compute_cogs_with_flag(
    orders, cost_by_product: dict, fallback_rate: float = 0.0
) -> tuple:
    """Like compute_cogs but also returns (cogs, estimated_lines, total_lines)
    so callers can surface a 'COGS partially estimated' flag on the P&L when
    the fallback was used (rather than silently showing fabricated margins).
    Pure."""
    cogs = 0.0
    total_lines = 0
    estimated_lines = 0
    for o in orders:
        for it in o.get("items") or []:
            total_lines += 1
            qty = it.get("quantity", 1) or 1
            cost = _item_cost(it, cost_by_product)
            if cost is not None:
                cogs += cost * float(qty)
            elif fallback_rate:
                cogs += float(it.get("total", 0) or 0) * fallback_rate
                estimated_lines += 1
    return round(cogs, 2), estimated_lines, total_lines


def _cost_by_product(db) -> dict:
    """product_id (and _id) -> cost_price, for COGS. Keyed both ways because
    imported orders may reference a product by its Mongo _id."""
    out: dict = {}
    try:
        for p in db.get_collection("products").find(
            {}, {"product_id": 1, "cost_price": 1}
        ):
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
    now = now_ist()
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


def gst_reconciliation(
    orders,
    purchases,
    store_to_entity: dict,
    entity_names: dict = None,
    store_state_by_id: dict = None,
    customer_state_by_id: dict = None,
) -> dict:
    """Group GST output (orders) vs input credit (purchases) by legal entity.

    Output tax is classified per order: inter-state (seller state != buyer
    state) -> IGST; intra-state (same / unknown) -> CGST + SGST (tax/2 each),
    the same rule GSTR-1 / GSTR-3B use. When the state maps are empty (the DB
    didn't carry state, or a caller doesn't supply them) every sale is treated
    as intra-state -- the prior behaviour -- so cgst+sgst == gst_collected.
    Pure."""
    entity_names = entity_names or {}
    store_state_by_id = store_state_by_id or {}
    customer_state_by_id = customer_state_by_id or {}
    acc: dict = {}

    def _ent(store_id):
        return store_to_entity.get(store_id) or "_unassigned"

    def _blank():
        return {"cgst": 0.0, "sgst": 0.0, "igst": 0.0, "input_credit": 0.0}

    for o in orders:
        eid = _ent(o.get("store_id"))
        tax = float(o.get("tax_amount") or o.get("tax_total") or 0)
        seller = _norm_state(store_state_by_id.get(o.get("store_id")))
        buyer = _norm_state(customer_state_by_id.get(o.get("customer_id")))
        is_inter_state = bool(seller and buyer and seller != buyer)
        bucket = acc.setdefault(eid, _blank())
        if is_inter_state:
            bucket["igst"] += tax
        else:
            bucket["cgst"] += tax / 2
            bucket["sgst"] += tax / 2
    for p in purchases:
        eid = _ent(p.get("delivery_store_id") or p.get("store_id"))
        tax = float(p.get("tax_amount") or 0)
        acc.setdefault(eid, _blank())["input_credit"] += tax

    entities, tot_c, tot_i = [], 0.0, 0.0
    for eid, d in acc.items():
        cgst = round(d["cgst"], 2)
        sgst = round(d["sgst"], 2)
        igst = round(d["igst"], 2)
        collected = round(cgst + sgst + igst, 2)
        i = round(d["input_credit"], 2)
        tot_c += collected
        tot_i += i
        entities.append(
            {
                "entity_id": eid,
                "entity_name": entity_names.get(eid, eid),
                "gst_collected": collected,
                "cgst": cgst,
                "sgst": sgst,
                "igst": igst,
                "input_credit": i,
                "net_payable": round(collected - i, 2),
            }
        )
    return {
        "entities": sorted(entities, key=lambda e: -e["gst_collected"]),
        "total_collected": round(tot_c, 2),
        "total_input_credit": round(tot_i, 2),
        "total_net_payable": round(tot_c - tot_i, 2),
    }


def _norm_state(value) -> str:
    """Canonicalize a state (full name / 2-letter abbr / 2-digit GST code) to the
    GST numeric code so 'Jharkhand', 'JH' and '20' all compare equal. Without
    this, an order whose store.state and customer.state are stored in DIFFERENT
    formats is misclassified inter-state (wrong IGST vs CGST/SGST). Unresolvable
    values pass through unchanged; empty -> '' (treated as unknown/intra)."""
    from ..services import org_validation as _ov

    return _ov.normalize_state_code(str(value or "").strip()) or ""


def _split_output_tax(orders, store_state_by_id: dict, customer_state_by_id: dict):
    """Split output tax into (cgst, sgst, igst), paise-balanced.

    inter-state (seller state != buyer state) -> IGST; intra-state OR unknown
    state on either side -> CGST + SGST (tax/2 each) -- the SAME rule as
    gst_reconciliation()/GSTR-1, so the GST-summary cards reconcile with the
    reconciliation report instead of the prior blind 50/50 split that mis-stated
    every inter-state sale. The residual goes on SGST so cgst + sgst == the intra
    portion exactly and cgst + sgst + igst == the total (no paise drift). Pure.
    Tax field resolves tax_amount, else tax_total (mirrors _TAX_EXPR)."""
    igst = 0.0
    total = 0.0
    for o in orders:
        t = o.get("tax_amount")
        if t is None:
            t = o.get("tax_total")
        tax = float(t or 0)
        total += tax
        seller = _norm_state(store_state_by_id.get(o.get("store_id")))
        buyer = _norm_state(customer_state_by_id.get(o.get("customer_id")))
        if seller and buyer and seller != buyer:
            igst += tax
    igst = round(igst, 2)
    intra = round(total - igst, 2)
    cgst = round(intra / 2, 2)
    sgst = round(intra - cgst, 2)
    return cgst, sgst, igst


def _store_maps(db):
    """Return (store_id -> entity_id, entity_id -> entity_name)."""
    s2e, enames = {}, {}
    try:
        for s in db.get_collection("stores").find(
            {}, {"_id": 0, "store_id": 1, "entity_id": 1}
        ):
            if s.get("store_id"):
                s2e[s["store_id"]] = s.get("entity_id")
        for e in db.get_collection("entities").find(
            {}, {"_id": 0, "entity_id": 1, "name": 1}
        ):
            enames[e.get("entity_id")] = e.get("name")
    except Exception:
        pass
    return s2e, enames


def _store_state_map(db) -> dict:
    """store_id -> home state (for intra/inter-state GST classification)."""
    out: dict = {}
    try:
        for s in db.get_collection("stores").find(
            {}, {"_id": 0, "store_id": 1, "state": 1}
        ):
            if s.get("store_id"):
                out[s["store_id"]] = str(s.get("state") or "")
    except Exception:
        pass
    return out


def _customer_state_map(db) -> dict:
    """customer_id -> state (for intra/inter-state GST classification)."""
    out: dict = {}
    try:
        for c in db.get_collection("customers").find(
            {}, {"_id": 0, "customer_id": 1, "state": 1}
        ):
            cid = c.get("customer_id")
            if cid:
                out[cid] = str(c.get("state") or "")
    except Exception:
        pass
    return out


def pnl_by_category(orders, cost_by_product: dict) -> list:
    """Revenue + COGS per product category (item_type), from order line items.
    Pure. Prefers item.cost_at_sale snapshot; 60%-of-line COGS fallback when
    a product's cost is unknown. Includes cogs_is_estimated flag per category
    so the UI can mark estimates (SYSTEM_INTENT: never show fabricated numbers
    without flagging them)."""
    acc: dict = {}
    for o in orders:
        for it in o.get("items") or []:
            cat = it.get("item_type") or it.get("category") or "OTHER"
            qty = it.get("quantity", 1) or 1
            rev = float(it.get("total", 0) or 0)
            d = acc.setdefault(cat, {"revenue": 0.0, "cogs": 0.0, "estimated_lines": 0})
            d["revenue"] += rev
            cost = _item_cost(it, cost_by_product)
            if cost is not None:
                d["cogs"] += cost * float(qty)
            else:
                d["cogs"] += rev * 0.6
                d["estimated_lines"] += 1
    rows = []
    for cat, d in acc.items():
        r, c = round(d["revenue"], 2), round(d["cogs"], 2)
        rows.append(
            {
                "category": cat,
                "revenue": r,
                "cogs": c,
                "gross_profit": round(r - c, 2),
                "gross_margin": round((r - c) / r * 100, 1) if r > 0 else 0,
                "cogs_is_estimated": d["estimated_lines"] > 0,
            }
        )
    return sorted(rows, key=lambda x: -x["revenue"])


def is_period_locked(db, month, year) -> bool:
    """True if the accounting period has been locked (closed)."""
    try:
        return (
            db.get_collection("period_locks").find_one(
                {"month": int(month), "year": int(year)}
            )
            is not None
        )
    except Exception:
        return False


def check_period_locked(db, posting_date) -> None:
    """Raise HTTPException(423) if the posting_date's month/year is locked.

    Fail-soft: if db is None or period_locks lookup fails, does not raise.
    Used by orders/returns/vendor-bills/payments to guard financial-period closure.
    """
    if db is None:
        return
    try:
        from datetime import date

        if isinstance(posting_date, str):
            posting_date = date.fromisoformat(posting_date)
        month, year = posting_date.month, posting_date.year
        if is_period_locked(db, month, year):
            raise HTTPException(
                status_code=423,
                detail=f"Accounting period {month:02d}/{year} is locked; cannot post to a closed month.",
            )
    except HTTPException:
        raise
    except Exception:
        pass  # fail-soft: period check errors do not block posting


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
            out[sid] = out.get(sid, 0) + (
                bd.get("ctc_cost", r.get("net_salary", 0)) or 0
            )
    except Exception:
        pass
    return out


# === Revenue Tracking ===


_HQ_STORE_ROLES = {"SUPERADMIN", "ADMIN", "AREA_MANAGER"}


def _scope_store(store_id, current_user):
    """Resolve + authorise the store filter for a finance aggregation (BUG-062).

    - explicit store_id -> validate_store_access (403 if a store-scoped role asks
      for ANOTHER store; admins / area-managers pass through).
    - omitted -> all stores (None) for HQ roles, but the caller's OWN active store
      for store-scoped roles -- so a store-level role can never read an all-stores
      or other-store financial aggregate (revenue / P&L / receivables).
    """
    if store_id:
        return validate_store_access(store_id, current_user)
    if set(current_user.get("roles") or []) & _HQ_STORE_ROLES:
        return None
    return current_user.get("active_store_id")


@router.get("/revenue")
async def get_revenue(
    period: str = Query("month", pattern="^(day|week|month|year)$"),
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    store_id = _scope_store(store_id, current_user)
    db = _get_db()
    if db is None:
        return {
            "total_revenue": 0,
            "total_orders": 0,
            "total_tax": 0,
            "total_discount": 0,
            "prev_revenue": 0,
            "change_pct": None,
        }
    today = ist_today()

    if period == "day":
        start = ist_day_start_utc(today)
    elif period == "week":
        start = ist_day_start_utc(today - timedelta(days=today.weekday()))
    elif period == "month":
        start = ist_day_start_utc(today.replace(day=1))
    else:
        fy_year = fy_start_year_ist(now_ist())
        start = ist_day_start_utc(today.replace(year=fy_year, month=4, day=1))

    # created_at is a BSON datetime -> compare against a datetime, not an ISO
    # string (a string bound never matches). Exclude DRAFT/CANCELLED so revenue
    # reflects real booked sales only.
    match = {"created_at": {"$gte": start}, "status": _REAL_ORDER_STATUS_FILTER}
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
            "created_at": {"$gte": prev_start, "$lt": start},
            "status": _REAL_ORDER_STATUS_FILTER,
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
    store_id = _scope_store(store_id, current_user)
    db = _get_db()
    if db is None:
        return {
            "revenue": 0,
            "tax": 0,
            "expenses": 0,
            "gross_profit": 0,
            "net_profit": 0,
            "gross_margin_pct": 0,
            "net_margin_pct": 0,
        }
    # Exclude DRAFT/CANCELLED (never-booked / reversed) and compare the
    # date range as datetimes (created_at is a BSON datetime, so a 'YYYY-MM-DD'
    # string bound matched nothing -> the whole date-ranged P&L read zero).
    match = {"status": _REAL_ORDER_STATUS_FILTER}
    if store_id:
        match["store_id"] = store_id
    _apply_created_at_range(match, from_date, to_date)

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

    # Expenses. Expense docs store the date on `expense_date` (a 'YYYY-MM-DD'
    # ISO string), NOT `date` -- filtering on `date` dropped EVERY expense
    # whenever a date range was supplied. from_date / to_date arrive as
    # 'YYYY-MM-DD' query strings, so the string comparison is consistent.
    exp_match = {}
    if store_id:
        exp_match["store_id"] = store_id
    if from_date:
        exp_match.setdefault("expense_date", {})["$gte"] = from_date
    if to_date:
        exp_match.setdefault("expense_date", {})["$lte"] = to_date
    exp_match["status"] = {"$in": ["APPROVED", "PAID", "approved", "paid"]}
    exp_pipeline = [
        {"$match": exp_match},
        {"$group": {"_id": "$category", "amount": {"$sum": "$amount"}}},
    ]
    expenses = list(db.get_collection("expenses").aggregate(exp_pipeline))
    total_expenses = sum(e["amount"] for e in expenses)

    # Real COGS from product cost_price (fallback 60% of line total if a
    # product's cost is unknown). Surface a flag when the fallback is used so
    # the UI can warn the owner that some margins are estimated (SYSTEM_INTENT:
    # never show fabricated numbers without flagging them as estimates).
    cost_map = _cost_by_product(db)
    period_orders = list(
        db.get_collection("orders").find(match, {"_id": 0, "items": 1})
    )
    cogs, cogs_est_lines, cogs_total_lines = compute_cogs_with_flag(
        period_orders, cost_map, fallback_rate=0.6
    )
    cogs_is_estimated = cogs_est_lines > 0
    gross_profit = revenue - cogs

    # Payroll cost-to-company for the period's months.
    payroll_cost = _payroll_cost(db, store_id, from_date, to_date)

    # F17/#25: POSTED manual journal entries adjust the P&L (depreciation, bank
    # charges, prior-period corrections). EXPENSE-type debits raise cost;
    # REVENUE-type credits raise revenue. DRAFT/SUBMITTED/APPROVED JEs do NOT
    # touch the ledger -- only POSTED ones (je_service filters on status).
    # JE entry_date is stored as a CALENDAR-day midnight (see _je_parse_entry_date),
    # so range it on calendar-day bounds -- NOT the ist_day_start_utc-shifted
    # created_at bounds used for orders above -- so a JE on the first/last day of
    # the window isn't mis-bucketed (the two frames must agree).
    je_from_dt = _je_cal_day(from_date)
    je_to_dt = _je_cal_day(to_date, end=True)
    je_adj = je_service.pnl_adjustments(
        db, store_id=store_id, from_dt=je_from_dt, to_dt=je_to_dt
    )
    je_rev = je_adj.get("je_revenue_adjustment", 0.0)
    je_exp = je_adj.get("je_expense_adjustment", 0.0)
    # JE EXPENSE debits are genuine period expenses (depreciation, bank charges)
    # and sit below gross profit with the other expenses. JE REVENUE credits are
    # NON-OPERATING income (misc income, prior-period corrections) -- they must
    # NOT inflate trading revenue / gross profit / gross margin (adversarial P2):
    # they enter as their own line below gross profit and only lift net_profit.
    total_expenses = round(total_expenses + je_exp, 2)
    net_profit = gross_profit - total_expenses - payroll_cost + je_rev

    pnl = {
        "revenue": revenue,
        "cogs": round(cogs, 2),
        # When cogs_is_estimated=True, some cost lines used a 60%-of-revenue
        # fallback because the product cost_price is not set. Gross margin shown
        # should be treated as approximate until all products have a cost_price.
        "cogs_is_estimated": cogs_is_estimated,
        "cogs_estimated_lines": cogs_est_lines,
        "cogs_total_lines": cogs_total_lines,
        "gross_profit": round(gross_profit, 2),
        "gross_margin": round(gross_profit / revenue * 100, 1) if revenue > 0 else 0,
        "expenses": {e["_id"]: e["amount"] for e in expenses},
        "total_expenses": total_expenses,
        # Manual-JE adjustments surfaced as their own lines (below gross profit).
        "je_revenue_adjustment": round(je_rev, 2),
        "je_expense_adjustment": round(je_exp, 2),
        "payroll_cost": payroll_cost,
        "net_profit": round(net_profit, 2),
        "net_margin": round(net_profit / revenue * 100, 1) if revenue > 0 else 0,
        "tax_collected": tax,
    }
    # F35 / GAP_ANALYSIS G1: /pnl is store-scoped only (NO role gate), so the cost,
    # profit and margin economics must be stripped for any role not in
    # COST_VISIBLE_ROLES (excludes AREA_MANAGER per DECISIONS sec 9). Revenue + tax
    # (top line) stay visible. Without this, gross_margin/cogs reach every role.
    if not can_see_cost(current_user):
        for _f in (
            "cogs",
            "cogs_is_estimated",
            "cogs_estimated_lines",
            "cogs_total_lines",
            "gross_profit",
            "gross_margin",
            "net_profit",
            "net_margin",
            "payroll_cost",
        ):
            pnl.pop(_f, None)
    return pnl


# === GST Management ===


# Bill statuses that mean "not yet received / not bookable for ITC". A bill in
# one of these is excluded from the ITC total; any other status (or none) counts.
_ITC_PENDING_STATUSES = {"DRAFT", "PENDING", "CANCELLED", "REJECTED", "VOID"}


def _itc_eligible_bill(bill: dict) -> bool:
    """Whether a vendor bill's GST counts toward input credit (owner decision:
    received AND not 17(5)-blocked). DEFAULT-INCLUDE: a bill with no eligibility
    flags is counted, so historical data never silently drops. Excluded only
    when EXPLICITLY blocked or not-yet-received."""
    if not isinstance(bill, dict):
        return False
    # 17(5) disallowed (food / motor vehicle / personal use ...) -> never ITC.
    if bool(bill.get("itc_blocked")):
        return False
    # An explicit itc_eligible=False also blocks (operator marked it).
    if bill.get("itc_eligible") is False:
        return False
    # Not-yet-received: explicit received=False, or a pending-ish status.
    if bill.get("received") is False:
        return False
    status = str(bill.get("status") or "").strip().upper()
    if status in _ITC_PENDING_STATUSES:
        return False
    return True


@router.get("/gst/summary")
async def get_gst_summary(
    month: Optional[int] = None,
    year: Optional[int] = None,
    current_user: dict = Depends(get_current_user),
):
    db = _get_db()
    if db is None:
        return {
            "tax_collected": 0,
            "tax_paid": 0,
            "net_gst_liability": 0,
            "gst_by_rate": {},
        }
    now = now_ist()
    m = month or now.month
    y = year or now.year

    # The GST tax period is an IST calendar month; orders.created_at is a
    # naive-UTC instant -- shift the month boundaries through ist_day_start_utc
    # (same pattern as /cash-flow). With plain datetime(y, m, 1) bounds an
    # invoice at 01-Jun 02:00 IST (= 31-May 20:30 UTC) summed into MAY.
    start = ist_day_start_utc(date(y, m, 1))
    end = ist_day_start_utc(date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1))
    # bill_date is a CALENDAR date (not an instant), so the ITC filter below
    # keeps calendar-day month bounds -- do NOT shift it through IST (see the
    # /itc-register note on bill_date framing).
    bill_start = datetime(y, m, 1)
    bill_end = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)

    # GST collected (from sales). Orders store `created_at` as a BSON
    # datetime, so the match MUST use datetime objects -- an .isoformat()
    # STRING comparison silently matched nothing and zeroed the GST summary.
    # Exclude DRAFT/CANCELLED (a cancelled sale collected no GST). total_sales
    # uses _REVENUE_EXPR (grand_total) -- `$total` is a legacy field modern
    # orders don't carry, so summing it returned ~0.
    sales_match = {
        "created_at": {"$gte": start, "$lt": end},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    # Fetch the matched sales rows once with the fields the CGST/SGST/IGST
    # classifier needs (the split happens below). The prior aggregation summed
    # total_tax then split it 50/50, which mis-stated every inter-state sale and
    # never reported IGST.
    _sales_orders = list(
        db.get_collection("orders").find(
            sales_match,
            {
                "_id": 0,
                "store_id": 1,
                "customer_id": 1,
                "tax_amount": 1,
                "tax_total": 1,
            },
        )
    )

    # GST paid (Input Tax Credit). ITC is claimable on PURCHASES recorded as
    # vendor BILLS (GRN-backed), NOT purchase_orders. Read vendor_bills.bill_date
    # (same source as /itc-register) so the summary reconciles. Date filtered in
    # Python via the tolerant parser (handles ISO string OR BSON datetime).
    #
    # ELIGIBILITY (owner decision, mix of 'received' + 'not blocked'): a bill's
    # tax counts toward ITC only when it is ELIGIBLE -- _itc_eligible_bill()
    # excludes a bill explicitly flagged itc_blocked (17(5) disallowed: food /
    # motor vehicle / etc.) OR whose status says it is not yet received
    # (DRAFT/PENDING/CANCELLED). A bill with NO such flags DEFAULTS to included
    # so historical data never silently drops. gst_amount is the legacy alias.
    gst_paid = 0.0
    gst_paid_excluded = 0.0  # surfaced so the report can show what was held back
    try:
        for _b in db.get_collection("vendor_bills").find(
            {},
            {
                "_id": 0,
                "bill_date": 1,
                "tax_amount": 1,
                "gst_amount": 1,
                "status": 1,
                "itc_blocked": 1,
                "received": 1,
                "itc_eligible": 1,
            },
        ):
            _bd = ap_engine.parse_date(_b.get("bill_date"))
            if _bd is None or not (bill_start <= _bd < bill_end):
                continue
            _tax = float(_b.get("tax_amount") or _b.get("gst_amount") or 0)
            if _itc_eligible_bill(_b):
                gst_paid += _tax
            else:
                gst_paid_excluded += _tax
    except Exception:
        gst_paid = 0.0
        gst_paid_excluded = 0.0
    gst_paid = round(gst_paid, 2)
    gst_paid_excluded = round(gst_paid_excluded, 2)

    # Classify output tax into CGST/SGST (intra-state) vs IGST (inter-state) by
    # the same store-state-vs-customer-state rule as gst_reconciliation()/GSTR-1
    # (unknown state either side -> intra-state). gst_collected is the sum of the
    # three, so the summary cards reconcile to the period total.
    cgst, sgst, igst = _split_output_tax(
        _sales_orders, _store_state_map(db), _customer_state_map(db)
    )
    gst_collected = round(cgst + sgst + igst, 2)
    net_payable = round(gst_collected - gst_paid, 2)

    # Filing status. GSTR-1 is due the 11th and GSTR-3B the 20th of the month
    # AFTER the tax period. For December (m==12) that is January of the NEXT
    # year -- the old `datetime(y, 1, 11)` kept the SAME year, so Dec returns
    # showed a due date in the past (e.g. Dec-2025 due 2025-01-11).
    due_year = y + 1 if m == 12 else y
    due_month = 1 if m == 12 else m + 1
    gstr1_due = datetime(due_year, due_month, 11)
    gstr3b_due = datetime(due_year, due_month, 20)

    return {
        "month": m,
        "year": y,
        "gst_collected": gst_collected,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "gst_input_credit": gst_paid,
        # ITC held back this period (not-yet-received or 17(5)-blocked bills) so
        # the CA can see what was excluded rather than wonder why ITC dropped.
        "gst_input_credit_excluded": gst_paid_excluded,
        "net_gst_payable": net_payable,
        "gstr1_due_date": gstr1_due.isoformat(),
        "gstr3b_due_date": gstr3b_due.isoformat(),
        "gstr1_filed": False,
        "gstr3b_filed": False,
    }


# === Outstanding Receivables ===


_DEFAULT_AR_CREDIT_TERMS_DAYS = 30


def _customer_credit_terms(db) -> dict:
    """customer_id -> credit_terms_days. Defaults to 30 days when missing.

    Threaded through AR so aging is computed from the due_date (= created +
    terms) rather than the create date -- a sale booked today with NET-45
    terms is NOT overdue tomorrow.
    """
    out: dict = {}
    try:
        for c in db.get_collection("customers").find(
            {},
            {
                "_id": 0,
                "customer_id": 1,
                "credit_terms_days": 1,
                "payment_terms_days": 1,
                "payment_terms": 1,
            },
        ):
            cid = c.get("customer_id")
            if not cid:
                continue
            terms = (
                c.get("credit_terms_days")
                or c.get("payment_terms_days")
                or c.get("payment_terms")
            )
            try:
                out[cid] = (
                    int(terms) if terms is not None else _DEFAULT_AR_CREDIT_TERMS_DAYS
                )
            except (TypeError, ValueError):
                out[cid] = _DEFAULT_AR_CREDIT_TERMS_DAYS
    except Exception:
        pass
    return out


def _ar_due_date(order: dict, terms_by_customer: dict) -> Optional[datetime]:
    """Compute the due date for an order: created_at + customer.credit_terms_days
    (fallback _DEFAULT_AR_CREDIT_TERMS_DAYS days when missing). Returns None when
    created_at can't be parsed."""
    created = ap_engine.parse_date(order.get("created_at"))
    if created is None:
        return None
    cid = order.get("customer_id")
    terms = terms_by_customer.get(cid)
    if terms is None:
        # Per-order override wins when a customer doc isn't in the map.
        try:
            terms = int(
                order.get("payment_terms_days") or _DEFAULT_AR_CREDIT_TERMS_DAYS
            )
        except (TypeError, ValueError):
            terms = _DEFAULT_AR_CREDIT_TERMS_DAYS
    return created + timedelta(days=int(terms))


def _ar_days_overdue(now: datetime, due: Optional[datetime]) -> int:
    """Days past the due date. <=0 means not yet due (current). None due ->
    treat as current."""
    if due is None:
        return 0
    return (now - due).days


@router.get("/outstanding")
async def get_outstanding(
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Customer receivables aged by DUE date, not order create date.

    Due date = order.created_at + customer.credit_terms_days (fallback 30).
    The old "days overdue" was actually order age which mislabels everything
    over 30 days as overdue even when the customer is within their NET-60
    terms. Real overdue = days past the due_date.
    """
    store_id = _scope_store(store_id, current_user)
    db = _get_db()
    if db is None:
        return []
    # A CANCELLED order is not a receivable even if it was left PARTIAL/UNPAID
    # before being voided -- exclude DRAFT/CANCELLED from AR.
    match = {
        "payment_status": {"$in": UNPAID_STATUSES},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    if store_id:
        match["store_id"] = store_id

    orders = list(
        db.get_collection("orders").find(
            match,
            {
                "_id": 0,
                "order_id": 1,
                "customer_id": 1,
                "customer_name": 1,
                "customer_phone": 1,
                "total": 1,
                "grand_total": 1,
                "amount_paid": 1,
                "created_at": 1,
                "payment_terms_days": 1,
            },
        )
    )

    terms_by_customer = _customer_credit_terms(db)
    now = now_ist_naive()
    buckets = {"0_30": 0.0, "31_60": 0.0, "61_90": 0.0, "90_plus": 0.0, "current": 0.0}
    items = []

    for o in orders:
        balance = _order_total(o) - (o.get("amount_paid", 0) or 0)
        if balance <= 0:
            continue
        due = _ar_due_date(o, terms_by_customer)
        days_overdue = _ar_days_overdue(now, due)

        if days_overdue <= 0:
            buckets["current"] += balance
        elif days_overdue <= 30:
            buckets["0_30"] += balance
        elif days_overdue <= 60:
            buckets["31_60"] += balance
        elif days_overdue <= 90:
            buckets["61_90"] += balance
        else:
            buckets["90_plus"] += balance

        cid = o.get("customer_id")
        items.append(
            {
                "order_id": o.get("order_id"),
                "customer_name": o.get("customer_name", "Unknown"),
                "customer_phone": o.get("customer_phone", ""),
                "amount": round(balance, 2),
                "days_overdue": max(0, days_overdue),
                "due_date": due.date().isoformat() if due else None,
                "payment_terms_days": terms_by_customer.get(cid)
                or o.get("payment_terms_days")
                or _DEFAULT_AR_CREDIT_TERMS_DAYS,
            }
        )

    buckets = {k: round(v, 2) for k, v in buckets.items()}
    return {
        "buckets": buckets,
        "total_outstanding": round(sum(buckets.values()), 2),
        "items": sorted(items, key=lambda x: -x["days_overdue"]),
    }


# === Vendor Payments ===


@router.get("/vendor-payments")
async def get_vendor_payments(current_user: dict = Depends(get_current_user)):
    """Per-vendor accounts-payable summary from REAL bills / payments / debit
    notes (via ap_engine). `balance` is the true outstanding payable; PO totals
    are kept only as context. Sorted by largest payable first."""
    db = _get_db()
    if db is None:
        return []
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
    today = ist_today()
    start = ist_day_start_utc(today.replace(day=1))

    active_store = validate_store_access(store_id, current_user) or current_user.get(
        "active_store_id"
    )

    # Inflows (from orders) -- scoped to the active store. created_at is a BSON
    # datetime; an .isoformat() string bound never matched, so inflow always
    # read zero. Also exclude DRAFT/CANCELLED -- a cancelled order is not cash
    # in even if it was once marked PAID.
    inflow_match = {
        "created_at": {"$gte": start},
        "payment_status": {"$in": PAID_STATUSES},
        "status": _REAL_ORDER_STATUS_FILTER,
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
    # Expenses are dated on `expense_date` (date-only 'YYYY-MM-DD' string), NOT
    # `date`; the old field name dropped every expense. The boundary uses
    # start.date().isoformat() (date-only) so it compares cleanly with the
    # stored date-only strings and INCLUDES 1st-of-month expenses (a datetime
    # 'YYYY-MM-01T00:00:00' boundary would sort AFTER the bare 'YYYY-MM-01').
    exp_match = {
        "expense_date": {"$gte": start.date().isoformat()},
        "status": {"$in": ["APPROVED", "PAID", "approved", "paid"]},
    }
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
                {
                    "$group": {
                        "_id": None,
                        "total": {
                            "$sum": {
                                "$ifNull": ["$total_amount", {"$ifNull": ["$total", 0]}]
                            }
                        },
                    }
                },
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
                        {
                            "$match": {
                                "payment_date": {"$gte": start.date().isoformat()}
                            }
                        },
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
    """Customer receivables aged by DUE date.

    due_date = order.created_at + customer.credit_terms_days (fallback 30).
    Buckets: 'current' (not yet due), then 0_30 / 31_60 / 61_90 / 90_plus
    measured from days PAST due. 'overdue' totals anything past due.

    Pre-fix this aged by (now - created_at), which mislabeled current-status
    receivables (NET-60 customer, sold 25 days ago) as already in the 0-30
    overdue bucket. The mirror /outstanding is also fixed to match.
    """
    buckets = {
        "current": 0.0,
        "0_30": 0.0,
        "31_60": 0.0,
        "61_90": 0.0,
        "90_plus": 0.0,
    }
    total = 0.0
    try:
        orders = list(
            db.get_collection("orders").find(
                {
                    "payment_status": {"$in": UNPAID_STATUSES},
                    # A cancelled order is not a receivable.
                    "status": _REAL_ORDER_STATUS_FILTER,
                },
                {
                    "_id": 0,
                    "customer_id": 1,
                    "grand_total": 1,
                    "total": 1,
                    "amount_paid": 1,
                    "created_at": 1,
                    "payment_terms_days": 1,
                },
            )
        )
    except Exception:
        orders = []
    terms_by_customer = _customer_credit_terms(db)
    for o in orders:
        bal = _order_total(o) - float(o.get("amount_paid", 0) or 0)
        if bal <= 0:
            continue
        due = _ar_due_date(o, terms_by_customer)
        days_overdue = _ar_days_overdue(now, due)
        if days_overdue <= 0:
            buckets["current"] += bal
        elif days_overdue <= 30:
            buckets["0_30"] += bal
        elif days_overdue <= 60:
            buckets["31_60"] += bal
        elif days_overdue <= 90:
            buckets["61_90"] += bal
        else:
            buckets["90_plus"] += bal
        total += bal
    buckets = {k: round(v, 2) for k, v in buckets.items()}
    # 'Overdue' is everything past the due date (any bucket except current).
    overdue = round(
        buckets["0_30"] + buckets["31_60"] + buckets["61_90"] + buckets["90_plus"], 2
    )
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
    now = now_ist_naive()
    start = ist_day_start_utc(now.replace(day=1).date())

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
            # Datetime bound (created_at is BSON datetime) + exclude
            # DRAFT/CANCELLED so the owner's month revenue is real.
            "created_at": {"$gte": start},
            "payment_status": {"$in": PAID_STATUSES},
            "status": _REAL_ORDER_STATUS_FILTER,
        },
        _REVENUE_EXPR,
    )
    expenses = _agg_sum(
        db,
        "expenses",
        {
            # Expenses are dated on `expense_date` (date-only 'YYYY-MM-DD'
            # string), NOT `date` -- the old field name matched nothing, so
            # this-month expenses read 0 and net_cash_flow was overstated by
            # the entire expense total. Use a date-only bound to match.
            "expense_date": {"$gte": start.date().isoformat()},
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

    # Structured alerts: emit `amount` + `label_template` (with a '{}' slot for
    # the FE-rendered INR symbol). `message` keeps an ASCII-only fallback so
    # existing consumers don't break (no Rupee sign in Python source -- breaks
    # Windows cp1252 on print/logger). The FE renders the rupee glyph via
    # inr() over the `amount` value.
    alerts = []
    if ap_overdue > 0:
        alerts.append(
            {
                "level": "warning",
                "amount": ap_overdue,
                "label_template": "{} of vendor payables overdue",
                "message": f"INR {ap_overdue:.0f} of vendor payables overdue",
            }
        )
    if due_7d > 0:
        alerts.append(
            {
                "level": "info",
                "amount": due_7d,
                "label_template": "{} of vendor bills due within 7 days",
                "message": f"INR {due_7d:.0f} of vendor bills due within 7 days",
            }
        )
    if ar["overdue"] > 0:
        alerts.append(
            {
                "level": "warning",
                "amount": ar["overdue"],
                "label_template": "{} of receivables past due date",
                "message": f"INR {ar['overdue']:.0f} of receivables past due date",
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
    now = now_ist_naive()

    # Inflows from AR.
    inflow_events = []
    try:
        orders = list(
            db.get_collection("orders").find(
                {
                    "payment_status": {"$in": UNPAID_STATUSES},
                    # Don't project a cancelled order as an expected collection.
                    "status": _REAL_ORDER_STATUS_FILTER,
                },
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

    # Recurring monthly outflow estimate. Expenses are dated on `expense_date`
    # (date-only 'YYYY-MM-DD' string), NOT `date` -- the old field name matched
    # nothing, so the recurring estimate was always 0 and the forecast
    # understated outflows. Use a date-only bound to match the stored values.
    monthly_expense_est = 0.0
    try:
        three_mo_ago = (now - timedelta(days=90)).date().isoformat()
        r = list(
            db.get_collection("expenses").aggregate(
                [
                    {
                        "$match": {
                            "expense_date": {"$gte": three_mo_ago},
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


# === N8 owner "survival" cash-flow (essential vs deferrable + min-pay) ======
# Read-only analytics: NOTHING in this block writes to any collection. The
# pure math lives in services/survival_cashflow.py; these helpers only fetch
# rows + resolve the two E2 policy lists, so each piece is fail-soft and
# independently testable.


def _survival_policy_lists() -> tuple:
    """(essential_heads, critical_vendors) from E2 policy, fail-soft.

    Both keys resolve at GLOBAL scope (the survival view is an org-wide owner
    figure). Junk values (non-list) fall back to the code defaults; an owner
    who explicitly saves an EMPTY essential list is honored (everything
    becomes deferrable -- that is a meaningful policy choice, not junk).
    """
    try:
        essential = policy_engine.get_policy(
            "finance.survival_essential_heads",
            default=survival_cashflow.ESSENTIAL_DEFAULT_HEADS,
        )
    except Exception:
        essential = None
    try:
        critical = policy_engine.get_policy(
            "finance.survival_critical_vendors", default=[]
        )
    except Exception:
        critical = None
    if not isinstance(essential, list):
        essential = list(survival_cashflow.ESSENTIAL_DEFAULT_HEADS)
    if not isinstance(critical, list):
        critical = []
    return essential, critical


def _survival_month_expense_rows(db, now: datetime, store_id: Optional[str] = None):
    """Current-month committed expenses grouped by head (rupees).

    Same field conventions as the rest of this router: `expense_date` is a
    date-only 'YYYY-MM-DD' string and committed states are APPROVED / PAID.
    """
    start = date(now.year, now.month, 1).isoformat()
    end = (
        date(now.year + 1, 1, 1)
        if now.month == 12
        else date(now.year, now.month + 1, 1)
    ).isoformat()
    match = {
        "expense_date": {"$gte": start, "$lt": end},
        "status": {"$in": ["APPROVED", "PAID", "approved", "paid"]},
    }
    if store_id:
        match["store_id"] = store_id
    try:
        rows = list(
            db.get_collection("expenses").aggregate(
                [
                    {"$match": match},
                    {"$group": {"_id": "$category", "total": {"$sum": "$amount"}}},
                ]
            )
        )
    except Exception:
        rows = []
    return [
        {"head": (r.get("_id") or "uncategorized"), "amount": r.get("total") or 0}
        for r in rows
    ]


def _survival_ap_items(db):
    """Open AP bills as aging items (rupee `outstanding`, resolved `due_date`)
    + the raw bill's vendor_critical flag carried through.

    Vendor bills carry no store_id (they are entity-level liabilities), so the
    AP side of the survival view is always org-wide.
    """
    bills, payments, dn = _ap_rows(db)
    ap = ap_engine.build_aging(bills, payments, dn)
    crit_by_bill = {}
    for b in bills:
        if isinstance(b, dict) and b.get("bill_id") is not None:
            crit_by_bill[b["bill_id"]] = bool(b.get("vendor_critical"))
    items = []
    for it in ap.get("items", []):
        row = dict(it)
        row["vendor_critical"] = crit_by_bill.get(it.get("bill_id"), False)
        items.append(row)
    return items


def _survival_projected_income_paise(
    db, now: datetime, store_id: Optional[str] = None
) -> int:
    """This month's PAID revenue-to-date pro-rated to a full month, in paise.

    Uses the exact same revenue definition as /owner-dashboard (paid orders,
    DRAFT/CANCELLED excluded, datetime bound on created_at) so the two owner
    views can never disagree about what 'income' means.
    """
    start = ist_day_start_utc(now.replace(day=1).date())
    match = {
        "created_at": {"$gte": start},
        "payment_status": {"$in": PAID_STATUSES},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    if store_id:
        match["store_id"] = store_id
    revenue_to_date = _agg_sum(db, "orders", match, _REVENUE_EXPR)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    projected = revenue_to_date / max(now.day, 1) * days_in_month
    return int(round(projected * 100))


def _build_survival_payload(db, now: datetime, store_id: Optional[str] = None) -> dict:
    """Assemble inputs and run the pure builder. db=None -> all-zero view."""
    essential, critical = _survival_policy_lists()
    if db is None:
        expenses, ap_items, income = [], [], 0
    else:
        expenses = _survival_month_expense_rows(db, now, store_id=store_id)
        ap_items = _survival_ap_items(db)
        income = _survival_projected_income_paise(db, now, store_id=store_id)
    # P3-2: month-to-date fraction = elapsed days / days in month. The income
    # helper projects full-month from revenue-to-date by dividing by now.day,
    # so scaling that projection back by exactly this fraction recovers the
    # month-to-date booked revenue -- a true like-for-like vs MTD expenses.
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    mtd_fraction = min(now.day, days_in_month) / days_in_month
    return survival_cashflow.build_survival_view(
        expenses,
        ap_items,
        income,
        now=now,
        essential_heads=essential,
        critical_vendors=critical,
        # P3-1: AP is always org-wide; income/expenses are store-scoped only
        # when a store filter is supplied.
        store_scoped=bool(store_id),
        month_to_date_fraction=mtd_fraction,
    )


@router.get("/survival-cashflow")
async def get_survival_cashflow(
    store_id: Optional[str] = Query(
        None,
        description="Filter expenses + income to one store. AP bills are "
        "entity-level liabilities and stay org-wide.",
    ),
    current_user: dict = Depends(get_current_user),
):
    """Owner "survival" view: ESSENTIAL fixed costs vs MUST-PAY vendor bills
    vs DEFERRABLE spend, with the min-pay scenario (fixed + must-pay) compared
    against projected month income (this month's paid revenue pro-rated).

    Read-only analytics; integer paise. ADMIN / ACCOUNTANT only -- mirrors
    /owner-dashboard's gate exactly.
    """
    _require_finance_admin(current_user)
    db = _get_db()
    now = now_ist_naive()
    return {
        "as_of": now.date().isoformat(),
        "month": f"{now.year:04d}-{now.month:02d}",
        "store_id": store_id,
        "survival": _build_survival_payload(db, now, store_id=store_id),
    }


# === GST input-tax-credit (ITC) reconciliation (ADMIN / ACCOUNTANT) ===


def _primary_entity_state(db, entity_id: Optional[str] = None) -> Optional[str]:
    """Resolve the primary state code for the entity.

    When `entity_id` is given, take that entity's `primary_state` / `state`.
    Otherwise pick the first entity in the DB. Returns None when no entity
    matches -- in that case the ITC register falls back to intra-state
    behaviour (existing rows aren't reclassified).
    """
    try:
        coll = db.get_collection("entities")
        if entity_id:
            doc = coll.find_one(
                {"entity_id": entity_id},
                {"_id": 0, "primary_state": 1, "state": 1, "state_code": 1},
            )
        else:
            doc = coll.find_one(
                {}, {"_id": 0, "primary_state": 1, "state": 1, "state_code": 1}
            )
        if not doc:
            return None
        return (
            doc.get("primary_state")
            or doc.get("state_code")
            or doc.get("state")
            or None
        )
    except Exception:
        return None


@router.get("/itc-register")
async def itc_register(
    period: Optional[str] = Query(None, description="YYYY-MM filter; omit for all"),
    entity_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Input tax credit available from booked vendor bills, grouped by period.

    When `period` (YYYY-MM) is given, only that period is returned in
    `periods[]` (totals still represent the full bill set so the FE can show
    a "Total booked ITC" anchor)."""
    _require_finance_admin(current_user)
    db = _get_db()
    if db is None:
        return {
            "periods": [],
            "total_taxable": 0,
            "total_itc": 0,
            "total_cgst": 0,
            "total_sgst": 0,
            "total_igst": 0,
        }
    try:
        bills = list(
            db.get_collection("vendor_bills").find(
                {},
                {
                    "_id": 0,
                    "bill_date": 1,
                    "taxable_amount": 1,
                    "tax_amount": 1,
                    "place_of_supply": 1,
                },
            )
        )
    except Exception:
        bills = []
    entity_state = _primary_entity_state(db, entity_id)
    out = itc_reconcile.build_itc_register(bills, entity_state=entity_state)
    if period:
        out["periods"] = [p for p in out["periods"] if p.get("period") == period]
    return out


class Gstr2bRow(BaseModel):
    gstin: Optional[str] = None
    invoice_no: Optional[str] = None
    taxable: Optional[float] = 0
    tax: Optional[float] = 0


class Gstr2bReconcileBody(BaseModel):
    rows: List[Gstr2bRow] = Field(default_factory=list)
    as_of: Optional[str] = None


def _book_rows_from_db(db) -> List[dict]:
    """Pull all vendor bills + their vendor GSTIN, formatted for the reconciler."""
    gstin_by_vendor: Dict[str, str] = {}
    try:
        for v in db.get_collection("vendors").find(
            {}, {"_id": 0, "vendor_id": 1, "gstin": 1}
        ):
            gstin_by_vendor[v.get("vendor_id")] = v.get("gstin")
    except Exception:
        pass
    rows = []
    try:
        for b in db.get_collection("vendor_bills").find({}, {"_id": 0}):
            rows.append(
                {
                    "gstin": gstin_by_vendor.get(b.get("vendor_id")),
                    "invoice_no": b.get("bill_number"),
                    "taxable": b.get("taxable_amount"),
                    "tax": b.get("tax_amount"),
                    "bill_id": b.get("bill_id"),
                    "vendor_name": b.get("vendor_name"),
                    "bill_date": b.get("bill_date"),
                    "place_of_supply": b.get("place_of_supply"),
                }
            )
    except Exception:
        pass
    return rows


@router.post("/gstr2b-reconcile")
async def gstr2b_reconcile(
    body: Gstr2bReconcileBody, current_user: dict = Depends(get_current_user)
):
    """Reconcile booked vendor bills against an uploaded GSTR-2B (rows parsed
    client-side from the portal download). Returns matched / mismatch /
    only-in-books (ITC at risk) / only-in-2B buckets, plus a sum-identity
    summary (matched + mismatch + at-risk == total booked ITC)."""
    _require_finance_admin(current_user)
    rows = [r.model_dump() for r in body.rows]
    db = _get_db()
    if db is None:
        return itc_reconcile.reconcile_gstr2b([], rows, as_of_iso=body.as_of)
    return itc_reconcile.reconcile_gstr2b(
        _book_rows_from_db(db), rows, as_of_iso=body.as_of
    )


_ITC_CSV_HEADERS = {
    "matched": [
        "vendor_name",
        "gstin",
        "invoice_no",
        "bill_date",
        "book_tax",
        "portal_tax",
    ],
    "mismatch": [
        "vendor_name",
        "gstin",
        "invoice_no",
        "bill_date",
        "book_tax",
        "portal_tax",
        "diff",
    ],
    "only_in_books": [
        "vendor_name",
        "gstin",
        "invoice_no",
        "bill_date",
        "book_tax",
        "days_old",
    ],
    "only_in_2b": ["gstin", "invoice_no", "taxable", "tax"],
}


@router.post("/itc-export")
async def itc_export_csv(
    body: Gstr2bReconcileBody,
    bucket: str = Query(..., pattern="^(matched|mismatch|only_in_books|only_in_2b)$"),
    current_user: dict = Depends(get_current_user),
):
    """CSV export of a single reconciliation bucket. POST instead of GET
    because the GSTR-2B rows live client-side (the FE keeps the upload in
    memory; re-uploading on every download would be terrible UX)."""
    _require_finance_admin(current_user)
    rows = [r.model_dump() for r in body.rows]
    db = _get_db()
    book_rows = _book_rows_from_db(db) if db is not None else []
    recon = itc_reconcile.reconcile_gstr2b(book_rows, rows, as_of_iso=body.as_of)
    bucket_rows = recon.get(bucket) or []
    headers = _ITC_CSV_HEADERS[bucket]

    buf = io.StringIO()
    # BUG-139: neutralize formula-injection -- the GSTR-2B rows are uploaded
    # client-side, so vendor_name/gstin/invoice_no are fully attacker-controlled.
    writer = csv_safe.safe_writer(buf)
    writer.writerow(headers)
    for r in bucket_rows:
        writer.writerow([r.get(h, "") for h in headers])
    csv_bytes = (csv_safe.BOM + buf.getvalue()).encode("utf-8")
    fname = f"itc_{bucket}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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

    if db is None:
        raise HTTPException(503, "Database not available")

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
    if db is None:
        return []
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
    now = now_ist()
    m = month or now.month
    y = year or now.year

    budget = db.get_collection("budgets").find_one(
        {"month": m, "year": y, "mode": mode}, {"_id": 0}
    )
    if not budget:
        # No budget configured for this period -- return an honest empty
        # skeleton (budget=0 for all categories) rather than fabricated
        # allocation numbers. The UI should prompt the user to set a budget.
        budget = {
            "month": m,
            "year": y,
            "mode": mode,
            "no_budget_set": True,
            "categories": {
                "rent": {"budget": 0, "actual": 0},
                "salaries": {"budget": 0, "actual": 0},
                "utilities": {"budget": 0, "actual": 0},
                "marketing": {"budget": 0, "actual": 0},
                "inventory": {"budget": 0, "actual": 0},
                "miscellaneous": {"budget": 0, "actual": 0},
            },
        }

    # Fill actuals from expenses. Expenses are dated on `expense_date`
    # (date-only 'YYYY-MM-DD' string), NOT `date`; the old field name + datetime
    # isoformat boundary matched nothing. Use date-only string bounds to match
    # the stored values.
    start = datetime(y, m, 1)
    end = datetime(y, m + 1 if m < 12 else 1, 1) if m < 12 else datetime(y + 1, 1, 1)
    actuals = list(
        db.get_collection("expenses").aggregate(
            [
                {
                    "$match": {
                        "expense_date": {
                            "$gte": start.date().isoformat(),
                            "$lt": end.date().isoformat(),
                        },
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

    if mode == "survival":
        # N8: this branch used to be DEAD -- the budgets writer never stores a
        # `mode` field, so the lookup above always missed and mode=survival
        # returned only the empty no_budget_set skeleton. Wire it to the REAL
        # survival view (kept on the existing envelope for back-compat; the
        # dedicated GET /finance/survival-cashflow is the first-class API).
        # The survival figures are org-wide owner material (AP totals +
        # projected income), so this mode narrows to the owner-dashboard gate
        # -- the plain budget skeleton stays visible to the wider finance set.
        _require_finance_admin(current_user)
        budget.pop("no_budget_set", None)
        # Survival is inherently an as-of-NOW "can I cover THIS month" question
        # (it weighs live overdue AP + month-to-date revenue), so it cannot be
        # rendered for an arbitrary historical month/year. P3-3: rather than
        # silently embed a NOW view inside an envelope stamped with a requested
        # past month, stamp the survival block with the real as-of date and
        # flag when the requested period is not the current month, so the
        # envelope can never mislead.
        survival_now = now_ist_naive()
        survival = _build_survival_payload(db, survival_now)
        budget["survival"] = survival
        budget["survival_as_of"] = survival["as_of"]
        budget["survival_month"] = f"{survival_now.year:04d}-{survival_now.month:02d}"
        requested_is_current = m == survival_now.month and y == survival_now.year
        budget["survival_reflects_requested_period"] = requested_is_current
        if not requested_is_current:
            budget["survival_note"] = (
                f"The budget skeleton is for {y:04d}-{m:02d}, but the survival "
                f"block is an as-of-{survival['as_of']} view of the CURRENT "
                "month -- it always reflects today's overdue AP and "
                "month-to-date revenue, not the requested historical period."
            )

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
    # Org-wide, entity-grouped GST reconciliation is a filing/accounting view --
    # finance-admin only (owner decision 2026-06-16), so a single-store
    # STORE_MANAGER/AREA_MANAGER cannot read every entity's GST position.
    _require_finance_admin(current_user)
    db = _get_db()
    now = now_ist()
    m = month or now.month
    y = year or now.year
    # IST calendar month -> naive-UTC created_at bounds (same as /gst/summary),
    # so the reconciliation and GSTR-1/3B agree on the period's invoices.
    start = ist_day_start_utc(date(y, m, 1))
    end = ist_day_start_utc(date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1))

    s2e, enames = _store_maps(db)
    store_ids = None
    if entity_id:
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]

    # Orders persist created_at as a BSON datetime; the previous .isoformat()
    # string bound matched nothing, so GST-collected per entity read zero.
    # Exclude DRAFT/CANCELLED -- only real sales carry GST liability.
    o_match = {
        "created_at": {"$gte": start, "$lt": end},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    if store_ids is not None:
        o_match["store_id"] = {"$in": store_ids}
    orders = list(
        db.get_collection("orders").find(
            o_match,
            {
                "_id": 0,
                "store_id": 1,
                "customer_id": 1,
                "tax_amount": 1,
                "tax_total": 1,
            },
        )
    )

    # purchase_orders.date is a CALENDAR-date string -- keep calendar-frame
    # month bounds for it (do NOT reuse the IST-shifted created_at instants).
    p_lo = datetime(y, m, 1).isoformat()
    p_hi = (datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)).isoformat()
    p_match = {"date": {"$gte": p_lo, "$lt": p_hi}}
    if store_ids is not None:
        p_match["delivery_store_id"] = {"$in": store_ids}
    purchases = list(
        db.get_collection("purchase_orders").find(
            p_match, {"_id": 0, "delivery_store_id": 1, "store_id": 1, "tax_amount": 1}
        )
    )

    recon = gst_reconciliation(
        orders,
        purchases,
        s2e,
        enames,
        store_state_by_id=_store_state_map(db),
        customer_state_by_id=_customer_state_map(db),
    )
    recon.update(
        {
            "month": m,
            "year": y,
            "note": (
                "Output tax split intra-state (CGST+SGST) vs inter-state (IGST) "
                "by store vs customer state; file via Tally."
            ),
        }
    )
    return recon


def _jv_cgst_sgst_split(tax: float) -> tuple:
    """Split a line's total GST into intra-state CGST + SGST so they sum to the
    tax EXACTLY (the rounding residual goes on SGST). A naive round(tax/2) on both
    sides over-states by a paisa on odd-paise tax (100.01 -> 50.01 + 50.01 =
    100.02), which IMBALANCES the Tally voucher and gets it rejected on import.
    Mirrors orders._build_invoice_gst_split."""
    cgst = round(tax / 2.0, 2)
    sgst = round(tax - cgst, 2)
    return cgst, sgst


@router.get("/tally/sales-jv")
async def get_tally_sales_jv(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Tally sales-voucher XML for a period + scope, ready to import into Tally."""
    # Org-wide sales-voucher export is an accounting function -- finance-admin
    # only (owner decision 2026-06-16); a store-level role can't export all
    # stores' sales JV. Mirror of the tender-receipt-jv sibling gate.
    _require_finance_admin(current_user)
    db = _get_db()
    # Never export DRAFT/CANCELLED orders to Tally -- they aren't real sales
    # vouchers. created_at is a BSON datetime so the range is built as datetimes
    # (a 'YYYY-MM-DD' string bound never matched -> empty export).
    match: dict = {"status": _REAL_ORDER_STATUS_FILTER}
    store_ids = None
    if store_id:
        store_ids = [store_id]
    elif entity_id:
        s2e, _ = _store_maps(db)
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]
    if store_ids is not None:
        match["store_id"] = {"$in": store_ids}
    _apply_created_at_range(match, from_date, to_date)

    orders = list(db.get_collection("orders").find(match, {"_id": 0}))
    # Determine inter-state vs intra-state for each order so the Tally voucher
    # uses the correct output ledger (IGST for inter-state, CGST+SGST for intra).
    _store_states = _store_state_map(db)
    _customer_states = _customer_state_map(db)
    for o in orders:
        tax = float(o.get("tax_amount") or o.get("tax_total") or 0)
        grand = float(o.get("grand_total") or o.get("total") or 0)
        seller = _norm_state(_store_states.get(o.get("store_id")))
        buyer = _norm_state(_customer_states.get(o.get("customer_id")))
        is_inter_state = bool(seller and buyer and seller != buyer)
        if is_inter_state:
            o["igst_amount"] = round(tax, 2)
            o["cgst_amount"] = 0.0
            o["sgst_amount"] = 0.0
        else:
            cgst, sgst = _jv_cgst_sgst_split(tax)
            o["igst_amount"] = 0.0
            o["cgst_amount"] = cgst
            o["sgst_amount"] = sgst
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
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    # E5 wiring (flag-gated, ADDITIVE): when policy tally.tender_receipt_voucher
    # is ON, OFFER the companion tender-routed Receipt voucher via a response
    # header. The XML BODY of this Sales export is byte-identical whether the
    # flag is on or off -- per the adversarial-chair guidance, receipt legs are
    # NEVER injected into the existing Sales vouchers; they live on the sibling
    # route below. Fail-dark: a policy-read error adds nothing.
    try:
        from .reconciliation import tender_receipt_policy_enabled

        if tender_receipt_policy_enabled(store_id=store_id, entity_id=entity_id):
            headers["X-Tally-Tender-Receipt"] = (
                "/api/v1/finance/tally/tender-receipt-jv"
            )
    except Exception:  # noqa: BLE001
        pass
    return Response(
        content=xml,
        media_type="application/xml",
        headers=headers,
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
    store_ids = (
        [sid for sid, eid in s2e.items() if eid == entity_id] if entity_id else None
    )

    # Exclude DRAFT/CANCELLED and compare the date range as datetimes
    # (created_at is a BSON datetime). This `match` is reused for both the
    # revenue aggregation and the COGS find() below.
    match: dict = {"status": _REAL_ORDER_STATUS_FILTER}
    if store_ids is not None:
        match["store_id"] = {"$in": store_ids}
    _apply_created_at_range(match, from_date, to_date)

    rev = list(
        db.get_collection("orders").aggregate(
            [
                {"$match": match},
                {"$group": {"_id": "$store_id", "revenue": {"$sum": _REVENUE_EXPR}}},
            ]
        )
    )
    rev_by_store = {r["_id"]: r["revenue"] for r in rev}

    cost_map = _cost_by_product(db)
    cogs_by_store: dict = {}
    cogs_estimated_by_store: dict = {}  # store_id -> bool (any line estimated)
    for o in db.get_collection("orders").find(
        match, {"_id": 0, "store_id": 1, "items": 1}
    ):
        sid = o.get("store_id")
        _c, _est, _tot = compute_cogs_with_flag([o], cost_map, fallback_rate=0.6)
        cogs_by_store[sid] = cogs_by_store.get(sid, 0) + _c
        if _est > 0:
            cogs_estimated_by_store[sid] = True

    # Expenses are dated on `expense_date` (ISO 'YYYY-MM-DD' string), not
    # `date`; the old field name silently dropped every expense for any
    # date-ranged P&L. from_date / to_date are 'YYYY-MM-DD' strings -> the
    # string comparison is consistent.
    exp_match: dict = {"status": {"$in": ["APPROVED", "PAID", "approved", "paid"]}}
    if store_ids is not None:
        exp_match["store_id"] = {"$in": store_ids}
    if from_date:
        exp_match.setdefault("expense_date", {})["$gte"] = from_date
    if to_date:
        exp_match.setdefault("expense_date", {})["$lte"] = to_date
    exp = list(
        db.get_collection("expenses").aggregate(
            [
                {"$match": exp_match},
                {"$group": {"_id": "$store_id", "amt": {"$sum": "$amount"}}},
            ]
        )
    )
    exp_by_store = {e["_id"]: e["amt"] for e in exp}

    pay_by_store = _payroll_by_store(db, from_date, to_date)

    rows = []
    for sid in (
        set(rev_by_store) | set(cogs_by_store) | set(exp_by_store) | set(pay_by_store)
    ):
        r = round(rev_by_store.get(sid, 0), 2)
        c = round(cogs_by_store.get(sid, 0), 2)
        e = round(exp_by_store.get(sid, 0), 2)
        p = round(pay_by_store.get(sid, 0), 2)
        net = round(r - c - e - p, 2)
        rows.append(
            {
                "store_id": sid,
                "entity_id": s2e.get(sid),
                "revenue": r,
                "cogs": c,
                # True when any order line used the 60%-fallback (no cost_price).
                # Gross margin for this store should be treated as approximate.
                "cogs_is_estimated": bool(cogs_estimated_by_store.get(sid)),
                "expenses": e,
                "payroll": p,
                "net_profit": net,
                "net_margin": round(net / r * 100, 1) if r > 0 else 0,
            }
        )
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
    # Exclude DRAFT/CANCELLED; compare the date range as datetimes
    # (created_at is a BSON datetime, so a string bound matched nothing).
    match: dict = {"status": _REAL_ORDER_STATUS_FILTER}
    store_ids = None
    if store_id:
        store_ids = [store_id]
    elif entity_id:
        s2e, _ = _store_maps(db)
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]
    if store_ids is not None:
        match["store_id"] = {"$in": store_ids}
    _apply_created_at_range(match, from_date, to_date)

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


# ============================================================================
# CASH REGISTER / EOD RECONCILIATION
# ============================================================================
# A till session: opened with a denomination float, closed with a counted
# denomination breakdown. Expected cash = opening + POS CASH sales for the
# window - cash refunds - cash payouts/expenses - bank deposit. Variance =
# counted - expected. Store-scoped; persisted to `cash_register_sessions`.
#
# Pure money math lives in services/cash_register.py; this router owns
# persistence, store scoping, and pulling the POS CASH figure for the window.

_CASH_SESSIONS = "cash_register_sessions"


class DenominationLine(BaseModel):
    face: int = Field(..., description="Face value in rupees, e.g. 500")
    pieces: int = Field(0, ge=0, description="Number of notes/coins of this face")
    kind: str = Field("note", description="'note' or 'coin'")


class CashRegisterOpen(BaseModel):
    store_id: Optional[str] = None
    shift: Optional[str] = None  # AM / PM / FULL (free text)
    denominations: List[DenominationLine] = Field(default_factory=list)
    opening_float: Optional[float] = None  # optional override of denom sum
    note: Optional[str] = None


class CashRegisterClose(BaseModel):
    session_id: str
    denominations: List[DenominationLine] = Field(default_factory=list)
    bank_deposit: float = 0.0
    counted_override: Optional[float] = None  # optional override of denom sum
    tolerance: float = 0.0
    note: Optional[str] = None


def _iso_now() -> str:
    return datetime.utcnow().isoformat()


def _to_dt(s):
    """Parse an ISO date/datetime string to a datetime (None on failure)."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:19])
    except (ValueError, TypeError):
        try:
            return datetime.fromisoformat(str(s)[:10])
        except (ValueError, TypeError):
            return None


def _cash_sales_for_window(db, store_id: str, start_iso: str, end_iso: Optional[str]):
    """Net POS CASH collected for a store between start and end (ISO strings).

    Sums order.payments[] where method == 'CASH' (the canonical tender field;
    `mode` tolerated as a legacy alias). Negative CASH tenders (refunds) are
    returned separately so the reconciliation can show sales vs refunds.
    Returns (cash_sales, cash_refunds) as positive magnitudes."""
    if db is None:
        return 0.0, 0.0
    # BUG-031: orders.created_at is a BSON Date, so an ISO-STRING $gte/$lte never
    # matches it (Mongo type-bracketing) -> cash_sales always 0 -> false drawer
    # variance. Match BOTH a datetime window (current Date-typed docs) AND a
    # string window (any legacy ISO-string created_at) via $or.
    start_dt = _to_dt(start_iso)
    date_win: Dict = {"$gte": start_dt} if start_dt else {}
    str_win: Dict = {"$gte": start_iso}
    if end_iso:
        str_win["$lte"] = end_iso
        end_dt = _to_dt(end_iso)
        if end_dt:
            date_win["$lte"] = end_dt
    or_clauses = [{"created_at": str_win}]
    if date_win:
        or_clauses.insert(0, {"created_at": date_win})
    match: Dict = {"store_id": store_id, "$or": or_clauses}

    cash_sales = 0.0
    cash_refunds = 0.0
    try:
        cursor = db.get_collection("orders").find(match, {"_id": 0, "payments": 1})
        for o in cursor:
            for p in o.get("payments") or []:
                method = str(p.get("method") or p.get("mode") or "").upper()
                if method != "CASH":
                    continue
                try:
                    amt = float(p.get("amount", 0) or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                if amt >= 0:
                    cash_sales += amt
                else:
                    cash_refunds += -amt
    except Exception:
        return 0.0, 0.0
    return round(cash_sales, 2), round(cash_refunds, 2)


def _cash_expenses_for_window(
    db, store_id: str, start_iso: str, end_iso: Optional[str]
):
    """Cash payouts from the drawer for a store in the window.

    Expenses use `expense_date` (ISO date) and `payment_mode`. Only CASH-mode
    expenses come out of the physical drawer; UPI/CARD/BANK don't. Counts
    APPROVED / PAID / SENT_TO_ACCOUNTANT spends (anything that represents money
    actually disbursed). Fail-soft to 0."""
    if db is None:
        return 0.0
    start_day = start_iso[:10]
    end_day = (end_iso or now_ist().isoformat())[:10]
    total = 0.0
    try:
        cursor = db.get_collection("expenses").find(
            {
                "store_id": store_id,
                "expense_date": {"$gte": start_day, "$lte": end_day},
            },
            {"_id": 0, "amount": 1, "payment_mode": 1, "status": 1, "expense_date": 1},
        )
        for e in cursor:
            mode = str(e.get("payment_mode") or "").upper()
            if mode and mode != "CASH":
                continue  # unknown mode counts as cash (conservative)
            status = str(e.get("status") or "").upper()
            if status not in ("APPROVED", "PAID", "SENT_TO_ACCOUNTANT", "REIMBURSED"):
                continue
            try:
                total += float(e.get("amount", 0) or 0)
            except (TypeError, ValueError):
                pass
    except Exception:
        return 0.0
    return round(total, 2)


@router.post("/cash-register/open")
async def open_cash_register(
    body: CashRegisterOpen,
    current_user: dict = Depends(get_current_user),
):
    """Open a till session with an opening float counted by denomination.

    Store-scoped (validate_store_access). Blocks a second OPEN session for the
    same store so the drawer can't be opened twice without closing."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    store_id = validate_store_access(body.store_id or "", current_user)
    if not store_id:
        raise HTTPException(status_code=400, detail="No store context for this user")

    coll = db.get_collection(_CASH_SESSIONS)

    # Guard: one OPEN session per store at a time.
    existing = None
    try:
        existing = coll.find_one({"store_id": store_id, "status": "OPEN"})
    except Exception:
        existing = None
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "A cash register session is already open for this store. "
                "Close it before opening a new one."
            ),
        )

    denoms = cash_register.normalize_denominations(
        [d.model_dump() for d in body.denominations]
    )
    denom_total = cash_register.total_from_denominations(denoms)
    opening_float = (
        round(float(body.opening_float), 2)
        if body.opening_float is not None
        else denom_total
    )

    now = _iso_now()
    session_id = f"CR-{store_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    doc = {
        "session_id": session_id,
        "store_id": store_id,
        "status": "OPEN",
        "shift": (body.shift or "").upper() or None,
        "opening_float": opening_float,
        "opening_denominations": denoms,
        "opened_at": now,
        "opened_by": current_user.get("user_id"),
        "opened_by_name": current_user.get("name"),
        "opening_note": body.note,
        # close-time fields, filled on /close
        "closed_at": None,
        "closed_by": None,
        "closed_by_name": None,
        "closing_denominations": [],
        "counted": None,
        "expected": None,
        "variance": None,
        "variance_status": None,
    }
    try:
        coll.insert_one(dict(doc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Could not open session: {exc}")
    doc.pop("_id", None)
    return doc


@router.post("/cash-register/close")
async def close_cash_register(
    body: CashRegisterClose,
    current_user: dict = Depends(get_current_user),
):
    """Close a till session: count the drawer by denomination, compute expected
    vs counted variance, and lock the session.

    Expected = opening float + POS CASH sales for the session window
    - cash refunds - cash expenses - bank deposit."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    coll = db.get_collection(_CASH_SESSIONS)
    session = None
    try:
        session = coll.find_one({"session_id": body.session_id})
    except Exception:
        session = None
    if session is None:
        raise HTTPException(status_code=404, detail="Cash register session not found")

    store_id = validate_store_access(session.get("store_id") or "", current_user)
    if session.get("status") == "CLOSED":
        raise HTTPException(status_code=409, detail="Session already closed")

    start_iso = session.get("opened_at") or _iso_now()
    end_iso = _iso_now()

    cash_sales, cash_refunds = _cash_sales_for_window(db, store_id, start_iso, end_iso)
    cash_expenses = _cash_expenses_for_window(db, store_id, start_iso, end_iso)
    opening_float = float(session.get("opening_float", 0) or 0)

    denoms = cash_register.normalize_denominations(
        [d.model_dump() for d in body.denominations]
    )
    counted = (
        round(float(body.counted_override), 2)
        if body.counted_override is not None
        else cash_register.total_from_denominations(denoms)
    )

    summary = cash_register.build_close_summary(
        opening_float=opening_float,
        cash_sales=cash_sales,
        cash_refunds=cash_refunds,
        cash_expenses=cash_expenses,
        bank_deposit=body.bank_deposit,
        denominations=denoms,
        tolerance=body.tolerance,
    )
    # build_close_summary uses the denoms total for counted; honour an override.
    summary["counted"] = counted
    summary["variance"] = cash_register.compute_variance(counted, summary["expected"])
    summary["variance_status"] = cash_register.variance_status(
        summary["variance"], body.tolerance
    )

    # E5 (ADDITIVE): by-mode reconciliation over the same session window. This
    # does NOT touch the CASH-only variance above (build_close_summary is
    # unchanged) -- it stores a per-tender breakdown alongside it so the close
    # screen / Tally JV can see UPI/CARD/etc. net. Fail-soft: any error leaves
    # the cash close exactly as before.
    by_mode_breakdown = None
    try:
        from ..services.tender_reconciliation import reconcile_window

        _recon = reconcile_window(db, store_id, start_iso, end_iso)
        by_mode_breakdown = _recon.get("by_mode")
    except Exception:  # noqa: BLE001
        by_mode_breakdown = None

    update = {
        "status": "CLOSED",
        "closed_at": end_iso,
        "closed_by": current_user.get("user_id"),
        "closed_by_name": current_user.get("name"),
        "closing_denominations": denoms,
        "cash_sales": cash_sales,
        "cash_refunds": cash_refunds,
        "cash_expenses": cash_expenses,
        "by_mode_breakdown": by_mode_breakdown,
        "bank_deposit": summary["bank_deposit"],
        "counted": counted,
        "expected": summary["expected"],
        "variance": summary["variance"],
        "variance_status": summary["variance_status"],
        "tolerance": summary["tolerance"],
        "closing_note": body.note,
    }
    try:
        coll.update_one({"session_id": body.session_id}, {"$set": update})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Could not close session: {exc}")

    merged = dict(session)
    merged.update(update)
    merged.pop("_id", None)
    return merged


@router.get("/cash-register/sessions")
async def list_cash_register_sessions(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="OPEN / CLOSED"),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """Cash register session history, store-scoped, newest first.

    Also returns the live `open_session` (if any) and an `expected_preview` for
    it so the close screen can show the running expected figure before a count
    is entered."""
    db = _get_db()
    if db is None:
        return {"sessions": [], "open_session": None, "expected_preview": None}

    scoped_store = validate_store_access(store_id or "", current_user)
    coll = db.get_collection(_CASH_SESSIONS)

    match: Dict = {}
    if scoped_store:
        match["store_id"] = scoped_store
    elif store_id:
        match["store_id"] = store_id
    if status:
        match["status"] = status.upper()

    sessions: List[dict] = []
    try:
        cursor = coll.find(match, {"_id": 0}).sort("opened_at", -1).limit(limit)
        sessions = list(cursor)
    except Exception:
        sessions = []

    # Surface the currently-open session + a running expected preview.
    open_session = None
    expected_preview = None
    try:
        open_match = {"status": "OPEN"}
        if scoped_store:
            open_match["store_id"] = scoped_store
        elif store_id:
            open_match["store_id"] = store_id
        open_session = coll.find_one(open_match, {"_id": 0})
    except Exception:
        open_session = None

    if open_session is not None:
        os_store = open_session.get("store_id")
        start_iso = open_session.get("opened_at") or _iso_now()
        cash_sales, cash_refunds = _cash_sales_for_window(db, os_store, start_iso, None)
        cash_expenses = _cash_expenses_for_window(db, os_store, start_iso, None)
        opening_float = float(open_session.get("opening_float", 0) or 0)
        expected = cash_register.compute_expected_cash(
            opening_float, cash_sales, cash_refunds, cash_expenses, 0.0
        )
        expected_preview = {
            "opening_float": round(opening_float, 2),
            "cash_sales": cash_sales,
            "cash_refunds": cash_refunds,
            "cash_expenses": cash_expenses,
            "bank_deposit": 0.0,
            "expected": expected,
        }

    return {
        "sessions": sessions,
        "open_session": open_session,
        "expected_preview": expected_preview,
    }


# ============================================================================
# GST e-invoice (IRN + signed QR) -- FIN-1
# ============================================================================
# DARK by default: returns {status: "SIMULATED"} until IMS_EINVOICE_ENABLED=1
# AND GSP credentials are present in the integrations collection. Owner-gated.
# Roles mirror the sibling finance routes: ACCOUNTANT, ADMIN, SUPERADMIN.

_EINVOICE_ROLES = ("ACCOUNTANT", "ADMIN", "SUPERADMIN")


@router.post("/einvoice/{order_id}")
async def trigger_einvoice(
    order_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Trigger IRN generation for a single order.

    Returns the einvoice result dict (status SIMULATED | GENERATED | SKIPPED |
    FAILED). DARK by default -- caller always gets a structured response; never
    a 500. Finance roles only: ACCOUNTANT / ADMIN / SUPERADMIN.
    """
    from api.routers.auth import require_roles
    from api.services.einvoice import generate_irn

    role = str(
        current_user.get("activeRole") or (current_user.get("roles") or [""])[0] or ""
    )
    if role not in _EINVOICE_ROLES:
        raise HTTPException(
            status_code=403, detail="Finance roles required for e-invoice"
        )

    db = _get_db()

    # Load the order / invoice doc for this id
    order = None
    for collection_name in ("orders", "invoices"):
        try:
            coll = db.get_collection(collection_name)
            doc = coll.find_one(
                {
                    "$or": [
                        {"id": order_id},
                        {"order_id": order_id},
                        {"invoice_id": order_id},
                    ]
                },
                {"_id": 0},
            )
            if doc:
                order = doc
                break
        except Exception:  # noqa: BLE001 -- db not available in test env
            pass

    if order is None:
        raise HTTPException(
            status_code=404, detail=f"Order/invoice {order_id!r} not found"
        )

    result = await generate_irn(db, order)
    return result


# ============================================================================
# FIND-5: Bank statement import + auto-reconciliation
# ============================================================================


# Supported CSV column aliases (case-insensitive) for each canonical field.
_BS_DATE_COLS = {
    "date",
    "txn date",
    "transaction date",
    "value date",
    "posting date",
    "value dt",
}
_BS_DESC_COLS = {"description", "narration", "particulars", "details", "remarks"}
_BS_DEBIT_COLS = {
    "debit",
    "withdrawal",
    "dr",
    "amount (dr)",
    "withdrawal amt.",
    "withdrawal amt",
    "debit amount",
}
_BS_CREDIT_COLS = {
    "credit",
    "deposit",
    "cr",
    "amount (cr)",
    "deposit amt.",
    "deposit amt",
    "credit amount",
}
_BS_AMOUNT_COLS = {"amount"}  # single column with sign or +/- indicator
_BS_BALANCE_COLS = {"balance", "closing balance", "running balance", "closing balance"}


def _parse_bank_csv(content: str) -> List[dict]:
    """Parse a bank statement CSV into a canonical list of transaction dicts.

    Handles common Indian bank statement CSV layouts:
      - Separate Debit/Credit columns (HDFC, ICICI, Axis, Kotak)
      - Single Amount column with +/- prefix (SBI, PNB)
      - Various date formats (DD/MM/YYYY, YYYY-MM-DD, DD-MMM-YYYY)

    Returns rows with keys: date (ISO), description, debit, credit, balance.
    Rows with no parseable amount are skipped.
    """

    def _norm(s: str) -> str:
        return s.strip().lower()

    def _parse_amount(s: str) -> float:
        if not s:
            return 0.0
        s = s.replace(",", "").strip()
        # Handle Dr/Cr suffix or prefix
        neg = s.endswith("(Dr)") or s.endswith("Dr") or s.endswith("DR")
        s = (
            s.replace("(Dr)", "")
            .replace("(Cr)", "")
            .replace("Dr", "")
            .replace("Cr", "")
            .replace("DR", "")
            .replace("CR", "")
            .strip()
        )
        try:
            val = float(s)
        except ValueError:
            return 0.0
        return -val if neg else val

    def _parse_date(s: str) -> Optional[str]:
        s = s.strip()
        for fmt in (
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%Y-%m-%d",
            "%d/%m/%y",
            "%d-%b-%Y",
            "%d %b %Y",
            "%Y/%m/%d",
        ):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    reader = csv.DictReader(io.StringIO(content))
    headers = {_norm(h): h for h in (reader.fieldnames or [])}

    date_col = next((headers[h] for h in headers if h in _BS_DATE_COLS), None)
    desc_col = next((headers[h] for h in headers if h in _BS_DESC_COLS), None)
    debit_col = next((headers[h] for h in headers if h in _BS_DEBIT_COLS), None)
    credit_col = next((headers[h] for h in headers if h in _BS_CREDIT_COLS), None)
    amount_col = next((headers[h] for h in headers if h in _BS_AMOUNT_COLS), None)
    balance_col = next((headers[h] for h in headers if h in _BS_BALANCE_COLS), None)

    rows = []
    for row in reader:
        date_str = _parse_date(row.get(date_col, "")) if date_col else None
        if not date_str:
            continue

        desc = (row.get(desc_col, "") or "").strip() if desc_col else ""

        if debit_col and credit_col:
            debit = abs(_parse_amount(row.get(debit_col, "")))
            credit = abs(_parse_amount(row.get(credit_col, "")))
        elif amount_col:
            amt = _parse_amount(row.get(amount_col, ""))
            debit = abs(amt) if amt < 0 else 0.0
            credit = amt if amt > 0 else 0.0
        else:
            continue

        balance_raw = _parse_amount(row.get(balance_col, "")) if balance_col else None

        rows.append(
            {
                "date": date_str,
                "description": desc,
                "debit": round(debit, 2),
                "credit": round(credit, 2),
                "balance": round(balance_raw, 2) if balance_raw is not None else None,
            }
        )

    return rows


def _auto_match_statement(
    statement_rows: List[dict],
    receipts: List[dict],
    payments: List[dict],
    *,
    amount_tolerance: float = 1.0,
    date_window_days: int = 3,
) -> List[dict]:
    """Auto-match statement rows against recorded receipts/payments.

    A match is accepted when:
      - The statement debit is within Rs 1 of a payment amount, OR
        the statement credit is within Rs 1 of a receipt amount,
      AND
      - The statement date is within 3 days of the recorded date.

    Returns an enriched list of statement rows with a `match` field
    (None or the matched record) and `match_type` ("RECEIPT", "PAYMENT",
    "UNMATCHED").
    """
    from ..services.ap_engine import _f, parse_date

    def _amt_close(a: float, b: float) -> bool:
        return abs(a - b) <= amount_tolerance

    def _dt_close(d1: Optional[str], d2: Optional[str]) -> bool:
        if not d1 or not d2:
            return False
        try:
            dd1 = datetime.fromisoformat(d1[:10])
            dd2 = datetime.fromisoformat(d2[:10])
            return abs((dd1 - dd2).days) <= date_window_days
        except (ValueError, TypeError):
            return False

    results = []
    used_receipt_ids: set = set()
    used_payment_ids: set = set()

    for row in statement_rows:
        matched = None
        match_type = "UNMATCHED"

        if row["credit"] > 0:
            # Credit in bank = money received = a receipt (order payment)
            for rec in receipts:
                rid = (
                    rec.get("receipt_id")
                    or rec.get("order_id")
                    or rec.get("_id")
                    or id(rec)
                )
                if rid in used_receipt_ids:
                    continue
                # Orders use grand_total; receipt docs use amount / total_amount.
                rec_amt = _f(
                    rec.get("grand_total")
                    or rec.get("amount")
                    or rec.get("total_amount")
                )
                rec_date = (
                    rec.get("receipt_date")
                    or rec.get("payment_date")
                    or rec.get("created_at")
                    or ""
                )[:10]
                if _amt_close(row["credit"], rec_amt) and _dt_close(
                    row["date"], rec_date
                ):
                    matched = {
                        "id": str(rid),
                        "type": "receipt",
                        "amount": rec_amt,
                        "date": rec_date,
                        "reference": rec.get("reference") or rec.get("order_id") or "",
                    }
                    used_receipt_ids.add(rid)
                    match_type = "RECEIPT"
                    break

        elif row["debit"] > 0:
            # Debit in bank = money paid = a payment
            for pmt in payments:
                pid = pmt.get("payment_id") or pmt.get("_id") or id(pmt)
                if pid in used_payment_ids:
                    continue
                pmt_amt = _f(pmt.get("amount") or pmt.get("total_amount"))
                pmt_date = (pmt.get("payment_date") or pmt.get("created_at") or "")[:10]
                if _amt_close(row["debit"], pmt_amt) and _dt_close(
                    row["date"], pmt_date
                ):
                    matched = {
                        "id": str(pid),
                        "type": "payment",
                        "amount": pmt_amt,
                        "date": pmt_date,
                        "reference": pmt.get("reference") or pmt.get("vendor_id") or "",
                    }
                    used_payment_ids.add(pid)
                    match_type = "PAYMENT"
                    break

        results.append({**row, "match": matched, "match_type": match_type})

    return results


@router.post("/bank-statement/import")
async def import_bank_statement(
    file: UploadFile = File(..., description="Bank statement CSV file"),
    store_id: Optional[str] = Form(None),
    account_name: Optional[str] = Form(None, description="Bank account name/label"),
    current_user: dict = Depends(get_current_user),
):
    """FIND-5: Import a bank statement CSV and auto-match against recorded
    receipts and vendor payments.

    Accepts common Indian bank CSV layouts (HDFC / ICICI / SBI / Axis /
    Kotak). Returns a statement_id and the matched/unmatched rows so the
    accountant can review and confirm each match.

    The import is non-destructive: no existing records are modified until
    the accountant calls POST /finance/bank-statement/{id}/confirm.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Only CSV files are accepted")

    content_bytes = await file.read()
    try:
        content = content_bytes.decode("utf-8-sig")  # strip BOM if present
    except UnicodeDecodeError:
        try:
            content = content_bytes.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=422,
                detail="Could not decode CSV file. Use UTF-8 or Latin-1 encoding.",
            )

    rows = _parse_bank_csv(content)
    if not rows:
        raise HTTPException(
            status_code=422,
            detail="No parseable transactions found in the CSV. Check column names: Date, Description, Debit/Credit (or Amount), Balance.",
        )

    db = _get_db()
    effective_store = store_id or current_user.get("active_store_id") or ""

    # Fetch recorded receipts and vendor payments for the date range in the statement
    dates = [r["date"] for r in rows if r.get("date")]
    if dates:
        min_date = min(dates)
        max_date = max(dates)
    else:
        min_date = max_date = ist_today().isoformat()

    receipts: List[dict] = []
    payments: List[dict] = []
    if db is not None:
        try:
            # Order receipts (payments against orders): look in orders where
            # payment_status is PAID, using created_at within the statement window.
            receipts = list(
                db.get_collection("orders").find(
                    {
                        "payment_status": {
                            "$in": ["PAID", "paid", "PARTIAL", "partial"]
                        },
                        "created_at": {
                            "$gte": min_date,
                            "$lte": max_date + "T23:59:59",
                        },
                        **({"store_id": effective_store} if effective_store else {}),
                    },
                    {
                        "order_id": 1,
                        "grand_total": 1,
                        "total_amount": 1,
                        "created_at": 1,
                        "_id": 0,
                    },
                )
            )
        except Exception:
            pass
        try:
            payments = list(
                db.get_collection("vendor_payments").find(
                    {
                        "payment_date": {"$gte": min_date, "$lte": max_date},
                        **({"store_id": effective_store} if effective_store else {}),
                    },
                    {
                        "payment_id": 1,
                        "amount": 1,
                        "payment_date": 1,
                        "vendor_id": 1,
                        "_id": 0,
                    },
                )
            )
        except Exception:
            pass

    matched_rows = _auto_match_statement(rows, receipts, payments)

    summary = {
        "total": len(matched_rows),
        "matched_receipts": sum(
            1 for r in matched_rows if r["match_type"] == "RECEIPT"
        ),
        "matched_payments": sum(
            1 for r in matched_rows if r["match_type"] == "PAYMENT"
        ),
        "unmatched": sum(1 for r in matched_rows if r["match_type"] == "UNMATCHED"),
        "total_credits": round(sum(r["credit"] for r in matched_rows), 2),
        "total_debits": round(sum(r["debit"] for r in matched_rows), 2),
    }

    # Persist the import for later confirmation (best-effort; fail-soft).
    statement_id = str(uuid.uuid4())
    if db is not None:
        try:
            db.get_collection("bank_statements").insert_one(
                {
                    "statement_id": statement_id,
                    "store_id": effective_store,
                    "account_name": account_name or "",
                    "filename": file.filename,
                    "uploaded_by": current_user.get("id")
                    or current_user.get("user_id")
                    or "",
                    "uploaded_at": datetime.utcnow().isoformat(),
                    "row_count": len(matched_rows),
                    "summary": summary,
                    "rows": matched_rows,
                    "status": "PENDING_REVIEW",
                }
            )
        except Exception:
            pass  # non-fatal; data returned in response regardless

    return {
        "statement_id": statement_id,
        "summary": summary,
        "rows": matched_rows,
    }


@router.get("/bank-statement")
async def list_bank_statements(
    store_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """FIND-5: List previously imported bank statements."""
    db = _get_db()
    if db is None:
        return {"statements": []}
    effective_store = store_id or current_user.get("active_store_id") or ""
    flt = {"store_id": effective_store} if effective_store else {}
    try:
        docs = list(
            db.get_collection("bank_statements").find(
                flt,
                {
                    "statement_id": 1,
                    "account_name": 1,
                    "filename": 1,
                    "uploaded_at": 1,
                    "row_count": 1,
                    "summary": 1,
                    "status": 1,
                    "_id": 0,
                },
                sort=[("uploaded_at", -1)],
                limit=limit,
            )
        )
    except Exception:
        docs = []
    return {"statements": docs}


@router.get("/bank-statement/{statement_id}")
async def get_bank_statement(
    statement_id: str,
    current_user: dict = Depends(get_current_user),
):
    """FIND-5: Retrieve an imported bank statement with all rows and matches."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        doc = db.get_collection("bank_statements").find_one(
            {"statement_id": statement_id}, {"_id": 0}
        )
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Statement not found")
    return doc


# === F34 Global Target Ticker ============================================
# A privacy-stratified live monthly-revenue-vs-target card on the Hub.
# Management roles see rupees + pace; floor roles see ONLY pct_complete.
# raw_visible is computed SERVER-SIDE from the JWT role (never trusted from the
# client). No REVENUE budget for the month -> no_target=true (never fabricated).

_TICKER_CACHE_PREFIX = "ticker:"


def _ticker_stores_for(
    db, store_id: Optional[str], current_user: dict
) -> List[Dict[str, str]]:
    """The list of {store_id, store_name} this caller may see on the ticker.

    - explicit store_id -> validate_store_access (403 on cross-store request).
    - HQ roles (SUPERADMIN/ADMIN/AREA_MANAGER) with no store_id -> all active
      stores (AREA_MANAGER limited to their own store_ids).
    - any other role -> their single active store.
    """
    name_by_id: Dict[str, str] = {}
    try:
        for s in db.get_collection("stores").find(
            {}, {"_id": 0, "store_id": 1, "name": 1, "is_active": 1}
        ):
            sid = s.get("store_id")
            if sid:
                name_by_id[sid] = s.get("name") or sid
    except Exception:  # noqa: BLE001
        name_by_id = {}

    def _entry(sid: str) -> Dict[str, str]:
        return {"store_id": sid, "store_name": name_by_id.get(sid, sid)}

    if store_id:
        sid = validate_store_access(store_id, current_user)
        return [_entry(sid)] if sid else []

    roles = set(current_user.get("roles") or [])
    if roles & {"SUPERADMIN", "ADMIN"}:
        # All active stores.
        active: List[str] = []
        try:
            for s in db.get_collection("stores").find(
                {"$or": [{"is_active": True}, {"is_active": {"$exists": False}}]},
                {"_id": 0, "store_id": 1},
            ):
                if s.get("store_id"):
                    active.append(s["store_id"])
        except Exception:  # noqa: BLE001
            active = list(name_by_id.keys())
        return [_entry(s) for s in (active or list(name_by_id.keys()))]
    if "AREA_MANAGER" in roles:
        return [_entry(s) for s in (current_user.get("store_ids") or [])]
    # Store-scoped role: their single active store.
    sid = current_user.get("active_store_id")
    return [_entry(sid)] if sid else []


@ticker_router.get("/target-ticker")
async def get_target_ticker(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Monthly-target ticker, privacy-stratified by JWT role (server-side).

    Management (SUPERADMIN/ADMIN/AREA_MANAGER/STORE_MANAGER/ACCOUNTANT) get
    raw_visible:true with mtd_revenue/monthly_target/pct_complete/pace; floor
    roles get raw_visible:false and pct_complete ONLY (rupee keys are ABSENT,
    never null). Fail-soft: DB down -> a single no_target store, HTTP 200."""
    raw_visible = ticker_service.raw_visible_for(current_user)
    refresh_seconds = int(
        policy_engine.get_policy(
            "ticker.refresh_seconds",
            scope={},
            default=ticker_service.DEFAULT_REFRESH_SECONDS,
        )
    )

    db = _get_db()
    if db is None:
        entry = ticker_service.compute_store_entry(
            store_id="",
            store_name="",
            monthly_target=None,
            mtd=0.0,
            days_elapsed=0,
            days_in_month=0,
            milestones_fired=[],
        )
        entry = entry if raw_visible else ticker_service.mask_entry(entry)
        return {
            "raw_visible": raw_visible,
            "stores": [entry],
            "ticker_refresh_seconds": refresh_seconds,
        }

    stores = _ticker_stores_for(db, store_id, current_user)
    period = ticker_service.current_period()
    _, days_elapsed, days_in_month = ticker_service._month_bounds()
    orders_coll = db.get_collection("orders")
    budgets_coll = db.get_collection("budgets")

    out_stores: List[Dict] = []
    for st in stores:
        sid = st["store_id"]
        # Cache the AGGREGATE (revenue + target) per store+period; masking happens
        # AFTER the cache read so raw + masked share one cached compute.
        ck = "%s%s:%s" % (_TICKER_CACHE_PREFIX, sid, period)
        cached = cache.get(ck)
        if cached is not None:
            mtd = float(cached.get("mtd_revenue") or 0.0)
            target = cached.get("monthly_target")
            milestones_fired = cached.get("milestones_fired") or []
        else:
            mtd = ticker_service.mtd_revenue(orders_coll, sid)
            target = None
            milestones_fired = []
            try:
                bdoc = (
                    budgets_coll.find_one(
                        {"store_id": sid, "period": period, "head": "REVENUE"}
                    )
                    if budgets_coll is not None
                    else None
                )
            except Exception:  # noqa: BLE001
                bdoc = None
            if bdoc:
                target = bdoc.get("planned_amount")
                milestones_fired = bdoc.get("milestones_fired") or []
            cache.set(
                ck,
                {
                    "mtd_revenue": mtd,
                    "monthly_target": target,
                    "milestones_fired": milestones_fired,
                },
                ttl=cache.TTL_SHORT,
            )

        entry = ticker_service.compute_store_entry(
            store_id=sid,
            store_name=st["store_name"],
            monthly_target=target,
            mtd=mtd,
            days_elapsed=days_elapsed,
            days_in_month=days_in_month,
            milestones_fired=milestones_fired,
        )
        out_stores.append(entry if raw_visible else ticker_service.mask_entry(entry))

    if not out_stores:
        # No store resolved (e.g. a role with no active store) -- greyed card.
        entry = ticker_service.compute_store_entry(
            store_id="",
            store_name="",
            monthly_target=None,
            mtd=0.0,
            days_elapsed=days_elapsed,
            days_in_month=days_in_month,
            milestones_fired=[],
        )
        out_stores = [entry if raw_visible else ticker_service.mask_entry(entry)]

    return {
        "raw_visible": raw_visible,
        "stores": out_stores,
        "ticker_refresh_seconds": refresh_seconds,
    }


class TickerSettingsBody(BaseModel):
    milestone_pcts: List[int] = Field(..., description="Milestone thresholds 1-100")
    refresh_seconds: int = Field(..., ge=30, le=300)


@router.post("/target-ticker/settings")
async def update_target_ticker_settings(
    body: TickerSettingsBody,
    current_user: dict = Depends(get_current_user),
):
    """Set the two E2 ticker keys (SUPERADMIN/ADMIN only) via the policy engine.
    Invalidates the per-store ticker cache so the next GET reflects the change."""
    roles = set(current_user.get("roles") or [])
    if not (roles & {"SUPERADMIN", "ADMIN"}):
        raise HTTPException(
            status_code=403, detail="Only SUPERADMIN/ADMIN may change ticker settings"
        )

    pcts = body.milestone_pcts or []
    if not pcts or any((not isinstance(p, int)) or p < 1 or p > 100 for p in pcts):
        raise HTTPException(
            status_code=400, detail="milestone_pcts must be integers in 1..100"
        )
    # de-dup + sort for a stable stored list
    pcts = sorted(set(int(p) for p in pcts))

    try:
        policy_engine.set_policy(
            "ticker.milestone_pcts", pcts, scope={}, actor=current_user
        )
        policy_engine.set_policy(
            "ticker.refresh_seconds",
            int(body.refresh_seconds),
            scope={},
            actor=current_user,
        )
    except policy_engine.PolicyError as exc:
        raise HTTPException(status_code=getattr(exc, "status", 400), detail=str(exc))

    # Invalidate cached aggregates (Redis: pattern; in-memory: best-effort per-store).
    try:
        cache.delete_pattern("%s*" % _TICKER_CACHE_PREFIX)
    except Exception:  # noqa: BLE001
        pass

    return {
        "milestone_pcts": pcts,
        "refresh_seconds": int(body.refresh_seconds),
        "saved": True,
    }


# ============================================================================
# F17/#25 - Maker-checker manual journal entries (gated by E4 ApprovalEngine)
# ============================================================================
# A maker (ACCOUNTANT/ADMIN/SUPERADMIN) drafts a balanced double-entry voucher,
# submits it (opens an E4 journal_entry approval request), a DIFFERENT-user
# checker (ADMIN/SUPERADMIN) PIN-approves via E4 (E4 hard-blocks self-approval),
# then posts it (consuming the E4 approval EXACTLY once) so it flows into the
# P&L read + nightly Tally journal-voucher export. The maker-checker / PIN /
# single-use logic is the shared E4 engine -- NOT reimplemented here.

# Roles allowed to draft + submit a JE (the maker side).
_JE_MAKER_ROLES = {"ACCOUNTANT", "ADMIN", "SUPERADMIN"}
# Roles allowed to approve / reject / post / reverse a JE (the checker side).
_JE_CHECKER_ROLES = {"ADMIN", "SUPERADMIN"}


def _je_require_enabled() -> None:
    """Feature flag gate for JE WRITE endpoints (off by default)."""
    if not je_service.is_je_enabled():
        raise HTTPException(status_code=503, detail="manual_je_not_enabled")


def _require_roles_for(current_user: dict, allowed: set, msg: str) -> None:
    roles = set(current_user.get("roles") or [])
    if not (roles & allowed):
        raise HTTPException(status_code=403, detail=msg)


def _je_cal_day(s, *, end: bool = False) -> Optional[datetime]:
    """Parse a 'YYYY-MM-DD' (or ISO) string as an ACCOUNTING CALENDAR day -> a
    naive datetime at that day's midnight (``end=True`` -> 23:59:59.999999).

    Unlike _parse_range_dt this does NOT shift through ist_day_start_utc(): a JE's
    entry_date IS the maker's intended calendar day, so its stored frame and the
    P&L window bounds must BOTH be calendar-day -- otherwise ``.date()/.month/
    .year`` (period-lock month, FY serial bucket, display, FY-guard) read the
    PRIOR IST day (IST-midnight maps to 18:30 UTC the day before). None when
    empty / unparseable."""
    if s is None:
        return None
    if isinstance(s, datetime):
        d = s.date()
    else:
        txt = str(s).strip()
        if not txt:
            return None
        try:
            d = datetime.fromisoformat(txt[:10]).date()
        except ValueError:
            return None
    dt = datetime(d.year, d.month, d.day)
    if end:
        return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


def _je_parse_entry_date(s, current_user: dict) -> datetime:
    """Parse the maker-supplied entry_date (YYYY-MM-DD / ISO) to the naive datetime
    at MIDNIGHT of the intended IST CALENDAR day. entry_date is an ACCOUNTING day,
    NOT a created_at instant -- it must NOT be routed through ist_day_start_utc()
    (which maps IST-midnight to 18:30 UTC the PRIOR day, mis-bucketing the
    period-lock month + the Rule-46(b) FY serial + the displayed date, and wrongly
    rejecting a legit 1-April entry at the FY-guard). Defaults to today (IST).
    Non-SUPERADMIN makers cannot back-date before the current financial-year start."""
    if not s:
        _t = ist_today()
        dt = datetime(_t.year, _t.month, _t.day)
    else:
        dt = _je_cal_day(s)
        if dt is None:
            raise HTTPException(status_code=400, detail="invalid_entry_date")
    roles = set(current_user.get("roles") or [])
    if "SUPERADMIN" not in roles:
        fy_year = fy_start_year_ist(now_ist())
        fy_start = datetime(fy_year, 4, 1)
        if dt < fy_start:
            raise HTTPException(
                status_code=400,
                detail="entry_date before current financial year (SUPERADMIN only)",
            )
    return dt


def _je_raise(res: dict) -> dict:
    """Map a je_service {"ok", "http", "error"} result to a response or raise."""
    if res.get("ok"):
        return res
    code = int(res.get("http", 400))
    detail: dict = {"error": res.get("error", "failed")}
    for k in ("status", "remaining", "retry_after_min"):
        if k in res:
            detail[k] = res[k]
    raise HTTPException(status_code=code, detail=detail)


class JeLineBody(BaseModel):
    account_code: str
    debit: float = Field(default=0, ge=0)
    credit: float = Field(default=0, ge=0)
    narration: Optional[str] = None


class JeCreateBody(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    lines: List[JeLineBody]
    store_id: Optional[str] = None
    entity_id: Optional[str] = None
    entry_date: Optional[str] = None
    reference: Optional[str] = None


class JePinBody(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6)


class JeRejectBody(BaseModel):
    pin: str = Field(..., min_length=4, max_length=6)
    note: str = Field(..., min_length=10, max_length=500)


class CoaUpsertBody(BaseModel):
    account_code: str = Field(..., min_length=1, max_length=20)
    account_name: str = Field(..., min_length=1, max_length=120)
    account_type: str
    allow_manual_je: bool = True
    is_active: bool = True


@router.post("/journal-entries")
async def create_journal_entry(
    body: JeCreateBody,
    current_user: dict = Depends(get_current_user),
):
    """Create a DRAFT journal voucher (maker). Validates a balanced
    debit=credit voucher against the chart of accounts, checks the period lock
    on entry_date, mints an FY-scoped JE number."""
    _je_require_enabled()
    _require_roles_for(
        current_user, _JE_MAKER_ROLES, "Journal entries require ACCOUNTANT / ADMIN"
    )
    db = _get_db()
    store_id = _scope_store(body.store_id, current_user)
    entry_date = _je_parse_entry_date(body.entry_date, current_user)
    # Gate 1: a closed period rejects a draft at creation.
    check_period_locked(db, entry_date.date())
    res = je_service.create_je(
        db,
        store_id=store_id,
        entity_id=body.entity_id,
        entry_date=entry_date,
        description=body.description,
        lines=[ln.model_dump() for ln in body.lines],
        maker_id=current_user.get("user_id"),
        maker_name=current_user.get("name") or current_user.get("full_name"),
        reference=body.reference,
    )
    return _je_raise(res)


@router.get("/journal-entries")
async def list_journal_entries(
    store_id: Optional[str] = None,
    status: Optional[str] = None,
    maker_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """List journal vouchers, store-scoped for store-level roles."""
    db = _get_db()
    store_id = _scope_store(store_id, current_user)
    rows = je_service.list_jes(
        db, store_id=store_id, status=status, maker_id=maker_id, limit=200
    )
    return {"journal_entries": rows, "total": len(rows)}


@router.get("/journal-entries/{je_id}")
async def get_journal_entry(
    je_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Fetch one journal voucher with its full line detail."""
    db = _get_db()
    je = je_service.get_je(db, je_id)
    if not je:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    # Store-scope read: a store-level role cannot read another store's JE.
    if je.get("store_id"):
        _scope_store(je.get("store_id"), current_user)
    else:
        # Store-LESS (HQ/entity-level) voucher: skipping the guard here let any
        # store-scoped finance role read HQ vouchers by id (adversarial P3).
        # HQ vouchers are HQ-readable only.
        roles = set(current_user.get("roles") or [])
        if not (roles & {"SUPERADMIN", "ADMIN"}):
            raise HTTPException(
                status_code=403, detail="HQ journal entries are not store-readable"
            )
    je.pop("_id", None)
    return je_service._jsonable(je)


@router.post("/journal-entries/{je_id}/submit")
async def submit_journal_entry(
    je_id: str,
    current_user: dict = Depends(get_current_user),
):
    """DRAFT -> SUBMITTED (maker only). Opens the E4 maker-checker approval."""
    _je_require_enabled()
    _require_roles_for(
        current_user, _JE_MAKER_ROLES, "Journal entries require ACCOUNTANT / ADMIN"
    )
    db = _get_db()
    res = je_service.submit_je(
        db,
        je_id=je_id,
        maker_id=current_user.get("user_id"),
        maker_roles=list(current_user.get("roles") or []),
        maker_store_ids=list(current_user.get("store_ids") or []),
    )
    return _je_raise(res)


@router.post("/journal-entries/{je_id}/approve")
async def approve_journal_entry(
    je_id: str,
    body: JePinBody,
    current_user: dict = Depends(get_current_user),
):
    """SUBMITTED -> APPROVED (checker, PIN-gated, via E4). The maker cannot
    approve their own entry -- E4 enforces approver != maker."""
    _je_require_enabled()
    _require_roles_for(
        current_user,
        _JE_CHECKER_ROLES,
        "Only ADMIN / SUPERADMIN may approve a journal entry",
    )
    db = _get_db()
    res = je_service.approve_je(
        db,
        je_id=je_id,
        approver_id=current_user.get("user_id"),
        approver_roles=list(current_user.get("roles") or []),
        pin=body.pin,
        approver_store_ids=list(current_user.get("store_ids") or []),
    )
    return _je_raise(res)


@router.post("/journal-entries/{je_id}/reject")
async def reject_journal_entry(
    je_id: str,
    body: JeRejectBody,
    current_user: dict = Depends(get_current_user),
):
    """SUBMITTED -> REJECTED with a mandatory note (checker, PIN-gated, via E4)."""
    _je_require_enabled()
    _require_roles_for(
        current_user,
        _JE_CHECKER_ROLES,
        "Only ADMIN / SUPERADMIN may reject a journal entry",
    )
    db = _get_db()
    res = je_service.reject_je(
        db,
        je_id=je_id,
        approver_id=current_user.get("user_id"),
        approver_roles=list(current_user.get("roles") or []),
        pin=body.pin,
        note=body.note,
        approver_store_ids=list(current_user.get("store_ids") or []),
    )
    return _je_raise(res)


@router.post("/journal-entries/{je_id}/post")
async def post_journal_entry(
    je_id: str,
    current_user: dict = Depends(get_current_user),
):
    """APPROVED -> POSTED (checker). Consumes the E4 approval EXACTLY once, then
    re-checks the period lock (double gate) before the JE hits the ledger."""
    _je_require_enabled()
    _require_roles_for(
        current_user,
        _JE_CHECKER_ROLES,
        "Only ADMIN / SUPERADMIN may post a journal entry",
    )
    db = _get_db()
    je = je_service.get_je(db, je_id)
    if not je:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    # Gate 2 (double period-lock): a period locked after approval blocks posting.
    entry_date = je.get("entry_date")
    if isinstance(entry_date, datetime):
        check_period_locked(db, entry_date.date())
    res = je_service.post_je(db, je_id=je_id, poster_id=current_user.get("user_id"))
    return _je_raise(res)


@router.post("/journal-entries/{je_id}/reverse")
async def reverse_journal_entry(
    je_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Reverse a POSTED JE (ADMIN/SUPERADMIN). Mints a mirror voucher dated
    today (today's period must be open); both vouchers are linked."""
    _je_require_enabled()
    _require_roles_for(
        current_user,
        _JE_CHECKER_ROLES,
        "Only ADMIN / SUPERADMIN may reverse a journal entry",
    )
    db = _get_db()
    # The reversal posts on today's date -- today's period must be open.
    check_period_locked(db, now_ist_naive().date())
    res = je_service.reverse_je(
        db,
        je_id=je_id,
        actor_id=current_user.get("user_id"),
        actor_name=current_user.get("name") or current_user.get("full_name"),
    )
    return _je_raise(res)


@router.get("/chart-of-accounts")
async def get_chart_of_accounts(
    manual_only: bool = False,
    current_user: dict = Depends(get_current_user),
):
    """Active chart of accounts. ``manual_only=true`` returns only accounts the
    JE line picker may use (allow_manual_je=True)."""
    db = _get_db()
    return {"accounts": je_service.list_accounts(db, manual_only=manual_only)}


@router.post("/chart-of-accounts")
async def upsert_chart_of_account(
    body: CoaUpsertBody,
    current_user: dict = Depends(get_current_user),
):
    """Upsert a chart-of-accounts entry (SUPERADMIN only)."""
    roles = set(current_user.get("roles") or [])
    if "SUPERADMIN" not in roles:
        raise HTTPException(
            status_code=403, detail="Only SUPERADMIN may edit the chart of accounts"
        )
    db = _get_db()
    res = je_service.upsert_account(
        db,
        account_code=body.account_code,
        account_name=body.account_name,
        account_type=body.account_type,
        allow_manual_je=body.allow_manual_je,
        is_active=body.is_active,
    )
    if not res.get("ok"):
        err = res.get("error")
        code = 503 if err == "no_db" else 400
        raise HTTPException(status_code=code, detail=err or "upsert_failed")
    return res


@router.get("/tally/journal-jv")
async def get_tally_journal_jv(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    store_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """POSTED journal vouchers as Tally ``<JOURNALVOUCHER>`` import XML. DRAFT /
    SUBMITTED / APPROVED JEs are never exported."""
    db = _get_db()
    store_id = _scope_store(store_id, current_user)
    from_dt = _parse_range_dt(from_date)
    to_dt = _parse_range_dt(to_date, end=True)
    rows = je_service.list_jes(
        db, store_id=store_id, status=je_service.STATUS_POSTED, limit=1000
    )
    # Date-filter in Python (entry_date is a datetime); keeps the service query simple.
    filtered = []
    for je in rows:
        ed = je.get("entry_date")
        if isinstance(ed, str):
            ed = _parse_range_dt(ed)
        if from_dt is not None and ed is not None and ed < from_dt:
            continue
        if to_dt is not None and ed is not None and ed > to_dt:
            continue
        filtered.append(je)
    xml = je_service.build_journal_voucher_xml(filtered)
    fname = f"journal_jv_{(from_date or 'all')[:10]}_{(to_date or 'all')[:10]}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
