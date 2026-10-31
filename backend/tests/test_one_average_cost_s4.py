"""S4 -- one average cost per product (owner MCQ 2026-10-23, confirms 2026-09-28).

Every piece carries the product's ONE average cost; it moves only when a vendor
bill is ACCEPTED (booked off hold, or its hold approved) or accounts correct it.
Pinned here:
  * an ON-HOLD bill moves neither the cost nor the MRP until it is approved;
  * a delivery-challan bill counts its pieces once (never as old stock AND as
    the billed layer);
  * pieces of a receipt still waiting for its bill are not part of the
    average's basis (two bills in a row give the true average);
  * rejected pieces never count; exact to the paisa (half up); GST a bill
    cannot claim back goes into the cost;
  * exactly once per (product, bill): a retry, a crash re-run (the sweeper)
    or two at once never double a bill;
  * the cost card / history / correction are the accounts roles' only;
  * opening stock gives a product with no average its first one;
  * the backfill fills only products with no average whose pieces all carry
    a cost, backs up and reverts.
"""

import copy
from datetime import timedelta

import mongomock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import purchase_invoices as pi
from api.routers.auth import get_current_user
from api.services import cost_mask, product_cost, stock_value
from api.utils.event_time import utc_now
from tests.test_purchase_match import _GRN_DOC, _PO_DOC, _FakeDB, _app, _body


@pytest.fixture(autouse=True)
def _no_finish(monkeypatch):
    from api.routers.vendors import grn_accept

    monkeypatch.setattr(grn_accept, "catalogue_write_done", lambda *_a, **_k: None)


def _db(cost=None, **product):
    db = mongomock.MongoClient().s4
    db.products.insert_one({"product_id": "P", "cost_price": cost, **product})
    return db


def _pieces(db, n, receipt=None, status="AVAILABLE", prefix="U"):
    start = db.stock_units.count_documents({})
    for i in range(n):
        doc = {"stock_id": f"{prefix}{start + i}", "product_id": "P", "store_id": "S1",
               "status": status, "quantity": 1}
        if receipt:
            doc["grn_id"] = receipt
        db.stock_units.insert_one(doc)


def _receipt(db, gid, accepted, **extra):
    db.grns.insert_one({"grn_id": gid, "status": "ACCEPTED", "vendor_id": "V",
                        "items": [{"product_id": "P", "accepted_qty": accepted}], **extra})


def _bill(bid, qty, price, *, grn_id=None, dcs=None, **extra):
    return {"bill_id": bid, "invoice_id": bid, "invoice_number": f"N-{bid}", "status": "OUTSTANDING",
            "grn_id": grn_id, "linked_dc_ids": dcs, "cost_state": product_cost.PENDING,
            "created_by": "acc", "itc_eligible": True,
            "lines": [{"product_id": "P", "qty": qty, "unit_price": price,
                       "taxable": round(qty * price, 2), "gst_rate": 5}], **extra}


def _accept(db, bill):
    db.vendor_bills.insert_one(copy.deepcopy(bill))
    return pi._apply_accepted_bill(db, bill, actor="acc")


def _avg(db):
    return db.products.find_one({"product_id": "P"})["cost_price"]


# --------------------------------------------------------------------------
# The two named bugs
# --------------------------------------------------------------------------


def test_an_on_hold_bill_moves_neither_cost_nor_mrp_until_approved():
    db = _FakeDB()
    cli = _app(db, po=_PO_DOC, grn=_GRN_DOC)
    body = _body(invoice_number="HOLD-1", total=1260,
                 lines=[{"product_id": "P1", "qty": 10, "unit_price": 120, "gst_rate": 5, "mrp": 333}])
    created = cli.post("/api/v1/vendors/purchase-invoices", json=body).json()
    assert created["match_status"] == pi.pmatch.MATCH_ON_HOLD
    assert created["cost_state"] == product_cost.HELD
    product = db.get_collection("products").find_one({"product_id": "P1"})
    assert (product["cost_price"], product["mrp"]) == (100.0, 200.0)
    assert not product.get("cost_history")

    r = cli.post(f"/api/v1/vendors/purchase-invoices/{created['invoice_id']}/approve-exception",
                 json={"reason": "price rise agreed"})
    assert r.status_code == 200, r.text
    product = db.get_collection("products").find_one({"product_id": "P1"})
    assert (product["cost_price"], product["mrp"]) == (120.0, 333.0)
    (row,) = product["cost_history"]
    assert (row["kind"], row["qty"], row["price"], row["new_cost"], row["by"]) == ("BILL", 10, 120.0, 120.0, "u1")
    bill = db.get_collection("vendor_bills").find_one({"bill_id": created["invoice_id"]})
    assert bill["cost_state"] == product_cost.APPLIED
    # A second approval is refused and applies nothing again.
    again = cli.post(f"/api/v1/vendors/purchase-invoices/{created['invoice_id']}/approve-exception",
                     json={"reason": "again"})
    assert again.status_code == 400
    assert len(db.get_collection("products").find_one({"product_id": "P1"})["cost_history"]) == 1


