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


# ---------------------------------------------------------------------------
# Review of c67e0dcc -- goods that left stay gone
# ---------------------------------------------------------------------------


def _moved_online(monkeypatch):
    """What the website is told moved, line by line."""
    from api.services import online_stock_writeback

    told = []
    monkeypatch.setattr(
        online_stock_writeback, "writeback_after_sale",
        lambda _db, lines, _store: told.extend(lines),
    )
    return told


@pytest.mark.parametrize("status", ["SHIPPED", "DELIVERED", "REFUNDED", "VOID"])
def test_revised_invoice_never_shelves_goods_that_left(
    client, shop, invoiced, monkeypatch, status
):
    """The parcel holds the piece: reading AVAILABLE would sell it twice, and
    the website would be told it is back."""
    oid = invoiced([_line(FR, stock_id="SU-FR-A"), _line(SVC)])
    told = _moved_online(monkeypatch)
    shop["db"].get_collection("orders").update_one({"order_id": oid}, {"$set": {"status": status}})
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    r = _edit(client, oid, [_as_sent(keep)], route="superadmin-invoice-change",
              mode="REVISED_INVOICE")
    assert r.status_code == 200, r.text
    unit = _unit(shop, "SU-FR-A")
    assert (unit["status"], unit.get("order_id")) == ("SOLD", oid)
    assert told == [], "a unit that did not move is not news for the website"


def test_lowering_the_quantity_on_a_shipped_bill_shelves_nothing(
    client, shop, invoiced, monkeypatch
):
    oid = invoiced([_line(FR, stock_id="SU-FR-A", quantity=2)])
    told = _moved_online(monkeypatch)
    shop["db"].get_collection("orders").update_one({"order_id": oid},
                                                   {"$set": {"status": "SHIPPED"}})
    held = _sold_to(shop, oid)
    assert len(held) == 2
    line = _as_sent(_order(shop, oid)["items"][0], quantity=1)
    r = _edit(client, oid, [line], route="superadmin-invoice-change", mode="REVISED_INVOICE")
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == held
    assert told == []


def test_replacing_a_line_on_a_shipped_bill_keeps_the_old_and_takes_the_new(
    client, shop, invoiced, monkeypatch
):
    oid = invoiced([_line(FR, stock_id="SU-FR-A")])
    told = _moved_online(monkeypatch)
    shop["db"].get_collection("orders").update_one({"order_id": oid},
                                                   {"$set": {"status": "SHIPPED"}})
    stored = _order(shop, oid)["items"][0]
    r = _edit(client, oid, [{**_line(FR, stock_id="SU-FR-B"), "item_id": stored["item_id"]}],
              route="superadmin-invoice-change", mode="REVISED_INVOICE")
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == ["SU-FR-A", "SU-FR-B"]
    assert [ln.get("stock_id") for ln in told] == ["SU-FR-B"], "only the new piece moved"


@pytest.mark.parametrize("status", ["CONFIRMED", "PROCESSING", "READY"])
def test_revised_invoice_still_shelves_goods_the_shop_holds(
    client, shop, invoiced, monkeypatch, status
):
    oid = invoiced([_line(FR, stock_id="SU-FR-A"), _line(SVC)])
    told = _moved_online(monkeypatch)
    shop["db"].get_collection("orders").update_one({"order_id": oid}, {"$set": {"status": status}})
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    r = _edit(client, oid, [_as_sent(keep)], route="superadmin-invoice-change",
              mode="REVISED_INVOICE")
    assert r.status_code == 200, r.text
    unit = _unit(shop, "SU-FR-A")
    assert (unit["status"], unit.get("order_id")) == ("AVAILABLE", None)
    assert [ln.get("product_id") for ln in told] == [FR]


# ---------------------------------------------------------------------------
# Review of c67e0dcc -- goods with no product id
# ---------------------------------------------------------------------------

NO_ID_LINES = [
    {"item_type": "FRAME", "category": "FRAME"},
    {"item_type": "ACCESSORY"},
    {"item_type": "LENS"},
    {"item_type": "SUNGLASS", "product_id": ""},
    {"item_type": "SERVICE", "category": "FRAME"},
    {"item_type": "WHATEVER"},
]


@pytest.mark.parametrize("extra", NO_ID_LINES)
def test_goods_with_no_product_are_refused_on_the_edit(client, shop, extra):
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    before, total = _ledger(shop), _order(shop, oid)["grand_total"]
    r = _edit(client, oid, [line, {"product_name": "Ray-Ban RB3025", "quantity": 1,
                                   "unit_price": 1000.0, **extra}])
    _refused_unchanged(shop, r, before, 400)
    assert _order(shop, oid)["grand_total"] == total
    assert len(_order(shop, oid)["items"]) == 1


@pytest.mark.parametrize("mode", ["REVISED_INVOICE", "CREDIT_NOTE"])
def test_goods_with_no_product_are_refused_on_the_invoice_doors(client, shop, invoiced, mode):
    oid = invoiced([_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    before = _ledger(shop)
    r = _edit(client, oid, [line, {"item_type": "FRAME", "category": "FRAME",
                                   "product_name": "Ray-Ban RB3025", "unit_price": 1000.0}],
              route="superadmin-invoice-change", mode=mode)
    _refused_unchanged(shop, r, before, 400)
    assert _order(shop, oid)["invoice_number"] == "BV-INV-1"


def test_a_stored_line_with_no_product_can_still_be_corrected(client, shop):
    """History: an old free-text frame stays correctable, it adds no goods."""
    oid = _saved_bill(client, shop, [_line(SVC)])
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid},
        {"$set": {"items.0.item_type": "FRAME", "items.0.product_id": None,
                  "items.0.category": "FRAME"}},
    )
    line = _order(shop, oid)["items"][0]
    r = _edit(client, oid, [_as_sent(line, unit_price=150.0)])
    assert r.status_code == 200, r.text
    assert _order(shop, oid)["items"][0]["unit_price"] == 150.0


