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
from tests.test_purchase_match import _GRN_DOC, _PO_DOC, _FakeDB, _app, _body, _restore_router  # noqa: F401 (autouse: undoes _app's router patches)


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
                 json={"average_cost": 3395, "expected_average_cost": 3340,
                       "reason": "Safilo credit note for scheme discount"})
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["stock_value"] == 16975.0
    (row,) = card["history"]
    assert (row["kind"], row["old_cost"], row["new_cost"], row["by"]) == ("CORRECTION", 3340.0, 3395.0, "u-ACCOUNTANT")
    assert row["reason"].startswith("Safilo")
    same = cli.post("/inventory/products/P/cost-correction",
                    json={"average_cost": 3395, "expected_average_cost": 3395, "reason": "same again"})
    assert same.status_code == 409 and "already that figure" in same.json()["detail"]
    assert cli.post("/inventory/products/P/cost-correction",
                    json={"average_cost": 3395, "expected_average_cost": 3395, "reason": "  "}).status_code == 422


@pytest.mark.parametrize("twin_id,link", [("TWIN", {"pim_product_id": "TWIN"}), ("P", {})])
def test_the_catalogue_copy_follows_the_average(twin_id, link):
    """A correction and a first cost reach the catalogue copy by the one
    mirror rule: a create-door twin (keyed by the spine's pim_product_id, a
    different uuid) as well as a converged one (sharing the product id)."""
    db = _db(None, **link)
    db.catalog_products.insert_one({"id": twin_id, "pricing": {"cost_price": 1.0}})
    product_cost.seed(db, "P", 800, key="opening:1", kind="OPENING_STOCK", actor="m")
    assert db.catalog_products.find_one({"id": twin_id})["pricing"]["cost_price"] == 800.0
    product_cost.correct(db, "P", 812.5, "credit note", "acc", expected=800)
    assert db.catalog_products.find_one({"id": twin_id})["pricing"]["cost_price"] == 812.5


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
                    json={"average_cost": 1, "expected_average_cost": 3340, "reason": "nope"}).status_code == 403
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


@pytest.mark.parametrize("source", ("PO_RATE", "GRN_PO", "GRN_WALKIN"))
def test_a_rate_fill_never_overwrites_a_cost_that_landed_after_its_read(source):
    """S4 review round 3: the PO / receipt rate fill checks the copy its
    caller read; a first cost (or bill) landing after that read is kept."""
    from api.routers.vendors.gst import _promote_cost_from_rate
    from database.repositories.product_repository import ProductRepository

    db = _db(None)
    stale = db.products.find_one({"product_id": "P"})
    product_cost.seed(db, "P", 500, key="opening:x", kind="OPENING_STOCK", actor="m")
    assert not _promote_cost_from_rate("P", stale, 450, source, ProductRepository(db.products))
    doc = db.products.find_one({"product_id": "P"})
    assert doc["cost_price"] == 500.0 == doc["moving_avg_cost"]
    assert [r["new_cost"] for r in doc["cost_history"]] == [500.0]
    assert doc.get("cost_source") != source
    # With nothing landing in between, the fill still fills.
    db = _db(None)
    fresh = db.products.find_one({"product_id": "P"})
    assert _promote_cost_from_rate("P", fresh, 450, source, ProductRepository(db.products))
    assert _avg(db) == 450.0


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
    # FULL filled; HAS (an average with no history) carried over into it.
    assert bf.apply(db, list(rows.values()), "RUN1") == 2
    assert db.products.find_one({"product_id": "FULL"})["cost_price"] == 100.5
    assert bf.plan(db)[0]["fill"] is None  # a re-run finds nothing left
    assert bf.revert(db, "RUN1") == 2
    full = db.products.find_one({"product_id": "FULL"})
    assert full["cost_price"] is None and full["cost_history"] == [] and full["cost_keys"] == []


# --------------------------------------------------------------------------
# Review round 1 (S4): every accepted bill puts its key on every product it
# bills; the exactly-once guard is on the keys the work was based on; a bill
# is APPLIED only when it really is in every average; legacy receipts blend.
# --------------------------------------------------------------------------


