"""A08 physical rejection custody and nonfinancial draft handoff, no live DB."""
from datetime import datetime, timedelta
import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from tests.test_receipt_without_po import (
    world, MANAGER, ACCOUNTANT, ADMIN, STORE, FRAME, DEALER, _seed_receipt, _walk_in_body,
)
from api.routers import vendor_returns as vr
from api.routers import rtv_debit_notes as dn
from api.routers import vendors as vd
from api.routers.vendors import grn_rejections as rejects
from api.routers.vendors.grn_accept_lock import _receipt_units, _receipt_price_paid
from api.services.rtv_debit_note import DebitNoteEngine
from api.services.cost_mask import mask_vendor_return


@pytest.fixture
def app_world(world, monkeypatch):
    monkeypatch.setattr(vr, "_get_db", lambda: world["db"])
    monkeypatch.setattr(dn, "_get_db", lambda: world["db"])
    http = world["as_"](MANAGER)
    http.app.include_router(vr.router, prefix="/vendor-returns")
    http.app.include_router(dn.router, prefix="/rtv-debit-notes")
    return world


def line(accepted=2, rejected=1, disposition="credit_note", product_id=FRAME):
    return {"product_id": product_id, "received_qty": accepted + rejected,
            "accepted_qty": accepted, "rejected_qty": rejected,
            "rejection_reason": "Damaged packaging and scratched lens",
            "rejection_disposition": disposition, "tallied": True}


def receipt(world, items=None, **extra):
    items = [line()] if items is None else items
    return _seed_receipt(
        world["db"], grn_id="GRN-A08", subtype=extra.pop("subtype", "NO_PO"),
        status="PENDING", items=items, total_received=sum(i["received_qty"] for i in items),
        total_accepted=sum(i["accepted_qty"] for i in items),
        total_rejected=sum(i["rejected_qty"] for i in items), accepted_at=None, **extra,
    )


def post(world):
    return world["as_"](MANAGER).post("/vendors/grn/GRN-A08/accept")


@pytest.mark.parametrize("subtype", ["STANDARD", "DELIVERY_CHALLAN", "NO_PO"])
def test_one_custody_unit_per_reject_without_changing_accepted_counts(app_world, subtype):
    db = app_world["db"]
    grn = receipt(app_world, subtype=subtype)
    response = post(app_world)
    assert response.status_code == 200, response.text
    assert response.json()["units_added"] == 2
    assert response.json()["quarantined_units"] == 1
    assert len(response.json()["stock_ids"]) == 2
    assert db.stock_units.count_documents({"status": "AVAILABLE"}) == 2
    rejected = db.stock_units.find_one({"status": "QUARANTINED"})
    assert rejected["receipt_disposition"] == "REJECTED"
    assert rejected["line_unit_seq"] == -1
    assert rejected["grn_id"] == grn["grn_id"]
    assert rejected["quarantine_by"] == MANAGER["user_id"]
    assert "unit_cost" not in rejected and "cost_price" not in rejected
    draft = db.vendor_returns.find_one({})
    assert draft["return_id"] == rejected["rtv_vendor_id"]
    assert draft["stock_ids"] == [rejected["stock_id"]]
    assert draft["status"] == "draft" and draft["return_type"] == "credit_note"
    assert draft["items"][0]["quantity"] == 1 and draft["items"][0]["unit_price"] is None
    assert draft["total_value"] is None and draft["debit_note_draft_status"] == "DRAFT"
    assert "debit_note_number" not in draft
    assert db.debit_notes.count_documents({}) == db.vendor_debit_notes.count_documents({}) == 0
    assert db.counters.count_documents({"_id": {"$regex": "^debit_note:"}}) == 0
    assert _receipt_units(vd.get_stock_repository(), grn) == 2
    assert _receipt_units(vd.get_stock_repository(), grn, include_rejected=True) == 3
    labels = app_world["as_"](MANAGER).get("/inventory/units", params={"grn_id": grn["grn_id"]}).json()
    assert labels["total"] == len(labels["units"]) == 2
    assert sum(u["in_shop"] for u in labels["units"]) == 2
    assert rejected["stock_id"] not in [u["stock_id"] for u in labels["units"] if u["in_shop"]]


