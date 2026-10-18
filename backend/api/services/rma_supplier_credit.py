"""RMA adapter for canonical supplier confirmations; no shadow RTV documents."""
from copy import deepcopy

from pymongo.errors import DuplicateKeyError

from . import supplier_credit_confirmation as canonical
from .supplier_credit_evidence import EVENTS, SupplierCreditError, confirmation_view, credit_preview, paise
from .vendor_rma import RMAStatus, VendorRMAEngine, _now

APPROVAL_BINDINGS = "rma_supplier_credit_approvals"
READY = {RMAStatus.DISPATCHED.value, RMAStatus.CREDIT_RECEIVED.value}


def normalize(doc):
    """Adapt the stored lifecycle in memory; preserve its exact CAS preimage."""
    items = []
    for line in doc.get("lines") or []:
        item = deepcopy(line)
        value = line.get("unit_cost_paise")
        item["unit_price"] = value / 100 if value is not None else line.get("unit_cost")
        items.append(item)
    return {**deepcopy(doc), "return_id": doc["rma_id"], "return_kind": "RMA",
            "items": items, "return_type": "credit_note",
            "status": "received_by_vendor" if doc.get("status") in READY else "not_ready",
            "_rma_snapshot": deepcopy(doc)}


def preview(db, doc, *, selections=None, confirmation_id=None, allowed_store_ids=None):
    return credit_preview(db, normalize(doc), selections=selections,
                          confirmation_id=confirmation_id, allowed_store_ids=allowed_store_ids)


def fence(db, event, normalized):
    raw = normalized["_rma_snapshot"]
    tokens = [None, event["token"]]
    if event.get("fenced_token"):
        tokens.append(event["fenced_token"])
    query = {"rma_id": event["return_id"], "credit_confirmation_fence": {"$in": tokens}}
    for key in ("status", "vendor_id", "store_id", "lines", "expected_credit_paise",
                "received_credit_paise", "credit_notes", "stock_ids", "grn_id"):
        query[key] = raw[key] if key in raw else {"$exists": False}
    result = db.get_collection("vendor_rmas").update_one(
        query, {"$set": {"credit_confirmation_fence": event["token"]}})
    if result.matched_count != 1:
        raise SupplierCreditError("RMA_CHANGED", "The RMA changed or another confirmation is posting. Refresh and retry.", 409)


def release(db, event):
    db.get_collection("vendor_rmas").update_one(
        {"rma_id": event["return_id"], "credit_confirmation_fence": event["token"]},
        {"$unset": {"credit_confirmation_fence": ""}})


def finish(db, event):
    """Apply each POSTED amount once, preserving legacy totals and close state."""
    collection = db.get_collection("vendor_rmas")
    current = collection.find_one({"rma_id": event["return_id"]})
    if not current:
        raise SupplierCreditError("RMA_NOT_FOUND", "The confirmed RMA needs technical reconciliation.", 503)
    if any(note.get("confirmation_id") == event["confirmation_id"] for note in current.get("credit_notes") or []):
        return
    received = int(current.get("received_credit_paise") or 0)
    amount = event["received_paise"]
    expected = current.get("expected_credit_paise")
    if expected is None:
        raise SupplierCreditError("RMA_EXPECTATION_CHANGED", "The RMA's priced expectation needs reconciliation.", 503)
    now = _now()
    note = {"confirmation_id": event["confirmation_id"], "credit_note_number": event["credit_note_number"],
            "credit_note_date": event["credit_note_date"], "received_paise": amount,
            "recorded_at": now, "recorded_by": event.get("posted_by"), "notes": event.get("notes") or ""}
    result = collection.update_one(
        {"rma_id": event["return_id"], "credit_confirmation_fence": event["token"],
         "received_credit_paise": received, "credit_notes.confirmation_id": {"$ne": event["confirmation_id"]}},
        {"$set": {"status": RMAStatus.CREDIT_RECEIVED.value, "variance_paise": int(expected) - received - amount,
                  "updated_at": now}, "$inc": {"received_credit_paise": amount},
         "$push": {"credit_notes": note, "status_history": {
             "status": RMAStatus.CREDIT_RECEIVED.value, "at": now, "by": event.get("posted_by"),
             "notes": "Recorded confirmed supplier credit " + event["credit_note_number"]}},
         "$unset": {"credit_confirmation_fence": ""}})
    if result.matched_count != 1:
        fresh = collection.find_one({"rma_id": event["return_id"]}) or {}
        if not any(n.get("confirmation_id") == event["confirmation_id"] for n in fresh.get("credit_notes") or []):
            raise SupplierCreditError("RMA_CONFIRMATION_UNCERTAIN", "The supplier credit posted; retry unchanged to repair the RMA summary.", 503)
    else:
        engine = VendorRMAEngine(db=db)
        after = {**current, "status": RMAStatus.CREDIT_RECEIVED.value,
                 "received_credit_paise": received + amount, "variance_paise": int(expected) - received - amount}
        engine._audit("rma_credit_recorded", after, actor=event.get("posted_by"),
                      before=engine._snapshot(current), after=engine._snapshot(after), reason=event.get("notes"))


