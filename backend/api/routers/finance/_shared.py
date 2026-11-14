"""Shared finance helpers: the two routers, the DB handle, the money/status
constants and every helper used by more than one finance sub-module.

Moved verbatim out of the 6,604-line api/routers/finance.py (Wave 5
package split): no path, method, dependency, status code, response_model
or default was changed.
"""

from api.utils.event_time import utc_now_iso

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
    to_utc_naive,
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
from ...dependencies import (
    NO_SHOP_DETAIL,
    is_no_shop_scope,
    resolve_store_scope,
    user_store_scope,
    validate_store_access,
)
from ...services import ap_engine, cashflow, itc_reconcile, cash_register, csv_safe
from ...services.stores_util import is_online_store
from ...services import survival_cashflow
from ...services.cost_mask import can_see_cost
# One cost rule for the P&L and every margin report (services/margin_basis):
# cost_at_sale snapshot, then the live catalogue cost_price, else None.
from ...services.margin_basis import (
    cost_by_product as _cost_by_product,
    line_revenue_ex_gst,
    line_unit_cost as _item_cost,
)
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
# What services/margin_basis.order_revenue_ex_gst reads (the bill's taxable
# value) plus the line items COGS reads -- the P&L order fetches.
_ORDER_REVENUE_PROJECTION = {
    "_id": 0,
    "store_id": 1,
    "items": 1,
    "grand_total": 1,
    "total_amount": 1,
    "total": 1,  # legacy bills: grand_total ?? total, as _REVENUE_EXPR
    "round_off": 1,
    "tax": 1,
    "tax_amount": 1,
    "tax_total": 1,
    "taxable": 1,
    "taxable_amount": 1,
}

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
# VOID (deleted in Shopify): a sale only once invoiced or shipped (owner
# ruling 2026-10-22). THE rule lives in api/utils/real_sale -- shared with the
# GST returns so the books and the returns agree; spread it into a query
# (`{**real_sale_match(db), ...}`).
from ...utils.real_sale import (  # noqa: E402
    EXCLUDED_ORDER_STATUSES as _EXCLUDED_ORDER_STATUSES,
    real_sale_match,
)


def _parse_range_dt(s, *, end: bool = False) -> Optional[datetime]:
    """Parse a 'YYYY-MM-DD' (or ISO) query string into a datetime suitable for
    a Mongo range bound on `created_at`.

    Orders persist `created_at` as a BSON *datetime* (BaseRepository
    `_add_timestamps` -> datetime.now()), so a bare 'YYYY-MM-DD' STRING bound
    never matches -- a string-vs-datetime comparison in Mongo silently returns
    nothing. This was the bug behind every date-ranged finance figure reading
    zero. We convert to a datetime so the comparison is apples-to-apples; an
    `end` date-only bound covers the whole final IST day. Returns None when the input is empty / unparseable (caller then
    omits that bound).

    A date-only input ('YYYY-MM-DD') is an IST business day: the bound is IST
    midnight as the naive-UTC instant (ist_day_start_utc), and an ``end`` bound
    is the last microsecond before the NEXT IST midnight. The date-only shape is
    checked FIRST -- on Python 3.11+ datetime.fromisoformat accepts a bare date,
    which once made the IST branch dead and turned every bound into a UTC day
    (an order at 02:00 IST on 1 Nov landed in October's Tally export). An
    offset-bearing ISO input is CONVERTED to naive UTC, never just stripped.
    A naive full ISO datetime is taken as already in the stored frame.
    """
    if s is None:
        return None
    if isinstance(s, datetime):
        return s
    txt = str(s).strip()
    if not txt:
        return None
    if len(txt) <= 10:
        try:
            d = date.fromisoformat(txt)
        except ValueError:
            return None
        if end:
            return ist_day_start_utc(d + timedelta(days=1)) - timedelta(microseconds=1)
        return ist_day_start_utc(d)
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        return None
    return to_utc_naive(dt) if dt.tzinfo is not None else dt


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
                cogs += line_revenue_ex_gst(it) * fallback_rate
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
                cogs += line_revenue_ex_gst(it) * fallback_rate
                estimated_lines += 1
    return round(cogs, 2), estimated_lines, total_lines


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


