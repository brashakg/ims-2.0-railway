"""Internal returned-goods request from exact receipt/bill evidence.

This is not the supplier's confirmation and never posts AP or input tax.
Unbooked/rejected custody retains the known entered goods value, with tax
explicitly unverified; it does not become a zero-rated financial instrument.
"""
from decimal import Decimal, ROUND_HALF_UP

from .return_provenance import ReturnProvenanceError, resolve_request_provenance


def prepare_request(db, return_doc, vendor, seller, src_lines):
    from .rtv_debit_note import _rate_key, rupees_to_paise

    sources = resolve_request_provenance(db, return_doc)
    booked = [s for s in sources if s["origin_status"] == "BOOKED"]
    pairs = {(s.get("recipient_entity_id"), s.get("recipient_gstin"), s.get("vendor_gstin")) for s in booked}
    if len(pairs) > 1:
        raise ReturnProvenanceError("MIXED_ORIGINAL_REGISTRATIONS", "The returned goods have different original registration pairs; Accounts must reconcile their request documents.")
    vendor, seller = dict(vendor or {}), dict(seller or {})
    if booked:
        origin = booked[0]
        vendor["gstin"] = origin.get("vendor_gstin") or ""
        seller.update(gstin=origin.get("recipient_gstin") or "", entity_id=origin.get("recipient_entity_id"))
    elif not seller.get("gstin"):
        raise ReturnProvenanceError("seller_has_no_gstin", "The issuing shop's legal-entity registration must be corrected before issuing the request.")
    normalized = []
    for source in sources:
        raw = src_lines[source["return_line_index"]]
        key = _rate_key(raw)
        rate = int(raw[key]) if key.endswith("_paise") else rupees_to_paise(raw[key])
        quantity = source["quantity"]
        taxable = quantity * rate
        heads = {"cgst": 0, "sgst": 0, "igst": 0}
        gst_rate, hsn, description = None, "", raw.get("description") or raw.get("product_name") or ""
        if source["origin_status"] == "BOOKED":
            original = source["original_taxable_paise"]
            if not original and any(source["original_tax_paise"].values()):
                raise ReturnProvenanceError("AMBIGUOUS_ORIGIN_AMOUNT", "The original bill has tax without a verified goods value.")
            heads = {h: int((Decimal(taxable) * amount / original).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                     if original else 0 for h, amount in source["original_tax_paise"].items()}
            gst_rate, hsn = source["gst_rate"], source.get("hsn") or ""
            description = source.get("description") or description
            if not source["claims_credit"] and not source["reverse_charge"]:
                taxable += sum(heads.values())
                rate = int((Decimal(taxable) / quantity).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                heads, gst_rate = {h: 0 for h in heads}, 0
        tax = sum(heads.values())
        normalized.append({
            "sku": source["product_id"], "description": description, "hsn": hsn,
            "qty": quantity, "rate_paise": rate, "taxable_paise": taxable, "gst_rate": gst_rate,
            **{f"{h}_paise": amount for h, amount in heads.items()}, "tax_paise": tax,
            "line_total_paise": taxable + (0 if source.get("reverse_charge") else tax),
            "original_bill_id": source.get("bill_id"), "original_bill_line_index": source.get("bill_line_index"),
            "origin_status": source["origin_status"], "reverse_charge": source.get("reverse_charge", False),
        })
    return {"sources": sources, "lines": normalized, "vendor": vendor, "seller": seller,
            "tax_status": "ORIGINAL_BILL" if len(booked) == len(sources) else "UNVERIFIED"}


def build_request(return_doc, prepared, serial):
    from .rtv_debit_note import build_debit_note

    note = build_debit_note(return_doc, prepared["vendor"], [], serial, seller=prepared["seller"])
    lines = prepared["lines"]
    totals = {key: sum(line[key] for line in lines) for key in (
        "taxable_paise", "cgst_paise", "sgst_paise", "igst_paise", "tax_paise",
    )}
    totals["grand_total_paise"] = sum(line["line_total_paise"] for line in lines)
    invoices = {(s.get("bill_id"), s.get("bill_number"), str(s.get("bill_date") or ""))
                for s in prepared["sources"] if s["origin_status"] == "BOOKED"}
    refs = [{"bill_id": bid, "number": number, "date": day[:10]} for bid, number, day in sorted(invoices)]
    note.update(lines=lines, totals=totals, document_kind="INTERNAL_RETURN_REQUEST",
                accounting_status="UNPOSTED", tax_status=prepared["tax_status"], original_invoices=refs,
                original_invoice=refs[0] if len(refs) == 1 else {"number": "Multiple original bills" if refs else None, "date": None})
    # The note's display follows the original stored heads, never today's master.
    note["is_inter_state"] = bool(totals["igst_paise"])
    if totals["igst_paise"] and (totals["cgst_paise"] or totals["sgst_paise"]):
        raise ReturnProvenanceError("MIXED_ORIGINAL_TAX_HEADS", "The internal request needs separate documents for its different original tax heads.")
    return note
