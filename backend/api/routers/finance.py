# Finance & Accounting Router — _get_db() pattern (matches working routers)

import calendar
import csv
import io
import logging
import uuid
from datetime import datetime, timedelta, date, timezone
from ..utils.ist import (
    now_ist,
    now_ist_naive,
    ist_date_str,
    ist_today,
    ist_day_start_utc,
    fy_start_year_ist,
)
from ..utils.online_gst import order_interstate_flag
from typing import Any, Optional, List, Dict, NamedTuple
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, Body
from fastapi.responses import Response
from pydantic import BaseModel, Field
from .auth import get_current_user
from ..dependencies import validate_store_access
from ..services import ap_engine, cashflow, itc_reconcile, cash_register, csv_safe
from ..services.stores_util import is_online_store
from ..services import survival_cashflow
from ..services.cost_mask import can_see_cost
from ..services.salary_visibility import (
    SALARY_RESTRICTED_MESSAGE,
    is_payroll_shaped_expense,
    is_salary_admin,
    normalise_expense_category,
)
from ..services.cache import cache
from ..services import ticker_service, policy_engine
from ..services import je_service
from ..services import cash_denominations as cash_denom
from ..services import eod_tally as till_service
from ..services import name_resolver

# Mounted at /api/v1/finance in main.py. NO internal prefix: the earlier
# prefix="/finance" double-prefixed every path to /api/v1/finance/finance/*,
# which the frontend financeApi (it calls /finance/*) never hit — so the whole
# Finance dashboard 404'd. Dropping it aligns the routes with the client.
logger = logging.getLogger(__name__)

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
# HISTORICAL = a pre-IMS order imported for customer-360 history only
# (scripts/migrate_bvi_pim.py orders leg): transacted + settled OUTSIDE IMS
# books, never invoiced by IMS -- so it is NOT revenue/tax/GST either.
_EXCLUDED_ORDER_STATUSES = [
    "CANCELLED", "DRAFT", "cancelled", "draft", "HISTORICAL", "historical",
]
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
        # OS-008: the order's own interstate flag wins (online orders carry it);
        # store-vs-customer state stays the fallback for docs without it.
        is_inter_state = _order_is_interstate(
            o, store_state_by_id, customer_state_by_id
        )
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


def _order_is_interstate(
    order, store_state_by_id: dict, customer_state_by_id: dict
) -> bool:
    """Inter-state (IGST) vs intra-state (CGST+SGST) classification for ONE order.

    Prefer the order doc's OWN persisted ``interstate`` flag (OS-008): online
    (Shopify) orders stamp it at ingest from the buyer's DELIVERY address via
    the same _build_invoice_gst_split the POS invoice uses -- while their buyer
    customer records are minted stateless, so recomputing the split from
    customers.state misfiled every inter-state online sale as CGST/SGST even
    though the minted invoice (place of supply) said IGST. The store-state vs
    customer-state heuristic stays as the FALLBACK for docs that don't carry
    the flag (POS orders don't persist it). Requires callers that project
    fields to include ``interstate`` in the projection.

    The OS-008 flag-preference gate is the shared ``order_interstate_flag``
    helper (utils.online_gst); the store-vs-customer state fallback below is
    finance-specific (GST-code normalization via ``_norm_state``) and stays
    byte-identical to this file's prior rule -- reports.py keeps its own raw
    string-compare fallback deliberately."""
    flag = order_interstate_flag(order)
    if flag is not None:
        return flag
    seller = _norm_state(store_state_by_id.get(order.get("store_id")))
    buyer = _norm_state(customer_state_by_id.get(order.get("customer_id")))
    return bool(seller and buyer and seller != buyer)


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
        # OS-008: the order's own interstate flag wins (online orders carry it);
        # store-vs-customer state stays the fallback for docs without it.
        if _order_is_interstate(o, store_state_by_id, customer_state_by_id):
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


def _store_gstin_map(db) -> dict:
    """store_id -> GSTIN. Used to dedupe transfer-borne ITC once per GSTIN in the
    GST cross-check aggregator (R1): a same-entity cross-state stock transfer is
    claimed by the RECEIVING GSTIN only, so sibling stores of one entity with
    different GSTINs return different transfer ITC."""
    out: dict = {}
    try:
        for s in db.get_collection("stores").find(
            {}, {"_id": 0, "store_id": 1, "gstin": 1}
        ):
            if s.get("store_id"):
                out[s["store_id"]] = str(s.get("gstin") or "").strip()
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
        # BUG-104: derive the previous month in the CALENDAR frame, then
        # convert. `start` is a SHIFTED naive-UTC instant (18:30 on the last
        # day of the prior month), so calendar arithmetic on it -- the old
        # `(start - 1 day).replace(day=1)` -- landed on the 1st AT 18:30 UTC,
        # i.e. IST midnight of the 2nd: the whole first IST day of the
        # previous month fell out of the MoM denominator, every month.
        prev_first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        prev_start = ist_day_start_utc(prev_first)
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


# The /pnl response is masked by TWO independent gates, because it mixes two
# different secrets and they do not belong to the same people.
#
# 1. COST_ONLY_PNL_FIELDS -- supplier landing prices and the margins derived from
#    them. Commercially sensitive; gate is services/cost_mask.can_see_cost, which
#    deliberately includes ACCOUNTANT (the books need COGS). UNCHANGED.
COST_ONLY_PNL_FIELDS = (
    "cogs",
    "cogs_is_estimated",
    "cogs_estimated_lines",
    "cogs_total_lines",
    "gross_profit",
    "gross_margin",
)

# 2. PAYROLL_DERIVED_PNL_FIELDS -- the wage bill and every figure it can be
#    subtracted OUT of. Gate is services/salary_visibility.is_salary_admin
#    (ADMIN / SUPERADMIN), per the owner ruling of 2026-08-09.
#
#    net_profit and net_margin are here even though neither is "a salary": with
#    net_profit = gross_profit - total_expenses - payroll_cost + je_revenue_adj,
#    a reader holding the other four recovers payroll_cost with one subtraction.
#    That is how the ACCOUNTANT would still have had the wage bill after
#    payroll_cost alone was removed -- they can see gross_profit and
#    total_expenses. Hiding a number while leaving its addends beside it is not
#    hiding it. In a 1-5 person store the wage bill IS an individual's pay.
#
#    NOT stripped, deliberately: total_expenses, the `expenses` category dict and
#    je_expense_adjustment. All three are payroll-EXCLUSIVE (payroll is a
#    separate line; the payroll run writes the `payroll` collection, never
#    `expenses`), and with the six fields above removed there is no longer any
#    payroll-INCLUSIVE figure left in the body for them to be subtracted from.
#    Dropping them would blank the store manager's operating-cost panel and buy
#    nothing. If a payroll-inclusive figure is ever ADDED to this response, it
#    belongs in this tuple on the same day.
PAYROLL_DERIVED_PNL_FIELDS = (
    "payroll_cost",
    "net_profit",
    "net_margin",
)

# 3. The one hole in "the `expenses` dict is payroll-EXCLUSIVE" above: `category`
#    is FREE TEXT typed by whoever books the expense, so somebody can book the
#    wage bill as an ordinary expense and hand it to a store manager by name.
#    The deny-set that catches the heads a person actually reaches for, what it
#    covers and what it cannot, now lives in services/salary_visibility.py
#    beside the role tuple -- it was found forked-by-omission onto
#    routers/budgets.py within a round of being written here. Imported above as
#    ``is_payroll_shaped_expense``; the two private aliases below keep this
#    module's existing call sites (and their tests) reading naturally.
_normalise_expense_category = normalise_expense_category
_is_payroll_shaped_expense = is_payroll_shaped_expense


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
        for _f in COST_ONLY_PNL_FIELDS:
            pnl.pop(_f, None)
    # SEPARATE GATE, SEPARATE RULE (owner ruling 2026-08-09). The payroll figures
    # used to ride on can_see_cost, which admits ACCOUNTANT -- the same accountant
    # /payroll/registers/summary already 403s. Cost is a commercial secret; pay is
    # somebody's pay packet, and the owner declined the accountant carve-out. So
    # the wage bill answers to is_salary_admin, never to the cost gate.
    if not is_salary_admin(current_user):
        for _f in PAYROLL_DERIVED_PNL_FIELDS:
            pnl.pop(_f, None)
        # Same gate, same reason: an expense head somebody TYPED as "Salary" is
        # pay, whatever collection it landed in. Remove the head AND its
        # contribution to total_expenses.
        #
        # WHY NOT BUCKET IT INTO "Other", which is the obvious move: renaming a
        # head does not hide the amount. An "Other" bucket built from the
        # payroll-shaped heads alone IS the wage bill under a new label, and even
        # if it were merged into a real "Other" head, the reader subtracts the
        # named heads from total_expenses and has it back. That is precisely the
        # aggregate-of-one arithmetic PR #984 exists to close, so this drops the
        # figure out of the body entirely instead.
        #
        # sum(expenses.values()) + je_expense_adjustment == total_expenses still
        # holds afterwards, so the manager's operating-cost panel stays
        # internally consistent -- it is simply a smaller, pay-free panel.
        _restricted = {
            head: amount
            for head, amount in (pnl.get("expenses") or {}).items()
            if _is_payroll_shaped_expense(head)
        }
        if _restricted:
            pnl["expenses"] = {
                head: amount
                for head, amount in pnl["expenses"].items()
                if head not in _restricted
            }
            pnl["total_expenses"] = round(
                float(pnl.get("total_expenses") or 0.0)
                - sum(float(v or 0.0) for v in _restricted.values()),
                2,
            )
            # Tell the reader their panel is incomplete rather than letting a
            # short total read as the truth. A flag, never a figure.
            pnl["expenses_partially_restricted"] = True
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
                # OS-008: the order-carried inter-state flag (online orders).
                "interstate": 1,
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
    # Grouped BY HEAD, not into a single grand total, purely so the payroll
    # strip below can subtract the pay heads out. Nothing downstream sees the
    # heads -- this response has never carried them and still does not.
    exp_out = list(
        db.get_collection("expenses").aggregate(
            [
                {"$match": exp_match},
                {"$group": {"_id": "$category", "total": {"$sum": "$amount"}}},
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

    expense_outflow = round(sum(float(r.get("total") or 0.0) for r in exp_out), 2)
    purchase_outflow = po_out[0]["total"] if po_out else 0

    # THE CROSS-ROUTE SUBTRACTION, and the reason this strip exists at all.
    #
    # /pnl is payroll-EXCLUSIVE below salary-admin. This route totals THE SAME
    # expenses collection over THE SAME store (validate_store_access above) for
    # THE SAME window (1st of the current month) -- a window /pnl will happily
    # be asked for. So a store manager who holds both responses does:
    #
    #     cash-flow.expense_outflow - pnl.total_expenses = the wage bill
    #
    # measured live at 88360.65 - 24450.00 = 63910.65 before this change. The
    # earlier sibling sweep cleared this route because its body carries no head
    # names. The head name was never the leak: two figures over the same scope
    # that differ only by payroll ARE the payroll. See the second corollary in
    # services/salary_visibility.py.
    #
    # ALL THREE FIGURES MOVE TOGETHER. `outflows` and `net_cash_flow` are built
    # FROM expense_outflow, so reducing expense_outflow alone would hand the pay
    # straight back as
    #     outflows - expense_outflow - purchase_outflow - vendor_payment_outflow
    # Reducing the variable at source, BEFORE total_outflow is summed, is what
    # keeps the whole body internally consistent: the four numbers still add up,
    # they simply add up to a smaller, pay-free month. `inflows` is order
    # revenue and shares no term with any of them.
    expenses_partially_restricted = False
    if not is_salary_admin(current_user):
        _restricted = round(
            sum(
                float(r.get("total") or 0.0)
                for r in exp_out
                if is_payroll_shaped_expense(r.get("_id"))
            ),
            2,
        )
        if _restricted:
            expense_outflow = round(expense_outflow - _restricted, 2)
            expenses_partially_restricted = True

    total_outflow = round(
        expense_outflow + purchase_outflow + vendor_payment_outflow, 2
    )

    body = {
        "period": period,
        "inflows": total_inflow,
        "outflows": total_outflow,
        "net_cash_flow": round(total_inflow - total_outflow, 2),
        "expense_outflow": expense_outflow,
        "purchase_outflow": purchase_outflow,
        "vendor_payment_outflow": vendor_payment_outflow,
    }
    if expenses_partially_restricted:
        # Same flag /pnl sets, and for the same reason: a short total must not
        # read as the truth. A flag, never a figure.
        body["expenses_partially_restricted"] = True
    return body


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
    # PAYROLL-INCLUSIVE, DELIBERATELY -- INSIDE THE 2026-08-14 EXCEPTION.
    #
    # This sum has no category filter, so it carries any pay booked as an
    # ordinary expense, and it is surfaced raw as `this_month.expenses` and
    # folded into `this_month.net_cash_flow`. It is NOT stripped, and the reason
    # is the ROLE SET, not the shape of the figure:
    #
    #   the gate is _require_finance_admin = SUPERADMIN / ADMIN / ACCOUNTANT,
    #   and the rbac_policy row is the same three. Take the salary admins out
    #   and exactly ONE role remains: ACCOUNTANT -- the role the owner ruled on
    #   2026-08-14 may read the pay heads BY NAME AND BY AMOUNT on
    #   /finance/survival-cashflow. STORE_MANAGER and AREA_MANAGER, the roles
    #   the /pnl and /cash-flow strips genuinely protect, cannot reach this
    #   route at all, at either layer.
    #
    # So stripping here would withhold from the accountant, blended, a figure
    # the owner has just decided they may read unblended one screen over. That
    # is theatre, and it would cost the owner an accurate month-to-date cash
    # position on his own dashboard.
    #
    # IF THIS GATE EVER WIDENS BELOW ACCOUNTANT this comment is void and the
    # strip must be added the same day -- see get_cash_flow for the shape.
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

    # PAYROLL-INCLUSIVE, DELIBERATELY, AND HERE IS WHY (reviewed 2026-08-14).
    #
    # `monthly_expense_est` is an all-heads expense total and it is surfaced in
    # `assumptions.monthly_expense_estimate`, so it carries any pay booked as an
    # expense. It is NOT stripped, and the reason is the ROLE SET, not the
    # blending:
    #
    #   this route's gate is _require_finance_admin = SUPERADMIN / ADMIN /
    #   ACCOUNTANT, and the rbac_policy row is the same three. Take the salary
    #   admins out and exactly ONE role remains: ACCOUNTANT -- the role the owner
    #   ruled on 2026-08-14 may read the pay heads BY NAME AND BY AMOUNT on
    #   /finance/survival-cashflow, which sits behind this identical gate.
    #
    # So stripping here would withhold from the accountant, in blended form, a
    # figure the owner has just decided they may read unblended one endpoint
    # over. It buys no secrecy from anybody and costs the accountant an accurate
    # runway. STORE_MANAGER and AREA_MANAGER -- the roles the /pnl and
    # /cash-flow strips genuinely protect against -- cannot reach this route at
    # all, at either layer.
    #
    # WHAT AN ATTACKER WOULD HAVE TO KNOW TO UNBLEND IT, stated properly rather
    # than hiding behind "it is blended": the figure is sum(all approved/paid
    # expenses, ALL stores, trailing 90 days) / 3. To pull one month's wage bill
    # out of it they would need the 90-day non-pay expense total across every
    # store (this response does not carry it, and /pnl is per-store and
    # caller-scoped) AND the other two months' pay. An accountant closing the
    # books has both from the ledger anyway, which is the point above.
    #
    # IF THIS GATE EVER WIDENS BELOW ACCOUNTANT, this comment is void and the
    # strip must be added the same day -- see get_cash_flow for the shape.
    #
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

    *** OWNER RULING 2026-08-14: THIS ROUTE STAYS OPEN TO THE ACCOUNTANT, AND
        THAT IS A DELIBERATE EXCEPTION TO HIS OWN 2026-08-09 SALARY RULING.
        DO NOT "TIDY IT UP". ***

    services/survival_cashflow.ESSENTIAL_DEFAULT_HEADS literally lists salary,
    salaries, payroll, pf and esi, and `survival.essential_detail` returns those
    heads BY NAME with their amounts. The route is store-narrowable via
    ?store_id=, and the business runs stores of 1-5 people, so for an ACCOUNTANT
    -- who is NOT a salary admin -- this is a named, per-store wage bill.

    The owner was shown that exact consequence and left it open anyway. His
    reasoning, recorded because it is sound: knowing what pay is due IS the
    point of a survival-cash view. An accountant who cannot see the largest
    committed outgoing of the month cannot answer "can we make payroll this
    month", which is the only question this screen exists to answer.

    THE COST, stated plainly so nobody has to rediscover it: after PR #985 the
    ACCOUNTANT cannot see the wage bill on /finance/pnl or /finance/cash-flow,
    but CAN read it by name here. Closing /finance/cash-flow for the ACCOUNTANT
    is therefore belt-and-braces rather than a seal; the roles those strips
    genuinely protect against are STORE_MANAGER and AREA_MANAGER, who cannot
    reach this route at all. That inconsistency is the owner's to resolve, not a
    fresh audit finding -- test_salary_aggregate_leak.py PINS this behaviour so
    the next well-meaning security pass cannot silently delete the accountant's
    cash-survival tool.
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
    # Scope like the GSTR-3B Table-4 ITC read (reports._itc_from_vendor_bills,
    # #899): (1) when entity_id is given, count only THAT entity's booked bills
    # via recipient_entity_id -- the same field a transfer mirror bill records
    # for its RECEIVING entity, so an inter-entity/same-entity-cross-state
    # transfer's ITC lands with the receiver and never inflates the sender's
    # register; (2) always drop ITC-ineligible bills (cancelled / 17(5)-blocked
    # / not-yet-received) via _itc_eligible_bill -- the register previously
    # summed EVERY vendor_bills doc, so cancelled + ineligible tax showed as
    # claimable ITC. The projection widens to carry the eligibility + scoping
    # fields; the output shape is unchanged.
    q: dict = {}
    if entity_id:
        q["recipient_entity_id"] = entity_id
    try:
        raw_bills = list(
            db.get_collection("vendor_bills").find(
                q,
                {
                    "_id": 0,
                    "bill_date": 1,
                    "taxable_amount": 1,
                    "tax_amount": 1,
                    "place_of_supply": 1,
                    "status": 1,
                    "itc_blocked": 1,
                    "itc_eligible": 1,
                    "received": 1,
                },
            )
        )
    except Exception:
        raw_bills = []
    bills = [b for b in raw_bills if _itc_eligible_bill(b)]
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

    # THE TWIN OF THE /pnl EXPENSE STRIP ABOVE, and the reason it is here rather
    # than in a later ticket: the Budgets tab of FinanceDashboard renders this
    # response beside the P&L tab that /pnl feeds. Closing "Salary" on one tab
    # while the tab next to it shows the same rupees under `categories.salaries`
    # would be the rule applied in one place and not its twin -- the failure this
    # codebase keeps repeating.
    #
    # BOTH numbers go, not just the actual: `budget` is the PLANNED wage bill for
    # the month, which in a 1-5 person store is an individual's pay to within a
    # rounding, and `actual` is the same expense figure /pnl now withholds.
    #
    # THE FLAG IS GATED ON MONEY, NOT ON THE POP. Dropping the head always
    # happens; CLAIMING something was withheld only happens when the head
    # actually carried a figure. The no-budget skeleton above (:2374) hard-codes
    # `"salaries": {"budget": 0, "actual": 0}`, and no-budget-set is the live
    # state today -- so flagging on the pop alone lit the amber "this is not the
    # full operating cost" notice permanently for every store and area manager,
    # while nothing of value had been withheld. A warning that is always on is
    # exactly as useless as one that never appears: it gets tuned out, and then
    # it is not believed on the day a real wage bill IS withheld.
    if not is_salary_admin(current_user):
        _cats = budget.get("categories") or {}
        _dropped = [c for c in list(_cats) if _is_payroll_shaped_expense(c)]
        _withheld_money = any(
            float((_cats.get(_c) or {}).get("budget") or 0) != 0
            or float((_cats.get(_c) or {}).get("actual") or 0) != 0
            for _c in _dropped
        )
        for _c in _dropped:
            _cats.pop(_c, None)
        if _withheld_money:
            budget["categories_partially_restricted"] = True
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
                # OS-008: the order-carried inter-state flag (online orders).
                "interstate": 1,
            },
        )
    )

    # purchase_orders persist created_at as a BSON datetime (BaseRepository
    # _add_timestamps). No writer ever set a top-level `date` field, so the old
    # {"date": <string>} filter matched NOTHING and input_credit read a permanent
    # 0. Use the SAME IST created_at window as the orders side, and map a PO to an
    # entity via delivery_store_id OR store_id (gst_reconciliation's own mapping).
    # HR-3: exclude DRAFT / CANCELLED POs so a human draft or cancelled PO with
    # tax_amount does not inflate input_credit (parity with the cross-check leg).
    p_match = {
        "created_at": {"$gte": start, "$lt": end},
        "status": {"$nin": ["DRAFT", "draft", "CANCELLED", "cancelled"]},
    }
    if store_ids is not None:
        p_match["$or"] = [
            {"delivery_store_id": {"$in": store_ids}},
            {"store_id": {"$in": store_ids}},
        ]
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


