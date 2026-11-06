"""The stock a sale takes is bound where it is written, on every door that writes it.

Codex re-review of PR #1393, 2026-11-06, on the REAL Stock / Product / Order
repositories over mongomock:

  P1  the pre-claim check read a unit's shop and product, but the claim itself
      matched only stock_id + AVAILABLE. A real transfer landing between the two
      sold a unit at shop B onto a shop A bill. The claim now re-matches shop and
      product in the same write. Scheduled on the real transfer handlers below.
  P2  PUT /superadmin-edit (and the revised invoice) rebuilt the line list with
      no master / type / archive / stock rule: an unpaid bill grew by a physical
      line that took no unit. A line the edit ADDS or REPLACES now meets the till's
      rules and takes its unit; the admin's price authority is untouched.
  P2  a price-only correction dropped the line's stock_id. It stays.

Every outcome is read back off the collections (pieces, bills), not the reply.
No emoji (Windows cp1252).
"""
from __future__ import annotations

import asyncio
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

pytest.importorskip("mongomock")

from tests.test_pos_stock_identity import (  # noqa: E402,F401  (fixture import)
    ACC,
    ADMIN,
    FR,
    OTHER_STORE,
    SG,
    STAFF,
    STORE,
    SVC,
    _ledger,
    _line,
    _order_id,
    _sell,
    _sold_to,
    _token,
    shop,
)

PRIVILEGED = [["SUPERADMIN"], ["ADMIN"]]


def _unit(shop, sid):
    return shop["db"].get_collection("stock_units").find_one({"stock_id": sid}, {"_id": 0})


def _order(shop, oid):
    return shop["db"].get_collection("orders").find_one({"order_id": oid}, {"_id": 0})


def _saved_bill(client, shop, items, status="CONFIRMED", **stamp):
    """A bill sold at the till, moved to `status` with no tax invoice."""
    r = _sell(client, items)
    assert r.status_code in (200, 201), r.text
    oid = _order_id(r)
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid}, {"$set": {"status": status, **stamp}}
    )
    return oid


def _edit(client, oid, lines, roles=ADMIN, route="superadmin-edit", **body):
    return client.put(
        f"/api/v1/orders/{oid}/{route}",
        json={"reason": "correcting this bill", "items": lines, **body},
        headers=_token(roles),
    )


def _as_sent(line, **over):
    """A stored line as the edit screen sends it back: the fields it knows."""
    keys = ("item_id", "item_type", "product_id", "product_name", "sku", "brand",
            "category", "hsn_code", "quantity", "unit_price", "discount_percent")
    return {**{k: line.get(k) for k in keys}, **over}


def _refused_unchanged(shop, resp, before, code):
    assert resp.status_code == code, resp.text
    assert isinstance(resp.json().get("detail"), str), resp.text
    assert _ledger(shop) == before, "a refused edit must leave the shelf untouched"


# ---------------------------------------------------------------------------
# P1 -- the claim itself re-matches shop and product
# ---------------------------------------------------------------------------


def test_repository_claim_refuses_another_shop_and_another_product(shop):
    from database.repositories.product_repository import StockRepository

    repo = StockRepository(shop["db"].get_collection("stock_units"))
    assert repo.mark_sold("SU-FR-A", "ORD-1", OTHER_STORE, [FR]) is False
    assert repo.mark_sold("SU-FR-A", "ORD-1", STORE, [ACC]) is False
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"
    assert repo.mark_sold("SU-FR-A", "ORD-1", STORE, [FR]) is True
    assert _unit(shop, "SU-FR-A")["order_id"] == "ORD-1"


def _before_the_claim(monkeypatch, sid, action):
    """Run `action` after the gate has passed, immediately before the real claim
    of `sid`. Whatever the claim's arguments are, they reach the real write."""
    from database.repositories.product_repository import StockRepository

    real = StockRepository.mark_sold
    done = []

    def scheduled(self, stock_id, order_id, *args, **kwargs):
        if stock_id == sid and not done:
            done.append(True)
            action()
        return real(self, stock_id, order_id, *args, **kwargs)

    monkeypatch.setattr(StockRepository, "mark_sold", scheduled)
    return done


def test_unit_moved_to_another_shop_after_the_check_is_not_sold(client, shop, monkeypatch):
    units = shop["db"].get_collection("stock_units")
    done = _before_the_claim(
        monkeypatch, "SU-FR-B",
        lambda: units.update_one({"stock_id": "SU-FR-B"}, {"$set": {"store_id": OTHER_STORE}}),
    )
    r = _sell(client, [_line(FR, stock_id="SU-FR-B")])
    assert done, "the schedule never reached the claim"
    assert r.status_code == 409, r.text
    assert _ledger(shop)[1] == [], "no bill for a unit this shop no longer holds"
    unit = _unit(shop, "SU-FR-B")
    assert (unit["status"], unit.get("order_id"), unit["store_id"]) == (
        "AVAILABLE", None, OTHER_STORE,
    )


