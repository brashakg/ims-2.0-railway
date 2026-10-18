"""Explicit original purchase evidence for legacy RMA permission/lifecycle tests."""
from datetime import datetime
from uuid import uuid4

from api.services.rma_supplier_credit import normalize
from api.services.supplier_credit_evidence import credit_preview


def credit_payload(db, rma_id, *, amount, number="CN-1"):
    """Seed one original zero-tax bill for a priced RMA, then allocate its note.

    The fixture preserves the old amount/variance tests while supplying the
    original evidence now required for a positive supplier confirmation.
    """
    data = {"credit_note_number": number, "credit_note_date": "2026-10-18",
            "idempotency_key": str(uuid4()), "received_amount": amount}
    doc = db.get_collection("vendor_rmas").find_one({"rma_id": rma_id})
    if not doc or doc.get("expected_credit_paise") is None:
        return data
    gid = "original-" + rma_id
    bid = "bill-" + rma_id
    if not db.get_collection("grns").find_one({"grn_id": gid}):
        items = [{"product_id": row["product_id"], "accepted_qty": row["quantity"]} for row in doc["lines"]]
        lines = [{"product_id": row["product_id"], "qty": row["quantity"],
                  "taxable": row["quantity"] * row["unit_cost_paise"] / 100,
                  "cgst": 0, "sgst": 0, "igst": 0, "gst_rate": 0,
                  "line_total": row["quantity"] * row["unit_cost_paise"] / 100} for row in doc["lines"]]
        db.get_collection("grns").insert_one({"grn_id": gid, "store_id": doc["store_id"],
            "vendor_id": doc["vendor_id"], "status": "ACCEPTED", "items": items})
        db.get_collection("vendor_bills").insert_one({"bill_id": bid, "grn_id": gid,
            "store_id": doc["store_id"], "vendor_id": doc["vendor_id"], "status": "UNPAID",
            "received": True, "doc_type": "PURCHASE_INVOICE", "bill_number": "INV-" + rma_id,
            "bill_date": datetime(2026, 1, 1), "recipient_entity_id": "RMA-ENTITY",
            "recipient_gstin": "20AAAAA0000A1Z5", "vendor_gstin": "20BBBBB0000B1Z5",
            "lines": lines, "taxable_amount": doc["expected_credit_paise"] / 100,
            "tax_amount": 0, "cgst_total": 0, "sgst_total": 0, "igst_total": 0})
        if not db.get_collection("entities").find_one({"entity_id": "RMA-ENTITY"}):
            db.get_collection("entities").insert_one({"entity_id": "RMA-ENTITY",
                "gstins": [{"gstin": "20AAAAA0000A1Z5"}]})
        db.get_collection("vendor_rmas").update_one({"rma_id": rma_id}, {"$set": {"grn_id": gid}})
        doc["grn_id"] = gid
    preview = credit_preview(db, normalize(doc))
    assert preview["source_status"] == "RESOLVED", preview
    assert len(preview["sources"]) == 1, "This fixture intentionally covers a single-line RMA."
    if amount:
        data["allocations"] = [{"source_key": preview["sources"][0]["source_key"],
                                "taxable": amount, "cgst": 0, "sgst": 0, "igst": 0}]
    return data
