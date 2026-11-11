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


# THE rule for which orders a GST return files is THE real-sale rule
# (api/utils/real_sale) -- the books use the same one, so they agree. A VOID
# order is filed once invoiced or shipped (owner ruling 2026-10-22).
from ...utils.real_sale import real_sale_match  # noqa: E402,F401

# B2CL (GSTR-1 Table 5): an INTER-state invoice to an unregistered buyer whose
# invoice value is above this, in rupees. Intra-state B2C of any value is B2CS.
# CA MUST CONFIRM THE VALUE: the limit may have been lowered to Rs 1,00,000 from
# Aug 2024 (Notification 12/2024-Central Tax). Kept at Rs 2,50,000 until the
# CA rules -- change it here and nowhere else.
B2CL_INVOICE_THRESHOLD = 250000


def is_b2cl(gstin, inter: bool, invoice_value: float) -> bool:
    """THE B2CL rule (GSTR-1 Table 5): no buyer GSTIN, inter-state, invoice
    value above B2CL_INVOICE_THRESHOLD. The sale's table and its notes' section
    (CDNUR vs B2CS netting) both ask this, so they can never disagree."""
    return not gstin and bool(inter) and invoice_value > B2CL_INVOICE_THRESHOLD


# The customer fields the place-of-supply rule reads (org_validation.
# customer_state_code): every return reads customers with this projection.
CUSTOMER_SUPPLY_FIELDS = {
    "customer_id": 1, "gstin": 1, "name": 1, "state": 1, "state_code": 1,
    "billing_address": 1,
}


def _order_supply(order: dict, store: Optional[dict], customer: Optional[dict]) -> tuple:
    """``(interstate, place_code, assumed)`` of ONE order for the GST returns
    (GSTR-1, GSTR-3B, the /reports/finance/gst report) -- the head the tax
    invoice PRINTED. The order's own persisted ``interstate`` stamp wins (an
    online order stamps it at ingest from the delivery address, OS-008);
    otherwise THE invoice rule, org_validation.supply_interstate (state CODES,
    so 'JH' at a Jharkhand shop is intra; the customer's billing-address state
    counts; an unknown buyer state is intra, as printed). One function, so a
    return can never file a head the invoice did not print."""
    from ...services.org_validation import supply_interstate
    from ...utils.online_gst import order_place_of_supply

    # The buyer as the invoice was minted (_invoice_customer): every return,
    # the credit-note head and the books ask through here, so none can file
    # the place of supply of a customer record edited after the mint.
    customer = _invoice_customer(order, customer)
    inter, code, assumed = supply_interstate(store, customer, order_place_of_supply(order))
    flag = order_interstate_flag(order)
    if flag is not None:
        return flag, code, assumed and not flag
    return inter, code, assumed


def _invoice_customer(order, customer: Optional[dict]) -> Optional[dict]:
    """The customer as the sale's tax invoice was minted: the buyer GSTIN
    frozen on the order at the mint (``customer_gstin`` -- GST batch 5) over
    the record read now. An invoice minted before the stamp existed reads the
    record, as before. GSTR-1 and GSTR-3B both ask this."""
    if isinstance(order, dict) and "customer_gstin" in order and (
        customer or order.get("customer_gstin")
    ):
        return {**(customer or {}), "gstin": str(order.get("customer_gstin") or "")}
    return customer


def _invoice_gstin(order, live: str) -> str:
    """The buyer GSTIN a sale (or its note) files under: _invoice_customer's."""
    return str((_invoice_customer(order, {"gstin": live or ""}) or {}).get("gstin") or "")


def _sale_period_match(from_dt, to_dt) -> dict:
    """THE month a sale files in, as a Mongo filter to spread into a query
    (one top-level $or): a numbered tax invoice in the month of its own
    invoice_date (stamped at the mint -- an order taken 28 Oct and printed
    3 Nov files in November); an order with no number or no invoice_date
    (one minted before the stamp) in its created_at month. GSTR-1, GSTR-3B
    and Table 13 all ask this, so the returns file every invoice once, in
    one month."""
    return _sale_window_match({"$gte": from_dt, "$lt": to_dt})


def _sale_window_match(window: dict) -> dict:
    """_sale_period_match over any date-range operator dict (the Tally doors'
    inclusive ``$lte`` day bounds)."""
    return {"$or": [
        {"invoice_number": {"$nin": [None, ""]}, "invoice_date": window},
        {"$or": [{"invoice_number": {"$in": [None, ""]}}, {"invoice_date": None}],
         "created_at": window},
    ]}


def _sale_filed_at(order: dict):
    """The instant a sale files on (_sale_period_match's choice): its tax
    invoice's own date, else the order's created_at."""
    if order.get("invoice_number") and order.get("invoice_date") is not None:
        return order.get("invoice_date")
    return order.get("created_at")


