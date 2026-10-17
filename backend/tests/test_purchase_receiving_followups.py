"""Receiving offers every relevant order and preserves quantity/audit rules."""

from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException

from api.routers import purchase_recon
from api.routers.vendors import grn_create, po_detail, purchase_orders
from database.repositories.vendor_repository import PurchaseOrderRepository
from strict_fakes import StrictCollection
from test_off_catalogue_items_release import (
    MANAGER, STORE, _receive_some, _run, vd, world,
)


@pytest.mark.parametrize("delivery_challan", [False, True])
def test_manager_receives_extra_after_full_receipt_with_unit_audit(world, delivery_challan):
    item = world.catalogue_frame("Carrera", "EXTRA 1", "C1", "52", 5000, 4500, 2000)
    pid = item["product_id"]
    po = world.raise_po([{"product_id": pid, "product_name": "Carrera EXTRA 1", "quantity": 2, "unit_price": 2000}])
    world.receive_everything(po)
    assert world.db.purchase_orders.find_one({"po_id": po["po_id"]})["status"] == "RECEIVED"

    if delivery_challan:
        receipt = _run(vd.create_grn(vd.GRNCreate(
            po_id=po["po_id"], grn_subtype="DELIVERY_CHALLAN", dc_number="EXTRA-DC",
            dc_date="2026-10-18", items=[vd.GRNItemCreate(
                product_id=pid, received_qty=1, accepted_qty=1, tallied=True,
            )],
        ), MANAGER))
        accepted = _run(vd.accept_grn(receipt["grn_id"], MANAGER))
    else:
        receipt, accepted = _receive_some(world, po, {pid: 1}, "EXTRA/AFTER/FULL")
    assert accepted["units_added"] == 1
    assert len(world.units(pid)) == 3
    audit = list(world.db.stock_audit.find({"grn_id": receipt["grn_id"]}))
    assert len(audit) == 1
    assert audit[0]["po_id"] == po["po_id"] and audit[0]["by_user"] == MANAGER["user_id"]
    assert audit[0]["stock_id"] in {unit["stock_id"] for unit in world.units(pid)}
    assert any("Over-delivery" in task["title"] for task in world.db.tasks.find({}))


def test_extra_on_uncancelled_product_is_allowed_when_other_line_closed_by_cancel(world):
    item = world.catalogue_frame("Carrera", "EXTRA 2", "C1", "52", 5000, 4500, 2000)
    pid = item["product_id"]
    po = world.raise_po([{"product_id": pid, "product_name": "Carrera EXTRA 2", "quantity": 1, "unit_price": 2000}])
    world.receive_everything(po)
    po = world.db.purchase_orders.find_one({"po_id": po["po_id"]})
    po["items"].append({"product_id": "CANCELLED-ITEM", "quantity": 0,
                        "cancelled_qty": 2, "line_status": "CANCELLED"})
    world.db.purchase_orders.update_one({"po_id": po["po_id"]}, {"$set": {"items": po["items"]}})
    _, accepted = _receive_some(world, po, {pid: 1}, "EXTRA/UNCANCELLED")
    assert accepted["units_added"] == 1 and len(world.units(pid)) == 2
    with pytest.raises(HTTPException) as refused:
        _receive_some(world, po, {"CANCELLED-ITEM": 1}, "EXTRA/CANCELLED")
    assert refused.value.status_code == 400
    assert not world.units("CANCELLED-ITEM")


def test_receipt_create_returns_503_when_the_order_cannot_be_read(world, monkeypatch):
    class BrokenCollection:
        def find_one(self, _query):
            raise RuntimeError("read unavailable")

    repo = PurchaseOrderRepository(BrokenCollection())
    monkeypatch.setattr(grn_create, "get_purchase_order_repository", lambda: repo)
    with pytest.raises(HTTPException) as refused:
        _receive_some(world, {"po_id": "EXISTING"}, {"P1": 1}, "READ/FAIL")
    assert refused.value.status_code == 503
    assert not list(world.db.grns.find({}))


def test_order_fallback_removes_raw_object_id():
    raw = {"_id": ObjectId(), "po_id": "P1", "status": "SENT"}
    repo = SimpleNamespace(find_by_id=lambda _id: None,
                           collection=SimpleNamespace(find_one=lambda _query: raw))
    assert po_detail._order_or_503(repo, "P1") == {"po_id": "P1", "status": "SENT"}
    assert "_id" in raw


@pytest.mark.parametrize("accounts", [False, True])
def test_receiving_candidates_include_old_sent_cancelled_orders_beyond_first_page(monkeypatch, accounts):
    rows = [{"po_id": f"D-{i}", "status": "DRAFT", "delivery_store_id": STORE,
             "created_at": "2026-10-18"} for i in range(60)]
    for pid, status, sent, date in [
        ("OLD-SENT-CANCEL", "CANCELLED", "2026-08-01", "2026-08-01"),
        ("NEVER-SENT-CANCEL", "CANCELLED", None, "2026-10-17"),
        ("OPEN", "SENT", "2026-10-15", "2026-10-15"),
        ("DONE", "RECEIVED", "2026-10-14", "2026-10-14"),
    ]:
        rows.append({"po_id": pid, "status": status, "sent_at": sent,
                     "delivery_store_id": STORE, "created_at": date,
                     "total_amount": 1000,
                     "items": [{"product_id": "P1", "quantity": 1, "unit_price": 1000}]})
    repo = PurchaseOrderRepository(StrictCollection("purchase_orders", rows))
    monkeypatch.setattr(purchase_orders, "get_purchase_order_repository", lambda: repo)
    out = _run(purchase_orders.list_pos(
        vendor_id=None, status=None, store_id=STORE, skip=0, limit=50,
        receipt_candidates=True, current_user={**MANAGER, "roles": ["ACCOUNTANT"]} if accounts else MANAGER,
    ))["purchase_orders"]
    assert [po["po_id"] for po in out] == ["OPEN", "DONE", "OLD-SENT-CANCEL"]
    assert out[1]["extra_receipt_allowed"] is True
    for po in out:
        assert ("unit_price" in po["items"][0]) is accounts
        assert ("total_amount" in po) is accounts
        assert po["items"][0]["quantity"] == 1


def test_reconciliation_uses_all_and_only_open_receiving_statuses(world):
    statuses = ["SENT", "ACKNOWLEDGED", "PARTIAL", "PARTIALLY_RECEIVED", "DRAFT", "RECEIVED", "CANCELLED"]
    for status in statuses:
        world.db.purchase_orders.insert_one({"po_id": status, "status": status,
            "delivery_store_id": STORE, "created_at": "2026-10-18",
            "items": [{"product_id": "P1", "quantity": 2, "received_qty": 0}]})
    got = purchase_recon._stock_yet_to_receive(world.db, STORE)
    assert {po["po_id"] for po in got} == set(statuses[:4])