def test_a_bill_booked_on_hold_before_s4_is_not_costed_again_at_approval():
    """Before S4 a held bill's cost went in at booking: approving it now must
    not apply it a second time (no cost_state on it)."""
    db = _FakeDB()
    cli = _app(db, po=_PO_DOC, grn=_GRN_DOC)
    created = cli.post("/api/v1/vendors/purchase-invoices", json=_body(
        invoice_number="OLD-HOLD", total=1260,
        lines=[{"product_id": "P1", "qty": 10, "unit_price": 120, "gst_rate": 5}])).json()
    db.get_collection("vendor_bills").update_one({"bill_id": created["invoice_id"]},
                                                 {"$unset": {"cost_state": ""}})
    r = cli.post(f"/api/v1/vendors/purchase-invoices/{created['invoice_id']}/approve-exception",
                 json={"reason": "ok"})
    assert r.status_code == 200
    assert db.get_collection("products").find_one({"product_id": "P1"})["cost_price"] == 100.0


def test_a_challan_bill_counts_its_pieces_once():
    """10 billed pieces at 100 on the shelf; two challans brought 6 + 4 more,
    already on the shelf when their bill (10 at 120) is accepted. The
    average is (10 x 100 + 10 x 120) / 20 = 110 -- not the old
    (20 x 100 + 10 x 120) / 30 = 106.67 that counted the challans' pieces
    twice."""
    db = _db(100.0)
    _pieces(db, 10)  # costed long ago (no receipt left to bill)
    _receipt(db, "DC1", 6)
    _receipt(db, "DC2", 4)
    _pieces(db, 6, "DC1")
    _pieces(db, 4, "DC2")
    _accept(db, _bill("B1", 10, 120, dcs=["DC1", "DC2"]))
    assert _avg(db) == 110.0


# --------------------------------------------------------------------------
# The average itself
# --------------------------------------------------------------------------


def test_pieces_still_waiting_for_their_bill_are_not_in_the_basis():
    """Receipt A (3 pieces) and receipt B (2) both on the shelf at the
    provisional PO cost; A's bill at 3400 then B's at 3500. True average
    (3 x 3400 + 2 x 3500) / 5 = 3440 -- B's pieces must not weigh into A's
    average at the provisional 3450."""
    db = _db(3450.0, cost_source="GRN_PO")
    _receipt(db, "A", 3)
    _receipt(db, "B", 2)
    _pieces(db, 3, "A")
    _pieces(db, 2, "B")
    _accept(db, _bill("BA", 3, 3400, grn_id="A"))
    assert _avg(db) == 3400.0
    _accept(db, _bill("BB", 2, 3500, grn_id="B"))
    assert _avg(db) == 3440.0


def test_the_board_example_rejected_pieces_never_count():
    """Cost board: 3 pieces at 3340, a bill for 4 of which 1 was rejected at
    receipt, 3450 each -> 3395 (3 good pieces at 3450)."""
    db = _db(3340.0)
    _pieces(db, 3)
    _receipt(db, "G", 3)
    _pieces(db, 3, "G")
    _accept(db, _bill("B", 4, 3450, grn_id="G"))
    assert _avg(db) == 3395.0


def test_exact_to_the_paisa_half_up():
    db = _db(100.00)
    _pieces(db, 1)
    _receipt(db, "G", 1)
    _pieces(db, 1, "G")
    _accept(db, _bill("B", 1, 100.01, grn_id="G"))
    assert _avg(db) == 100.01  # 100.005 rounds half up, never down to 100.00


