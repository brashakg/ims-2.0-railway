"""
IMS 2.0 - Reports Router
=========================
Real database queries for dashboard and reports
"""

import logging
import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
from datetime import date, datetime, timedelta
from ...utils.ist import (
    now_ist,
    now_ist_naive,
    fy_start_year_ist,
    ist_date_str,
    ist_day_start_utc,
    ist_month_window_utc,
    ist_today,
)
from ...utils.online_gst import order_interstate_flag
from calendar import monthrange
from ..auth import get_current_user, require_roles
from ...dependencies import (
    get_order_repository,
    get_stock_repository,
    get_customer_repository,
    get_task_repository,
    get_attendance_repository,
    get_audit_repository,
    get_eye_test_repository,
    get_product_repository,
    get_db,
    validate_store_access,
)
from ...services.name_resolver import order_actor_id, order_actor_name_map
from ...services.reorder_policy import auto_reorder_disabled as _auto_reorder_disabled
from ...services import cash_denominations as cash_denom
from ...services import eod_tally as till_service

logger = logging.getLogger(__name__)

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
        "status": {"$nin": ["CANCELLED", "DRAFT", "HISTORICAL"]},
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


_GSTN_DOC_ILLEGAL = re.compile(r"[^A-Za-z0-9/-]")

# Marker key for the AGGREGATED >16-char invoice-serial warn (one issue per
# report, not one per order -- see _note_over_cap_serial).
_OVER_CAP_ISSUE_CODE = "INVOICE_SERIAL_OVER_16"


def _note_over_cap_serial(validation_issues: list, serial: str) -> None:
    """Aggregate the >16-char minted-serial warn into ONE issue per report.

    Every minted serial shares the same numbering scheme ({PREFIX}/{STORE}/{FY}/
    {serial}, 20 chars), so a per-order warn would fire for EVERY sale in the
    month: issueCount ~= order count and the issues[:50] window fills with
    identical serial warns, burying the genuinely actionable ones (missing
    GSTIN/state). One aggregated issue keeps ok=false and the real signal:
    a count plus one example serial. The real fix is shortening the mint format
    (order_repository.next_invoice_number) -- tracked separately."""
    for issue in validation_issues:
        if isinstance(issue, dict) and issue.get("issue_code") == _OVER_CAP_ISSUE_CODE:
            issue["count"] = int(issue.get("count") or 1) + 1
            return
    validation_issues.append(
        {
            "level": "warn",
            "issue_code": _OVER_CAP_ISSUE_CODE,
            "invoice": serial,
            "count": 1,
            "issue": (
                "Invoice numbers exceed the GSTN 16-char cap (example: "
                f"{serial}) -- the portal may reject the upload; the minted "
                "serial format needs shortening"
            ),
        }
    )


def _gstr1_bill_number(order: dict, validation_issues: Optional[list] = None) -> str:
    """GSTN invoice identifier for a GSTR-1 B2B / B2CL row.

    A present, non-empty ``invoice_number`` ALWAYS wins VERBATIM: GSTR-1 must file
    the number of the tax invoice actually issued (the minted serial, e.g.
    ``BV/BOK-01/26-27/0001``), never a substituted identifier -- substituting
    breaks the B2B recipient's GSTR-2A/2B matching and books-vs-return
    reconciliation. If the real serial exceeds GSTN's 16-char cap, that is a
    NUMBERING-SCHEME defect to flag (a ``validation_issues`` warn when a list is
    passed) and fix at mint time, not something to paper over per row.

    Only when ``invoice_number`` is None/empty (historical online imports mint no
    serial -- CGST Rule 46(b)) do the FALLBACK tiers apply, each sanitized to the
    GSTN doc-number charset ([A-Za-z0-9/-]) and gated at <=16 chars:
      bill_number -> order_number (e.g. ``ONL-<id>``) -> the short Shopify order
      name ('#' stripped; bare numerics are prefixed with the storefront, e.g.
      ``BV-1001``, so BV and WizOpt names can't collide under one GSTIN) ->
      finally a 16-char-capped order_id (last resort, never the full UUID).
    Never raises."""
    inv = order.get("invoice_number")
    if inv:
        s = str(inv).strip()
        if s:
            if len(s) > 16 and validation_issues is not None:
                # ONE aggregated warn per report (count + example), never one
                # per order -- see _note_over_cap_serial.
                _note_over_cap_serial(validation_issues, s)
            return s
    for key in ("bill_number", "order_number"):
        val = order.get(key)
        if val:
            s = _GSTN_DOC_ILLEGAL.sub("", str(val).strip())
            if s and len(s) <= 16:
                return s
    name = str(order.get("shopify_order_name") or "").strip().lstrip("#").strip()
    name = _GSTN_DOC_ILLEGAL.sub("", name)
    if name and name.isdigit():
        # Bare Shopify order names ('#1001') restart per storefront; BV and
        # WizOpt ONLINE both bill under BV Opticals Pvt Ltd (one GSTIN book), so
        # disambiguate with the storefront prefix (store_id's first segment).
        prefix = _GSTN_DOC_ILLEGAL.sub(
            "", str(order.get("store_id") or "").strip().split("-")[0].upper()
        )
        if prefix and len(f"{prefix}-{name}") <= 16:
            name = f"{prefix}-{name}"
    if name and len(name) <= 16:
        return name
    return str(order.get("order_id") or "").strip()[:16]