def _walk_in_booked_late(db):
    """Walk-in receipt W (5 at 100) accepted, then PO receipt R (5, minted at
    200) accepted; W's bill (at the average, 100) is booked only after R and
    leaves the average where it is."""
    _receipt(db, "W", 5, grn_subtype="NO_PO", accepted_at="2026-10-01T10:00:00+00:00")
    _pieces(db, 5, "W", prefix="W")
    _receipt(db, "R", 5, accepted_at="2026-10-02T10:00:00+00:00", po_id="PO1")
    _pieces(db, 5, "R", prefix="R")
    db.stock_units.update_many({"grn_id": "R"}, {"$set": {"unit_cost": 200.0}})
    _accept(db, _bill("BW", 5, 100, grn_id="W"))


def test_a_bill_that_moves_no_average_still_puts_its_receipt_in_the_basis():
    db = _db(100.0)
    _walk_in_booked_late(db)
    product = db.products.find_one({})
    assert product["cost_price"] == 100.0 and product["cost_keys"] == ["BW"]
    assert product_cost.basis_pieces(db, "P", product["cost_keys"]) == 5
    assert db.vendor_bills.find_one({"bill_id": "BW"})["cost_state"] == product_cost.APPLIED
    # R's bill blends with W's 5 pieces: (5 x 100 + 5 x 200) / 10 = 150, not 200.
    _accept(db, _bill("BR", 5, 200, grn_id="R"))
    assert _avg(db) == 150.0


def test_a_bill_landing_between_read_and_write_is_re_worked_even_if_it_moved_nothing(monkeypatch):
    """A-B-A: B2 (10 at 100 on an average of 100) lands after B1 worked out
    its basis but before B1's write; the average did not move, yet B2's 10
    pieces joined the basis. B1 must re-work: (20 x 100 + 10 x 130) / 30 =
    110 -- never the stale (10 x 100 + 10 x 130) / 20 = 115. (The product
    has moved before, so no other field changes when B2 lands.)"""
    db = _db(100.0, cost_price_is_catalogue_only=False, cost_keys=["OLD"])
    _pieces(db, 10)
    _receipt(db, "G1", 10)
    _receipt(db, "G2", 10)
    _pieces(db, 10, "G1")
    _pieces(db, 10, "G2")
    b2 = _bill("B2", 10, 100, grn_id="G2")
    db.vendor_bills.insert_one(copy.deepcopy(b2))
    real = product_cost.basis_pieces
    fired = []

    def stale(*a, **k):
        n = real(*a, **k)
        if not fired:
            fired.append(1)
            pi._apply_accepted_bill(db, b2, actor="acc")
        return n

    monkeypatch.setattr(product_cost, "basis_pieces", stale)
    _accept(db, _bill("B1", 10, 130, grn_id="G1"))
    product = db.products.find_one({})
    assert product["cost_keys"] == ["OLD", "B2", "B1"]
    assert product["cost_price"] == 110.0


def test_a_failed_apply_stays_pending_and_the_sweeper_finishes_it(monkeypatch):
    db = _db(100.0)
    _pieces(db, 10)
    _receipt(db, "G", 10)
    _pieces(db, 10, "G")
    bill = _bill("B", 10, 120, grn_id="G", created_at=(utc_now() - timedelta(minutes=10)).isoformat())
    real = product_cost.basis_pieces
    blips = []

    def flaky(*a, **k):
        if not blips:
            blips.append(1)
            raise RuntimeError("database blip")
        return real(*a, **k)

    monkeypatch.setattr(product_cost, "basis_pieces", flaky)
    _accept(db, bill)
    assert (_avg(db), db.vendor_bills.find_one({})["cost_state"]) == (100.0, product_cost.PENDING)
    assert pi.finish_accepted_bills(db) == 1
    assert (_avg(db), db.vendor_bills.find_one({})["cost_state"]) == (110.0, product_cost.APPLIED)


def test_legacy_receipt_pieces_blend_with_the_first_s4_bill():
    """10 pieces of a receipt accepted before S4 (never billed) carry the
    average 1000; a new receipt's bill, 5 at 1100, blends with them:
    (10 x 1000 + 5 x 1100) / 15 = 1033.33 -- not 1100."""
    from scripts import backfill_average_cost as bf

    db = _db(1000.0)
    _receipt(db, "OLD", 10)
    _pieces(db, 10, "OLD")
    assert bf.legacy_receipts(db) == ["OLD"]
    bf.apply(db, bf.plan(db), "RUN1")
    _receipt(db, "NEW", 5)
    _pieces(db, 5, "NEW")
    _accept(db, _bill("B", 5, 1100, grn_id="NEW", created_at="2026-11-01T00:00:00+00:00"))
    assert _avg(db) == 1033.33
    # A receipt accepted after the first S4 bill is never taken for a legacy one.
    _receipt(db, "LATER", 1, accepted_at="2026-11-02T00:00:00+00:00")
    assert bf.legacy_receipts(db) == []
    bf.revert(db, "RUN1")
    assert not db.grns.find_one({"grn_id": "OLD"}).get(product_cost.PRE_S4_STAMP)