def existing_confirmation(db, doc, submitted):
    event = db.get_collection(EVENTS).find_one({"_id": submitted["confirmation_id"]})
    if not event:
        return None
    expected = {"return_kind": "RMA", "return_id": doc["rma_id"], "vendor_id": doc["vendor_id"],
                "store_id": doc["store_id"], **{key: submitted[key] for key in (
                    "normalized_number", "credit_note_date", "received_paise")}}
    if any(event.get(key) != value for key, value in expected.items()):
        raise SupplierCreditError("CONFIRMATION_IDENTITY_CHANGED", "This confirmation belongs to a different immutable supplier note.", 409)
    if event.get("posting_status") in {"PREPARING", "POSTED"} and event.get("request_hash") != submitted["request_hash"]:
        raise SupplierCreditError("CONFIRMATION_ALREADY_SUBMITTED", "Retry the original confirmation unchanged.", 409)
    return event


def existing_acknowledgement(doc, submitted):
    """The zero-credit acknowledgement is operational, never a posting fact."""
    for note in doc.get("credit_notes") or []:
        if note.get("operational_acknowledgement_id") == submitted["confirmation_id"]:
            if note.get("request_hash") != submitted["request_hash"]:
                raise SupplierCreditError("ACKNOWLEDGEMENT_CHANGED", "Retry the original zero-credit acknowledgement unchanged.", 409)
            return note
        number = " ".join(str(note.get("credit_note_number") or "").upper().split())
        if number == submitted["normalized_number"] and note.get("confirmation_id") != submitted["confirmation_id"]:
            raise SupplierCreditError("duplicate_credit_note", "This note number is already recorded against this RMA.", 409)
    return None


def _summary(doc, *, repeated):
    return {"ok": True, "rma_id": doc["rma_id"], "idempotent": repeated,
            "status": doc.get("status"), "received_credit_paise": doc.get("received_credit_paise"),
            "expected_credit_paise": doc.get("expected_credit_paise"), "variance_paise": doc.get("variance_paise"),
            "fully_reconciled": doc.get("variance_paise") is not None and doc["variance_paise"] <= 0}


def record_zero(db, doc, submitted, *, actor_id):
    """Preserve the legacy zero acknowledgement/write-off path without credit."""
    if submitted["received_paise"] != 0 or submitted["allocations"]:
        raise SupplierCreditError("ZERO_ACKNOWLEDGEMENT_ONLY", "A zero acknowledgement cannot contain credit allocations.")
    repeated = existing_acknowledgement(doc, submitted)
    if repeated:
        return {**_summary(doc, repeated=True), "operational_acknowledgement": repeated}
    now = _now()
    note = {"operational_acknowledgement_id": submitted["confirmation_id"],
            "request_hash": submitted["request_hash"], "credit_note_number": submitted["credit_note_number"],
            "credit_note_date": submitted["credit_note_date"], "received_paise": 0,
            "recorded_at": now, "recorded_by": actor_id, "notes": submitted.get("notes") or ""}
    query = {"rma_id": doc["rma_id"], "credit_confirmation_fence": None}
    for key in ("status", "lines", "expected_credit_paise", "received_credit_paise", "credit_notes"):
        query[key] = doc[key] if key in doc else {"$exists": False}
    received = int(doc.get("received_credit_paise") or 0)
    variance = int(doc["expected_credit_paise"]) - received
    result = db.get_collection("vendor_rmas").update_one(query, {
        "$set": {"status": RMAStatus.CREDIT_RECEIVED.value, "variance_paise": variance, "updated_at": now},
        "$push": {"credit_notes": note, "status_history": {
            "status": RMAStatus.CREDIT_RECEIVED.value, "at": now, "by": actor_id,
            "notes": "Recorded zero-credit operational acknowledgement " + submitted["credit_note_number"]}}})
    fresh = db.get_collection("vendor_rmas").find_one({"rma_id": doc["rma_id"]})
    if result.matched_count != 1:
        repeated = existing_acknowledgement(fresh or {}, submitted)
        if not repeated:
            raise SupplierCreditError("RMA_CHANGED", "The RMA changed or another confirmation is posting. Refresh and retry.", 409)
    else:
        engine = VendorRMAEngine(db=db)
        engine._audit("rma_credit_recorded", fresh, actor=actor_id,
                      before=engine._snapshot(doc), after=engine._snapshot(fresh), reason=submitted.get("notes"))
    return {**_summary(fresh, repeated=result.matched_count != 1), "operational_acknowledgement": repeated or note}


