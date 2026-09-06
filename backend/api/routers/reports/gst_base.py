"""Raw-Mongo handle, the per-order GST split and the tax-period parser
shared by GSTR-1 and GSTR-3B."""

from typing import Optional
from ...utils.online_gst import order_interstate_flag
from ...dependencies import (
    get_db,
)

# ============================================================================
# GST RETURNS - GSTR-1 (Outward Supplies)
# ============================================================================


def _get_raw_db():
    """Get raw MongoDB database object for aggregation queries."""
    try:
        conn = get_db()
        if conn is not None and conn.is_connected:
            return conn.db
    except Exception:
        pass
    return None


def _order_is_interstate(order: dict, store_state: str, customer_state: str) -> bool:
    """Inter-state (IGST) vs intra-state (CGST+SGST) for ONE order in the GST
    report family (/finance/gst report, GSTR-1, GSTR-3B).

    Prefer the order doc's OWN persisted ``interstate`` flag (OS-008): online
    (Shopify) orders stamp it at ingest from the buyer's DELIVERY address via
    the shared _build_invoice_gst_split -- while their buyer customer records
    are minted stateless, so deriving the split from customers.state misfiled
    every inter-state online sale as CGST/SGST even though the minted invoice
    said IGST. The store-state vs customer-state string comparison stays as the
    FALLBACK for docs without the flag (POS orders don't persist it),
    byte-identical to the prior rule (unknown either side -> intra).

    The OS-008 flag-preference gate is the shared ``order_interstate_flag``
    helper (utils.online_gst); the raw case-insensitive string-compare fallback
    below is reports-specific and stays byte-identical to this file's prior rule
    -- finance.py keeps its own GST-code-normalizing fallback deliberately."""
    flag = order_interstate_flag(order)
    if flag is not None:
        return flag
    return bool(
        store_state
        and customer_state
        and store_state.strip().lower() != customer_state.strip().lower()
    )


