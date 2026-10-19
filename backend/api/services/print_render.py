"""
IMS 2.0 - Server-side print HTML renderer (delivery challan + estimate)
=======================================================================

Two statutory print documents were missing a backend render endpoint: the
Rule 55 Delivery Challan and the (non-binding) Estimate / Quotation. The
frontend modal components for both already existed but were never fed by the
server. This module is the server-side renderer that closes that gap.

It REUSES the existing primitives instead of re-inventing them:
  * api.services.print_legal  -> LegalHeader / copy_marker_block /
                                 statutory_footer / hsn_tax_summary /
                                 amount_in_words / format_date
  * the order GST math is mirrored by passing each line's already-computed
    taxable + tax to hsn_tax_summary (the same per-rate CGST/SGST/IGST split
    the invoice uses) -- estimates additionally reuse the orders.py
    _compute_per_category_gst so an estimate total == what POS would bill.

Output is a single self-contained HTML string (no external CSS/JS, inline
<style>, A4 @page) so the caller can return it straight from a FastAPI route
with media_type="text/html" and the browser can print it directly. The
aesthetic matches the existing statutory templates: bordered, ALL-CAPS header,
sans-only, light theme. ASCII-only source (Windows cp1252 safe).

Every helper is fail-soft: junk input renders an empty-but-valid document
rather than raising, so a print route never 500s on a malformed order/estimate.
"""

from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional

from .org_validation import state_name
from .print_legal import (
    LegalHeader,
    amount_in_words,
    copy_marker_block,
    declarations,
    format_date,
    hsn_tax_summary,
    statutory_footer,
)


# ---------------------------------------------------------------------------
# small formatting helpers
# ---------------------------------------------------------------------------


def _e(value: Any) -> str:
    """HTML-escape any value to a string. None -> ''."""
    if value is None:
        return ""
    return escape(str(value))


def _money(value: Any) -> str:
    """Render a number as Indian rupee amount: Rs 1,234.50. Fail-soft to Rs 0.00."""
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    neg = n < 0
    n = abs(n)
    whole = int(n)
    paise = int(round((n - whole) * 100))
    if paise == 100:
        whole += 1
        paise = 0
    # Indian grouping: last 3 digits, then groups of 2.
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join(parts) + "," + tail
    else:
        grouped = s
    out = "Rs {0}.{1:02d}".format(grouped, paise)
    return ("- " + out) if neg else out


def _qty(value: Any) -> str:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        n = 0.0
    if n == int(n):
        return str(int(n))
    return "{0:g}".format(n)


# ---------------------------------------------------------------------------
# shared CSS (statutory light theme, A4)
# ---------------------------------------------------------------------------

_BASE_CSS = """
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  font-family: Arial, Helvetica, sans-serif;
  color: #111827; background: #ffffff;
}
.doc {
  max-width: 210mm; margin: 0 auto; padding: 14mm;
  font-size: 12px; line-height: 1.4;
}
.doc .border-box { border: 2px solid #111827; padding: 12px; }
.doc h1 { font-size: 20px; margin: 0; text-transform: uppercase; letter-spacing: 1px; }
.doc h2 { font-size: 16px; margin: 0; text-transform: uppercase; letter-spacing: 2px; text-align: center; }
.doc h3 { font-size: 11px; margin: 0 0 4px 0; text-transform: uppercase; letter-spacing: 0.5px; color: #374151; }
.doc .muted { color: #6b7280; }
.doc .center { text-align: center; }
.doc .right { text-align: right; }
.doc .title-band {
  text-align: center; border-top: 2px solid #111827; border-bottom: 2px solid #111827;
  padding: 6px 0; margin: 10px 0;
}
.doc .not-invoice {
  text-align: center; font-weight: bold; text-transform: uppercase;
  border: 2px solid #b45309; background: #fffbeb; color: #92400e;
  padding: 6px; margin: 8px 0; letter-spacing: 0.5px;
}
.doc .copy-marker {
  text-align: right; font-size: 10px; text-transform: uppercase;
  letter-spacing: 0.5px; color: #374151; margin-bottom: 4px;
}
.doc table { width: 100%; border-collapse: collapse; margin: 8px 0; }
.doc table.lines th, .doc table.lines td {
  border: 1px solid #9ca3af; padding: 6px 8px; text-align: left; vertical-align: top;
}
.doc table.lines th { background: #f3f4f6; text-transform: uppercase; font-size: 10px; letter-spacing: 0.5px; }
.doc table.lines td.num, .doc table.lines th.num { text-align: right; font-variant-numeric: tabular-nums; }
.doc table.lines td.ctr, .doc table.lines th.ctr { text-align: center; }
.doc .kv { width: 100%; border-collapse: collapse; }
.doc .kv td { padding: 2px 0; vertical-align: top; }
.doc .kv td.k { color: #6b7280; width: 38%; padding-right: 8px; }
.doc .meta-grid { display: flex; justify-content: space-between; gap: 12px; margin: 8px 0; }
.doc .meta-grid > div { flex: 1; }
.doc .party-grid { display: flex; gap: 12px; margin: 8px 0; }
.doc .party-grid > div { flex: 1; border: 1px solid #9ca3af; padding: 8px; }
.doc .totals { width: 320px; margin-left: auto; }
.doc .totals td { padding: 4px 8px; }
.doc .totals tr.grand td { border-top: 2px solid #111827; font-weight: bold; font-size: 14px; }
.doc .words { margin: 8px 0; font-style: italic; }
.doc .declaration { margin: 8px 0; font-size: 11px; color: #374151; }
.doc .signs { display: flex; justify-content: space-between; margin-top: 28px; }
.doc .signs > div { text-align: center; width: 30%; }
.doc .signs .line { border-top: 1px solid #6b7280; margin-bottom: 4px; padding-top: 28px; }
.doc .footer { margin-top: 12px; padding-top: 6px; border-top: 1px solid #d1d5db; font-size: 10px; color: #6b7280; text-align: center; }
.no-print { text-align: center; margin: 10px 0; }
.no-print button {
  font: inherit; padding: 8px 18px; border: 1px solid #111827; background: #111827;
  color: #fff; border-radius: 6px; cursor: pointer;
}
@media print {
  .no-print { display: none !important; }
  @page { size: A4; margin: 12mm; }
  html, body { background: #fff; }
  .doc { padding: 0; max-width: none; }
}
"""

