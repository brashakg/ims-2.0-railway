"""Read-only provenance for supplier returns; no inferred latest-receipt tax.

Callers authorize the return and each selected bill before exposing a snapshot.
These snapshots are inputs to posting, not reservations: the posting writer must
atomically enforce cumulative quantity/value/tax caps against earlier credits.
"""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json


class ReturnProvenanceError(ValueError):
    """The return must stay pending until its original purchase is identified."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _pending(code, message):
    raise ReturnProvenanceError(code, message)


def _number(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        _pending("INVALID_ORIGIN_AMOUNT", "The original quantity or amount is missing or invalid.")
    if not number.is_finite() or number < 0:
        _pending("INVALID_ORIGIN_AMOUNT", "The original quantity or amount is missing or invalid.")
    return number


def _paise(value):
    return int((_number(value) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _quantity(value):
    qty = _number(value)
    if qty <= 0 or qty != qty.to_integral_value():
        _pending("INVALID_RETURN_QUANTITY", "A returned quantity must name whole physical units.")
    return int(qty)


def _receipt_quantity(receipt, product_id):
    """Only accepted goods are evidenced as purchases, never rejected custody."""
    return sum(
        _number(line.get("accepted_qty"))
        for line in receipt.get("items") or []
        if line.get("product_id") == product_id
    )


def _snapshot(bill, line, line_index, quantity, receipt_id, stock_ids, return_index):
    from api.routers.finance.gst import _itc_eligible_bill

    original_qty = _number(line.get("qty"))
    if original_qty <= 0 or quantity > original_qty:
        _pending("ORIGINAL_QUANTITY_EXCEEDED", "The return exceeds the original billed quantity.")
    taxable = _paise(line.get("taxable"))
    heads = {head: _paise(line.get(head)) for head in ("cgst", "sgst", "igst")}
    if heads["igst"] and (heads["cgst"] or heads["sgst"]):
        _pending("AMBIGUOUS_TAX_HEADS", "The original bill has conflicting tax heads.")
    tax = sum(heads.values())
    if _paise(line.get("line_total")) != taxable + tax:
        _pending("AMBIGUOUS_ORIGIN_AMOUNT", "The original bill line's stored amounts do not reconcile.")
    # Snapshot only what the bill actually stored. No present-day vendor/shop
    # lookup can change the original tax head or credit eligibility.
    claimed = _itc_eligible_bill(bill)
    gst_rate = float(_number(line.get("gst_rate")))
    evidence = {
        "bill_id": bill.get("bill_id"), "line_index": line_index,
        "bill_number": bill.get("bill_number") or bill.get("invoice_number"),
        "bill_date": str(bill.get("bill_date") or bill.get("invoice_date")),
        "store_id": bill.get("store_id"), "vendor_id": bill.get("vendor_id"),
        "product_id": line.get("product_id"), "qty": str(original_qty),
        "gst_rate": gst_rate, "hsn": line.get("hsn"),
        "taxable_paise": taxable, "tax_paise": heads,
        "recipient_entity_id": bill.get("recipient_entity_id"),
        "recipient_gstin": bill.get("recipient_gstin"),
        "vendor_gstin": bill.get("vendor_gstin"), "claims_credit": claimed,
        "reverse_charge": bill.get("reverse_charge") is True,
    }
    fingerprint = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    return {
        "origin_status": "BOOKED",
        "return_line_index": return_index,
        "product_id": line.get("product_id"), "quantity": quantity,
        "description": line.get("description"), "hsn": line.get("hsn"),
        "grn_id": receipt_id, "stock_ids": sorted(stock_ids),
        "bill_id": bill["bill_id"], "bill_line_index": line_index,
        "bill_line_fingerprint": fingerprint,
        "bill_number": bill.get("bill_number") or bill.get("invoice_number"),
        "bill_date": bill.get("bill_date") or bill.get("invoice_date"),
        "store_id": bill.get("store_id"), "vendor_id": bill.get("vendor_id"),
        "recipient_entity_id": bill.get("recipient_entity_id"),
        "recipient_gstin": bill.get("recipient_gstin"), "vendor_gstin": bill.get("vendor_gstin"),
        "gst_rate": gst_rate,
        "claims_credit": claimed, "reverse_charge": bill.get("reverse_charge") is True,
        "original_quantity": float(original_qty),
        "original_taxable_paise": taxable,
        "original_tax_paise": heads,
    }


def _bill_line(db, receipt, product_id, choice, *, allow_unbooked=False):
    from api.routers.finance.gst import _bill_booked

    gid = receipt["grn_id"]
    candidates = []
    for bill in db.get_collection("vendor_bills").find({
        "vendor_id": receipt["vendor_id"],
        "$or": [{"grn_id": gid}, {"linked_dc_ids": gid}],
    }):
        if (not _bill_booked(bill) or bill.get("source_transfer_id")
                or bill.get("doc_type") not in (None, "PURCHASE_INVOICE")):
            continue
        if choice and choice.get("bill_id") != bill.get("bill_id"):
            continue
        if not bill.get("bill_id") or bill.get("store_id") != receipt.get("store_id"):
            _pending("AMBIGUOUS_BILL_OWNER", "The original bill's receiving shop is not verified.")
        lines = bill.get("lines") or []
        if not lines:
            # Even a single-product receipt does not prove that a header-only
            # bill covered ALL its accepted units. Do not invent billed qty.
            _pending("HEADER_BREAKUP_REQUIRED", "This header-only bill needs its confirmed item breakup.")
        for index, line in enumerate(lines):
            if line.get("product_id") != product_id:
                continue
            line_index = index
            if choice and (choice.get("bill_id") != bill.get("bill_id")
                           or choice.get("bill_line_index") != line_index):
                continue
            candidates.append((bill, line, line_index))
    if not candidates:
        if allow_unbooked and not choice:
            return None
        _pending("UNBOOKED_ORIGIN", "No matching booked original bill line is identified.")
    if len(candidates) != 1:
        _pending("AMBIGUOUS_BILL_LINE", "Accounts must identify the original bill line.")
    return candidates[0]


def _set_caps(origins):
    """Split one original line's cap once, preserving paise across receipts."""
    original_qty = _number(origins[0]["original_quantity"])
    total_qty = sum(origin["quantity"] for origin in origins)
    if total_qty > original_qty:
        _pending("ORIGINAL_QUANTITY_EXCEEDED", "The return exceeds the original billed quantity.")

    def shares(amount):
        exact = [Decimal(amount) * origin["quantity"] / original_qty for origin in origins]
        parts = [int(value) for value in exact]
        total = int((Decimal(amount) * total_qty / original_qty).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        order = sorted(range(len(parts)), key=lambda i: exact[i] - parts[i], reverse=True)
        for index in order[:total - sum(parts)]:
            parts[index] += 1
        return parts

    taxable = shares(origins[0]["original_taxable_paise"])
    heads = {head: shares(amount) for head, amount in origins[0]["original_tax_paise"].items()}
    for index, origin in enumerate(origins):
        origin["taxable_cap_paise"] = taxable[index]
        origin["tax_cap_paise"] = {head: parts[index] for head, parts in heads.items()}
        origin["itc_cap_paise"] = {
            head: amount if origin["claims_credit"] else 0
            for head, amount in origin["tax_cap_paise"].items()
        }
        origin["supplier_amount_cap_paise"] = taxable[index] + (
            0 if origin["reverse_charge"] else sum(origin["tax_cap_paise"].values())
        )


def _resolve_origins(db, return_doc, *, selections=None, allow_unbooked=False):
    """Resolve every returned unit to its own booked bill/line, or stay pending.

    Optional selections carry return_line_index, grn_id, quantity, bill_id and
    bill_line_index. Linked units split across repeated product/bill lines also
    require explicit stock_ids. Selections cannot contradict linked unit origins
    or a return's explicit receipt. This helper makes no writes.
    Database read failures propagate; callers must not turn them into figures.
    """
    items = return_doc.get("items") or return_doc.get("lines") or []
    if not items:
        _pending("RETURN_LINES_REQUIRED", "The return has no items to identify.")
    vendor_id, shop = return_doc.get("vendor_id"), return_doc.get("store_id")
    if not vendor_id or not shop:
        _pending("RETURN_OWNER_REQUIRED", "The return's supplier and shop must be identified.")
    return_id = return_doc.get("return_id") or return_doc.get("rma_id")
    units = list(db.get_collection("stock_units").find({"rtv_vendor_id": return_id})) if return_id else []
    wanted = set(return_doc.get("stock_ids") or [])
    if wanted and wanted != {unit.get("stock_id") for unit in units}:
        _pending("INCOMPLETE_UNIT_LINKS", "The return's physical unit links are incomplete.")
    by_product = defaultdict(list)
    unit_ids = set()
    for unit in units:
        if unit.get("store_id") != shop or not unit.get("stock_id"):
            _pending("INVALID_UNIT_OWNER", "A returned physical unit is not verified at this shop.")
        if unit.get("receipt_disposition") == "REJECTED":
            _pending("REJECTED_ORIGIN_UNBILLED", "Rejected custody alone does not prove an original billed purchase.")
        if unit["stock_id"] in unit_ids:
            _pending("DUPLICATE_UNIT_LINK", "A physical unit is linked more than once.")
        unit_ids.add(unit["stock_id"])
        by_product[unit.get("product_id")].append(unit)
    if set(by_product) - {item.get("product_id") for item in items}:
        _pending("UNEXPECTED_LINKED_UNIT", "Linked physical units do not match the return's items.")
    selected = defaultdict(list)
    for choice in selections or []:
        if not isinstance(choice, dict):
            _pending("INVALID_SELECTION", "The selected origin must identify a return item and bill line.")
        index = choice.get("return_line_index")
        if type(index) is not int or not 0 <= index < len(items):
            _pending("INVALID_SELECTION", "The selected origin does not name a return item.")
        if choice.get("bill_id") and (type(choice.get("bill_line_index")) is not int or choice["bill_line_index"] < 0):
            _pending("INVALID_SELECTION", "The selected origin must identify an original bill line.")
        selected[index].append(choice)
    product_counts = Counter(item.get("product_id") for item in items)
    for pid, linked in by_product.items():
        total = sum(_quantity(item.get("quantity", item.get("qty"))) for item in items if item.get("product_id") == pid)
        if len(linked) != total:
            _pending("INCOMPLETE_UNIT_LINKS", "Linked units do not cover the complete returned quantity.")
    result, used_units = [], set()
    receipt_quantities = Counter()
    for index, item in enumerate(items):
        pid = item.get("product_id")
        if not pid:
            _pending("PRODUCT_REQUIRED", "The returned item needs its original product identity.")
        quantity = _quantity(item.get("quantity", item.get("qty")))
        choices = selected[index]
        linked = by_product[pid]
        if product_counts[pid] > 1:
            if not choices:
                _pending("DUPLICATE_RETURN_PRODUCT", "Repeated product lines need an explicit per-line physical origin.")
            if linked:
                ids = [sid for choice in choices for sid in choice.get("stock_ids") or []]
                if len(ids) != quantity or len(set(ids)) != len(ids):
                    _pending("EXPLICIT_UNIT_SELECTION_REQUIRED", "Identify the physical units for each repeated product line.")
                linked = [unit for unit in linked if unit["stock_id"] in ids]
        origins = defaultdict(list)
        for unit in linked:
            gid = unit.get("grn_id") or (unit.get("source_id") if unit.get("source_type") == "GRN" else None)
            if not gid:
                _pending("UNIT_ORIGIN_MISSING", "A returned unit's original receipt is missing.")
            origins[gid].append(unit["stock_id"])
        if origins and sum(map(len, origins.values())) != quantity:
            _pending("INCOMPLETE_UNIT_LINKS", "Linked units do not cover the complete returned quantity.")
        if return_doc.get("grn_id") and origins and set(origins) != {return_doc["grn_id"]}:
            _pending("CONFLICTING_RECEIPT", "The return receipt conflicts with the physical unit origin.")
        if choices:
            chosen_qty = Counter()
            for choice in choices:
                chosen_qty[choice.get("grn_id")] += _quantity(choice.get("quantity"))
            if sum(chosen_qty.values()) != quantity or (origins and chosen_qty != {g: len(u) for g, u in origins.items()}):
                _pending("SELECTION_QUANTITY_MISMATCH", "Selected origins do not cover exactly the returned units.")
        elif origins:
            choices = [{"grn_id": gid, "quantity": len(ids)} for gid, ids in origins.items()]
        elif return_doc.get("grn_id"):
            choices = [{"grn_id": return_doc["grn_id"], "quantity": quantity}]
        else:
            # No unit link: consider ALL accepted origins, never sort/limit to
            # the newest one. Even an unbilled accepted receipt is a candidate.
            receipts = [g for g in db.get_collection("grns").find({
                "vendor_id": vendor_id, "store_id": shop, "items.product_id": pid,
                "status": {"$in": ["ACCEPTED", "PARTIALLY_ACCEPTED"]},
            }) if _receipt_quantity(g, pid) > 0]
            if len(receipts) != 1:
                _pending("AMBIGUOUS_RECEIPT", "Accounts must identify the returned item's original receipt and bill.")
            choices = [{"grn_id": receipts[0]["grn_id"], "quantity": quantity}]
        choice_counts = Counter(choice.get("grn_id") for choice in choices)
        for choice in choices:
            gid = choice.get("grn_id")
            if not gid or (return_doc.get("grn_id") and gid != return_doc["grn_id"]):
                _pending("CONFLICTING_RECEIPT", "The selected receipt conflicts with the return's origin.")
            receipt = db.get_collection("grns").find_one({"grn_id": gid}) or {}
            if (receipt.get("vendor_id") != vendor_id or receipt.get("status") not in {"ACCEPTED", "PARTIALLY_ACCEPTED"}
                    or (not origins and receipt.get("store_id") != shop)):
                _pending("INVALID_RECEIPT_OWNER", "The selected receipt is not a verified purchase for this return.")
            qty = _quantity(choice.get("quantity"))
            receipt_quantities[(gid, pid)] += qty
            if receipt_quantities[(gid, pid)] > _receipt_quantity(receipt, pid):
                _pending("RECEIPT_QUANTITY_EXCEEDED", "The return exceeds the receipt's accepted quantity.")
            ids = choice.get("stock_ids")
            if origins:
                if ids is None and choice_counts[gid] == 1:
                    ids = origins[gid]
                if (not isinstance(ids, list) or len(ids) != qty or len(set(ids)) != qty
                        or not set(ids).issubset(origins[gid]) or used_units.intersection(ids)):
                    _pending("EXPLICIT_UNIT_SELECTION_REQUIRED", "Identify each physical unit once for the selected bill line.")
                used_units.update(ids)
            elif ids:
                _pending("INVALID_UNIT_SELECTION", "Selected physical units are not linked to this return.")
            matched = _bill_line(db, receipt, pid, choice if choice.get("bill_id") else None,
                                 allow_unbooked=allow_unbooked)
            if matched is None:
                result.append({
                    "origin_status": "UNBOOKED", "return_line_index": index,
                    "product_id": pid, "quantity": qty, "grn_id": gid,
                    "stock_ids": sorted(ids or []), "store_id": receipt["store_id"],
                    "vendor_id": vendor_id,
                })
            else:
                bill, line, line_index = matched
                result.append(_snapshot(bill, line, line_index, qty, gid, ids or [], index))
    if used_units != unit_ids:
        _pending("INCOMPLETE_UNIT_LINKS", "Selections do not cover all linked physical units.")
    # Multiple receipt origins can share one consolidated bill line. Its cap
    # applies to the whole resolved quantity, not separately to each receipt.
    bill_lines = defaultdict(list)
    for origin in result:
        if origin["origin_status"] != "BOOKED":
            continue
        key = (origin["bill_id"], origin["bill_line_index"])
        bill_lines[key].append(origin)
    for origins in bill_lines.values():
        if len({origin["bill_line_fingerprint"] for origin in origins}) != 1:
            _pending("ORIGINAL_BILL_CHANGED", "The original bill changed during source verification. Retry the confirmation.")
        _set_caps(origins)
    return result


def resolve_return_provenance(db, return_doc, *, selections=None):
    """Strict original-bill evidence required for supplier-credit posting.

    Missing or rejected/unbilled origins are pending, never proof of claimed
    tax. An internal request uses the separate resolver below; its output must
    never be passed off as this financial evidence.
    """
    return _resolve_origins(db, return_doc, selections=selections)


def resolve_request_provenance(db, return_doc, *, selections=None):
    """Custody evidence for an internal return request, not a posted credit.

    Unique accepted but unbilled origins can describe goods being returned;
    A08 rejected units use their exact rejected receipt line instead. Neither
    establishes GST eligibility, a tax rate, or an amount owed by the supplier.
    Ambiguity, corrupt links and database failures do not become a fallback.
    """
    if not return_doc.get("receipt_rejection"):
        return _resolve_origins(db, return_doc, selections=selections, allow_unbooked=True)

    gid, index = return_doc.get("grn_id"), return_doc.get("grn_line_index")
    receipt = db.get_collection("grns").find_one({"grn_id": gid}) if gid else None
    items = return_doc.get("items") or []
    if (not receipt or receipt.get("status") not in {"ACCEPTED", "PARTIALLY_ACCEPTED"}
            or receipt.get("vendor_id") != return_doc.get("vendor_id")
            or receipt.get("store_id") != return_doc.get("store_id")
            or not return_doc.get("vendor_id") or not return_doc.get("store_id")
            or type(index) is not int or not 0 <= index < len(receipt.get("items") or [])
            or len(items) != 1):
        _pending("INVALID_REJECTED_ORIGIN", "The rejected goods' original receipt line is not verified.")
    item, receipt_line = items[0], receipt["items"][index]
    quantity = _quantity(item.get("quantity"))
    pid = item.get("product_id")
    if (not pid or receipt_line.get("product_id") != pid
            or quantity != _quantity(receipt_line.get("rejected_qty"))):
        _pending("INVALID_REJECTED_ORIGIN", "The return does not match its rejected receipt quantity.")
    wanted = return_doc.get("stock_ids") or []
    rid = return_doc.get("return_id")
    units = list(db.get_collection("stock_units").find({"rtv_vendor_id": rid})) if rid else []
    if (len(wanted) != quantity or len(set(wanted)) != quantity or len(units) != quantity
            or set(wanted) != {unit.get("stock_id") for unit in units}):
        _pending("INCOMPLETE_UNIT_LINKS", "The rejected return's physical unit links are incomplete.")
    for unit in units:
        if (unit.get("receipt_disposition") != "REJECTED" or unit.get("grn_id") != gid
                or unit.get("grn_line_index") != index or unit.get("product_id") != pid
                or unit.get("store_id") != return_doc["store_id"] or unit.get("quantity") != 1):
            _pending("INVALID_REJECTED_ORIGIN", "A linked unit does not match the rejected receipt line.")
    if selections:
        _pending("REJECTED_ORIGIN_UNBILLED", "Rejected custody cannot be assigned to an accepted-goods bill.")
    return [{
        "origin_status": "REJECTED_CUSTODY", "return_line_index": 0,
        "product_id": pid, "quantity": quantity, "grn_id": gid,
        "grn_line_index": index, "stock_ids": sorted(wanted),
        "store_id": return_doc["store_id"], "vendor_id": return_doc["vendor_id"],
    }]
