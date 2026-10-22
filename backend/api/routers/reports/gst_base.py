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


# THE rule for which orders a GST return files (GSTR-1, GSTR-3B and the GST
# report all query with it): not an unbooked DRAFT, not a CANCELLED order, not
# a HISTORICAL pre-IMS import (settled outside IMS books), and not an online
# order deleted in Shopify (VOID / VOIDED, services/shopify_order_delete) --
# a deleted order is no supply. Finance's revenue rule
# (finance/_shared._REAL_ORDER_STATUS_FILTER) deliberately still counts VOID;
# that is a revenue question, not this one.
GST_RETURN_ORDER_STATUS_FILTER = {
    "$nin": [
        s
        for status in ("CANCELLED", "DRAFT", "HISTORICAL", "VOID", "VOIDED")
        for s in (status, status.lower())
    ]
}

# B2CL (GSTR-1 Table 5): an INTER-state invoice to an unregistered buyer whose
# invoice value is above this, in rupees. Intra-state B2C of any value is B2CS.
# CA MUST CONFIRM THE VALUE: the limit may have been lowered to Rs 1,00,000 from
# Aug 2024 (Notification 12/2024-Central Tax). Kept at Rs 2,50,000 until the
# CA rules -- change it here and nowhere else.
B2CL_INVOICE_THRESHOLD = 250000


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
    `taxable + tax + round_off == grand_total` in BOTH inclusive and exclusive
    modes, so the correct GST taxable value is
    `grand_total - round_off - tax_amount` -- NOT
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
    # Owner ruling 2026-10-08: a till bill's grand_total is rounded to the
    # rupee and the paise it moved are stored as `round_off`. Round off is
    # neither taxable value nor tax, so back it out before deriving taxable.
    # (0 on bills made before the ruling and on online orders.)
    grand_total = round(grand_total - _f(order.get("round_off")), 2)

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


def _order_rate_lines(items, order_taxable, order_tax):
    """THE rule for splitting one order's taxable value and tax by GST rate,
    for every GSTR-1 table: the B2B / B2CL invoice item rows (one per rate,
    GSTN `itms[]`), the B2CS buckets and the HSN summary. Returns
    ``(gst_rate, hsn, taxable, tax)`` tuples, one per usable line, so an
    invoice mixing a 5% frame and an 18% sunglass is filed at both rates
    instead of being lumped under the first line's rate.

    A line's own stamped ``gst_rate`` / ``taxable_value`` / ``tax_amount``
    (orders._compute_per_category_gst at the till, shopify_ingest online) are
    used as stamped -- no price is recomputed. Only a legacy line without that
    stamp is derived from its GST-inclusive ``item_total``
    (taxable = item_total * 100 / (100 + rate)), its rate the item's
    ``gst_rate`` or else the category table rate (api.services.gst_rates).
    A line's HSN is its ``hsn_code`` (9004 when blank). With no usable line,
    one line carries the order totals at 5% / 9004 so nothing is dropped.

    The lines DISTRIBUTE the invoice's taxable + tax, never change them: they
    are scaled to the order totals (scale 1.0 when the stamps already
    reconcile, the normal case) and any rounding paisa goes on the last line,
    so they sum to the order's taxable and tax exactly.
    """
    from api.services.gst_rates import GST_CATEGORY_TABLE, _normalize_category

    def _f(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    raw = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        rate = it.get("gst_rate")
        if rate is None:
            entry = GST_CATEGORY_TABLE.get(_normalize_category(it.get("category")))
            rate = entry[1] if entry else 5.0
        try:
            rate = int(round(float(rate)))
        except (TypeError, ValueError):
            rate = 5
        hsn = str(it.get("hsn_code") or "9004")
        if it.get("taxable_value") is not None and it.get("tax_amount") is not None:
            taxable, tax = _f(it.get("taxable_value")), _f(it.get("tax_amount"))
            if taxable <= 0 and tax <= 0:
                continue
        else:
            gross = _f(it.get("item_total"))
            if gross <= 0:
                continue
            taxable = gross * 100.0 / (100.0 + rate)
            tax = gross - taxable
        raw.append((rate, hsn, taxable, tax))

    if not raw:
        # No usable line items -> keep the order-level totals under one bucket.
        return [(5, "9004", round(order_taxable, 2), round(order_tax, 2))]

    sum_taxable = sum(t for _, _, t, _ in raw)
    sum_tax = sum(x for _, _, _, x in raw)
    st = (order_taxable / sum_taxable) if sum_taxable > 0 else 1.0
    sx = (order_tax / sum_tax) if sum_tax > 0 else 1.0
    lines = [[r, h, round(t * st, 2), round(x * sx, 2)] for r, h, t, x in raw]
    lines[-1][2] = round(lines[-1][2] + round(order_taxable, 2) - round(sum(ln[2] for ln in lines), 2), 2)
    lines[-1][3] = round(lines[-1][3] + round(order_tax, 2) - round(sum(ln[3] for ln in lines), 2), 2)
    return [tuple(ln) for ln in lines]


def _split_heads(tax: float, is_inter: bool) -> tuple:
    """(igst, cgst, sgst) of one tax amount: all IGST inter-state, else halved
    with the odd paisa on SGST so the heads always sum to the tax."""
    if is_inter:
        return round(tax, 2), 0.0, 0.0
    cgst = round(tax / 2, 2)
    return 0.0, cgst, round(tax - cgst, 2)


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