def test_the_backfill_carries_an_old_average_into_the_history_and_reverts_it():
    from scripts import backfill_average_cost as bf

    db = _db(3340.0, cost_source="PURCHASE_INVOICE")  # a pre-S4 bill's average
    _pieces(db, 2)
    assert bf.apply(db, bf.plan(db), "RUN2") == 1
    product = db.products.find_one({})
    (row,) = product["cost_history"]
    assert (row["kind"], row["old_cost"], row["new_cost"], row["source"]) == (
        "CARRIED_OVER", None, 3340.0, "PURCHASE_INVOICE")
    assert product["cost_price"] == 3340.0
    assert bf.apply(db, bf.plan(db), "RUN3") == 0  # nothing left to carry over
    assert bf.revert(db, "RUN2") == 1
    product = db.products.find_one({})
    assert (product["cost_price"], product["cost_history"], product["cost_keys"]) == (3340.0, [], [])


def test_the_backfill_revert_finds_a_product_written_just_before_a_crash():
    from scripts import backfill_average_cost as bf

    db = _db(None)
    _pieces(db, 2)
    db.stock_units.update_many({}, {"$set": {"unit_cost": 500.0}})
    rows = bf.plan(db)
    bf._backup(db, "RUN4", "P")  # the backup landed, the seed wrote, then the process died
    product_cost.seed(db, "P", rows[0]["fill"], key="backfill:RUN4", kind="BACKFILL", actor="script")
    assert _avg(db) == 500.0
    assert bf.revert(db, "RUN4") == 1
    assert _avg(db) is None


def test_the_dry_run_flags_an_average_from_a_held_or_challan_bill():
    from scripts import backfill_average_cost as bf

    db = _db(120.0, cost_source_id="HB")
    _pieces(db, 1)
    db.vendor_bills.insert_one({"bill_id": "HB", "bill_number": "INV-9", "match_status": "ON_HOLD_EXCEPTION"})
    assert "still on hold" in bf.plan(db)[0]["check"]
    db.vendor_bills.update_one({"bill_id": "HB"}, {"$set": {"match_status": "MATCHED", "linked_dc_ids": ["DC1"]}})
    assert "delivery-challan" in bf.plan(db)[0]["check"]
    assert _avg(db) == 120.0  # flagged, never changed


# --------------------------------------------------------------------------
# Fix round 1 (S4): owner rulings 2026-11-01 and review items 1-3.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", ("PO_RATE", "GRN_PO", "GRN_WALKIN"))
def test_a_provisional_cost_is_replaced_by_the_first_real_bill_never_averaged(source):
    """Review 1: 3 cost-less opening pieces, a rate fill of 400 (no history
    row), then a bill of 500 for 2 received pieces: the average is 500 --
    never (3 x 400 + 2 x 500) / 5 = 440."""
    from api.routers.vendors.gst import _promote_cost_from_rate
    from database.repositories.product_repository import ProductRepository

    db = _db(None)
    _pieces(db, 3)
    assert _promote_cost_from_rate("P", db.products.find_one({}), 400, source, ProductRepository(db.products))
    assert product_cost.provisional(db.products.find_one({}))
    _receipt(db, "G", 2)
    _pieces(db, 2, "G")
    _accept(db, _bill("B", 2, 500, grn_id="G"))
    assert _avg(db) == 500.0
    assert not product_cost.provisional(db.products.find_one({}))


def test_opening_stock_replaces_a_provisional_cost():
    """Review 1: opening stock (seed) and its preview (_opening_cost) treat a
    provisional cost as no average: the typed cost becomes the first one."""
    from api.routers.inventory.opening_stock import _opening_cost

    db = _db(400.0, cost_source="PO_RATE")
    assert _opening_cost(db.products.find_one({}), 450.0) == (450.0, None)
    assert product_cost.seed(db, "P", 450, key="opening:1", kind="OPENING_STOCK", actor="m")
    assert _avg(db) == 450.0
    # A real average still wins over a typed opening cost.
    assert product_cost.seed(db, "P", 999, key="opening:2", kind="OPENING_STOCK", actor="m") is None