def test_unit_remapped_to_another_product_after_the_check_is_not_sold(client, shop, monkeypatch):
    units = shop["db"].get_collection("stock_units")
    done = _before_the_claim(
        monkeypatch, "SU-FR-B",
        lambda: units.update_one({"stock_id": "SU-FR-B"}, {"$set": {"product_id": ACC}}),
    )
    r = _sell(client, [_line(FR, stock_id="SU-FR-B")])
    assert done and r.status_code == 409, r.text
    assert _ledger(shop)[1] == []
    assert _unit(shop, "SU-FR-B")["status"] == "AVAILABLE"


def test_add_item_door_claim_is_bound_too(client, shop, monkeypatch):
    oid = _saved_bill(client, shop, [_line(ACC)], status="DRAFT")
    units = shop["db"].get_collection("stock_units")
    done = _before_the_claim(
        monkeypatch, "SU-FR-B",
        lambda: units.update_one({"stock_id": "SU-FR-B"}, {"$set": {"store_id": OTHER_STORE}}),
    )
    r = client.post(
        f"/api/v1/orders/{oid}/items",
        json=_line(FR, stock_id="SU-FR-B"),
        headers=_token(STAFF),
    )
    assert done and r.status_code == 409, r.text
    assert len(_order(shop, oid)["items"]) == 1
    assert _unit(shop, "SU-FR-B")["status"] == "AVAILABLE"


def test_a_failed_claim_gives_back_the_units_this_bill_already_took(client, shop, monkeypatch):
    units = shop["db"].get_collection("stock_units")
    done = _before_the_claim(
        monkeypatch, "SU-FR-B",
        lambda: units.update_one({"stock_id": "SU-FR-B"}, {"$set": {"store_id": OTHER_STORE}}),
    )
    r = _sell(client, [_line(FR, stock_id="SU-FR-A"), _line(FR, stock_id="SU-FR-B")])
    assert done and r.status_code == 409, r.text
    assert _ledger(shop)[1] == []
    unit = _unit(shop, "SU-FR-A")
    assert (unit["status"], unit.get("order_id")) == ("AVAILABLE", None)


def test_alias_unit_still_sells_through_the_bound_claim(client, shop):
    r = _sell(client, [_line(FR, stock_id="SU-FR-LEGACY")])
    assert r.status_code in (200, 201), r.text
    assert _sold_to(shop, _order_id(r)) == ["SU-FR-LEGACY"]


def test_unit_lookup_fault_is_try_again_not_missing_stock(client, shop, monkeypatch):
    """BaseRepository.find_by_id swallows a read fault: the till said "not in
    the stock records" for a unit that is there (Codex fault probe)."""
    real_find = type(shop["db"].get_collection("stock_units")).find_one

    def fails(coll, flt=None, *a, **k):
        if coll.name == "stock_units" and (flt or {}).get("stock_id") == "SU-FR-B":
            raise TimeoutError("lookup failed")
        return real_find(coll, flt, *a, **k)

    monkeypatch.setattr(type(shop["db"].get_collection("stock_units")), "find_one", fails)
    before = _ledger(shop)
    r = _sell(client, [_line(FR, stock_id="SU-FR-B")])
    _refused_unchanged(shop, r, before, 503)


# ---------------------------------------------------------------------------
# P1 -- the same schedule on the real transfer handlers
# ---------------------------------------------------------------------------


class _Conn:
    is_connected = True

    def __init__(self, db):
        self.db = db

    def __getattr__(self, name):
        return self.db[name]


def _shop_doc(sid, code):
    gstin = "20AAACZ1111A1ZH"
    return {
        "store_id": sid, "store_name": sid, "name": sid, "entity_id": "ENT-Z", "gstin": gstin,
        "state": "Jharkhand", "state_code": "20", "address": "Road", "city": "Dhanbad",
        "pincode": "826001", "store_type": "PHYSICAL", "invoice_code": code,
    }