def _order_taxable_and_tax(order: dict) -> tuple:
    """Derive (taxable_value, total_tax) for GST returns from an order doc.

    Persisted order docs carry `subtotal` (the PRE-cart-discount GROSS sum,
    NOT the taxable base), `tax_amount` (total GST), and `grand_total` (what
    the customer actually pays). `orders._compute_per_category_gst` guarantees
    `taxable + tax == grand_total` in BOTH inclusive and exclusive modes, so
    the correct GST taxable value is `grand_total - tax_amount` -- NOT
    `subtotal`, which overstates when a cart discount applies or under
    inclusive pricing.

    Real orders have NO top-level `taxable` / `taxable_amount` field (those
    legacy names never landed) -- reading them returned 0 and zeroed out every
    GSTR-1 / GSTR-3B / GST-report taxable value. This was the bug. Resolution
    order (first usable signal wins):
      Tax total: explicit top-level `tax` (authoritative when present) ->
        `tax_amount` (what orders.py persists) -> `tax_total`.
      Taxable:
        1. Explicit top-level `taxable` / `taxable_amount` if present -- kept
           for backward compatibility with any doc / fixture that stamps them;
           for a well-formed order this equals grand_total - tax.
        2. grand_total - tax (the canonical derivation for real orders).
        3. Per-line `taxable_value` / `tax_amount` sums when the top-level
           totals are absent (e.g. partial legacy rows).
    """

    def _f(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    # Total GST. An explicit top-level `tax` (the sibling of an explicit
    # `taxable`) is authoritative when present; real orders don't carry it and
    # fall through to `tax_amount` (what orders.py persists) then `tax_total`.
    if order.get("tax") is not None:
        total_tax = _f(order.get("tax"))
    elif order.get("tax_amount") is not None:
        total_tax = _f(order.get("tax_amount"))
    else:
        total_tax = _f(order.get("tax_total"))

    # (1) Explicit top-level taxable wins when present (legacy / fixtures).
    if order.get("taxable") is not None or order.get("taxable_amount") is not None:
        explicit = order.get("taxable")
        if explicit is None:
            explicit = order.get("taxable_amount")
        return round(_f(explicit), 2), round(total_tax, 2)

    grand_total = (
        _f(order.get("grand_total"))
        if order.get("grand_total") is not None
        else _f(order.get("total_amount"))
    )

    # Per-line fallback data.
    line_taxable = 0.0
    line_tax = 0.0
    has_line_data = False
    for it in order.get("items") or []:
        if not isinstance(it, dict):
            continue
        if it.get("taxable_value") is not None or it.get("tax_amount") is not None:
            has_line_data = True
            line_taxable += _f(it.get("taxable_value"))
            line_tax += _f(it.get("tax_amount"))

    # (2) Canonical derivation: taxable = grand_total - tax_amount.
    if grand_total > 0:
        # If top-level tax is missing but the lines carry it, trust the lines.
        if total_tax <= 0 and has_line_data and line_tax > 0:
            total_tax = line_tax
        return round(grand_total - total_tax, 2), round(total_tax, 2)

    # (3) No grand_total -> per-line sums.
    if has_line_data:
        return round(line_taxable, 2), round(line_tax, 2)

    return 0.0, round(total_tax, 2)


def _b2cs_rate_lines(items, order_taxable, order_tax):
    """NEW-GST-B2CS-HSN: split a consumer (B2CS) order's lines into
    ``(gst_rate, taxable, tax)`` tuples, one per line, so an invoice that mixes
    GST rates (e.g. a 5% frame + an 18% sunglass) lands in the correct per-rate
    B2CS bucket instead of being lumped under the first line's rate.

    Each line's rate is ``item.gst_rate`` when present, else the canonical
    category rate (api.services.gst_rates). Taxable is derived GST-exclusively
    from the line gross ``item_total`` (Indian retail prices are GST-inclusive):
    ``taxable = item_total * 100 / (100 + rate)``. When no usable line items
    exist, fall back to a single line carrying the order-level totals so nothing
    is dropped.
    """
    from api.services.gst_rates import GST_CATEGORY_TABLE, _normalize_category

    raw = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        try:
            gross = float(it.get("item_total") or 0) or 0.0
        except (TypeError, ValueError):
            gross = 0.0
        if gross <= 0:
            continue
        rate = it.get("gst_rate")
        if rate is None:
            entry = GST_CATEGORY_TABLE.get(_normalize_category(it.get("category")))
            rate = entry[1] if entry else 5.0
        try:
            rate = int(round(float(rate)))
        except (TypeError, ValueError):
            rate = 5
        taxable = gross * 100.0 / (100.0 + rate)
        raw.append((rate, taxable, gross - taxable))

    if not raw:
        # No usable line items -> keep the order-level totals under one bucket.
        return [(5, round(order_taxable, 2), round(order_tax, 2))]

    # Reconcile the per-line split back to the order-level taxable/tax: the split
    # only DISTRIBUTES the invoice's taxable + tax across rate buckets by line, it
    # must not change the SUM -- so the GSTR-1 totals (which add these buckets)
    # still equal the booked invoice. Scale is 1.0 when the lines already
    # reconcile (the normal case); it absorbs order-level rounding / cart discount.
    sum_taxable = sum(t for _, t, _ in raw)
    sum_tax = sum(x for _, _, x in raw)
    st = (order_taxable / sum_taxable) if sum_taxable > 0 else 1.0
    sx = (order_tax / sum_tax) if sum_tax > 0 else 1.0
    return [(r, round(t * st, 2), round(x * sx, 2)) for r, t, x in raw]


def _normalise_period(month: str, year: Optional[int] = None) -> str:
    """Normalise the (month, year) query params into IMS canonical "YYYY-MM".

    Accepts:
      - month="YYYY-MM"           -> returned as-is
      - month="MM" or "M" + year  -> "{year}-{MM}"
      - month="MMYYYY"            -> "{YYYY}-{MM}"
    Falls back to returning `month` unchanged so the downstream parser can
    raise a clean 400 on genuinely bad input.
    """
    m = (month or "").strip()
    if len(m) >= 7 and m[4] == "-":
        return m  # already YYYY-MM[-DD]
    if year is not None and m.isdigit() and 1 <= int(m) <= 12:
        return f"{int(year):04d}-{int(m):02d}"
    if len(m) == 6 and m.isdigit():  # MMYYYY
        return f"{m[2:]}-{m[:2]}"
    return m