def test_a_correction_typed_against_a_stale_average_is_refused(monkeypatch):
    """Review 2: the card showed 3340; a bill moved the average to 3395
    before the correction was saved -- 409, saying so, nothing written."""
    db = _db(3340.0)
    _pieces(db, 5)
    cli = _card_app(db, monkeypatch, "ACCOUNTANT")
    product_cost.correct(db, "P", 3395, "a bill landed", "acc", expected=3340)
    r = cli.post("/inventory/products/P/cost-correction",
                 json={"average_cost": 3300, "expected_average_cost": 3340, "reason": "credit note"})
    assert r.status_code == 409 and "changed since you opened" in r.json()["detail"]
    assert _avg(db) == 3395.0
    assert [h["new_cost"] for h in db.products.find_one({})["cost_history"]] == [3395.0]


def test_a_bill_landing_between_a_corrections_read_and_write_refuses_it(monkeypatch):
    """Review 2: the bill lands after the correction checked the average but
    before its guarded write: the write misses, the re-read sees the new
    average, and the correction is refused (CostChanged) -- never written
    over the bill."""
    db = _db(3340.0)
    real = product_cost.utc_now_iso
    fired = []

    def racing():
        if not fired:
            fired.append(1)
            db.products.update_one({"product_id": "P"}, {"$set": {"cost_price": 3395.0},
                                                         "$push": {"cost_keys": "BILL-X"}})
        return real()

    monkeypatch.setattr(product_cost, "utc_now_iso", racing)
    with pytest.raises(product_cost.CostChanged):
        product_cost.correct(db, "P", 3300, "credit note", "acc", expected=3340)
    product = db.products.find_one({})
    assert product["cost_price"] == 3395.0 and not product.get("cost_history")


def test_a_walk_in_bill_averages_in_like_every_other_bill():
    """Owner 2026-11-01 (b): 5 pieces at 100 on the shelf; a walk-in (no-PO)
    receipt's bill, 5 at 200, averages in: (5 x 100 + 5 x 200) / 10 = 150 --
    the latest price paid (200) no longer replaces the cost."""
    db = _db(100.0)
    _pieces(db, 5)
    _receipt(db, "W", 5, grn_subtype="NO_PO")
    _pieces(db, 5, "W")
    _accept(db, _bill("BW", 5, 200, grn_id="W"))
    product = db.products.find_one({})
    assert product["cost_price"] == 150.0
    assert product["cost_history"][-1]["method"] == "AVERAGE"


def test_the_blind_count_values_a_piece_with_no_product_as_uncosted(monkeypatch):
    """Review 3: the blind count's cost resolver reads the one cost rule --
    an id with no products doc is uncosted, never its catalogue copy's
    price."""
    from api.routers import blind_stock_take as r

    db = _db(250.0)
    db.catalog_products.insert_one({"id": "CAT-ONLY", "pricing": {"cost_price": 1000.0}})
    monkeypatch.setattr(r, "_get_db", lambda: db)
    assert r._cost_resolver(["P", "CAT-ONLY"]) == {"P": 25000}


def test_the_backfill_averages_held_pieces_only_walk_ins_included_and_leaves_provisional_costs():
    """Owner 2026-11-01 under the backfill: (a) a sold piece never weighs in;
    (b) a walk-in receipt's piece is averaged in at its price like any
    receipt's; a provisional cost is not carried over (its first bill
    replaces it)."""
    from scripts import backfill_average_cost as bf

    db = mongomock.MongoClient().bf2
    db.products.insert_many([
        {"product_id": "NONE", "cost_price": None},
        {"product_id": "PROV", "cost_price": 400.0, "cost_source": "PO_RATE"},
    ])
    db.stock_units.insert_many([
        {"product_id": "NONE", "status": "AVAILABLE", "unit_cost": 100, "grn_id": "PO-REC"},
        {"product_id": "NONE", "status": "AVAILABLE", "unit_cost": 200, "grn_id": "WALK-IN"},
        {"product_id": "NONE", "status": "SOLD", "unit_cost": 999, "grn_id": "WALK-IN"},
        {"product_id": "PROV", "status": "AVAILABLE", "unit_cost": 400},
    ])
    rows = {r["product_id"]: r for r in bf.plan(db)}
    assert rows["NONE"]["fill"] == 150.0
    assert rows["PROV"]["fill"] is None and "provisional" in rows["PROV"]["reason"]
    assert bf.carry_over_candidates(db) == []
    assert bf.apply(db, list(rows.values()), "RUN9") == 1
    assert db.products.find_one({"product_id": "PROV"}).get("cost_history") is None