def _order_is_interstate(
    order, store_state_by_id: dict, customer_state_by_id: dict
) -> bool:
    """Inter-state (IGST) vs intra-state (CGST+SGST) for ONE order -- THE
    place-of-supply rule the tax invoice and the GST returns use
    (reports.gst_base._order_supply -> org_validation.supply_interstate): the
    order's own ``interstate`` stamp wins (OS-008), else state CODES from the
    shop (state_code / state / GSTIN) and the customer (GSTIN, billing
    address, state_code, state); an unknown buyer state is intra-state, as
    the invoice prints. So the Tally voucher, the finance GST cards and the
    cross-check books split every order exactly as GSTR-1 / GSTR-3B file it.

    The maps are _store_state_map / _customer_state_map (id -> the fields the
    rule reads); a bare state string, from an older caller, is read as the
    doc's `state`. Callers that project order fields must include
    ``interstate`` and ``place_of_supply``."""
    from ..reports.gst_base import _order_supply

    def _doc(v):
        return v if isinstance(v, dict) else {"state": v or ""}

    return _order_supply(
        order,
        _doc(store_state_by_id.get(order.get("store_id"))),
        _doc(customer_state_by_id.get(order.get("customer_id"))),
    )[0]


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


def _store_state_map(db, *, strict=False) -> dict:
    """store_id -> the shop fields the place-of-supply rule reads (state_code,
    state, GSTIN -- org_validation.shop_state_code), for _order_is_interstate."""
    out: dict = {}
    try:
        for s in db.get_collection("stores").find(
            {}, {"_id": 0, "store_id": 1, "state": 1, "state_code": 1, "gstin": 1}
        ):
            if s.get("store_id"):
                out[s["store_id"]] = s
    except Exception:
        if strict:
            raise
    return out


def _customer_state_map(db, *, strict=False) -> dict:
    """customer_id -> the customer fields the place-of-supply rule reads
    (GSTIN, billing address, state_code, state --
    org_validation.customer_state_code), for _order_is_interstate."""
    from ..reports.gst_base import CUSTOMER_SUPPLY_FIELDS

    out: dict = {}
    try:
        for c in db.get_collection("customers").find(
            {}, {"_id": 0, **CUSTOMER_SUPPLY_FIELDS}
        ):
            cid = c.get("customer_id")
            if cid:
                out[cid] = c
    except Exception:
        if strict:
            raise
    return out


def pnl_by_category(orders, cost_by_product: dict, variance_by_category: Optional[dict] = None,
                    returns_by_category: Optional[dict] = None) -> list:
    """Revenue + COGS per product category (item_type), from order line items.
    Pure. Prefers item.cost_at_sale snapshot; 60%-of-line COGS fallback when
    a product's cost is unknown. Includes cogs_is_estimated flag per category
    so the UI can mark estimates (SYSTEM_INTENT: never show fabricated numbers
    without flagging them). `variance_by_category`: the cost-of-goods
    variance of pieces sold before their bill (product_cost.variance_by
    keyed "item_type": the sold lines' item_type-or-category, as here),
    added to each category's COGS. `returns_by_category`: the completed
    customer returns of the period (customer_return_adjustments by
    "category", RUN-04), their taxable value off revenue and the cost they
    give back off COGS, as on GET /pnl."""
    acc: dict = {}
    for cat, v in (variance_by_category or {}).items():
        acc.setdefault(cat or "OTHER", {"revenue": 0.0, "cogs": 0.0, "estimated_lines": 0})["cogs"] += v
    for cat, r in (returns_by_category or {}).items():
        d = acc.setdefault(cat or "OTHER", {"revenue": 0.0, "cogs": 0.0, "estimated_lines": 0})
        d["revenue"] -= r["taxable"]
        d["cogs"] -= r["cogs_reversal"]
    for o in orders:
        for it in o.get("items") or []:
            cat = it.get("item_type") or it.get("category") or "OTHER"
            qty = it.get("quantity", 1) or 1
            # Net of GST, as COGS is. (This read `total`, which no order line
            # carries -- every category's revenue was 0.)
            rev = line_revenue_ex_gst(it)
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