def test_raising_a_stored_line_with_no_product_is_refused(client, shop):
    oid = _saved_bill(client, shop, [_line(SVC)])
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid},
        {"$set": {"items.0.item_type": "FRAME", "items.0.product_id": None}},
    )
    before = _ledger(shop)
    line = _order(shop, oid)["items"][0]
    r = _edit(client, oid, [_as_sent(line, quantity=2)])
    _refused_unchanged(shop, r, before, 400)


def test_the_till_refuses_goods_with_a_blank_product_too(client, shop):
    before = _ledger(shop)
    r = _sell(client, [_line(FR, product_id="")])
    _refused_unchanged(shop, r, before, 400)


def test_add_item_refuses_goods_with_a_blank_product_too(client, shop):
    oid = _saved_bill(client, shop, [_line(ACC)], status="DRAFT")
    before = _ledger(shop)
    r = client.post(
        f"/api/v1/orders/{oid}/items", json=_line(FR, product_id=""), headers=_token(STAFF)
    )
    _refused_unchanged(shop, r, before, 400)
    assert len(_order(shop, oid)["items"]) == 1


def test_the_till_still_bills_a_free_text_service(client, shop):
    r = _sell(client, [_line(SVC, product_id="", product_name="Nose pad fitting")])
    assert r.status_code in (200, 201), r.text


# ---------------------------------------------------------------------------
# Review of c67e0dcc -- lens cells, read back off lens_stock_lines
# ---------------------------------------------------------------------------

CELL = "LENS-LINE-1"


def _lens(power=-1.0, **over):
    """A lens line in the till's own shape: the cell rides in lens_options."""
    return {
        "item_type": "LENS", "product_id": "lens-x", "product_name": "SV 1.5",
        "quantity": 1, "unit_price": 1000.0,
        "sph": power, "lens_options": {"lens_line_id": CELL}, "prescription_id": "RX-PSI-1",
        **over,
    }


@pytest.fixture
def cells(shop, monkeypatch):
    """The real lens_stock router on this shop's database. `cells(sph, on_hand,
    reserved)` seeds one cell; `cells.read(sph)` is (on_hand, reserved)."""
    from api.routers import lens_stock as stock_router

    monkeypatch.setattr(stock_router, "_get_db", lambda: shop["db"])
    coll = shop["db"].get_collection("lens_stock_lines")

    class Cells:
        def __call__(self, sph=-1.0, on_hand=5, reserved=0):
            coll.insert_one({
                "line_stock_id": f"CELL-{sph}", "lens_line_id": CELL, "store_id": STORE,
                "sph": sph, "cyl": 0.0, "add": None, "on_hand": on_hand, "reserved": reserved,
            })

        def read(self, sph=-1.0):
            doc = coll.find_one({"lens_line_id": CELL, "sph": sph})
            return doc["on_hand"], doc["reserved"]

    return Cells()


def _cancel(client, oid):
    r = client.post(
        f"/api/v1/orders/{oid}/cancel", params={"reason": "customer left"}, headers=_token(ADMIN)
    )
    assert r.status_code == 200, r.text


def _lens_line(shop, oid):
    return next(ln for ln in _order(shop, oid)["items"] if ln["item_type"] == "LENS")


def _other_line(shop, oid):
    return _as_sent(next(ln for ln in _order(shop, oid)["items"] if ln["item_type"] != "LENS"))


def _lens_sent(line, **over):
    """A stored lens line as the screen sends it: no powers, no lens_options."""
    return _as_sent(line, **over)


def _lens_bill(client, shop, cells, qty=1, on_hand=5):
    """A bill holding a reserved cell at quantity `qty`."""
    cells(on_hand=on_hand)
    oid = _saved_bill(client, shop, [_line(SVC)])
    keep = _as_sent(_order(shop, oid)["items"][0])
    r = _edit(client, oid, [keep, _lens(quantity=qty)])
    assert r.status_code == 200, r.text
    assert cells.read() == (on_hand, qty)
    return oid, _lens_line(shop, oid)


def test_new_lens_on_an_empty_cell_is_refused(client, shop, cells):
    cells(on_hand=0)
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    total = _order(shop, oid)["grand_total"]
    r = _edit(client, oid, [line, _lens()])
    assert r.status_code == 409, r.text
    assert _order(shop, oid)["grand_total"] == total
    assert cells.read() == (0, 0)


def test_new_lens_cannot_take_what_another_bill_holds(client, shop, cells):
    cells(on_hand=1, reserved=1)
    oid = _saved_bill(client, shop, [_line(SVC)])
    r = _edit(client, oid, [_as_sent(_order(shop, oid)["items"][0]), _lens()])
    assert r.status_code == 409, r.text
    assert cells.read() == (1, 1)


def test_new_lens_on_a_cell_never_set_up_is_refused(client, shop, cells):
    oid = _saved_bill(client, shop, [_line(SVC)])
    r = _edit(client, oid, [_as_sent(_order(shop, oid)["items"][0]), _lens(power=-9.0)])
    assert r.status_code == 409, r.text
    assert len(_order(shop, oid)["items"]) == 1


def test_new_lens_reserves_and_cancel_gives_it_back(client, shop, cells):
    oid, _line_saved = _lens_bill(client, shop, cells)
    assert cells.read() == (5, 1)
    _cancel(client, oid)
    assert cells.read() == (5, 0)


def test_new_lens_reserves_under_the_saved_lines_key(client, shop, cells):
    oid, line = _lens_bill(client, shop, cells)
    audit = shop["db"].get_collection("lens_stock_audit")
    assert audit.find_one({"source_id": f"{oid}#{line['item_id']}", "action": "reserve"})