def test_gst_the_bill_cannot_claim_goes_into_the_cost():
    db = _db(None)
    _receipt(db, "G", 10)
    _pieces(db, 10, "G")
    bill = _bill("B", 10, 100, grn_id="G", itc_eligible=False)
    bill["lines"][0].update({"cgst": 25.0, "sgst": 25.0})
    _accept(db, bill)
    assert _avg(db) == 105.0


def test_a_product_with_no_average_takes_the_bills_price():
    db = _db(None)
    _pieces(db, 2)  # legacy pieces with no cost at all
    _receipt(db, "G", 3)
    _pieces(db, 3, "G")
    _accept(db, _bill("B", 3, 250, grn_id="G"))
    product = db.products.find_one({})
    assert (product["cost_price"], product["cost_price_is_catalogue_only"]) == (250.0, False)


def test_a_bill_applies_exactly_once():
    db = _db(100.0)
    _pieces(db, 10)
    _receipt(db, "G", 10)
    _pieces(db, 10, "G")
    bill = _bill("B", 10, 120, grn_id="G")
    _accept(db, bill)
    pi._apply_accepted_bill(db, bill, actor="acc")  # a retry
    pi._apply_valuation_trueup(db, bill, {"lines": bill["lines"]})  # a re-run
    product = db.products.find_one({})
    assert product["cost_price"] == 110.0
    assert len(product["cost_history"]) == 1 and product["cost_keys"] == ["B"]


def test_two_bills_at_once_lose_neither(monkeypatch):
    """Bill B2 lands between B1's read and B1's write: B1's guarded write
    misses, re-reads and re-works on top of B2."""
    db = _db(100.0)
    _pieces(db, 10)
    _receipt(db, "G1", 10)
    _receipt(db, "G2", 10)
    _pieces(db, 10, "G1")
    _pieces(db, 10, "G2")
    b2 = _bill("B2", 10, 130, grn_id="G2")
    db.vendor_bills.insert_one(copy.deepcopy(b2))
    real = product_cost.basis_pieces
    fired = []

    def racing(*a, **k):
        if not fired:
            fired.append(1)
            pi._apply_accepted_bill(db, b2, actor="acc")
        return real(*a, **k)

    monkeypatch.setattr(product_cost, "basis_pieces", racing)
    _accept(db, _bill("B1", 10, 120, grn_id="G1"))
    product = db.products.find_one({})
    # B2: (10 x 100 + 10 x 130) / 20 = 115; then B1: (20 x 115 + 10 x 120) / 30 = 116.67
    assert product["cost_keys"] == ["B2", "B1"]
    assert product["cost_price"] == 116.67


def test_the_sweeper_finishes_a_bill_a_crash_left_half_done():
    db = _db(100.0)
    _pieces(db, 10)
    _receipt(db, "G", 10)
    _pieces(db, 10, "G")
    bill = _bill("B", 10, 120, grn_id="G",
                 created_at=(utc_now() - timedelta(minutes=10)).isoformat())
    db.vendor_bills.insert_one(copy.deepcopy(bill))  # booked, then the process died
    assert pi.finish_accepted_bills(db) == 1
    assert _avg(db) == 110.0
    assert db.vendor_bills.find_one({})["cost_state"] == product_cost.APPLIED
    assert pi.finish_accepted_bills(db) == 0


def test_sales_and_counts_never_change_the_average_and_value_is_pieces_times_it():
    db = _db(150.0)
    _pieces(db, 4)
    _pieces(db, 1, status="SOLD")
    db.stock_units.update_one({"stock_id": "U0"}, {"$set": {"unit_cost": 999}})
    from database.repositories import ProductRepository, StockRepository

    units = stock_value.shelf_units(StockRepository(db.stock_units), ProductRepository(db.products), None)
    assert stock_value.total(units) == 600.0 == stock_value.value(4, db.products.find_one({}))


# --------------------------------------------------------------------------
# The cost card, history and correction: accounts only
# --------------------------------------------------------------------------


def _card_app(db, monkeypatch, role):
    from api.routers import inventory
    from database.repositories import ProductRepository, StockRepository

    monkeypatch.setattr(inventory, "get_stock_repository", lambda: StockRepository(db.stock_units))
    monkeypatch.setattr(inventory, "get_product_repository", lambda: ProductRepository(db.products))
    monkeypatch.setattr(inventory, "_get_db", lambda: db)
    monkeypatch.setattr(inventory, "get_audit_repository", lambda: None)
    app = FastAPI()
    app.include_router(inventory.router, prefix="/inventory")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": f"u-{role}", "roles": [role], "store_ids": ["S1"], "active_store_id": "S1"}
    return TestClient(app)


