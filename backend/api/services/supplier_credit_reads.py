"""One POSTED supplier-confirmation fact feeds AP, input tax and exports.

Pending/reserved/internal request documents are never accounting rows. Readers
do not re-infer an old event from today's vendor, bill or shop configuration.
Database failures propagate so a caller cannot silently display partial money.
"""
from collections import defaultdict

from .supplier_credit_evidence import EVENTS, HEADS, return_event_query


def confirmed_allocations(db, *, vendor_id=None, bill_id=None, store_id=None,
                          entity_id=None, gstin=None, period=None, return_id=None):
    query = {"posting_status": "POSTED"}
    if vendor_id:
        query["vendor_id"] = vendor_id
    if return_id:
        query["return_id"] = return_id
    if period:
        query["credit_note_date"] = {"$gte": f"{period}-01", "$lt": f"{period}-32"}
    filters = {key: value for key, value in (
        ("bill_id", bill_id), ("store_id", store_id),
        ("recipient_entity_id", entity_id), ("recipient_gstin", gstin),
    ) if value is not None}
    if filters:
        query["allocations"] = {"$elemMatch": filters}
    rows = []
    for event in db.get_collection(EVENTS).find(query):
        for allocation in event["allocations"]:
            if any(allocation.get(key) != value for key, value in filters.items()):
                continue
            rows.append({**allocation, "confirmation_id": event["confirmation_id"],
                         "return_id": event["return_id"], "vendor_id": event["vendor_id"],
                         "credit_note_number": event["credit_note_number"],
                         "credit_note_date": event["credit_note_date"], "posted_at": event["posted_at"],
                         "posted_by": event["posted_by"]})
    return rows


def confirmed_ap_rows(db, *, vendor_id=None, bill_id=None, store_id=None):
    """One amount per supplier note and original bill, using the AP row shape."""
    groups = {}
    for allocation in confirmed_allocations(db, vendor_id=vendor_id, bill_id=bill_id, store_id=store_id):
        key = (allocation["confirmation_id"], allocation["bill_id"])
        row = groups.setdefault(key, {
            "debit_note_id": f"SUPPLIER-CN:{key[0]}:{key[1]}",
            "debit_note_number": allocation["credit_note_number"],
            "vendor_id": allocation["vendor_id"], "store_id": allocation["store_id"],
            "bill_id": allocation["bill_id"], "return_id": allocation["return_id"],
            "confirmation_id": allocation["confirmation_id"], "date": allocation["credit_note_date"],
            "amount_paise": 0, "source": "SUPPLIER_CONFIRMED_CREDIT", "cn_type": "RETURN_CN",
            "reason": "Supplier-confirmed return credit", "created_at": allocation["posted_at"],
            "created_by": allocation["posted_by"],
        })
        row["amount_paise"] += allocation["supplier_amount_paise"]
    return [{**row, "amount": row["amount_paise"] / 100} for row in groups.values()]


def payable_credit_rows(db, *, vendor_id=None, bill_id=None, store_id=None):
    """Preserve existing manual/scheme credits, add confirmed returns once."""
    query = {key: value for key, value in (("vendor_id", vendor_id), ("bill_id", bill_id), ("store_id", store_id))
             if value is not None}
    legacy = list(db.get_collection("vendor_debit_notes").find(query, {"_id": 0}))
    return legacy + confirmed_ap_rows(db, vendor_id=vendor_id, bill_id=bill_id, store_id=store_id)


def confirmed_itc_rows(db, *, store_id=None, entity_id=None, gstin=None, period=None):
    """Negative register rows in the supplier-note period, only original ITC."""
    groups = {}
    for allocation in confirmed_allocations(db, store_id=store_id, entity_id=entity_id, gstin=gstin, period=period):
        if not allocation["claims_credit"]:
            continue
        key = (allocation["confirmation_id"], allocation["bill_id"])
        row = groups.setdefault(key, {
            "bill_id": f"SUPPLIER-CN:{key[0]}:{key[1]}", "original_bill_id": allocation["bill_id"],
            "confirmation_id": allocation["confirmation_id"], "bill_number": allocation["credit_note_number"],
            "bill_date": allocation["credit_note_date"], "vendor_id": allocation["vendor_id"],
            "vendor_gstin": allocation["vendor_gstin"], "store_id": allocation["store_id"],
            "recipient_entity_id": allocation["recipient_entity_id"], "recipient_gstin": allocation["recipient_gstin"],
            "taxable_amount": 0, "tax_amount": 0, "cgst_total": 0, "sgst_total": 0, "igst_total": 0,
            "document_kind": "SUPPLIER_CONFIRMED_CREDIT", "reverse_charge": allocation["reverse_charge"],
            "status": "POSTED", "received": True, "itc_eligible": True,
        })
        row["taxable_amount"] -= allocation["taxable_paise"]
        for head in HEADS:
            row[f"{head}_total"] -= allocation["itc_reversal_paise"][head]
            row["tax_amount"] -= allocation["itc_reversal_paise"][head]
    for row in groups.values():
        for key in ("taxable_amount", "tax_amount", "cgst_total", "sgst_total", "igst_total"):
            row[key] /= 100
    return list(groups.values())


def confirmed_itc_heads(db, *, store_id=None, entity_id=None, gstin=None, period=None):
    """Positive reversal heads; consumers subtract once without zero-clamping."""
    values = defaultdict(int)
    for allocation in confirmed_allocations(db, store_id=store_id, entity_id=entity_id, gstin=gstin, period=period):
        for head in HEADS:
            values[head] += allocation["itc_reversal_paise"][head]
    return {head: values[head] / 100 for head in HEADS}


def confirmed_tally_xml(db, request_doc):
    """Export actual posted supplier notes, never the internal requested sum."""
    from .rtv_debit_note import tally_build_debit_notes_xml

    rid = (request_doc.get("rtv_ref") or {}).get("id") or request_doc.get("rtv_ref_id")
    kind = "RMA" if (request_doc.get("rtv_ref") or {}).get("type") == "vendor_rma" else "VENDOR_RETURN"
    events = list(db.get_collection(EVENTS).find({**return_event_query({"return_id": rid, "return_kind": kind}), "posting_status": "POSTED"}))
    if not rid or not events:
        raise ValueError("supplier_confirmation_required")
    documents = []
    for event in events:
        heads = {h: sum(a["itc_reversal_paise"][h] for a in event["allocations"]) for h in HEADS}
        # RCM tax was self-paid, never owed to the supplier. The balancing
        # purchase-return leg retains that lost credit as purchase expense.
        totals = {"taxable_paise": event["received_paise"] - sum(heads.values()),
                  **{f"{h}_paise": amount for h, amount in heads.items()},
                  "tax_paise": sum(heads.values()), "grand_total_paise": event["received_paise"]}
        document = {**request_doc, "accounting_status": "POSTED", "confirmation_id": event["confirmation_id"],
                    "debit_note_number": event["credit_note_number"], "issue_date": event["credit_note_date"], "totals": totals}
        documents.append(document)
    return tally_build_debit_notes_xml(documents)