_PRINT_BUTTON = (
    '<div class="no-print"><button onclick="window.print()">Print</button></div>'
)


def _page(title: str, body_html: str, auto_print: bool = False) -> str:
    """Wrap a document body fragment in a complete, self-contained HTML page."""
    auto = (
        "<script>window.addEventListener('load',function(){setTimeout("
        "function(){window.print();},250);});</script>"
        if auto_print
        else ""
    )
    return (
        "<!DOCTYPE html><html lang=\"en\"><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>" + _e(title) + "</title>"
        "<style>" + _BASE_CSS + "</style>"
        "</head><body>"
        + _PRINT_BUTTON
        + '<div class="doc">'
        + body_html
        + "</div>"
        + auto
        + "</body></html>"
    )


# ---------------------------------------------------------------------------
# shared header fragment
# ---------------------------------------------------------------------------


def _header_fragment(header: Dict[str, Any], title: str) -> str:
    """Render the statutory header (entity identity + store + meta strip) from a
    print_legal.LegalHeader dict, plus the centered document title band."""
    h = header or {}
    name = _e(h.get("legal_name") or h.get("trade_name") or "")
    trade = _e(h.get("trade_name") or "")
    subtitle = _e(h.get("header_subtitle") or "")
    brand_label = _e(h.get("brand_label") or "")
    logo_url = str(h.get("logo_url") or "").strip()

    supplier_rows = ""
    for k, v in h.get("supplier_kv") or []:
        if not v:
            continue
        supplier_rows += (
            '<tr><td class="k">' + _e(k) + "</td><td>" + _e(v) + "</td></tr>"
        )

    meta_rows = ""
    for k, v in h.get("meta") or []:
        meta_rows += (
            '<tr><td class="k">' + _e(k) + "</td><td>" + _e(v) + "</td></tr>"
        )

    store_name = _e(h.get("store_name") or "")
    store_addr = _e(h.get("store_address") or "")
    store_contact_parts = [
        p for p in [h.get("store_phone"), h.get("store_email")] if p
    ]
    store_contact = _e(" | ".join(str(p) for p in store_contact_parts))

    copy = h.get("copy_marker") or {}
    copy_rendered = _e(copy.get("active") or "")
    copy_html = (
        '<div class="copy-marker">' + copy_rendered + "</div>" if copy_rendered else ""
    )

    # Per-store/brand logo: render the entity's invoice logo (or a per-brand
    # default) above the legal name. Falls back to no image when none configured.
    logo_html = (
        '<div class="center"><img src="'
        + _e(logo_url)
        + '" alt="logo" style="max-height:60px;max-width:240px;margin-bottom:6px"></div>'
        if logo_url
        else ""
    )

    head = (
        '<div class="border-box">'
        + copy_html
        + '<div class="center">'
        + logo_html
        + "<h1>" + name + "</h1>"
        + ("<div>" + trade + "</div>" if trade and trade != name else "")
        + (
            '<div class="muted">' + brand_label + "</div>"
            if brand_label and brand_label.lower() not in (name.lower(), trade.lower())
            else ""
        )
        + ('<div class="muted">' + subtitle + "</div>" if subtitle else "")
        + "</div>"
        + '<table class="kv" style="margin-top:8px">'
        + supplier_rows
        + "</table>"
        + "</div>"
    )

    title_band = '<div class="title-band"><h2>' + _e(title) + "</h2></div>"

    meta_block = (
        '<div class="meta-grid">'
        + "<div><h3>Branch / Place of issue</h3>"
        + "<div><strong>" + store_name + "</strong></div>"
        + ("<div>" + store_addr + "</div>" if store_addr else "")
        + ("<div>" + store_contact + "</div>" if store_contact else "")
        + "</div>"
        + '<div class="right"><table class="kv" style="margin-left:auto;max-width:260px">'
        + meta_rows
        + "</table></div>"
        + "</div>"
    )

    return head + title_band + meta_block


