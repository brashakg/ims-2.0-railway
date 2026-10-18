"""No live Mongo: actual note recognition, caps, races and lost responses."""
from copy import deepcopy
from uuid import uuid4

import pytest

from api.services import supplier_credit_confirmation as service
from api.services.supplier_credit_evidence import SupplierCreditError, credit_preview, resolve_credit_evidence
from test_return_provenance import bill, db, line, receipt, selection, unit  # noqa: F401
from test_supplier_credit_evidence import ready  # noqa: F401


@pytest.fixture(autouse=True)
def _isolate_db():
    """No application startup or shared database in writer unit tests."""
    yield


@pytest.fixture
def return_doc(db, ready):
    ready.update(status="received_by_vendor", return_type="credit_note")
    db.vendor_returns.insert_one(deepcopy(ready))
    return ready


def payload(db, return_doc, amount=84, *, identifier=None, number="SUP-CN-1", base=80, cgst=2, sgst=2, igst=0):
    sources = resolve_credit_evidence(db, return_doc)
    return {"idempotency_key": identifier or str(uuid4()), "credit_note_number": number,
            "credit_note_date": "2026-10-18", "received_amount": amount,
            "source_selections": [], "allocations": [{"source_key": sources[0]["source_key"],
             "taxable": base, "cgst": cgst, "sgst": sgst, "igst": igst}]}


def post(db, return_doc, body):
    return service.record_supplier_confirmation(db, return_doc, body, actor_id="ACCOUNTS", allowed_store_ids={"S1"})


def test_actual_note_posts_one_canonical_fact_and_closes_fully_confirmed_return(db, return_doc):
    event, repeated = post(db, return_doc, payload(db, return_doc))
    assert repeated is False
    assert event["posting_status"] == "POSTED"
    assert event["received_paise"] == 8400
    assert event["allocations"][0]["itc_reversal_paise"] == {"cgst": 200, "sgst": 200, "igst": 0}
    assert db.vendor_returns.find_one({})["status"] == "credit_issued"
    assert db.vendor_debit_notes.count_documents({}) == 0
    assert db.debit_notes.count_documents({}) == 0


@pytest.mark.parametrize("rcm", [False, True])
def test_zero_commercial_credit_cannot_post_or_reserve_even_with_rcm_tax(db, return_doc, rcm):
    db.vendor_bills.update_one({}, {"$set": {"reverse_charge": rcm}})
    body = payload(db, return_doc, amount=0, base=0, cgst=2 if rcm else 0, sgst=2 if rcm else 0)
    with pytest.raises(SupplierCreditError) as caught:
        post(db, return_doc, body)
    assert caught.value.code == "POSITIVE_SUPPLIER_CREDIT_REQUIRED"
    assert db.supplier_credit_confirmations.count_documents({}) == 0
    assert db.supplier_credit_origin_guards.count_documents({}) == 0


def test_partial_notes_reserve_physical_quantity_once_and_only_confirm_received_amount(db, return_doc):
    first, _ = post(db, return_doc, payload(db, return_doc, 42, base=40, cgst=1, sgst=1))
    assert db.vendor_returns.find_one({})["status"] == "received_by_vendor"
    second, _ = post(db, db.vendor_returns.find_one({}), payload(db, return_doc, 42, number="SUP-CN-2", base=40, cgst=1, sgst=1))
    assert first["confirmation_id"] != second["confirmation_id"]
    assert db.vendor_returns.find_one({})["status"] == "credit_issued"
    assert db.supplier_credit_confirmations.count_documents({"posting_status": "POSTED"}) == 2


def test_preparing_retry_rechecks_origin_after_another_partial_note_posts(db, return_doc, monkeypatch):
    receipt(db, gid="G2")
    bill(db, bid="B2", gid="G2", bill_date="2026-01-01", taxable_amount=400, tax_amount=20, cgst_total=10, sgst_total=10, igst_total=0)
    def selected(gid, bid, number):
        choices = [selection(grn_id=gid, bill_id=bid, stock_ids=[])]
        source = resolve_credit_evidence(db, return_doc, selections=choices)[0]
        return {"idempotency_key": str(uuid4()), "credit_note_number": number,
                "credit_note_date": "2026-10-18", "received_amount": 42,
                "source_selections": choices,
                "allocations": [{"source_key": source["source_key"], "taxable": 40, "cgst": 1, "sgst": 1, "igst": 0}]}
    second = selected("G2", "B2", "SECOND")
    original = service._return_fence
    def interrupted(*_args):
        raise OSError("lost before claim")
    monkeypatch.setattr(service, "_return_fence", interrupted)
    with pytest.raises(SupplierCreditError, match="same request"):
        post(db, return_doc, second)
    monkeypatch.setattr(service, "_return_fence", original)
    post(db, return_doc, selected("G1", "B1", "FIRST"))
    with pytest.raises(SupplierCreditError) as caught:
        post(db, db.vendor_returns.find_one({}), second)
    assert caught.value.code == "RETURN_ORIGIN_CHANGED"
    assert db.supplier_credit_confirmations.count_documents({"posting_status": "POSTED"}) == 1