# -- Promoted here by the Wave 5 package split ---------------------------
# Each of these is called from a sub-module that registers its routes
# EARLIER than the sub-module the helper used to sit in, so it cannot stay
# there without an import cycle. Bodies are unchanged.


def _require_finance_admin(current_user: dict) -> None:
    """Org-wide financials are owner/accountant material: the accounts roles,
    asked of services/cost_mask "payables" (AP_ROLES) -- the one rule the
    vendor ledger and /finance/vendor-payments answer. These routes read
    supplier money (AP aging, bills, vendor payments, ITC, Tally), so they
    never keep a role list of their own."""
    if not can_see_cost(current_user, "payables"):
        raise HTTPException(
            status_code=403, detail="Owner financials require ADMIN / ACCOUNTANT"
        )


def _require_org_finance(
    current_user: dict, store_id: Optional[str] = None, entity_id: Optional[str] = None
) -> Optional[str]:
    """THE gate AND the shop of the accounts' book views (the Tally sales JV
    and B2B invoice book, the GST cross-check and its sign-off, the per-entity
    GST reconciliation, bank statements): the accounts roles
    (_require_finance_admin), then the one shop rule (resolve_store_scope),
    whose answer it returns as the shop to read:

    - ADMIN / SUPERADMIN: every shop (None), or the one they name;
    - an ACCOUNTANT assigned to a shop: ONLY that shop (owner 2026-10-18 --
      company-wide needs an admin); a shop he names must be his (403);
    - a non-admin login with NO shop is refused (owner ruling 2026-10-12, R4):
      these views have no empty, one-shop form to hand it -- a blank JV or
      reconciliation would read as a nil period -- so it is refused in the
      owner's words, as GSTR-3B is (reports/gstr3b._return_shop), named shop
      or not;
    - a company (`entity_id`) an accountant names must be his shop's (403):
      his shop is never relabelled under another company's name."""
    _require_finance_admin(current_user)
    shop = resolve_store_scope(store_id, current_user)
    if is_no_shop_scope(shop):
        raise HTTPException(status_code=403, detail=NO_SHOP_DETAIL)
    if entity_id and shop and not user_store_scope(current_user)[0]:
        s2e, _ = _store_maps(_get_db())
        if s2e.get(shop) != entity_id:
            raise HTTPException(status_code=403, detail="That company is not your shop's.")
    return shop


def _iso_now() -> str:
    return utc_now_iso()


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


# -- Supplier services bills and customer returns in the P&L ----------------
# Bill states that never were (or no longer are) a cost. Stored header bills
# are OUTSTANDING / PARTIAL / PAID; the rest are tolerated legacy spellings.
_NOT_A_COST_BILL_STATUSES = ("DRAFT", "CANCELLED", "CANCELED", "VOID", "VOIDED", "REJECTED")