class UnfileableOrder(ValueError):
    """A stored order a GST return cannot file as it stands. The return doors
    answer it with the ORDER named (a retry cannot fix it)."""

    def __init__(self, order, problem):
        self.order_id = str((order or {}).get("order_id") or (order or {}).get("order_number") or "?")
        super().__init__(
            f"Order {self.order_id}: {problem}; correct the order, then download "
            "the return again."
        )


class UnreadableOrderMoney(UnfileableOrder):
    """A stored money field that no tolerant parse can read."""

    def __init__(self, order, value):
        super().__init__(order, f"unreadable money amount ({value!r})")


def _money(value, order=None) -> float:
    """One stored money amount as a float, read tolerantly: None / '' -> 0;
    int, float, Decimal, BSON Decimal128; a numeric string with commas or a
    'Rs' / rupee prefix. Anything else RAISES UnreadableOrderMoney naming the
    order -- a GST return never files an unreadable amount as 0."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        raise UnreadableOrderMoney(order, value)
    if hasattr(value, "to_decimal"):  # bson.Decimal128
        value = value.to_decimal()
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        for prefix in ("Rs.", "Rs", "INR", "₹"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        value = text
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise UnreadableOrderMoney(order, value) from None
    if out != out or out in (float("inf"), float("-inf")):
        raise UnreadableOrderMoney(order, value)
    return out


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
        return _money(v, order)

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


_GST_SLABS = (0, 5, 12, 18, 28)


def _order_rate_lines(items, order_taxable, order_tax, order=None, issues=None, qtys=None, sources=None):
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
    one line carries the order totals at the order's own stamped ``tax_rate``,
    else at the GST slab its own tax / taxable gives (flagged in ``issues``),
    else at its effective rate flagged as an ERROR -- never a guessed 5%. A 0% line
    (an exempt eye test) is flagged: it belongs in GSTR-1 Table 8 (nil /
    exempt), which is not built yet (batch 7).

    The lines DISTRIBUTE the invoice's taxable + tax, never change them: they
    are scaled to the order totals (scale 1.0 when the stamps already
    reconcile, the normal case) and any rounding paisa goes on the last line,
    so they sum to the order's taxable and tax exactly.

    ``qtys``, when a list, is filled with each returned line's quantity (the
    item's ``quantity``, 1 when blank; 0 for the order-totals fallback line) --
    the HSN summary's UQC quantity (GSTR-1 Table 12), aligned with the lines.
    ``sources``, when a list, is filled the same way with each returned line's
    own item dict (None for the order-totals fallback line) -- the e-invoice
    names each IRP item row from it.
    """
    from api.services.gst_rates import GST_CATEGORY_TABLE, _normalize_category

    def _f(v):
        return _money(v, order)

    oid = str((order or {}).get("invoice_number") or (order or {}).get("order_id") or "")

    def _flag(issue):
        if issues is not None:
            issues.append({"level": "warn", "invoice": oid, "issue": issue})

    raw = []
    line_qtys: list = []
    line_items: list = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        stamped = it.get("taxable_value") is not None and it.get("tax_amount") is not None
        if stamped:
            taxable, tax = _f(it.get("taxable_value")), _f(it.get("tax_amount"))
            if taxable <= 0 and tax <= 0:
                continue  # a Rs 0 line files nothing, whatever its rate says
        else:
            gross = _f(it.get("item_total"))
            if gross <= 0:
                continue
        rate = it.get("gst_rate")
        if isinstance(rate, str):
            rate = rate.strip().rstrip("%").strip() or None  # '5%' / '5 %' -> '5'
        if rate is None:
            entry = GST_CATEGORY_TABLE.get(_normalize_category(it.get("category")))
            if entry:
                rate = entry[1]
            elif stamped and taxable > 0 and round(tax / taxable * 100) in _GST_SLABS:
                # No rate, no known category: the line's own stamped tax gives
                # the slab (flagged); with nothing to read, the order raises.
                rate = round(tax / taxable * 100)
                _flag(f"A line has no GST rate; filed at {rate}% (its own tax / taxable).")
            else:
                raise UnfileableOrder(order, f"a line has no GST rate and no known category ({it.get('category')!r})")
        try:
            rate = int(round(float(rate)))
        except (TypeError, ValueError):
            raise UnfileableOrder(order, f"a line's GST rate is unreadable ({rate!r})") from None
        hsn = str(it.get("hsn_code") or "9004")
        if not stamped:
            taxable = gross * 100.0 / (100.0 + rate)
            tax = gross - taxable
        raw.append((rate, hsn, taxable, tax))
        line_qtys.append(_line_qty(it.get("quantity")))
        line_items.append(it)

    if not raw:
        # No usable line items -> the order totals under ONE bucket, at the
        # order's own rate -- never a guessed one.
        stamped = (order or {}).get("tax_rate")
        if stamped is not None:
            rate = int(round(_f(stamped)))
        elif order_taxable > 0 and round(order_tax / order_taxable * 100) in _GST_SLABS:
            rate = int(round(order_tax / order_taxable * 100))
            _flag(f"No line carries a GST rate; filed at {rate}% (its own tax / taxable).")
        elif order_taxable <= 0 and order_tax <= 0:
            return []
        else:
            # No slab fits: filed at its own effective rate and flagged as an
            # ERROR -- the portal takes no such rate, the CA must correct it.
            rate = round(order_tax / order_taxable * 100, 2) if order_taxable > 0 else 0
            if issues is not None:
                issues.append({"level": "error", "invoice": oid, "issue": (
                    f"No line carries a GST rate and its tax is {rate}% of its taxable value -- "
                    "no GST slab; correct the order before filing.")})
        raw = [(rate, "9004", order_taxable, order_tax)]
        if rate == 0 and order_taxable > 0:
            _flag(f"Rs {order_taxable:,.2f} at 0% (nil / exempt) belongs in GSTR-1 Table 8, not built yet (batch 7) -- filed at 0% here; the CA moves it.")
        if qtys is not None:
            qtys[:] = [0.0]
        if sources is not None:
            sources[:] = [None]
        return [(rate, "9004", round(order_taxable, 2), round(order_tax, 2))]
    for rate, _h, taxable, _x in raw:
        if rate == 0 and taxable > 0:
            _flag(f"Rs {taxable:,.2f} at 0% (nil / exempt) belongs in GSTR-1 Table 8, not built yet (batch 7) -- filed at 0% here; the CA moves it.")

    sum_taxable = sum(t for _, _, t, _ in raw)
    sum_tax = sum(x for _, _, _, x in raw)
    st = (order_taxable / sum_taxable) if sum_taxable > 0 else 1.0
    sx = (order_tax / sum_tax) if sum_tax > 0 else 1.0
    lines = [[r, h, round(t * st, 2), round(x * sx, 2)] for r, h, t, x in raw]
    lines[-1][2] = round(lines[-1][2] + round(order_taxable, 2) - round(sum(ln[2] for ln in lines), 2), 2)
    lines[-1][3] = round(lines[-1][3] + round(order_tax, 2) - round(sum(ln[3] for ln in lines), 2), 2)
    if qtys is not None:
        qtys[:] = line_qtys
    if sources is not None:
        sources[:] = line_items
    return [tuple(ln) for ln in lines]