def test_identical_retry_returns_same_event_even_after_return_closed(db, return_doc):
    body = payload(db, return_doc)
    event, _ = post(db, return_doc, body)
    again, repeated = post(db, db.vendor_returns.find_one({}), body)
    assert repeated is True
    assert again["confirmation_id"] == event["confirmation_id"]
    assert db.supplier_credit_confirmations.count_documents({"posting_status": "POSTED"}) == 1


@pytest.mark.parametrize("change", [{"received_amount": 43}, {"credit_note_number": "CHANGED"},
                                    {"credit_note_date": "2026-10-17"}, {"notes": "changed after post"}])
def test_posted_payload_cannot_change(db, return_doc, change):
    body = payload(db, return_doc, 42, base=40, cgst=1, sgst=1)
    post(db, return_doc, body)
    with pytest.raises(SupplierCreditError) as caught:
        post(db, db.vendor_returns.find_one({}), {**body, **change})
    assert caught.value.status == 409


def test_new_uuid_cannot_duplicate_same_supplier_note_after_lost_response(db, return_doc):
    body = payload(db, return_doc, 42, base=40, cgst=1, sgst=1)
    post(db, return_doc, body)
    with pytest.raises(SupplierCreditError, match="already been recorded"):
        post(db, db.vendor_returns.find_one({}), {**body, "idempotency_key": str(uuid4()), "credit_note_number": "  sup-cn-1  "})
    assert db.supplier_credit_confirmations.count_documents({"posting_status": "POSTED"}) == 1


def test_unresolved_actual_note_stays_pending_without_reserving_or_posting(db, return_doc):
    body = payload(db, return_doc)
    db.vendor_bills.delete_many({})
    body["allocations"] = []
    event, _ = post(db, return_doc, body)
    assert event["posting_status"] == "PENDING_EVIDENCE"
    assert event["pending_reasons"][0]["code"] == "UNBOOKED_ORIGIN"
    assert db.supplier_credit_bill_guards.count_documents({}) == 0
    assert db.vendor_returns.find_one({})["status"] == "received_by_vendor"
    assert credit_preview(db, return_doc)["posted_amount"] == 0


def test_pending_same_uuid_can_complete_after_original_evidence_is_corrected(db, return_doc):
    body = payload(db, return_doc)
    db.vendor_bills.update_one({}, {"$set": {"vendor_gstin": None}})
    event, _ = post(db, return_doc, {**body, "allocations": []})
    assert event["posting_status"] == "PENDING_EVIDENCE"
    db.vendor_bills.update_one({}, {"$set": {"vendor_gstin": "20BBBBB0000B1Z5"}})
    posted, _ = post(db, return_doc, body)
    assert posted["posting_status"] == "POSTED"
    assert posted["confirmation_id"] == event["confirmation_id"]


def test_a_supplier_note_is_not_partly_posted_when_total_is_incomplete(db, return_doc):
    body = payload(db, return_doc, 84, base=40, cgst=1, sgst=1)
    with pytest.raises(SupplierCreditError, match="complete actual"):
        post(db, return_doc, body)
    assert db.supplier_credit_confirmations.count_documents({"posting_status": "POSTED"}) == 0


def test_individual_and_cumulative_source_head_caps(db, return_doc):
    return_doc["items"][0]["unit_price"] = 200
    db.vendor_returns.update_one({}, {"$set": {"items": return_doc["items"]}})
    post(db, return_doc, payload(db, return_doc, 52.5, base=50, cgst=1.25, sgst=1.25))
    with pytest.raises(SupplierCreditError, match="Previous partial"):
        post(db, db.vendor_returns.find_one({}), payload(db, return_doc, 63, number="SECOND", base=60, cgst=1.5, sgst=1.5))
    assert db.supplier_credit_confirmations.count_documents({"posting_status": "POSTED"}) == 1
    assert "credit_confirmation_fence" not in db.vendor_returns.find_one({})


