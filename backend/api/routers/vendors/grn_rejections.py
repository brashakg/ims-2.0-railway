"""Receipt-rejected physical custody and an unissued supplier-return draft.

Runs under the existing GRN accept claim. Nothing here posts supplier money,
allocates a debit-note serial, changes product cost or makes stock available.
"""
from datetime import datetime, timezone
from hashlib import sha256

from fastapi import HTTPException

from ...services import barcode
from .grn_accept_lock import _grn_accept_heartbeat_tick


def _identity(prefix, grn_id, line_index, ordinal=None):
    value = f"{grn_id}:{line_index}:{ordinal}"
    return prefix + sha256(value.encode("utf-8")).hexdigest()[:32]


def _insert_once(collection, doc):
    """The deterministic Mongo _id is a mandatory uniqueness backstop.

    Read through the raw collection: repository find methods swallow errors.
    A lost insert reply is repaired by retrying this same identity, never by
    deleting a row or resetting an already-confirmed return/quarantine state.
    """
    existing = collection.find_one({"_id": doc["_id"]})
    if existing is not None:
        return existing, False
    try:
        collection.insert_one(dict(doc))
        return doc, True
    except Exception as exc:
        if exc.__class__.__name__ != "DuplicateKeyError":
            raise
        existing = collection.find_one({"_id": doc["_id"]})
        if existing is None:
            # Some OTHER index rejected this row: it was not received.
            raise
        return existing, False


def _audit_unit(db, unit, actor):
    # Same fail-soft audit/event convention as the accepted-unit mint. The
    # immutable custody row itself retains actor, reason and timestamp.
    from .numbering import _grn_stock_audit

    _grn_stock_audit(unit["stock_id"], "QUARANTINED", unit["grn_id"],
                     unit.get("po_id"), unit.get("store_id"), actor)
    try:
        from ...services import item_events as ie

        ie.record_post_write_event(
            db, event_type=ie.ItemEventType.MINT, actor_id=actor or "",
            stock_id=unit["stock_id"], from_state=None,
            to_state=ie.StockState.QUARANTINED, store_id=unit.get("store_id"),
            product_id=unit.get("product_id"), source_type="GRN",
            source_id=unit["grn_id"], payload={"receipt_disposition": "REJECTED"},
        )
    except Exception:  # noqa: BLE001 - custody survives an event-store outage
        pass