def _line_qty(raw) -> float:
    """A sale line's quantity for the HSN summary: its ``quantity``, 1 when
    blank or unreadable (a line is at least one piece)."""
    try:
        q = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return q if q > 0 else 1.0


def _alloc(total: float, weights: list) -> list:
    """`total` shared pro rata by `weights` (equally when they sum to 0), to
    the paisa, the rounding on the last share -- the shares sum to `total`."""
    w = sum(weights)
    parts = [round(total * x / w, 2) if w else round(total / len(weights), 2) for x in weights]
    if parts:
        parts[-1] = round(parts[-1] + round(total, 2) - round(sum(parts), 2), 2)
    return parts


def _rate_heads(sale_lines, is_inter: bool) -> list:
    """An order's rate lines (_order_rate_lines) summed PER RATE with each
    rate's tax split by THE split rule (gst_rates.split_gst -- what the tax
    invoice prints per rate): ``[{rate, taxable, tax, cgst, sgst, igst,
    lines}]`` sorted by rate. ``lines`` = ``[(hsn, taxable, cgst, sgst,
    igst)]``, the rate's heads shared over its lines pro rata to taxable, so
    the HSN rows add up to the rate rows to the paisa. GSTR-1's item rows,
    B2CS buckets and HSN rows and GSTR-3B's Table 3.1(a) all come from here,
    so the returns agree to the paisa on every head."""
    from api.services.gst_rates import split_gst

    by_rate: dict = {}
    for rate, hsn, taxable, tax in sale_lines:
        b = by_rate.setdefault(rate, {"rate": rate, "taxable": 0.0, "tax": 0.0, "_l": []})
        b["taxable"] = round(b["taxable"] + taxable, 2)
        b["tax"] = round(b["tax"] + tax, 2)
        b["_l"].append((hsn, taxable))
    out = []
    for rate in sorted(by_rate):
        b = by_rate.pop(rate)
        cgst, sgst, igst = split_gst(b["tax"], is_inter)
        lines = b.pop("_l")
        w = [t for _, t in lines]
        shares = [_alloc(h, w) for h in (cgst, sgst, igst)]
        b["lines"] = [(h, t, c, g, i) for (h, t), c, g, i in zip(lines, *shares)]
        out.append({**b, "cgst": cgst, "sgst": sgst, "igst": igst})
    return out


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