def supplier_identity_html(header: Dict[str, Any]) -> str:
    """Render a compact, self-contained issuing-store identity block from a
    print_legal.LegalHeader dict. Used by simpler templates (e.g. the Rx card)
    that want the store/entity identity header but not the full A4 challan
    layout. Inline-styled so it renders identically inside any page CSS.

    Shows: logo (when configured), legal name, trade name, registered office,
    store branch + address, GSTIN, PAN, and -- for rx_card -- NCAHP/DMC reg.
    """
    h = header or {}
    name = _e(h.get("legal_name") or h.get("trade_name") or "")
    trade = _e(h.get("trade_name") or "")
    brand_label = _e(h.get("brand_label") or "")
    logo_url = str(h.get("logo_url") or "").strip()
    store_name = _e(h.get("store_name") or "")
    store_addr = _e(h.get("store_address") or "")
    store_contact = _e(
        " | ".join(
            str(p) for p in [h.get("store_phone"), h.get("store_email")] if p
        )
    )

    rows = ""
    for k, v in h.get("supplier_kv") or []:
        if not v:
            continue
        rows += (
            '<tr><td style="color:#666;padding:1px 8px 1px 0;'
            'text-transform:uppercase;font-size:10px;white-space:nowrap;'
            'vertical-align:top">'
            + _e(k)
            + '</td><td style="padding:1px 0;font-size:11px">'
            + _e(v)
            + "</td></tr>"
        )

    logo_html = (
        '<img src="' + _e(logo_url) + '" alt="logo" '
        'style="max-height:54px;max-width:200px;margin-bottom:4px">'
        if logo_url
        else ""
    )
    brand_html = (
        '<div style="color:#666;font-size:10px;text-transform:uppercase;'
        'letter-spacing:.08em">' + brand_label + "</div>"
        if brand_label
        and brand_label.lower() not in (name.lower(), trade.lower())
        else ""
    )
    trade_html = (
        '<div style="color:#444;font-size:11px">' + trade + "</div>"
        if trade and trade != name
        else ""
    )
    branch_html = ""
    if store_name or store_addr:
        branch_html = (
            '<div style="margin-top:6px;font-size:11px;color:#333">'
            + ("<strong>" + store_name + "</strong>" if store_name else "")
            + ("<div>" + store_addr + "</div>" if store_addr else "")
            + ("<div>" + store_contact + "</div>" if store_contact else "")
            + "</div>"
        )

    return (
        '<div style="border:1.5px solid #111;padding:10px 14px;'
        'margin-bottom:14px;text-align:center;font-family:Arial,Helvetica,'
        'sans-serif;color:#111">'
        + logo_html
        + '<div style="font-size:16px;font-weight:700;text-transform:uppercase;'
        'letter-spacing:.5px">' + name + "</div>"
        + trade_html
        + brand_html
        + branch_html
        + '<table style="margin:8px auto 0;border-collapse:collapse;'
        'text-align:left">'
        + rows
        + "</table>"
        + "</div>"
    )


def _not_a_tax_invoice(text: str) -> str:
    return '<div class="not-invoice">' + _e(text) + "</div>"


def _signs(left: str, mid: str, right: str) -> str:
    return (
        '<div class="signs">'
        + '<div><div class="line"></div>' + _e(left) + "</div>"
        + '<div><div class="line"></div>' + _e(mid) + "</div>"
        + '<div><div class="line"></div>' + _e(right) + "</div>"
        + "</div>"
    )


def _footer(doc_type: str, retain_years: int = 7, extra: str = "") -> str:
    line = statutory_footer(doc_type, retain_years)
    sys_line = "System-generated document. No signature required if digitally issued."
    out = '<div class="footer">' + _e(line)
    if extra:
        out += " &middot; " + _e(extra)
    out += "<br>" + _e(sys_line) + "</div>"
    return out


# ===========================================================================
# DELIVERY CHALLAN (Rule 55) -- goods moved without an invoice
# ===========================================================================