# --------------------------------------------------------------------------
# Fix round 2 (S4): the backfill under the owner's 2026-11-01 rulings.
# --------------------------------------------------------------------------


def test_a_po_rate_cost_already_billed_before_s4_blends_with_the_first_s4_bill():
    """Before S4 a GST-claiming header bill on a PO receipt left the product at
    its PO rate (cost_source GRN_PO, no history). That cost IS billed: the
    backfill carries it over, so the first S4 bill BLENDS with the 10 old
    pieces: (10 x 400 + 5 x 500) / 15 = 433.33 -- not 500. A PO rate no bill
    ever priced stays provisional."""
    from scripts import backfill_average_cost as bf

    db = _db(400.0, cost_source="GRN_PO")
    _receipt(db, "G0", 10)
    _pieces(db, 10, "G0")
    db.vendor_bills.insert_one({"bill_id": "OLD", "grn_id": "G0", "status": "PAID", "itc_eligible": True})
    assert "billed before S4" in bf.plan(db)[0]["reason"]
    bf.apply(db, bf.plan(db), "RUN1")
    (row,) = db.products.find_one({})["cost_history"]
    assert (row["kind"], row["new_cost"]) == ("CARRIED_OVER", 400.0)
    _receipt(db, "G1", 5)
    _pieces(db, 5, "G1")
    _accept(db, _bill("B", 5, 500, grn_id="G1", created_at="2026-11-01T00:00:00+00:00"))
    assert _avg(db) == 433.33

    unbilled = _db(400.0, cost_source="GRN_PO")
    _receipt(unbilled, "G0", 10)
    _pieces(unbilled, 10, "G0")
    unbilled.vendor_bills.insert_one({"bill_id": "HOLD", "grn_id": "G0", "status": "OUTSTANDING",
                                      "match_status": "ON_HOLD_EXCEPTION"})
    assert bf.carry_over_candidates(unbilled) == []
    assert "provisional" in bf.plan(unbilled)[0]["reason"]


def test_the_backfill_re_averages_a_pre_s4_walk_in_latest_price():
    """From 2026-10-12 main costed a walk-in bill at the LATEST price: 10 held
    pieces at 100, a walk-in bill for 1 at 200 left the average at 200. Under
    ruling (b) walk-ins average in: the dry run shows (10 x 100 + 1 x 200) /
    11 = 109.09, --apply writes it (backed up), --revert puts 200 back."""
    from scripts import backfill_average_cost as bf

    db = _db(200.0, cost_source="PURCHASE_INVOICE", cost_source_id="INV-W1")
    _receipt(db, "G1", 10)
    _receipt(db, "G2", 1, grn_subtype="NO_PO")
    _pieces(db, 10, "G1")
    _pieces(db, 1, "G2")
    db.stock_units.update_many({"grn_id": "G1"}, {"$set": {"unit_cost": 100.0}})
    db.stock_units.update_many({"grn_id": "G2"}, {"$set": {"unit_cost": 200.0}})
    db.vendor_bills.insert_one({"bill_id": "INV-W1", "invoice_id": "INV-W1", "grn_id": "G2",
                                "status": "PAID", "match_status": "MATCHED"})
    (row,) = bf.plan(db)
    assert row["fill"] == 109.09 and "walk-in" in row["reason"]
    assert _avg(db) == 200.0  # the dry run writes nothing
    assert bf.apply(db, [row], "RUN5") == 1
    product = db.products.find_one({})
    assert product["cost_price"] == 109.09
    (hist,) = product["cost_history"]
    assert (hist["kind"], hist["old_cost"], hist["new_cost"]) == ("BACKFILL", 200.0, 109.09)
    assert bf.plan(db)[0]["fill"] is None  # a re-run finds nothing left
    assert bf.revert(db, "RUN5") == 1
    product = db.products.find_one({})
    assert (product["cost_price"], product["cost_history"]) == (200.0, [])



# --------------------------------------------------------------------------
# Fix round 3 (S4).
# --------------------------------------------------------------------------