def handoff_rejected_units(db, grn_repo, stock_repo, grn, current_user,
                          claim_token, heartbeat_state):
    """One stable quarantine unit per rejected piece, one draft per GRN line.

    Negative ordinals are reserved for rejected custody; existing accepted
    ordinals remain 0..N-1. A line's disposition is independent of other lines.
    Legacy receipts without a choice stay draft until a manager chooses it.
    """
    lines = [(idx, item) for idx, item in enumerate(grn.get("items") or [])
             if int(item.get("rejected_qty") or 0) > 0]
    result = {"quarantined_units": 0, "rejection_return_ids": []}
    if not lines:
        return result
    if db is None or stock_repo is None:
        raise HTTPException(status_code=503, detail="Rejected-goods storage is unavailable. Retry receipt posting.")
    stock = getattr(stock_repo, "collection", None)
    if stock is None:
        stock = db.get_collection("stock_units")
    returns = db.get_collection("vendor_returns")
    actor = current_user.get("user_id")
    grn_id = grn["grn_id"]
    now = datetime.now(timezone.utc)
    try:
        vendor = db.get_collection("vendors").find_one({"vendor_id": grn.get("vendor_id")}) or {}
        for index, item in lines:
            return_id = _identity("VR-GRN-", grn_id, index)
            stock_ids = []
            quantity = int(item["rejected_qty"])
            reason = str(item.get("rejection_reason") or "Rejection recorded at receiving")
            for ordinal in range(quantity):
                _grn_accept_heartbeat_tick(grn_repo, grn_id, claim_token, heartbeat_state)
                stock_id = _identity("GRN-REJ-", grn_id, index, ordinal)
                existing = stock.find_one({"_id": stock_id})
                if existing is None:
                    doc = {
                        "_id": stock_id, "stock_id": stock_id,
                        "store_id": grn.get("store_id"), "product_id": item.get("product_id"),
                        "barcode": barcode.mint_unit_barcode(db, grn.get("store_id")),
                        "quantity": 1, "status": "QUARANTINED", "is_reserved": False,
                        "barcode_printed": False, "location_code": item.get("location_code") or "DEFAULT",
                        "source_type": "GRN", "source_id": grn_id, "grn_id": grn_id,
                        "grn_number": grn.get("grn_number"), "po_id": grn.get("po_id"),
                        "grn_line_index": index, "line_unit_seq": -1 - ordinal,
                        "receipt_disposition": "REJECTED", "rtv_vendor_id": return_id,
                        "quarantine_generation": stock_id, "quarantine_reason": "OTHER",
                        "quarantine_notes": reason, "quarantine_at": now,
                        "quarantine_by": actor,
                        "quarantine_by_name": current_user.get("name") or current_user.get("username") or "",
                        "quarantine_label_printed": False,
                        "created_by": actor, "created_at": now.isoformat(), "updated_at": now.isoformat(),
                    }
                    for field in ("batch_code", "lot_number", "expiry_date"):
                        if item.get(field):
                            doc[field] = item[field]
                    if item.get("rejection_photo_file_id"):
                        doc["quarantine_photo_file_id"] = item["rejection_photo_file_id"]
                    existing, inserted = _insert_once(stock, doc)
                    if inserted:
                        _audit_unit(db, existing, actor)
                if (existing.get("grn_id") != grn_id or existing.get("grn_line_index") != index
                        or existing.get("receipt_disposition") != "REJECTED"):
                    raise RuntimeError("Rejected-unit identity does not match its receipt")
                stock_ids.append(stock_id)

            _grn_accept_heartbeat_tick(grn_repo, grn_id, claim_token, heartbeat_state)
            disposition = item.get("rejection_disposition")
            product = db.get_collection("products").find_one({"product_id": item.get("product_id")}) or {}
            doc = {
                "_id": return_id, "return_id": return_id,
                "vendor_id": grn.get("vendor_id"),
                "vendor_name": grn.get("vendor_name") or vendor.get("trade_name") or vendor.get("legal_name") or "",
                "store_id": grn.get("store_id"), "grn_id": grn_id,
                "grn_number": grn.get("grn_number"), "grn_line_index": index,
                "receipt_rejection": True, "stock_ids": stock_ids,
                "return_type": disposition if disposition in ("credit_note", "replacement") else None,
                "status": "draft", "debit_note_draft_status": "DRAFT",
                "debit_note_draft_id": "DRAFT-" + return_id,
                "items": [{"product_id": item.get("product_id"),
                           "product_name": product.get("name") or item.get("product_name") or item.get("product_id"),
                           "quantity": quantity, "reason": reason, "unit_price": None,
                           "photo_file_id": item.get("rejection_photo_file_id")}],
                "total_value": None, "credit_note_number": None, "credit_note_amount": None,
                "notes": "Rejected at receiving. Confirm credit or replacement; Accounts review prices before issuing a note.",
                "created_by": actor, "created_at": now.isoformat(),
                "status_history": [{"status": "draft", "timestamp": now.isoformat(),
                                    "changed_by": actor, "notes": "Receipt rejection handoff"}],
            }
            saved, _ = _insert_once(returns, doc)
            if saved.get("grn_id") != grn_id or saved.get("grn_line_index") != index:
                raise RuntimeError("Return draft identity does not match its receipt")
            result["quarantined_units"] += quantity
            result["rejection_return_ids"].append(return_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=(
            "Rejected-goods posting is incomplete. Retry this receipt from History; "
            "already recorded pieces and drafts will not be duplicated."
        )) from exc
    return result
