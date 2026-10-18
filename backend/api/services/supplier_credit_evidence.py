"""Verified, read-only evidence for the supplier's actual credit confirmation.

No current vendor tax or inferred latest receipt can repair an original bill.
The caller authorizes the return before entry and supplies its bill-read scope.
This module never posts money; the writer must reserve the returned capacity.
"""
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json

from .org_validation import has_valid_gstin
from .return_provenance import ReturnProvenanceError, resolve_return_provenance

HEADS = ("cgst", "sgst", "igst")
EVENTS = "supplier_credit_confirmations"


class SupplierCreditError(ValueError):
    """A visible validation, evidence-change, or retry error."""

    def __init__(self, code, message, status=422):
        self.code, self.status = code, status
        super().__init__(message)


def paise(value):
    """Reject non-finite/negative input and fractions smaller than one paise."""
    try:
        number = Decimal(str(value))
        cents = number * 100
        valid = number.is_finite() and 0 <= number <= 1_000_000_000 and cents == cents.to_integral_value()
    except (InvalidOperation, ValueError, TypeError):
        valid = False
    if not valid:
        raise SupplierCreditError("INVALID_AMOUNT", "Use a non-negative amount with at most two decimal places.")
    return int(cents)


def day(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (ValueError, TypeError) as exc:
        raise ReturnProvenanceError("ORIGINAL_DATE_REQUIRED", "The original bill date is missing or invalid.") from exc


def source_key(return_id, source):
    evidence = {k: source[k] for k in (
        "bill_line_fingerprint", "bill_id", "bill_line_index", "grn_id",
        "return_line_index", "quantity", "stock_ids",
    )}
    evidence["return_id"] = return_id
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def _pending(code, message):
    raise ReturnProvenanceError(code, message)


def _verify_original(db, origin):
    """The stored tax headers must support the same claim as the item breakup."""
    recipient = origin.get("recipient_gstin")
    entity_id = origin.get("recipient_entity_id")
    entity = db.get_collection("entities").find_one({"entity_id": entity_id}) if entity_id else None
    held = {str(row.get("gstin") or "").strip().upper()
            for row in (entity or {}).get("gstins") or [] if isinstance(row, dict)}
    if (not has_valid_gstin(recipient) or recipient != recipient.strip().upper()
            or recipient not in held):
        _pending("ORIGINAL_REGISTRATION_REQUIRED", "The original bill's stamped recipient GSTIN must belong to its legal entity.")
    day(origin.get("bill_date"))
    if not origin.get("bill_number"):
        _pending("ORIGINAL_NUMBER_REQUIRED", "The original supplier bill number is missing.")
    if origin["claims_credit"] and any(origin["original_tax_paise"].values()):
        if not origin["reverse_charge"] and not has_valid_gstin(origin.get("vendor_gstin")):
            _pending("ORIGINAL_SUPPLIER_GSTIN_REQUIRED", "Claimed input tax needs the original bill's valid supplier GSTIN; current vendor data cannot replace it.")
        bill = db.get_collection("vendor_bills").find_one({"bill_id": origin["bill_id"]}) or {}
        try:
            for head in HEADS:
                total = sum(paise(line.get(head)) for line in bill.get("lines") or [])
                if total != paise(bill.get(f"{head}_total")):
                    _pending("ORIGINAL_TAX_TOTAL_MISMATCH", "The original bill's item tax does not match its recorded input-tax total.")
        except SupplierCreditError as exc:
            raise ReturnProvenanceError("ORIGINAL_TAX_TOTAL_REQUIRED", "The original bill's input-tax totals need verified item evidence.") from exc


def resolve_credit_evidence(db, return_doc, *, selections=None, allowed_store_ids=None):
    """Strict posting proof. Missing/contradictory tax identity stays pending."""
    sources = resolve_return_provenance(db, return_doc, selections=selections)
    return_id = return_doc.get("return_id") or return_doc.get("rma_id")
    for source in sources:
        if allowed_store_ids is not None and source["store_id"] not in allowed_store_ids:
            _pending("ORIGINAL_SHOP_ACCESS_REQUIRED", "Accounts needs access to the original bill's shop before confirming this credit.")
        _verify_original(db, source)
        source["source_key"] = source_key(return_id, source)
    registrations = {(s["recipient_entity_id"], s["recipient_gstin"], s.get("vendor_gstin")) for s in sources}
    if len(registrations) != 1:
        _pending("MIXED_ORIGINAL_REGISTRATIONS", "One supplier note must identify one original supplier and recipient registration pair.")
    return sources


def _selection(source):
    return {key: source[key] for key in (
        "return_line_index", "grn_id", "bill_id", "bill_line_index", "quantity", "stock_ids",
    )}


def _amounts(base, heads, reverse_charge):
    return {"taxable": base / 100, **{h: heads[h] / 100 for h in HEADS},
            "total": (base + (0 if reverse_charge else sum(heads.values()))) / 100}


def _requested(return_doc, source):
    item = (return_doc.get("items") or return_doc.get("lines"))[source["return_line_index"]]
    value = item.get("unit_price", item.get("unit_cost"))
    if value is None:
        return None
    try:
        base = paise(value) * source["quantity"]
    except SupplierCreditError:
        return None
    original = source["original_taxable_paise"]
    if not original and any(source["original_tax_paise"].values()):
        return None
    heads = {h: int((Decimal(base) * value / original).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
             if original else 0 for h, value in source["original_tax_paise"].items()}
    return {"taxable": base, **heads}


def confirmation_view(event):
    return {
        "confirmation_id": event["confirmation_id"],
        "credit_note_number": event["credit_note_number"], "credit_note_date": event["credit_note_date"],
        "received_amount": event["received_paise"] / 100, "notes": event.get("notes"),
        "posting_status": "POSTED" if event.get("posting_status") == "POSTED" else "PENDING_EVIDENCE",
        "source_selections": event.get("source_selections") or [],
        "allocations": [{"source_key": a["source_key"], **{h: a[f"{h}_paise"] / 100
                        for h in ("taxable", *HEADS)}} for a in event.get("allocations") or []],
        "pending_reasons": event.get("pending_reasons") or [],
    }


def credit_choices(db, return_doc, *, allowed_store_ids=None):
    """Only stored physical origins are offered; no guessed latest purchase."""
    from api.routers.finance.gst import _bill_booked

    rid = return_doc.get("return_id") or return_doc.get("rma_id")
    linked = list(db.get_collection("stock_units").find({"rtv_vendor_id": rid}))
    output = []
    for index, item in enumerate(return_doc.get("items") or return_doc.get("lines") or []):
        product = item.get("product_id")
        units = [u for u in linked if u.get("product_id") == product]
        gids = {u.get("grn_id") or (u.get("source_id") if u.get("source_type") == "GRN" else None) for u in units}
        query = {"vendor_id": return_doc["vendor_id"], "items.product_id": product,
                 "status": {"$in": ["ACCEPTED", "PARTIALLY_ACCEPTED"]}}
        if units:
            query["grn_id"] = {"$in": list(gids)}
        else:
            query["store_id"] = return_doc["store_id"]
        if return_doc.get("grn_id"):
            query["grn_id"] = return_doc["grn_id"]
        candidates = []
        for receipt in db.get_collection("grns").find(query):
            if allowed_store_ids is not None and receipt.get("store_id") not in allowed_store_ids:
                continue
            gid = receipt["grn_id"]
            origin_units = [u for u in units if (u.get("grn_id") or u.get("source_id")) == gid]
            ids = sorted(u["stock_id"] for u in origin_units if u.get("stock_id"))
            labels = [{"stock_id": u["stock_id"], "label": str(u.get("barcode") or u.get("unit_code") or u["stock_id"])}
                      for u in origin_units if u.get("stock_id")]
            accepted = sum(int(row.get("accepted_qty") or 0) for row in receipt.get("items") or [] if row.get("product_id") == product)
            bills = [b for b in db.get_collection("vendor_bills").find({
                "vendor_id": return_doc["vendor_id"], "store_id": receipt.get("store_id"),
                "$or": [{"grn_id": gid}, {"linked_dc_ids": gid}],
            }) if _bill_booked(b) and not b.get("source_transfer_id") and b.get("doc_type") in (None, "PURCHASE_INVOICE")]
            for bill in bills or [{}]:
                matching = [(n, row) for n, row in enumerate(bill.get("lines") or []) if row.get("product_id") == product]
                for line_index, line in matching or [(-1, {})]:
                    candidate = {"grn_id": gid, "bill_id": bill.get("bill_id") or "",
                                 "bill_line_index": line_index, "bill_number": bill.get("bill_number") or bill.get("invoice_number") or "",
                                 "bill_date": str(bill.get("bill_date") or bill.get("invoice_date") or "")[:10],
                                 "quantity": min(len(ids) if units else accepted, int(line.get("qty") or accepted)),
                                 "stock_ids": ids, "stock_units": labels}
                    if return_doc.get("receipt_rejection"):
                        candidate["disabled_reason"] = "Rejected custody does not prove a billed purchase."
                    elif not bill:
                        candidate["disabled_reason"] = "No original booked bill."
                    elif not line:
                        candidate["disabled_reason"] = "Verified original bill item breakup required."
                    candidates.append(candidate)
        output.append({"return_line_index": index, "product_name": item.get("product_name") or product,
                       "quantity": item.get("quantity", item.get("qty")), "candidates": candidates})
    return output


def credit_preview(db, return_doc, *, selections=None, confirmation_id=None, allowed_store_ids=None):
    """Read-only form data; reservations and a fresh source check still gate POST."""
    rid = return_doc.get("return_id") or return_doc.get("rma_id")
    events = list(db.get_collection(EVENTS).find({"return_id": rid}))
    confirmation = next((e for e in events if e["confirmation_id"] == confirmation_id), None)
    if confirmation_id and confirmation is None:
        raise SupplierCreditError("CONFIRMATION_NOT_FOUND", "This supplier confirmation was not found for the return.", 404)
    if selections is None and confirmation:
        selections = confirmation.get("source_selections") or []
    posted = sum(e["received_paise"] for e in events if e.get("posting_status") == "POSTED")
    response = {"return_id": rid, "source_status": "RESOLVED", "pending_reasons": [], "sources": [],
                "choices": credit_choices(db, return_doc, allowed_store_ids=allowed_store_ids),
                "source_selections": selections or [], "expected_credit_amount": None,
                "posted_amount": posted / 100, "remaining_amount": None,
                "confirmations": [confirmation_view(e) for e in events],
                "confirmation": confirmation_view(confirmation) if confirmation else None}
    try:
        sources = resolve_credit_evidence(db, return_doc, selections=selections, allowed_store_ids=allowed_store_ids)
    except ReturnProvenanceError as exc:
        response.update(source_status="PENDING_EVIDENCE", pending_reasons=[{"code": exc.code, "message": str(exc)}])
        return response
    used = defaultdict(lambda: defaultdict(int))
    for event in events:
        if event.get("posting_status") == "POSTED":
            for allocation in event.get("allocations") or []:
                for head in ("taxable", *HEADS):
                    used[allocation["source_key"]][head] += allocation[f"{head}_paise"]
    expected = 0
    for source in sources:
        key = source["source_key"]
        request = _requested(return_doc, source)
        if request is None:
            expected = None
        elif expected is not None:
            expected += request["taxable"] + (0 if source["reverse_charge"] else sum(request[h] for h in HEADS))
        caps = {"taxable": source["taxable_cap_paise"], **source["tax_cap_paise"]}
        remaining = {h: max(0, amount - used[key][h]) for h, amount in caps.items()}
        suggested = {h: min(remaining[h], max(0, (request or caps)[h] - used[key][h])) for h in caps}
        response["sources"].append({
            **{k: source[k] for k in ("source_key", "return_line_index", "product_id", "quantity", "grn_id", "bill_id",
                                      "bill_line_index", "bill_number", "claims_credit", "reverse_charge", "stock_ids")},
            "product_name": (return_doc.get("items") or return_doc.get("lines"))[source["return_line_index"]].get("product_name") or source["product_id"],
            "bill_date": day(source["bill_date"]),
            "suggested": _amounts(suggested["taxable"], {h: suggested[h] for h in HEADS}, source["reverse_charge"]),
            "remaining_caps": _amounts(remaining["taxable"], {h: remaining[h] for h in HEADS}, source["reverse_charge"]),
        })
    response["source_selections"] = [_selection(s) for s in sources]
    response["expected_credit_amount"] = None if expected is None else expected / 100
    response["remaining_amount"] = None if expected is None else max(0, expected - posted) / 100
    return response