def render_delivery_challan(
    *,
    entity: Optional[Dict[str, Any]],
    store: Optional[Dict[str, Any]],
    challan_number: str,
    challan_date: Any,
    consignee_name: str = "",
    consignee_address: str = "",
    from_label: str = "",
    to_label: str = "",
    items: Optional[List[Dict[str, Any]]] = None,
    notes: str = "",
    copy_marker: str = "ORIGINAL",
    place_of_supply: Any = None,
    transport_reason: str = "",
    overrides: Optional[Dict[str, Any]] = None,
    auto_print: bool = False,
    consignor_gstin: str = "",
    consignee_gstin: str = "",
    valued: bool = False,
) -> str:
    """Render a Rule 55 Delivery Challan as a self-contained HTML page.

    `items` rows accept: product_name/name/description, qty/quantity,
    hsn_code/hsn, serial/serial_numbers/remarks, and -- when `valued` (a move
    between two GST registrations, owner ruling D13) -- rate and value, which
    add Rate / Value columns and a total value. The challan is NOT a tax
    invoice (Rule 55: goods moved without an invoice -- e.g. an inter-store
    transfer, or goods sent for the customer to take delivery of). It carries
    Rule 55 copy markers (CONSIGNEE / TRANSPORTER / CONSIGNOR).

    `overrides` is the per-entity print_template_overrides "fields" dict
    (signatory/footer/logo); the caller loads it and threads it through so
    owner-configured content actually reaches the printed challan.
    """
    items = items or []
    if consignee_gstin and place_of_supply is None:
        # Rule 55: the place of supply is where the goods go -- the consignee
        # registration's state (its GSTIN's first two digits), never the
        # consignor's state the letterhead would default to.
        place_of_supply = "{0} ({1})".format(
            state_name(consignee_gstin[:2]) or "", consignee_gstin[:2]
        ).strip()

    header = LegalHeader(
        entity=entity,
        store=store,
        doc_type="delivery_challan",
        doc_number=challan_number,
        doc_date=challan_date,
        place_of_supply=place_of_supply,
        copy_marker=copy_marker,
        overrides=overrides,
        extra_meta=(
            [("Reason for transport", transport_reason)] if transport_reason else None
        ),
    )
    # Rule 55 copy markers (the LegalHeader uses COPY_MARKER_MODES which does
    # not include delivery_challan -> defaults to rule_48; force rule_55 here).
    header["copy_marker"] = copy_marker_block(copy_marker, mode="rule_55")
    # One consignor registration per page: the letterhead's GSTIN AND its
    # state are the caller's (the one shop rule, org_validation.shop_gstin;
    # the state is the GSTIN's first two digits), never a second answer from
    # print_legal's state match / primary fallback. And one place of supply:
    # the meta row's. The letterhead's shop address (print_legal labels it
    # "Place of supply") is where a challan's goods leave from.
    code = consignor_gstin[:2]
    mine = (
        {
            "GSTIN / UIN": consignor_gstin,
            "State / Code": " / ".join(p for p in (state_name(code), code) if p),
        }
        if consignor_gstin
        else {}
    )
    header["supplier_kv"] = [
        ("Dispatched from" if k == "Place of supply" else k, mine.get(k, v))
        for k, v in header.get("supplier_kv") or []
    ]

    head = _header_fragment(header, "Delivery Challan")
    banner = _not_a_tax_invoice("Delivery Challan - Not a Tax Invoice (CGST Rule 55)")

    # Consignor / consignee block.
    consignor = from_label or (store or {}).get("name") or header.get("store_name") or ""
    party = (
        '<div class="party-grid">'
        + "<div><h3>Consignor (From)</h3><div><strong>"
        + _e(consignor)
        + "</strong></div>"
        + ("<div>GSTIN: " + _e(consignor_gstin) + "</div>" if consignor_gstin else "")
        + "</div>"
        + "<div><h3>Consignee (To)</h3><div><strong>"
        + _e(to_label or consignee_name)
        + "</strong></div>"
        + ("<div>" + _e(consignee_address) + "</div>" if consignee_address else "")
        + ("<div>GSTIN: " + _e(consignee_gstin) + "</div>" if consignee_gstin else "")
        + "</div>"
        + "</div>"
    )

    rows = ""
    total_qty = 0.0
    total_value = 0.0
    for idx, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            continue
        name = (
            it.get("product_name")
            or it.get("name")
            or it.get("description")
            or ""
        )
        hsn = it.get("hsn_code") or it.get("hsn") or ""
        try:
            q = float(it.get("qty") or it.get("quantity") or it.get("quantity_requested") or 0)
        except (TypeError, ValueError):
            q = 0.0
        total_qty += q
        remarks = (
            it.get("serial")
            or it.get("serial_numbers")
            or it.get("remarks")
            or it.get("notes")
            or ""
        )
        rows += (
            "<tr>"
            + '<td class="ctr">' + str(idx) + "</td>"
            + "<td>" + _e(name) + "</td>"
            + "<td>" + _e(hsn) + "</td>"
            + '<td class="num">' + _qty(q) + "</td>"
            + (
                '<td class="num">' + _money(it.get("rate")) + "</td>"
                + '<td class="num">' + _money(it.get("value")) + "</td>"
                if valued
                else ""
            )
            + "<td>" + _e(remarks) + "</td>"
            + "</tr>"
        )
        total_value += float(it.get("value") or 0) if valued else 0.0
    if not rows:
        rows = '<tr><td colspan="{0}" class="ctr muted">No items</td></tr>'.format(
            7 if valued else 5
        )

    table = (
        '<table class="lines">'
        + "<thead><tr>"
        + '<th class="ctr">Sr.</th><th>Description of Goods</th><th>HSN/SAC</th>'
        + '<th class="num">Qty</th>'
        + ('<th class="num">Rate (at cost)</th><th class="num">Value</th>' if valued else "")
        + "<th>Serial / Remarks</th>"
        + "</tr></thead><tbody>"
        + rows
        + "</tbody>"
        + "<tfoot><tr>"
        + '<td colspan="3" class="right"><strong>Total Quantity</strong></td>'
        + '<td class="num"><strong>' + _qty(total_qty) + "</strong></td>"
        + (
            '<td></td><td class="num"><strong>' + _money(round(total_value, 2)) + "</strong></td>"
            if valued
            else ""
        )
        + "<td></td>"
        + "</tr></tfoot>"
        + "</table>"
    )

    notes_html = (
        '<div style="margin:8px 0"><h3>Notes</h3><div>' + _e(notes) + "</div></div>"
        if notes
        else ""
    )

    declaration = (
        '<div class="declaration">This challan accompanies goods being moved '
        "and is NOT a tax invoice. A tax invoice will be raised separately where "
        "applicable per the CGST Act 2017.</div>"
    )

    signs = _signs("Consignor / Dispatched By", "Transporter", "Consignee / Received By")
    footer = _footer(
        "delivery_challan", int(header.get("retention_years") or 7)
    )

    body = (
        head
        + banner
        + party
        + table
        + notes_html
        + declaration
        + signs
        + footer
    )
    return _page("Delivery Challan " + (challan_number or ""), body, auto_print)