def services_bill_costs(db, store_ids=None, from_date=None, to_date=None) -> List[dict]:
    """Supplier SERVICES bills (rent, repairs, freight, ...) as P&L costs
    (audit L3-04): one row ``{store_id, head, amount}`` per bill dated in the
    range (``bill_date``, a 'YYYY-MM-DD' string, like ``expense_date``).

    A services bill is the same cost whether the accounts team records it as
    an Expense or as a supplier bill, so it is recognised when it is BOOKED,
    as an approved expense is -- paying it later is a cash event, not a second
    cost. Its value is the taxable amount; its GST too when that GST is NOT
    input credit (17(5) tick or unregistered supplier: the CA's 2026-10-23
    answer, unclaimed GST is cost). Goods bills (a goods receipt, or
    bill_kind GOODS / unstated) are stock, already costed through COGS when
    the pieces sell, and an inter-shop transfer mirror bill is never a cost.
    A supplier credit (debit note) against such a bill lowers its cost in the
    credit's own period, by the bill's cost share of the credit (cost /
    bill total: a credit's input-credit GST is not cost). Fail-soft: [] on a
    read error, as the other P&L legs."""
    match: dict = {
        "bill_kind": ap_engine.BILL_KIND_SERVICES,
        "status": {"$nin": list(_NOT_A_COST_BILL_STATUSES)},
        "grn_id": {"$in": [None, ""]},
        "source_transfer_id": {"$in": [None, ""]},
        # Goods typed onto a services bill are stock, costed through COGS when
        # they sell: expensed here they were charged twice.
        "expense_type": {"$ne": "STOCK"},
    }
    if store_ids is not None:
        match["store_id"] = {"$in": list(store_ids)}
    days: dict = {}
    if from_date:
        days["$gte"] = str(from_date)[:10]
    if to_date:
        days["$lte"] = str(to_date)[:10]
    labels = {key: label for key, label, _blocked in ap_engine.EXPENSE_TYPES}

    def _cost(b) -> float:
        amount = float(b.get("taxable_amount") or 0)
        if b.get("itc_eligible") is False:
            amount += float(b.get("tax_amount") or 0)
        return amount

    def _row(b, amount) -> dict:
        head = labels.get(b.get("expense_type") or "", "Other business expense")
        return {"store_id": b.get("store_id"), "head": f"Supplier bills: {head}", "amount": round(amount, 2)}

    rows: List[dict] = []
    try:
        bills = db.get_collection("vendor_bills")
        for b in bills.find({**match, **({"bill_date": days} if days else {})}, {"_id": 0}):
            rows.append(_row(b, _cost(b)))
        credits = list(db.get_collection("vendor_debit_notes").find(
            {"bill_id": {"$nin": [None, ""]}, **({"date": days} if days else {})},
            {"_id": 0, "bill_id": 1, "amount": 1},
        ))
        if credits:
            credited = {b["bill_id"]: b for b in bills.find(
                {**match, "bill_id": {"$in": list({c["bill_id"] for c in credits})}}, {"_id": 0})}
            for c in credits:
                b = credited.get(c["bill_id"])
                total = float((b or {}).get("total_amount") or 0)
                if b is None or total <= 0:
                    continue
                rows.append(_row(b, -float(c.get("amount") or 0) * _cost(b) / total))
    except Exception:  # noqa: BLE001
        logger.warning("[PNL] services bills could not be read", exc_info=True)
        return []
    return rows


