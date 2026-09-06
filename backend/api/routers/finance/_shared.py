"""Shared finance helpers: the two routers, the DB handle, the money/status
constants and every helper used by more than one finance sub-module.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

# This is the ORIGINAL finance.py import block, kept whole on purpose. Several
# of these names are not referenced in THIS file any more (the sub-modules
# import what they need directly), but they are part of the module surface the
# single-file router exposed: __init__.py re-exports them so
# ``from api.routers.finance import cache / till_service / is_online_store``
# and the tests that monkeypatch ``finance.now_ist`` keep working unchanged.
# Do not "clean up" the unused-looking ones -- that is the compatibility layer.
import calendar
import csv
import io
import logging
import uuid
from datetime import datetime, timedelta, date, timezone
from ...utils.ist import (
    now_ist,
    now_ist_naive,
    ist_date_str,
    ist_today,
    ist_day_start_utc,
    fy_start_year_ist,
)
from ...utils.online_gst import order_interstate_flag
from typing import Any, Optional, List, Dict, NamedTuple
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    UploadFile,
    File,
    Form,
    Body,
)
from fastapi.responses import Response
from pydantic import BaseModel, Field
from ..auth import get_current_user
from ...dependencies import validate_store_access
from ...services import ap_engine, cashflow, itc_reconcile, cash_register, csv_safe
from ...services.stores_util import is_online_store
from ...services import survival_cashflow
from ...services.cost_mask import can_see_cost
from ...services.salary_visibility import (
    SALARY_RESTRICTED_MESSAGE,
    is_payroll_shaped_expense,
    is_salary_admin,
    normalise_expense_category,
)
from ...services.cache import cache
from ...services import ticker_service, policy_engine
from ...services import je_service
from ...services import cash_denominations as cash_denom
from ...services import eod_tally as till_service
from ...services import name_resolver

# Mounted at /api/v1/finance in main.py. NO internal prefix: the earlier
# prefix="/finance" double-prefixed every path to /api/v1/finance/finance/*,
# which the frontend financeApi (it calls /finance/*) never hit — so the whole
# Finance dashboard 404'd. Dropping it aligns the routes with the client.
# __package__ == "api.routers.finance", so the logger keeps the exact name it
# had while this was one module (log filters and caplog assertions still match).
logger = logging.getLogger(__package__)

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
    "CANCELLED",
    "DRAFT",
    "cancelled",
    "draft",
    "HISTORICAL",
    "historical",
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
    from ...services import org_validation as _ov

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


# === Store scoping ===
# (this sat under a "Revenue Tracking" banner while /revenue was the next
# route in the file; the gate is used by most finance aggregations.)


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


# -- Promoted here by the Wave 5 package split ---------------------------
# Each of these is called from a sub-module that registers its routes
# EARLIER than the sub-module the helper used to sit in, so it cannot stay
# there without an import cycle. Bodies are unchanged.


def _require_finance_admin(current_user: dict) -> None:
    """Org-wide financials are owner/accountant material."""
    roles = current_user.get("roles", []) or []
    if not any(r in roles for r in ("SUPERADMIN", "ADMIN", "ACCOUNTANT")):
        raise HTTPException(
            status_code=403, detail="Owner financials require ADMIN / ACCOUNTANT"
        )


def _iso_now() -> str:
    return datetime.utcnow().isoformat()


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