# ===========================================================================
# TRANSFER TAX INVOICE + E-WAY BILL DATA -- a move between two registrations
# ===========================================================================

# Rule 138: a consignment over Rs 50,000 (its invoice value, tax included)
# moves on an e-way bill. A state may set its own intra-state limit.
EWAY_BILL_LIMIT = 50000.0


def _state_label(gstin: str) -> str:
    code = (gstin or "")[:2]
    return " / ".join(p for p in (state_name(code), code) if p)


def render_transfer_tax_invoice(
    *,
    entity: Optional[Dict[str, Any]],
    store: Optional[Dict[str, Any]],
    invoice_number: str,
    invoice_date: Any,
    supplier_name: str,
    supplier_address: str,
    supplier_gstin: str,
    recipient_name: str,
    recipient_address: str,
    recipient_gstin: str,
    lines: List[Dict[str, Any]],
    transfer_number: str = "",
    copy_marker: str = "ORIGINAL",
    overrides: Optional[Dict[str, Any]] = None,
    auto_print: bool = False,
) -> str:
    """The tax invoice a stock move between two GST registrations travels on
    (owner / CA 2026-10-18), then the data the GST portal's e-way bill form
    asks for. `lines` are the transfer's per-line taxable + GST rows
    (transfers._mirror_bill_lines on the units shipped: description, hsn,
    qty, unit_price, taxable, gst_rate, cgst, sgst, igst, line_total), so the
    paper and the books price the same way. Rule 46 header (both GSTINs, the
    place of supply = the recipient registration's state), Rule 48 copies."""
    lines = [ln for ln in (lines or []) if isinstance(ln, dict)]
    place = _state_label(recipient_gstin)
    code = recipient_gstin[:2]
    header = LegalHeader(
        entity=entity,
        store=store,
        doc_type="tax_invoice",
        doc_number=invoice_number,
        doc_date=invoice_date,
        # Where the goods go: the recipient registration's state.
        place_of_supply="{0} ({1})".format(state_name(code) or "", code).strip()
        if code
        else None,
        copy_marker=copy_marker,
        overrides=overrides,
        extra_meta=[("Transfer No.", transfer_number)] if transfer_number else None,
    )
    # One supplier registration and one place of supply per page (the same
    # overrides the delivery challan makes): the sending shop's GSTIN, and
    # its address is where the goods are dispatched from.
    mine = (
        {"GSTIN / UIN": supplier_gstin, "State / Code": _state_label(supplier_gstin)}
        if supplier_gstin
        else {}
    )
    header["supplier_kv"] = [
        ("Dispatched from" if k == "Place of supply" else k, mine.get(k, v))
        for k, v in header.get("supplier_kv") or []
    ]
    head = _header_fragment(header, "Tax Invoice")

    def _party(title: str, name: str, address: str, gstin: str) -> str:
        return (
            "<div><h3>" + _e(title) + "</h3><div><strong>" + _e(name) + "</strong></div>"
            + ("<div>" + _e(address) + "</div>" if address else "")
            + "<div>GSTIN: " + _e(gstin) + "</div>"
            + "<div>State: " + _e(_state_label(gstin)) + "</div></div>"
        )

    party = (
        '<div class="party-grid">'
        + _party("Supplier (Dispatched from)", supplier_name, supplier_address, supplier_gstin)
        + _party("Recipient (Bill to / Ship to)", recipient_name, recipient_address, recipient_gstin)
        + "</div>"
    )

    money_cols = ("unit_price", "taxable", "gst_rate", "cgst", "sgst", "igst", "line_total")
    t = {k: 0.0 for k in ("qty", "taxable", "cgst", "sgst", "igst", "line_total")}
    rows = ""
    for idx, ln in enumerate(lines, start=1):
        for k in t:
            t[k] = round(t[k] + float(ln.get(k) or 0), 2)
        cells = "".join(
            '<td class="num">'
            + (_qty(ln.get(k)) + "%" if k == "gst_rate" else _money(ln.get(k)))
            + "</td>"
            for k in money_cols
        )
        serials = ", ".join(str(b) for b in ln.get("serials") or [])
        rows += (
            '<tr><td class="ctr">' + str(idx) + "</td><td>" + _e(ln.get("description"))
            + ('<div class="muted">' + _e(serials) + "</div>" if serials else "")
            + "</td><td>" + _e(ln.get("hsn")) + '</td><td class="num">'
            + _qty(ln.get("qty")) + "</td>" + cells + "</tr>"
        )
    total_cells = "".join(
        '<td class="num"><strong>' + _money(t[k]) + "</strong></td>"
        + ("<td></td>" if k == "taxable" else "")
        for k in ("taxable", "cgst", "sgst", "igst", "line_total")
    )
    table = (
        '<table class="lines"><thead><tr>'
        '<th class="ctr">Sr.</th><th>Description of Goods</th><th>HSN</th>'
        '<th class="num">Qty</th><th class="num">Rate (at cost)</th>'
        '<th class="num">Taxable value</th><th class="num">GST</th>'
        '<th class="num">CGST</th><th class="num">SGST</th><th class="num">IGST</th>'
        '<th class="num">Total</th></tr></thead><tbody>'
        + rows
        + '</tbody><tfoot><tr><td colspan="3" class="right"><strong>Total</strong></td>'
        + '<td class="num"><strong>' + _qty(t["qty"]) + "</strong></td><td></td>"
        + total_cells
        + "</tr></tfoot></table>"
    )
    total = t["line_total"]
    rupees = int(total)
    words = (
        '<div class="words">Amount in words: '
        + _e(amount_in_words(rupees, int(round((total - rupees) * 100))))
        + "</div>"
    )
    declaration = (
        '<div class="declaration">Stock transferred between two GST registrations '
        "(a supply under Schedule I of the CGST Act), valued at cost. "
        + _e(declarations("tax_invoice"))
        + "</div>"
    )
    signs = _signs(
        "Dispatched by",
        "Transporter",
        (header.get("signatory_designation") or "Authorised Signatory"),
    )
    footer = _footer("tax_invoice", int(header.get("retention_years") or 7))

    # E-way bill data: what the portal's form asks for. Part B (the vehicle)
    # is written in by hand at dispatch.
    if total > EWAY_BILL_LIMIT:
        flag = (
            '<div class="not-invoice">E-way bill REQUIRED: this consignment is worth '
            + _money(total) + ", over " + _money(EWAY_BILL_LIMIT)
            + ". Generate it on ewaybillgst.gov.in before the goods leave.</div>"
        )
    else:
        flag = (
            '<div class="declaration">Consignment value ' + _money(total)
            + " is not over " + _money(EWAY_BILL_LIMIT)
            + ": the central rule needs no e-way bill (check the state's own limit).</div>"
        )
    part_a = [
        ("Supply type / Sub type", "Outward / Supply"),
        ("Document type", "Tax Invoice"),
        ("Document No. / Date", invoice_number + " / " + format_date(invoice_date)),
        ("From GSTIN / State", supplier_gstin + " / " + _state_label(supplier_gstin)),
        ("From (dispatch) address", supplier_address),
        ("To GSTIN / State", recipient_gstin + " / " + place),
        ("To (ship to) address", recipient_address),
        ("Taxable value", _money(t["taxable"])),
        ("CGST / SGST / IGST", " / ".join(_money(t[k]) for k in ("cgst", "sgst", "igst"))),
        ("Total invoice value", _money(total)),
    ]
    goods = "".join(
        "<tr><td>" + _e(ln.get("hsn")) + "</td><td>" + _e(ln.get("description"))
        + '</td><td class="num">' + _qty(ln.get("qty")) + ' NOS</td><td class="num">'
        + _money(ln.get("taxable")) + '</td><td class="num">' + _qty(ln.get("gst_rate"))
        + "%</td></tr>"
        for ln in lines
    )
    blank = (
        '<span style="display:inline-block;min-width:220px;'
        'border-bottom:1px solid #6b7280">&nbsp;</span>'
    )
    part_b = (
        "Transport mode", "Transporter name", "Transporter ID", "Vehicle No.",
        "Transport doc No. / Date", "Approx. distance (km)", "E-way bill No. / Date",
    )
    eway = (
        '<div style="page-break-before:always"></div>'
        '<div class="title-band"><h2>E-way Bill Data</h2></div>'
        + flag
        + '<h3>Part A</h3><table class="kv">'
        + "".join('<tr><td class="k">' + _e(k) + "</td><td>" + _e(v) + "</td></tr>" for k, v in part_a)
        + '</table><table class="lines"><thead><tr><th>HSN</th><th>Product</th>'
        '<th class="num">Quantity</th><th class="num">Taxable value</th>'
        '<th class="num">Tax rate</th></tr></thead><tbody>'
        + goods
        + '</tbody></table><h3>Part B (fill in at dispatch)</h3><table class="kv">'
        + "".join('<tr><td class="k">' + _e(k) + "</td><td>" + blank + "</td></tr>" for k in part_b)
        + "</table>"
    )
    body = head + party + table + words + declaration + signs + footer + eway
    return _page("Tax Invoice " + (invoice_number or ""), body, auto_print)