def customer_return_adjustments(db, cost_by_product: dict, store_ids=None, from_date=None, to_date=None,
                                by: str = "store_id") -> Dict[str, dict]:
    """What COMPLETED customer returns take back off the P&L in the range
    (audit RUN-04): ``{gross, taxable, tax, cogs_reversal, count}`` per
    store_id, or with ``by="category"`` per product category (the sale line's
    item_type-or-category, as pnl_by_category: the credit note split across
    its lines by their value, each piece's cost to its own line), so the store
    and category P&Ls add up to the statement.

    Dated by the return (its credit note), never by the original sale: a
    return in a later month reduces THAT month, and the sale's own month is
    left as it was. The money is the return's own ``gst_breakup`` -- the same
    figures GSTR-3B nets off output tax, whatever the refund tender (cash, UPI,
    card or store credit).

    Only a return whose order is still a sale in the BOOKS (utils/real_sale,
    the rule the P&L reads its orders by): an order the books already drop --
    cancelled (an invoiced advance refunded through its whole-invoice credit
    note, a Shopify order imported cancelled and refunded), refund completed,
    a pre-IMS import, or no order on record -- adds no revenue, so its credit
    note must not be taken off a second time.

    The cost of goods comes back for a piece that really went back on sale,
    at the cost the original sale line was charged at (cost_at_sale, else the
    live cost, else the same 60% estimate the sale used), so one piece is
    never costed twice. It is keyed on the physical UNIT, so one piece gives
    its cost back once whichever door moved it (PR #1423 review: a Shopify
    refund whose unit Goods back put back before the confirm was given back
    by its lines AND by its unit). Each give-back is dated when the unit
    FIRST went back, and never before the return itself, so an earlier month
    is never changed: a unit the return's restock put back (restock_stock_ids,
    a partial restock still waiting for its retry too), by its own restock
    audit row (stock_audit RETURN_RESTOCK for this return), else the retry
    (restock_completed_at), else the return; a piece the return set aside in
    quarantine, by its first release back to sale (item_events quarantine.out
    -> AVAILABLE, the ledger both release doors write); a Shopify refund's
    unit put back through Goods back (its review row's goods_landed, one
    entry per landed unit of every press, a partial one too; a legacy row's
    goods_restock by goods_back_at). A set-aside piece written off or sent to
    the supplier stays in the cost of goods. A HISTORICAL order (our Shopify
    order-history import; IMS never held its stock, no unit ever goes back)
    gives back the cost its sale recorded with a credit note that brought
    goods back, at the note's date (decisions D42(c), D45(b)): its goods
    lines; never a lines-less note (the whole-order note made from
    amount-only refunds included), a goodwill or shipping refund, or a
    Shopify line not restocked unless Goods back brought it back.
    Fail-soft: {} on a read error."""
    lo = _parse_range_dt(from_date, end=False)
    hi = _parse_range_dt(to_date, end=True)

    def _in_range(when) -> bool:
        return when is not None and not (lo is not None and when < lo) and not (hi is not None and when > hi)

    match: dict = {"status": {"$in": ["COMPLETED", "completed"]}}
    if store_ids is not None:
        match["store_id"] = {"$in": list(store_ids)}
    out: Dict[str, dict] = {}
    try:
        from ...utils.event_time import parse_event_utc_naive
        from ...utils.real_sale import is_real_sale

        def _restocked_at(ret, when):
            return max(when, parse_event_utc_naive(ret.get("restock_completed_at")) or when)

        # ponytail: the date tests run in Python because returns store
        # created_at as ISO strings AND BSON dates; returns are few.
        rets = [r for r in db.get_collection("returns").find(match, {"_id": 0})
                if parse_event_utc_naive(r.get("created_at")) is not None]
        # Goods back on a Shopify refund (stamped on the review row only):
        # every unit a press landed, with when it landed -- goods_landed (each
        # press, a partial one too), else a legacy row's goods_restock.
        refund_ids = [r["shopify_refund_id"] for r in rets if r.get("shopify_refund_id")]
        goods: Dict[str, list] = {}
        if refund_ids:
            for row in db.get_collection("shopify_refund_review").find(
                {"shopify_refund_id": {"$in": refund_ids},
                 "$or": [{"goods_restock.restock_applied": True}, {"goods_landed.0": {"$exists": True}}]},
                {"_id": 0, "shopify_refund_id": 1, "goods_back_at": 1, "goods_restock": 1, "goods_landed": 1},
            ):
                at = parse_event_utc_naive(row.get("goods_back_at"))
                rs = row.get("goods_restock") or {}
                landed = [(sid, at) for sid in (rs.get("restock_stock_ids") or [])
                          if rs.get("restock_applied") and at is not None]
                landed += [(e.get("stock_id"), parse_event_utc_naive(e.get("at")))
                           for e in row.get("goods_landed") or [] if isinstance(e, dict)]
                goods.setdefault(row["shopify_refund_id"], []).extend(landed)
        # When each piece a return's restock put back landed, whichever door
        # ran it (the return, its retry, Goods back): the per-unit audit row
        # the one restock writes once the unit is back on sale
        # (returns._audit_stock_transition), keyed on the return or refund it
        # was for. It also finds a landed piece whose id the door then failed
        # to save on the return or review row.
        # ponytail: scans stock_audit by return id (no index); add one on
        # return_id if the collection grows large.
        keys = list({k for r in rets for k in (r.get("return_id"), r.get("shopify_refund_id")) if k})
        landed_at: Dict[tuple, datetime] = {}
        audited: Dict[str, list] = {}
        if keys:
            for ev in db.get_collection("stock_audit").find(
                {"return_id": {"$in": keys}, "source": "RETURN_RESTOCK", "new_status": "AVAILABLE"},
                {"_id": 0, "stock_id": 1, "return_id": 1, "at": 1},
            ):
                at = parse_event_utc_naive(ev.get("at"))
                key = (ev.get("stock_id"), ev.get("return_id"))
                if at is not None and ev.get("stock_id") and (key not in landed_at or at < landed_at[key]):
                    landed_at[key] = at
                    audited.setdefault(ev.get("return_id"), []).append(ev.get("stock_id"))
        picked = []  # (return, when, taxable, tax, gross, its own credit note in range)
        for ret in rets:
            when = parse_event_utc_naive(ret.get("created_at"))
            gb = ret.get("gst_breakup") if isinstance(ret.get("gst_breakup"), dict) else {}
            taxable = float(gb.get("taxable") or 0)
            tax = float(gb.get("tax") or 0)
            noted = _in_range(when) and (taxable > 0 or tax > 0)
            # Each unit is range-tested on its own below.
            if (noted or ret.get("quarantined_stock_ids") or ret.get("restock_stock_ids")
                    or goods.get(ret.get("shopify_refund_id"))
                    or audited.get(ret.get("return_id")) or audited.get(ret.get("shopify_refund_id"))):
                picked.append((ret, when, taxable, tax, float(gb.get("gross") or (taxable + tax)), noted))
        order_ids = list({r[0].get("order_id") for r in picked if r[0].get("order_id")})
        orders = {
            o["order_id"]: o
            for o in db.get_collection("orders").find(
                {"order_id": {"$in": order_ids}},
                {"_id": 0, "order_id": 1, "items": 1, "status": 1, "advance_refund_status": 1,
                 "advance_refund_credit_note": 1, "invoice_number": 1, "status_before_void": 1,
                 "historical": 1},
            )
        } if order_ids else {}
        picked = [p for p in picked if p[0].get("order_id") in orders and is_real_sale(orders[p[0]["order_id"]])]
        # The first release back to sale of each set-aside piece.
        held = {sid for p in picked for sid in (p[0].get("quarantined_stock_ids") or [])}
        releases: Dict[str, list] = {}
        if held:
            for ev in db.get_collection("item_events").find(
                {"stock_id": {"$in": list(held)}, "event_type": "quarantine.out", "to_state": "AVAILABLE"},
                {"_id": 0, "stock_id": 1, "at": 1},
            ):
                at = parse_event_utc_naive(ev.get("at"))
                if at is not None:
                    releases.setdefault(ev["stock_id"], []).append(at)
        units = db.get_collection("stock_units")
        empty = {"gross": 0.0, "taxable": 0.0, "tax": 0.0, "cogs_reversal": 0.0, "count": 0}
        for ret, when, taxable, tax, gross, noted in picked:
            lines = orders[ret["order_id"]].get("items") or []
            by_item = {str(i.get("item_id")): i for i in lines if i.get("item_id")}
            by_product = {str(i.get("product_id")): i for i in lines if i.get("product_id")}
            items = ret.get("items") or []
            # A note stamped with no lines (RET-HIST) is the order's lines,
            # whole, for its category split.
            whole = [{"order_item_id": li.get("item_id"), "product_id": li.get("product_id"),
                      "return_qty": float(li.get("quantity", 1) or 1),
                      "unit_price": line_revenue_ex_gst(li) / float(li.get("quantity", 1) or 1)}
                     for li in lines]
            split = items or ret.get("goods_lines") or whole

            def _line(it, by_item=by_item, by_product=by_product):
                return by_item.get(str(it.get("order_item_id"))) or by_product.get(str(it.get("product_id")))

            def _key(it, ret=ret, _line=_line) -> str:
                if by != "category":
                    return ret.get("store_id")
                line = _line(it) or {}
                return line.get("item_type") or line.get("category") or "OTHER"

            def _piece_cost(it, _line=_line) -> float:
                line = _line(it)
                if line is None:
                    return 0.0
                cost = _item_cost(line, cost_by_product)
                if cost is None:  # the sale's own 60% estimate, per piece
                    cost = line_revenue_ex_gst(line) * 0.6 / float(line.get("quantity", 1) or 1)
                return cost

            def _unit_item(sid, among):
                # The unit carries its product; the return line is found by it.
                pid = str((units.find_one({"stock_id": sid}, {"_id": 0, "product_id": 1}) or {}).get("product_id"))
                return next((i for i in among if str(i.get("product_id")) == pid),
                            among[0] if among else {"product_id": pid})

            reversal: Dict[str, float] = {}

            def _give_back(it, qty=1.0, reversal=reversal, _key=_key, _piece_cost=_piece_cost):
                reversal[_key(it)] = reversal.get(_key(it), 0.0) + _piece_cost(it) * qty

            if orders[ret["order_id"]].get("historical"):
                # Decision D42(c): IMS never held an imported Shopify order's
                # stock, so a credit note that brought its goods back gives
                # back the cost the sale recorded, at the note's date, once.
                # D45(b): only such a note -- its goods lines (the live
                # refund's, or those the importer stamped as goods_lines). A
                # lines-less note (the whole-order orderfull: note made from
                # amount-only refunds, an amount-only, goodwill or shipping
                # refund), a shipping line, and a Shopify refund line marked
                # not restocked (no_restock: the customer kept it) give back
                # no cost -- unless Goods back on its refund says the goods
                # physically came back.
                came_back = ret.get("shopify_refund_id") in goods
                if noted:
                    for it in items or ret.get("goods_lines") or []:
                        if (_line(it) or {}).get("item_type") == "SHIPPING":
                            continue
                        if it.get("restock") is False and ret.get("shopify_refund_id") and not came_back:
                            continue
                        _give_back(it, float(it.get("return_qty") or 0))
            else:
                # Keyed on the physical unit: each piece back on sale gives its
                # cost back once, on the day it FIRST went back, whichever door
                # moved it -- the return's own restock (its restock_stock_ids:
                # every unit it reactivated or minted, a partial restock still
                # waiting for its retry too; "applied" with none put nothing
                # back), a set-aside piece's first release, or Goods back on a
                # Shopify refund (every press, a partial one too).
                landed: Dict[str, tuple] = {}

                def _lands(sid, at, among, landed=landed):
                    if at is not None and (sid not in landed or at < landed[sid][0]):
                        landed[sid] = (at, among)

                good = [it for it in items if str(it.get("condition") or "").upper() == "GOOD"]
                rkeys = [k for k in (ret.get("return_id"), ret.get("shopify_refund_id")) if k]
                for sid in ret.get("restock_stock_ids") or []:
                    at = min((landed_at[(sid, k)] for k in rkeys if (sid, k) in landed_at), default=None)
                    _lands(sid, max(when, at) if at is not None else _restocked_at(ret, when), good or items)
                for k in rkeys:
                    for sid in audited.get(k, []):
                        _lands(sid, max(when, landed_at[(sid, k)]), items)
                set_aside = [it for it in items if str(it.get("condition") or "").upper() != "GOOD"]
                for sid in ret.get("quarantined_stock_ids") or []:
                    _lands(sid, min((t for t in releases.get(sid, []) if t >= when), default=None),
                           set_aside or items)
                for sid, at in goods.get(ret.get("shopify_refund_id")) or []:
                    if sid and at is not None:
                        _lands(sid, max(when, at), items)
                for sid, (at, among) in landed.items():
                    if _in_range(at):
                        _give_back(_unit_item(sid, among))
            if not noted and not reversal:
                continue
            shares = [(ret.get("store_id"), 1.0)]
            if noted and by == "category":
                worth = [(_key(it), float(it.get("unit_price") or 0) * float(it.get("return_qty") or 0))
                         for it in split]
                total = sum(w for _k, w in worth)
                shares = ([(k, w / total) for k, w in worth if w] if total > 0
                          else [(_key(split[0]) if split else "OTHER", 1.0)])
            if noted:
                for key, share in shares:
                    row = out.setdefault(key, dict(empty))
                    row["gross"] += gross * share
                    row["taxable"] += taxable * share
                    row["tax"] += tax * share
                    row["count"] += 1
            for key, amount in reversal.items():
                out.setdefault(key, dict(empty))["cogs_reversal"] += amount
    except Exception:  # noqa: BLE001
        logger.warning("[PNL] customer returns could not be read", exc_info=True)
        return {}
    for row in out.values():
        for k in ("gross", "taxable", "tax", "cogs_reversal"):
            row[k] = round(row[k], 2)
    return out