def require_approval(db, doc, submitted, *, request_id, token, actor_id, consume):
    """Bind before consuming; a lost consume reply can only resume this note."""
    if not request_id:
        raise SupplierCreditError("approval_required", "This credit needs an approval request bound to this RMA and shop.", 403)
    requests = db.get_collection("approval_requests")
    request = requests.find_one({"request_id": request_id})
    if (not request or request.get("status") not in {"APPROVED", "CONSUMED"}
            or (token and request.get("approval_token") != token)):
        raise SupplierCreditError("approval_invalid", "The approval is missing or does not match this request.", 403)
    reviewer = request.get("reviewed_by")
    if not actor_id or not reviewer or reviewer in (actor_id, request.get("requested_by")):
        raise SupplierCreditError("cannot_approve_own", "The approval must come from a second person, other than its requester and the credit recorder.", 403)
    if (request.get("action_type") != "rtv" or request.get("store_id") != doc["store_id"]
            or (request.get("context") or {}).get("rma_id") != doc["rma_id"]
            or (request.get("amount") is not None and paise(request["amount"]) < submitted["received_paise"])):
        raise SupplierCreditError("approval_invalid", "The approval does not cover this RMA, shop and amount.", 403)
    binding = {"confirmation_id": submitted["confirmation_id"], "rma_id": doc["rma_id"],
               "store_id": doc["store_id"], "actor_id": actor_id,
               "normalized_number": submitted["normalized_number"], "credit_note_date": submitted["credit_note_date"],
               "received_paise": submitted["received_paise"], "expected_credit_paise": doc.get("expected_credit_paise"),
               "received_before_paise": doc.get("received_credit_paise")}
    bindings = db.get_collection(APPROVAL_BINDINGS)
    saved = bindings.find_one({"_id": request_id})
    if saved is None:
        if request.get("status") != "APPROVED" or request.get("consumed"):
            raise SupplierCreditError("approval_invalid", "An already consumed approval cannot authorize a new supplier confirmation.", 403)
        try:
            bindings.insert_one({"_id": request_id, **binding})
        except DuplicateKeyError:
            pass
        saved = bindings.find_one({"_id": request_id})
    if not saved or any(saved.get(key) != value for key, value in binding.items()):
        raise SupplierCreditError("approval_invalid", "This approval is reserved for a different confirmation or RMA amount. Retry its original note.", 403)
    if request.get("status") == "APPROVED":
        consume()
        request = requests.find_one({"request_id": request_id})
    if (not request or request.get("status") != "CONSUMED" or request.get("consumed") is not True
            or request.get("consumed_by") != actor_id):
        raise SupplierCreditError("approval_invalid", "The approval could not be verified as consumed for this confirmation.", 403)


def record(db, doc, payload, *, actor_id, allowed_store_ids=None):
    event, repeated = canonical.record_supplier_confirmation(
        db, normalize(doc), payload, actor_id=actor_id, allowed_store_ids=allowed_store_ids)
    current = db.get_collection("vendor_rmas").find_one({"rma_id": doc["rma_id"]}) or doc
    shown = confirmation_view(event)
    return {"ok": True, "rma_id": doc["rma_id"], "idempotent": repeated,
            "confirmation": shown, "status": current.get("status"),
            "received_credit_paise": current.get("received_credit_paise"),
            "expected_credit_paise": current.get("expected_credit_paise"),
            "variance_paise": current.get("variance_paise"),
            "fully_reconciled": current.get("variance_paise") is not None and current["variance_paise"] <= 0}