def test_mixed_dispositions_and_duplicate_product_lines_keep_separate_origins(app_world):
    db = app_world["db"]
    receipt(app_world, [line(1, 2, "credit_note"), line(1, 1, "replacement"), line(0, 1, None)])
    response = post(app_world)
    assert response.status_code == 200, response.text
    drafts = list(db.vendor_returns.find({}).sort("grn_line_index", 1))
    assert [d["return_type"] for d in drafts] == ["credit_note", "replacement", None]
    assert len(set(d["return_id"] for d in drafts)) == 3
    assert [d["items"][0]["quantity"] for d in drafts] == [2, 1, 1]
    assert db.stock_units.count_documents({"status": "AVAILABLE"}) == 2
    assert db.stock_units.count_documents({"status": "QUARANTINED"}) == 4


def test_draft_failure_retries_without_double_mint_or_resetting_accepted_stock(app_world, monkeypatch):
    db = app_world["db"]
    grn = receipt(app_world)
    real = db.vendor_returns.insert_one
    monkeypatch.setattr(db.vendor_returns, "insert_one", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("offline")))
    failed = post(app_world)
    assert failed.status_code == 503
    assert db.grns.find_one({"grn_id": grn["grn_id"]})["status"] == "PENDING"
    assert db.stock_units.count_documents({}) == 3
    # Accepted stock can already have moved; origin, not current source/status, counts it.
    db.stock_units.update_one({"status": "AVAILABLE"}, {"$set": {
        "source_type": "TRANSFER", "source_id": "MOVE-1", "store_id": "S-OTHER", "status": "SOLD"}})
    monkeypatch.setattr(db.vendor_returns, "insert_one", real)
    retried = post(app_world)
    assert retried.status_code == 200, retried.text
    assert retried.json()["units_added"] == 0
    assert retried.json()["quarantined_units"] == 1  # Total custody, not newly minted pieces.
    assert db.stock_units.count_documents({}) == 3
    assert db.vendor_returns.count_documents({}) == 1


def test_all_rejected_custody_blocks_void_and_lift_even_without_catalogue_or_cost(app_world, monkeypatch):
    db = app_world["db"]
    grn = receipt(app_world, [line(0, 1, None, "NOT-CATALOGUED")])
    real = db.vendor_returns.insert_one
    monkeypatch.setattr(db.vendor_returns, "insert_one", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("offline")))
    assert post(app_world).status_code == 503
    assert _receipt_units(vd.get_stock_repository(), grn) == 0
    assert _receipt_units(vd.get_stock_repository(), grn, include_rejected=True) == 1
    refused = app_world["as_"](MANAGER).post("/vendors/grn/GRN-A08/void", json={"reason": "Counted incorrectly"})
    assert refused.status_code == 409, refused.text
    monkeypatch.setattr(db.vendor_returns, "insert_one", real)
    assert post(app_world).status_code == 200
    unit = db.stock_units.find_one({})
    lifted = app_world["as_"](MANAGER).patch(
        f"/inventory/stock/{unit['stock_id']}/lift-quarantine", json={"lift_reason": "Looks good now"})
    assert lifted.status_code == 409 and lifted.json()["detail"]["code"] == "receipt_rejected"
    assert db.stock_units.find_one({})["status"] == "QUARANTINED"
    assert db.products.find_one({"product_id": "NOT-CATALOGUED"}) is None


def test_accepted_then_quarantined_still_counts_and_rejected_cost_never_becomes_last_paid(app_world):
    db = app_world["db"]
    grn = receipt(app_world)
    assert post(app_world).status_code == 200
    db.stock_units.update_many({"status": "AVAILABLE"}, {"$set": {"status": "QUARANTINED", "unit_cost": 111.0}})
    db.stock_units.update_one({"receipt_disposition": "REJECTED"}, {"$set": {"unit_cost": 999.0}})
    assert _receipt_units(vd.get_stock_repository(), grn) == 2
    assert _receipt_price_paid(vd.get_stock_repository(), grn, FRAME, db) == 111.0