# ---------------------------------------------------------------------------
# GST cross-check (accountant month-end sign-off)
# ---------------------------------------------------------------------------
# The accountant's reconciliation aid: for a chosen (month, entity) it lays the
# IMS GSTR-1 / GSTR-3B numbers SIDE BY SIDE against the books (orders, payments,
# Tally sales-JV totals, purchase-side ITC) so they can confirm everything
# agrees before filing, then record a CHECKED sign-off with notes. The tax math
# is entirely REUSED (reports._compute_gstr1 / _compute_gstr3b and the finance
# order aggregations); this layer only compares + audits. It does NOT lock the
# period -- that is the separate /period-lock action.

_GST_CROSSCHECK_SIGNOFFS = "gst_crosscheck_signoffs"


class GstCrossCheckSignoff(BaseModel):
    month: int = Field(..., ge=1, le=12)
    year: int = Field(..., ge=2000, le=2100)
    entity_id: Optional[str] = None
    note: Optional[str] = None
    # What the CLIENT claims it saw. The server RECOMPUTES the cross-check at
    # sign-off and records its OWN mismatch_count / gst_payable as the
    # authoritative audit snapshot; these client values are kept only for drift
    # forensics and must never be trusted as the record of what was signed off.
    mismatch_count: Optional[int] = None
    gst_payable: Optional[float] = None


def _gst_month_window(y: int, m: int):
    """IST-calendar-month -> naive-UTC created_at bounds [start, end). Same
    framing as /gst/summary and /gst/reconciliation so every GST view agrees
    on which invoices belong to the period."""
    start = ist_day_start_utc(date(y, m, 1))
    end = ist_day_start_utc(date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1))
    return start, end


def _books_and_tally_for_stores(db, store_ids, start, end) -> tuple:
    """Books + Tally-JV totals for a store set over a created_at window.

    Returns (books, tally). books = {sales_taxable, sales_tax,
    sales_grand_total, payments_collected}; tally = {taxable, tax, cgst, sgst,
    igst}. Reuses the SAME field expressions and intra/inter-state split as the
    finance /gst/summary and /tally/sales-jv endpoints -- no new tax math."""
    o_match = {
        "created_at": {"$gte": start, "$lt": end},
        "status": _REAL_ORDER_STATUS_FILTER,
    }
    if store_ids is not None:
        o_match["store_id"] = {"$in": store_ids}

    store_states = _store_state_map(db)
    customer_states = _customer_state_map(db)

    sales_grand = 0.0
    sales_tax = 0.0
    payments_collected = 0.0
    t_cgst = t_sgst = t_igst = 0.0

    cursor = db.get_collection("orders").find(
        o_match,
        {
            "_id": 0,
            "store_id": 1,
            "customer_id": 1,
            "grand_total": 1,
            "total": 1,
            "tax_amount": 1,
            "tax_total": 1,
            "payments": 1,
            # OS-008: the order-carried inter-state flag (online orders).
            "interstate": 1,
        },
    )
    for o in cursor:
        grand = float(o.get("grand_total") or o.get("total") or 0)
        tax = float(o.get("tax_amount") or o.get("tax_total") or 0)
        sales_grand += grand
        sales_tax += tax
        # OS-008: the order's own interstate flag wins (online orders carry it);
        # store-vs-customer state stays the fallback for docs without it.
        if _order_is_interstate(o, store_states, customer_states):
            t_igst += tax
        else:
            cgst, sgst = _jv_cgst_sgst_split(tax)
            t_cgst += cgst
            t_sgst += sgst
        for p in o.get("payments") or []:
            try:
                payments_collected += float(p.get("amount", 0) or 0)
            except (TypeError, ValueError):
                pass

    books = {
        "sales_grand_total": round(sales_grand, 2),
        "sales_tax": round(sales_tax, 2),
        "sales_taxable": round(sales_grand - sales_tax, 2),
        "payments_collected": round(payments_collected, 2),
    }
    tally = {
        "taxable": round(sales_grand - sales_tax, 2),
        "tax": round(sales_tax, 2),
        "cgst": round(t_cgst, 2),
        "sgst": round(t_sgst, 2),
        "igst": round(t_igst, 2),
    }
    return books, tally


def _run_gst_cross_check(db, m: int, y: int, entity_id: Optional[str]) -> dict:
    """Compute the full GST cross-check payload for (month, year, entity).

    Shared by the GET (display) and the POST sign-off, which RECOMPUTES
    server-side so the durable audit snapshot is the SERVER's figure, never the
    client's claim. Raises 503 when the DB is unreachable and 404 when a named
    entity resolves to no stores (which would otherwise render every source as
    0.00 -- a false 'all sources reconcile' green screen). Returns the result
    WITHOUT the sign-off marker (the GET attaches that separately)."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    from ..services import gst_crosscheck as _xc
    from .reports import _compute_gstr1, _compute_gstr3b

    period = f"{y:04d}-{m:02d}"

    s2e, enames = _store_maps(db)
    s2g = _store_gstin_map(db)
    if entity_id:
        store_ids = [sid for sid, eid in s2e.items() if eid == entity_id]
        if not store_ids:
            raise HTTPException(
                status_code=404, detail="No stores found for this entity"
            )
    else:
        store_ids = list(s2e.keys())
        # HR-2: an empty store map (wiped foundation data or a transient stores
        # query failure) would otherwise render every source 0.00 and sign off a
        # false all-entities "all sources reconcile" green -- the same failure
        # mode the named-entity 404 guards. Refuse the unscoped figure.
        if not store_ids:
            raise HTTPException(
                status_code=404, detail="No stores configured - nothing to reconcile"
            )

    # Reuse the per-store GST-return computations (single source of truth for
    # the tax math), then aggregate to the entity level in the pure service. A
    # per-store compute failure is tracked (not silently dropped) so the screen
    # can flag partial data instead of presenting understated GSTR totals as a
    # tax break, and so sign-off can be blocked.
    g1_reports, g3_reports, g3_entities, g3_gstins = [], [], [], []
    failed: set = set()
    for sid in store_ids:
        try:
            g1_reports.append(_compute_gstr1(period, sid))
        except Exception:  # noqa: BLE001 -- one bad store must not sink the view
            logger.exception("cross-check: GSTR-1 compute failed for %s", sid)
            failed.add(sid)
        try:
            g3_reports.append(_compute_gstr3b(period, sid))
            g3_entities.append(s2e.get(sid))
            g3_gstins.append(s2g.get(sid))
        except Exception:  # noqa: BLE001
            logger.exception("cross-check: GSTR-3B compute failed for %s", sid)
            failed.add(sid)

    gstr1 = _xc.aggregate_gstr1(g1_reports)
    # Regular ITC / RCM are entity-scoped (identical for every sibling store), so
    # pass the entity per store report -- aggregate_gstr3b counts them ONCE per
    # entity. Transfer-borne ITC is GSTIN-scoped (R1), so pass the GSTIN per
    # store report -- it is counted ONCE per GSTIN, making a multi-GSTIN entity's
    # ITC independent of store enumeration order.
    gstr3b = _xc.aggregate_gstr3b(g3_reports, g3_entities, g3_gstins)

    start, end = _gst_month_window(y, m)
    books, tally = _books_and_tally_for_stores(db, store_ids, start, end)

    # Purchase-side ITC (from purchase_orders) as an INDEPENDENT cross-check
    # against GSTR-3B's vendor-bills ITC. Reuse gst_reconciliation()'s math.
    itc_leg_failed = False
    try:
        # purchase_orders.created_at is a BSON datetime (BaseRepository
        # _add_timestamps); no writer ever set a `date` field, so the old
        # `date` string filter matched NOTHING and ITC read a permanent 0. Use
        # the SAME IST created_at window as the orders side, and map a PO to an
        # entity via delivery_store_id OR store_id (gst_reconciliation's mapping).
        # HR-3: exclude DRAFT / CANCELLED POs (auto-draft POs are tax-less, but a
        # human DRAFT or CANCELLED PO with tax_amount would inflate books ITC and
        # flag a phantom mismatch) -- match the case variants PO writers write.
        p_match: dict = {
            "created_at": {"$gte": start, "$lt": end},
            "status": {"$nin": ["DRAFT", "draft", "CANCELLED", "cancelled"]},
        }
        if entity_id:
            p_match["$or"] = [
                {"delivery_store_id": {"$in": store_ids}},
                {"store_id": {"$in": store_ids}},
            ]
        purchases = list(
            db.get_collection("purchase_orders").find(
                p_match,
                {"_id": 0, "delivery_store_id": 1, "store_id": 1, "tax_amount": 1},
            )
        )
        recon = gst_reconciliation(
            [], purchases, s2e, enames,
            store_state_by_id=_store_state_map(db),
            customer_state_by_id=_customer_state_map(db),
        )
        if entity_id:
            match = next(
                (e for e in recon["entities"] if e["entity_id"] == entity_id), None
            )
            books["input_credit"] = match["input_credit"] if match else 0.0
        else:
            books["input_credit"] = recon.get("total_input_credit", 0.0)
    except Exception:  # noqa: BLE001
        logger.exception("cross-check: purchase-side ITC failed")
        # HR-1: a dead ITC leg must not sign off green. input_credit=None renders
        # the ITC row INFO (one source), which the mismatch_count silently
        # ignores; flag it so the sign-off gate can block (409) and the record
        # is stamped for forensics.
        books["input_credit"] = None
        itc_leg_failed = True

    result = _xc.build_crosscheck(gstr1, gstr3b, books, tally)
    result.update(
        {
            "month": m,
            "year": y,
            "period": period,
            "entity_id": entity_id,
            "entity_name": enames.get(entity_id) if entity_id else "All entities",
            "store_count": len(store_ids),
            "stores_computed": len(store_ids) - len(failed),
            "failed_store_ids": sorted(failed),
            "partial": bool(failed),
            "itc_leg_failed": itc_leg_failed,
            "gstr1": {
                "totalTaxableValue": gstr1["totalTaxableValue"],
                "totalTax": gstr1["totalTax"],
                "cgst": gstr1["cgst"],
                "sgst": gstr1["sgst"],
                "igst": gstr1["igst"],
            },
            "gstr3b": {
                "outwardTaxableValue": gstr3b["outwardTaxableValue"],
                "outwardTax": gstr3b["outwardTax"],
                "itc": gstr3b["itc"],
                "netCash": gstr3b["netCash"],
                "rcm": gstr3b["rcm"],
            },
            "books": books,
            "tally": tally,
        }
    )
    return result


@router.get("/gst/cross-check")
async def get_gst_cross_check(
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=2000, le=2100),
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Accountant GST cross-check for a (month, entity): GSTR-1 / GSTR-3B vs the
    books (orders / payments / Tally / purchase-side ITC), side by side, with
    per-rate breakup, CDNR + deemed-supply detail, and mismatch flags. A review
    aid only -- it changes no figure and locks no period."""
    _require_finance_admin(current_user)
    db = _get_db()

    now = now_ist()
    m = month or now.month
    y = year or now.year

    result = _run_gst_cross_check(db, m, y, entity_id)

    # Attach the latest sign-off (if any) so the screen shows CHECKED status.
    signoff = None
    try:
        signoff = db.get_collection(_GST_CROSSCHECK_SIGNOFFS).find_one(
            {"year": y, "month": m, "entity_id": entity_id or "_all"}, {"_id": 0}
        )
    except Exception:  # noqa: BLE001
        signoff = None
    result["signoff"] = signoff
    return result


