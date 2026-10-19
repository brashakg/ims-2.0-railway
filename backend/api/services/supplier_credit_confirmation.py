"""Canonical confirmed supplier credit, with fenced standalone-Mongo writes.

Only POSTED events are accounting facts. A bill's unique _id guard serializes
quantity/value/head reservations; a token-matching event transition publishes
all allocations together. No expiry releases an uncertain reservation. Retrying
the same UUID resumes it, and fencing precedes release after known rejection.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
from uuid import UUID, uuid4

from pymongo.errors import DuplicateKeyError

from .return_provenance import ReturnProvenanceError
from .supplier_credit_evidence import (
    EVENTS, HEADS, SupplierCreditError, credit_preview, paise, resolve_credit_evidence, return_event_query,
)

GUARDS = "supplier_credit_bill_guards"
IDENTITIES = "supplier_credit_note_identities"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _error(code, message, status=422):
    raise SupplierCreditError(code, message, status)


def _input(payload, *, allow_zero=False):
    try:
        identifier = str(UUID(str(payload.get("idempotency_key"))))
    except (ValueError, TypeError, AttributeError) as exc:
        raise SupplierCreditError("INVALID_CONFIRMATION_ID", "The confirmation needs a UUID idempotency key.") from exc
    number = str(payload.get("credit_note_number") or "").strip()
    if not number or len(number) > 120:
        _error("SUPPLIER_NOTE_NUMBER_REQUIRED", "Enter the supplier's actual credit-note number (up to 120 characters).")
    # The supplier's note date places the input-credit reversal on GSTR-3B
    # (owner/CA 2026-10-18: the month of the SUPPLIER'S note), so it is read
    # by THE bill-date rule (ap_engine.iso_bill_date: a real day from the
    # start of GST to today in IST) -- a mistyped future year would otherwise
    # move the reversal onto a return nobody files.
    from .ap_engine import iso_bill_date

    try:
        note_date = iso_bill_date(str(payload.get("credit_note_date") or "")[:10])
    except (ValueError, TypeError) as exc:
        raise SupplierCreditError(
            "SUPPLIER_NOTE_DATE_REQUIRED",
            "Enter the date printed on the supplier's credit note as YYYY-MM-DD, "
            "between 1 July 2017 and today.",
        ) from exc
    allocations, keys = [], set()
    for allocation in payload.get("allocations") or []:
        key = allocation.get("source_key")
        if not isinstance(key, str) or len(key) != 64 or key in keys:
            _error("INVALID_ALLOCATION", "Select each verified original source only once.")
        keys.add(key)
        allocations.append({"source_key": key, **{f"{h}_paise": paise(allocation.get(h)) for h in ("taxable", *HEADS)}})
    notes = str(payload.get("notes") or "").strip()
    if len(notes) > 4000:
        _error("NOTES_TOO_LONG", "Keep the confirmation notes within 4000 characters.")
    result = {"confirmation_id": identifier, "credit_note_number": number,
              "normalized_number": " ".join(number.upper().split()), "credit_note_date": note_date,
              "received_paise": paise(payload.get("received_amount")), "notes": notes or None,
              "source_selections": payload.get("source_selections") or [],
              "allocations": sorted(allocations, key=lambda row: row["source_key"])}
    if result["received_paise"] <= 0 and not allow_zero:
        _error("POSITIVE_SUPPLIER_CREDIT_REQUIRED", "Enter a positive amount actually credited by the supplier.")
    result["request_hash"] = _hash(result)
    return result


def _locked(db, note_date):
    posting_date = date.fromisoformat(note_date)
    if db.get_collection("period_locks").find_one({"year": posting_date.year, "month": posting_date.month}):
        _error("CLOSED_ACCOUNTING_PERIOD", "The supplier credit-note period is locked; Accounts must resolve the closed period first.", 423)


def _identity(db, event, registration):
    """Deduplicate even if a lost response is followed by a new form UUID."""
    collection = db.get_collection(IDENTITIES)
    key = _hash([event["vendor_id"], event["normalized_number"], event["credit_note_date"]])
    try:
        collection.update_one({"_id": key}, {"$setOnInsert": {"version": 0, "claims": []}}, upsert=True)
    except DuplicateKeyError:
        pass
    for _ in range(12):
        state = collection.find_one({"_id": key})
        claims = state["claims"]
        for claim in claims:
            if claim["confirmation_id"] == event["confirmation_id"]:
                if claim.get("registration") not in (None, registration) and registration is not None:
                    _error("SUPPLIER_NOTE_REGISTRATION_CHANGED", "The supplier note's original registration cannot change.", 409)
            elif registration is None or claim.get("registration") in (None, registration):
                _error("SUPPLIER_NOTE_ALREADY_RECORDED", "This supplier note has already been recorded. Resume its existing confirmation instead.", 409)
        replacement = [c for c in claims if c["confirmation_id"] != event["confirmation_id"]]
        previous = next((c.get("registration") for c in claims if c["confirmation_id"] == event["confirmation_id"]), None)
        replacement.append({"confirmation_id": event["confirmation_id"], "return_id": event["return_id"],
                            "registration": registration if registration is not None else previous})
        saved = collection.update_one({"_id": key, "version": state["version"]},
                                      {"$set": {"claims": replacement}, "$inc": {"version": 1}})
        if saved.matched_count:
            db.get_collection(EVENTS).update_one({"_id": event["_id"]}, {"$set": {"identity_claimed": True}})
            return
    _error("SUPPLIER_NOTE_BUSY", "Another confirmation is recording this supplier note. Retry the same request.", 409)


def _event(db, return_doc, submitted, actor_id):
    collection = db.get_collection(EVENTS)
    identifier = submitted["confirmation_id"]
    candidate = {"_id": identifier, **submitted, "return_id": return_doc["return_id"],
                 "return_kind": return_doc.get("return_kind", "VENDOR_RETURN"),
                 "vendor_id": return_doc["vendor_id"], "store_id": return_doc["store_id"],
                 "posting_status": "PENDING_EVIDENCE", "pending_reasons": [], "version": 0,
                 "identity_claimed": False,
                 "created_at": _now(), "created_by": actor_id}
    try:
        collection.insert_one(candidate)
    except DuplicateKeyError:
        pass
    existing = collection.find_one({"_id": identifier})
    if existing.get("return_kind", "VENDOR_RETURN") != candidate["return_kind"]:
        _error("CONFIRMATION_IDENTITY_CHANGED", "This confirmation belongs to another return workflow.", 409)
    immutable = ("return_id", "vendor_id", "store_id", "normalized_number", "credit_note_date", "received_paise")
    if any(existing.get(k) != candidate.get(k) for k in immutable):
        _error("CONFIRMATION_IDENTITY_CHANGED", "This confirmation's supplier, return, note number, date and amount are immutable.", 409)
    if existing.get("posting_status") in {"PREPARING", "POSTED"} and existing["request_hash"] != submitted["request_hash"]:
        _error("CONFIRMATION_ALREADY_SUBMITTED", "Retry the original confirmation unchanged; its posting result must be resolved first.", 409)
    return existing


def _pending_event(db, event, submitted, reasons, actor_id):
    updated = db.get_collection(EVENTS).update_one(
        {"_id": event["_id"], "version": event["version"], "posting_status": "PENDING_EVIDENCE"},
        {"$set": {**submitted, "pending_reasons": reasons, "updated_at": _now(), "updated_by": actor_id}, "$inc": {"version": 1}},
    )
    if not updated.matched_count:
        _error("CONFIRMATION_CHANGED", "This supplier confirmation changed. Refresh and retry.", 409)
    return db.get_collection(EVENTS).find_one({"_id": event["_id"]})


def _prepare_allocations(sources, submitted):
    by_key = {s["source_key"]: s for s in sources}
    total = 0
    rows = []
    for allocation in submitted["allocations"]:
        source = by_key.get(allocation["source_key"])
        if source is None:
            _error("ORIGINAL_SOURCE_CHANGED", "The selected original bill evidence changed. Refresh the credit preview.", 409)
        caps = {"taxable": source["taxable_cap_paise"], **source["tax_cap_paise"]}
        if any(allocation[f"{head}_paise"] > cap for head, cap in caps.items()):
            _error("ORIGINAL_AMOUNT_EXCEEDED", "A credited amount exceeds the original returned goods' verified value or tax.")
        supplier_amount = allocation["taxable_paise"] + (0 if source["reverse_charge"] else sum(allocation[f"{h}_paise"] for h in HEADS))
        total += supplier_amount
        rows.append({**allocation, "bill_id": source["bill_id"], "bill_line_index": source["bill_line_index"],
                     "store_id": source["store_id"], "recipient_entity_id": source["recipient_entity_id"],
                     "recipient_gstin": source["recipient_gstin"], "vendor_gstin": source.get("vendor_gstin"),
                     "reverse_charge": source["reverse_charge"], "claims_credit": source["claims_credit"],
                     "supplier_amount_paise": supplier_amount,
                     "itc_reversal_paise": {h: allocation[f"{h}_paise"] if source["claims_credit"] else 0 for h in HEADS}})
    if total != submitted["received_paise"] or (not rows and submitted["received_paise"]):
        _error("ALLOCATION_TOTAL_MISMATCH", "Allocate the complete actual supplier credit amount before posting it.")
    return rows


def _return_fence(db, event, return_doc):
    if event.get("return_kind") == "RMA":
        from .rma_supplier_credit import fence
        return fence(db, event, return_doc)
    query = {"return_id": event["return_id"], "$or": [
        {"credit_confirmation_fence": {"$exists": False}}, {"credit_confirmation_fence": event["token"]},
    ]}
    if event.get("fenced_token"):
        query["$or"].append({"credit_confirmation_fence": event["fenced_token"]})
    for key in ("status", "items", "stock_ids", "grn_id", "total_value", "return_type", "receipt_rejection"):
        query[key] = return_doc[key] if key in return_doc else {"$exists": False}
    result = db.get_collection("vendor_returns").update_one(query, {"$set": {"credit_confirmation_fence": event["token"]}})
    if not result.matched_count:
        _error("RETURN_CHANGED", "The return changed or another confirmation is posting. Refresh and retry.", 409)


def _live_reservations(db, entries):
    kept = []
    for entry in entries:
        event = db.get_collection(EVENTS).find_one({"_id": entry["confirmation_id"]})
        if event is None:
            _error("UNVERIFIABLE_RESERVATION", "An earlier supplier credit needs technical reconciliation before more credit can post.", 503)
        if event.get("posting_status") in {"PREPARING", "POSTED"} and event.get("token") == entry["token"]:
            kept.append(entry)
    return kept


def _check_capacity(entries):
    lines, sources, physical = {}, {}, {}
    paid_by_line = defaultdict(lambda: defaultdict(int))
    paid_by_source = defaultdict(lambda: defaultdict(int))
    for entry in entries:
        for source in entry["sources"]:
            index = source["bill_line_index"]
            if index in lines and lines[index]["bill_line_fingerprint"] != source["bill_line_fingerprint"]:
                _error("ORIGINAL_SOURCE_CHANGED", "An original bill used by an earlier credit has changed; reconciliation is required.", 409)
            lines[index] = source
            # Each source reserves its physical quantity once across successive
            # partial notes. The whole return's selection is frozen separately.
            owner = (entry.get("return_kind", "VENDOR_RETURN"), entry["return_id"])
            sources[source["source_key"]] = {"source": source, "return_id": owner}
            for stock_id in source["stock_ids"]:
                if stock_id in physical and physical[stock_id] != owner:
                    _error("RETURNED_UNIT_ALREADY_CREDITED", "A physical unit is already reserved by another supplier return.")
                physical[stock_id] = owner
        for allocation in entry["allocations"]:
            for head in ("taxable", *HEADS):
                paid_by_line[allocation["bill_line_index"]][head] += allocation[f"{head}_paise"]
                paid_by_source[allocation["source_key"]][head] += allocation[f"{head}_paise"]
    for index, source in lines.items():
        quantity = sum(s["source"]["quantity"] for s in sources.values() if s["source"]["bill_line_index"] == index)
        if quantity > source["original_quantity"]:
            _error("ORIGINAL_QUANTITY_EXCEEDED", "Supplier returns already reserve the original billed quantity.")
        caps = {"taxable": source["original_taxable_paise"], **source["original_tax_paise"]}
        if any(paid_by_line[index][h] > amount for h, amount in caps.items()):
            _error("ORIGINAL_AMOUNT_EXCEEDED", "Supplier credits already consume the original bill value or tax.")
    for key, value in sources.items():
        source = value["source"]
        caps = {"taxable": source["taxable_cap_paise"], **source["tax_cap_paise"]}
        if any(paid_by_source[key][h] > amount for h, amount in caps.items()):
            _error("RETURN_AMOUNT_EXCEEDED", "Previous partial credits already consume this return's original value or tax.")


def _reserve_bill(db, event, bill_id):
    guards = db.get_collection(GUARDS)
    try:
        guards.update_one({"_id": bill_id}, {"$setOnInsert": {"version": 0, "reservations": []}}, upsert=True)
    except DuplicateKeyError:
        pass
    entry = {"confirmation_id": event["confirmation_id"], "return_id": event["return_id"], "token": event["token"],
             "return_kind": event.get("return_kind", "VENDOR_RETURN"),
             "sources": [s for s in event["sources"] if s["bill_id"] == bill_id],
             "allocations": [a for a in event["allocations"] if a["bill_id"] == bill_id]}
    for _ in range(12):
        state = guards.find_one({"_id": bill_id})
        kept = _live_reservations(db, state["reservations"])
        kept = [r for r in kept if r["confirmation_id"] != event["confirmation_id"]] + [entry]
        _check_capacity(kept)
        result = guards.update_one({"_id": bill_id, "version": state["version"]},
                                   {"$set": {"reservations": kept}, "$inc": {"version": 1}})
        if result.matched_count:
            return
    _error("ORIGINAL_BILL_BUSY", "Another supplier credit is reserving this bill. Retry the same request.", 409)


def _reserve_physical(db, event):
    """Receipt capacity and a named unit cannot be reused through another bill."""
    collection = db.get_collection("supplier_credit_origin_guards")
    groups = defaultdict(list)
    for source in event["sources"]:
        groups[(source["grn_id"], source["product_id"], source.get("receipt_disposition", "ACCEPTED"))].append(source)
    for (grn_id, product_id, disposition), sources in sorted(groups.items()):
        receipt = db.get_collection("grns").find_one({"grn_id": grn_id}) or {}
        quantity_key = "rejected_qty" if disposition == "REJECTED" else "accepted_qty"
        limit = sum(int(line.get(quantity_key) or 0) for line in receipt.get("items") or [] if line.get("product_id") == product_id)
        key = _hash([grn_id, product_id, "REJECTED"] if disposition == "REJECTED" else [grn_id, product_id])
        try:
            collection.update_one({"_id": key}, {"$setOnInsert": {"version": 0, "reservations": []}}, upsert=True)
        except DuplicateKeyError:
            pass
        entry = {"confirmation_id": event["confirmation_id"], "return_id": event["return_id"], "token": event["token"],
                 "return_kind": event.get("return_kind", "VENDOR_RETURN"),
                 "quantity": sum(s["quantity"] for s in sources), "stock_ids": [u for s in sources for u in s["stock_ids"]]}
        for _ in range(12):
            state = collection.find_one({"_id": key})
            kept = _live_reservations(db, state["reservations"])
            kept = [r for r in kept if r["confirmation_id"] != event["confirmation_id"]] + [entry]
            quantities, units = {}, {}
            for reservation in kept:
                owner = (reservation.get("return_kind", "VENDOR_RETURN"), reservation["return_id"])
                quantities[owner] = max(quantities.get(owner, 0), reservation["quantity"])
                for unit_id in reservation["stock_ids"]:
                    if unit_id in units and units[unit_id] != owner:
                        _error("RETURNED_UNIT_ALREADY_CREDITED", "A physical unit is already reserved by another supplier return.")
                    units[unit_id] = owner
            if sum(quantities.values()) > limit:
                _error("RECEIPT_QUANTITY_EXCEEDED", f"Supplier returns already reserve this receipt's {disposition.lower()} quantity.")
            saved = collection.update_one({"_id": key, "version": state["version"]},
                                          {"$set": {"reservations": kept}, "$inc": {"version": 1}})
            if saved.matched_count:
                break
        else:
            _error("ORIGINAL_RECEIPT_BUSY", "Another supplier return is reserving this receipt. Retry unchanged.", 409)


def _fence_failed(db, event, error):
    """The old worker loses POST permission BEFORE capacity can be reclaimed."""
    changed = db.get_collection(EVENTS).update_one(
        {"_id": event["_id"], "posting_status": "PREPARING", "token": event["token"]},
        {"$set": {"posting_status": "PENDING_EVIDENCE", "fenced_token": event["token"],
                  "pending_reasons": [{"code": error.code, "message": str(error)}]},
         "$unset": {"token": ""}, "$inc": {"version": 1}},
    )
    if changed.matched_count:
        if event.get("return_kind") == "RMA":
            from .rma_supplier_credit import release
            release(db, event)
            return
        db.get_collection("vendor_returns").update_one(
            {"return_id": event["return_id"], "credit_confirmation_fence": event["token"]},
            {"$unset": {"credit_confirmation_fence": ""}},
        )
    # Reservation cleanup is lazy: _live_reservations sees the event's missing
    # token. If any write result is uncertain, leave it occupied for retry.


def _finish_return(db, event):
    if event.get("return_kind") == "RMA":
        from .rma_supplier_credit import finish
        return finish(db, event)
    current = db.get_collection("vendor_returns").find_one({"return_id": event["return_id"]})
    preview = credit_preview(db, current, selections=event["source_selections"])
    full = preview["remaining_amount"] is not None and preview["remaining_amount"] <= 0
    numbers = list(dict.fromkeys(c["credit_note_number"] for c in preview["confirmations"] if c["posting_status"] == "POSTED"))
    db.get_collection("vendor_returns").update_one(
        {"return_id": event["return_id"], "credit_confirmation_fence": event["token"]},
        {"$set": {"status": "credit_issued" if full else "received_by_vendor",
                  "credit_note_number": ", ".join(numbers), "credit_note_amount": preview["posted_amount"],
                  "confirmed_credit_amount": preview["posted_amount"],
                  "credit_confirmation_locked": True,
                  "updated_at": _now()}, "$unset": {"credit_confirmation_fence": ""}},
    )


def record_supplier_confirmation(db, return_doc, payload, *, actor_id, allowed_store_ids=None):
    """Record a whole actual supplier note, or preserve it wholly pending.

    Route adapters must apply Accounts RBAC/shop access and prevent ordinary
    status/pricing writes while credit_confirmation_fence is present.
    """
    submitted = _input(payload)
    event = _event(db, return_doc, submitted, actor_id)
    if event["posting_status"] == "POSTED":
        _finish_return(db, event)
        return event, True
    if return_doc.get("status") != "received_by_vendor" or return_doc.get("return_type") != "credit_note":
        _error("RETURN_NOT_READY_FOR_CREDIT", "Confirm supplier credit only after the credit return is received by the supplier.", 409)
    if event["posting_status"] != "PREPARING":
        try:
            sources = resolve_credit_evidence(db, return_doc, selections=submitted["source_selections"], allowed_store_ids=allowed_store_ids)
        except ReturnProvenanceError as exc:
            _identity(db, event, None)
            return _pending_event(db, event, submitted, [{"code": exc.code, "message": str(exc)}], actor_id), False
        registration = _hash([sources[0]["recipient_entity_id"], sources[0]["recipient_gstin"]])
        _identity(db, event, registration)
        for previous in db.get_collection(EVENTS).find({**return_event_query(event), "posting_status": "POSTED"}):
            if {s["source_key"] for s in previous["sources"]} != {s["source_key"] for s in sources}:
                _error("RETURN_ORIGIN_CHANGED", "A partly confirmed return cannot change its original bill allocation.", 409)
        allocations = _prepare_allocations(sources, submitted)
        _locked(db, submitted["credit_note_date"])
        token = uuid4().hex
        updated = db.get_collection(EVENTS).update_one(
            {"_id": event["_id"], "version": event["version"], "posting_status": "PENDING_EVIDENCE"},
            {"$set": {**submitted, "allocations": allocations, "sources": sources, "token": token,
                      "submitted_source_selections": submitted["source_selections"],
                      "source_selections": [{k: s[k] for k in ("return_line_index", "grn_id", "bill_id", "bill_line_index", "quantity", "stock_ids")} for s in sources],
                      "registration_key": registration, "posting_status": "PREPARING", "pending_reasons": [],
                      "updated_by": actor_id, "updated_at": _now()}, "$inc": {"version": 1}},
        )
        if not updated.matched_count:
            _error("CONFIRMATION_CHANGED", "This supplier confirmation changed. Retry the same request.", 409)
        event = db.get_collection(EVENTS).find_one({"_id": event["_id"]})
    try:
        _return_fence(db, event, return_doc)
        # A PREPARING retry may have waited while another partial note posted.
        # Recheck the now-frozen origin while holding this return's CAS fence.
        for previous in db.get_collection(EVENTS).find({**return_event_query(event), "posting_status": "POSTED"}):
            if {s["source_key"] for s in previous["sources"]} != {s["source_key"] for s in event["sources"]}:
                _error("RETURN_ORIGIN_CHANGED", "A partly confirmed return cannot change its original bill allocation.", 409)
        _reserve_physical(db, event)
        for bill_id in sorted({s["bill_id"] for s in event["sources"]}):
            _reserve_bill(db, event, bill_id)
        fresh = resolve_credit_evidence(db, return_doc, selections=event["source_selections"], allowed_store_ids=allowed_store_ids)
        if {s["source_key"] for s in fresh} != {s["source_key"] for s in event["sources"]}:
            _error("ORIGINAL_SOURCE_CHANGED", "Original bill evidence changed during confirmation. Refresh the preview.", 409)
        _locked(db, event["credit_note_date"])
        saved = db.get_collection(EVENTS).update_one(
            {"_id": event["_id"], "posting_status": "PREPARING", "token": event["token"]},
            {"$set": {"posting_status": "POSTED", "posted_at": _now(), "posted_by": actor_id}, "$inc": {"version": 1}},
        )
        if not saved.matched_count:
            current = db.get_collection(EVENTS).find_one({"_id": event["_id"]})
            if current.get("posting_status") != "POSTED":
                _error("CONFIRMATION_FENCED", "This confirmation was fenced before posting. Refresh and retry.", 409)
        event = db.get_collection(EVENTS).find_one({"_id": event["_id"]})
        _finish_return(db, event)
        return event, False
    except ReturnProvenanceError as exc:
        failure = SupplierCreditError(exc.code, str(exc), 409)
        _fence_failed(db, event, failure)
        raise failure from exc
    except SupplierCreditError as exc:
        _fence_failed(db, event, exc)
        current = db.get_collection(EVENTS).find_one({"_id": event["_id"]})
        if current.get("posting_status") == "POSTED":
            _finish_return(db, current)
            return current, True
        raise
    except Exception as exc:
        # Unknown result: never clear its token/capacity. Resolve a lost POSTED
        # reply if readable; otherwise the identical UUID resumes PREPARING.
        try:
            current = db.get_collection(EVENTS).find_one({"_id": event["_id"]})
            if current and current.get("posting_status") == "POSTED":
                _finish_return(db, current)
                return current, True
        except Exception:
            pass
        raise SupplierCreditError("CONFIRMATION_UNCERTAIN", "The confirmation result could not be verified. Retry the same request unchanged.", 503) from exc