@pytest.fixture
def real_transfer(shop, monkeypatch):
    """Create + pack SU-FR-B on the real handlers; returns the ship + receive."""
    from api import dependencies as deps
    from api.routers import transfers as tr

    db = shop["db"]
    db["entities"].insert_one({"entity_id": "ENT-Z", "legal_name": "Zed Opticals", "gstins": [
        {"gstin": "20AAACZ1111A1ZH", "state_code": "20", "is_primary": True}]})
    db["stores"].insert_many([_shop_doc(STORE, "AAA01"), _shop_doc(OTHER_STORE, "BBB01")])
    monkeypatch.setattr(deps, "get_db", lambda: _Conn(db))
    monkeypatch.setattr(tr, "_writeback_units_left", lambda *a, **k: None)
    tr.STOCK_TRANSFERS.clear()
    admin = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"],
             "store_ids": [], "active_store_id": None}
    body = tr.TransferInput(
        transfer_type="store_to_store",
        from_location_id=STORE, from_location_name=STORE,
        to_location_id=OTHER_STORE, to_location_name=OTHER_STORE,
        items=[tr.TransferItemInput(product_id=FR, sku="FR-CARRERA-CA1-54",
                                    product_name="Carrera", quantity_requested=1)],
    )
    tid = asyncio.run(tr.create_transfer(body, admin))["transfer"]["id"]
    asyncio.run(tr.pack_transfer(tid, tr.TransferPack(barcodes=["BC-SU-FR-B"]), admin))
    assert _unit(shop, "SU-FR-B")["status"] == "AVAILABLE"  # packing moves nothing

    def ship_and_receive():
        asyncio.run(tr.ship_transfer(tid, "T-1", None, "DTDC", False, admin))
        asyncio.run(tr.receive_transfer(tid, tr.TransferPieceReceive(barcode="BC-SU-FR-B"), admin))

    return ship_and_receive


def test_real_transfer_between_the_check_and_the_claim_cannot_sell_the_moved_unit(
    client, shop, real_transfer, monkeypatch
):
    def land_it():
        # asyncio.run needs its own thread: the sale is on the event loop here.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(real_transfer).result()
        unit = _unit(shop, "SU-FR-B")
        assert (unit["status"], unit["store_id"]) == ("AVAILABLE", OTHER_STORE)

    done = _before_the_claim(monkeypatch, "SU-FR-B", land_it)
    r = _sell(client, [_line(FR, stock_id="SU-FR-B")])
    assert done, "the schedule never reached the claim"
    assert r.status_code == 409, r.text
    assert _ledger(shop)[1] == [], "no shop A bill for a unit now at shop B"
    unit = _unit(shop, "SU-FR-B")
    assert (unit["status"], unit.get("order_id"), unit["store_id"]) == (
        "AVAILABLE", None, OTHER_STORE,
    )


def test_a_packed_unit_still_sells_at_its_shop_and_the_ship_is_refused(
    client, shop, real_transfer
):
    """The other order of the same race: the sale wins, the box cannot ship it."""
    from fastapi import HTTPException

    r = _sell(client, [_line(FR, stock_id="SU-FR-B")])
    assert r.status_code in (200, 201), r.text
    with pytest.raises(HTTPException) as exc:
        real_transfer()
    assert exc.value.status_code == 409
    assert _sold_to(shop, _order_id(r)) == ["SU-FR-B"]
    assert _unit(shop, "SU-FR-B")["store_id"] == STORE


# ---------------------------------------------------------------------------
# P2 -- price-only correction keeps the unit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("roles", PRIVILEGED)
def test_price_correction_keeps_the_unit_on_the_line(client, shop, roles):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    line = _order(shop, oid)["items"][0]
    before = _ledger(shop)[0]
    r = _edit(client, oid, [_as_sent(line, unit_price=4500.0)], roles)
    assert r.status_code == 200, r.text
    saved = _order(shop, oid)["items"][0]
    assert saved["stock_id"] == "SU-FR-A", "the price correction lost the unit"
    assert saved["unit_price"] == 4500.0 and saved["item_id"] == line["item_id"]
    assert _ledger(shop)[0] == before


