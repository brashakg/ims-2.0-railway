"""TechCherry R1: footfall audit, price bands, lens deep dive, seasonality."""

from fastapi import Depends, Query
from typing import Optional
from datetime import date, datetime, timedelta
from ...utils.ist import (
    now_ist_naive,
    fy_start_year_ist,
    ist_date_str,
    ist_day_start_utc,
    ist_today,
)
from ..auth import get_current_user
from ...dependencies import (
    get_order_repository,
    get_db,
    validate_store_access,
)
from ._shared import (
    _item_revenue,
    _order_revenue,
    _order_tax,
    _orders_in_window,
    router,
)

# ============================================================================
# TechCherry R1 — Net-new analytics dimensions
# ============================================================================
# Footfall Audit · Price Band Analysis · Lens Deep Dive · Seasonality.
# Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.
#
# These four endpoints fill the only TechCherry sections IMS didn't already
# have. All compute live off Mongo — no XLS importer, no caching layer.
#
# Conventions match the existing /reports/* endpoints:
#   - store_id query param, fall back to user.active_store_id, fall back to "store-001"
#   - Period: year/month for monthly, years_back for multi-year
#   - Response always wraps the payload in a dict with store_id echoed
#   - Empty results never raise — return zeroed structures so the UI
#     can render the cards without conditional logic.


from collections import defaultdict


def _fy_of(dt: datetime) -> str:
    """Indian financial-year LABEL ('FY24-25') for a STORED instant.

    BUG-104, VALUE rule -- the day this tags an order with is read off a
    report, so it moves FORWARD (+5:30) before the April test is applied.

    This used to carry its own ``dt.month >= 4`` test on the RAW stored
    instant. ``created_at`` is a naive ``datetime.now()`` == the UTC wall
    clock (Railway runs UTC), so an order placed 1-Apr 01:30 IST carries a
    31-Mar timestamp and was tagged with the PRIOR financial year -- while its
    GST invoice serial, minted off the IST clock, says the new one (Rule
    46(b)). Production holds exactly one order in that shape.

    The duplicate April rule is now DELETED rather than repaired: ``ist.py``
    already owns the only definition of the financial year
    (``fy_start_year_ist``) and of the IST day of a stored instant
    (``ist_date_str``). What is left here is the reports-local LABEL format,
    which ``fy_start_year_ist`` (an int) deliberately does not produce. Two
    copies of the April rule is how the boundary drifts.
    """
    try:
        anchor = datetime.fromisoformat(ist_date_str(dt))
    except (TypeError, ValueError):
        # Unparseable / frameless input: fall back to the raw instant rather
        # than raise. Never reached for a datetime, which is all _order_created_at
        # ever yields.
        anchor = dt
    yr = fy_start_year_ist(anchor)
    return f"FY{yr % 100:02d}-{(yr + 1) % 100:02d}"


_PRICE_BANDS: list[tuple[str, float, float]] = [
    ("<1K", 0, 1000),
    ("1K-2.5K", 1000, 2500),
    ("2.5K-5K", 2500, 5000),
    ("5K-10K", 5000, 10000),
    ("10K-15K", 10000, 15000),
    ("15K-20K", 15000, 20000),
    ("20K-30K", 20000, 30000),
    ("30K-50K", 30000, 50000),
    ("50K-75K", 50000, 75000),
    ("75K-1.5L", 75000, 150000),
    ("1.5L+", 150000, float("inf")),
]
_PRICE_BAND_NAMES: list[str] = [b[0] for b in _PRICE_BANDS]


def _price_band_of(amount: float) -> str:
    """Bucket an amount (₹) into one of the 11 _PRICE_BANDS."""
    for name, lo, hi in _PRICE_BANDS:
        if lo <= amount < hi:
            return name
    return "1.5L+"


