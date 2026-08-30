"""
IMS 2.0 - Order tax-invoice PDF (F52 / POS Wave 4 groundwork)
=============================================================
Server-side A4 PDF of an order's GST tax invoice, so the invoice can be
WhatsApp'd as a DOCUMENT and printed without the browser. Design rules:

  * ONE assembly, ONE renderer. Every number on this PDF comes from the
    payload the existing GET /orders/{id}/invoice door assembled (serial
    minting, IDOR/DRAFT/GSTIN gates, C-6 CGST/SGST/IGST split) plus the
    order document's own persisted per-line statutory values (hsn_code,
    gst_rate, taxable_value, tax_amount stamped at create). NOTHING is
    recomputed here -- this module only lays out (one-rule-two-
    implementations defence: the JSON door stays the single invoice brain).
  * reportlab platypus (already a dependency; pure python, Railway-safe) --
    same stack as catalogue_pdf.py.
  * ASCII money ("Rs 1,500"): built-in PDF fonts lack the rupee glyph.
  * Identity (legal name / GSTIN / signatory / declaration overrides) via
    print_identity.resolve_issuing_identity, same as the delivery challan.
"""

from __future__ import annotations

import io
from typing import Any, Dict, List, Optional

from .print_identity import resolve_issuing_identity
from .print_legal import amount_in_words, declarations, format_date


