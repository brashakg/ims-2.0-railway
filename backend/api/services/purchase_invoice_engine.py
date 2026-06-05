"""
IMS 2.0 - Purchase Invoice engine
==================================
Pure, side-effect-free money + GST-classification math for a first-class
purchase invoice (the vendor's tax invoice, recorded with line items). No DB
imports -- the router fetches rows and calls these helpers, so the logic is
trivially unit-testable.

THE BUG THIS FIXES
------------------
`place_of_supply` is READ by the ITC code (services/itc_reconcile.py, which
routes a bill's tax to IGST when its place_of_supply state differs from the
recipient entity's state) but it was WRITTEN nowhere -- the header-only AP
"bill" never captured it. So every INTER-STATE purchase was silently booked as
CGST + SGST instead of IGST. For a client running 2 states / 4 GSTINs that is a
real GST-return error (wrong ITC ledger, wrong GSTR-3B section).

This engine determines place_of_supply from the supplier GSTIN vs the recipient
(buyer) GSTIN and computes the per-line split accordingly:

  * supplier state != recipient state  -> INTER-STATE -> IGST = gst, CGST=SGST=0
  * supplier state == recipient state  -> INTRA-STATE -> CGST+SGST split, IGST=0

GST place-of-supply for a goods purchase is the buyer's (recipient's) state, so
`place_of_supply` is the recipient state code -- which is exactly what
itc_reconcile compares against the entity's primary state. We WRITE it onto the
invoice doc so the register classifies correctly from then on.

Paisa-exactness mirrors itc_reconcile / the sales-invoice split: CGST is
round(tax/2) and SGST is the residual (tax - cgst), so CGST + SGST == tax to the
paisa even on odd-paise tax amounts (e.g. 5.01 -> 2.50 + 2.51).
"""

from typing import List, Optional, Tuple

try:
    # Reuse the canonical state-code normaliser (handles "MH" / "Maharashtra" /
    # "27" / a full GSTIN's 2-char prefix). Import defensively so the engine is
    # importable in isolation for unit tests even if the package layout shifts.
    from .org_validation import normalize_state_code as _normalize_state_code
except Exception:  # pragma: no cover - defensive fallback

    def _normalize_state_code(value):
        return value


def _f(v) -> float:
    """Coerce anything to a 2dp float, defaulting to 0.0. Never raises."""
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def state_code_of(value: Optional[str]) -> str:
    """Two-digit GST state code from a GSTIN / state code / state name.

    Accepts a full 15-char GSTIN (first 2 chars are the state code), a bare
    "27" / "27-Maharashtra", a 2-letter abbreviation ("MH"), or a full state
    name. Returns "" when nothing usable can be derived (so a missing supplier
    or recipient GSTIN degrades to intra-state rather than mis-classifying).
    """
    if value is None:
        return ""
    s = str(value).strip().upper()
    if not s:
        return ""
    # A GSTIN (or any 15-char id) embeds the state code in the first two digits.
    if len(s) >= 12 and s[:2].isdigit():
        return s[:2]
    # Otherwise let the canonical normaliser map abbr / name / code -> "NN".
    norm = _normalize_state_code(s)
    norm = str(norm or "").strip().upper()
    if len(norm) >= 2 and norm[:2].isdigit():
        return norm[:2]
    return ""


def determine_place_of_supply(
    supplier_gstin: Optional[str],
    recipient_gstin: Optional[str],
    explicit_place_of_supply: Optional[str] = None,
) -> Tuple[Optional[str], bool]:
    """Resolve (place_of_supply_state_code, is_interstate) for a purchase.

    place_of_supply for a goods purchase is the recipient (buyer) state. We
    prefer an explicit override (a caller may pass a known POS state), else the
    recipient GSTIN's state. The inter-state decision compares supplier state vs
    that place-of-supply state:

      * both known and different -> inter-state (IGST)
      * both known and equal     -> intra-state (CGST + SGST)
      * either unknown           -> intra-state (conservative default; matches
        itc_reconcile's "missing place_of_supply -> intra-state" fallback so an
        incomplete record is never silently routed to IGST)

    Returns the 2-digit place_of_supply code (or None if it can't be derived)
    plus the boolean inter-state flag.
    """
    supplier_state = state_code_of(supplier_gstin)
    pos_state = state_code_of(explicit_place_of_supply) or state_code_of(
        recipient_gstin
    )
    if not pos_state:
        # No usable buyer state -> can't assert inter-state; stay intra-state.
        return (None, False)
    if not supplier_state:
        # Buyer state known but supplier unknown -> can't prove a difference.
        return (pos_state, False)
    return (pos_state, supplier_state != pos_state)