@router.post("/gst/cross-check-signoff")
async def gst_cross_check_signoff(
    body: GstCrossCheckSignoff,
    current_user: dict = Depends(get_current_user),
):
    """Accountant marks a (month, entity) GST cross-check as CHECKED, with notes.

    Idempotent upsert keyed on (year, month, entity_id). Audit-logged. This is a
    review marker only -- it does NOT lock the accounting period (that is the
    separate /period-lock action) and changes no GST figure."""
    _require_finance_admin(current_user)
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    m = int(body.month)
    y = int(body.year)

    # Recompute the cross-check SERVER-SIDE so the durable audit snapshot is the
    # server's figure, never a (forgeable) client-supplied mismatch_count=0.
    server = _run_gst_cross_check(db, m, y, body.entity_id)
    # HR-1: block sign-off when EITHER reconciliation leg is degraded -- store
    # compute failure (understated GSTR totals) or a dead purchase-side ITC leg
    # (ITC row silently drops to INFO and escapes mismatch_count). Name the leg.
    if server.get("partial") or server.get("itc_leg_failed"):
        reasons = []
        if server.get("partial"):
            reasons.append(
                "%d store(s) failed to compute"
                % len(server.get("failed_store_ids") or [])
            )
        if server.get("itc_leg_failed"):
            reasons.append("the purchase-side ITC leg failed to compute")
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot sign off: %s, so the figures are understated. Resolve "
                "and retry." % " and ".join(reasons)
            ),
        )
    server_summary = server.get("summary", {})
    server_mismatch = int(server_summary.get("mismatch_count", 0) or 0)
    server_payable = float(server_summary.get("gst_payable", 0.0) or 0.0)

    key = {
        "year": y,
        "month": m,
        "entity_id": body.entity_id or "_all",
    }
    reviewer = current_user.get("name") or current_user.get("full_name")
    record = {
        **key,
        "checked": True,
        "checked_by": current_user.get("user_id"),
        "checked_by_name": reviewer,
        "checked_at": _iso_now(),
        "note": body.note,
        # Server-recomputed authoritative snapshot.
        "mismatch_count": server_mismatch,
        "gst_payable": server_payable,
        "mismatch_metrics": server_summary.get("mismatch_metrics", []),
        # Forensic marker: both legs computed cleanly (the gate above blocks a
        # sign-off otherwise, so this is always False on a stored record).
        "itc_leg_failed": bool(server.get("itc_leg_failed")),
        # What the client claimed it saw (drift forensics only, never trusted).
        "client_mismatch_count": body.mismatch_count,
        "client_gst_payable": body.gst_payable,
    }

    # Preserve any prior sign-off in a history array instead of silently
    # overwriting another reviewer's record.
    prior = None
    try:
        prior = db.get_collection(_GST_CROSSCHECK_SIGNOFFS).find_one(key, {"_id": 0})
    except Exception:  # noqa: BLE001
        prior = None
    update: dict = {"$set": record}
    if prior:
        # HR-5: keep BOTH drift-forensics figures (server + client gst_payable)
        # and cap the history at the last 50 sign-offs so it cannot grow without
        # bound.
        update["$push"] = {
            "history": {
                "$each": [
                    {
                        k: prior.get(k)
                        for k in (
                            "checked_by", "checked_by_name", "checked_at", "note",
                            "mismatch_count", "gst_payable",
                            "client_mismatch_count", "client_gst_payable",
                        )
                    }
                ],
                "$slice": -50,
            }
        }
    try:
        db.get_collection(_GST_CROSSCHECK_SIGNOFFS).update_one(
            key, update, upsert=True
        )
    except Exception:  # noqa: BLE001
        logger.exception("GST cross-check sign-off failed")
        raise HTTPException(
            status_code=500,
            detail="Could not record the sign-off - try again or contact support",
        )

    # Audit (fail-soft; never undoes the sign-off write).
    try:
        from api.dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is not None:
            repo.create(
                {
                    "action": "gst_crosscheck.signoff",
                    "entity_type": "gst_crosscheck",
                    "entity_id": f"{key['year']}-{key['month']:02d}:{key['entity_id']}",
                    "user_id": current_user.get("user_id"),
                    "user_name": reviewer,
                    "severity": "INFO",
                    "source": "finance",
                    "after_state": {
                        "checked": True,
                        "note": body.note,
                        "mismatch_count": server_mismatch,
                        "gst_payable": server_payable,
                        "client_mismatch_count": body.mismatch_count,
                    },
                }
            )
    except Exception:  # noqa: BLE001
        pass

    return {"ok": True, "signoff": record}


def _jv_cgst_sgst_split(tax: float) -> tuple:
    """Split a line's total GST into intra-state CGST + SGST so they sum to the
    tax EXACTLY (the rounding residual goes on SGST). A naive round(tax/2) on both
    sides over-states by a paisa on odd-paise tax (100.01 -> 50.01 + 50.01 =
    100.02), which IMBALANCES the Tally voucher and gets it rejected on import.
    Mirrors orders._build_invoice_gst_split."""
    cgst = round(tax / 2.0, 2)
    sgst = round(tax - cgst, 2)
    return cgst, sgst


# ===========================================================================
# B2B invoices -> Tally (accountant console + worklist)
# ===========================================================================
# Owner decision (2026-06-17): GST e-invoice (IRN) + e-way bill are NOT
# generated in IMS -- they are issued in Tally. So the accountant needs to
# (1) pull every B2B sales invoice as Tally-importable XML and (2) keep a
# reminder worklist of which B2B invoices still need handling in Tally.
#
# Identity: an order is a B2B invoice when its CUSTOMER is B2B -- i.e.
# customer_type == "B2B" OR the customer carries a non-empty GSTIN. Walk-in /
# B2C retail sales are excluded.
#
# tally_status lifecycle (stored on the order doc, additive):
#   PENDING   -- default, not yet handled in Tally (the reminder backlog)
#   IN_TALLY  -- exported to Tally XML (optional auto-advance on bulk export)
#   DONE      -- accountant confirmed the invoice + e-invoice/e-way exist in
#                Tally (terminal; stamps done_at / done_by)
#
# needs_eway is DERIVED (never trusted from input): an e-way bill is generally
# required for an inter-state movement OR when the invoice value is >= the
# Rs 50,000 threshold. We derive it fail-soft from the per-invoice GST split
# (inter-state flag) + grand_total; a missing field never raises.

# E-way bill consignment-value threshold (Rs). Inter-state OR value at/above
# this generally needs an e-way bill (Rule 138). Derived hint only -- Tally is
# the system of record; the accountant confirms.
_EWAY_VALUE_THRESHOLD = 50000.0

# How long (days) a B2B invoice may sit PENDING before the worklist flags it as
# an overdue reminder. Small by design -- the accountant should clear the
# Tally backlog promptly so e-invoice/e-way are not missed.
_B2B_PENDING_REMINDER_DAYS = 3

_VALID_TALLY_STATUS = {"PENDING", "IN_TALLY", "DONE"}


def _b2b_customer_map(db) -> dict:
    """customer_id -> {gstin, customer_type, state, name} for B2B classification.

    Fail-soft: any DB error yields an empty map (callers then treat every order
    as non-B2B, returning an empty list rather than a 500)."""
    out: dict = {}
    try:
        for c in db.get_collection("customers").find(
            {},
            {
                "_id": 0,
                "customer_id": 1,
                "gstin": 1,
                "customer_type": 1,
                "state": 1,
                "name": 1,
            },
        ):
            cid = c.get("customer_id")
            if cid:
                out[cid] = {
                    "gstin": str(c.get("gstin") or "").strip(),
                    "customer_type": str(c.get("customer_type") or "").strip().upper(),
                    "state": str(c.get("state") or ""),
                    "name": c.get("name") or "",
                }
    except Exception:  # noqa: BLE001
        pass
    return out


def _is_b2b_customer(cust: Optional[dict]) -> bool:
    """A customer is B2B when customer_type == 'B2B' OR a non-empty GSTIN is
    present (an order placed against a GSTIN-bearing party is a B2B invoice)."""
    if not isinstance(cust, dict):
        return False
    if cust.get("customer_type") == "B2B":
        return True
    return bool(str(cust.get("gstin") or "").strip())


def _days_since(when, now: datetime) -> Optional[int]:
    """Whole days between a stored created_at (BSON datetime or ISO string) and
    now. None when the value is missing/unparseable (never raises)."""
    if when is None:
        return None
    dt = None
    if isinstance(when, datetime):
        dt = when
    else:
        try:
            dt = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    try:
        delta = now - dt
    except (TypeError, ValueError):
        return None
    return max(0, delta.days)


def _b2b_invoice_row(o: dict, cust: dict, split: dict, now: datetime) -> dict:
    """Build one B2B invoice list/worklist row from an order doc + its customer
    + the precomputed GST split. Pure (no I/O)."""
    totals = split.get("totals") or {}
    taxable = round(float(totals.get("taxable") or 0.0), 2)
    cgst = round(float(totals.get("cgst") or 0.0), 2)
    sgst = round(float(totals.get("sgst") or 0.0), 2)
    igst = round(float(totals.get("igst") or 0.0), 2)
    grand = round(float(o.get("grand_total") or o.get("total") or 0.0), 2)
    interstate = bool(split.get("interstate"))

    # needs_eway (derived, fail-soft): inter-state movement OR consignment value
    # at/above the Rs 50,000 threshold generally requires an e-way bill.
    needs_eway = bool(interstate or grand >= _EWAY_VALUE_THRESHOLD)

    status = str(o.get("tally_status") or "PENDING").upper()
    if status not in _VALID_TALLY_STATUS:
        status = "PENDING"

    age_days = _days_since(o.get("created_at"), now)
    # Only PENDING invoices accrue a reminder; once IN_TALLY/DONE it is handled.
    overdue = bool(
        status == "PENDING"
        and age_days is not None
        and age_days >= _B2B_PENDING_REMINDER_DAYS
    )

    # Display number: the stamped GST invoice_number if present, else the
    # order_number (the export list must NOT mint a new invoice serial -- that
    # has accounting-sequence side effects and is owned by the invoice route).
    invoice_number = o.get("invoice_number") or o.get("order_number") or o.get("order_id")

    return {
        "order_id": o.get("order_id"),
        "invoice_number": invoice_number,
        "has_invoice_number": bool(o.get("invoice_number")),
        # BUG-104: the invoice date an accountant reads off the GST / Tally
        # reconciliation screen. IST business day, not the UTC box clock.
        "date": ist_date_str(o.get("created_at")),
        "store_id": o.get("store_id"),
        "customer_id": o.get("customer_id"),
        "customer_name": o.get("customer_name") or cust.get("name") or "",
        "customer_gstin": cust.get("gstin") or split.get("customer_gstin") or "",
        "place_of_supply": split.get("place_of_supply") or "",
        "interstate": interstate,
        "taxable": taxable,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "tax": round(cgst + sgst + igst, 2),
        "total": grand,
        "needs_eway": needs_eway,
        "tally_status": status,
        "exported_to_tally": bool(o.get("exported_to_tally")),
        "exported_at": o.get("exported_at"),
        "exported_by": o.get("exported_by"),
        "done_at": o.get("done_at"),
        "done_by": o.get("done_by"),
        "attention_note": o.get("attention_note") or "",
        "age_days": age_days,
        "overdue": overdue,
    }


