"""Unknown stock cost stays unknown through later supplier bill valuation."""
import copy

import mongomock
import pytest

from api.routers import purchase_invoices as pi
from api.routers.vendors.grn_accept import catalogue_write_done as finish_catalogue
from api.services import purchase_match as pm, stock_value, cost_mask
from database.repositories import ProductRepository, StockRepository
from tests.test_receipt_without_po import world as receiving_world


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient().a09
    db.products.insert_one({"product_id": "P", "cost_price": None, "status": "ACTIVE"})
    db.grns.insert_one({"grn_id": "G", "grn_number": "GRN-G", "store_id": "S1",
                        "vendor_id": "V", "status": "ACCEPTED", "grn_subtype": "STANDARD",
                        "items": [{"product_id": "P", "accepted_qty": 10}]})
    from api.routers.vendors import grn_accept
    monkeypatch.setattr(grn_accept, "catalogue_write_done", lambda *_a, **_k: None)
    monkeypatch.setattr(pi, "get_product_repository", lambda: ProductRepository(db.products))
    return db


def units(db, prefix, qty, *, cost=None, grn="OLD", pending=False, store="S1", status="AVAILABLE", **extra):
    for i in range(qty):
        doc = {"stock_id": f"{prefix}{i}", "product_id": "P", "store_id": store,
               "status": status, "quantity": 1, "grn_id": grn,
               "source_type": "GRN", "source_id": grn, **extra}
        if cost is not None:
            doc["unit_cost"] = cost
        if pending:
            doc["valuation_cost_pending"] = True
        db.stock_units.insert_one(doc)


def bill(db, qty=10, price=100, *, ident="B", **extra):
    doc = {"bill_id": ident, "invoice_id": ident, "doc_type": "PURCHASE_INVOICE",
           "status": "OUTSTANDING", "grn_id": "G", "vendor_id": "V",
           "lines": [{"product_id": "P", "qty": qty, "unit_price": price, "gst_rate": 5}], **extra}
    db.vendor_bills.insert_one(copy.deepcopy(doc))
    return doc


def apply(db, doc, method="MOVING_AVERAGE"):
    return pi._apply_valuation_trueup(db, doc, {"lines": doc["lines"]}, {"valuation_method": method})


def value(db):
    return stock_value.by_product(stock_value.shelf_units(
        StockRepository(db.stock_units), ProductRepository(db.products), None))["P"]


@pytest.mark.parametrize("unknown", [None, "", "bad", float("nan"), float("inf")])
def test_unknown_old_cost_is_not_explicit_zero(unknown):
    assert pm.moving_average_cost(10, unknown, 10, 100) is None
    assert pm.moving_average_cost(10, 0, 10, 100) == 50
    assert pm.moving_average_cost(0, unknown, 10, 100) == 100
    assert pm.moving_average_cost(10, 200, 10, 100) == 150


def test_missing_incoming_price_stays_unknown_in_trueup():
    got = pm.valuation_trueup_for_invoice(
        [{"product_id": "P", "qty": 10, "unit_price": None}],
        {"P": {"on_hand_qty": 0, "cost_price": None}})[0]
    assert got["receipt_unit_cost"] is None and got["new_cost"] is None


def test_unknown_old_layer_and_new_billed_layer_stay_separate(world):
    units(world, "OLD", 10)
    units(world, "NEW", 10, grn="G", pending=True)
    result = apply(world, bill(world))[0]
    assert result["old_cost"] is None and result["new_cost"] is None
    assert result["unknown_old_qty"] == 10
    product = world.products.find_one({})
    assert product["cost_price"] == 100 and product["moving_avg_cost"] is None
    assert product["cost_price_is_catalogue_only"] is True
    assert product["cost_valuation_pending"] is True and product["status"] == "ACTIVE"
    assert value(world) == {"units": 20, "cost": 1000, "uncosted_units": 10, "unit_cost": 100}
    assert world.stock_units.count_documents({"grn_id": "OLD", "unit_cost": {"$exists": False}}) == 10