def test_draft_confirmation_requires_choice_and_prices_remain_accounts_only(app_world):
    db = app_world["db"]
    receipt(app_world, [line(0, 1, None)])
    assert post(app_world).status_code == 200
    draft = db.vendor_returns.find_one({})
    rid = draft["return_id"]
    manager = app_world["as_"](MANAGER)
    refused = manager.patch(f"/vendor-returns/{rid}/status", json={"status": "created"})
    assert refused.status_code == 422
    assert manager.put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [100]}).status_code == 403
    confirmed = manager.patch(f"/vendor-returns/{rid}/status", json={"status": "created", "return_type": "replacement"})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["return"]["return_type"] == "replacement"
    assert "total_value" not in confirmed.json()["return"]
    assert "unit_price" not in confirmed.json()["return"]["items"][0]
    assert manager.patch(f"/vendor-returns/{rid}/status", json={"status": "approved", "return_type": "credit_note"}).status_code == 409
    assert app_world["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [100]}).status_code == 200


def test_draft_does_not_issue_or_change_payment_hold_then_existing_issue_is_idempotent(app_world):
    db = app_world["db"]
    receipt(app_world)
    assert post(app_world).status_code == 200
    rid = db.vendor_returns.find_one({})["return_id"]
    accounts = app_world["as_"](ACCOUNTANT)
    assert accounts.put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [100]}).status_code == 200
    failed = accounts.post("/rtv-debit-notes/issue", json={"rtv_id": rid})
    assert failed.status_code == 409, failed.text
    assert db.debit_notes.count_documents({}) == 0
    db.vendor_bills.insert_one({"bill_id": "BILL-A08", "grn_id": "GRN-A08"})
    assert vd._rejected_goods_hold(db, "BILL-A08") is not None
    assert app_world["as_"](MANAGER).patch(f"/vendor-returns/{rid}/status", json={"status": "created", "return_type": "credit_note"}).status_code == 200
    accounts = app_world["as_"](ACCOUNTANT)
    result = accounts.post("/rtv-debit-notes/issue", json={"rtv_id": rid})
    assert result.status_code == 201, result.text
    note_id = result.json()["debit_note"]["debit_note_id"]
    repeated = accounts.post("/rtv-debit-notes/issue", json={"rtv_id": rid})
    assert repeated.json()["idempotent"] is True
    assert repeated.json()["debit_note"]["debit_note_id"] == note_id
    assert db.vendor_returns.find_one({"return_id": rid})["debit_note_draft_status"] == "ISSUED"
    assert db.vendor_debit_notes.count_documents({}) == 0  # A01 remains separately scoped.


def test_new_receipt_keeps_line_choice_and_existing_unit_cannot_create_duplicate_return(app_world):
    body = _walk_in_body(app_world, items=[line(0, 1, "replacement")])
    http = app_world["as_"](MANAGER)
    made = http.post("/vendors/grn", json=body)
    assert made.status_code == 201, made.text
    gid = made.json()["grn_id"]
    assert app_world["db"].grns.find_one({"grn_id": gid})["items"][0]["rejection_disposition"] == "replacement"
    assert http.post(f"/vendors/grn/{gid}/accept").status_code == 200
    unit = app_world["db"].stock_units.find_one({})
    duplicate = http.post("/vendor-returns/", json={"vendor_id": DEALER, "vendor_name": "Dealer", "store_id": STORE,
        "return_type": "credit_note", "stock_ids": [unit["stock_id"]],
        "items": [{"product_id": FRAME, "product_name": "Frame", "quantity": 1, "reason": "damaged"}]})
    assert duplicate.status_code == 409, duplicate.text
    assert app_world["db"].vendor_returns.count_documents({}) == 1


def test_failed_draft_after_releasing_held_line_does_not_record_phantom_hold(app_world, monkeypatch):
    db = app_world["db"]
    receipt(app_world, [line(1, 1)])
    db.grns.update_one({"grn_id": "GRN-A08"}, {"$set": {
        "status": "PARTIALLY_ACCEPTED", "unresolved_lines": [{
            "product_id": FRAME, "accepted_qty": 1, "held_qty": 1, "reason": "incomplete_catalog"}]}})
    monkeypatch.setattr(db.vendor_returns, "insert_one", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("offline")))
    assert post(app_world).status_code == 503
    assert db.stock_units.count_documents({"status": "AVAILABLE"}) == 1
    assert db.grns.find_one({"grn_id": "GRN-A08"})["unresolved_lines"] == []


def test_lost_rejected_insert_reply_is_recovered_without_duplicate_piece(app_world, monkeypatch):
    db = app_world["db"]
    receipt(app_world, [line(0, 2)])
    real = db.stock_units.insert_one
    lost = []

    def insert_then_lose_reply(doc, *args, **kwargs):
        result = real(doc, *args, **kwargs)
        if doc.get("receipt_disposition") == "REJECTED" and not lost:
            lost.append(doc["stock_id"])
            raise RuntimeError("insert reply lost")
        return result

    monkeypatch.setattr(db.stock_units, "insert_one", insert_then_lose_reply)
    assert post(app_world).status_code == 503
    assert db.stock_units.count_documents({}) == 1
    assert db.vendor_returns.count_documents({}) == 0
    retried = post(app_world)
    assert retried.status_code == 200, retried.text
    assert retried.json()["units_added"] == 0 and retried.json()["quarantined_units"] == 2
    assert db.stock_units.count_documents({}) == 2
    assert db.vendor_returns.count_documents({}) == 1
    assert lost[0] in db.vendor_returns.find_one({})["stock_ids"]


