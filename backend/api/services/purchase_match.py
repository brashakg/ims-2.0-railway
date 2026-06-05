"""
IMS 2.0 - Purchase 3-Way Match + Inventory Valuation engine (Phase 2)
=====================================================================
Pure, side-effect-free helpers for the procure-to-pay control: comparing the
THREE documents that must agree before a vendor invoice is paid --

    PURCHASE ORDER (what we ordered + agreed price)
        vs
    GOODS RECEIPT NOTE / GRN (what physically arrived + was accepted)
        vs
    VENDOR INVOICE (what the supplier billed us for + at what price)

plus the moving-average inventory-valuation math used to true-up product cost
when an invoice is booked. No DB imports here -- the router fetches the three
docs and calls these helpers, so the logic is trivially unit-testable.

WHY THIS MATTERS (docs/SYSTEM_INTENT.md -- Control over Convenience, Fail
Loudly): without a 3-way match, an AP clerk can pay an invoice for goods that
were never ordered, never arrived, or were billed at a higher price than the PO.
The match flags any of those as an ON_HOLD_EXCEPTION (with a per-line reason)
instead of silently booking the payable. A clean match (every line's received
and invoiced quantity AND the invoiced price are within tolerance of the PO)
returns MATCHED and the invoice books normally.

CONFIGURABILITY: the quantity/price tolerance (default 5%) and the valuation
method (default MOVING_AVERAGE) are constants here with a DB-override resolver
(``resolve_config`` reads a single ``purchase_settings`` doc), mirroring the
hsn_gst_master "static seed + DB override, fail-soft to the seed" pattern so the
behaviour is identical to the defaults when no settings doc exists and never
crashes a booking on a settings read.

The match keys per product_id (the stable join the rest of the purchase flow --
GRN receipt reconciliation, lines_from_grn -- already uses). Garbage / missing
numbers coerce to 0.0 so nothing here ever raises.
"""

from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Configurable defaults (static seed; DB can override via resolve_config)
# ---------------------------------------------------------------------------

# Valuation methods we support. MOVING_AVERAGE updates a single blended cost on
# the product each receipt/invoice; FIFO is recorded as the chosen method (the
# per-unit unit_cost stamped on each serialized stock_unit at GRN time IS the
# FIFO layer) but the product-level blended true-up is a no-op under FIFO.
VALUATION_MOVING_AVERAGE = "MOVING_AVERAGE"
VALUATION_FIFO = "FIFO"
VALID_VALUATION_METHODS = (VALUATION_MOVING_AVERAGE, VALUATION_FIFO)

DEFAULT_VALUATION_METHOD = VALUATION_MOVING_AVERAGE
# Percentage tolerance for the qty + price 3-way match. 5% by default: a line is
# MATCHED when received qty, invoiced qty, and invoiced unit price are each
# within +/-5% of the PO. Beyond that the line (and the whole invoice) goes
# ON_HOLD_EXCEPTION.
DEFAULT_MATCH_TOLERANCE_PCT = 5.0

# Match verdicts.
MATCH_MATCHED = "MATCHED"
MATCH_ON_HOLD = "ON_HOLD_EXCEPTION"
MATCH_OVERRIDE = "MATCHED_OVERRIDE"  # an ADMIN/ACCOUNTANT approved a held match


def _f(v) -> float:
    """Coerce anything to a 2dp float, defaulting to 0.0. Never raises."""
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _i(v) -> int:
    """Coerce anything to an int, defaulting to 0. Never raises."""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _pct_diff(actual: float, expected: float) -> float:
    """Signed percentage difference of actual vs expected.

    (actual - expected) / expected * 100. When expected is 0 we return 0.0 if
    actual is also 0 (no difference) else a large sentinel (1e9) so any non-zero
    actual against a zero baseline is treated as out-of-tolerance -- you cannot
    be "within 5%" of zero.
    """
    a = _f(actual)
    e = _f(expected)
    if e == 0:
        return 0.0 if a == 0 else 1e9
    return round((a - e) / e * 100.0, 4)


# ---------------------------------------------------------------------------
# Config resolution (constants-with-DB-override; fail-soft to defaults)
# ---------------------------------------------------------------------------


def normalize_valuation_method(value: Optional[str]) -> str:
    """Normalise a valuation-method string to one of VALID_VALUATION_METHODS,
    falling back to the default for anything unrecognised. Never raises."""
    s = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if s in VALID_VALUATION_METHODS:
        return s
    if s in ("MOVING_AVG", "AVERAGE", "WEIGHTED_AVERAGE", "AVCO"):
        return VALUATION_MOVING_AVERAGE
    return DEFAULT_VALUATION_METHOD