def test_original_quantity_cannot_be_credited_by_another_return(db, return_doc):
    db.grns.update_one({}, {"$set": {"items.0.accepted_qty": 1}})
    db.vendor_bills.update_one({}, {"$set": {"lines.0.qty": 1}})
    body = payload(db, return_doc)
    post(db, return_doc, body)
    other = {**return_doc, "return_id": "R2"}
    db.vendor_returns.insert_one(deepcopy(other))
    with pytest.raises(SupplierCreditError, match="receipt's accepted quantity"):
        post(db, other, payload(db, other, number="SECOND"))


def test_rcm_supplier_credit_omits_self_paid_tax_while_reversal_keeps_it(db, return_doc):
    db.vendor_bills.update_one({}, {"$set": {"reverse_charge": True, "vendor_gstin": "URP"}})
    event, _ = post(db, return_doc, payload(db, return_doc, amount=80))
    assert event["allocations"][0]["supplier_amount_paise"] == 8000
    assert event["allocations"][0]["itc_reversal_paise"]["cgst"] == 200


def test_nonclaimed_original_never_creates_a_gst_reversal(db, return_doc):
    db.vendor_bills.update_one({}, {"$set": {"itc_eligible": False}})
    event, _ = post(db, return_doc, payload(db, return_doc))
    assert event["allocations"][0]["supplier_amount_paise"] == 8400
    assert event["allocations"][0]["itc_reversal_paise"] == {"cgst": 0, "sgst": 0, "igst": 0}


def test_locked_note_period_stops_before_reservation(db, return_doc):
    db.period_locks.insert_one({"year": 2026, "month": 10})
    with pytest.raises(SupplierCreditError) as caught:
        post(db, return_doc, payload(db, return_doc))
    assert caught.value.status == 423
    assert db.supplier_credit_bill_guards.count_documents({}) == 0


def test_lost_post_reply_is_recovered_without_duplicate_credit(db, return_doc, monkeypatch):
    collection = db.supplier_credit_confirmations
    original = collection.update_one
    raised = False
    def lose_reply(query, update, *args, **kwargs):
        nonlocal raised
        result = original(query, update, *args, **kwargs)
        if update.get("$set", {}).get("posting_status") == "POSTED" and not raised:
            raised = True
            raise OSError("reply lost after committed write")
        return result
    monkeypatch.setattr(collection, "update_one", lose_reply)
    event, recovered = post(db, return_doc, payload(db, return_doc))
    assert recovered is True
    assert event["posting_status"] == "POSTED"
    assert db.vendor_returns.find_one({})["status"] == "credit_issued"


def test_unknown_reservation_result_stays_occupied_and_same_request_resumes(db, return_doc, monkeypatch):
    original = service._reserve_bill
    def lose_reply(*args):
        original(*args)
        raise OSError("lost reservation reply")
    monkeypatch.setattr(service, "_reserve_bill", lose_reply)
    body = payload(db, return_doc)
    with pytest.raises(SupplierCreditError) as caught:
        post(db, return_doc, body)
    assert caught.value.status == 503
    pending = db.supplier_credit_confirmations.find_one({})
    assert pending["posting_status"] == "PREPARING"
    assert db.vendor_returns.find_one({})["credit_confirmation_fence"] == pending["token"]
    assert credit_preview(db, return_doc)["posted_amount"] == 0
    monkeypatch.setattr(service, "_reserve_bill", original)
    event, _ = post(db, db.vendor_returns.find_one({}), body)
    assert event["posting_status"] == "POSTED"
    assert db.supplier_credit_confirmations.count_documents({}) == 1


def test_reopened_preparing_confirmation_preserves_exact_submitted_auto_selection_payload(db, return_doc, monkeypatch):
    reserve = service._reserve_bill
    def uncertain(*args):
        reserve(*args)
        raise OSError("lost response")
    body = payload(db, return_doc)
    assert body["source_selections"] == []
    monkeypatch.setattr(service, "_reserve_bill", uncertain)
    with pytest.raises(SupplierCreditError):
        post(db, return_doc, body)
    preview = credit_preview(db, return_doc, confirmation_id=body["idempotency_key"])
    restored = preview["confirmation"]
    assert restored["source_selections"] == []
    assert preview["source_selections"][0]["bill_id"] == "B1"
    assert restored["retry_unchanged"] is True
    retry = restored["retry_payload"]
    monkeypatch.setattr(service, "_reserve_bill", reserve)
    event, _ = post(db, db.vendor_returns.find_one({}), retry)
    assert event["posting_status"] == "POSTED"