def test_second_new_lens_refused_gives_the_first_back(client, shop, cells):
    cells(on_hand=1)
    cells(sph=-2.0, on_hand=0)
    oid = _saved_bill(client, shop, [_line(SVC)])
    r = _edit(client, oid, [_as_sent(_order(shop, oid)["items"][0]), _lens(), _lens(power=-2.0)])
    assert r.status_code == 409, r.text
    assert cells.read() == (1, 0)


def test_lens_given_back_when_a_unit_on_the_same_edit_is_lost(client, shop, cells, monkeypatch):
    from database.repositories.product_repository import StockRepository

    cells(on_hand=1)
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    monkeypatch.setattr(StockRepository, "mark_sold", lambda self, *a, **k: False)
    r = _edit(client, oid, [line, _lens(), _line(FR, stock_id="SU-FR-A")])
    assert r.status_code == 409, r.text
    assert cells.read() == (1, 0)


def test_replacing_a_lens_frees_the_old_cell_and_takes_a_fresh_key(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells)
    cells(sph=-2.0, on_hand=2)
    r = _edit(client, oid, [
        _other_line(shop, oid),
        {**_lens(power=-2.0, product_id="lens-y"), "item_id": stored["item_id"]},
    ])
    assert r.status_code == 200, r.text
    assert cells.read() == (5, 0) and cells.read(-2.0) == (2, 1)
    assert _lens_line(shop, oid)["item_id"] != stored["item_id"], (
        "the old key already has its #release row"
    )
    _cancel(client, oid)
    assert cells.read(-2.0) == (2, 0), "a replaced lens must still come back on cancel"


def test_replacing_a_lens_onto_an_empty_cell_keeps_the_old(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells)
    cells(sph=-2.0, on_hand=0)
    r = _edit(client, oid, [
        _other_line(shop, oid),
        {**_lens(power=-2.0, product_id="lens-y"), "item_id": stored["item_id"]},
    ])
    assert r.status_code == 409, r.text
    assert cells.read() == (5, 1) and cells.read(-2.0) == (0, 0)
    assert _lens_line(shop, oid)["item_id"] == stored["item_id"]


def test_changing_only_the_power_moves_the_reservation(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells)
    cells(sph=-2.0, on_hand=1)
    r = _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, sph=-2.0)])
    assert r.status_code == 200, r.text
    assert cells.read() == (5, 0) and cells.read(-2.0) == (1, 1)
    _cancel(client, oid)
    assert cells.read(-2.0) == (1, 0)


def test_changing_only_the_power_onto_an_empty_cell_is_refused(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells)
    cells(sph=-2.0, on_hand=0)
    r = _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, sph=-2.0)])
    assert r.status_code == 409, r.text
    assert cells.read() == (5, 1)


def test_price_correction_keeps_the_lens_cell_and_cancel_still_frees_it(client, shop, cells):
    """The edit screen sends no powers back: the line must not forget its cell."""
    oid, stored = _lens_bill(client, shop, cells)
    r = _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, unit_price=900.0)])
    assert r.status_code == 200, r.text
    saved = _lens_line(shop, oid)
    assert saved["item_id"] == stored["item_id"]
    assert (saved["sph"], saved["lens_options"]) == (stored["sph"], stored["lens_options"])
    assert cells.read() == (5, 1)
    _cancel(client, oid)
    assert cells.read() == (5, 0)


def test_a_lens_line_with_no_item_id_is_still_the_stored_line(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells, on_hand=1)
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid}, {"$unset": {"items.1.item_id": ""}}
    )
    sent = {k: v for k, v in _lens_sent(stored, unit_price=900.0).items() if k != "item_id"}
    r = _edit(client, oid, [_other_line(shop, oid), sent])
    assert r.status_code == 200, r.text
    assert cells.read() == (1, 1), "a price correction moves no cell"
    assert _lens_line(shop, oid)["sph"] == stored["sph"], "it is still the stored line"


def test_raising_a_lens_quantity_reserves_the_extra(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells, on_hand=3)
    r = _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, quantity=3)])
    assert r.status_code == 200, r.text
    assert cells.read() == (3, 3)
    _cancel(client, oid)
    assert cells.read() == (3, 0), "the line's release covers the raised quantity"


def test_raising_a_lens_quantity_past_the_cell_is_refused(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells, on_hand=1)
    r = _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, quantity=2)])
    assert r.status_code == 409, r.text
    assert cells.read() == (1, 1)
    assert _lens_line(shop, oid)["quantity"] == 1


def test_lowering_a_lens_quantity_gives_back_the_rest_and_cancel_the_remainder(
    client, shop, cells
):
    oid, stored = _lens_bill(client, shop, cells, qty=3)
    r = _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, quantity=1)])
    assert r.status_code == 200, r.text
    assert cells.read() == (5, 1)
    _cancel(client, oid)
    assert cells.read() == (5, 0)


def test_lowering_a_lens_on_a_full_cell_is_never_refused(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells, qty=2, on_hand=2)
    r = _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, quantity=1)])
    assert r.status_code == 200, r.text
    assert cells.read() == (2, 1)


def test_removing_a_lens_after_a_raise_gives_all_of_it_back(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells)
    other = _other_line(shop, oid)
    assert _edit(client, oid, [other, _lens_sent(stored, quantity=2)]).status_code == 200
    assert cells.read() == (5, 2)
    assert _edit(client, oid, [other]).status_code == 200
    assert cells.read() == (5, 0)