def test_later_receipt_preserves_prior_known_invoice_average(world):
    # Receipt prices remain on already-known units after a bill true-up.
    # The next bill must blend the adjusted master, not undo it from units.
    units(world, "FIRST", 10, cost=100, grn="G")
    first = apply(world, bill(world, price=120))[0]
    assert first["new_cost"] == 120
    assert world.stock_units.find_one({"stock_id": "FIRST0"})["unit_cost"] == 100
    world.grns.insert_one({"grn_id": "G2", "grn_number": "GRN-G2", "store_id": "S1",
                           "vendor_id": "V", "status": "ACCEPTED", "grn_subtype": "STANDARD",
                           "items": [{"product_id": "P", "accepted_qty": 10}]})
    units(world, "SECOND", 10, cost=100, grn="G2")
    second = apply(world, bill(world, ident="B2", grn_id="G2"))[0]
    assert second["old_qty"] == 10 and second["old_cost"] == 120
    assert second["unknown_old_qty"] == 0 and second["new_cost"] == 110
    assert world.products.find_one({})["cost_price"] == 110


def test_mixed_old_known_unknown_and_later_master_change_cannot_invent_value(world):
    units(world, "KNOWN", 10, cost=100)
    units(world, "UNKNOWN", 10)
    units(world, "NEW", 10, grn="G", pending=True)
    result = apply(world, bill(world, price=200))[0]
    assert result["old_cost"] == 100 and result["unknown_old_qty"] == 10
    assert result["new_cost"] is None
    assert value(world) == {"units": 30, "cost": 3000, "uncosted_units": 10, "unit_cost": 150}
    world.products.update_one({}, {"$set": {"cost_price": 999}})
    assert value(world)["cost"] == 3000 and value(world)["uncosted_units"] == 10


def test_known_master_fallback_remains_for_legacy_units_but_not_marked_unknown(world):
    world.products.update_one({}, {"$set": {"cost_price": 80}})
    units(world, "LEGACY", 5)
    units(world, "UNKNOWN", 5, pending=True)
    units(world, "NEW", 10, grn="G", pending=True)
    result = apply(world, bill(world))[0]
    assert result["old_cost"] == 80 and result["new_cost"] is None
    assert world.products.find_one({})["cost_price"] == 80
    assert value(world) == {"units": 20, "cost": 1400, "uncosted_units": 5, "unit_cost": 93.33}


@pytest.mark.parametrize("part_prices", [[(5, 100)], [(5, 100), (5, 120)]])
def test_partial_or_conflicting_bills_do_not_assign_arbitrary_unit_costs(world, part_prices):
    units(world, "NEW", 10, grn="G", pending=True)
    for i, (qty, price) in enumerate(part_prices):
        doc = bill(world, qty, price, ident=f"B{i}")
    apply(world, doc)
    assert value(world)["uncosted_units"] == 10
    assert world.stock_units.count_documents({"unit_cost": {"$exists": True}}) == 0
    assert world.products.find_one({})["cost_valuation_pending"] is True
    assert world.products.find_one({})["moving_avg_cost"] is None


def test_complete_split_bills_at_one_price_can_resolve_all_units(world):
    units(world, "NEW", 10, grn="G", pending=True)
    bill(world, 4, 100, ident="B1")
    apply(world, bill(world, 6, 100, ident="B2"))
    assert value(world)["cost"] == 1000 and value(world)["uncosted_units"] == 0


@pytest.mark.parametrize("change", [
    {"status": "DRAFT"}, {"status": "CANCELLED"},
    {"vendor_id": "OTHER"}, {"doc_type": "CREDIT_NOTE"},
])
def test_only_this_suppliers_real_booked_purchase_invoice_can_resolve_cost(world, change):
    units(world, "NEW", 10, grn="G", pending=True)
    doc = bill(world, **change)
    apply(world, doc)
    assert world.stock_units.count_documents({"unit_cost": {"$exists": True}}) == 0
    assert value(world)["uncosted_units"] == 10


@pytest.mark.parametrize("duplicate_number", [False, True])
def test_legacy_transferred_unit_requires_unique_receipt_number(world, duplicate_number):
    units(world, "NEW", 10, grn="G", pending=True, grn_number="GRN-G")
    world.stock_units.update_one({"stock_id": "NEW0"}, {"$unset": {"grn_id": ""},
        "$set": {"source_type": "TRANSFER", "source_id": "T"}})
    if duplicate_number:
        world.grns.insert_one({"grn_id": "OTHER", "grn_number": "GRN-G"})
    apply(world, bill(world))
    expected = 0 if duplicate_number else 10
    assert world.stock_units.count_documents({"unit_cost": 100}) == expected


def test_incomplete_physical_receipt_does_not_price_unminted_pieces(world):
    units(world, "NEW", 9, grn="G", pending=True)
    apply(world, bill(world))
    assert world.stock_units.count_documents({"unit_cost": {"$exists": True}}) == 0
    assert value(world)["uncosted_units"] == 9