def test_source_change_during_reservation_fences_old_writer_before_releasing_capacity(db, return_doc, monkeypatch):
    original = service._reserve_bill
    def change_bill(*args):
        original(*args)
        db.vendor_bills.update_one({}, {"$set": {"bill_number": "changed meanwhile"}})
    monkeypatch.setattr(service, "_reserve_bill", change_bill)
    with pytest.raises(SupplierCreditError):
        post(db, return_doc, payload(db, return_doc))
    event = db.supplier_credit_confirmations.find_one({})
    assert event["posting_status"] == "PENDING_EVIDENCE"
    assert "token" not in event
    guard = db.supplier_credit_bill_guards.find_one({})
    assert service._live_reservations(db, guard["reservations"]) == []
    old_token = guard["reservations"][0]["token"]
    late = db.supplier_credit_confirmations.update_one({"_id": event["_id"], "posting_status": "PREPARING", "token": old_token},
                                                      {"$set": {"posting_status": "POSTED"}})
    assert late.matched_count == 0


def test_same_uuid_recovers_its_failed_cleanup_but_another_uuid_cannot(db, return_doc, monkeypatch):
    body = payload(db, return_doc)
    reserve = service._reserve_bill
    update = db.vendor_returns.update_one
    def refused(*_args):
        raise SupplierCreditError("KNOWN_REJECTION", "Temporary verified rejection")
    def lose_cleanup(query, values, *args, **kwargs):
        if "credit_confirmation_fence" in values.get("$unset", {}):
            raise OSError("lost cleanup before write")
        return update(query, values, *args, **kwargs)
    monkeypatch.setattr(service, "_reserve_bill", refused)
    monkeypatch.setattr(db.vendor_returns, "update_one", lose_cleanup)
    with pytest.raises((SupplierCreditError, OSError)):
        post(db, return_doc, body)
    orphan = db.vendor_returns.find_one({})["credit_confirmation_fence"]
    assert db.supplier_credit_confirmations.find_one({})["fenced_token"] == orphan
    monkeypatch.setattr(service, "_reserve_bill", reserve)
    monkeypatch.setattr(db.vendor_returns, "update_one", update)
    with pytest.raises(SupplierCreditError) as caught:
        post(db, db.vendor_returns.find_one({}), payload(db, return_doc, number="OTHER"))
    assert caught.value.code == "RETURN_CHANGED"
    event, _ = post(db, db.vendor_returns.find_one({}), body)
    assert event["posting_status"] == "POSTED"
    assert "credit_confirmation_fence" not in db.vendor_returns.find_one({})


def test_competing_worker_wins_bill_cas_and_loser_cannot_exceed_quantity(db, return_doc, monkeypatch):
    db.vendor_bills.update_one({}, {"$set": {"lines.0.qty": 1}})
    competitor = {**return_doc, "return_id": "R2"}
    db.vendor_returns.insert_one(deepcopy(competitor))
    competing_payload = payload(db, competitor, number="OTHER-NOTE")
    outer_payload = payload(db, return_doc)
    guards = db.supplier_credit_bill_guards
    update = guards.update_one
    interleaved = False
    def compete(query, values, *args, **kwargs):
        nonlocal interleaved
        if "version" in query and not interleaved:
            interleaved = True
            posted, _ = post(db, competitor, competing_payload)
            assert posted["posting_status"] == "POSTED"
        return update(query, values, *args, **kwargs)
    monkeypatch.setattr(guards, "update_one", compete)
    with pytest.raises(SupplierCreditError) as caught:
        post(db, return_doc, outer_payload)
    assert caught.value.code == "ORIGINAL_QUANTITY_EXCEEDED"
    assert db.supplier_credit_confirmations.count_documents({"posting_status": "POSTED"}) == 1
    assert db.supplier_credit_confirmations.find_one({"return_id": "R1"})["posting_status"] == "PENDING_EVIDENCE"
    assert "credit_confirmation_fence" not in db.vendor_returns.find_one({"return_id": "R1"})


