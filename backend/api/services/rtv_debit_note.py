"""RTV (Return-To-Vendor) Debit Note -- GST-compliant accounting document.

Feature #20. A DEBIT NOTE is the formal GST document issued to a vendor when
goods are returned to them (the physical RTV / N4 RMA / vendor_return already
moved the stock). The debit note DEBITS the vendor: it reduces accounts payable
and lets the business reverse the input-tax-credit claimed on the original
purchase. This module is a DOCUMENT / accounting layer that sits on top of the
existing vendor-return / RMA. It does NOT touch the RMA state machine nor the
stock movement.

What this REUSES (no fork)
--------------------------
  * GST split convention -- mirrors the SALES-side splitter
    ``orders._build_invoice_gst_split``: place-of-supply from the recipient's
    2-digit GST state code, inter-state => full IGST, intra-state => CGST + SGST
    with the residual paisa pushed onto SGST so CGST + SGST == line tax exactly.
  * State code extraction -- ``org_validation.normalize_state_code`` + the GSTIN
    first-two-chars rule (same as the orders ``_invoice_state_code`` helper).
  * Atomic FY-scoped consecutive serial -- the ``counters`` collection
    ``find_one_and_update($inc)`` pattern from ``je_service._next_je_number`` /
    ``order_repository.next_invoice_number`` (Rule 46(b): a unique serial per
    financial year). Backed by a UNIQUE index on ``debit_note_number``.
  * Tally voucher -- mirrors ``tally_tender_receipt.tally_build_tender_receipt_xml``
    (``_ledger_entry`` sign convention: negative AMOUNT + ISDEEMEDPOSITIVE Yes ==
    debit). Emits a SEPARATE ``VCHTYPE="Debit Note"`` voucher; touches nothing on
    the sales side.

Debit-note document shape (returned by ``build_debit_note``)::

    {
        "debit_note_number": "DN/BV/2026-27/000001",  # consecutive serial per entity+FY
        "financial_year": "2026-27",
        "issue_date": "2026-06-12",
        "entity_id": "...", "store_id": "...",
        "seller": {                                  # us (the issuer)
            "name": ..., "gstin": ..., "state_code": "20", "address": ...,
        },
        "vendor": {                                  # the recipient (debited)
            "vendor_id": ..., "name": ..., "gstin": ..., "state_code": "27", "address": ...,
        },
        "original_invoice": {"number": ..., "date": ...},   # purchase invoice ref
        "rtv_ref": {"type": "vendor_return"|"vendor_rma", "id": ...},
        "is_inter_state": True|False,                # decides IGST vs CGST+SGST
        "place_of_supply": "27",                     # recipient (vendor) state
        "lines": [
            {
                "sku": ..., "description": ..., "hsn": ...,
                "qty": 2, "rate_paise": 150000,      # per-unit taxable rate in paise
                "taxable_paise": 300000,             # qty * rate (line taxable value)
                "gst_rate": 5.0,                     # percent
                "cgst_paise": ..., "sgst_paise": ..., "igst_paise": ...,
                "tax_paise": ...,                    # cgst+sgst+igst
                "line_total_paise": ...,             # taxable + tax
            },
            ...
        ],
        "totals": {
            "taxable_paise": ...,                    # sum of line taxable
            "cgst_paise": ..., "sgst_paise": ..., "igst_paise": ...,
            "tax_paise": ...,                        # cgst+sgst+igst
            "grand_total_paise": ...,                # taxable + tax
        },
    }

All money is INTEGER PAISE. GST split is paise-exact and matches the sales-side
splitter (CGST = round-down half, SGST = tax - CGST residual).

Conventions (CLAUDE.md): NO emoji (Windows cp1252); ASCII log tag [RTV_DN].
Fail-soft: ``db is None`` => reads empty, writes a structured error, never raises.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

COLLECTION = "debit_notes"
COUNTERS_COLLECTION = "counters"
AUDIT_COLLECTION = "audit_logs"


# ============================================================================
# Money helpers (paise-exact; mirror vendor_rma conventions)
# ============================================================================


def rupees_to_paise(amount: Any) -> int:
    """Convert a rupee amount to integer paise, rounding half-up. Negative /
    non-numeric -> 0 (defensive; the schema layer also validates)."""
    try:
        d = Decimal(str(amount))
    except Exception:  # noqa: BLE001
        return 0
    if d <= 0:
        return 0
    paise = (d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(paise)


def paise_to_rupees(paise: Any) -> float:
    """Render integer paise to a rupee float for API display ONLY."""
    try:
        return round(int(paise) / 100.0, 2)
    except Exception:  # noqa: BLE001
        return 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================
# GST state code + financial year (REUSED helpers, no fork)
# ============================================================================


def state_code_of(*candidates: Any) -> str:
    """Best-effort 2-digit GST state code from the first usable candidate.
    Accepts a 2-digit code, a 2-letter / full state name, or a 15-char GSTIN
    (state = first two chars). Mirrors orders._invoice_state_code. Never raises."""
    try:
        from api.services.org_validation import (
            normalize_state_code,
            INDIAN_STATE_CODES,
        )
    except Exception:  # noqa: BLE001
        normalize_state_code = None
        INDIAN_STATE_CODES = {}

    def _valid_code(code: Any) -> str:
        c = str(code or "").strip()
        if len(c) == 2 and c.isdigit() and (not INDIAN_STATE_CODES or c in INDIAN_STATE_CODES):
            return c
        return ""

    for cand in candidates:
        s = str(cand or "").strip()
        if not s:
            continue
        # A GSTIN: first two chars are the state code.
        if len(s) == 15:
            code = _valid_code(s[:2])
            if code:
                return code
        # A direct 2-digit code.
        code = _valid_code(s)
        if code:
            return code
        # A name / abbreviation routed through the canonical normalizer.
        if normalize_state_code is not None:
            try:
                norm = normalize_state_code(s)
            except Exception:  # noqa: BLE001
                norm = None
            code = _valid_code(norm)
            if code:
                return code
    return ""


def financial_year_label(dt: Optional[datetime] = None) -> str:
    """Indian FY label (e.g. ``2026-27``) for an instant (FY starts 1 April IST).
    Reuses ``utils.ist.fy_start_year_ist`` with a safe inline fallback."""
    try:
        from api.utils.ist import fy_start_year_ist

        start = fy_start_year_ist(dt)
    except Exception:  # noqa: BLE001
        d = dt or _now()
        start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


# ============================================================================
# GST split (MIRRORS orders._build_invoice_gst_split -- paise-integer variant)
# ============================================================================


def _split_line_tax(taxable_paise: int, gst_rate: float, is_inter_state: bool) -> Dict[str, int]:
    """Split a line's tax into CGST/SGST/IGST in integer paise.

    The tax is computed from the taxable value at the line's GST rate, then split
    by the SAME convention the sales invoice uses: inter-state => full IGST;
    intra-state => CGST = floor(tax/2), SGST = tax - CGST (residual on SGST) so
    CGST + SGST == tax to the paise. Round-half-up on the rate application."""
    try:
        rate = Decimal(str(gst_rate or 0))
    except Exception:  # noqa: BLE001
        rate = Decimal(0)
    tax_paise = int(
        (Decimal(int(taxable_paise)) * rate / Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    if tax_paise < 0:
        tax_paise = 0
    if is_inter_state:
        return {"cgst_paise": 0, "sgst_paise": 0, "igst_paise": tax_paise, "tax_paise": tax_paise}
    cgst = tax_paise // 2
    sgst = tax_paise - cgst  # residual on SGST -> cgst + sgst == tax exactly
    return {"cgst_paise": cgst, "sgst_paise": sgst, "igst_paise": 0, "tax_paise": tax_paise}


def _norm_line(line: Dict[str, Any], is_inter_state: bool) -> Dict[str, Any]:
    """Normalize one input line into a paise-exact debit-note line. ``rate_paise``
    is authoritative when present (integer paise); else the rupee ``unit_cost`` /
    ``rate`` / ``unit_price`` is converted at the edge."""
    qty = int(line.get("qty") or line.get("quantity") or 0)
    if qty < 0:
        qty = 0
    if line.get("rate_paise") is not None:
        rate_paise = int(line.get("rate_paise") or 0)
    elif line.get("unit_cost_paise") is not None:
        rate_paise = int(line.get("unit_cost_paise") or 0)
    else:
        rate_paise = rupees_to_paise(
            line.get("unit_cost")
            if line.get("unit_cost") is not None
            else (line.get("rate") if line.get("rate") is not None else line.get("unit_price"))
        )
    if rate_paise < 0:
        rate_paise = 0
    taxable_paise = qty * rate_paise
    try:
        gst_rate = float(line.get("gst_rate") or line.get("tax_rate") or 0.0)
    except (TypeError, ValueError):
        gst_rate = 0.0
    tax = _split_line_tax(taxable_paise, gst_rate, is_inter_state)
    return {
        "sku": line.get("sku") or line.get("product_id"),
        "description": line.get("description") or line.get("product_name") or "",
        "hsn": str(line.get("hsn") or line.get("hsn_code") or "").strip(),
        "qty": qty,
        "rate_paise": rate_paise,
        "taxable_paise": taxable_paise,
        "gst_rate": gst_rate,
        "cgst_paise": tax["cgst_paise"],
        "sgst_paise": tax["sgst_paise"],
        "igst_paise": tax["igst_paise"],
        "tax_paise": tax["tax_paise"],
        "line_total_paise": taxable_paise + tax["tax_paise"],
    }


def build_debit_note(
    rtv_doc: Dict[str, Any],
    vendor: Dict[str, Any],
    lines: List[Dict[str, Any]],
    serial: str,
    *,
    seller: Optional[Dict[str, Any]] = None,
    financial_year: Optional[str] = None,
    issue_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the GST-compliant debit-note document (PURE -- no DB).

    ``rtv_doc``  the source vendor-return / RMA (carries store_id, original
                 purchase-invoice ref, and -- if its own lines aren't passed
                 explicitly via ``lines`` -- the line items).
    ``vendor``   the recipient (debited) -- name, gstin, state, address.
    ``lines``    explicit line items; falls back to ``rtv_doc['items']`` /
                 ``rtv_doc['lines']`` when empty.
    ``serial``   the pre-allocated FY-scoped debit-note number (the router mints
                 it atomically; this builder is pure and never touches the DB).
    ``seller``   our issuing entity (name/gstin/state/address). State decides
                 intra-vs-inter against the vendor's state.

    Inter-vs-intra: the vendor (recipient of the return) is the place-of-supply.
    inter-state when BOTH state codes are known and differ; missing -> assume
    intra (CGST+SGST), the safe single-state default (matches the sales splitter).
    """
    rtv_doc = rtv_doc if isinstance(rtv_doc, dict) else {}
    vendor = vendor if isinstance(vendor, dict) else {}
    seller = seller if isinstance(seller, dict) else {}

    seller_gstin = str(seller.get("gstin") or "").strip()
    vendor_gstin = str(vendor.get("gstin") or "").strip()
    seller_state = state_code_of(seller.get("state_code"), seller_gstin, seller.get("state"))
    vendor_state = state_code_of(
        vendor.get("state_code"), vendor_gstin, vendor.get("state"),
        (vendor.get("address") or {}).get("state_code") if isinstance(vendor.get("address"), dict) else None,
    )
    if seller_state and vendor_state:
        is_inter_state = seller_state != vendor_state
    else:
        is_inter_state = False  # safe intra default (CGST+SGST)
    place_of_supply = vendor_state or seller_state

    src_lines = lines or rtv_doc.get("lines") or rtv_doc.get("items") or []
    norm_lines = [_norm_line(ln, is_inter_state) for ln in src_lines if isinstance(ln, dict)]

    totals = {
        "taxable_paise": sum(ln["taxable_paise"] for ln in norm_lines),
        "cgst_paise": sum(ln["cgst_paise"] for ln in norm_lines),
        "sgst_paise": sum(ln["sgst_paise"] for ln in norm_lines),
        "igst_paise": sum(ln["igst_paise"] for ln in norm_lines),
    }
    totals["tax_paise"] = totals["cgst_paise"] + totals["sgst_paise"] + totals["igst_paise"]
    totals["grand_total_paise"] = totals["taxable_paise"] + totals["tax_paise"]

    fy = financial_year or financial_year_label()
    issued = issue_date or _now().date().isoformat()

    rtv_type = "vendor_rma" if rtv_doc.get("rma_id") else "vendor_return"
    rtv_id = rtv_doc.get("rma_id") or rtv_doc.get("return_id") or rtv_doc.get("id")

    return {
        "debit_note_number": serial,
        "financial_year": fy,
        "issue_date": issued,
        "entity_id": rtv_doc.get("entity_id") or seller.get("entity_id"),
        "store_id": rtv_doc.get("store_id"),
        "seller": {
            "name": seller.get("name") or seller.get("entity_name") or "",
            "gstin": seller_gstin,
            "state_code": seller_state,
            "address": seller.get("address") or "",
        },
        "vendor": {
            "vendor_id": vendor.get("vendor_id") or rtv_doc.get("vendor_id"),
            "name": vendor.get("name") or vendor.get("vendor_name") or rtv_doc.get("vendor_name") or "",
            "gstin": vendor_gstin,
            "state_code": vendor_state,
            "address": vendor.get("address") or "",
        },
        "original_invoice": {
            "number": rtv_doc.get("purchase_invoice_number")
            or rtv_doc.get("original_invoice_number")
            or rtv_doc.get("po_id"),
            "date": rtv_doc.get("purchase_invoice_date") or rtv_doc.get("original_invoice_date"),
        },
        "rtv_ref": {"type": rtv_type, "id": rtv_id},
        "is_inter_state": is_inter_state,
        "place_of_supply": place_of_supply,
        "lines": norm_lines,
        "totals": totals,
    }