def _cdnr_note_number(entry: dict) -> str:
    """16-char-safe CDNR note number for a GSTR-1 credit-note row.

    GSTN caps note numbers at 16 chars like invoice numbers. Synthesized
    historical credit notes (shopify_ingest) stamp a dedicated GSTN-legal
    ``note_number`` ('CNH-<refund-id tail>', <=16 chars) at insert -- prefer it.
    Legacy rows carry only the internal ``ref`` (e.g. 'RET-250415-ABC123', 17
    chars; 'SHOPIFY-HIST-REFUND-<id>', ~33 chars) -- cap it at 16 for the filing.
    The internal ``ref`` itself (the idempotency key) is NEVER changed."""
    note = str(entry.get("note_number") or "").strip()
    if note and len(note) <= 16:
        return note
    ref = str(entry.get("ref", entry.get("entry_id", "")) or "").strip()
    return ref[:16]


def _credit_note_date_ist(raw) -> str:
    """IST calendar day ('YYYY-MM-DD') of a ``credit_note_ledger.created_at``.

    BUG-104, and the one place ``ist_date_str`` alone CANNOT do the job.

    That column is stored as an ISO **STRING**, not a datetime -- all 11
    production rows are string-typed -- and both writers put a NAIVE-UTC
    instant in it:

      * ``services/store_credit_ledger.py::make_entry`` ->
        ``datetime.now().isoformat()``, and Railway runs UTC, so that is the
        UTC wall clock with no offset written;
      * ``services/shopify_ingest.py::_refund_credit_note_date_iso`` ->
        ``_to_naive_utc(...).isoformat()``, documented as naive-UTC so it
        compares apples-to-apples with the finance/GST datetime windows.

    ``ist_date_str`` passes strings through UNSHIFTED by documented design (a
    bare string carries no reliable frame, so guessing would corrupt it). Here
    the frame IS known from the writers, so parse to the naive-UTC instant
    FIRST and then take the IST day. The date is a VALUE that leaves the system
    -- it is the ``creditNoteDate`` on the GSTN portal export -- so it moves
    FORWARD (+5:30). Without this a credit note issued 00:00-05:30 IST prints
    the previous day, and on 1-Apr prints a PRIOR-financial-year date, even
    though the GSTR-1 month window (``ist_day_start_utc``, correctly a BOUND
    moving backward) already selected the row into the right return.

    Fail-soft: an unparseable string falls back to its own first 10 characters
    (the pre-BUG-104 behaviour), never raises, and '' for a missing value so
    the caller's ``or month + "-01"`` default still fires.
    """
    if raw is None or raw == "":
        return ""
    if isinstance(raw, datetime):
        return ist_date_str(raw)
    text = str(raw).strip()
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return text[:10]
    return ist_date_str(parsed)


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
    """Group orders by the IST calendar day. Returns sorted-asc list of
    {date, sales, orders}.

    BUG-104. The day here is a VALUE the owner reads off a chart, derived from
    a stored instant -- so it is corrected by moving the VALUE FORWARD (+5:30)
    through ``ist_date_str``, the same rule ``/dashboard`` in this file already
    uses. (Contrast a Mongo range BOUND, which moves BACKWARD via
    ``ist_day_start_utc``.) ``created_at`` is a naive ``datetime.now()`` == the
    UTC wall clock, so before this every order placed 00:00-05:30 IST -- 76 of
    934 live orders, 8.1% -- was plotted on the PREVIOUS day.

    The ``date_str`` preference is KEPT but is dead today: 0 of 934 production
    orders carry that field, so the ``created_at`` path is the ONLY live path.
    It stays because it is correct if a caller ever does supply one (like
    points_log.date_str, an IST business-day label a human typed), and removing
    it would change behaviour for any such caller; it is documented here so
    nobody reads it as a live safeguard.
    """
    by_day: dict = {}
    for o in orders:
        ds = o.get("date_str")
        if not ds:
            ds = ist_date_str(o.get("created_at"))
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