def normalize_tolerance_pct(value) -> float:
    """Normalise a tolerance percentage. Clamps to [0, 100]; non-numeric or
    negative -> the default. 0 is allowed (exact-match-only)."""
    try:
        t = float(value)
    except (TypeError, ValueError):
        return DEFAULT_MATCH_TOLERANCE_PCT
    if t < 0:
        return DEFAULT_MATCH_TOLERANCE_PCT
    if t > 100:
        return 100.0
    return round(t, 4)


def resolve_config(settings_doc: Optional[dict]) -> dict:
    """Resolve the effective purchase config from an optional settings doc.

    Returns {valuation_method, match_tolerance_pct} with safe defaults applied,
    so callers get a complete, valid config whether the doc is None, partial, or
    has junk values. This is the single place the defaults live.
    """
    doc = settings_doc if isinstance(settings_doc, dict) else {}
    return {
        "valuation_method": normalize_valuation_method(doc.get("valuation_method")),
        "match_tolerance_pct": normalize_tolerance_pct(
            doc.get("match_tolerance_pct", DEFAULT_MATCH_TOLERANCE_PCT)
        ),
    }


# ---------------------------------------------------------------------------
# Index helpers: roll PO / GRN / invoice quantities + prices per product
# ---------------------------------------------------------------------------


def _po_by_product(po: Optional[dict]) -> Dict[str, dict]:
    """{product_id: {ordered_qty, unit_price, description, hsn}} from a PO.

    Multiple PO lines for the same product roll up: quantities sum; the unit
    price is the quantity-weighted average so a split-line PO compares sensibly.
    """
    out: Dict[str, dict] = {}
    items = (po or {}).get("items") if isinstance(po, dict) else None
    for it in items or []:
        if not isinstance(it, dict):
            continue
        pid = it.get("product_id")
        if pid is None:
            continue
        qty = _i(it.get("quantity"))
        price = _f(it.get("unit_price"))
        cur = out.get(pid)
        if cur is None:
            out[pid] = {
                "product_id": pid,
                "ordered_qty": qty,
                # running weighted price accumulator (value / qty)
                "_value": qty * price,
                "unit_price": price,
                "description": it.get("product_name") or it.get("sku"),
                "hsn": it.get("hsn"),
            }
        else:
            cur["ordered_qty"] += qty
            cur["_value"] += qty * price
            cur["unit_price"] = (
                round(cur["_value"] / cur["ordered_qty"], 2)
                if cur["ordered_qty"]
                else price
            )
    for v in out.values():
        v.pop("_value", None)
    return out


def _grn_accepted_by_product(grn: Optional[dict]) -> Dict[str, int]:
    """{product_id: accepted_qty} summed across a GRN's lines (accepted units --
    what actually entered stock and is billable)."""
    out: Dict[str, int] = {}
    items = (grn or {}).get("items") if isinstance(grn, dict) else None
    for it in items or []:
        if not isinstance(it, dict):
            continue
        pid = it.get("product_id")
        if pid is None:
            continue
        out[pid] = out.get(pid, 0) + _i(it.get("accepted_qty"))
    return out


def _invoice_by_product(invoice_lines: Optional[List[dict]]) -> Dict[str, dict]:
    """{product_id: {invoiced_qty, unit_price}} from invoice lines.

    A line's unit price is taken explicitly when present, else derived from
    taxable / qty (the engine stores taxable + qty + unit_price on each computed
    line). Quantities sum across lines for the same product.
    """
    out: Dict[str, dict] = {}
    for ln in invoice_lines or []:
        if not isinstance(ln, dict):
            continue
        pid = ln.get("product_id")
        if pid is None:
            continue
        qty = _f(ln.get("qty"))
        unit_price = ln.get("unit_price")
        if unit_price in (None, 0) and qty:
            # derive from taxable when an explicit price wasn't carried
            taxable = ln.get("taxable")
            if taxable is not None:
                unit_price = _f(taxable) / qty if qty else 0.0
        price = _f(unit_price)
        cur = out.get(pid)
        if cur is None:
            out[pid] = {
                "invoiced_qty": qty,
                "_value": qty * price,
                "unit_price": price,
            }
        else:
            cur["invoiced_qty"] += qty
            cur["_value"] += qty * price
            cur["unit_price"] = (
                round(cur["_value"] / cur["invoiced_qty"], 2)
                if cur["invoiced_qty"]
                else price
            )
    for v in out.values():
        v.pop("_value", None)
    return out


# ---------------------------------------------------------------------------
# THE 3-WAY MATCH
# ---------------------------------------------------------------------------