def test_second_correction_keeps_it_and_the_line_sent_whole_is_the_same(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    line = _order(shop, oid)["items"][0]
    assert _edit(client, oid, [_as_sent(line, unit_price=4500.0)]).status_code == 200
    whole = {k: v for k, v in _order(shop, oid)["items"][0].items()
             if k in ("item_id", "item_type", "product_id", "quantity", "unit_price", "stock_id")}
    assert _edit(client, oid, [{**whole, "unit_price": 4400.0}]).status_code == 200
    assert _order(shop, oid)["items"][0]["stock_id"] == "SU-FR-A"
    assert _sold_to(shop, oid) == ["SU-FR-A"]


def test_cancel_after_a_price_correction_gives_back_that_exact_unit(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-B")])
    line = _order(shop, oid)["items"][0]
    assert _edit(client, oid, [_as_sent(line, unit_price=4500.0)]).status_code == 200
    r = client.post(
        f"/api/v1/orders/{oid}/cancel", params={"reason": "customer left"}, headers=_token(ADMIN)
    )
    assert r.status_code == 200, r.text
    unit = _unit(shop, "SU-FR-B")
    assert (unit["status"], unit.get("order_id")) == ("AVAILABLE", None)


def test_price_correction_on_a_since_archived_product_still_works(client, shop):
    """History: the bill may be corrected after the master is switched off."""
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    shop["db"].get_collection("products").update_one(
        {"product_id": FR}, {"$set": {"is_active": False, "deleted_at": "2026-11-01"}}
    )
    line = _order(shop, oid)["items"][0]
    r = _edit(client, oid, [_as_sent(line, unit_price=4500.0)])
    assert r.status_code == 200, r.text
    assert _order(shop, oid)["items"][0]["stock_id"] == "SU-FR-A"


def _as_lens_line(shop, oid):
    """A held lens-cell line (the till needs an Rx to bill one; the stock
    question here starts from the stored line)."""
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid},
        {"$set": {"items.0.item_type": "LENS", "items.0.lens_line_id": "CELL-1",
                  "items.0.line_index": 0}},
    )
    return _order(shop, oid)["items"][0]


def test_price_correction_keeps_the_lens_cell_reference(client, shop):
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_lens_line(shop, oid)
    r = _edit(client, oid, [_as_sent(line, unit_price=150.0)])
    assert r.status_code == 200, r.text
    saved = _order(shop, oid)["items"][0]
    assert (saved["lens_line_id"], saved["line_index"]) == ("CELL-1", 0)


def test_removing_a_lens_line_releases_its_cell(client, shop, monkeypatch):
    from api.services import lens_stock_hook

    oid = _saved_bill(client, shop, [_line(SVC), _line(SVC)])
    line = _as_lens_line(shop, oid)
    released = []

    async def release(*, order_item, order_id, line_index, store_id, user):
        released.append((order_item.get("lens_line_id"), order_id, line_index))

    monkeypatch.setattr(lens_stock_hook, "release_for_cancel", release)
    r = _edit(client, oid, [_as_sent(_order(shop, oid)["items"][1])])
    assert r.status_code == 200, r.text
    assert ("CELL-1", oid, line["item_id"]) in released


def test_customer_only_edit_touches_no_line(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    before = _ledger(shop)
    r = client.put(
        f"/api/v1/orders/{oid}/superadmin-edit",
        json={"reason": "fixing the customer", "notes": "called back"},
        headers=_token(ADMIN),
    )
    assert r.status_code == 200, r.text
    assert _ledger(shop) == before
    assert _order(shop, oid)["items"][0]["stock_id"] == "SU-FR-A"


# ---------------------------------------------------------------------------
# P2 -- a line the edit ADDS or REPLACES meets the till's rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("roles", PRIVILEGED)
def test_new_zero_stock_line_is_refused(client, shop, roles):
    oid = _saved_bill(client, shop, [_line(SVC)])
    stored = _order(shop, oid)
    before = _ledger(shop)
    r = _edit(client, oid, [_as_sent(stored["items"][0]), _line(SG)], roles)
    _refused_unchanged(shop, r, before, 409)
    assert _order(shop, oid)["grand_total"] == stored["grand_total"]


def test_new_line_naming_a_fabricated_unit_is_refused(client, shop):
    oid = _saved_bill(client, shop, [_line(SVC)])
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(FR, stock_id="SU-IMPOSSIBLE")])
    _refused_unchanged(shop, r, before, 409)


def test_new_line_naming_another_shops_unit_is_refused(client, shop):
    oid = _saved_bill(client, shop, [_line(SVC)])
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(FR, stock_id="SU-FR-OTHER")])
    _refused_unchanged(shop, r, before, 409)


@pytest.mark.parametrize("flag", [{"is_active": False}, {"deleted_at": "2026-11-01"}])
def test_new_archived_line_is_refused(client, shop, flag):
    oid = _saved_bill(client, shop, [_line(SVC)])
    shop["db"].get_collection("products").update_one({"product_id": FR}, {"$set": flag})
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(FR, stock_id="SU-FR-A")])
    _refused_unchanged(shop, r, before, 400)


@pytest.mark.parametrize("spoof", ["SERVICE", "EYE_TEST", "LENS"])
def test_new_stocked_master_relabelled_to_skip_stock_is_refused(client, shop, spoof):
    oid = _saved_bill(client, shop, [_line(SVC)])
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(SG, item_type=spoof)])
    _refused_unchanged(shop, r, before, 400)