# ============================================================================
# Inventory enrichment (product master join)
# ============================================================================
# Serialized stock rows carry only product_id / store_id / barcode / quantity /
# status — NOT `category` (that lives on the `products` master; see
# inventory.py INV-6). The valuation + daily-stock-count loops read
# `item.get("category", "Other")` straight off the stock doc, so EVERY unit
# bucketed under "Other" (the grand total was right because cost_price is the
# same per unit, but the per-category split was meaningless). non-moving-stock
# and the inventory ledger already join the master; this gives the two laggards
# the same join.


def _stock_category_map(stock_rows: list) -> dict:
    """Return {product_id: category} for every product_id in `stock_rows`,
    sourced from the product master (with a catalog_products fallback for
    catalog-only products). Fail-soft: unresolved ids are simply absent, so the
    caller falls back to "Other".

    PERF: batched. This used to be a per-distinct-pid find_by_id plus a
    per-miss 3-way $or catalog find_one (an N+1 that made valuation and the
    daily stock count multi-second on a live catalog). Now it is ONE products
    `$in` query for every id, then ONE catalog_products query for the ids the
    master does not know at all. Resolution semantics are unchanged: master
    hit (even without a category) never falls through to the catalog; only a
    truthy category lands in the map. Repos whose collection cannot batch
    (e.g. test fakes that only implement find_by_id) fall back to the
    original per-id loop, so behaviour is identical either way."""
    pids = {
        str(r.get("product_id"))
        for r in (stock_rows or [])
        if r.get("product_id") is not None
    }
    out: dict = {}
    if not pids:
        return out

    product_repo = get_product_repository()
    catalog_resolver = None
    catalog_coll_getter = None
    try:
        from ..orders import _get_catalog_collection as catalog_coll_getter
        from ..orders import _resolve_catalog_product_doc as catalog_resolver
    except Exception:  # noqa: BLE001
        catalog_resolver = None
        catalog_coll_getter = None

    ordered_pids = sorted(pids)
    resolved: dict = {}  # pid -> product doc (master hit OR catalog fallback)

    # -- Pass 1: product master, ONE $in query ------------------------------
    if product_repo is not None:
        batched = False
        coll = getattr(product_repo, "collection", None)
        if coll is not None:
            try:
                master_hits: dict = {}
                for doc in coll.find(
                    {"product_id": {"$in": ordered_pids}},
                    {"product_id": 1, "category": 1},
                ):
                    pid = str(doc.get("product_id"))
                    # First doc in natural order wins == find_one semantics.
                    if pid not in master_hits:
                        master_hits[pid] = doc
                resolved.update(master_hits)
                batched = True
            except Exception:  # noqa: BLE001
                batched = False
        if not batched:
            # Per-id fallback (mock/fake collections that cannot batch).
            for pid in ordered_pids:
                try:
                    product = product_repo.find_by_id(pid)
                except Exception:  # noqa: BLE001
                    product = None
                if product:
                    resolved[pid] = product

    # -- Pass 2: catalog fallback for ids the master has NO doc for ---------
    # (_resolve_catalog_product_doc returns None for a falsy pid, so blank
    # ids are excluded up front exactly as before.)
    misses = [pid for pid in ordered_pids if pid and pid not in resolved]
    if misses and catalog_resolver is not None:
        batched = False
        try:
            coll = catalog_coll_getter() if catalog_coll_getter is not None else None
        except Exception:  # noqa: BLE001
            coll = None
        if coll is not None:
            try:
                catalog_hits: dict = {}
                miss_set = set(misses)
                for doc in coll.find(
                    {
                        "$or": [
                            {"id": {"$in": misses}},
                            {"sku": {"$in": misses}},
                            {"_id": {"$in": misses}},
                        ]
                    },
                    {"id": 1, "sku": 1, "category": 1},
                ):
                    # First doc in natural order matching the pid by ANY of
                    # the three keys wins == the old per-pid $or find_one.
                    for key in (doc.get("id"), doc.get("sku"), doc.get("_id")):
                        if key in miss_set and key not in catalog_hits:
                            catalog_hits[key] = doc
                resolved.update(catalog_hits)
                batched = True
            except Exception:  # noqa: BLE001
                batched = False
        if not batched:
            for pid in misses:
                try:
                    product = catalog_resolver(pid)
                except Exception:  # noqa: BLE001
                    product = None
                if product:
                    resolved[pid] = product

    for pid in pids:
        product = resolved.get(pid)
        if product and product.get("category"):
            out[pid] = product.get("category")
    return out


def _row_category(row: dict, cat_map: dict) -> str:
    """Resolve a stock row's category: product master first (authoritative),
    then any category stamped on the row itself, then 'Other'."""
    pid = str(row.get("product_id")) if row.get("product_id") is not None else ""
    return cat_map.get(pid) or row.get("category") or "Other"