def split_line_gst(taxable, gst_rate, interstate: bool) -> dict:
    """Per-line GST split for a given taxable value + rate.

    Returns {taxable, gst_rate, gst, cgst, sgst, igst, line_total}. The tax is
    computed once as round(taxable * rate / 100); for an inter-state line the
    whole tax is IGST, otherwise it splits CGST = round(tax/2) and SGST = the
    residual so CGST + SGST == tax to the paisa.
    """
    tax_base = _f(taxable)
    rate = _f(gst_rate)
    gst = round(tax_base * rate / 100.0, 2)
    if interstate:
        cgst = 0.0
        sgst = 0.0
        igst = gst
    else:
        cgst = round(gst / 2, 2)
        sgst = round(gst - cgst, 2)  # residual -> exact sum
        igst = 0.0
    return {
        "taxable": tax_base,
        "gst_rate": rate,
        "gst": gst,
        "cgst": cgst,
        "sgst": sgst,
        "igst": igst,
        "line_total": round(tax_base + gst, 2),
    }


def _line_taxable(line: dict) -> float:
    """Taxable value of a line: explicit `taxable` if present, else qty*unit_price."""
    if line.get("taxable") is not None:
        return _f(line.get("taxable"))
    return round(_f(line.get("qty")) * _f(line.get("unit_price")), 2)


def compute_invoice(
    lines: List[dict],
    supplier_gstin: Optional[str],
    recipient_gstin: Optional[str],
    explicit_place_of_supply: Optional[str] = None,
) -> dict:
    """Compute a fully-split purchase invoice from raw lines.

    Each input line is a dict with any of: product_id, description, hsn, qty,
    unit_price, taxable, gst_rate. Returns:

        {
          place_of_supply,        # the LEGAL place of supply = recipient state
          itc_place_of_supply,    # what to STORE for the ITC register's test
          supplier_state, recipient_state, interstate,
          lines: [ {<input fields>, taxable, gst_rate, cgst, sgst, igst,
                    line_total} ],
          taxable_total, cgst_total, sgst_total, igst_total, tax_total, total
        }

    The inter-state vs intra-state classification is decided ONCE for the whole
    invoice from supplier vs recipient state (a single tax invoice has a single
    place of supply), then applied per line. Totals are summed from the rounded
    per-line numbers so header == sum(lines) to the paisa.

    `itc_place_of_supply` vs `place_of_supply` -- WHY TWO FIELDS:
    services/itc_reconcile.build_itc_register decides inter-state by comparing
    the STORED place_of_supply against the recipient ENTITY's primary state
    (pos != entity_state => IGST). For that pre-existing test to fire on an
    inter-state purchase, the stored value must be the COUNTERPARTY (supplier)
    state -- which differs from the buyer-entity state exactly when the purchase
    is inter-state. So `itc_place_of_supply` is the supplier state (what the
    router writes onto the doc's `place_of_supply` field, keeping the register
    working unchanged), while `place_of_supply` is the legal recipient state for
    display. When supplier state is unknown we fall back to the recipient state
    (intra-state by default -- the register's own missing-value behaviour).
    """
    pos, interstate = determine_place_of_supply(
        supplier_gstin, recipient_gstin, explicit_place_of_supply
    )
    supplier_state = state_code_of(supplier_gstin)
    recipient_state = state_code_of(explicit_place_of_supply) or state_code_of(
        recipient_gstin
    )
    # The value the ITC register keys on: the supplier (counterparty) state, so
    # `stored_pos != recipient_entity_state` is True iff the buy is inter-state.
    # Fall back to the recipient state when the supplier is unknown (keeps the
    # register's "missing -> intra" default).
    itc_pos = supplier_state or recipient_state or None

    out_lines: List[dict] = []
    taxable_total = 0.0
    cgst_total = 0.0
    sgst_total = 0.0
    igst_total = 0.0

    for raw in lines or []:
        if not isinstance(raw, dict):
            continue
        taxable = _line_taxable(raw)
        split = split_line_gst(taxable, raw.get("gst_rate"), interstate)
        line = {
            "product_id": raw.get("product_id"),
            "description": raw.get("description"),
            "hsn": raw.get("hsn"),
            "qty": _f(raw.get("qty")),
            "unit_price": _f(raw.get("unit_price")),
            "taxable": split["taxable"],
            "gst_rate": split["gst_rate"],
            "cgst": split["cgst"],
            "sgst": split["sgst"],
            "igst": split["igst"],
            "line_total": split["line_total"],
        }
        out_lines.append(line)
        taxable_total = round(taxable_total + split["taxable"], 2)
        cgst_total = round(cgst_total + split["cgst"], 2)
        sgst_total = round(sgst_total + split["sgst"], 2)
        igst_total = round(igst_total + split["igst"], 2)

    tax_total = round(cgst_total + sgst_total + igst_total, 2)
    total = round(taxable_total + tax_total, 2)

    return {
        # Legal place of supply (recipient state) -- for display.
        "place_of_supply": pos,
        # What the router STORES on the doc so itc_reconcile classifies right.
        "itc_place_of_supply": itc_pos,
        "supplier_state": supplier_state or None,
        "recipient_state": recipient_state or None,
        "interstate": interstate,
        "lines": out_lines,
        "taxable_total": taxable_total,
        "cgst_total": cgst_total,
        "sgst_total": sgst_total,
        "igst_total": igst_total,
        "tax_total": tax_total,
        "total": total,
    }