def test_existing_physical_line_relabelled_service_is_refused(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0], item_type="SERVICE")
    r = _edit(client, oid, [line])
    _refused_unchanged(shop, r, before, 400)


def test_a_stored_line_sent_twice_needs_its_own_unit(client, shop):
    """One item_id twice must not bill two pieces on one unit."""
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, line])
    assert r.status_code == 200, r.text
    items = _order(shop, oid)["items"]
    assert len({ln["item_id"] for ln in items}) == 2
    assert len(_sold_to(shop, oid)) == 2


@pytest.mark.parametrize("roles", PRIVILEGED)
def test_new_line_takes_the_unit_it_names(client, shop, roles):
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(FR, stock_id="SU-FR-B")], roles)
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == ["SU-FR-B"]
    new = _order(shop, oid)["items"][1]
    assert new["stock_id"] == "SU-FR-B" and new["product_id"] == FR


def test_new_line_named_by_sku_is_stored_under_the_master_id(client, shop):
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(FR, product_id="FR-CARRERA-CA1-54")])
    assert r.status_code == 200, r.text
    assert _order(shop, oid)["items"][1]["product_id"] == FR
    assert len(_sold_to(shop, oid)) == 1


def test_new_line_with_no_named_unit_takes_one_off_the_shelf(client, shop):
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(ACC)])
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == ["SU-ACC-A"]


def test_admin_price_on_a_new_line_is_still_the_admins(client, shop):
    """The till's price rules are not re-applied: authority is unchanged."""
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(FR, unit_price=1.0)])
    assert r.status_code == 200, r.text
    assert _order(shop, oid)["items"][1]["unit_price"] == 1.0


def test_true_service_and_free_text_lines_take_no_stock(client, shop):
    oid = _saved_bill(client, shop, [_line(SVC)])
    before = _ledger(shop)[0]
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [
        line,
        _line(SVC, unit_price=150.0),
        {"item_type": "SERVICE", "product_name": "Adjustment", "unit_price": 50.0},
        {"item_type": "FRAME", "product_id": "custom-1", "product_name": "Made to order",
         "unit_price": 300.0},
    ])
    assert r.status_code == 200, r.text
    assert _ledger(shop)[0] == before