def _b2b_invoices(
    db,
    *,
    from_date=None,
    to_date=None,
    store_id=None,
    entity_id=None,
    tally_status=None,
) -> List[dict]:
    """Shared B2B-invoice list builder used by BOTH the export console and the
    worklist. Returns rows for every real (non-DRAFT/CANCELLED) order whose
    customer is B2B, with the per-invoice GST split + needs_eway + tally_status
    + age/overdue reminder fields. Fail-soft: a DB error yields []."""
    from ..routers.orders import _build_invoice_gst_split  # reuse GST math

    cust_map = _b2b_customer_map(db)
    store_state = _store_state_map(db)

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

    try:
        orders = list(db.get_collection("orders").find(match, {"_id": 0}))
    except Exception:  # noqa: BLE001
        return []

    now = now_ist_naive()
    rows: List[dict] = []
    for o in orders:
        cust = cust_map.get(o.get("customer_id"))
        # An order may carry a GSTIN even if the customer record is gone; fall
        # back to a synthetic customer dict so it is still classified B2B.
        if cust is None:
            cust = {
                "gstin": str(o.get("customer_gstin") or "").strip(),
                "customer_type": "",
                "state": "",
                "name": o.get("customer_name") or "",
            }
        if not _is_b2b_customer(cust):
            continue

        # Build the store + customer dicts the GST-split helper expects. The
        # store provides supplier state; the customer provides place-of-supply.
        store_doc = {
            "state": store_state.get(o.get("store_id"), ""),
            "state_code": store_state.get(o.get("store_id"), ""),
            "gstin": "",
        }
        cust_doc = {"gstin": cust.get("gstin"), "state": cust.get("state")}
        try:
            split = _build_invoice_gst_split(o.get("items") or [], store_doc, cust_doc)
        except Exception:  # noqa: BLE001 -- never let one bad order kill the list
            split = {"totals": {}, "interstate": False, "customer_gstin": cust.get("gstin", "")}

        row = _b2b_invoice_row(o, cust, split, now)
        if tally_status and row["tally_status"] != str(tally_status).upper():
            continue
        rows.append(row)
    return rows


def _b2b_summary(rows: List[dict]) -> dict:
    """Aggregate counts/totals for the console header cards."""
    return {
        "count": len(rows),
        "pending": sum(1 for r in rows if r["tally_status"] == "PENDING"),
        "in_tally": sum(1 for r in rows if r["tally_status"] == "IN_TALLY"),
        "done": sum(1 for r in rows if r["tally_status"] == "DONE"),
        "needs_eway": sum(1 for r in rows if r["needs_eway"]),
        "overdue": sum(1 for r in rows if r["overdue"]),
        "exported": sum(1 for r in rows if r["exported_to_tally"]),
        "total_taxable": round(sum(r["taxable"] for r in rows), 2),
        "total_tax": round(sum(r["tax"] for r in rows), 2),
        "total_value": round(sum(r["total"] for r in rows), 2),
    }


class B2BExportRequest(BaseModel):
    """Bulk export of selected B2B invoices to a single Tally XML file."""

    order_ids: List[str] = Field(..., min_length=1)
    # When true, PENDING invoices in the selection are advanced to IN_TALLY
    # (they have now been handed to Tally as XML). DONE rows are never demoted.
    mark_in_tally: bool = True


class B2BMarkExportedRequest(BaseModel):
    order_ids: List[str] = Field(..., min_length=1)


class B2BAttentionNoteRequest(BaseModel):
    # Free-text reminder. Empty string clears the note. Capped to keep the doc
    # small and avoid an unbounded write.
    note: str = Field("", max_length=2000)


def _b2b_fetch_orders(db, order_ids: List[str]) -> List[dict]:
    """Fetch the selected B2B orders (real status only), preserving the
    per-invoice GST/IGST split the day-voucher builder needs. Fail-soft []."""
    cust_map = _b2b_customer_map(db)
    store_state = _store_state_map(db)
    try:
        from ..routers.orders import _build_invoice_gst_split

        docs = list(
            db.get_collection("orders").find(
                {"order_id": {"$in": list(order_ids)}, "status": _REAL_ORDER_STATUS_FILTER},
                {"_id": 0},
            )
        )
    except Exception:  # noqa: BLE001
        return []

    out: List[dict] = []
    for o in docs:
        cust = cust_map.get(o.get("customer_id")) or {
            "gstin": str(o.get("customer_gstin") or "").strip(),
            "customer_type": "",
            "state": "",
            "name": o.get("customer_name") or "",
        }
        if not _is_b2b_customer(cust):
            continue
        store_doc = {
            "state": store_state.get(o.get("store_id"), ""),
            "state_code": store_state.get(o.get("store_id"), ""),
            "gstin": "",
        }
        cust_doc = {"gstin": cust.get("gstin"), "state": cust.get("state")}
        try:
            split = _build_invoice_gst_split(o.get("items") or [], store_doc, cust_doc)
            totals = split.get("totals") or {}
        except Exception:  # noqa: BLE001
            totals = {}
        grand = float(o.get("grand_total") or o.get("total") or 0.0)
        taxable = float(totals.get("taxable") or 0.0)
        # Shape the order for tally_build_day_voucher_xml (subtotal + tax legs).
        o["subtotal"] = round(taxable, 2) if taxable else round(grand - float(totals.get("tax") or 0.0), 2)
        o["cgst_amount"] = round(float(totals.get("cgst") or 0.0), 2)
        o["sgst_amount"] = round(float(totals.get("sgst") or 0.0), 2)
        o["igst_amount"] = round(float(totals.get("igst") or 0.0), 2)
        o["grand_total"] = round(grand, 2)
        # Use the stamped invoice number as the Tally VOUCHERNUMBER when present
        # (falls back to order_id inside the builder otherwise).
        if o.get("invoice_number"):
            o["order_id"] = o["invoice_number"]
        out.append(o)
    return out


@router.get("/b2b-invoices")
async def list_b2b_invoices(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
    tally_status: Optional[str] = Query(
        None, description="Filter PENDING / IN_TALLY / DONE"
    ),
    current_user: dict = Depends(get_current_user),
):
    """List B2B sales invoices (customer is B2B / has a GSTIN) for a date range
    + optional store/entity scope. Powers BOTH the 'Export to Tally' console and
    the 'Tally worklist'. Each row carries the GST split (CGST/SGST/IGST),
    needs_eway, tally_status, exported flag, and the PENDING-age reminder.

    Accountant material -- ADMIN / ACCOUNTANT / SUPERADMIN only."""
    _require_finance_admin(current_user)
    db = _get_db()
    rows = _b2b_invoices(
        db,
        from_date=from_date,
        to_date=to_date,
        store_id=store_id,
        entity_id=entity_id,
        tally_status=tally_status,
    )
    return {
        "invoices": rows,
        "summary": _b2b_summary(rows),
        "eway_threshold": _EWAY_VALUE_THRESHOLD,
        "pending_reminder_days": _B2B_PENDING_REMINDER_DAYS,
    }


@router.get("/b2b-invoices/{order_id}/tally-xml")
async def get_b2b_invoice_tally_xml(
    order_id: str, current_user: dict = Depends(get_current_user)
):
    """Tally sales-voucher XML for ONE B2B invoice (downloadable). The
    accountant imports it into Tally, which then issues the e-invoice/e-way."""
    _require_finance_admin(current_user)
    db = _get_db()
    orders = _b2b_fetch_orders(db, [order_id])
    if not orders:
        raise HTTPException(status_code=404, detail="B2B invoice not found")

    from agents.nexus_providers import tally_build_day_voucher_xml

    xml = tally_build_day_voucher_xml(orders)
    safe = str(order_id).replace("/", "-")
    fname = f"b2b_tally_{safe}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/b2b-invoices/export")