def three_way_match(
    po: Optional[dict],
    grn: Optional[dict],
    invoice_lines: Optional[List[dict]],
    tolerance_pct: float = DEFAULT_MATCH_TOLERANCE_PCT,
) -> dict:
    """Compare PO vs GRN vs invoice per product and return a match verdict.

    Args:
      po:            the purchase-order doc ({items: [{product_id, quantity,
                     unit_price, ...}]}) or None.
      grn:           the goods-receipt doc ({items: [{product_id, accepted_qty,
                     ...}]}) or None.
      invoice_lines: the computed invoice lines ({product_id, qty, unit_price,
                     taxable, ...}).
      tolerance_pct: +/- percentage within which a qty/price counts as matching.

    Returns:
      {
        match_status: "MATCHED" | "ON_HOLD_EXCEPTION",
        tolerance_pct,
        has_po, has_grn,
        lines: [ {product_id, description, hsn,
                  ordered_qty, received_qty, invoiced_qty,
                  po_unit_price, invoice_unit_price,
                  qty_variance_pct, price_variance_pct,
                  status: "MATCHED" | "EXCEPTION", reasons: [str, ...]} ],
        exceptions: [str, ...],     # flat list of every reason across all lines
        summary: {matched_lines, exception_lines, total_lines},
      }

    Rules (each compared against tolerance_pct):
      * qty: |invoiced - ordered| and |received - ordered| within tolerance.
        A short/over receipt OR an over/under-invoice vs the order is flagged.
      * price: |invoice_unit_price - po_unit_price| within tolerance.
      * a product invoiced/received that is NOT on the PO -> EXCEPTION
        ("not on purchase order").
      * a PO product that was ordered but neither received nor invoiced is NOT
        itself an exception (open/partial PO lines are normal); but if it was
        invoiced-but-not-received (billed for goods that didn't arrive) that IS
        flagged.

    The overall match_status is MATCHED only when EVERY line is MATCHED (and at
    least the invoice has lines). Any exception -> ON_HOLD_EXCEPTION.
    """
    tol = normalize_tolerance_pct(tolerance_pct)
    po_idx = _po_by_product(po)
    grn_idx = _grn_accepted_by_product(grn)
    inv_idx = _invoice_by_product(invoice_lines)

    has_po = bool(po_idx)
    has_grn = bool(grn_idx)

    # Every product that appears in ANY of the three documents gets a line.
    product_ids: List[str] = []
    for pid in list(inv_idx.keys()) + list(po_idx.keys()) + list(grn_idx.keys()):
        if pid not in product_ids:
            product_ids.append(pid)

    lines: List[dict] = []
    all_exceptions: List[str] = []
    matched_lines = 0
    exception_lines = 0

    for pid in product_ids:
        po_line = po_idx.get(pid)
        ordered_qty = _i(po_line.get("ordered_qty")) if po_line else None
        po_unit_price = _f(po_line.get("unit_price")) if po_line else None
        inv_line = inv_idx.get(pid)
        invoiced_qty = _f(inv_line.get("invoiced_qty")) if inv_line else 0.0
        invoice_unit_price = _f(inv_line.get("unit_price")) if inv_line else 0.0
        received_qty = grn_idx.get(pid)

        reasons: List[str] = []
        qty_var_pct = None
        price_var_pct = None

        if po_line is None:
            # Invoiced/received something we never ordered.
            reasons.append("Product not on purchase order")
        else:
            # Qty: invoiced vs ordered.
            if inv_line is not None:
                qty_var_pct = _pct_diff(invoiced_qty, ordered_qty)
                if abs(qty_var_pct) > tol:
                    reasons.append(
                        f"Invoiced qty {invoiced_qty:g} differs from ordered "
                        f"{ordered_qty:g} by {qty_var_pct:.1f}% (> {tol:g}%)"
                    )
            # Qty: received vs ordered (short / over shipment).
            if received_qty is not None:
                rcv_var = _pct_diff(received_qty, ordered_qty)
                if abs(rcv_var) > tol:
                    reasons.append(
                        f"Received qty {received_qty:g} differs from ordered "
                        f"{ordered_qty:g} by {rcv_var:.1f}% (> {tol:g}%)"
                    )
            # Billed for goods that never arrived (invoiced but nothing received).
            if inv_line is not None and (received_qty is None or received_qty == 0):
                if invoiced_qty > 0:
                    reasons.append(
                        "Invoiced but no goods received against this PO line"
                    )
            # Price: invoice vs PO.
            if inv_line is not None and po_unit_price is not None:
                price_var_pct = _pct_diff(invoice_unit_price, po_unit_price)
                if abs(price_var_pct) > tol:
                    reasons.append(
                        f"Invoice price {invoice_unit_price:g} differs from PO "
                        f"price {po_unit_price:g} by {price_var_pct:.1f}% "
                        f"(> {tol:g}%)"
                    )

        status = MATCH_MATCHED if not reasons else "EXCEPTION"
        if reasons:
            exception_lines += 1
            all_exceptions.extend(reasons)
        else:
            matched_lines += 1

        lines.append(
            {
                "product_id": pid,
                "description": (po_line or {}).get("description")
                or (inv_line or {}).get("description"),
                "hsn": (po_line or {}).get("hsn"),
                "ordered_qty": ordered_qty,
                "received_qty": received_qty,
                "invoiced_qty": invoiced_qty,
                "po_unit_price": po_unit_price,
                "invoice_unit_price": invoice_unit_price,
                "qty_variance_pct": qty_var_pct,
                "price_variance_pct": price_var_pct,
                "status": status,
                "reasons": reasons,
            }
        )

    # Overall: MATCHED only if there are lines and none are exceptions.
    if not lines:
        match_status = MATCH_ON_HOLD
        all_exceptions.append("No comparable lines across PO / GRN / invoice")
    elif exception_lines > 0:
        match_status = MATCH_ON_HOLD
    else:
        match_status = MATCH_MATCHED

    return {
        "match_status": match_status,
        "tolerance_pct": tol,
        "has_po": has_po,
        "has_grn": has_grn,
        "lines": lines,
        "exceptions": all_exceptions,
        "summary": {
            "matched_lines": matched_lines,
            "exception_lines": exception_lines,
            "total_lines": len(lines),
        },
    }