def test_replacing_a_line_swaps_the_units(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    stored = _order(shop, oid)["items"][0]
    r = _edit(client, oid, [{**_line(ACC), "item_id": stored["item_id"]}])
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == ["SU-ACC-A"]
    old = _unit(shop, "SU-FR-A")
    assert (old["status"], old.get("order_id")) == ("AVAILABLE", None)
    assert _order(shop, oid)["items"][0].get("stock_id") in (None, "")


def test_swapping_the_named_unit_of_the_same_product(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    line = _as_sent(_order(shop, oid)["items"][0], stock_id="SU-FR-B")
    r = _edit(client, oid, [line])
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == ["SU-FR-B"]
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"
    assert _order(shop, oid)["items"][0]["stock_id"] == "SU-FR-B"


def _by_id(line, **over):
    """The least a caller may send for a stored line: its id, type, qty, price."""
    keys = ("item_id", "item_type", "quantity", "unit_price")
    return {**{k: line[k] for k in keys}, **over}


@pytest.mark.parametrize("route, body", [
    ("superadmin-edit", {}),
    ("superadmin-invoice-change", {"mode": "REVISED_INVOICE"}),
])
def test_a_line_sent_by_its_id_alone_is_still_the_stored_line(client, shop, invoiced, route, body):
    """Omitting product_id is not a swap: it used to send the unit back to the
    shelf while the bill went on charging for the piece."""
    if route == "superadmin-edit":
        oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    else:
        oid = invoiced([_line(FR, stock_id="SU-FR-A")])
    stored = _order(shop, oid)["items"][0]
    before = _ledger(shop)[0]
    r = _edit(client, oid, [_by_id(stored, unit_price=4500.0)], route=route, **body)
    assert r.status_code == 200, r.text
    saved = _order(shop, oid)["items"][0]
    assert (saved["product_id"], saved["stock_id"]) == (stored["product_id"], "SU-FR-A")
    assert saved["unit_price"] == 4500.0
    assert _ledger(shop)[0] == before


def test_raising_the_quantity_by_id_alone_still_takes_a_unit(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    r = _edit(client, oid, [_by_id(_order(shop, oid)["items"][0], quantity=2)])
    assert r.status_code == 200, r.text
    assert "SU-FR-A" in _sold_to(shop, oid) and len(_sold_to(shop, oid)) == 2


def test_raising_the_quantity_by_id_alone_past_the_shelf_is_refused(client, shop):
    oid = _saved_bill(client, shop, [_line(ACC)])
    before = _ledger(shop)
    r = _edit(client, oid, [_by_id(_order(shop, oid)["items"][0], quantity=2)])
    _refused_unchanged(shop, r, before, 409)


def test_swapping_the_named_unit_by_id_alone_is_the_same_product(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    r = _edit(client, oid, [_by_id(_order(shop, oid)["items"][0], stock_id="SU-FR-B")])
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == ["SU-FR-B"]
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"


def test_turning_a_line_into_a_free_text_service_is_still_a_replacement(client, shop):
    """Control: a different KIND of goods with no product is a real swap."""
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    r = _edit(client, oid, [_by_id(_order(shop, oid)["items"][0], item_type="SERVICE")])
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == []
    assert _order(shop, oid)["items"][0].get("product_id") in (None, "")


def test_refused_replacement_keeps_the_old_unit(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    stored = _order(shop, oid)["items"][0]
    before = _ledger(shop)
    r = _edit(client, oid, [{**_line(SG), "item_id": stored["item_id"]}])
    _refused_unchanged(shop, r, before, 409)
    assert _order(shop, oid)["items"][0]["stock_id"] == "SU-FR-A"


def test_removing_a_line_gives_its_unit_back(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A"), _line(SVC)])
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    r = _edit(client, oid, [_as_sent(keep)])
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == []
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"


def test_raising_the_quantity_takes_one_more_and_keeps_the_named_unit(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    line = _as_sent(_order(shop, oid)["items"][0], quantity=2)
    r = _edit(client, oid, [line])
    assert r.status_code == 200, r.text
    assert "SU-FR-A" in _sold_to(shop, oid) and len(_sold_to(shop, oid)) == 2
    assert _order(shop, oid)["items"][0]["stock_id"] == "SU-FR-A"


def test_raising_the_quantity_past_the_shelf_is_refused(client, shop):
    oid = _saved_bill(client, shop, [_line(ACC)])
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0], quantity=2)
    r = _edit(client, oid, [line])
    _refused_unchanged(shop, r, before, 409)


def test_lowering_the_quantity_gives_back_the_extra_not_the_named_unit(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A", quantity=2)])
    assert len(_sold_to(shop, oid)) == 2
    line = _as_sent(_order(shop, oid)["items"][0], quantity=1)
    r = _edit(client, oid, [line])
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == ["SU-FR-A"]


def test_the_same_edit_pressed_twice_takes_one_unit(client, shop):
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    for _ in range(2):
        r = _edit(client, oid, [line, _line(FR)])
        assert r.status_code == 200, r.text
    assert len(_sold_to(shop, oid)) == 1
    assert len(_order(shop, oid)["items"]) == 2


def test_edit_that_loses_the_order_gives_its_new_unit_back(client, shop, monkeypatch):
    from database.repositories.order_repository import OrderRepository

    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    monkeypatch.setattr(OrderRepository, "update_if", lambda self, *a, **k: False)
    before = _ledger(shop)
    r = _edit(client, oid, [line, _line(FR, stock_id="SU-FR-A")])
    _refused_unchanged(shop, r, before, 409)


def test_two_edits_read_together_only_the_first_lands(client, shop, monkeypatch):
    """Two admins, one bill: the loser's unit must not stay SOLD with no line."""
    from database.repositories.order_repository import OrderRepository

    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    orders = shop["db"].get_collection("orders")
    real = OrderRepository.update_if

    def rival_first(self, order_id, expected, data, *a, **k):
        orders.update_one({"order_id": order_id},
                          {"$push": {"items": {"item_id": "rival", "item_type": "SERVICE"}}})
        return real(self, order_id, expected, data, *a, **k)

    monkeypatch.setattr(OrderRepository, "update_if", rival_first)
    r = _edit(client, oid, [line, _line(FR, stock_id="SU-FR-A")])
    assert r.status_code == 409, r.text
    unit = _unit(shop, "SU-FR-A")
    assert (unit["status"], unit.get("order_id")) == ("AVAILABLE", None)


def test_write_that_raised_before_landing_gives_the_unit_back(client, shop, monkeypatch):
    from database.repositories.order_repository import OrderRepository

    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])

    def down(self, *a, **k):
        raise TimeoutError("write failed")

    monkeypatch.setattr(OrderRepository, "update_if", down)
    client_no_raise = type(client)(client.app, raise_server_exceptions=False)
    r = client_no_raise.put(
        f"/api/v1/orders/{oid}/superadmin-edit",
        json={"reason": "correcting this bill", "items": [line, _line(FR, stock_id="SU-FR-A")]},
        headers=_token(ADMIN),
    )
    assert r.status_code >= 500, r.text
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"


def test_write_that_landed_but_lost_its_reply_keeps_the_unit(client, shop, monkeypatch):
    from database.repositories.order_repository import OrderRepository

    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    real = OrderRepository.update_if

    def lost_reply(self, *a, **k):
        real(self, *a, **k)
        raise TimeoutError("reply lost")

    monkeypatch.setattr(OrderRepository, "update_if", lost_reply)
    client_no_raise = type(client)(client.app, raise_server_exceptions=False)
    r = client_no_raise.put(
        f"/api/v1/orders/{oid}/superadmin-edit",
        json={"reason": "correcting this bill", "items": [line, _line(FR, stock_id="SU-FR-A")]},
        headers=_token(ADMIN),
    )
    assert r.status_code >= 500, r.text
    assert len(_order(shop, oid)["items"]) == 2
    assert _sold_to(shop, oid) == ["SU-FR-A"], "the saved line must keep its unit"


def test_ordinary_roles_still_cannot_edit(client, shop):
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")])
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0], unit_price=1.0)
    for roles in (["SALES_STAFF"], ["AREA_MANAGER"]):
        r = _edit(client, oid, [line], roles)
        _refused_unchanged(shop, r, before, 403)


# ---------------------------------------------------------------------------
# The post-invoice door shares the rule
# ---------------------------------------------------------------------------


@pytest.fixture
def invoiced(client, shop, monkeypatch):
    """A saved bill with a tax invoice issued today (the revised-invoice month)."""
    from api.routers import finance, orders as om, returns
    from api.utils.event_time import utc_now_iso
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.order_repository import OrderRepository

    db = shop["db"]
    db["stores"].insert_one({"store_id": STORE, "store_code": STORE, "is_active": True})
    monkeypatch.setattr(om, "_get_db", lambda: db)
    monkeypatch.setattr(finance, "_get_db", lambda: db, raising=False)
    monkeypatch.setattr(returns, "_get_db", lambda: db, raising=False)
    monkeypatch.setattr(returns, "get_customer_repository",
                        lambda: CustomerRepository(db.get_collection("customers")))
    monkeypatch.setattr(
        OrderRepository, "next_invoice_number", lambda self, *a, **k: "BV-INV-REV-1"
    )

    def make(items):
        return _saved_bill(
            client, shop, items, invoice_number="BV-INV-1", invoice_date=utc_now_iso(),
        )

    return make


def test_note_keeps_the_lines_and_moves_no_stock(client, shop, invoiced):
    oid = invoiced([_line(FR, stock_id="SU-FR-A")])
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0], unit_price=4500.0)
    r = _edit(client, oid, [line], route="superadmin-invoice-change", mode="CREDIT_NOTE")
    assert r.status_code == 200, r.text
    assert _ledger(shop) == before
    assert _order(shop, oid)["items"][0]["stock_id"] == "SU-FR-A"