def _stamped_g0(db):
    """Average 400 (a pre-S4 bill's); PO receipt G0 (10 pieces) accepted
    before S4, its bill not in yet: the backfill stamps it at 400. Then G1's
    S4 bill, 5 at 500: (10 x 400 + 5 x 500) / 15 = 433.33."""
    from scripts import backfill_average_cost as bf

    _receipt(db, "G0", 10, po_id="PO0", accepted_at="2026-10-20T10:00:00+00:00")
    _pieces(db, 10, "G0", prefix="G0-")
    bf.apply(db, bf.plan(db), "RUN1")
    g0 = db.grns.find_one({"grn_id": "G0"})
    assert (g0[product_cost.PRE_S4_STAMP], g0[product_cost.PRE_S4_AVERAGES]) == ("RUN1", {"P": 400.0})
    _receipt(db, "G1", 5, accepted_at="2026-11-01T10:00:00+00:00")
    _pieces(db, 5, "G1", prefix="G1-")
    _accept(db, _bill("B1", 5, 500, grn_id="G1", created_at="2026-11-01T00:00:00+00:00"))
    assert _avg(db) == 433.33


def test_a_stamped_receipts_own_s4_bill_after_another_keeps_its_weight():
    """G0's own S4 bill lands after B1, 10 at 400: its pieces stay in the
    basis and add 10 x (400 - 400) = 0 -- the average stays the true
    (10 x 400 + 5 x 500) / 15 = 433.33, never 411.11 (G0 dropped out, then
    re-averaged in at 400 over B1's blend)."""
    db = _db(400.0, cost_source="PURCHASE_INVOICE")
    _stamped_g0(db)
    _accept(db, _bill("B0", 10, 400, grn_id="G0", created_at="2026-11-01T00:00:00+00:00"))
    assert _avg(db) == 433.33
    row = db.products.find_one({})["cost_history"][-1]
    assert (row["held"], row["pre_s4"]) == (10.0, 10.0)


def test_a_stamped_piece_sold_after_the_stamp_is_trued_up_from_its_stamped_average():
    """As above; one G0 piece is sold at 433.33 after B1, then G0's bill
    lands at 460. The 9 held pieces add 9 x (460 - 400) = 540 to the pool:
    (14 x 433.33 + 540) / 14 = 471.90. The sold piece entered the pool at
    400, so its variance is 460 - 400 = 60 (never 460 - 433.33): pool +
    COGS + variance = every rupee billed."""
    from api.utils.event_time import utc_now_iso

    db = _db(400.0, cost_source="PURCHASE_INVOICE")
    _stamped_g0(db)
    db.stock_units.update_one({"stock_id": "G0-0"}, {"$set": {"status": "SOLD", "order_id": "O1",
                                                             "sold_at": utc_now_iso()}})
    db.orders.insert_one({"order_id": "O1", "items": [{"product_id": "P", "quantity": 1, "cost_at_sale": 433.33}]})
    _accept(db, _bill("B0", 10, 460, grn_id="G0", bill_date="2026-11-01",
                      created_at="2026-11-01T00:00:00+00:00"))
    assert _avg(db) == 471.9
    var = db.cogs_variances.find_one({})
    assert (var["amount"], var["stock_ids"]) == (60.0, ["G0-0"])


def test_damaged_pieces_are_written_off_never_in_the_basis():
    """10 shelf pieces at 100 and 10 DAMAGED (a write-off: off the books, its
    input credit reversed); a new receipt's bill, 10 at 200: (1000 + 2000) /
    20 = 150 -- the damaged pieces never dilute it to 133.33."""
    db = _db(100.0)
    _pieces(db, 10)
    _pieces(db, 10, status="DAMAGED", prefix="D")
    _receipt(db, "G", 10)
    _pieces(db, 10, "G", prefix="G")
    _accept(db, _bill("B", 10, 200, grn_id="G"))
    assert _avg(db) == 150.0