async def export_b2b_invoices_to_tally(
    body: B2BExportRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Build ONE Tally XML for the selected B2B invoices (a Sales voucher per
    invoice with party + per-rate tax ledgers). Optionally advances PENDING
    rows to IN_TALLY (they have been handed to Tally). Returns the XML so the
    accountant imports it into Tally (which issues the e-invoice/e-way bill)."""
    _require_finance_admin(current_user)
    db = _get_db()
    orders = _b2b_fetch_orders(db, body.order_ids)
    if not orders:
        raise HTTPException(
            status_code=404, detail="No B2B invoices found for the selection"
        )

    from agents.nexus_providers import tally_build_day_voucher_xml

    xml = tally_build_day_voucher_xml(orders)

    # Optionally move PENDING -> IN_TALLY (never demote DONE). Fail-soft: a write
    # error does not block the XML download (the accountant still gets the file).
    if body.mark_in_tally:
        try:
            db.get_collection("orders").update_many(
                {
                    "order_id": {"$in": list(body.order_ids)},
                    "tally_status": {"$in": [None, "PENDING"]},
                },
                {
                    "$set": {
                        "tally_status": "IN_TALLY",
                        "tally_in_tally_at": now_ist_naive(),
                    }
                },
            )
        except Exception:  # noqa: BLE001
            pass

    fname = f"b2b_tally_export_{ist_today().isoformat()}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/b2b-invoices/mark-exported")
async def mark_b2b_invoices_exported(
    body: B2BMarkExportedRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Stamp the selected B2B invoices as handed to Tally: exported_to_tally =
    true + exported_at/exported_by, and advance PENDING -> IN_TALLY. The list
    then shows what has already been given to the accountant's Tally."""
    _require_finance_admin(current_user)
    db = _get_db()
    who = current_user.get("user_id") or current_user.get("username") or "system"
    now = now_ist_naive()
    try:
        res = db.get_collection("orders").update_many(
            {"order_id": {"$in": list(body.order_ids)}},
            {
                "$set": {
                    "exported_to_tally": True,
                    "exported_at": now,
                    "exported_by": who,
                }
            },
        )
        # Advance any still-PENDING rows to IN_TALLY (DONE is left intact).
        db.get_collection("orders").update_many(
            {
                "order_id": {"$in": list(body.order_ids)},
                "tally_status": {"$in": [None, "PENDING"]},
            },
            {"$set": {"tally_status": "IN_TALLY", "tally_in_tally_at": now}},
        )
        modified = getattr(res, "modified_count", 0)
    except Exception:  # noqa: BLE001 -- fail loudly, do not pretend success
        logger.exception("Tally mark-exported failed")
        raise HTTPException(
            status_code=503,
            detail="Could not mark the export - try again or contact support",
        )
    return {"ok": True, "marked": modified, "exported_by": who}


@router.post("/b2b-invoices/{order_id}/mark-done")
async def mark_b2b_invoice_done(
    order_id: str, current_user: dict = Depends(get_current_user)
):
    """Confirm a B2B invoice has been created in Tally (with its e-invoice +
    e-way bill where required): tally_status = DONE + done_at/done_by. Clears it
    off the reminder worklist."""
    _require_finance_admin(current_user)
    db = _get_db()
    who = current_user.get("user_id") or current_user.get("username") or "system"
    try:
        res = db.get_collection("orders").update_one(
            {"order_id": order_id},
            {
                "$set": {
                    "tally_status": "DONE",
                    "done_at": now_ist_naive(),
                    "done_by": who,
                }
            },
        )
        matched = getattr(res, "matched_count", 0)
    except Exception:  # noqa: BLE001
        logger.exception("Tally mark-done failed")
        raise HTTPException(
            status_code=503,
            detail="Could not mark as done - try again or contact support",
        )
    if not matched:
        raise HTTPException(status_code=404, detail="B2B invoice not found")
    return {"ok": True, "order_id": order_id, "tally_status": "DONE", "done_by": who}


@router.post("/b2b-invoices/{order_id}/attention-note")
async def set_b2b_attention_note(
    order_id: str,
    body: B2BAttentionNoteRequest = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Set (or clear, with an empty string) the free-text attention note on a
    B2B invoice -- a reminder for the accountant about special Tally handling."""
    _require_finance_admin(current_user)
    db = _get_db()
    note = (body.note or "").strip()
    who = current_user.get("user_id") or current_user.get("username") or "system"
    try:
        res = db.get_collection("orders").update_one(
            {"order_id": order_id},
            {
                "$set": {
                    "attention_note": note,
                    "attention_note_by": who,
                    "attention_note_at": now_ist_naive(),
                }
            },
        )
        matched = getattr(res, "matched_count", 0)
    except Exception:  # noqa: BLE001
        logger.exception("Attention-note save failed")
        raise HTTPException(
            status_code=503,
            detail="Could not save the attention note - try again or contact support",
        )
    if not matched:
        raise HTTPException(status_code=404, detail="B2B invoice not found")
    return {"ok": True, "order_id": order_id, "attention_note": note}


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
        # OS-008: the order's own interstate flag wins (online orders carry it);
        # store-vs-customer state stays the fallback for docs without it.
        is_inter_state = _order_is_interstate(o, _store_states, _customer_states)
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
    """P&L (revenue - COGS - approved expenses - payroll) per store.

    ADMIN / SUPERADMIN only (OWNER DECISION 2026-08-13). Every row carries that
    store's monthly wage bill in `payroll`, and the business runs 4 stores of
    1-5 people: a per-store payroll total IS an individual's pay packet at that
    size, and a two-person store gives up the second person to one subtraction
    against the reader's own payslip. `net_profit` re-exposes the same figure
    (revenue - cogs - expenses - payroll) even if `payroll` were dropped, so
    there is no version of this table that is safe to widen -- the owner was
    offered a payroll-free variant and chose to close the whole screen instead.

    THE COST, STATED PLAINLY: store managers and area managers lose sight of
    their own store's performance ON THIS SCREEN. Their store-level trading
    figures (revenue, cost, profit -- no payroll term anywhere) remain open, and
    keep a working UI, on GET /api/v1/reports/finance/expense-vs-revenue, which
    is store-scoped via validate_store_access and is what ReportsPage's Forecast
    tab renders. GET /api/v1/reports/profit/by-store is likewise clean and
    likewise left open (verified: store-scoped via user_store_scope, and
    profit == revenue - cost exactly), but no frontend screen calls it.
    """
    if not is_salary_admin(current_user):
        raise HTTPException(status_code=403, detail=SALARY_RESTRICTED_MESSAGE)
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

    # backlog #4: show the store NAME, not the raw store id, on the P&L table.
    store_names = _store_name_map(db)

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
                "store_name": store_names.get(sid, sid),
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


# The count-sheet line is defined ONCE, in services/cash_denominations.py.
# This alias keeps the name this router has always used without holding a
# fourth copy of the shape for the four to drift apart. The LIST is
# ``cash_denom.CountSheet`` -- lenient rows inside a strict list still 422'd a
# sheet sent as a bare string, which is a refusal one level up.
DenominationLine = cash_denom.DenominationRow


class CashRegisterOpen(BaseModel):
    store_id: Optional[str] = None
    shift: Optional[str] = None  # AM / PM / FULL (free text)
    denominations: cash_denom.CountSheet = Field(default_factory=list)
    # COUNTED | SUGGESTED | NOT_CAPTURED -- an untouched grid is recorded as
    # never counted, never as an empty float.
    opening_count_state: Optional[str] = None
    opening_float: Optional[float] = None  # optional override of denom sum
    note: Optional[str] = None


class CashRegisterClose(BaseModel):
    session_id: str
    denominations: cash_denom.CountSheet = Field(default_factory=list)
    # COUNTED | SUGGESTED | NOT_CAPTURED -- so a close with an untouched
    # grid is recorded as never counted rather than as an empty drawer.
    closing_count_state: Optional[str] = None
    bank_deposit: float = 0.0
    counted_override: Optional[float] = None  # optional override of denom sum
    # ``tolerance`` was DELETED from this body (owner ruling 2026-08-25: ONE
    # Rs 100 band, from policy). The closer choosing their own band was the
    # exact thing the ruling banned; the server now reads
    # ``till.variance_tolerance_paisa`` -- the SAME band the blind EOD uses.
    # ``note`` becomes MANDATORY when the variance is beyond that band.
    note: Optional[str] = None


def _iso_now() -> str:
    return datetime.utcnow().isoformat()


def _to_dt(s):
    """Parse an ISO date/datetime string to a NAIVE-UTC datetime (None on
    failure).

    Stored instants here are naive-UTC (``_iso_now()``); every caller compares
    the result against that frame. An offset-suffixed string -- never written
    by our stamps, but present in fixtures and possible in hand-edited rows --
    is CONVERTED to that frame. The old ``[:19]`` slice silently DROPPED the
    offset and re-badged the wall time as UTC: '...T21:00:00+05:30' (21:00
    IST) was read as 21:00 UTC, 5h30m late, which filed a legacy till close
    under the next IST business day (BUG-104's mirror image)."""
    if not s:
        return None
    raw = str(s)
    dt = None
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        try:
            dt = datetime.fromisoformat(raw[:19])
        except (ValueError, TypeError):
            try:
                dt = datetime.fromisoformat(raw[:10])
            except (ValueError, TypeError):
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _ist_day_face(value) -> str:
    """The IST calendar day ('YYYY-MM-DD') of a stored instant.

    BUG-104. Timestamps on this surface (`opened_at`, `closed_at`) are written
    by ``_iso_now()`` == ``datetime.utcnow().isoformat()``, so their first ten
    characters are the UTC day -- which, for anything between 00:00 and 05:30
    IST, is YESTERDAY. Parse to the instant first, then take the IST day.
    Unparseable junk keeps the old first-ten-characters behaviour rather than
    dropping the row. One definition, so no caller can drift from another."""
    dt = _to_dt(value)
    return ist_date_str(dt) if dt is not None else str(value or "")[:10]


def _created_at_or_clauses(start_iso, end_iso) -> list:
    """The dual-type `created_at` window: a DATETIME clause OR a STRING clause.

    Mongo type-brackets a Date range away from a string field, so a column
    holding both shapes must be asked for both ways or one type is silently
    dropped (BUG-031). Bounds are normalised to the naive-UTC frame the stored
    values use (BUG-104).

    ONE definition because the refund TOTAL and the per-leg list behind the
    drawer's double-entry advisory read the SAME `returns` rows: aligning
    their bound VALUES but not their clause SHAPES left a Date-typed refund
    visible to the total and invisible to the legs -- and the advisory returns
    early on an empty leg list, so it would switch itself off while the drawer
    still deducted the money."""
    start_str = _naive_utc_iso_bound(start_iso)
    end_str = _naive_utc_iso_bound(end_iso)
    start_dt = _to_dt(start_iso)
    date_win: Dict = {"$gte": start_dt} if start_dt else {}
    str_win: Dict = {"$gte": start_str} if start_str else {}
    if end_iso:
        if end_str:
            str_win["$lte"] = end_str
        end_dt = _to_dt(end_iso)
        if end_dt:
            date_win["$lte"] = end_dt
    clauses = []
    if date_win:
        clauses.append({"created_at": date_win})
    if str_win:
        clauses.append({"created_at": str_win})
    return clauses


def _ist_day_window(start_iso, end_iso) -> tuple:
    """The (start_day, end_day) IST calendar-day pair for a till session.

    BUG-104. Both bounds are IST days because they filter `expense_date`, an
    operator-typed IST CALENDAR DATE. `start_iso` / `end_iso` are the
    session's naive-UTC stamps (`_iso_now`), so their first ten characters are
    the UTC day -- yesterday for anything in the 00:00-05:30 IST band. An
    ABSENT `end_iso` means "still open, up to now", which is TODAY in the same
    IST frame.

    ONE definition on purpose. This exact two-line pair was copied inline
    FOUR times across four review rounds -- the drawer charge and the
    double-entry advisory being the pair that mattered, because a drift
    between them makes the advisory name a payout the drawer never
    subtracted. Both callers now route through here so they cannot drift."""
    start_day = _ist_day_face(start_iso)
    end_day = _ist_day_face(end_iso) if end_iso else ist_today().isoformat()
    return start_day, end_day


def _naive_utc_iso_bound(v) -> Optional[str]:
    """The NAIVE-UTC ISO face of a bound, for a LEGACY STRING clause.

    BUG-104. String-typed ``created_at`` columns (`returns`, legacy online
    orders) hold naive-UTC isoformats, so a bound compared against them
    LEXICALLY must be in that frame too. Passing the raw value through meant
    an offset-suffixed bound ('...T21:00:00+05:30') was compared face-value
    against '...T15:30:00' -- sorting 5h30m late and silently dropping rows.
    ``_to_dt`` already normalises an aware value to naive-UTC (#993);
    unparseable junk keeps the old raw face.

    ONE definition: the drawer's refund TOTAL and the per-leg list behind its
    advisory read the same returns rows, so a second copy here would let the
    total say one thing while the legs said another and the advisory quietly
    switched itself off."""
    if v is None:
        return None
    dt = v if isinstance(v, datetime) else _to_dt(v)
    if dt is None:
        return str(v)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def _cash_sales_for_window(db, store_id: str, start_iso: str, end_iso: Optional[str]):
    """Net POS CASH collected for a store between start and end (ISO strings).

    Sums order.payments[] where method == 'CASH' (the canonical tender field;
    `mode` tolerated as a legacy alias). Negative CASH tenders (refunds) are
    returned separately so the reconciliation can show sales vs refunds.

    MONEY-LEAK FIX: a money refund is recorded ONLY as a `returns` doc
    (returns.py create_return) -- POS never writes a negative payments[] row --
    so cash_refunds stayed 0 and Day-End showed a false SHORT every time cash
    was refunded. Refunds are therefore ALSO netted from the RETURNS collection,
    windowed on the RETURN's OWN created_at (= the refund date; the original
    sale's day-window would book the refund into the wrong day). Deliberately
    NOT modelled as negative payments[] rows: that would corrupt the
    payment_status ladder, the online-mapper staff_sum floor, the cumulative
    refund cap, Tally receipt vouchers and stamp_payment_ledgers.

    TENDER SOURCE (money-panel redesign, PR #964 round 2): the CASH figure is
    netted off the EXPLICIT per-tender breakdown the cashier recorded on the
    Returns screen (returns.refund_tenders for a RETURN; returns.collect_method
    for a COLLECT-direction EXCHANGE cash-IN) -- NEVER the inferred refund_method.
    Guessing the tender from the original sale regressed split-tender days (a
    UPI+CASH sale's cash-back kept a false shortage; a CASH+CARD sale's whole
    refund was cut from CASH -> negative drawer). A return that carries NO
    refund_tenders is UNKNOWN to the drawer and netted NOWHERE (staff keep the
    manual "cash paid out" workaround; the drawer never sees a fabricated number).
    Returns (cash_sales, cash_refunds) as positive magnitudes."""
    if db is None:
        return 0.0, 0.0
    # BUG-031: orders.created_at is a BSON Date, so an ISO-STRING $gte/$lte never
    # matches it (Mongo type-bracketing) -> cash_sales always 0 -> false drawer
    # variance. Match BOTH a datetime window (current Date-typed docs) AND a
    # string window (any legacy ISO-string created_at) via $or.
    #
    # The string clause is built from an ISO-STRING form of the bound even when a
    # caller passes a datetime (the annotation says str, but the IST-day helpers
    # elsewhere hand out naive-UTC datetimes) -- returns.created_at is string-only
    # and would type-bracket to no match against a datetime bound, silently
    # resurrecting the whole false-shortage bug. Coerce so it never can.
    or_clauses = _created_at_or_clauses(start_iso, end_iso)
    match: Dict = {"store_id": store_id}
    if or_clauses:
        match["$or"] = or_clauses

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

    # Returns-collection netting (see docstring). created_at on returns docs is
    # an ISO STRING (datetime.now().isoformat()); reuse the same dual
    # string/datetime $or window (BUG-031 pattern) for safety. historical:True
    # docs (Shopify-era imports) settled OUTSIDE the drawer and are excluded.
    #
    # TENDER SOURCE (money-panel redesign): net off the EXPLICIT refund_tenders /
    # collect_method the cashier recorded -- NEVER the inferred refund_method
    # (guessing it regressed split-tender days). A return with no refund_tenders
    # is UNKNOWN to the drawer and netted NOWHERE (staff keep the manual "cash
    # paid out" workaround; the drawer never gets a fabricated number).
    #
    # Fail-soft is NON-DESTRUCTIVE: accumulate into locals and merge ONLY after
    # the cursor drains, so a mid-cursor error leaves the payments-derived
    # figures untouched (never a half-netted drawer presented as authoritative).
    try:
        from ..services.tender_routing import canonicalize_tender

        add_cash_refunds = 0.0
        add_cash_sales = 0.0
        ret_match: Dict = {
            "store_id": store_id,
            "status": "COMPLETED",
            "historical": {"$ne": True},
            "$or": or_clauses,
        }
        rcursor = db.get_collection("returns").find(
            ret_match,
            {
                "_id": 0,
                "return_type": 1,
                "status": 1,
                "historical": 1,
                "refund_tenders": 1,
                "collect_method": 1,
                "collect_amount": 1,
                "settlement": 1,
            },
        )
        for r in rcursor:
            # Belt-and-braces re-checks (a stub collection may ignore the
            # filter; dirty data must never move the drawer figure).
            if str(r.get("status") or "").upper() != "COMPLETED":
                continue
            if r.get("historical") is True:
                continue
            rtype = str(r.get("return_type") or "").upper()
            if rtype == "RETURN":
                # Sum the CASH legs of the EXPLICIT refund breakdown only.
                for t in r.get("refund_tenders") or []:
                    if canonicalize_tender((t or {}).get("method")) != "CASH":
                        continue
                    try:
                        amt = float((t or {}).get("amount") or 0)
                    except (TypeError, ValueError):
                        amt = 0.0
                    if amt > 0:
                        add_cash_refunds += amt
            elif rtype == "EXCHANGE":
                # A COLLECT is a NEW cash-in; key it off collect_method (absent
                # -> UNKNOWN, added nowhere).
                direction = str(
                    (r.get("settlement") or {}).get("direction") or ""
                ).upper()
                if direction != "COLLECT":
                    continue
                if canonicalize_tender(r.get("collect_method")) != "CASH":
                    continue
                try:
                    coll_amt = float(r.get("collect_amount") or 0)
                except (TypeError, ValueError):
                    coll_amt = 0.0
                if coll_amt > 0:
                    add_cash_sales += coll_amt
            # CREDIT_NOTE / EXCHANGE-refund issue STORE CREDIT (no drawer cash).
        cash_refunds += add_cash_refunds
        cash_sales += add_cash_sales
    except Exception:
        logger.warning("[CASH-DRAWER] returns netting skipped (fail-soft)", exc_info=True)
    return round(cash_sales, 2), round(cash_refunds, 2)


# ===========================================================================
# OFF-TILL EXPENSE HEADS -- an EXPENSE-CLASSIFICATION correction, not a
# redaction. Read this before changing the drawer maths below.
# ===========================================================================
# OWNER RULING 2026-08-14, asked directly: are salaries, staff advances or
# PF/ESI ever paid out of a shop cash till?
#
#     "NEVER - always bank, cheque or from the office."
#
# Today the code disagrees with him, and the disagreement costs money at
# day-end rather than merely leaking one. ExpenseCreate.payment_mode is
# Optional (routers/expenses.py), and the loop below treats a BLANK mode as
# cash. So a wage bill typed into the free-text expense box with the payment-
# mode dropdown left alone is subtracted from the drawer:
#
#     expected_cash = opening + cash_sales - cash_refunds - cash_expenses
#
# Subtracting money that never left the till makes `expected` too LOW, so the
# physical count reads as a large phantom OVERAGE. That fires in every affected
# store the first month somebody books pay as an expense, and an "overage" the
# size of a month's wages is exactly the kind of alarm a manager learns to
# ignore. This is a money-correctness bug first.
#
# IT ALSO CLOSES A LEAK THIS BRANCH OPENED. Round 1 (47bd52c) made
# GET /finance/pnl payroll-EXCLUSIVE for the manager tier while these till
# routes stayed payroll-INCLUSIVE over the SAME store -- the four roles on
# rbac_policy rows for /finance/pnl and /finance/cash-register/sessions are the
# same four roles. Two requests, one subtraction, wage bill recovered. See
# services/salary_visibility.py "THE SECOND COROLLARY".
#
# THEREFORE THIS EXCLUSION IS IDENTICAL FOR EVERY ROLE, ADMIN AND SUPERADMIN
# INCLUDED. There is deliberately no role check in this function or its
# callers. A role-conditional drawer would just be a THIRD asymmetry to
# subtract across; removing the differential entirely leaves nothing to
# subtract. It also means the number a human counts money against is the same
# number for everyone who looks at it, which is the only defensible property
# for a cash-drawer figure.
#
# THE DELIBERATE CHOICE, AND ITS FAILURE MODE
# -------------------------------------------
# What if somebody books a payroll-shaped head with payment_mode EXPLICITLY set
# to CASH? That contradicts the owner. Two options:
#
#   (a) honour the explicit mode -- drawer stays arithmetically right in the
#       case where it really did happen, but the wage bill is back in a figure
#       the manager tier can read, and the cross-route subtraction reopens.
#   (b) exclude it ALWAYS, whatever the mode.
#
# WE TAKE (b). The ruling is unconditional, so an explicit CASH mode on a pay
# head is a mis-booking, and (a) would make the leak controllable by whoever
# books the expense -- an attacker-chosen field. Failure mode of (b), stated
# plainly because the owner has to live with it at day-end: IF cash genuinely
# went out of a till for a pay head, that drawer reads SHORT by that amount.
# A shortage gets investigated; it is not silent. AND IT IS NOT SILENT HERE
# EITHER -- `excluded_count` below drives a visible note on the close screen
# and the running preview (no amount, no head name), because a number a human
# counts money against must never be adjusted behind their back.
#
# RESIDUAL, DISCLOSED: on a store where cash really did leave the till for a
# pay head, the resulting variance IS that amount. Deriving it requires the
# owner's "never" to have been broken, and in that world the manager watched
# the cash leave. Not closable without reintroducing (a).


class _CashExpenseWindow(NamedTuple):
    """Drawer-relevant cash expenses, plus what was left out of them.

    ``excluded_count`` is a COUNT and never an amount: it exists so the screen
    can say "something here is not paid from the till" without handing the
    manager tier the pay figure the rest of this PR withholds.
    """

    total: float
    excluded_count: int


def _cash_expenses_for_window(
    db, store_id: str, start_iso: str, end_iso: Optional[str]
) -> _CashExpenseWindow:
    """Cash payouts from the drawer for a store in the window.

    Expenses use `expense_date` (ISO date) and `payment_mode`. Only CASH-mode
    expenses come out of the physical drawer; UPI/CARD/BANK don't. Counts
    APPROVED / PAID / SENT_TO_ACCOUNTANT spends (anything that represents money
    actually disbursed). Payroll-shaped heads are excluded outright -- see the
    block above. Fail-soft to (0.0, 0)."""
    if db is None:
        return _CashExpenseWindow(0.0, 0)
    # BUG-104. `expense_date` is an operator-typed IST CALENDAR DATE, so BOTH
    # bounds must be IST days. `start_iso` is the session's `opened_at`, a
    # naive-UTC instant: its first ten characters are the UTC day, so a till
    # opened 00:00-05:30 IST reached back into the PREVIOUS IST day and
    # subtracted a second day of payouts from the drawer (expected too low ->
    # an honest count reads as an overage). The upper bound was also
    # INCONSISTENT with the lower one -- the UTC face when a close passed
    # `end_iso`, the IST face when the live preview passed None -- so the two
    # ends of one window sat in different calendar frames.
    start_day, end_day = _ist_day_window(start_iso, end_iso)
    total = 0.0
    excluded = 0
    try:
        cursor = db.get_collection("expenses").find(
            {
                "store_id": store_id,
                "expense_date": {"$gte": start_day, "$lte": end_day},
            },
            {
                "_id": 0,
                "amount": 1,
                "payment_mode": 1,
                "status": 1,
                "expense_date": 1,
                "category": 1,
            },
        )
        for e in cursor:
            mode = str(e.get("payment_mode") or "").upper()
            if mode and mode != "CASH":
                continue  # unknown mode counts as cash (conservative)
            status = str(e.get("status") or "").upper()
            if status not in ("APPROVED", "PAID", "SENT_TO_ACCOUNTANT", "REIMBURSED"):
                continue
            # Salaries, advances and PF/ESI never come out of a shop till
            # (owner, 2026-08-14). Same matcher as /pnl and /budgets, imported
            # from services/salary_visibility -- never a local copy.
            if is_payroll_shaped_expense(e.get("category")):
                excluded += 1
                continue
            try:
                total += float(e.get("amount", 0) or 0)
            except (TypeError, ValueError):
                pass
    except Exception:
        return _CashExpenseWindow(0.0, 0)
    return _CashExpenseWindow(round(total, 2), excluded)


# Shown verbatim to whoever is counting the drawer. No amount, no head name:
# it tells them the expected figure deliberately leaves something out and who
# to ask, which is all they need to stop themselves "correcting" a real count.
OFF_TILL_EXPENSE_MESSAGE = (
    "One or more expenses booked here in this period are not paid out of the "
    "shop till, so they are left out of the expected-cash figure. If your "
    "count does not tally, check with an administrator before adjusting "
    "anything."
)


# A CASH expense whose amount matches a recorded CASH refund to within a paisa
# is almost certainly the SAME money keyed twice (the pre-fix workaround was to
# log a customer refund as a "cash paid out"). A bare co-occurrence check would
# fire on any day with a refund and Rs 200 of chai money and be tuned out inside
# a week, so the advisory requires an AMOUNT match (or an explicitly
# refund-flavoured category) and names both figures.
_REFUND_EXPENSE_HINTS = ("REFUND", "RETURN", "CUSTOMER REFUND")
_DOUBLE_ENTRY_EPSILON = 0.01


def _cash_refund_legs_for_window(
    db, store_id: str, start_iso, end_iso
) -> List[float]:
    """Individual recorded CASH refund leg amounts in the window (drawer-truth
    from returns.refund_tenders). Feeds the amount-matched double-entry
    advisory. Fail-soft -> []."""
    if db is None:
        return []
    legs: List[float] = []
    try:
        from ..services.tender_routing import canonicalize_tender

        # The SAME WINDOW as the refund TOTAL -- same bound values AND the
        # same dual-type clause shape (_created_at_or_clauses). Emitting only
        # the string arm here left a Date-typed refund visible to the total
        # and invisible to these legs, and the advisory gives up on an empty
        # leg list: the drawer would deduct the money while the warning about
        # it silently switched off.
        leg_match: Dict = {
            "store_id": store_id,
            "status": "COMPLETED",
            "historical": {"$ne": True},
        }
        leg_or = _created_at_or_clauses(start_iso, end_iso)
        if leg_or:
            leg_match["$or"] = leg_or
        cursor = db.get_collection("returns").find(
            leg_match,
            {"_id": 0, "return_type": 1, "refund_tenders": 1},
        )
        for r in cursor:
            if str(r.get("return_type") or "").upper() != "RETURN":
                continue
            for t in r.get("refund_tenders") or []:
                if canonicalize_tender((t or {}).get("method")) != "CASH":
                    continue
                try:
                    amt = float((t or {}).get("amount") or 0)
                except (TypeError, ValueError):
                    amt = 0.0
                if amt > 0:
                    legs.append(round(amt, 2))
    except Exception:  # noqa: BLE001
        return []
    return legs


def _refund_double_entry_advisory(
    db, store_id: str, start_iso, end_iso, cash_refunds: float
) -> Optional[Dict]:
    """Amount-matched double-count advisory, or None.

    Returns ``{matched_amount, cash_refunds, cash_expenses_matched, message}``
    when a CASH expense in the window matches a recorded CASH refund leg to the
    paisa (or carries a refund-flavoured category/note). Advisory ONLY -- never
    blocks a close and never adjusts a figure (a genuine same-day petty-cash
    payout must stay recordable)."""
    if db is None or not cash_refunds:
        return None
    legs = _cash_refund_legs_for_window(db, store_id, start_iso, end_iso)
    if not legs:
        return None
    try:
        # THE SAME WINDOW the drawer itself uses (BUG-104). This advisory
        # names a specific payout and tells a manager to delete it, so reading
        # a different window from _cash_expenses_for_window is worse than
        # reading none: on a till opened 00:00-05:30 IST it reached back a day
        # and told them to remove a legitimate entry the PREVIOUS day's close
        # had already subtracted -- turning a correct count into a shortage.
        start_day, end_day = _ist_day_window(start_iso, end_iso)
        cursor = db.get_collection("expenses").find(
            {
                "store_id": store_id,
                "expense_date": {"$gte": start_day, "$lte": end_day},
            },
            {
                "_id": 0, "amount": 1, "payment_mode": 1, "status": 1,
                "category": 1, "note": 1, "description": 1,
            },
        )
        for e in cursor:
            mode = str(e.get("payment_mode") or "").upper()
            if mode and mode != "CASH":
                continue
            status = str(e.get("status") or "").upper()
            if status not in ("APPROVED", "PAID", "SENT_TO_ACCOUNTANT", "REIMBURSED"):
                continue
            try:
                amt = round(float(e.get("amount", 0) or 0), 2)
            except (TypeError, ValueError):
                continue
            blob = " ".join(
                str(e.get(k) or "") for k in ("category", "note", "description")
            ).upper()
            refundish = any(h in blob for h in _REFUND_EXPENSE_HINTS)
            match = any(abs(amt - leg) <= _DOUBLE_ENTRY_EPSILON for leg in legs)
            if match or (refundish and amt > 0):
                return {
                    "matched_amount": amt,
                    "cash_refunds": round(float(cash_refunds), 2),
                    "reason": "AMOUNT_MATCH" if match else "REFUND_CATEGORY",
                    "message": (
                        f"A cash expense of Rs {amt:.2f} matches a customer cash "
                        f"refund already auto-deducted from this drawer "
                        f"(recorded refunds Rs {float(cash_refunds):.2f}). If it is "
                        "the same money, remove the manual entry - otherwise "
                        "ignore this notice."
                    ),
                }
    except Exception:  # noqa: BLE001
        return None
    return None


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

    # W1.4 / OS-030: an ONLINE store has no cash drawer -- its payments settle
    # through the online payment gateway. Opening a till for it would create a
    # fictitious session in the cash-reconciliation summary.
    if is_online_store(db, store_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "This is an online store - payments settle via the payment "
                "gateway, so there is no cash drawer to open."
            ),
        )

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

    # TWO DOORS, ONE RECORD -- the OPEN half. The float declared here lands on
    # the day's single till session, the same record POS Day-End closes onto.
    # Without it the shared record holds an opening float of ZERO with
    # opening_float_not_recorded set, so expected cash and EVERY per-face
    # expected row are computed from nothing and the note-by-note verdict is
    # withheld for a store that opens on this screen. Fail-soft: linking must
    # never stop a store opening its drawer.
    #
    # NOTHING DECLARED -> NOTHING FORWARDED. A blank grid with no typed float
    # makes `opening_float` 0.0 because it is the sum of nothing; forwarding
    # that would put "the drawer opened with Rs 0.00" on the record all three
    # screens read.
    till_open = till_service.record_screen_open(
        db,
        store_id=store_id,
        # The BUSINESS day, in the same IST frame the close half uses.
        session_date=ist_date_str(_to_dt(now)) or str(now)[:10],
        opening_denominations=denoms,
        opening_count_state=body.opening_count_state,
        opening_float_paisa=(
            cash_denom.rupees_to_paisa(opening_float)
            if (denoms or body.opening_float is not None)
            else None
        ),
        shift=body.shift,
        note=body.note,
        actor=current_user,
    )
    till_session = till_open.get("session") or {}

    session_id = f"CR-{store_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    doc = {
        "session_id": session_id,
        "store_id": store_id,
        "status": "OPEN",
        "shift": (body.shift or "").upper() or None,
        "opening_float": opening_float,
        # LEGACY SHAPE, unchanged -- every existing reader of this collection
        # keeps working byte-for-byte.
        "opening_denominations": denoms,
        # THE SHARED SHAPE. Same rows, plus the state, so a float nobody
        # counted reads NOT_CAPTURED instead of as an empty drawer.
        "opening_count": cash_denom.build_block(
            denoms,
            cash_denom.rupees_to_paisa(opening_float),
            state=body.opening_count_state,
            actor=current_user,
        ),
        "opened_at": now,
        "opened_by": current_user.get("user_id"),
        "opened_by_name": current_user.get("name"),
        "opening_note": body.note,
        # The shared session this float was declared on, and whether it got
        # there. A failure is stored, not swallowed into a fake success.
        "till_session_id": till_session.get("session_id"),
        "till_link_ok": bool(till_open.get("ok")),
        "till_link_error": till_open.get("error"),
        # True when the day's session already carried a declared float: that
        # one STANDS and this screen's is not the shared one.
        "till_float_already_declared": bool(
            till_open.get("already_open")
            and not till_session.get("opening_float_not_recorded")
            and till_session.get("opening_float_paisa")
            != cash_denom.rupees_to_paisa(opening_float)
        ),
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
    except Exception:  # noqa: BLE001
        logger.exception("Cash session open failed")
        raise HTTPException(
            status_code=500,
            detail="Could not open the cash session - try again or contact support",
        )
    doc.pop("_id", None)
    return doc


def _shared_counted_paisa(till_link: Optional[Dict[str, Any]]) -> Optional[int]:
    """The counted figure ON THE SHARED TILL RECORD after a close linked to it,
    or None when nothing was counted there (or the link failed).

    This is the ONE answer for the store-day: whichever door counted the drawer
    first owns it, and both screens must report that same number."""
    session = (till_link or {}).get("session") or {}
    value = session.get("blind_count_paisa")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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

    # ONE band, from policy (owner ruling 2026-08-25): the SAME store-scopable
    # ``till.variance_tolerance_paisa`` the blind EOD verdict uses -- never a
    # figure the closer types. Rupees at this door's boundary.
    tolerance_rupees = till_service.get_variance_tolerance_paisa(store_id=store_id) / 100.0

    cash_sales, cash_refunds = _cash_sales_for_window(db, store_id, start_iso, end_iso)
    _cash_exp = _cash_expenses_for_window(db, store_id, start_iso, end_iso)
    cash_expenses = _cash_exp.total
    opening_float = float(session.get("opening_float", 0) or 0)

    denoms = cash_register.normalize_denominations(
        [d.model_dump() for d in body.denominations]
    )
    # The sheet exactly as it will be stored, built ONCE here; its amount is
    # restated at the bottom, when the counted figure is final.
    closing_block = cash_denom.build_block(
        denoms, 0, state=body.closing_count_state, actor=current_user
    )
    # NOBODY COUNTED -> NO COUNTED FIGURE. The sum of a blank grid is 0.00 --
    # the sum of nothing -- and persisting it said the drawer WAS counted and
    # found empty: a full-day negative variance and a SHORT verdict against a
    # manager who simply never counted. Blank is an absence, at this door too.
    # `is_captured` is the same authority the shared record uses, so an
    # explicit COUNTED ("counted, and there was none") is still a real zero,
    # and so is a typed override of 0.
    counted_recorded = (
        cash_denom.is_captured(closing_block) or body.counted_override is not None
    )
    counted = (
        (
            round(float(body.counted_override), 2)
            if body.counted_override is not None
            else cash_register.total_from_denominations(denoms)
        )
        if counted_recorded
        else None
    )

    summary = cash_register.build_close_summary(
        opening_float=opening_float,
        cash_sales=cash_sales,
        cash_refunds=cash_refunds,
        cash_expenses=cash_expenses,
        bank_deposit=body.bank_deposit,
        denominations=denoms,
        tolerance=tolerance_rupees,
    )
    # build_close_summary uses the denoms total for counted; honour an override.
    summary["counted"] = counted
    summary["variance"] = (
        None
        if counted is None
        else cash_register.compute_variance(counted, summary["expected"])
    )
    # RECOMPUTE THE VERDICT -- but NEVER resurrect an over/short verdict that
    # build_close_summary deliberately withheld. No count means no variance and
    # so no verdict at all; a negative expected drawer means a cash-in is
    # missing, and re-deriving the status here overwrote NEGATIVE_EXPECTED with
    # a phantom OVERAGE and persisted it next to its own amber note saying the
    # verdict was withheld.
    if counted is None:
        summary["variance_status"] = cash_register.NOT_COUNTED
    elif summary.get("negative_expected_advisory"):
        summary["variance_status"] = cash_register.NEGATIVE_EXPECTED
    else:
        summary["variance_status"] = cash_register.variance_status(
            summary["variance"], tolerance_rupees
        )

    # E5 (ADDITIVE): by-mode reconciliation over the same session window. This
    # does NOT touch the CASH-only variance above (build_close_summary is
    # unchanged) -- it stores a per-tender breakdown alongside it so the close
    # screen / Tally JV can see UPI/CARD/etc. net. Fail-soft: any error leaves
    # the cash close exactly as before.
    # include_returns=True so this breakdown is on the SAME basis as the
    # blind-EOD rows (net of recorded refunds). The manager console renders both
    # in one grid; mixing a payments-only figure with a returns-netted one made
    # the same store/day/tender show two different numbers with no label.
    by_mode_breakdown = None
    try:
        from ..services.tender_reconciliation import reconcile_window

        _recon = reconcile_window(db, store_id, start_iso, end_iso, include_returns=True)
        by_mode_breakdown = _recon.get("by_mode")
    except Exception:  # noqa: BLE001
        by_mode_breakdown = None

    # Non-blocking, AMOUNT-MATCHED double-count advisory: a manual CASH expense
    # that matches a recorded cash refund to the paisa is probably the same money
    # keyed twice (the pre-fix workaround). Never auto-applied, never blocking.
    refund_double_entry = _refund_double_entry_advisory(
        db, store_id, start_iso, end_iso, cash_refunds
    )

    # TWO DOORS, ONE RECORD: this close and POS Day-End land the SAME counted
    # drawer on the SAME till session for the day, so the two screens can never
    # hold two different answers. The rupee arithmetic above is UNCHANGED --
    # this only links and shares the count. Fail-soft: linking must never stop
    # a store closing its till, and a failure is reported, not hidden.
    till_link = till_service.record_screen_close(
        db,
        store_id=store_id,
        # The BUSINESS day, in the same frame everything else uses. `end_iso`
        # is a NAIVE-UTC instant, so slicing its first ten characters reads
        # the UTC day: a till closed between 00:00 and 05:30 IST would link
        # to YESTERDAY's session while this console files the very same row
        # under today (it already parses-then-shifts, just below). Same
        # helper, same answer.
        session_date=ist_date_str(_to_dt(end_iso)) or str(end_iso)[:10],
        closing_rows=[d.model_dump() for d in body.denominations],
        closing_count_state=body.closing_count_state,
        # The counted figure this screen is storing. It is only forwarded as the
        # count when no grid came with it; with a grid, the notes rule and this
        # is used to notice a `counted_override` that disagrees with them.
        #
        # NOTHING COUNTED -> NOTHING FORWARDED. `counted` is None when nobody
        # counted, and a None never becomes a Rs 0.00 submitted to the shared
        # record -- that is the blank-persisted-as-emptied defect this work
        # removes, and the record all three screens read is the worst place for
        # it. Same test as the figure this screen stores: one answer, not two.
        counted_paisa=(
            cash_denom.rupees_to_paisa(counted) if counted is not None else None
        ),
        actor=current_user,
    )

    # ONE DRAWER, ONE COUNTED FIGURE. When the day was already counted through
    # the other door, THAT count stands (record_screen_close never overwrites a
    # signed-off count) -- so this screen must REPORT it rather than its own.
    # Reporting its own is how one drawer on one day came to read Rs 2,000 on
    # Finance > Cash Register and Rs 3,000 on the Z-Read. Only the COUNT is
    # shared; the expected figure below is still this window's own arithmetic.
    # The grid typed here is still stored as this screen's sheet, and a sheet
    # that disagrees with the shared count flags itself (matches_amount False).
    shared_paisa = _shared_counted_paisa(till_link)
    count_adopted = shared_paisa is not None and (
        counted is None or shared_paisa != cash_denom.rupees_to_paisa(counted)
    )
    if count_adopted:
        counted = cash_denom.paisa_to_rupees(shared_paisa)
        summary["counted"] = counted
        summary["variance"] = cash_register.compute_variance(
            counted, summary["expected"]
        )
        # There IS a count now, so the withheld NOT_COUNTED verdict must not
        # survive -- only NEGATIVE_EXPECTED still outranks the arithmetic.
        summary["variance_status"] = (
            cash_register.NEGATIVE_EXPECTED
            if summary.get("negative_expected_advisory")
            else cash_register.variance_status(summary["variance"], tolerance_rupees)
        )

    # MANDATORY NOTE ABOVE THE BAND (owner ruling 2026-08-25). Checked AFTER
    # the shared-record adoption so the figure judged is the ONE counted figure
    # for the day. Same rule, same helper as the blind Z-Read lock. Refusing
    # here is retry-safe: the count already landed on the shared till record,
    # and a resubmit with the note adopts it back (already_counted) unchanged.
    close_note = (body.note or "").strip()
    variance_out_of_band = till_service.needs_variance_note(
        cash_denom.rupees_to_paisa(summary["variance"]) if summary["variance"] is not None else None,
        cash_denom.rupees_to_paisa(tolerance_rupees),
    )
    if variance_out_of_band and not close_note:
        raise HTTPException(
            status_code=400,
            detail=(
                "variance_note_required: the drawer is over/short beyond the "
                f"allowed band of Rs {tolerance_rupees:.0f} - a note explaining "
                "the variance is required to close."
            ),
        )

    update = {
        "status": "CLOSED",
        "closed_at": end_iso,
        "closed_by": current_user.get("user_id"),
        "closed_by_name": current_user.get("name"),
        "closing_denominations": denoms,
        # The shared Cash Count Block + the session it belongs to. The block
        # was built above; this points it at the figure actually being stored.
        "closing_count": cash_denom.restate_amount(
            closing_block, cash_denom.rupees_to_paisa(counted)
        ),
        "till_session_id": till_link.get("session_id"),
        "till_link_ok": bool(till_link.get("ok")),
        "till_link_error": till_link.get("error"),
        "till_already_counted": bool(till_link.get("already_counted")),
        "till_counted": bool(till_link.get("counted")),
        "till_opening_float_not_recorded": bool(
            till_link.get("opening_float_not_recorded")
        ),
        # True when this screen's counted figure and the shared record's differ
        # -- a manual override typed over the notes, or a drawer the other door
        # had already counted. Either way the shared figure is what is stored
        # above, and this flag is how the screen says so out loud.
        "till_count_differs": bool(till_link.get("count_differs")) or count_adopted,
        # The count on this close came from the shared record, not from the
        # grid on this screen.
        "counted_from_shared_record": count_adopted,
        "cash_sales": cash_sales,
        "cash_refunds": cash_refunds,
        "cash_expenses": cash_expenses,
        "refund_double_entry_advisory": refund_double_entry,
        # Salaries / advances / PF-ESI are never paid from a till (owner
        # 2026-08-14), so they are OUT of `cash_expenses` above. Say so on the
        # record: an expected-cash figure that quietly leaves something out is
        # exactly the "screen stating something the system knows is not true"
        # that PR #960 was written to kill. Count only -- never the amount.
        "off_till_expense_advisory": bool(_cash_exp.excluded_count),
        "off_till_expense_message": (
            OFF_TILL_EXPENSE_MESSAGE if _cash_exp.excluded_count else None
        ),
        "negative_expected_advisory": summary.get("negative_expected_advisory", False),
        "negative_expected_message": summary.get("negative_expected_message"),
        # by_mode_breakdown is NET OF RECORDED REFUNDS (same basis as the
        # blind-EOD rows) so the manager console never shows two definitions of
        # the same tender figure in one grid.
        "by_mode_breakdown": by_mode_breakdown,
        "by_mode_basis": "NET_OF_RECORDED_REFUNDS",
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
    except Exception:  # noqa: BLE001
        logger.exception("Cash session close failed")
        raise HTTPException(
            status_code=500,
            detail="Could not close the cash session - try again or contact support",
        )

    # Manager alert above the band (owner ruling 2026-08-25). SAME helper and
    # SAME (store, day) dedupe as the blind Z-Read lock, so the two doors raise
    # ONE task for one drawer-day. Fail-soft inside the helper.
    if variance_out_of_band:
        till_service.raise_variance_task(
            store_id=store_id,
            session_date=ist_date_str(_to_dt(end_iso)) or str(end_iso)[:10],
            variance_paisa=cash_denom.rupees_to_paisa(summary["variance"]),
            tolerance_paisa=cash_denom.rupees_to_paisa(tolerance_rupees),
            note=close_note,
            source="Finance cash-register close",
        )

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
        _cash_exp = _cash_expenses_for_window(db, os_store, start_iso, None)
        cash_expenses = _cash_exp.total
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
            # AMOUNT-MATCHED advisory (or None): a manual cash expense that
            # matches a recorded cash refund is probably the same money twice.
            "refund_double_entry_advisory": _refund_double_entry_advisory(
                db, os_store, start_iso, None, cash_refunds
            ),
            # Something booked this period is not paid from the till, so it is
            # not in `cash_expenses` and not in `expected`. Count only, never
            # the amount -- see OFF_TILL_EXPENSE_MESSAGE.
            "off_till_expense_advisory": bool(_cash_exp.excluded_count),
            "off_till_expense_message": (
                OFF_TILL_EXPENSE_MESSAGE if _cash_exp.excluded_count else None
            ),
            # A negative expectation means a cash-in is missing -- never present
            # the resulting "overage" as a verdict.
            "negative_expected_advisory": expected < 0,
            "negative_expected_message": (
                cash_register.NEGATIVE_EXPECTED_MESSAGE if expected < 0 else None
            ),
        }

    return {
        "sessions": sessions,
        "open_session": open_session,
        "expected_preview": expected_preview,
    }


# ============================================================================
# Cash reconciliation summary -- manager-facing console (#7)
# ============================================================================
# A unified, read-only view across BOTH day-close systems so a manager / owner
# can see, per store per day, whether the cash drawer tallied:
#   * cash_register_sessions (CR-...) -- the manual close-by-denomination flow
#     (status CLOSED): counted/expected/variance in RUPEES, by_mode_breakdown.
#   * till_sessions (TILL-...)        -- the BLIND EOD count (status LOCKED):
#     blind_count_paisa/expected_cash_paisa/variance_paisa in PAISA, by_mode.
# Both already compute the variance; this endpoint ONLY reads + normalises them
# into one row shape and applies store-scoping. NO new variance math, NO writes
# (other than the optional manager sign-off below). Variance = counted - expected
# (positive = OVERAGE / drawer over; negative = SHORTAGE / drawer short);
# |variance| <= a small rounding epsilon is BALANCED.

_CASH_RECON_ROLES = (
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "ACCOUNTANT",
)
# Rounding epsilon (rupees) within which a session is treated as BALANCED even
# when a session was closed with tolerance 0 -- guards against sub-paisa float
# noise. A real over/short is always > 1 paisa, so 0.005 never masks one.
_RECON_EPSILON = 0.005

_CASH_RECON_SIGNOFFS = "cash_recon_signoffs"


def _recon_status(variance: float, tolerance: float = 0.0) -> str:
    """Classify a rupee variance into BALANCED / OVERAGE / SHORTAGE. The band is
    the session's own tolerance OR the rounding epsilon, whichever is larger."""
    try:
        v = float(variance or 0)
        band = max(abs(float(tolerance or 0)), _RECON_EPSILON)
    except (TypeError, ValueError):
        return "BALANCED"
    if abs(v) <= band:
        return "BALANCED"
    return "OVERAGE" if v > 0 else "SHORTAGE"


def _store_name_map(db) -> Dict[str, str]:
    """store_id -> store_name (falls back to the id when a store has no name)."""
    out: Dict[str, str] = {}
    try:
        for s in db.get_collection("stores").find(
            {}, {"_id": 0, "store_id": 1, "store_name": 1}
        ):
            sid = s.get("store_id")
            if sid:
                out[sid] = s.get("store_name") or sid
    except Exception:  # noqa: BLE001
        pass
    return out


def _user_name_map(db, user_ids) -> Dict[str, str]:
    """user_id -> display name for the given ids (closed_by / locked_by fallback
    when the session doc didn't already stamp the name). Fail-soft to {}."""
    ids = [u for u in set(user_ids) if u]
    if not ids or db is None:
        return {}
    out: Dict[str, str] = {}
    try:
        for u in db.get_collection("users").find(
            {"$or": [{"user_id": {"$in": ids}}, {"id": {"$in": ids}}]},
            {"_id": 0, "user_id": 1, "id": 1, "full_name": 1, "name": 1, "username": 1},
        ):
            uid = u.get("user_id") or u.get("id")
            if uid:
                out[uid] = u.get("full_name") or u.get("name") or u.get("username") or uid
    except Exception:  # noqa: BLE001
        pass
    return out


def _norm_by_mode(raw) -> Dict[str, Dict[str, float]]:
    """Normalise a by-mode/by-tender breakdown into {MODE: {net, count}} rupees.
    Both engines store {MODE: {collected, refunded, net, count}} -- we keep the
    net + count for the console (the per-tender drill-down)."""
    out: Dict[str, Dict[str, float]] = {}
    if not isinstance(raw, dict):
        return out
    for mode, row in raw.items():
        if not isinstance(row, dict):
            continue
        try:
            net = round(float(row.get("net", 0) or 0), 2)
        except (TypeError, ValueError):
            net = 0.0
        try:
            count = int(row.get("count", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        out[str(mode).upper()] = {"net": net, "count": count}
    return out


@router.get("/cash-reconciliation-summary")
async def cash_reconciliation_summary(
    from_date: Optional[str] = Query(
        None, alias="from", description="Range start (YYYY-MM-DD, inclusive)"
    ),
    to_date: Optional[str] = Query(
        None, alias="to", description="Range end (YYYY-MM-DD, inclusive)"
    ),
    store_id: Optional[str] = Query(None, description="Filter to one store"),
    current_user: dict = Depends(get_current_user),
):
    """Manager-facing cash reconciliation across the close-by-denomination
    (cash_register_sessions, CLOSED) AND the blind-EOD (till_sessions, LOCKED)
    flows for a date range.

    One normalised row per closed session: opening_float, cash_sales,
    cash_refunds, cash_expenses, expected_cash, counted_cash (blind for till
    sessions), variance (counted - expected), variance_status (BALANCED /
    OVERAGE / SHORTAGE), by_mode (per-tender net), closed_by + closed_at.

    Store-scope: STORE_MANAGER / store-level roles see only their own store;
    ADMIN / AREA_MANAGER / ACCOUNTANT / SUPERADMIN see all (or a chosen store).
    Read-only; no variance is recomputed (both engines already did it)."""
    from ..dependencies import resolve_store_scope

    roles = set(current_user.get("roles") or [])
    if not (roles & set(_CASH_RECON_ROLES)):
        raise HTTPException(
            status_code=403, detail="Manager / finance roles required"
        )

    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Resolve the store filter through the canonical guard: an explicit store_id
    # is access-checked; an omitted one means all-stores for admins but the
    # caller's OWN store for a store-scoped role (no all-stores by omission).
    scoped_store = resolve_store_scope(store_id, current_user)

    # Default the range to the current IST month so the console is never empty
    # on first load.
    today = ist_today()
    start_day = (from_date or today.replace(day=1).isoformat())[:10]
    end_day = (to_date or today.isoformat())[:10]

    store_names = _store_name_map(db)

    rows: List[dict] = []
    pending_user_ids: List[str] = []

    # --- 1) Manual close-by-denomination sessions (rupees) -----------------
    cr_match: Dict = {"status": "CLOSED"}
    if scoped_store:
        cr_match["store_id"] = scoped_store
    try:
        cr_cursor = db.get_collection(_CASH_SESSIONS).find(cr_match, {"_id": 0})
        cr_sessions = list(cr_cursor)
    except Exception:  # noqa: BLE001
        cr_sessions = []

    for s in cr_sessions:
        # The "business day" is the close date (these sessions have no
        # session_date field). Fall back to opened_at.
        # BUG-104, VALUE rule via parse-then-shift (the _credit_note_date_ist
        # shape): closed_at/opened_at are written by _iso_now() ==
        # datetime.utcnow().isoformat() -- a NAIVE-UTC ISO STRING, whose
        # frame is known from the writer, so parse it to the instant FIRST
        # and then take the IST day. Slicing [:10] read the UTC day: a till
        # closed after midnight IST (00:00-05:30) filed under YESTERDAY's
        # business day AND was range-filtered against the operator's IST
        # start/end days in the wrong frame. Unparseable junk falls back to
        # the old first-10-chars behaviour.
        closed_at = s.get("closed_at") or s.get("opened_at")
        sess_day = _ist_day_face(closed_at)
        if sess_day and not (start_day <= sess_day <= end_day):
            continue
        expected = round(float(s.get("expected", 0) or 0), 2)
        # A DRAWER NOBODY COUNTED HAS NO FIGURE HERE EITHER. `counted` is None
        # on such a close record; `float(None or 0)` would have rebuilt the
        # fabricated zero -- and its full-day shortfall -- one screen further
        # along, which is where a manager actually reads it.
        raw_counted = s.get("counted")
        counted = None if raw_counted is None else round(float(raw_counted or 0), 2)
        variance = None if counted is None else round(counted - expected, 2)
        tol = float(s.get("tolerance", 0) or 0)
        closed_by = s.get("closed_by")
        if closed_by and not s.get("closed_by_name"):
            pending_user_ids.append(closed_by)
        rows.append(
            {
                "session_id": s.get("session_id"),
                "source": "CASH_REGISTER",
                "store_id": s.get("store_id"),
                "store_name": store_names.get(s.get("store_id"), s.get("store_id")),
                "session_date": sess_day,
                "shift": s.get("shift"),
                "opening_float": round(float(s.get("opening_float", 0) or 0), 2),
                "cash_sales": round(float(s.get("cash_sales", 0) or 0), 2),
                "cash_refunds": round(float(s.get("cash_refunds", 0) or 0), 2),
                "cash_expenses": round(float(s.get("cash_expenses", 0) or 0), 2),
                "bank_deposit": round(float(s.get("bank_deposit", 0) or 0), 2),
                "expected_cash": expected,
                "counted_cash": counted,
                "blind": False,
                "variance": variance,
                # Same guard as the close handler: a withheld NEGATIVE_EXPECTED
                # verdict must not be re-derived into a phantom OVERAGE here.
                # (Otherwise _recon_status is authoritative for this grid -- it
                # emits the grid's OVERAGE/SHORTAGE vocabulary, which the totals
                # below bucket on; the session's own OVER/SHORT wording is not
                # interchangeable with it.)
                "variance_status": (
                    cash_register.NOT_COUNTED
                    if counted is None
                    else cash_register.NEGATIVE_EXPECTED
                    if s.get("negative_expected_advisory")
                    else _recon_status(variance, tol)
                ),
                "tolerance": round(abs(tol), 2),
                "by_mode": _norm_by_mode(s.get("by_mode_breakdown")),
                # Both row kinds are now on ONE basis so the grid's tender
                # column means the same thing in every row. Legacy sessions
                # closed before the change carry a payments-only breakdown.
                "by_mode_basis": s.get("by_mode_basis") or "PAYMENTS_ONLY_LEGACY",
                "refund_double_entry_advisory": s.get("refund_double_entry_advisory"),
                # Carried from the close record, not recomputed. The row's own
                # arithmetic (opening + sales - refunds - expenses = expected)
                # must keep tying out, and `expected` here is the figure the
                # closer actually counted money against on the day -- restating
                # a signed-off drawer from today's rules would be worse than
                # the thing it fixes. New closes are already payroll-free at
                # source; sessions closed before this change keep their number
                # and now say so.
                "off_till_expense_advisory": bool(s.get("off_till_expense_advisory")),
                "negative_expected_advisory": bool(s.get("negative_expected_advisory")),
                "closed_by": closed_by,
                "closed_by_name": s.get("closed_by_name"),
                "closed_at": closed_at,
                # The shared till session this close landed on, if any. Used
                # below to make sure ONE counted drawer is ONE row here.
                "till_session_id": s.get("till_session_id"),
            }
        )

    # --- 2) Blind EOD (Z-Read) sessions, paisa -> rupees -------------------
    till_match: Dict = {"status": "LOCKED"}
    if scoped_store:
        till_match["store_id"] = scoped_store
    try:
        till_cursor = db.get_collection("till_sessions").find(till_match, {"_id": 0})
        till_sessions = list(till_cursor)
    except Exception:  # noqa: BLE001
        till_sessions = []

    def _p2r(paisa) -> float:
        try:
            return round(int(paisa or 0) / 100.0, 2)
        except (TypeError, ValueError):
            return 0.0

    for s in till_sessions:
        sess_day = str(s.get("session_date") or "")[:10]
        if sess_day and not (start_day <= sess_day <= end_day):
            continue
        expected = _p2r(s.get("expected_cash_paisa"))
        # Same rule on the Z-Read side: `blind_count_paisa` stays None until
        # somebody counts, so an uncounted drawer reports no figure, not zero.
        counted = (
            None if s.get("blind_count_paisa") is None
            else _p2r(s.get("blind_count_paisa"))
        )
        variance = None if counted is None else round(counted - expected, 2)
        tol = _p2r(s.get("tolerance_paisa"))
        locked_by = s.get("locked_by")
        if locked_by and not s.get("locked_by_name"):
            pending_user_ids.append(locked_by)
        rows.append(
            {
                "session_id": s.get("session_id"),
                "source": "BLIND_EOD",
                "store_id": s.get("store_id"),
                "store_name": store_names.get(s.get("store_id"), s.get("store_id")),
                "session_date": sess_day,
                "shift": s.get("shift"),
                "opening_float": _p2r(s.get("opening_float_paisa")),
                # cash_sales is now GROSS collected; cash_refunds is the recorded
                # cash refunds auto-deducted (distinct from the manual cash
                # payouts in cash_expenses) so this row is comparable to the
                # cash-register rows above and no sales silently vanish.
                "cash_sales": _p2r(s.get("cash_sales_paisa")),
                "cash_refunds": _p2r(s.get("cash_refunds_paisa")),
                "cash_expenses": _p2r(s.get("cash_payouts_paisa")),
                "bank_deposit": 0.0,
                "expected_cash": expected,
                "counted_cash": counted,
                "blind": True,
                "variance": variance,
                # Trust the engine's stored status when present (it used the
                # configured tolerance); else classify with the same band.
                "variance_status": (
                    cash_register.NOT_COUNTED
                    if counted is None
                    else s.get("variance_status") or _recon_status(variance, tol)
                ),
                "tolerance": tol,
                "by_mode": _norm_by_mode(s.get("by_mode")),
                "by_mode_basis": "NET_OF_RECORDED_REFUNDS",
                "refund_double_entry_advisory": (
                    {"reason": "CO_OCCURRENCE"}
                    if s.get("refund_double_entry_advisory")
                    else None
                ),
                # Since the 2026-08-25 ruling `cash_payouts_paisa` is AUTO-
                # PULLED from the `expenses` collection at blind-submit, and
                # the session stores whether a payroll-shaped head was
                # deliberately left out. Older sessions (hand-keyed payouts)
                # never stored the flag -> False, as before.
                "off_till_expense_advisory": bool(s.get("off_till_expense_advisory")),
                "negative_expected_advisory": bool(s.get("negative_expected_advisory")),
                "closed_by": locked_by,
                "closed_by_name": s.get("locked_by_name"),
                "closed_at": s.get("locked_at"),
                "zread_number": s.get("zread_number"),
            }
        )

    # --- 2b) ONE COUNTED DRAWER, ONE ROW -----------------------------------
    # Since both close screens land on the SAME till session, a drawer closed
    # from Finance > Cash Register and then locked as a Z-Read would otherwise
    # appear TWICE in this grid -- the same money counted once, reported as two
    # days' worth. Where a cash-register row names a till session that is also
    # in this grid, the till row is the survivor (it is the shared record, and
    # it carries the Z-Read number); the cash-register row is folded into it so
    # nothing about who closed it is lost.
    till_row_ids = {
        r["session_id"] for r in rows if r.get("source") == "BLIND_EOD" and r.get("session_id")
    }
    if till_row_ids:
        by_id = {r["session_id"]: r for r in rows if r.get("source") == "BLIND_EOD"}
        survivors: List[dict] = []
        for r in rows:
            linked = r.get("till_session_id")
            if r.get("source") == "CASH_REGISTER" and linked in till_row_ids:
                by_id[linked]["also_closed_from"] = r.get("session_id")
                by_id[linked]["also_closed_from_source"] = "CASH_REGISTER"
                continue
            survivors.append(r)
        rows = survivors

    # Resolve any missing closer names in one batch, then backfill.
    if pending_user_ids:
        name_map = _user_name_map(db, pending_user_ids)
        for r in rows:
            if not r.get("closed_by_name") and r.get("closed_by"):
                r["closed_by_name"] = name_map.get(r["closed_by"], r["closed_by"])

    # Attach any manager sign-off marker (reviewed/audited) for each session.
    try:
        sess_ids = [r["session_id"] for r in rows if r.get("session_id")]
        if sess_ids:
            signoffs = {
                d.get("session_id"): d
                for d in db.get_collection(_CASH_RECON_SIGNOFFS).find(
                    {"session_id": {"$in": sess_ids}}, {"_id": 0}
                )
            }
            for r in rows:
                so = signoffs.get(r.get("session_id"))
                r["signoff"] = (
                    {
                        "reviewed": True,
                        "reviewed_by": so.get("reviewed_by"),
                        "reviewed_by_name": so.get("reviewed_by_name"),
                        "reviewed_at": so.get("reviewed_at"),
                        "note": so.get("note"),
                    }
                    if so
                    else {"reviewed": False}
                )
    except Exception:  # noqa: BLE001
        for r in rows:
            r.setdefault("signoff", {"reviewed": False})

    # Newest day first; within a day, newest close first.
    rows.sort(
        key=lambda r: (r.get("session_date") or "", str(r.get("closed_at") or "")),
        reverse=True,
    )

    # Per-range totals.
    def _sum(field: str) -> float:
        return round(sum(float(r.get(field, 0) or 0) for r in rows), 2)

    over = [r for r in rows if r.get("variance_status") == "OVERAGE"]
    short = [r for r in rows if r.get("variance_status") == "SHORTAGE"]
    balanced = [r for r in rows if r.get("variance_status") == "BALANCED"]
    totals = {
        "sessions": len(rows),
        "balanced": len(balanced),
        "overage": len(over),
        "shortage": len(short),
        "opening_float": _sum("opening_float"),
        "cash_sales": _sum("cash_sales"),
        "cash_refunds": _sum("cash_refunds"),
        "cash_expenses": _sum("cash_expenses"),
        "expected_cash": _sum("expected_cash"),
        "counted_cash": _sum("counted_cash"),
        "variance": _sum("variance"),
        "overage_amount": round(sum(r["variance"] for r in over), 2),
        "shortage_amount": round(sum(abs(r["variance"]) for r in short), 2),
    }

    return {
        "from": start_day,
        "to": end_day,
        "store_id": scoped_store,
        "rows": rows,
        "totals": totals,
    }


class CashReconSignoff(BaseModel):
    session_id: str
    source: str = "CASH_REGISTER"  # CASH_REGISTER | BLIND_EOD (informational)
    note: Optional[str] = None


@router.post("/cash-reconciliation-signoff")
async def cash_reconciliation_signoff(
    body: CashReconSignoff,
    current_user: dict = Depends(get_current_user),
):
    """Manager SIGN-OFF: mark a reconciled session as reviewed + audited.

    Idempotent upsert into ``cash_recon_signoffs`` keyed on session_id. The
    actor must have access to the session's store. Manager / finance roles only.
    This is the lightweight review marker the console surfaces per row; it does
    NOT change any variance figure (the day-close lock is the source of truth)."""
    roles = set(current_user.get("roles") or [])
    if not (roles & set(_CASH_RECON_ROLES)):
        raise HTTPException(
            status_code=403, detail="Manager / finance roles required"
        )

    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Locate the underlying session in either collection to store-scope the actor
    # (cross-store IDOR guard) before recording the sign-off.
    session = None
    for coll_name in (_CASH_SESSIONS, "till_sessions"):
        try:
            doc = db.get_collection(coll_name).find_one(
                {"session_id": body.session_id}, {"_id": 0}
            )
        except Exception:  # noqa: BLE001
            doc = None
        if doc:
            session = doc
            break
    if session is None:
        raise HTTPException(status_code=404, detail="Reconciliation session not found")

    validate_store_access(session.get("store_id") or "", current_user)

    now = _iso_now()
    reviewer = current_user.get("name") or current_user.get("full_name")
    record = {
        "session_id": body.session_id,
        "source": body.source,
        "store_id": session.get("store_id"),
        "reviewed": True,
        "reviewed_by": current_user.get("user_id"),
        "reviewed_by_name": reviewer,
        "reviewed_at": now,
        "note": body.note,
    }
    try:
        db.get_collection(_CASH_RECON_SIGNOFFS).update_one(
            {"session_id": body.session_id},
            {"$set": record},
            upsert=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Cash reconciliation sign-off failed")
        raise HTTPException(
            status_code=500,
            detail="Could not record the sign-off - try again or contact support",
        )

    # Audit the review (fail-soft; never undoes the sign-off write).
    try:
        from api.dependencies import get_audit_repository

        repo = get_audit_repository()
        if repo is not None:
            repo.create(
                {
                    "action": "cash_recon.signoff",
                    "entity_type": "cash_recon_session",
                    "entity_id": body.session_id,
                    "store_id": session.get("store_id"),
                    "user_id": current_user.get("user_id"),
                    "user_name": reviewer,
                    "severity": "INFO",
                    "source": "finance",
                    "after_state": {"reviewed": True, "note": body.note},
                }
            )
    except Exception:  # noqa: BLE001
        pass

    return {"ok": True, "signoff": record}


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
    # Resolve store_id -> human store name via the shared, fail-soft resolver
    # (owner backlog #4 / #780). The stores collection keys its display name on
    # ``store_name`` (then ``store_code``); the earlier inline lookup read a
    # non-existent ``name`` field, so the Hub "Monthly target" rows fell back to
    # the raw store_id (a UUID). store_name_map reads the correct fields.
    name_by_id: Dict[str, str] = name_resolver.store_name_map(db)

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