def test_voided_receipt_cannot_supply_price_provenance(world):
    units(world, "NEW", 10, grn="G", pending=True)
    world.grns.update_one({}, {"$set": {"status": "VOIDED"}})
    apply(world, bill(world))
    assert world.stock_units.count_documents({"unit_cost": {"$exists": True}}) == 0


def test_origin_survives_transfer_and_rejects_are_not_billed_units(world):
    units(world, "NEW", 10, grn="G", pending=True)
    world.stock_units.update_one({"stock_id": "NEW0"}, {"$set": {
        "store_id": "S2", "source_type": "TRANSFER", "source_id": "T"}})
    units(world, "REJECT", 1, grn="G", pending=True, status="QUARANTINED", receipt_disposition="REJECTED")
    units(world, "OTHER", 1, grn="OTHER", pending=True, grn_number="GRN-G")
    apply(world, bill(world))
    assert world.stock_units.count_documents({"grn_id": "G", "unit_cost": 100}) == 10
    assert world.stock_units.find_one({"stock_id": "REJECT0"}).get("unit_cost") is None
    assert world.stock_units.find_one({"stock_id": "OTHER0"}).get("unit_cost") is None


def test_unknown_reserved_or_other_shop_units_keep_their_pending_status(world):
    units(world, "RES", 1, status="RESERVED")
    units(world, "OTHER", 1, store="S2")
    units(world, "NEW", 10, grn="G", pending=True)
    apply(world, bill(world))
    assert value(world)["uncosted_units"] == 2
    assert world.stock_units.find_one({"stock_id": "RES0"})["valuation_cost_pending"] is True


def test_marker_write_failure_cannot_be_followed_by_a_master_price(world, monkeypatch):
    units(world, "OLD", 1)
    units(world, "NEW", 10, grn="G", pending=True)
    doc = bill(world)
    real = world.stock_units.update_one

    def refuse_marker(flt, update, *args, **kwargs):
        if update.get("$set", {}).get("valuation_cost_pending") is True:
            raise RuntimeError("marker write failed")
        return real(flt, update, *args, **kwargs)

    monkeypatch.setattr(world.stock_units, "update_one", refuse_marker)
    assert apply(world, doc) is None
    assert world.products.find_one({})["cost_price"] is None
    assert world.vendor_bills.count_documents({}) == 1