def _order_created_at(order: dict) -> Optional[datetime]:
    """Parse created_at as datetime, tolerating both datetime objects and
    ISO strings (some legacy docs store the latter)."""
    ca = order.get("created_at")
    if isinstance(ca, datetime):
        return ca
    if isinstance(ca, str) and ca:
        try:
            return datetime.fromisoformat(ca.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _order_net(order: dict) -> float:
    """Revenue net of tax (the band-relevant figure). Falls back to
    gross revenue if tax_total is unknown."""
    return _order_revenue(order) - _order_tax(order)


# ----------------------------------------------------------------------------
# R1.1 Footfall Audit
# ----------------------------------------------------------------------------


def _month_iter(start: datetime, end: datetime):
    """Yield (year, month) tuples from start month through end month inclusive."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            m, y = 1, y + 1


@router.get("/walkouts/footfall-audit")
async def footfall_audit(
    store_id: Optional[str] = Query(None),
    months_back: int = Query(12, ge=1, le=36),
    current_user: dict = Depends(get_current_user),
):
    """Cross-reference walk-in counters / walkouts / orders. Surfaces
    hidden sales (orders without a corresponding walkout entry).

    Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.1.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    db = get_db()

    now = now_ist_naive()
    start = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
    for _ in range(months_back - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    # BUG-104, BOUND rule (move the bound BACKWARD). `start` above is an IST
    # wall-clock value -- correct for the `date_str` walkin/walkout filters
    # (those columns ARE IST business-day strings) and for the _month_iter
    # labels, but WRONG as a Mongo bound on `created_at`, which is naive-UTC.
    # IST-midnight on the 1st IS 18:30 UTC on the last day of the previous
    # month, so a bare `start` dropped every order placed 00:00-05:30 IST on
    # the 1st out of the orders leg while the walkout leg kept it -- the two
    # halves of the same row of this table were counted in different months.
    orders_from = ist_day_start_utc(start.date())
    orders_to = ist_day_start_utc(ist_today() + timedelta(days=1))

    # Fetch all the inputs in three queries.
    walkin_counters = []
    walkouts = []
    if db is not None:
        try:
            walkin_counters = list(
                db.get_collection("walk_in_counters").find(
                    {
                        "store_id": active_store,
                        "date_str": {"$gte": start.date().isoformat()},
                    },
                )
            )
        except Exception:
            walkin_counters = []
        try:
            walkouts = list(
                db.get_collection("walkouts").find(
                    {
                        "store_id": active_store,
                        "date_str": {"$gte": start.date().isoformat()},
                        "deleted_at": None,
                    },
                )
            )
        except Exception:
            walkouts = []

    order_repo = get_order_repository()
    orders = (
        _orders_in_window(
            order_repo,
            store_id=active_store,
            start_dt=orders_from,
            end_dt=orders_to,
        )
        if order_repo is not None
        else []
    )

    # Index by YYYY-MM
    walkins_by_month: dict = defaultdict(int)
    for w in walkin_counters:
        ds = w.get("date_str") or ""
        if len(ds) >= 7:
            walkins_by_month[ds[:7]] += int(w.get("total") or 0)

    walkouts_total_by_month: dict = defaultdict(int)
    walkouts_conv_by_month: dict = defaultdict(int)
    for w in walkouts:
        ds = w.get("date_str") or ""
        if len(ds) < 7:
            continue
        walkouts_total_by_month[ds[:7]] += 1
        if (w.get("result") or "").upper() == "CONVERTED":
            walkouts_conv_by_month[ds[:7]] += 1

    orders_by_month: dict = defaultdict(int)
    for o in orders:
        ca = _order_created_at(o)
        if ca is None:
            continue
        # BUG-104, VALUE rule (move the derived month FORWARD). The other side
        # of this comparison is `ds[:7]` off walk_in_counters/walkouts
        # `date_str`, which is an IST business-day string. Bucketing orders by
        # the raw UTC month put an order placed 1-Jul 01:30 IST into JUNE while
        # its walkout sat in JULY -- the same row of the table then showed more
        # orders than walk-ins and invented "hidden sales".
        orders_by_month[ist_date_str(ca)[:7]] += 1

    months = []
    rolling = {
        "walkins_total": 0,
        "walkouts_total": 0,
        "walkouts_converted": 0,
        "orders_total": 0,
    }
    for y, m in _month_iter(start, now):
        key = f"{y:04d}-{m:02d}"
        wi = walkins_by_month.get(key, 0)
        wo = walkouts_total_by_month.get(key, 0)
        wc = walkouts_conv_by_month.get(key, 0)
        ot = orders_by_month.get(key, 0)
        hidden = max(0, ot - wc)
        months.append(
            {
                "month": key,
                "walkins_total": wi,
                "walkouts_total": wo,
                "walkouts_converted": wc,
                "orders_total": ot,
                "hidden_sales": hidden,
                "hidden_sales_pct": round(hidden / ot, 3) if ot else 0.0,
                "staff_reported_conversion_pct": round(wc / wi, 3) if wi else 0.0,
                "true_conversion_pct": round(ot / wi, 3) if wi else 0.0,
            }
        )
        rolling["walkins_total"] += wi
        rolling["walkouts_total"] += wo
        rolling["walkouts_converted"] += wc
        rolling["orders_total"] += ot

    rolling_hidden = max(0, rolling["orders_total"] - rolling["walkouts_converted"])
    rolling.update(
        {
            "hidden_sales": rolling_hidden,
            "hidden_sales_pct": (
                round(rolling_hidden / rolling["orders_total"], 3)
                if rolling["orders_total"]
                else 0.0
            ),
            "staff_reported_conversion_pct": (
                round(rolling["walkouts_converted"] / rolling["walkins_total"], 3)
                if rolling["walkins_total"]
                else 0.0
            ),
            "true_conversion_pct": (
                round(rolling["orders_total"] / rolling["walkins_total"], 3)
                if rolling["walkins_total"]
                else 0.0
            ),
        }
    )

    return {
        "store_id": active_store,
        "period_start": start.date().isoformat(),
        "period_end": now.date().isoformat(),
        "months": months,
        "rolling": rolling,
    }


# ----------------------------------------------------------------------------
# R1.2 Price Band Analysis
# ----------------------------------------------------------------------------


@router.get("/sales/price-bands")
async def sales_price_bands(
    store_id: Optional[str] = Query(None),
    fy_count: int = Query(3, ge=1, le=10),
    trend_bands: int = Query(4, ge=1, le=11),
    current_user: dict = Depends(get_current_user),
):
    """Segment invoices by net amount into 11 bands. Track movement
    between bands across financial years (premiumization signal).

    Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.2.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    order_repo = get_order_repository()
    now = now_ist_naive()
    # Start of FY (fy_count - 1) years before the current FY.
    current_fy_start_year = fy_start_year_ist()
    start_year = current_fy_start_year - (fy_count - 1)
    # BUG-104, BOUND rule (move the bound BACKWARD). A bare
    # `datetime(start_year, 4, 1)` is IST-midnight expressed in the WRONG
    # frame: `created_at` is naive-UTC, and 1-Apr 00:00 IST IS 31-Mar 18:30
    # UTC. The old bound therefore excluded every 1-April sale made between
    # 00:00 and 05:30 IST from the oldest FY in the window -- the same instants
    # that _fy_of below now tags INTO that FY. Bound and value move in opposite
    # directions, or the FY totals disagree with the FY labels.
    start = ist_day_start_utc(date(start_year, 4, 1))
    orders = (
        _orders_in_window(
            order_repo,
            store_id=active_store,
            start_dt=start,
            end_dt=ist_day_start_utc(ist_today() + timedelta(days=1)),
        )
        if order_repo is not None
        else []
    )

    # Tag each order with FY + net amount + band + month
    enriched: list = []
    for o in orders:
        ca = _order_created_at(o)
        if ca is None:
            continue
        net = _order_net(o)
        enriched.append(
            {
                # BUG-104, VALUE rule for BOTH: these are read off the
                # premiumization chart, so the stored instant moves FORWARD
                # (+5:30) before its FY / month is taken.
                "fy": _fy_of(ca),
                "month": ist_date_str(ca)[:7],
                "net": net,
                "band": _price_band_of(net),
                "customer_id": o.get("customer_id"),
            }
        )

    # by_fy: per-FY counts + revenue + ATV per band
    fy_band_agg: dict = defaultdict(
        lambda: {b: {"invoices": 0, "revenue": 0.0} for b in _PRICE_BAND_NAMES}
    )
    fy_order: list = []
    for e in enriched:
        if e["fy"] not in fy_band_agg:
            fy_order.append(e["fy"])
        slot = fy_band_agg[e["fy"]][e["band"]]
        slot["invoices"] += 1
        slot["revenue"] += e["net"]

    by_fy = []
    for fy in sorted(set(e["fy"] for e in enriched)):
        invoices = [fy_band_agg[fy][b]["invoices"] for b in _PRICE_BAND_NAMES]
        revenue = [round(fy_band_agg[fy][b]["revenue"], 2) for b in _PRICE_BAND_NAMES]
        atv = [
            (
                round(fy_band_agg[fy][b]["revenue"] / fy_band_agg[fy][b]["invoices"], 2)
                if fy_band_agg[fy][b]["invoices"]
                else 0.0
            )
            for b in _PRICE_BAND_NAMES
        ]
        by_fy.append(
            {
                "fy": fy,
                "invoices_by_band": invoices,
                "revenue_by_band": revenue,
                "atv_by_band": atv,
            }
        )

    # Monthly trend per band — top `trend_bands` by total revenue across the whole period
    band_totals: dict = defaultdict(float)
    for e in enriched:
        band_totals[e["band"]] += e["net"]
    top_bands = sorted(_PRICE_BAND_NAMES, key=lambda b: -band_totals[b])[:trend_bands]

    monthly_per_band: dict = {
        b: defaultdict(lambda: {"revenue": 0.0, "invoices": 0}) for b in top_bands
    }
    for e in enriched:
        if e["band"] in monthly_per_band:
            monthly_per_band[e["band"]][e["month"]]["revenue"] += e["net"]
            monthly_per_band[e["band"]][e["month"]]["invoices"] += 1

    monthly_trend_by_band = {}
    for b in top_bands:
        rows = sorted(monthly_per_band[b].items())
        monthly_trend_by_band[b] = [
            {"month": m, "revenue": round(d["revenue"], 2), "invoices": d["invoices"]}
            for m, d in rows
        ]

    # Movement summary: for customers who appear in BOTH the current FY and the
    # immediately-preceding FY, compute median band index in each and compare.
    movement_summary = {
        "premiumized_pct": 0.0,
        "stable_pct": 0.0,
        "downgraded_pct": 0.0,
        "compared_customers": 0,
    }
    if fy_count >= 2:
        sorted_fys = sorted(set(e["fy"] for e in enriched))
        if len(sorted_fys) >= 2:
            cur_fy, prev_fy = sorted_fys[-1], sorted_fys[-2]
            cust_bands_cur: dict = defaultdict(list)
            cust_bands_prev: dict = defaultdict(list)
            for e in enriched:
                if not e["customer_id"]:
                    continue
                idx = _PRICE_BAND_NAMES.index(e["band"])
                if e["fy"] == cur_fy:
                    cust_bands_cur[e["customer_id"]].append(idx)
                elif e["fy"] == prev_fy:
                    cust_bands_prev[e["customer_id"]].append(idx)
            common = set(cust_bands_cur) & set(cust_bands_prev)
            up = same = down = 0
            for cid in common:
                cur_med = sorted(cust_bands_cur[cid])[len(cust_bands_cur[cid]) // 2]
                prev_med = sorted(cust_bands_prev[cid])[len(cust_bands_prev[cid]) // 2]
                if cur_med > prev_med:
                    up += 1
                elif cur_med < prev_med:
                    down += 1
                else:
                    same += 1
            n = len(common)
            if n:
                movement_summary = {
                    "premiumized_pct": round(up / n, 3),
                    "stable_pct": round(same / n, 3),
                    "downgraded_pct": round(down / n, 3),
                    "compared_customers": n,
                }

    return {
        "store_id": active_store,
        "bands": _PRICE_BAND_NAMES,
        "fy_count": fy_count,
        "by_fy": by_fy,
        "trend_bands": top_bands,
        "monthly_trend_by_band": monthly_trend_by_band,
        "movement_summary": movement_summary,
        "total_orders": len(enriched),
    }


# ----------------------------------------------------------------------------
# R1.3 Lens Deep Dive
# ----------------------------------------------------------------------------


_LENS_ITEM_TYPES = {"LENS", "OPTICAL_LENS", "CONTACT_LENS", "COLORED_CONTACT_LENS"}


@router.get("/sales/lens-deep-dive")
async def sales_lens_deep_dive(
    store_id: Optional[str] = Query(None),
    months_back: int = Query(12, ge=1, le=36),
    current_user: dict = Depends(get_current_user),
):
    """Breakdown of lens line items by brand / type / coating / refractive index.

    Joins order items where item_type in _LENS_ITEM_TYPES against the products
    collection to pull `brand` + `attributes.{lens_type,coating,refractive_index}`.

    Surfaces `parse_rate` so users can see how clean their catalog metadata is.

    Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.3.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    order_repo = get_order_repository()
    db = get_db()

    now = now_ist_naive()
    start = now - timedelta(days=30 * months_back)
    orders = (
        _orders_in_window(
            order_repo,
            store_id=active_store,
            start_dt=start,
            end_dt=now,
        )
        if order_repo is not None
        else []
    )

    # Collect lens line items
    lens_items: list = []
    product_ids: set = set()
    for o in orders:
        for it in o.get("items") or []:
            t = (it.get("item_type") or it.get("category") or "").upper()
            if t in _LENS_ITEM_TYPES:
                lens_items.append(
                    {
                        "item_type": t,
                        "product_id": it.get("product_id"),
                        "quantity": int(it.get("quantity") or 1),
                        "revenue": _item_revenue(it),
                    }
                )
                if it.get("product_id"):
                    product_ids.add(it["product_id"])

    # Fetch products in one query, build lookup
    products_by_id: dict = {}
    if db is not None and product_ids:
        try:
            cursor = db.get_collection("products").find(
                {"product_id": {"$in": list(product_ids)}}
            )
            for p in cursor:
                products_by_id[p["product_id"]] = p
        except Exception:
            products_by_id = {}

    # Aggregate
    by_brand: dict = defaultdict(lambda: {"units": 0, "revenue": 0.0})
    by_type: dict = defaultdict(lambda: {"units": 0, "revenue": 0.0})
    by_coating: dict = defaultdict(lambda: {"units": 0, "revenue": 0.0})
    by_index: dict = defaultdict(lambda: {"units": 0, "revenue": 0.0})
    total_units = 0
    total_revenue = 0.0
    parsed_units = 0  # units where at least lens_type was identified
    contact_units = 0
    contact_revenue = 0.0

    for it in lens_items:
        qty, rev = it["quantity"], it["revenue"]
        total_units += qty
        total_revenue += rev
        prod = products_by_id.get(it["product_id"]) if it["product_id"] else None
        brand = (prod or {}).get("brand") or "Unknown"
        attrs = (prod or {}).get("attributes") or {}
        ltype = attrs.get("lens_type") or attrs.get("type")
        coating = attrs.get("coating")
        index = attrs.get("refractive_index") or attrs.get("index")

        if it["item_type"] in ("CONTACT_LENS", "COLORED_CONTACT_LENS"):
            contact_units += qty
            contact_revenue += rev
        by_brand[brand]["units"] += qty
        by_brand[brand]["revenue"] += rev
        if ltype:
            parsed_units += qty
            by_type[ltype]["units"] += qty
            by_type[ltype]["revenue"] += rev
        if coating:
            by_coating[coating]["units"] += qty
            by_coating[coating]["revenue"] += rev
        if index:
            by_index[str(index)]["units"] += qty
            by_index[str(index)]["revenue"] += rev

    def _materialize(agg: dict) -> list:
        rows = [
            {"key": k, "units": v["units"], "revenue": round(v["revenue"], 2)}
            for k, v in agg.items()
        ]
        return sorted(rows, key=lambda r: -r["revenue"])

    return {
        "store_id": active_store,
        "period_start": start.date().isoformat(),
        "period_end": now.date().isoformat(),
        "totals": {
            "lens_units": total_units,
            "lens_revenue": round(total_revenue, 2),
            "atv": round(total_revenue / total_units, 2) if total_units else 0.0,
            "contact_lens_units": contact_units,
            "contact_lens_revenue": round(contact_revenue, 2),
        },
        "by_brand": [
            {
                "brand": r["key"],
                "units": r["units"],
                "revenue": r["revenue"],
                "share": (
                    round(r["revenue"] / total_revenue, 3) if total_revenue else 0.0
                ),
            }
            for r in _materialize(by_brand)
        ],
        "by_type": [
            {"type": r["key"], "units": r["units"], "revenue": r["revenue"]}
            for r in _materialize(by_type)
        ],
        "by_coating": [
            {"coating": r["key"], "units": r["units"], "revenue": r["revenue"]}
            for r in _materialize(by_coating)
        ],
        "by_refractive_index": [
            {"index": r["key"], "units": r["units"], "revenue": r["revenue"]}
            for r in _materialize(by_index)
        ],
        "parse_rate": round(parsed_units / total_units, 3) if total_units else 0.0,
        "metadata_pending": parsed_units == 0 and total_units > 0,
    }


# ----------------------------------------------------------------------------
# R1.4 Seasonality
# ----------------------------------------------------------------------------


@router.get("/sales/seasonality")
async def sales_seasonality(
    store_id: Optional[str] = Query(None),
    years_back: int = Query(2, ge=1, le=10),
    current_user: dict = Depends(get_current_user),
):
    """Day-of-week × month-of-year aggregation. Identifies peak and trough
    days/months and computes peak DOW lift over the average DOW.

    Spec: docs/TECHCHERRY_PORT_SCOPE.md §5.4.
    """
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id") or "store-001"
    order_repo = get_order_repository()
    # BUG-104, BOUND rule (backward) for the window, VALUE rule (forward) for
    # the day-of-week / month-of-year buckets below. Both halves live in this
    # one endpoint so they had to move together: a Monday sale at 02:00 IST
    # carries a SUNDAY UTC timestamp, and which day the shop is busy is the
    # entire point of this report.
    start = ist_day_start_utc(ist_today() - timedelta(days=365 * years_back))
    orders = (
        _orders_in_window(
            order_repo,
            store_id=active_store,
            start_dt=start,
            end_dt=ist_day_start_utc(ist_today() + timedelta(days=1)),
        )
        if order_repo is not None
        else []
    )

    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    moy_names = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    dow_agg = {name: {"invoices": 0, "revenue": 0.0} for name in dow_names}
    moy_agg = {name: {"invoices": 0, "revenue": 0.0} for name in moy_names}

    for o in orders:
        ca = _order_created_at(o)
        if ca is None:
            continue
        # VALUE rule: take the IST calendar day FIRST, then its weekday /
        # month. Reading .weekday() off the raw stored instant filed every
        # 00:00-05:30 IST sale under the PREVIOUS day -- Monday-morning trade
        # showed up as Sunday revenue, and 1-April trade as March.
        try:
            ist_day = date.fromisoformat(ist_date_str(ca))
        except (TypeError, ValueError):
            continue
        rev = _order_revenue(o)
        dow_agg[dow_names[ist_day.weekday()]]["invoices"] += 1
        dow_agg[dow_names[ist_day.weekday()]]["revenue"] += rev
        moy_agg[moy_names[ist_day.month - 1]]["invoices"] += 1
        moy_agg[moy_names[ist_day.month - 1]]["revenue"] += rev

    def _materialize_row(name: str, slot: dict, key: str) -> dict:
        n = slot["invoices"]
        return {
            key: name,
            "invoices": n,
            "revenue": round(slot["revenue"], 2),
            "atv": round(slot["revenue"] / n, 2) if n else 0.0,
        }

    dow_out = [_materialize_row(n, dow_agg[n], "dow") for n in dow_names]
    moy_out = [_materialize_row(n, moy_agg[n], "month") for n in moy_names]

    nonzero_dow = [d for d in dow_out if d["revenue"] > 0]
    nonzero_moy = [d for d in moy_out if d["revenue"] > 0]
    peak_dow = max(nonzero_dow, key=lambda d: d["revenue"], default=None)
    trough_dow = min(nonzero_dow, key=lambda d: d["revenue"], default=None)
    peak_moy = max(nonzero_moy, key=lambda d: d["revenue"], default=None)
    trough_moy = min(nonzero_moy, key=lambda d: d["revenue"], default=None)
    avg_dow_rev = sum(d["revenue"] for d in dow_out) / 7
    peak_lift = (
        peak_dow["revenue"] / avg_dow_rev - 1.0 if peak_dow and avg_dow_rev else 0.0
    )

    return {
        "store_id": active_store,
        "years_back": years_back,
        "day_of_week": dow_out,
        "month_of_year": moy_out,
        "peak_dow": peak_dow["dow"] if peak_dow else None,
        "trough_dow": trough_dow["dow"] if trough_dow else None,
        "peak_month": peak_moy["month"] if peak_moy else None,
        "trough_month": trough_moy["month"] if trough_moy else None,
        "peak_dow_lift_pct": round(peak_lift, 3),
        "total_orders": len(orders),
    }