def test_lens_on_a_shipped_bill_keeps_its_cell(client, shop, invoiced, cells):
    oid, _stored = _lens_bill(client, shop, cells)
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid},
        {"$set": {"status": "SHIPPED", "invoice_number": "BV-INV-1"}},
    )
    r = _edit(client, oid, [_other_line(shop, oid)], route="superadmin-invoice-change",
              mode="REVISED_INVOICE")
    assert r.status_code == 200, r.text
    assert cells.read() == (5, 1)


def test_lowering_a_cut_lens_gives_nothing_back(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells, qty=2)
    shop["db"].get_collection("lens_stock_audit").insert_one(
        {"source_id": f"{oid}#{stored['item_id']}#commit", "action": "commit"}
    )
    r = _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, quantity=1)])
    assert r.status_code == 200, r.text
    assert cells.read() == (5, 2)


def test_edit_that_loses_the_order_gives_its_cell_back(client, shop, cells, monkeypatch):
    from database.repositories.order_repository import OrderRepository

    cells(on_hand=1)
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    monkeypatch.setattr(OrderRepository, "update_if", lambda self, *a, **k: False)
    r = _edit(client, oid, [line, _lens()])
    assert r.status_code == 409, r.text
    assert cells.read() == (1, 0)


def test_write_that_raised_before_landing_gives_the_cell_back(client, shop, cells, monkeypatch):
    from database.repositories.order_repository import OrderRepository

    cells(on_hand=1)
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])

    def dropped(self, *a, **k):
        raise TimeoutError("write never reached the server")

    monkeypatch.setattr(OrderRepository, "update_if", dropped)
    client_no_raise = type(client)(client.app, raise_server_exceptions=False)
    r = client_no_raise.put(
        f"/api/v1/orders/{oid}/superadmin-edit",
        json={"reason": "correcting this bill", "items": [line, _lens()]},
        headers=_token(ADMIN),
    )
    assert r.status_code >= 500, r.text
    assert cells.read() == (1, 0)


def test_write_that_landed_but_lost_its_reply_keeps_the_cell(client, shop, cells, monkeypatch):
    from database.repositories.order_repository import OrderRepository

    cells(on_hand=1)
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
        json={"reason": "correcting this bill", "items": [line, _lens()]},
        headers=_token(ADMIN),
    )
    assert r.status_code >= 500, r.text
    assert len(_order(shop, oid)["items"]) == 2
    assert cells.read() == (1, 1), "the saved line must keep its cell"


def test_order_unreadable_after_a_failed_write_keeps_what_it_took(client, shop, cells, monkeypatch):
    """Cannot tell if it landed: nothing goes back under a bill that may exist."""
    from database.repositories.order_repository import OrderRepository

    cells(on_hand=1)
    oid = _saved_bill(client, shop, [_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])

    def dropped(self, *a, **k):
        def dark(_self, *_a, **_k):
            raise TimeoutError("database gone")

        monkeypatch.setattr(OrderRepository, "find_by_id", dark)
        raise TimeoutError("write never reached the server")

    monkeypatch.setattr(OrderRepository, "update_if", dropped)
    client_no_raise = type(client)(client.app, raise_server_exceptions=False)
    r = client_no_raise.put(
        f"/api/v1/orders/{oid}/superadmin-edit",
        json={"reason": "correcting this bill",
              "items": [line, _lens(), _line(FR, stock_id="SU-FR-A")]},
        headers=_token(ADMIN),
    )
    assert r.status_code >= 500, r.text
    assert cells.read() == (1, 1)
    assert _unit(shop, "SU-FR-A")["status"] == "SOLD"


def test_a_soft_reserve_failure_is_flagged_on_the_bill(client, shop, cells, monkeypatch):
    from api.services import lens_stock_hook

    async def blip(**_kw):
        return {"status": "failed", "error": "mongo blip"}

    oid = _saved_bill(client, shop, [_line(SVC)])
    assert not _order(shop, oid)["lens_reserve_failed"]
    monkeypatch.setattr(lens_stock_hook, "reserve_for_order_item", blip)
    r = _edit(client, oid, [_as_sent(_order(shop, oid)["items"][0]), _lens()])
    assert r.status_code == 200, r.text
    assert _order(shop, oid)["lens_reserve_failed"] is True


def test_revised_invoice_with_a_lens_on_an_empty_cell_mints_no_number(
    client, shop, invoiced, cells, monkeypatch
):
    from database.repositories.order_repository import OrderRepository

    cells(on_hand=0)
    oid = invoiced([_line(SVC)])
    minted = []
    monkeypatch.setattr(OrderRepository, "next_invoice_number",
                        lambda self, *a, **k: minted.append(1) or "BV-INV-REV-1")
    r = _edit(client, oid, [_as_sent(_order(shop, oid)["items"][0]), _lens()],
              route="superadmin-invoice-change", mode="REVISED_INVOICE")
    assert r.status_code == 409, r.text
    assert not minted, "a refused correction must not burn an invoice number"
    assert _order(shop, oid)["invoice_number"] == "BV-INV-1"


def test_revised_invoice_reserves_a_new_lens(client, shop, invoiced, cells):
    cells(on_hand=1)
    oid = invoiced([_line(SVC)])
    r = _edit(client, oid, [_as_sent(_order(shop, oid)["items"][0]), _lens()],
              route="superadmin-invoice-change", mode="REVISED_INVOICE")
    assert r.status_code == 200, r.text
    assert cells.read() == (1, 1)


def test_revised_invoice_refused_at_the_write_gives_the_cell_back(
    client, shop, invoiced, cells, monkeypatch
):
    from database.repositories.order_repository import OrderRepository

    cells(on_hand=1)
    oid = invoiced([_line(SVC)])
    line = _as_sent(_order(shop, oid)["items"][0])
    monkeypatch.setattr(OrderRepository, "update_if", lambda self, *a, **k: False)
    r = _edit(client, oid, [line, _lens()], route="superadmin-invoice-change",
              mode="REVISED_INVOICE")
    assert r.status_code == 409, r.text
    assert cells.read() == (1, 0)