def _rs(v: Any) -> str:
    try:
        return "Rs {:,.2f}".format(float(v))
    except (TypeError, ValueError):
        return "Rs 0.00"


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_invoice_lines(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-line rows straight off the ORDER's persisted statutory fields.

    Pure + separately testable: the table renderer consumes exactly this.
    """
    rows: List[Dict[str, Any]] = []
    for it in order.get("items") or []:
        name = str(it.get("product_name") or it.get("sku") or "Item")
        brand = str(it.get("brand") or "").strip()
        desc = f"{brand} {name}".strip() if brand and brand not in name else name
        qty = _f(it.get("quantity"), 1.0)
        unit = _f(it.get("unit_price"))
        disc_pct = _f(it.get("discount_percent"))
        rows.append(
            {
                "description": desc,
                "hsn": str(it.get("hsn_code") or ""),
                "qty": qty,
                "unit_price": unit,
                "discount_percent": disc_pct,
                "taxable_value": _f(it.get("taxable_value")),
                "gst_rate": _f(it.get("gst_rate")),
                "tax_amount": _f(it.get("tax_amount")),
                "item_total": _f(it.get("item_total"), unit * qty),
            }
        )
    return rows


def build_invoice_pdf(
    payload: Dict[str, Any],
    order: Dict[str, Any],
    customer: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Render the A4 tax-invoice PDF. `payload` is the EXACT dict the JSON
    invoice door returned (invoiceNumber, taxSummary, taxTotals, split flags);
    `order` is the stored order document (per-line statutory values)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    ident = resolve_issuing_identity(order.get("store_id"), "tax_invoice")
    store = ident.get("store") or {}
    entity = ident.get("entity") or {}
    overrides = ident.get("overrides") or {}

    legal_name = (
        entity.get("legal_name")
        or entity.get("name")
        or store.get("name")
        or "Better Vision"
    )
    store_gstin = payload.get("storeGstin") or store.get("gstin") or ""
    address = str(
        store.get("address") or entity.get("address") or ""
    ).strip()
    phone = str(store.get("phone") or "").strip()

    # GSTIN-conditional title -- same rule the FE GSTInvoice applies.
    title = "TAX INVOICE" if store_gstin else "INVOICE"

    styles = {
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=14, leading=17),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=10),
        "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.5, leading=10.5),
        "cellb": ParagraphStyle(
            "cellb", fontName="Helvetica-Bold", fontSize=8.5, leading=10.5
        ),
    }

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title=f"{title} {payload.get('invoiceNumber') or ''}".strip(),
    )
    story: List[Any] = []
    hairline = colors.HexColor("#B8B8B4")

    # ---- Header: issuing identity + document title ----
    head_left = [
        Paragraph(legal_name, styles["h1"]),
    ]
    if address:
        head_left.append(Paragraph(address, styles["small"]))
    id_bits = []
    if store_gstin:
        id_bits.append(f"GSTIN: {store_gstin}")
    if phone:
        id_bits.append(f"Phone: {phone}")
    if id_bits:
        head_left.append(Paragraph(" | ".join(id_bits), styles["small"]))

    pos = str(payload.get("placeOfSupply") or "").strip()
    if pos and payload.get("placeOfSupplyAssumed"):
        pos += " (assumed)"
    meta_rows = [
        ["Invoice No", str(payload.get("invoiceNumber") or "")],
        ["Invoice Date", format_date(payload.get("invoiceDate"))],
        ["Order No", str(payload.get("orderNumber") or "")],
    ]
    if pos:
        meta_rows.append(["Place of Supply", pos])
    meta_rows.append(
        ["Supply Type", "Inter-state (IGST)" if payload.get("interstate") else "Intra-state (CGST+SGST)"]
    )
    meta_tbl = Table(
        [[Paragraph(k, styles["cellb"]), Paragraph(v, styles["cell"])] for k, v in meta_rows],
        colWidths=[28 * mm, 52 * mm],
    )
    meta_tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    head = Table(
        [
            [head_left, [Paragraph(title, styles["h1"]), Spacer(1, 2), meta_tbl]],
        ],
        colWidths=[100 * mm, 82 * mm],
    )
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(head)
    story.append(Spacer(1, 4 * mm))

    # ---- Bill-to ----
    cust_lines = [Paragraph("Bill To", styles["cellb"])]
    cust_lines.append(
        Paragraph(str(payload.get("customerName") or "Walk-in customer"), styles["cell"])
    )
    c = customer or {}
    cust_gstin = payload.get("customerGstin") or c.get("gstin")
    if cust_gstin:
        cust_lines.append(Paragraph(f"GSTIN: {cust_gstin}", styles["small"]))
    cphone = str(c.get("mobile") or c.get("phone") or "").strip()
    if cphone:
        cust_lines.append(Paragraph(f"Phone: {cphone}", styles["small"]))
    caddr = str(c.get("address") or "").strip()
    if caddr:
        cust_lines.append(Paragraph(caddr, styles["small"]))
    story.extend(cust_lines)
    story.append(Spacer(1, 4 * mm))

    # ---- Lines table ----
    lines = build_invoice_lines(order)
    header = ["#", "Description", "HSN", "Qty", "Rate", "Disc %", "Taxable", "GST %", "Tax"]
    data: List[List[Any]] = [[Paragraph(h, styles["cellb"]) for h in header]]
    for i, ln in enumerate(lines, start=1):
        data.append(
            [
                Paragraph(str(i), styles["cell"]),
                Paragraph(ln["description"], styles["cell"]),
                Paragraph(ln["hsn"], styles["cell"]),
                Paragraph("{:g}".format(ln["qty"]), styles["cell"]),
                Paragraph(_rs(ln["unit_price"]), styles["cell"]),
                Paragraph(
                    "{:g}".format(ln["discount_percent"]) if ln["discount_percent"] else "-",
                    styles["cell"],
                ),
                Paragraph(_rs(ln["taxable_value"]), styles["cell"]),
                Paragraph("{:g}".format(ln["gst_rate"]), styles["cell"]),
                Paragraph(_rs(ln["tax_amount"]), styles["cell"]),
            ]
        )
    tbl = Table(
        data,
        colWidths=[8 * mm, 58 * mm, 16 * mm, 11 * mm, 22 * mm, 13 * mm, 22 * mm, 12 * mm, 20 * mm],
        repeatRows=1,
    )
    tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.4, hairline),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]
        )
    )
    story.append(tbl)
    story.append(Spacer(1, 4 * mm))

    # ---- Tax summary (per-rate CGST/SGST/IGST from the JSON door) ----
    tax_rows = payload.get("taxSummary") or []
    if tax_rows:
        interstate = bool(payload.get("interstate"))
        if interstate:
            theader = ["GST %", "Taxable", "IGST"]
        else:
            theader = ["GST %", "Taxable", "CGST", "SGST"]
        tdata: List[List[Any]] = [[Paragraph(h, styles["cellb"]) for h in theader]]
        for r in tax_rows:
            row = [
                Paragraph("{:g}".format(_f(r.get("rate"))), styles["cell"]),
                Paragraph(_rs(r.get("taxable")), styles["cell"]),
            ]
            if interstate:
                row.append(Paragraph(_rs(r.get("igst")), styles["cell"]))
            else:
                row.append(Paragraph(_rs(r.get("cgst")), styles["cell"]))
                row.append(Paragraph(_rs(r.get("sgst")), styles["cell"]))
            tdata.append(row)
        ttbl = Table(tdata, colWidths=[18 * mm, 32 * mm, 28 * mm, 28 * mm][: len(theader)])
        ttbl.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, hairline),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(ttbl)
        story.append(Spacer(1, 3 * mm))

    # ---- Totals ----
    totals: List[List[str]] = []
    cart_disc = _f(order.get("cart_discount_amount"))
    if cart_disc:
        totals.append(["Bill discount", "- " + _rs(cart_disc)])
    tt = payload.get("taxTotals") or {}
    if payload.get("interstate"):
        totals.append(["IGST", _rs(tt.get("igst"))])
    else:
        totals.append(["CGST", _rs(tt.get("cgst"))])
        totals.append(["SGST", _rs(tt.get("sgst"))])
    totals.append(["Grand Total", _rs(payload.get("grandTotal"))])
    totals.append(["Amount Paid", _rs(payload.get("amountPaid"))])
    totals.append(["Balance Due", _rs(payload.get("balanceDue"))])
    tot_tbl = Table(
        [
            [Paragraph(k, styles["cellb"]), Paragraph(v, styles["cell"])]
            for k, v in totals
        ],
        colWidths=[40 * mm, 40 * mm],
        hAlign="RIGHT",
    )
    tot_tbl.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, len(totals) - 3), (-1, len(totals) - 3), 0.6, hairline),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    story.append(tot_tbl)
    story.append(Spacer(1, 2 * mm))

    grand = _f(payload.get("grandTotal"))
    rupees = int(grand)
    paise = int(round((grand - rupees) * 100))
    story.append(
        Paragraph(
            "Amount in words: " + amount_in_words(rupees, paise), styles["small"]
        )
    )
    story.append(Spacer(1, 5 * mm))

    # ---- Declaration + signatory ----
    decl = str(
        overrides.get("declaration_text") or declarations("tax_invoice") or ""
    ).strip()
    if decl:
        story.append(Paragraph(decl, styles["small"]))
        story.append(Spacer(1, 6 * mm))
    signatory = str(overrides.get("signatory_name") or "").strip()
    story.append(
        Paragraph(
            f"For {legal_name}", styles["cellb"]
        )
    )
    story.append(Spacer(1, 8 * mm))
    story.append(
        Paragraph(signatory or "Authorised Signatory", styles["small"])
    )

    doc.build(story)
    return buf.getvalue()
