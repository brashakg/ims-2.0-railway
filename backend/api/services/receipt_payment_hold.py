"""Rejected custody releases payment only with verifiable bill/return evidence."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .return_provenance import ReturnProvenanceError, resolve_request_provenance
from .supplier_credit_evidence import return_event_query


def _qty(value):
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Missing billed or received quantity") from exc
    if not quantity.is_finite() or quantity < 0 or quantity != quantity.to_integral_value():
        raise ValueError("Invalid billed or received quantity")
    return int(quantity)


def rejected_receipt_hold(db, bill_id):
    """Return a hold message, or None after proving the approved exception.

    Accepted-only quantities need confirmed A08 custody requests. If the bill
    includes rejected units, each request must instead have a fully confirmed
    canonical supplier credit allocated to this bill. Unclear data never pays.
    """
    if db is None or not bill_id:
        return "The original bill could not be verified before payment."
    try:
        bill = db.get_collection("vendor_bills").find_one({"bill_id": bill_id})
        if not bill:
            return "The original bill could not be verified before payment."
        gids = set([bill["grn_id"]] if bill.get("grn_id") else []) | set(bill.get("linked_dc_ids") or [])
        if not gids:
            return None
        receipts, rejected = [], []
        for gid in gids:
            receipt = db.get_collection("grns").find_one({"grn_id": gid})
            if not receipt:
                return "A linked receipt is missing; Accounts must verify it before payment."
            receipts.append(receipt)
            for index, line in enumerate(receipt.get("items") or []):
                if _qty(line.get("rejected_qty") or 0):
                    rejected.append((receipt, index))
        if not rejected:
            return None
        if not bill.get("lines"):
            return "This receipt has rejected goods; verify the bill's item quantities before payment."
        from api.routers.finance.gst import _bill_booked
        other_bills = [b for b in db.get_collection("vendor_bills").find({
            "$or": [{"grn_id": {"$in": list(gids)}}, {"linked_dc_ids": {"$in": list(gids)}}],
        }) if _bill_booked(b) and b.get("bill_id") != bill_id]
        if other_bills:
            return "More than one bill covers the rejected receipt; Accounts must verify which units were charged before payment."
        accepted, billed = defaultdict(int), defaultdict(int)
        for receipt in receipts:
            if receipt.get("vendor_id") != bill.get("vendor_id") or receipt.get("store_id") != bill.get("store_id"):
                return "The bill and rejected receipt have conflicting ownership; payment remains on hold."
            for line in receipt.get("items") or []:
                if not line.get("product_id"):
                    raise ValueError("Receipt product missing")
                accepted[line["product_id"]] += _qty(line.get("accepted_qty"))
        for line in bill["lines"]:
            if not line.get("product_id"):
                raise ValueError("Bill product missing")
            billed[line["product_id"]] += _qty(line.get("qty"))
        if set(billed) - set(accepted):
            return "The bill contains items outside its verified receipts; payment remains on hold."
        accepted_only = all(quantity <= accepted[pid] for pid, quantity in billed.items())
        for receipt, index in rejected:
            requests = list(db.get_collection("vendor_returns").find({
                "receipt_rejection": True, "grn_id": receipt["grn_id"], "grn_line_index": index,
            }))
            if len(requests) != 1:
                return "Confirm the rejected goods' internal return request before paying this bill."
            request = requests[0]
            if request.get("status") in (None, "draft", "cancelled") or request.get("return_type") not in ("credit_note", "replacement"):
                return "Confirm the rejected goods' credit or replacement request before paying this bill."
            resolve_request_provenance(db, request)
            if accepted_only:
                continue
            events = list(db.get_collection("supplier_credit_confirmations").find({
                **return_event_query(request), "posting_status": "POSTED",
            }))
            if (request.get("status") != "credit_issued" or not events
                    or any(a.get("bill_id") != bill_id for event in events for a in event.get("allocations") or [])):
                return "This bill may charge rejected goods; Accounts must confirm the supplier's actual credit against this bill before payment."
        return None
    except (ReturnProvenanceError, ValueError):
        return "The rejected goods' bill or custody evidence is incomplete; Accounts must verify it before payment."
    except Exception:
        return "Rejected-goods payment checks are unavailable; retry after the records can be verified."