def test_stolen_claim_stops_rejected_handoff_then_stale_takeover_repairs_it(app_world, monkeypatch):
    db = app_world["db"]
    receipt(app_world, [line(0, 3)])
    real = rejects._grn_accept_heartbeat_tick
    ticks = []

    def steal_on_second_tick(repo, gid, token, state):
        ticks.append(token)
        if len(ticks) == 2:
            db.grns.update_one({"grn_id": gid}, {"$set": {"accept_lock_token": "new-holder"}})
            state["at"] = datetime.now() - timedelta(minutes=1)
        return real(repo, gid, token, state)

    monkeypatch.setattr(rejects, "_grn_accept_heartbeat_tick", steal_on_second_tick)
    failed = post(app_world)
    assert failed.status_code == 409, failed.text
    assert db.stock_units.count_documents({}) == 1 and db.vendor_returns.count_documents({}) == 0
    assert db.grns.find_one({"grn_id": "GRN-A08"})["accept_lock_token"] == "new-holder"
    db.grns.update_one({"grn_id": "GRN-A08"}, {"$set": {
        "accept_lock_at": (datetime.now() - timedelta(days=1)).isoformat()}})
    repaired = post(app_world)
    assert repaired.status_code == 200, repaired.text
    assert db.stock_units.count_documents({}) == 3 and db.vendor_returns.count_documents({}) == 1


def test_first_draft_confirmation_wins_and_choice_is_audited(app_world, monkeypatch):
    db = app_world["db"]
    receipt(app_world, [line(0, 1, None)])
    assert post(app_world).status_code == 200
    rid = db.vendor_returns.find_one({})["return_id"]
    real = db.vendor_returns.update_one
    raced = []

    def concurrent_confirm(flt, update, *args, **kwargs):
        if flt.get("status") == "draft" and update.get("$set", {}).get("status") == "created" and not raced:
            raced.append(True)
            winner = app_world["as_"](MANAGER).patch(f"/vendor-returns/{rid}/status", json={
                "status": "created", "return_type": "replacement"})
            assert winner.status_code == 200, winner.text
            assert winner.json()["return"]["return_type"] == "replacement"
        return real(flt, update, *args, **kwargs)

    monkeypatch.setattr(db.vendor_returns, "update_one", concurrent_confirm)
    loser = app_world["as_"](MANAGER).patch(f"/vendor-returns/{rid}/status", json={
        "status": "created", "return_type": "credit_note"})
    assert loser.status_code == 409, loser.text
    saved = db.vendor_returns.find_one({"return_id": rid})
    assert saved["status"] == "created" and saved["return_type"] == "replacement"
    assert saved["status_history"][-1]["return_type"] == "replacement"
    assert len(saved["status_history"]) == 2