def test_revised_invoice_number_failure_gives_the_cell_back(
    client, shop, invoiced, cells, monkeypatch
):
    from database.repositories.order_repository import InvoiceNumberError, OrderRepository

    cells(on_hand=1)
    oid = invoiced([_line(SVC)])

    def no_number(self, *a, **k):
        raise InvoiceNumberError("series exhausted")

    monkeypatch.setattr(OrderRepository, "next_invoice_number", no_number)
    r = _edit(client, oid, [_as_sent(_order(shop, oid)["items"][0]), _lens()],
              route="superadmin-invoice-change", mode="REVISED_INVOICE")
    assert r.status_code == 400, r.text
    assert cells.read() == (1, 0)


def test_note_cannot_bill_a_new_lens(client, shop, invoiced, cells):
    cells(on_hand=1)
    oid = invoiced([_line(SVC)])
    r = _edit(client, oid, [_as_sent(_order(shop, oid)["items"][0]), _lens()],
              route="superadmin-invoice-change", mode="CREDIT_NOTE")
    assert r.status_code == 409, r.text
    assert cells.read() == (1, 0)


def test_note_raising_a_lens_quantity_is_refused(client, shop, invoiced, cells):
    oid, stored = _lens_bill(client, shop, cells)
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid}, {"$set": {"invoice_number": "BV-INV-1"}}
    )
    r = _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, quantity=2)],
              route="superadmin-invoice-change", mode="CREDIT_NOTE")
    assert r.status_code == 409, r.text
    assert cells.read() == (5, 1)


# ---------------------------------------------------------------------------
# Review of fba16956 -- a landed save settles what it removed, reply or no reply
# ---------------------------------------------------------------------------

DOORS = {"superadmin-edit": {}, "superadmin-invoice-change": {"mode": "REVISED_INVOICE"}}
FLAGS = ("line_remove_stock_release_failed", "line_remove_stock_failed_item_id")


@pytest.fixture(params=list(DOORS))
def door(request, client, shop, invoiced):
    """Both doors that rewrite a bill's lines. `arm` readies a saved bill for
    this door; `press` sends the edit; `press_reply_lost` lands it and loses
    the reply, on every order write, as test_write_that_landed does."""
    from api.utils.event_time import utc_now_iso
    from database.repositories.order_repository import OrderRepository

    route, body = request.param, DOORS[request.param]

    class Door:
        name = route

        @staticmethod
        def arm(oid):
            if body:
                shop["db"].get_collection("orders").update_one(
                    {"order_id": oid},
                    {"$set": {"invoice_number": "BV-INV-1", "invoice_date": utc_now_iso()}},
                )
            return oid

        @staticmethod
        def press(oid, lines):
            return _edit(client, oid, lines, route=route, **body)

        @staticmethod
        def press_reply_lost(oid, lines, monkeypatch):
            real = OrderRepository.update_if

            def lost_reply(self, *a, **k):
                real(self, *a, **k)
                raise TimeoutError("reply lost")

            quiet = type(client)(client.app, raise_server_exceptions=False)
            with monkeypatch.context() as m:
                m.setattr(OrderRepository, "update_if", lost_reply)
                r = quiet.put(
                    f"/api/v1/orders/{oid}/{route}",
                    json={"reason": "correcting this bill", "items": lines, **body},
                    headers=_token(ADMIN),
                )
            assert r.status_code >= 500, r.text

    return Door


def _reconciled(shop, oid):
    """Every piece and cell, the bill's lines and money, read off the collections."""
    order = _order(shop, oid)
    cell_rows = shop["db"].get_collection("lens_stock_lines").find({}, {"_id": 0})
    return (
        _ledger(shop),
        sorted((c["sph"], c["on_hand"], c["reserved"]) for c in cell_rows),
        [(ln.get("product_id"), ln.get("stock_id"), ln.get("quantity")) for ln in order["items"]],
        order.get("grand_total"),
        order.get("balance_due"),
    )


def _no_flag_left(shop, oid):
    order = _order(shop, oid)
    assert not any(k.startswith("line_remove_") for k in order), order


def _retry_changes_nothing(shop, oid, door, lines):
    """The same body again: the stored lines already match, so nothing moves."""
    before = _reconciled(shop, oid)
    r = door.press(oid, lines)
    assert r.status_code == 200, r.text
    assert _reconciled(shop, oid) == before
    _no_flag_left(shop, oid)


@pytest.mark.parametrize(
    "new_line", [_line(ACC), _line(FR, stock_id="SU-FR-B")], ids=["other_product", "same_product"]
)
def test_lost_reply_on_a_replaced_line_frees_the_old_unit(
    client, shop, door, monkeypatch, new_line
):
    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")]))
    stored = _order(shop, oid)["items"][0]
    assert _sold_to(shop, oid) == ["SU-FR-A"]
    lines = [{**new_line, "item_id": stored["item_id"]}]
    door.press_reply_lost(oid, lines, monkeypatch)
    old = _unit(shop, "SU-FR-A")
    assert (old["status"], old.get("order_id")) == ("AVAILABLE", None), "stranded on a bill without it"
    assert len(_sold_to(shop, oid)) == 1
    _no_flag_left(shop, oid)
    _retry_changes_nothing(shop, oid, door, lines)


def test_lost_reply_on_a_removed_line_frees_its_unit(client, shop, door, monkeypatch):
    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A"), _line(SVC)]))
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    lines = [_as_sent(keep)]
    door.press_reply_lost(oid, lines, monkeypatch)
    assert _order(shop, oid)["items"][0]["product_id"] == SVC
    assert _sold_to(shop, oid) == []
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"
    _no_flag_left(shop, oid)
    _retry_changes_nothing(shop, oid, door, lines)