def test_note_cannot_bill_new_goods(client, shop, invoiced):
    oid = invoiced([_line(SVC)])
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(ACC)], route="superadmin-invoice-change",
              mode="CREDIT_NOTE")
    _refused_unchanged(shop, r, before, 409)


def test_note_can_still_add_a_service_charge(client, shop, invoiced):
    oid = invoiced([_line(SVC)])
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(SVC, unit_price=150.0)],
              route="superadmin-invoice-change", mode="CREDIT_NOTE")
    assert r.status_code == 200, r.text
    assert _ledger(shop) == before


@pytest.fixture
def no_product_table(monkeypatch):
    """Any read of the product table by the edit is a failure."""
    from api.routers.orders import admin_edit

    def unreachable():
        raise AssertionError("a note that adds no goods read the product table")

    monkeypatch.setattr(admin_edit, "get_product_repository", unreachable, raising=False)


@pytest.mark.parametrize("send", [
    lambda line: _as_sent(line, unit_price=line["unit_price"] - 500.0),
    lambda line: _by_id(line, unit_price=line["unit_price"] - 500.0),
], ids=["whole-line", "by-id"])
def test_note_correcting_a_stored_line_needs_no_product_read(
    client, shop, invoiced, no_product_table, send
):
    oid = invoiced([_line(FR, stock_id="SU-FR-A")])
    before = _ledger(shop)
    r = _edit(client, oid, [send(_order(shop, oid)["items"][0])],
              route="superadmin-invoice-change", mode="CREDIT_NOTE")
    assert r.status_code == 200, r.text
    assert (r.json()["note_type"], r.json()["amount"]) == ("CREDIT_NOTE", 500.0)
    assert _ledger(shop) == before
    assert _order(shop, oid)["items"][0]["stock_id"] == "SU-FR-A"