# ---------------------------------------------------------------------------
# INVENTORY VALUATION -- moving-average cost true-up
# ---------------------------------------------------------------------------


def moving_average_cost(
    old_qty,
    old_cost,
    receipt_qty,
    receipt_unit_cost,
) -> float:
    """Weighted-average (AVCO) blended unit cost after a receipt.

        new_cost = (old_qty * old_cost + receipt_qty * receipt_unit_cost)
                   / (old_qty + receipt_qty)

    Pure + total: all inputs coerce to numbers. Degenerate cases:
      * total qty <= 0      -> fall back to the receipt unit cost (or old cost
                               if the receipt cost is 0) so cost is never NaN.
      * receipt_qty <= 0    -> cost unchanged (returns old_cost).
    Returns a 2dp float.
    """
    oq = _f(old_qty)
    oc = _f(old_cost)
    rq = _f(receipt_qty)
    rc = _f(receipt_unit_cost)
    if rq <= 0:
        return oc
    total_qty = oq + rq
    if total_qty <= 0:
        return rc if rc > 0 else oc
    blended = (oq * oc + rq * rc) / total_qty
    return round(blended, 2)


def valuation_trueup_for_invoice(
    invoice_lines: Optional[List[dict]],
    product_state: Dict[str, dict],
    method: str = DEFAULT_VALUATION_METHOD,
) -> List[dict]:
    """Compute per-product cost updates to apply when an invoice is booked.

    The invoice's per-unit landed price is the AUTHORITATIVE cost (the GRN
    stamped the PO price provisionally; the invoice trues it up to what we were
    actually billed). For MOVING_AVERAGE we blend that into the product's
    current on-hand cost; for FIFO the product-level blended cost is left as-is
    (the per-unit unit_cost on each stock_unit is the FIFO layer) and we simply
    record the latest invoice cost.

    Args:
      invoice_lines:  computed invoice lines ({product_id, qty, unit_price,
                      taxable}).
      product_state:  {product_id: {on_hand_qty, cost_price}} -- the CURRENT
                      on-hand quantity + cost for each product (the router reads
                      these from products / stock_units before calling).
      method:         resolved valuation method.

    Returns a list of {product_id, old_cost, new_cost, receipt_qty,
    receipt_unit_cost, method} -- one per product on the invoice that has a
    positive invoiced qty. The router persists these fail-soft (a valuation
    update must never block the booking).
    """
    meth = normalize_valuation_method(method)
    inv_idx = _invoice_by_product(invoice_lines)
    updates: List[dict] = []
    for pid, line in inv_idx.items():
        recv_qty = _f(line.get("invoiced_qty"))
        recv_unit_cost = _f(line.get("unit_price"))
        if recv_qty <= 0:
            continue
        cur = product_state.get(pid) if isinstance(product_state, dict) else None
        old_qty = _f((cur or {}).get("on_hand_qty"))
        old_cost = _f((cur or {}).get("cost_price"))
        if meth == VALUATION_FIFO:
            # FIFO: don't blend the product-level cost; record the latest layer
            # cost. (The stock_units carry their own per-unit unit_cost.)
            new_cost = recv_unit_cost if recv_unit_cost > 0 else old_cost
        else:
            new_cost = moving_average_cost(old_qty, old_cost, recv_qty, recv_unit_cost)
        updates.append(
            {
                "product_id": pid,
                "old_cost": old_cost,
                "new_cost": new_cost,
                "receipt_qty": recv_qty,
                "receipt_unit_cost": recv_unit_cost,
                "old_qty": old_qty,
                "method": meth,
            }
        )
    return updates