def test_lost_reply_on_a_lowered_quantity_frees_only_the_extra(client, shop, door, monkeypatch):
    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A", quantity=2)]))
    assert len(_sold_to(shop, oid)) == 2
    lines = [_as_sent(_order(shop, oid)["items"][0], quantity=1)]
    door.press_reply_lost(oid, lines, monkeypatch)
    assert _sold_to(shop, oid) == ["SU-FR-A"]
    _no_flag_left(shop, oid)
    _retry_changes_nothing(shop, oid, door, lines)


def test_lost_reply_on_a_replaced_lens_frees_the_old_cell(client, shop, cells, door, monkeypatch):
    oid, stored = _lens_bill(client, shop, cells)
    door.arm(oid)
    cells(sph=-2.0, on_hand=2)
    lines = [_other_line(shop, oid), _lens_sent(stored, sph=-2.0)]
    door.press_reply_lost(oid, lines, monkeypatch)
    assert cells.read() == (5, 0), "the old cell stayed held for a line no longer on the bill"
    assert cells.read(-2.0) == (2, 1)
    _no_flag_left(shop, oid)
    _retry_changes_nothing(shop, oid, door, lines)
    if door.name == "superadmin-edit":  # an invoiced bill goes back through Returns
        _cancel(client, oid)
        assert cells.read(-2.0) == (2, 0)


def test_lost_reply_on_a_removed_lens_frees_its_cell(client, shop, cells, door, monkeypatch):
    oid, _stored = _lens_bill(client, shop, cells)
    door.arm(oid)
    lines = [_other_line(shop, oid)]
    door.press_reply_lost(oid, lines, monkeypatch)
    assert cells.read() == (5, 0)
    _no_flag_left(shop, oid)
    _retry_changes_nothing(shop, oid, door, lines)


def test_lost_reply_on_a_lowered_lens_frees_only_the_extra(client, shop, cells, door, monkeypatch):
    oid, stored = _lens_bill(client, shop, cells, qty=3)
    door.arm(oid)
    lines = [_other_line(shop, oid), _lens_sent(stored, quantity=1)]
    door.press_reply_lost(oid, lines, monkeypatch)
    assert cells.read() == (5, 1)
    _retry_changes_nothing(shop, oid, door, lines)
    if door.name == "superadmin-edit":
        _cancel(client, oid)
        assert cells.read() == (5, 0)


def test_settling_twice_moves_each_piece_once(client, shop, cells, door, monkeypatch):
    """A settle goes by name, so a re-run is harmless on the shapes a blind re-run
    would hurt: a first-available unit and a lowered lens."""
    from api.routers.orders import admin_edit, edit_ops

    shop["db"].get_collection("stock_units").insert_one(
        {"stock_id": "SU-FR-C", "barcode": "BC-C", "product_id": FR, "store_id": STORE,
         "status": "AVAILABLE", "quantity": 1}
    )
    oid, stored = _lens_bill(client, shop, cells, qty=3)
    other = _other_line(shop, oid)
    assert _edit(client, oid, [other, _lens_sent(stored), _line(FR, quantity=3)]).status_code == 200
    door.arm(oid)
    assert len(_sold_to(shop, oid)) == 3
    real = edit_ops.settle

    async def twice(*a):
        await real(*a)
        await real(*a)

    monkeypatch.setattr(admin_edit, "settle", twice)
    monkeypatch.setattr(edit_ops, "_take_lease", lambda op: True)
    monkeypatch.setattr(edit_ops, "_end", lambda *a: None)
    lines = [_as_sent(ln, quantity=2) if ln["product_id"] != SVC else _as_sent(ln)
             for ln in _order(shop, oid)["items"]]
    r = door.press(oid, lines)
    assert r.status_code == 200, r.text
    assert len(_sold_to(shop, oid)) == 2, "a second settle freed a unit the bill still sells"
    assert cells.read() == (5, 2), "a second settle gave back a lens the bill still sells"
    _no_flag_left(shop, oid)


@pytest.mark.parametrize("lost", [False, True], ids=["reply_kept", "reply_lost"])
def test_a_unit_that_will_not_go_back_is_flagged(client, shop, door, monkeypatch, lost):
    from database.repositories.product_repository import StockRepository

    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A"), _line(SVC)]))
    stored = _order(shop, oid)["items"]
    gone = next(ln for ln in stored if ln["product_id"] == FR)
    lines = [_as_sent(next(ln for ln in stored if ln["product_id"] == SVC))]

    def dead(self, *a, **k):
        raise TimeoutError("stock store gone")

    monkeypatch.setattr(StockRepository, "release_sold_units_for_order", dead)
    if lost:
        door.press_reply_lost(oid, lines, monkeypatch)
    else:
        assert door.press(oid, lines).status_code == 200
    order = _order(shop, oid)
    assert order["line_remove_stock_release_failed"] is True
    assert order["line_remove_stock_failed_item_id"] == gone["item_id"]
    assert _unit(shop, "SU-FR-A")["status"] == "SOLD"


@pytest.mark.parametrize("lost", [False, True], ids=["reply_kept", "reply_lost"])
def test_a_cell_that_will_not_go_back_is_flagged(client, shop, cells, door, monkeypatch, lost):
    from api.routers import lens_stock as stock_router

    oid, stored = _lens_bill(client, shop, cells)
    door.arm(oid)
    lines = [_other_line(shop, oid)]

    async def dead(*a, **k):
        raise TimeoutError("lens store gone")

    monkeypatch.setattr(stock_router, "release_cell", dead)
    if lost:
        door.press_reply_lost(oid, lines, monkeypatch)
    else:
        assert door.press(oid, lines).status_code == 200
    order = _order(shop, oid)
    assert order["line_remove_stock_release_failed"] is True
    assert order["line_remove_stock_failed_item_id"] == stored["item_id"]
    assert cells.read() == (5, 1)