def test_note_with_no_lines_sent_needs_no_product_read(client, shop, invoiced, no_product_table):
    oid = invoiced([_line(FR, stock_id="SU-FR-A")])
    r = client.put(f"/api/v1/orders/{oid}/superadmin-invoice-change", headers=_token(ADMIN),
                   json={"reason": "correcting this bill", "mode": "CREDIT_NOTE",
                         "cart_discount_percent": 10.0})
    assert r.status_code == 200, r.text
    assert r.json()["note_type"] == "CREDIT_NOTE"


def test_note_on_an_old_line_with_no_type_is_still_a_correction(
    client, shop, invoiced, no_product_table
):
    """The edit screen sends FRAME for a stored line with no item_type."""
    oid = invoiced([_line(FR, stock_id="SU-FR-A")])
    stored = _order(shop, oid)["items"][0]
    sent = _as_sent(stored, item_type="FRAME", unit_price=stored["unit_price"] - 500.0)
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid}, {"$unset": {"items.0.item_type": ""}}
    )
    r = _edit(client, oid, [sent], route="superadmin-invoice-change", mode="CREDIT_NOTE")
    assert r.status_code == 200, r.text
    assert (r.json()["note_type"], r.json()["amount"]) == ("CREDIT_NOTE", 500.0)


def test_note_raising_a_quantity_is_new_goods_and_is_refused(client, shop, invoiced):
    oid = invoiced([_line(FR, stock_id="SU-FR-A")])
    before = _ledger(shop)
    r = _edit(client, oid, [_by_id(_order(shop, oid)["items"][0], quantity=2)],
              route="superadmin-invoice-change", mode="CREDIT_NOTE")
    _refused_unchanged(shop, r, before, 409)


def test_revised_invoice_refuses_a_new_zero_stock_line_and_mints_no_number(
    client, shop, invoiced, monkeypatch
):
    from database.repositories.order_repository import OrderRepository

    oid = invoiced([_line(SVC)])
    minted = []
    monkeypatch.setattr(OrderRepository, "next_invoice_number",
                        lambda self, *a, **k: minted.append(1) or "BV-INV-REV-1")
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(SG)], route="superadmin-invoice-change",
              mode="REVISED_INVOICE")
    _refused_unchanged(shop, r, before, 409)
    assert not minted, "a refused correction must not burn an invoice number"


def test_revised_invoice_with_a_new_line_takes_its_unit_and_keeps_the_old(
    client, shop, invoiced
):
    oid = invoiced([_line(FR, stock_id="SU-FR-A")])
    line = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [line, _line(FR, stock_id="SU-FR-B")],
              route="superadmin-invoice-change", mode="REVISED_INVOICE")
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == ["SU-FR-A", "SU-FR-B"]
    items = _order(shop, oid)["items"]
    assert [ln["stock_id"] for ln in items] == ["SU-FR-A", "SU-FR-B"]


def test_revised_invoice_price_correction_keeps_the_unit(client, shop, invoiced):
    oid = invoiced([_line(FR, stock_id="SU-FR-A")])
    line = _as_sent(_order(shop, oid)["items"][0], unit_price=4500.0)
    r = _edit(client, oid, [line], route="superadmin-invoice-change", mode="REVISED_INVOICE")
    assert r.status_code == 200, r.text
    assert _order(shop, oid)["items"][0]["stock_id"] == "SU-FR-A"


def test_revised_invoice_refused_at_the_write_gives_the_new_unit_back(
    client, shop, invoiced, monkeypatch
):
    from database.repositories.order_repository import OrderRepository

    oid = invoiced([_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    monkeypatch.setattr(OrderRepository, "update_if", lambda self, *a, **k: False)
    before = _ledger(shop)
    r = _edit(client, oid, [line, _line(FR, stock_id="SU-FR-A")],
              route="superadmin-invoice-change", mode="REVISED_INVOICE")
    _refused_unchanged(shop, r, before, 409)


def test_revised_invoice_on_a_delivered_bill_never_shelves_what_the_customer_holds(
    client, shop, invoiced
):
    oid = invoiced([_line(FR, stock_id="SU-FR-A"), _line(SVC)])
    shop["db"].get_collection("orders").update_one({"order_id": oid},
                                                   {"$set": {"status": "DELIVERED"}})
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    r = _edit(client, oid, [_as_sent(keep)], route="superadmin-invoice-change",
              mode="REVISED_INVOICE")
    assert r.status_code == 200, r.text
    assert _unit(shop, "SU-FR-A")["status"] == "SOLD"