# ===========================================================================
# ESTIMATE / QUOTATION -- non-binding, no invoice serial, no stock claim
# ===========================================================================


def render_estimate(
    *,
    entity: Optional[Dict[str, Any]],
    store: Optional[Dict[str, Any]],
    estimate_number: str,
    estimate_date: Any,
    valid_until: Any = None,
    customer_name: str = "",
    customer_phone: str = "",
    customer_address: str = "",
    items: Optional[List[Dict[str, Any]]] = None,
    totals: Optional[Dict[str, Any]] = None,
    interstate: bool = False,
    place_of_supply: Any = None,
    terms: str = "",
    overrides: Optional[Dict[str, Any]] = None,
    auto_print: bool = False,
) -> str:
    """Render an Estimate / Quotation as a self-contained HTML page.

    `items` rows accept: description/name, qty/quantity, mrp, offer_price,
    line_total/item_total, taxable_value, tax_amount, gst_rate, hsn_code.
    `totals` is the dict returned by the estimate's GST computation
    (subtotal/taxable/tax/round_off/grand_total). The document header reads
    "ESTIMATE / QUOTATION - not a tax invoice"; it carries NO invoice serial
    and makes NO stock claim (it is non-binding).

    `overrides` is the per-entity print_template_overrides "fields" dict so
    owner-configured signatory / footer / logo reach the printed estimate.
    """
    items = items or []
    totals = totals or {}

    header = LegalHeader(
        entity=entity,
        store=store,
        doc_type="tax_invoice",  # reuse the full customer-facing identity block
        doc_number=estimate_number,
        doc_date=estimate_date,
        place_of_supply=place_of_supply,
        copy_marker="ORIGINAL",
        overrides=overrides,
        extra_meta=(
            [("Valid Until", format_date(valid_until))] if valid_until else None
        ),
    )
    # An estimate carries NO copy markers (it is not a statutory triplicate doc).
    header["copy_marker"] = copy_marker_block("ORIGINAL", mode="none")

    head = _header_fragment(header, "Estimate / Quotation")
    banner = _not_a_tax_invoice("Estimate / Quotation - Not a Tax Invoice")

    cust = (
        '<div class="party-grid"><div><h3>Customer</h3>'
        + "<div><strong>" + _e(customer_name or "Walk-in") + "</strong></div>"
        + ("<div>Phone: " + _e(customer_phone) + "</div>" if customer_phone else "")
        + ("<div>" + _e(customer_address) + "</div>" if customer_address else "")
        + "</div></div>"
    )

    rows = ""
    for idx, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            continue
        desc = it.get("description") or it.get("name") or it.get("product_name") or ""
        hsn = it.get("hsn_code") or it.get("hsn") or ""
        try:
            q = float(it.get("qty") or it.get("quantity") or 1)
        except (TypeError, ValueError):
            q = 1.0
        mrp = it.get("mrp")
        offer = it.get("offer_price")
        line_total = it.get("line_total")
        if line_total is None:
            line_total = it.get("item_total")
        if line_total is None:
            try:
                line_total = float(offer or 0) * q
            except (TypeError, ValueError):
                line_total = 0.0
        try:
            rate = float(it.get("gst_rate") or 0)
        except (TypeError, ValueError):
            rate = 0.0
        rows += (
            "<tr>"
            + '<td class="ctr">' + str(idx) + "</td>"
            + "<td>" + _e(desc) + "</td>"
            + "<td>" + _e(hsn) + "</td>"
            + '<td class="num">' + (_money(mrp) if mrp is not None else "-") + "</td>"
            + '<td class="num">' + (_money(offer) if offer is not None else "-") + "</td>"
            + '<td class="ctr">' + _qty(q) + "</td>"
            + '<td class="ctr">' + ("{0:g}%".format(rate) if rate else "-") + "</td>"
            + '<td class="num">' + _money(line_total) + "</td>"
            + "</tr>"
        )
    if not rows:
        rows = '<tr><td colspan="8" class="ctr muted">No items</td></tr>'

    table = (
        '<table class="lines">'
        + "<thead><tr>"
        + '<th class="ctr">Sr.</th><th>Description</th><th>HSN/SAC</th>'
        + '<th class="num">MRP</th><th class="num">Offer</th>'
        + '<th class="ctr">Qty</th><th class="ctr">GST%</th><th class="num">Amount</th>'
        + "</tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )

    # HSN-wise tax summary, reusing the same routing the invoice uses.
    hsn_items = []
    for it in items:
        if not isinstance(it, dict):
            continue
        hsn_items.append(
            {
                "hsn": it.get("hsn_code") or it.get("hsn") or "",
                "description": it.get("description") or it.get("name") or "",
                "qty": it.get("qty") or it.get("quantity") or 0,
                "taxable": it.get("taxable_value") or 0,
                "rate": it.get("gst_rate") or 0,
            }
        )
    hsn_summary = hsn_tax_summary(
        hsn_items,
        place_of_supply=place_of_supply if interstate else None,
        supplier_state=None,
    )

    subtotal = totals.get("subtotal", 0)
    taxable = totals.get("taxable", 0)
    tax = totals.get("tax", 0)
    grand = totals.get("grand_total")
    if grand is None:
        try:
            grand = round(float(taxable or 0) + float(tax or 0), 2)
        except (TypeError, ValueError):
            grand = 0.0

    if interstate:
        tax_rows = (
            '<tr><td class="k">IGST</td><td class="num">' + _money(tax) + "</td></tr>"
        )
    else:
        half = round(float(tax or 0) / 2.0, 2)
        tax_rows = (
            '<tr><td class="k">CGST</td><td class="num">' + _money(half) + "</td></tr>"
            + '<tr><td class="k">SGST</td><td class="num">'
            + _money(round(float(tax or 0) - half, 2))
            + "</td></tr>"
        )

    try:
        round_off = float(totals.get("round_off") or 0)
    except (TypeError, ValueError):
        round_off = 0.0
    round_off_row = (
        '<tr><td class="k">Round off</td><td class="num">'
        + ("+ " if round_off > 0 else "- ") + _money(abs(round_off)) + "</td></tr>"
        if round_off
        else ""
    )

    totals_table = (
        '<table class="totals">'
        + '<tr><td class="k">Subtotal</td><td class="num">' + _money(subtotal) + "</td></tr>"
        + '<tr><td class="k">Taxable Value</td><td class="num">' + _money(taxable) + "</td></tr>"
        + tax_rows
        + round_off_row
        + '<tr class="grand"><td>Estimated Total</td><td class="num">' + _money(grand) + "</td></tr>"
        + "</table>"
    )

    words = '<div class="words">' + _e(amount_in_words(grand)) + "</div>"

    valid_note = ""
    if valid_until:
        valid_note = (
            '<div class="declaration">This estimate is valid until '
            + _e(format_date(valid_until))
            + ". Prices are indicative and subject to availability at the time of "
            "billing.</div>"
        )

    declaration = (
        '<div class="declaration">This is an estimate / quotation and NOT a tax '
        "invoice. No goods are reserved and no GST is charged against this document. "
        "A tax invoice with a statutory serial number will be issued on confirmation "
        "of the order and receipt of payment.</div>"
    )

    terms_html = (
        '<div style="margin:8px 0"><h3>Terms &amp; Conditions</h3><div>'
        + _e(terms)
        + "</div></div>"
        if terms
        else ""
    )

    signs = _signs("Prepared By", "", "Authorised Signatory")
    footer = (
        '<div class="footer">Estimate / Quotation - non-binding. '
        "Not a tax invoice; no input tax credit may be claimed against it.</div>"
    )

    body = (
        head
        + banner
        + cust
        + table
        + totals_table
        + words
        + valid_note
        + declaration
        + terms_html
        + signs
        + footer
    )
    # silence unused-var lint for hsn_summary (kept for parity / future surface)
    _ = hsn_summary
    return _page("Estimate " + (estimate_number or ""), body, auto_print)