def test_a_save_that_cannot_be_read_back_leaves_the_bill_flagged(client, shop, door, monkeypatch):
    """Landed or not cannot be told, so nothing moves; the save itself said what
    it owed, so a landed one is not silent."""
    from database.repositories.order_repository import OrderRepository

    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A"), _line(SVC)]))
    stored = _order(shop, oid)["items"]
    gone = next(ln for ln in stored if ln["product_id"] == FR)
    lines = [_as_sent(next(ln for ln in stored if ln["product_id"] == SVC))]
    real_write = OrderRepository.update_if

    def landed_then_dark(self, *a, **k):
        real_write(self, *a, **k)
        monkeypatch.setattr(OrderRepository, "find_by_id", lambda *_a, **_k: None)
        raise TimeoutError("reply lost and the database gone")

    with monkeypatch.context() as m:
        m.setattr(OrderRepository, "find_by_id", OrderRepository.find_by_id)
        m.setattr(OrderRepository, "update_if", landed_then_dark)
        quiet = type(client)(client.app, raise_server_exceptions=False)
        r = quiet.put(
            f"/api/v1/orders/{oid}/{door.name}",
            json={"reason": "correcting this bill", "items": lines, **DOORS[door.name]},
            headers=_token(ADMIN),
        )
    assert r.status_code >= 500, r.text
    order = _order(shop, oid)
    assert len(order["items"]) == 1
    assert _unit(shop, "SU-FR-A")["status"] == "SOLD", "never freed on a guess"
    assert order["line_remove_stock_release_failed"] is True
    assert order["line_remove_stock_failed_item_id"] == gone["item_id"]


def test_a_clean_edit_keeps_an_older_flag(client, shop, door):
    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A"), _line(SVC)]))
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid}, {"$set": dict(zip(FLAGS, (True, "OLD-LINE")))}
    )
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    assert door.press(oid, [_as_sent(keep)]).status_code == 200
    order = _order(shop, oid)
    assert (order[FLAGS[0]], order[FLAGS[1]]) == (True, "OLD-LINE")
    assert "line_remove_settle_token" not in order


def test_lost_reply_on_a_bill_that_left_shelves_nothing(client, shop, invoiced, monkeypatch):
    """A shipped parcel holds the piece, reply or no reply."""
    from database.repositories.order_repository import OrderRepository

    oid = invoiced([_line(FR, stock_id="SU-FR-A"), _line(SVC)])
    told = _moved_online(monkeypatch)
    shop["db"].get_collection("orders").update_one({"order_id": oid}, {"$set": {"status": "SHIPPED"}})
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    real = OrderRepository.update_if

    def lost_reply(self, *a, **k):
        real(self, *a, **k)
        raise TimeoutError("reply lost")

    monkeypatch.setattr(OrderRepository, "update_if", lost_reply)
    quiet = type(client)(client.app, raise_server_exceptions=False)
    r = quiet.put(
        f"/api/v1/orders/{oid}/superadmin-invoice-change",
        json={"reason": "correcting this bill", "items": [_as_sent(keep)],
              "mode": "REVISED_INVOICE"},
        headers=_token(ADMIN),
    )
    assert r.status_code >= 500, r.text
    assert len(_order(shop, oid)["items"]) == 1
    assert _sold_to(shop, oid) == ["SU-FR-A"]
    assert told == []
    _no_flag_left(shop, oid)


def test_lost_reply_still_tells_the_website_and_regates_the_points(
    client, shop, door, monkeypatch
):
    """The rest of what follows a save: the same shape, the same fix."""
    from api.routers import loyalty

    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A"), _line(SVC)]))
    told = _moved_online(monkeypatch)
    regated = []
    monkeypatch.setattr(
        loyalty, "regate_earn_after_edit", lambda doc, user_id=None: regated.append(doc["order_id"])
    )
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    door.press_reply_lost(oid, [_as_sent(keep)], monkeypatch)
    assert [ln["product_id"] for ln in told] == [FR]
    assert regated == [oid], "a discount edit must not leave the earn standing"


def test_a_claim_that_lost_its_reply_on_a_saved_bill_goes_back(client, shop, door, monkeypatch):
    """Refused, and the unit that wrote SOLD comes back: the claim's stamp proves
    it this press's, so nothing strands and the retry takes it (Codex round 4)."""
    from database.repositories.product_repository import StockRepository

    oid = door.arm(_saved_bill(client, shop, [_line(SVC)]))
    keep = _as_sent(_order(shop, oid)["items"][0])
    before = _order(shop, oid)["items"]
    real = StockRepository.mark_sold

    def wrote_then_lost(self, *a, **k):  # mark_sold swallows the fault: a plain False
        real(self, *a, **k)
        return False

    with monkeypatch.context() as m:
        m.setattr(StockRepository, "mark_sold", wrote_then_lost)
        r = door.press(oid, [keep, _line(FR, stock_id="SU-FR-A")])
    assert r.status_code == 409, r.text
    order = _order(shop, oid)
    assert order["items"] == before
    assert _sold_to(shop, oid) == []
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"
    _no_flag_left(shop, oid)
    r = door.press(oid, [keep, _line(FR, stock_id="SU-FR-A")])
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == ["SU-FR-A"]
    assert len(_order(shop, oid)["items"]) == 2