# ============================================================================
# Printable HTML (restrained, light; mirrors finance/invoice print)
# ============================================================================


def render_debit_note_html(note: Dict[str, Any]) -> str:
    """Render a printable GST debit note as a self-contained light HTML page.
    Pure; uses the doc shape from ``build_debit_note``."""
    note = note if isinstance(note, dict) else {}
    seller = note.get("seller") or {}
    vendor = note.get("vendor") or {}
    orig = note.get("original_invoice") or {}
    totals = note.get("totals") or {}
    inter = bool(note.get("is_inter_state"))

    def _r(p: Any) -> str:
        return f"{paise_to_rupees(p):,.2f}"

    rows = []
    for i, ln in enumerate(note.get("lines") or [], start=1):
        tax_cells = (
            f"<td class='num'>{_r(ln.get('igst_paise'))}</td>"
            if inter
            else f"<td class='num'>{_r(ln.get('cgst_paise'))}</td>"
            f"<td class='num'>{_r(ln.get('sgst_paise'))}</td>"
        )
        rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{escape(str(ln.get('description') or ''))}</td>"
            f"<td>{escape(str(ln.get('hsn') or ''))}</td>"
            f"<td class='num'>{int(ln.get('qty') or 0)}</td>"
            f"<td class='num'>{_r(ln.get('rate_paise'))}</td>"
            f"<td class='num'>{_r(ln.get('taxable_paise'))}</td>"
            f"<td class='num'>{float(ln.get('gst_rate') or 0):g}%</td>"
            f"{tax_cells}"
            f"<td class='num'>{_r(ln.get('line_total_paise'))}</td>"
            "</tr>"
        )

    tax_headers = (
        "<th>IGST</th>" if inter else "<th>CGST</th><th>SGST</th>"
    )
    tax_total_cells = (
        f"<td class='num'>{_r(totals.get('igst_paise'))}</td>"
        if inter
        else f"<td class='num'>{_r(totals.get('cgst_paise'))}</td>"
        f"<td class='num'>{_r(totals.get('sgst_paise'))}</td>"
    )
    colspan_inter = 9 if inter else 10

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Debit Note {escape(str(note.get('debit_note_number') or ''))}</title>
<style>
  body {{ font-family: Arial, Helvetica, sans-serif; color: #1a1a1a; margin: 24px; font-size: 13px; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; letter-spacing: .5px; }}
  .muted {{ color: #666; }}
  .meta, .parties {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
  .parties td {{ vertical-align: top; width: 50%; padding: 8px; border: 1px solid #ddd; }}
  table.items {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  table.items th, table.items td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
  table.items th {{ background: #f5f5f5; font-weight: 600; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .total-row td {{ font-weight: 600; background: #fafafa; }}
  .label {{ font-size: 11px; text-transform: uppercase; color: #888; letter-spacing: .4px; }}
</style></head>
<body>
  <h1>DEBIT NOTE</h1>
  <div class="muted">Return to Vendor (GST debit note)</div>
  <table class="meta"><tr>
    <td><span class="label">Debit Note No.</span><br>{escape(str(note.get('debit_note_number') or ''))}</td>
    <td><span class="label">Date</span><br>{escape(str(note.get('issue_date') or ''))}</td>
    <td><span class="label">Financial Year</span><br>{escape(str(note.get('financial_year') or ''))}</td>
    <td><span class="label">Place of Supply</span><br>{escape(str(note.get('place_of_supply') or ''))}</td>
  </tr></table>
  <table class="parties"><tr>
    <td>
      <span class="label">Issued By (Debiting)</span><br>
      <strong>{escape(str(seller.get('name') or ''))}</strong><br>
      GSTIN: {escape(str(seller.get('gstin') or ''))}<br>
      {escape(str(seller.get('address') or ''))}
    </td>
    <td>
      <span class="label">Vendor (Debited)</span><br>
      <strong>{escape(str(vendor.get('name') or ''))}</strong><br>
      GSTIN: {escape(str(vendor.get('gstin') or ''))}<br>
      {escape(str(vendor.get('address') or ''))}
    </td>
  </tr></table>
  <div class="muted">Against Original Purchase Invoice:
    {escape(str(orig.get('number') or '-'))} dated {escape(str(orig.get('date') or '-'))}
    &nbsp;|&nbsp; RTV Ref: {escape(str((note.get('rtv_ref') or {}).get('id') or '-'))}
  </div>
  <table class="items">
    <thead><tr>
      <th>#</th><th>Description</th><th>HSN</th><th>Qty</th><th>Rate</th>
      <th>Taxable</th><th>GST%</th>{tax_headers}<th>Total</th>
    </tr></thead>
    <tbody>
      {''.join(rows)}
      <tr class="total-row">
        <td colspan="{colspan_inter - 4}">Totals</td>
        <td class="num">{_r(totals.get('taxable_paise'))}</td>
        <td></td>{tax_total_cells}
        <td class="num">{_r(totals.get('grand_total_paise'))}</td>
      </tr>
    </tbody>
  </table>
  <p class="muted" style="margin-top:18px">Total Tax: {_r(totals.get('tax_paise'))} &nbsp;|&nbsp;
     Grand Total (Debited to Vendor): <strong>{_r(totals.get('grand_total_paise'))}</strong></p>
</body></html>"""


# ============================================================================
# Tally Debit Note voucher (MIRRORS tally_tender_receipt -- no fork)
# ============================================================================


def _ledger_entry(name: str, xml_amount: float) -> str:
    """One ALLLEDGERENTRIES.LIST block (same convention as the Sales / Receipt
    builders): a NEGATIVE amount is a debit (ISDEEMEDPOSITIVE Yes), a positive
    amount is a credit (ISDEEMEDPOSITIVE No)."""
    deemed = "Yes" if xml_amount < 0 else "No"
    return f"""
    <ALLLEDGERENTRIES.LIST>
      <LEDGERNAME>{escape(str(name))}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE>
      <AMOUNT>{xml_amount:.2f}</AMOUNT>
    </ALLLEDGERENTRIES.LIST>"""


def tally_build_debit_note_xml(
    note: Dict[str, Any],
    *,
    purchase_ledger: str = "Purchase Returns",
    input_cgst_ledger: str = "Input CGST",
    input_sgst_ledger: str = "Input SGST",
    input_igst_ledger: str = "Input IGST",
) -> str:
    """Build a Tally import XML carrying ONE ``VCHTYPE="Debit Note"`` voucher for
    the debit note. Mirrors the Receipt-voucher builder's sign convention +
    envelope; emits a SEPARATE voucher stream (never modifies sales vouchers).

    Accounting (a debit note returning goods to a vendor):
      * DEBIT  the vendor party ledger by the grand total (reduces payable) ->
        negative AMOUNT + ISDEEMEDPOSITIVE Yes.
      * CREDIT the Purchase Returns ledger by the taxable value (reverses the
        purchase) -> positive AMOUNT.
      * CREDIT the input-tax ledgers (reverses the ITC claimed) by CGST+SGST or
        IGST -> positive AMOUNT.

    Balanced by construction: debit(grand) == credit(taxable) + credit(tax). A
    paise-exact assertion runs before emit (fail loudly -- Tally would reject an
    unbalanced voucher anyway)."""
    note = note if isinstance(note, dict) else {}
    totals = note.get("totals") or {}
    inter = bool(note.get("is_inter_state"))

    taxable = paise_to_rupees(totals.get("taxable_paise"))
    cgst = paise_to_rupees(totals.get("cgst_paise"))
    sgst = paise_to_rupees(totals.get("sgst_paise"))
    igst = paise_to_rupees(totals.get("igst_paise"))
    grand = paise_to_rupees(totals.get("grand_total_paise"))

    party = escape(str((note.get("vendor") or {}).get("name") or "Vendor"))
    dn_number = escape(str(note.get("debit_note_number") or ""))
    issue = str(note.get("issue_date") or _now().date().isoformat())
    vch_date = issue.replace("-", "")[:8]  # yyyymmdd

    # Build the legs (signed): vendor debit (negative), then the credit legs.
    legs: List[Dict[str, Any]] = []
    legs.append({"ledger": party, "amount": -grand})  # debit vendor (reduce payable)
    legs.append({"ledger": purchase_ledger, "amount": taxable})  # credit purchase returns
    if inter:
        if igst:
            legs.append({"ledger": input_igst_ledger, "amount": igst})
    else:
        if cgst:
            legs.append({"ledger": input_cgst_ledger, "amount": cgst})
        if sgst:
            legs.append({"ledger": input_sgst_ledger, "amount": sgst})

    # Balance assertion (paise-exact). Sum of signed amounts must be ~0.
    paise_sum = sum(int(round(float(leg["amount"]) * 100)) for leg in legs)
    if paise_sum != 0:
        raise ValueError(f"debit_note_voucher_unbalanced:{paise_sum}")

    entries = "".join(_ledger_entry(leg["ledger"], float(leg["amount"])) for leg in legs)

    voucher = f"""
  <VOUCHER VCHTYPE="Debit Note" ACTION="Create">
    <DATE>{vch_date}</DATE>
    <VOUCHERTYPENAME>Debit Note</VOUCHERTYPENAME>
    <VOUCHERNUMBER>{dn_number}</VOUCHERNUMBER>
    <PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>
    <NARRATION>Return to vendor debit note {dn_number}</NARRATION>{entries}
  </VOUCHER>"""

    return f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>{voucher}
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""


# ============================================================================
# DB engine: atomic FY serial + idempotent issue + list/get
# ============================================================================


def _counters_coll(db):
    if db is None:
        return None
    try:
        return db.get_collection(COUNTERS_COLLECTION)
    except Exception:  # noqa: BLE001
        try:
            return db[COUNTERS_COLLECTION]
        except Exception:  # noqa: BLE001
            return None


def _entity_prefix(db, entity_id: Optional[str]) -> str:
    """Short entity prefix for the debit-note number (best-effort; default GEN).
    Mirrors je_service._entry_prefix."""
    if not entity_id:
        return "GEN"
    if db is not None:
        try:
            ent = db.get_collection("entities").find_one({"entity_id": entity_id}) or {}
            code = (
                ent.get("code")
                or ent.get("entity_code")
                or ent.get("short_code")
                or ""
            ).strip()
            if code:
                return code.upper()[:6]
        except Exception:  # noqa: BLE001
            pass
    return str(entity_id).upper()[:6]


def next_debit_note_number(
    db, entity_id: Optional[str], issue_dt: Optional[datetime] = None
) -> str:
    """Mint an FY-scoped consecutive debit-note number via an atomic counter
    (``counters.find_one_and_update($inc)``) -- the SAME pattern as
    ``je_service._next_je_number`` / ``order_repository.next_invoice_number``.

    Format ``DN/{prefix}/{FY}/{serial:06d}`` e.g. DN/BV/2026-27/000001. The
    counter key is per (entity, FY-start) so each legal entity gets its own
    consecutive series per financial year (Rule 46(b))."""
    from pymongo import ReturnDocument

    dt = issue_dt or _now()
    fy_label = financial_year_label(dt)
    fy_start = fy_label.split("-")[0]
    prefix = _entity_prefix(db, entity_id)
    key = f"debit_note:{entity_id or 'GEN'}:{fy_start}"

    seq = 1
    coll = _counters_coll(db)
    if coll is not None:
        try:
            doc = coll.find_one_and_update(
                {"_id": key},
                {"$inc": {"seq": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            seq = int((doc or {}).get("seq") or 1)
        except Exception as e:  # noqa: BLE001
            logger.warning("[RTV_DN] counter %s failed: %s", key, e)
            seq = int(_now().timestamp())  # fail-soft unique-ish fallback
    return f"DN/{prefix}/{fy_label}/{seq:06d}"


class DebitNoteEngine:
    """Persistence + idempotency for issued debit notes. Fail-soft on db=None.

    Idempotency: at most ONE debit note per source RTV (``rtv_ref.id``). A
    UNIQUE index on ``rtv_ref_id`` is the DB backstop; ``issue`` does a
    read-then-insert and returns the existing note on a re-issue (no double
    serial). The note number carries its OWN unique index for Rule 46(b)."""

    def __init__(self, db=None):
        self._db = db

    def _coll(self):
        if self._db is None:
            return None
        try:
            return self._db.get_collection(COLLECTION)
        except Exception:  # noqa: BLE001
            try:
                return self._db[COLLECTION]
            except Exception:  # noqa: BLE001
                return None

    def _audit_coll(self):
        if self._db is None:
            return None
        try:
            return self._db.get_collection(AUDIT_COLLECTION)
        except Exception:  # noqa: BLE001
            try:
                return self._db[AUDIT_COLLECTION]
            except Exception:  # noqa: BLE001
                return None

    def ensure_indexes(self) -> None:
        """Idempotent index creation. Best-effort; never raises.
          * UNIQUE debit_note_number -> Rule 46(b) consecutive-serial backstop.
          * UNIQUE rtv_ref_id -> one debit note per source RTV (idempotency)."""
        coll = self._coll()
        if coll is None:
            return
        try:
            coll.create_index("debit_note_id", unique=True)
            coll.create_index("debit_note_number", unique=True)
            coll.create_index("rtv_ref_id", unique=True)
            coll.create_index([("store_id", 1), ("issue_date", -1)])
            coll.create_index([("vendor.vendor_id", 1), ("issue_date", -1)])
        except Exception:  # noqa: BLE001
            logger.debug("[RTV_DN] ensure_indexes skipped", exc_info=True)

    def get(self, debit_note_id: str) -> Optional[Dict[str, Any]]:
        coll = self._coll()
        if coll is None:
            return None
        try:
            return coll.find_one({"debit_note_id": debit_note_id}, {"_id": 0})
        except Exception:  # noqa: BLE001
            return None

    def get_by_rtv(self, rtv_ref_id: str) -> Optional[Dict[str, Any]]:
        coll = self._coll()
        if coll is None:
            return None
        try:
            return coll.find_one({"rtv_ref_id": rtv_ref_id}, {"_id": 0})
        except Exception:  # noqa: BLE001
            return None

    def list(
        self,
        *,
        store_id: Optional[str] = None,
        store_ids: Optional[List[str]] = None,
        vendor_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        coll = self._coll()
        if coll is None:
            return []
        q: Dict[str, Any] = {}
        if store_id:
            q["store_id"] = store_id
        elif store_ids is not None:
            q["store_id"] = {"$in": list(store_ids)}
        if vendor_id:
            q["vendor.vendor_id"] = vendor_id
        try:
            return list(
                coll.find(q, {"_id": 0})
                .sort("issue_date", -1)
                .skip(int(skip))
                .limit(int(limit))
            )
        except Exception:  # noqa: BLE001
            return []

    def issue(
        self,
        rtv_doc: Dict[str, Any],
        vendor: Dict[str, Any],
        *,
        actor: str,
        seller: Optional[Dict[str, Any]] = None,
        lines: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Issue (or return the existing) debit note for an RTV. Idempotent: a
        second call for the same RTV returns the FIRST note -- no new serial.
        Fail-soft -> {"ok": False, "error": "no_db"}."""
        coll = self._coll()
        if coll is None:
            return {"ok": False, "http": 503, "error": "no_db"}

        rtv_id = rtv_doc.get("rma_id") or rtv_doc.get("return_id") or rtv_doc.get("id")
        if not rtv_id:
            return {"ok": False, "http": 400, "error": "rtv_ref_required"}

        # Idempotency: a note already exists for this RTV -> return it.
        existing = self.get_by_rtv(str(rtv_id))
        if existing is not None:
            return {"ok": True, "idempotent": True, "debit_note": existing}

        entity_id = rtv_doc.get("entity_id") or (seller or {}).get("entity_id")
        serial = next_debit_note_number(self._db, entity_id)
        note = build_debit_note(rtv_doc, vendor, lines or [], serial, seller=seller)

        debit_note_id = f"DN-{uuid.uuid4().hex[:12].upper()}"
        doc = dict(note)
        doc["debit_note_id"] = debit_note_id
        doc["rtv_ref_id"] = str(rtv_id)
        doc["created_by"] = actor
        doc["created_at"] = _now()

        try:
            coll.insert_one(dict(doc))
        except Exception as e:  # noqa: BLE001 - likely the rtv_ref_id UNIQUE race
            logger.warning("[RTV_DN] issue insert failed for %s: %s", rtv_id, e)
            # A racing duplicate lost the UNIQUE-index race -> return the winner.
            winner = self.get_by_rtv(str(rtv_id))
            if winner is not None:
                return {"ok": True, "idempotent": True, "debit_note": winner}
            return {"ok": False, "http": 500, "error": "write_failed"}

        doc.pop("_id", None)
        self._audit(actor, doc)
        logger.info(
            "[RTV_DN] issued %s for RTV %s (grand=%d paise)",
            serial, rtv_id, int((note.get("totals") or {}).get("grand_total_paise") or 0),
        )
        return {"ok": True, "idempotent": False, "debit_note": doc}

    def _audit(self, actor: str, doc: Dict[str, Any]) -> None:
        """Append a hash-chained DEBIT_NOTE audit row. Fail-soft -> None."""
        coll = self._audit_coll()
        repo = None
        if coll is not None:
            try:
                from database.repositories.audit_repository import AuditRepository

                repo = AuditRepository(coll)
            except Exception:  # noqa: BLE001
                repo = None
        if repo is None:
            try:
                from api.dependencies import get_audit_repository

                repo = get_audit_repository()
            except Exception:  # noqa: BLE001
                repo = None
        if repo is None:
            return
        totals = doc.get("totals") or {}
        try:
            repo.create(
                {
                    "log_id": f"AUD-{uuid.uuid4().hex[:12]}",
                    "action": "debit_note_issued",
                    "entity_type": "DEBIT_NOTE",
                    "entity_id": doc.get("debit_note_id"),
                    "store_id": doc.get("store_id"),
                    "user_id": actor,
                    "actor": actor,
                    "source": "RTV_DEBIT_NOTE",
                    "before_state": None,
                    "after_state": {
                        "debit_note_number": doc.get("debit_note_number"),
                        "rtv_ref_id": doc.get("rtv_ref_id"),
                        "vendor": (doc.get("vendor") or {}).get("name"),
                        "grand_total_paise": totals.get("grand_total_paise"),
                        "tax_paise": totals.get("tax_paise"),
                        "is_inter_state": doc.get("is_inter_state"),
                    },
                    "severity": "INFO",
                    "timestamp": _now(),
                }
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[RTV_DN] audit write failed for %s: %s",
                           doc.get("debit_note_id"), e)