def lines_from_grn(grn: dict, po: Optional[dict]) -> List[dict]:
    """Build draft invoice lines from a GRN's accepted quantities + the PO's
    unit price / tax rate. Pure: takes the two docs, returns raw line dicts
    (description / hsn / qty / unit_price / gst_rate) ready for compute_invoice.

    Matching: a GRN line carries product_id (+ po_item_id); the PO line carries
    unit_price + tax_rate. We index the PO by product_id (the stable join the
    rest of vendors.py uses for receipt reconciliation) and, when multiple PO
    lines share a product, fall back to a po_item_id match. Quantity is the
    ACCEPTED qty (units that actually entered stock and are billable); a line
    with accepted_qty <= 0 (fully rejected) is skipped -- those go on a debit
    note, not the purchase invoice.
    """
    if not isinstance(grn, dict):
        return []
    po_items = []
    if isinstance(po, dict) and isinstance(po.get("items"), list):
        po_items = [it for it in po["items"] if isinstance(it, dict)]

    by_product: dict = {}
    by_item_id: dict = {}
    for it in po_items:
        pid = it.get("product_id")
        if pid is not None and pid not in by_product:
            by_product[pid] = it
        iid = it.get("item_id") or it.get("po_item_id")
        if iid is not None:
            by_item_id[iid] = it

    lines: List[dict] = []
    for gi in grn.get("items", []) or []:
        if not isinstance(gi, dict):
            continue
        try:
            accepted = int(gi.get("accepted_qty", 0) or 0)
        except (TypeError, ValueError):
            accepted = 0
        if accepted <= 0:
            continue
        pid = gi.get("product_id")
        po_line = by_product.get(pid)
        if po_line is None:
            po_line = by_item_id.get(gi.get("po_item_id"))
        po_line = po_line or {}
        lines.append(
            {
                "product_id": pid,
                "description": gi.get("product_name")
                or po_line.get("product_name")
                or po_line.get("sku"),
                "hsn": gi.get("hsn") or po_line.get("hsn"),
                "qty": accepted,
                "unit_price": _f(po_line.get("unit_price")),
                "gst_rate": _f(po_line.get("tax_rate")),
            }
        )
    return lines