def test_a_first_available_claim_that_faulted_on_a_saved_bill_goes_back(
    client, shop, door, monkeypatch
):
    from database.repositories.product_repository import StockRepository

    oid = door.arm(_saved_bill(client, shop, [_line(SVC)]))
    keep = _as_sent(_order(shop, oid)["items"][0])
    real = StockRepository.claim_one_available

    def wrote_then_lost(self, *a, **k):
        real(self, *a, **k)
        raise TimeoutError("reply lost")

    monkeypatch.setattr(StockRepository, "claim_one_available", wrote_then_lost)
    r = door.press(oid, [keep, _line(FR)])
    assert r.status_code == 503, r.text
    assert _sold_to(shop, oid) == [], "a unit nobody can name stranded SOLD"
    _no_flag_left(shop, oid)


def test_a_plain_refusal_raises_no_flag(client, shop, door, monkeypatch):
    """The last unit gone since the gate read the shelf: a refusal, no stray unit."""
    from database.repositories.product_repository import StockRepository

    oid = door.arm(_saved_bill(client, shop, [_line(SVC)]))
    stored = _as_sent(_order(shop, oid)["items"][0])
    monkeypatch.setattr(StockRepository, "claim_one_available", lambda *a, **k: None)
    r = door.press(oid, [stored, _line(FR)])
    assert r.status_code == 409, r.text
    _no_flag_left(shop, oid)


# ---------------------------------------------------------------------------
# Review of fba16956 -- the lines the edit screen really sends
# ---------------------------------------------------------------------------

EDIT_MODES = [
    ("superadmin-edit", {}),
    ("superadmin-invoice-change", {"mode": "REVISED_INVOICE"}),
    ("superadmin-invoice-change", {"mode": "CREDIT_NOTE"}),
]
# SuperadminOrderEditAddLine.test.tsx asserts the screen builds exactly this.
TYPED = {"product_name": "Courier charge", "quantity": 1, "unit_price": 100, "discount_percent": 0}


def _typed_bill(client, shop, invoiced, route):
    items = [_line(SVC)]
    return _saved_bill(client, shop, items) if route == "superadmin-edit" else invoiced(items)


@pytest.mark.parametrize("route,body", EDIT_MODES)
def test_the_screens_typed_service_line_saves(client, shop, invoiced, route, body):
    oid = _typed_bill(client, shop, invoiced, route)
    before = _ledger(shop)
    stored = _as_sent(_order(shop, oid)["items"][0])
    line = {"item_type": "SERVICE", "category": "SERVICE", **TYPED}
    r = _edit(client, oid, [stored, line], route=route, **body)
    assert r.status_code == 200, r.text
    assert _ledger(shop) == before, "a service line takes nothing off the shelf"


@pytest.mark.parametrize("route,body", EDIT_MODES)
@pytest.mark.parametrize(
    "item_type,category", [("FRAME", "FRAME"), ("COURIER", "Courier")], ids=["goods", "unknown"]
)
def test_the_screens_typed_goods_stay_refused(
    client, shop, invoiced, route, body, item_type, category
):
    oid = _typed_bill(client, shop, invoiced, route)
    before, bill = _ledger(shop), _order(shop, oid)
    stored = _as_sent(bill["items"][0])
    line = {"item_type": item_type, "category": category, **TYPED}
    r = _edit(client, oid, [stored, line], route=route, **body)
    _refused_unchanged(shop, r, before, 400)
    assert _order(shop, oid) == bill


# ---------------------------------------------------------------------------
# Review of 770095d9 -- a line that takes no unit cannot name one
# ---------------------------------------------------------------------------

NO_UNIT_LINES = {
    "service": {"item_type": "SERVICE", "product_name": "Adjustment", "unit_price": 50.0},
    "virtual": {"item_type": "FRAME", "product_id": "custom-1", "product_name": "Made to order",
                "unit_price": 300.0},
    "lens": _lens(),
}


@pytest.mark.parametrize("route,body", EDIT_MODES)
@pytest.mark.parametrize("kind", list(NO_UNIT_LINES))
@pytest.mark.parametrize("named", ["sold", "SU-FR-B", "GHOST-1"])
def test_a_new_line_that_takes_no_unit_cannot_name_one(
    client, shop, invoiced, cells, route, body, kind, named
):
    """Nothing checks or claims such a line's unit, so the id would stand on the
    bill as a sale of a piece it never took (barcode trace, returns, release)."""
    cells()
    if named == "sold":
        named = "SU-FR-A"
        _saved_bill(client, shop, [_line(FR, stock_id=named)])
    oid = _typed_bill(client, shop, invoiced, route)
    before, bill = _ledger(shop), _order(shop, oid)
    stored = _as_sent(bill["items"][0])
    line = {**NO_UNIT_LINES[kind], "stock_id": named}
    r = _edit(client, oid, [stored, line], route=route, **body)
    _refused_unchanged(shop, r, before, 400)
    assert _order(shop, oid) == bill


def test_a_stored_service_line_cannot_be_handed_a_unit(client, shop):
    oid = _saved_bill(client, shop, [_line(SVC)])
    before, bill = _ledger(shop), _order(shop, oid)
    r = _edit(client, oid, [_as_sent(bill["items"][0], stock_id="SU-FR-B")])
    _refused_unchanged(shop, r, before, 400)
    assert _order(shop, oid) == bill


def test_an_old_line_keeps_the_unit_it_already_names(client, shop):
    """The refusal is for what the edit adds: a correction of a stored line
    answers to no stock rule, so an old bill stays correctable."""
    oid = _saved_bill(client, shop, [_line(SVC)])
    orders = shop["db"].get_collection("orders")
    orders.update_one({"order_id": oid}, {"$set": {"items.0.stock_id": "OLD-UNIT"}})
    before = _ledger(shop)
    line = _as_sent(_order(shop, oid)["items"][0], unit_price=90.0)
    r = _edit(client, oid, [line])
    assert r.status_code == 200, r.text
    assert _order(shop, oid)["items"][0]["stock_id"] == "OLD-UNIT"
    assert _ledger(shop) == before