def test_stock_read_failure_is_not_first_receipt(world, monkeypatch):
    units(world, "OLD", 1)
    doc = bill(world)
    monkeypatch.setattr(world.stock_units, "find", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unreadable")))
    assert apply(world, doc) is None
    assert world.products.find_one({})["cost_price"] is None


@pytest.mark.parametrize("itc,expected", [(True, 100), (False, 105)])
def test_walk_in_latest_price_still_resolves_only_its_own_unpriced_units(world, itc, expected):
    world.grns.update_one({}, {"$set": {"grn_subtype": "NO_PO", "accepted_at": "2026-10-01"}})
    units(world, "OLD", 10)
    units(world, "NEW", 10, grn="G", pending=True)
    apply(world, bill(world, itc_eligible=itc))
    assert world.products.find_one({})["cost_price"] == expected
    assert value(world)["cost"] == expected * 10
    assert value(world)["uncosted_units"] == 10
    from api.routers.vendors.grn_accept_lock import _receipt_price_paid
    assert _receipt_price_paid(world.stock_units, world.grns.find_one({}), "P", db=world) == 100
    assert world.stock_units.find_one({"stock_id": "NEW0"})["unit_cost"] == 100


def test_fifo_latest_master_does_not_cost_older_unknown_units(world):
    units(world, "OLD", 10)
    units(world, "NEW", 10, grn="G", pending=True)
    apply(world, bill(world), method="FIFO")
    assert world.products.find_one({})["cost_price"] == 100
    assert value(world) == {"units": 20, "cost": 1000, "uncosted_units": 10, "unit_cost": 100}


def test_real_unit_price_overrides_pending_marker(world):
    units(world, "KNOWN", 1, cost=123, pending=True)
    assert value(world) == {"units": 1, "cost": 123, "uncosted_units": 0, "unit_cost": 123}


def test_unrelated_landed_price_does_not_make_unknown_layers_known(world):
    world.products.update_one({}, {"$set": {"landed_cost": 999}})
    units(world, "OLD", 10)
    units(world, "NEW", 10, grn="G", pending=True)
    result = apply(world, bill(world))[0]
    assert result["new_cost"] is None
    assert value(world)["uncosted_units"] == 10


@pytest.mark.parametrize("landed,source,expected", [
    (105, "PURCHASE_INVOICE", 105), (None, "PURCHASE_INVOICE", 100),
    (0, "PURCHASE_INVOICE", 100), (105, "GRN_PO", 100),
])
def test_billing_inventory_cost_keeps_legacy_and_receipt_base_conventions(landed, source, expected):
    assert stock_value.resolved_cost({"unit_cost": 100, "landed_cost": landed,
                                      "cost_source": source, "valuation_cost_pending": True}, 999) == expected


@pytest.mark.parametrize("role", ["SALES_STAFF", "CASHIER", "STORE_MANAGER", "WORKSHOP_STAFF"])
def test_unit_base_landed_cost_and_pending_metadata_are_accounts_only(role):
    doc = {"stock_id": "U", "unit_cost": 100, "cost_price": 100, "landed_cost": 105,
           "valuation_cost_pending": False, "cost_valuation_pending": True,
           "cost_price_is_catalogue_only": True}
    assert cost_mask.mask_cost(doc, {"roles": [role]}) == {"stock_id": "U"}
    assert cost_mask.mask_cost(doc, {"roles": ["ACCOUNTANT"]}) == doc


@pytest.mark.parametrize("role", ["SALES_STAFF", "STORE_MANAGER", "ACCOUNTANT"])
def test_raw_inventory_unit_read_preserves_cost_mask(world, monkeypatch, role):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers import inventory
    from api.routers.auth import get_current_user
    from tests.test_stock_value_at_cost import _DBProxy

    units(world, "U", 1, cost=100, landed_cost=105, cost_source="PURCHASE_INVOICE", pending=True)
    monkeypatch.setattr(inventory, "get_stock_repository", lambda: StockRepository(world.stock_units))
    monkeypatch.setattr(inventory, "get_product_repository", lambda: ProductRepository(world.products))
    monkeypatch.setattr(inventory, "_get_db", lambda: _DBProxy(world))
    app = FastAPI()
    app.include_router(inventory.router, prefix="/inventory")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "u", "roles": [role], "store_ids": ["S1"], "active_store_id": "S1"}
    response = TestClient(app).get("/inventory/stock", params={"store_id": "S1", "product_id": "P"})
    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    if role == "ACCOUNTANT":
        assert row["unit_cost"] == 100 and row["landed_cost"] == 105
        assert row["valuation_cost_pending"] is True
    else:
        assert not {"unit_cost", "cost_price", "landed_cost", "valuation_cost_pending"} & row.keys()