def test_the_cost_card_and_a_correction(monkeypatch):
    db = _db(3340.0)
    _pieces(db, 5)
    cli = _card_app(db, monkeypatch, "ACCOUNTANT")
    card = cli.get("/inventory/products/P/cost").json()
    assert (card["pieces"], card["average_cost"], card["stock_value"], card["uncosted_pieces"]) == (
        5, 3340.0, 16700.0, 0)
    r = cli.post("/inventory/products/P/cost-correction",
                 json={"average_cost": 3395, "reason": "Safilo credit note for scheme discount"})
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["stock_value"] == 16975.0
    (row,) = card["history"]
    assert (row["kind"], row["old_cost"], row["new_cost"], row["by"]) == ("CORRECTION", 3340.0, 3395.0, "u-ACCOUNTANT")
    assert row["reason"].startswith("Safilo")
    assert cli.post("/inventory/products/P/cost-correction",
                    json={"average_cost": 3395, "reason": "same again"}).status_code == 409
    assert cli.post("/inventory/products/P/cost-correction",
                    json={"average_cost": 3395, "reason": "  "}).status_code == 422


def test_a_product_with_no_cost_says_its_figure_is_too_low(monkeypatch):
    db = _db(None)
    _pieces(db, 3)
    card = _card_app(db, monkeypatch, "ADMIN").get("/inventory/products/P/cost").json()
    assert (card["average_cost"], card["stock_value"], card["uncosted_pieces"]) == (None, 0.0, 3)


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER", "CATALOG_MANAGER", "SALES_STAFF"])
def test_what_we_pay_is_accounts_only(monkeypatch, role):
    db = _db(3340.0)
    cli = _card_app(db, monkeypatch, role)
    assert cli.get("/inventory/products/P/cost").status_code == 403
    assert cli.post("/inventory/products/P/cost-correction",
                    json={"average_cost": 1, "reason": "nope"}).status_code == 403
    doc = {"product_id": "P", "cost_history": [{"price": 1}], "cost_keys": ["B"]}
    assert cost_mask.mask_cost(doc, {"roles": [role]}, "product") == {"product_id": "P"}


# --------------------------------------------------------------------------
# Opening stock and the backfill
# --------------------------------------------------------------------------


def test_opening_stock_gives_a_product_with_no_average_its_first_one():
    db = _db(None)
    assert product_cost.seed(db, "P", 800, key="opening:1", kind="OPENING_STOCK", actor="m")
    assert product_cost.seed(db, "P", 900, key="opening:2", kind="OPENING_STOCK", actor="m") is None
    assert _avg(db) == 800.0


def test_the_backfill_fills_backs_up_and_reverts():
    from scripts import backfill_average_cost as bf

    db = mongomock.MongoClient().bf
    db.products.insert_many([
        {"product_id": "FULL", "cost_price": None},
        {"product_id": "PART", "cost_price": None},
        {"product_id": "HAS", "cost_price": 500.0},
    ])
    db.stock_units.insert_many([
        {"product_id": "FULL", "status": "AVAILABLE", "unit_cost": 100},
        {"product_id": "FULL", "status": "RESERVED", "unit_cost": 101},
        {"product_id": "PART", "status": "AVAILABLE", "unit_cost": 100},
        {"product_id": "PART", "status": "AVAILABLE"},
        {"product_id": "HAS", "status": "AVAILABLE", "unit_cost": 900},
    ])
    rows = {r["product_id"]: r for r in bf.plan(db)}
    assert (rows["FULL"]["fill"], rows["PART"]["fill"], rows["HAS"]["fill"]) == (100.5, None, None)
    assert rows["HAS"]["value_s4"] == 500.0 and rows["HAS"]["value_before"] == 900.0
    assert bf.apply(db, list(rows.values()), "RUN1") == 1
    assert db.products.find_one({"product_id": "FULL"})["cost_price"] == 100.5
    assert bf.plan(db)[0]["fill"] is None  # a re-run finds nothing left
    assert bf.revert(db, "RUN1") == 1
    full = db.products.find_one({"product_id": "FULL"})
    assert full["cost_price"] is None and full["cost_history"] == [] and full["cost_keys"] == []