def test_named_unit_cannot_be_claimed_by_another_return_even_with_other_bill(db, return_doc):
    unit(db)
    first = payload(db, return_doc)
    post(db, return_doc, first)
    bill(db, "B2", bill_date="2026-01-01", tax_amount=20, cgst_total=10, sgst_total=10, igst_total=0)
    db.stock_units.update_one({}, {"$set": {"rtv_vendor_id": "R2"}})
    other = {**return_doc, "return_id": "R2"}
    db.vendor_returns.insert_one(deepcopy(other))
    selected = [selection(bid="B2", stock_ids=["U1"])]
    source = resolve_credit_evidence(db, other, selections=selected)[0]
    body = {**first, "idempotency_key": str(uuid4()), "credit_note_number": "SECOND",
            "source_selections": selected,
            "allocations": [{**first["allocations"][0], "source_key": source["source_key"]}]}
    with pytest.raises(SupplierCreditError) as caught:
        post(db, other, body)
    assert caught.value.code == "RETURNED_UNIT_ALREADY_CREDITED"
    assert db.supplier_credit_confirmations.count_documents({"posting_status": "POSTED"}) == 1


def test_unlinked_receipt_quantity_cannot_be_reused_via_another_bill(db, return_doc):
    db.grns.update_one({}, {"$set": {"items.0.accepted_qty": 1}})
    first = payload(db, return_doc)
    post(db, return_doc, first)
    bill(db, "B2", bill_date="2026-01-01", tax_amount=20, cgst_total=10, sgst_total=10, igst_total=0)
    other = {**return_doc, "return_id": "R2"}
    db.vendor_returns.insert_one(deepcopy(other))
    selected = [selection(bid="B2")]
    source = resolve_credit_evidence(db, other, selections=selected)[0]
    body = {**first, "idempotency_key": str(uuid4()), "credit_note_number": "SECOND",
            "source_selections": selected,
            "allocations": [{**first["allocations"][0], "source_key": source["source_key"]}]}
    with pytest.raises(SupplierCreditError) as caught:
        post(db, other, body)
    assert caught.value.code == "RECEIPT_QUANTITY_EXCEEDED"


def test_partial_return_origin_stays_frozen_after_additional_receipt_becomes_available(db, return_doc):
    first = payload(db, return_doc, amount=42, base=40, cgst=1, sgst=1)
    post(db, return_doc, first)
    receipt(db, "G2")
    bill(db, "B2", gid="G2", bill_date="2026-01-01", tax_amount=20, cgst_total=10, sgst_total=10, igst_total=0)
    selected = [selection(gid="G2", bid="B2")]
    source = resolve_credit_evidence(db, return_doc, selections=selected)[0]
    body = {**first, "idempotency_key": str(uuid4()), "credit_note_number": "SECOND",
            "source_selections": selected,
            "allocations": [{**first["allocations"][0], "source_key": source["source_key"]}]}
    with pytest.raises(SupplierCreditError) as caught:
        post(db, db.vendor_returns.find_one({}), body)
    assert caught.value.code == "RETURN_ORIGIN_CHANGED"
    # A normal reopened preview still loads the frozen first origin.
    assert credit_preview(db, db.vendor_returns.find_one({}))["sources"][0]["bill_id"] == "B1"


def test_pending_note_duplicate_identity_is_not_exposed_as_second_confirmation(db, return_doc):
    body = payload(db, return_doc)
    db.vendor_bills.delete_many({})
    body["allocations"] = []
    original, _ = post(db, return_doc, body)
    with pytest.raises(SupplierCreditError) as caught:
        post(db, return_doc, {**body, "idempotency_key": str(uuid4())})
    assert caught.value.code == "SUPPLIER_NOTE_ALREADY_RECORDED"
    history = credit_preview(db, return_doc)["confirmations"]
    assert len(history) == 1
    assert history[0]["confirmation_id"] == original["confirmation_id"]


def test_period_lookup_failure_never_posts_or_releases_reserved_capacity(db, return_doc, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("period store unavailable")
    monkeypatch.setattr(db.period_locks, "find_one", unavailable)
    with pytest.raises(OSError, match="period store unavailable"):
        post(db, return_doc, payload(db, return_doc))
    assert db.supplier_credit_confirmations.count_documents({"posting_status": "POSTED"}) == 0
    assert db.supplier_credit_bill_guards.count_documents({}) == 0