def test_draft_reads_and_confirmation_keep_shop_and_money_boundaries(app_world):
    db = app_world["db"]
    receipt(app_world, [line(0, 1)])
    assert post(app_world).status_code == 200
    rid = db.vendor_returns.find_one({})["return_id"]
    foreign = {**MANAGER, "store_ids": ["OTHER"], "active_store_id": "OTHER"}
    assert app_world["as_"](foreign).get(f"/vendor-returns/{rid}").status_code == 404
    assert app_world["as_"](foreign).patch(f"/vendor-returns/{rid}/status", json={
        "status": "created", "return_type": "credit_note"}).status_code == 404
    workshop = {**MANAGER, "roles": ["WORKSHOP_STAFF"]}
    assert app_world["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [123]}).status_code == 200
    visible = app_world["as_"](workshop).get(f"/vendor-returns/{rid}")
    assert visible.status_code == 200
    assert visible.json()["debit_note_draft_status"] == "DRAFT"
    assert "total_value" not in visible.json() and "unit_price" not in visible.json()["items"][0]
    assert app_world["as_"](workshop).patch(f"/vendor-returns/{rid}/status", json={
        "status": "created", "return_type": "credit_note"}).status_code == 403


def test_all_rejected_receipt_never_reopens_cancelled_order(app_world):
    db = app_world["db"]
    db.purchase_orders.insert_one({"po_id": "PO-A08", "status": "CANCELLED", "store_id": STORE,
        "vendor_id": DEALER, "items": [{"product_id": FRAME, "quantity": 2, "received_qty": 0}]})
    receipt(app_world, [line(0, 2)], subtype="STANDARD", po_id="PO-A08")
    result = post(app_world)
    assert result.status_code == 200, result.text
    assert result.json()["units_added"] == 0
    assert db.purchase_orders.find_one({"po_id": "PO-A08"})["status"] == "CANCELLED"
    assert db.purchase_orders.find_one({"po_id": "PO-A08"})["items"][0]["received_qty"] == 0
    assert db.stock_units.count_documents({"status": "QUARANTINED"}) == 2


def test_deterministic_insert_race_recovers_only_the_same_identity(app_world, monkeypatch):
    coll = app_world["db"].vendor_returns
    real = coll.insert_one

    def concurrent_insert(doc):
        real({**doc, "status": "created"})
        raise DuplicateKeyError("same identity already committed")

    monkeypatch.setattr(coll, "insert_one", concurrent_insert)
    saved, inserted = rejects._insert_once(coll, {"_id": "stable", "status": "draft"})
    assert not inserted and saved["status"] == "created"
    monkeypatch.setattr(coll, "insert_one", lambda _doc: (_ for _ in ()).throw(DuplicateKeyError("other unique index")))
    with pytest.raises(DuplicateKeyError):
        rejects._insert_once(coll, {"_id": "different"})


def test_retry_preserves_first_lines_confirmed_priced_draft(app_world, monkeypatch):
    db = app_world["db"]
    receipt(app_world, [line(0, 1, "replacement"), line(0, 1, "credit_note")])
    real = db.vendor_returns.insert_one

    def fail_second_line(doc, *args, **kwargs):
        if doc.get("grn_line_index") == 1:
            raise RuntimeError("second draft unavailable")
        return real(doc, *args, **kwargs)

    monkeypatch.setattr(db.vendor_returns, "insert_one", fail_second_line)
    assert post(app_world).status_code == 503
    rid = db.vendor_returns.find_one({})["return_id"]
    assert app_world["as_"](MANAGER).patch(f"/vendor-returns/{rid}/status", json={
        "status": "created", "return_type": "credit_note"}).status_code == 200
    assert app_world["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [321]}).status_code == 200
    before = db.vendor_returns.find_one({"return_id": rid})
    monkeypatch.setattr(db.vendor_returns, "insert_one", real)
    retried = post(app_world)
    assert retried.status_code == 200 and retried.json()["quarantined_units"] == 2
    assert db.vendor_returns.find_one({"return_id": rid}) == before
    assert db.vendor_returns.count_documents({}) == 2
    assert db.stock_units.count_documents({}) == 2


def test_dropping_unreceived_extra_keeps_rejected_custody_and_receipt(app_world):
    db = app_world["db"]
    db.purchase_orders.insert_one({"po_id": "PO-A08", "status": "RECEIVED", "store_id": STORE,
        "vendor_id": DEALER, "items": [{"product_id": FRAME, "quantity": 0, "received_qty": 0}]})
    grn = receipt(app_world, [line(1, 1)], subtype="STANDARD", po_id="PO-A08")
    held = vd._put_on_shelf(vd.get_grn_repository(), grn["grn_id"], grn, MANAGER, order_cap=True)
    assert held["grn_status"] == "PARTIALLY_ACCEPTED" and held["units_added"] == 0
    assert held["unresolved_lines"][0]["reason"] == "over_order"
    result = app_world["as_"](MANAGER).post("/vendors/grn/GRN-A08/drop-over-order")
    assert result.status_code == 200, result.text
    assert result.json()["dropped_units"] == 1 and result.json()["grn_status"] == "ACCEPTED"
    saved = db.grns.find_one({"grn_id": grn["grn_id"]})
    assert saved["total_accepted"] == 0 and saved["total_rejected"] == 1
    assert db.stock_units.count_documents({"status": "QUARANTINED"}) == 1
    assert db.stock_units.count_documents({"status": "AVAILABLE"}) == 0
    assert db.vendor_returns.count_documents({"status": "draft"}) == 1