def test_booking_records_payable_while_unknown_older_stock_stays_unpriced(world, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.auth import get_current_user
    from tests.test_purchase_match import _FakeDB, _PO_DOC, _body

    seed = _FakeDB().collections
    for name in ("vendors", "entities", "stores"):
        world[name].insert_many(copy.deepcopy(seed[name]))
    product = {**seed["products"][0], "product_id": "P", "cost_price": None}
    world.products.replace_one({"product_id": "P"}, product)
    world.grns.update_one({}, {"$set": {"vendor_id": "V1", "po_id": "PO1"}})
    po = copy.deepcopy(_PO_DOC)
    po["items"][0]["product_id"] = "P"
    units(world, "OLD", 10)
    units(world, "NEW", 10, grn="G", pending=True)
    monkeypatch.setattr(pi, "_get_db", lambda: world)
    monkeypatch.setattr(pi, "get_vendor_repository", lambda: SimpleNamespace(find_by_id=lambda key: world.vendors.find_one({"vendor_id": key})))
    monkeypatch.setattr(pi, "get_purchase_order_repository", lambda: SimpleNamespace(find_by_id=lambda _key: po))
    monkeypatch.setattr(pi, "get_grn_repository", lambda: SimpleNamespace(find_by_id=lambda key: world.grns.find_one({"grn_id": key})))
    monkeypatch.setattr(pi, "get_audit_repository", lambda: None)
    app = FastAPI()
    app.include_router(pi.router, prefix="/purchase-invoices")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "a", "roles": ["ACCOUNTANT"], "store_ids": ["S1"], "active_store_id": "S1"}
    body = _body(grn_id="G")
    body["lines"][0]["product_id"] = "P"
    response = TestClient(app).post("/purchase-invoices", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["outstanding"] == 1050
    assert world.vendor_bills.count_documents({}) == 1
    assert world.products.find_one({})["cost_price"] == 100
    assert world.products.find_one({})["cost_price_is_catalogue_only"] is True
    assert value(world) == {"units": 20, "cost": 1000, "uncosted_units": 10, "unit_cost": 100}


def test_valid_bill_finishes_cost_only_draft_without_pricing_unknown_history(world, monkeypatch):
    from api import dependencies
    from api.routers.vendors import grn_accept
    from tests.test_purchase_match import _FakeDB

    product = {**copy.deepcopy(_FakeDB().collections["products"][0]), "product_id": "P",
               "cost_price": None, "catalog_status": "DRAFT", "provisional": True}
    assert pi._pm.compute_catalog_status(product) == ("DRAFT", ["cost_price"])
    world.products.replace_one({"product_id": "P"}, product)
    units(world, "OLD", 10)
    units(world, "NEW", 10, grn="G", pending=True)
    finished = []
    monkeypatch.setattr(dependencies, "get_db", lambda: world)
    monkeypatch.setattr(grn_accept, "catalogue_write_done", finish_catalogue)
    monkeypatch.setattr(grn_accept, "close_catalogue_asks", lambda pid, _reason: finished.append(pid))
    monkeypatch.setattr(grn_accept, "release_held_receipts", lambda _pid: [])
    result = apply(world, bill(world))[0]
    product = world.products.find_one({"product_id": "P"})
    assert product["catalog_status"] == "ACTIVE" and product["provisional"] is False
    assert product["cost_price"] == 100
    assert product["cost_price_is_catalogue_only"] is True
    assert product["moving_avg_cost"] is None and product["cost_valuation_pending"] is True
    assert result["new_cost"] is None and finished == ["P"]
    assert value(world) == {"units": 20, "cost": 1000, "uncosted_units": 10, "unit_cost": 100}


def test_later_full_pricing_uses_known_layers_not_catalogue_only_price(world):
    units(world, "KNOWN", 10, cost=300, grn="KNOWN")
    units(world, "OLD", 10)
    units(world, "NEW", 10, grn="G", pending=True)
    apply(world, bill(world))
    assert world.products.find_one({})["cost_price_is_catalogue_only"] is True
    world.grns.insert_one({"grn_id": "OLD", "grn_number": "GRN-OLD", "store_id": "S1",
                           "vendor_id": "V", "status": "ACCEPTED", "grn_subtype": "STANDARD",
                           "items": [{"product_id": "P", "accepted_qty": 10}]})
    resolved = apply(world, bill(world, price=200, ident="OLD-BILL", grn_id="OLD"))[0]
    product = world.products.find_one({})
    assert resolved["old_cost"] == 200 and resolved["new_cost"] == 200
    assert product["moving_avg_cost"] == 200 and product["cost_valuation_pending"] is False
    assert product["cost_price_is_catalogue_only"] is False
    assert value(world) == {"units": 30, "cost": 6000, "uncosted_units": 0, "unit_cost": 200}


def test_catalogue_only_price_never_proves_unmarked_unit_cost(world):
    world.products.update_one({}, {"$set": {"cost_price": 100, "cost_price_is_catalogue_only": True}})
    units(world, "UNKNOWN", 2)
    assert value(world)["uncosted_units"] == 2 and value(world)["cost"] == 0
    state = pi._product_state_for_valuation(world, ["P"])["P"]
    assert state["uncosted_qty"] == 2 and state["cost_price"] is None
    pi._preserve_unpriced_layers(world, ["P"])
    assert world.stock_units.count_documents({"valuation_cost_pending": True}) == 2


def test_receiving_without_price_marks_unknown_even_when_product_has_old_cost(receiving_world):
    from tests.test_receipt_without_po import _no_po_body, MANAGER, STORE

    http = receiving_world["as_"](MANAGER)
    created = http.post("/vendors/grn", json=_no_po_body(receiving_world))
    assert created.status_code == 201, created.text
    grn_id = created.json()["grn_id"]
    accepted = http.post(f"/vendors/grn/{grn_id}/accept")
    assert accepted.status_code == 200, accepted.text
    db = receiving_world["db"]
    received = list(db.stock_units.find({"grn_id": grn_id}))
    assert len(received) == 5 and {u["status"] for u in received} == {"AVAILABLE"}
    assert all(u.get("valuation_cost_pending") is True for u in received)
    assert all(u.get("unit_cost") is None for u in received)
    valued = stock_value.shelf_units(StockRepository(db.stock_units), ProductRepository(db.products), STORE)
    assert stock_value.uncosted(valued) == 5 and stock_value.total(valued) == 0