def _walk_in_blended_over(db, *, uncosted=False):
    """Before S4: 10 G1 pieces at 100; a walk-in bill BW (2026-10-15) for 1 at
    200 set the latest price 200; then PO bill B2 (10 at 100) blended over it
    to 152.38 -- cost_source_id names B2, not the walk-in bill."""
    _receipt(db, "G1", 10, po_id="PO1")
    _receipt(db, "GW", 1, grn_subtype="NO_PO")
    _receipt(db, "G2", 10, po_id="PO2")
    _pieces(db, 10, "G1", prefix="A")
    _pieces(db, 1, "GW", prefix="W")
    _pieces(db, 10, "G2", prefix="C")
    db.stock_units.update_many({"grn_id": {"$in": ["G1", "G2"]}}, {"$set": {"unit_cost": 100.0}})
    db.stock_units.update_many({"grn_id": "GW"}, {"$set": {"unit_cost": 200.0}})
    if uncosted:
        db.stock_units.update_one({"stock_id": "C20"}, {"$unset": {"unit_cost": ""}})
    db.vendor_bills.insert_many([
        {"bill_id": "BW", "invoice_id": "BW", "grn_id": "GW", "status": "PAID", "match_status": "MATCHED",
         "created_at": "2026-10-15T10:00:00+00:00", "lines": [{"product_id": "P", "qty": 1, "unit_price": 200}]},
        {"bill_id": "B2", "invoice_id": "B2", "grn_id": "G2", "status": "PAID", "match_status": "MATCHED",
         "created_at": "2026-10-16T10:00:00+00:00", "lines": [{"product_id": "P", "qty": 10, "unit_price": 100}]},
    ])


def test_the_backfill_finds_a_walk_in_latest_price_a_later_bill_blended_over():
    """Ruling (b): (10 x 100 + 200 + 10 x 100) / 21 = 104.76, although the
    product's cost source is the later PO bill."""
    from scripts import backfill_average_cost as bf

    db = _db(152.38, cost_source="PURCHASE_INVOICE", cost_source_id="B2")
    _walk_in_blended_over(db)
    (row,) = bf.plan(db)
    assert (row["fill"], row["recost"]) == (104.76, 152.38) and "walk-in" in row["reason"]


def test_the_backfill_flags_a_walk_in_latest_price_with_an_uncosted_piece():
    """A walk-in latest price with a held piece that has no cost: CHECK BY
    HAND, nothing filled; --apply leaves the average where it is."""
    from scripts import backfill_average_cost as bf

    for source in ("INV-W1", "B2"):  # the walk-in bill is the source / a later bill blended over it
        db = _db(200.0, cost_source="PURCHASE_INVOICE", cost_source_id=source)
        if source == "B2":
            _walk_in_blended_over(db, uncosted=True)
        else:
            _receipt(db, "G2", 2, grn_subtype="NO_PO")
            _pieces(db, 2, "G2")
            db.stock_units.update_one({"stock_id": "U0"}, {"$set": {"unit_cost": 200.0}})
            db.vendor_bills.insert_one({"bill_id": "INV-W1", "invoice_id": "INV-W1", "grn_id": "G2",
                                        "status": "PAID", "match_status": "MATCHED"})
        (row,) = bf.plan(db)
        assert row["fill"] is None and "walk-in" in row["check"]
        bf.apply(db, [row], "RUN6")
        assert _avg(db) == 200.0


def test_the_backfill_re_average_never_overwrites_a_bill_landing_after_the_dry_run():
    """The dry run read 200; an S4 bill moves the average to 180 before
    --apply: the stale re-average (109.09) is not written."""
    from scripts import backfill_average_cost as bf

    db = _db(200.0, cost_source="PURCHASE_INVOICE", cost_source_id="INV-W1")
    _receipt(db, "G1", 10)
    _receipt(db, "G2", 1, grn_subtype="NO_PO")
    _pieces(db, 10, "G1")
    _pieces(db, 1, "G2")
    db.stock_units.update_many({"grn_id": "G1"}, {"$set": {"unit_cost": 100.0}})
    db.stock_units.update_many({"grn_id": "G2"}, {"$set": {"unit_cost": 200.0}})
    db.vendor_bills.insert_one({"bill_id": "INV-W1", "invoice_id": "INV-W1", "grn_id": "G2",
                                "status": "PAID", "match_status": "MATCHED"})
    (row,) = bf.plan(db)
    assert row["fill"] == 109.09
    product_cost._move(db, "P", "bill:S4", lambda _p, _o: (180.0, {}), {"kind": "BILL", "source": "PURCHASE_INVOICE"})
    assert bf.apply(db, [row], "RUN7") == 0
    product = db.products.find_one({})
    assert product["cost_price"] == 180.0
    assert [h["key"] for h in product["cost_history"]] == ["bill:S4"]
