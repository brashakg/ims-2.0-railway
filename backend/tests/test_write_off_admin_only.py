"""
IMS 2.0 -- EVERY WRITE-OFF DOOR IS ADMIN / SUPERADMIN ONLY
==========================================================
Owner ruling 2026-08-25 (#8): taking stock off the books as a LOSS is ADMIN /
SUPERADMIN only, at every value. The stock-count write-off already enforced
it; these doors did not, and a store manager could reach them on the server:

  * PATCH/POST /inventory/serials  -- mark a unit DAMAGED or LOST_STOLEN
  * POST /catalog/products/{id}/inventory/adjust -- a NEGATIVE adjustment
  * POST /items/{id}/quarantine/release disposition=DAMAGE
    (tested beside its siblings in test_e3_item_event.py)

Each is proved against the real handler: 403 for a STORE_MANAGER and
nothing written; the admin passes; non-loss edits stay with the manager.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import catalog as catalog_mod  # noqa: E402
from api.routers import inventory as inv_mod  # noqa: E402
from api.routers.inventory.serials import SerialCreate, SerialUpdate  # noqa: E402

STORE = "ST-WO"
MANAGER = {"user_id": "m1", "roles": ["STORE_MANAGER"], "store_ids": [STORE],
           "active_store_id": STORE}
ADMIN = {"user_id": "a1", "roles": ["ADMIN"], "store_ids": [STORE],
         "active_store_id": STORE}


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def db(monkeypatch):
    mdb = mongomock.MongoClient()["wo"]

    class _Proxy:
        is_connected = True

        def get_collection(self, name):
            return mdb[name]

    monkeypatch.setattr(inv_mod, "_get_db", lambda: _Proxy())
    mdb["serial_numbers"].insert_one(
        {"serial_id": "s1", "serial_number": "SN1", "product_id": "P1",
         "store_id": STORE, "status": "IN_STOCK"}
    )
    return mdb


@pytest.mark.parametrize("status", ["DAMAGED", "LOST_STOLEN"])
def test_a_store_manager_cannot_mark_a_serial_lost_or_damaged(db, status):
    with pytest.raises(HTTPException) as exc:
        _run(inv_mod.update_serial("s1", SerialUpdate(status=status), MANAGER))
    assert exc.value.status_code == 403
    assert db["serial_numbers"].find_one({"serial_id": "s1"})["status"] == "IN_STOCK"

    with pytest.raises(HTTPException) as exc:
        _run(inv_mod.create_serial(
            SerialCreate(serial_number="SN2", product_id="P1", status=status), MANAGER))
    assert exc.value.status_code == 403
    assert db["serial_numbers"].count_documents({"serial_number": "SN2"}) == 0


def test_an_admin_can_mark_a_serial_lost(db):
    _run(inv_mod.update_serial("s1", SerialUpdate(status="LOST_STOLEN"), ADMIN))
    assert db["serial_numbers"].find_one({"serial_id": "s1"})["status"] == "LOST_STOLEN"


def test_a_manager_still_edits_a_serial_that_is_already_damaged(db):
    """Not a write-off: the unit was already marked; a note is just a note."""
    db["serial_numbers"].update_one({"serial_id": "s1"}, {"$set": {"status": "DAMAGED"}})
    _run(inv_mod.update_serial(
        "s1", SerialUpdate(status="DAMAGED", notes="box kept"), MANAGER))
    assert db["serial_numbers"].find_one({"serial_id": "s1"})["notes"] == "box kept"


def test_a_store_manager_cannot_adjust_stock_down(monkeypatch):
    saved = []
    product = {"inventory": {"locations": {"L1": 5}, "total_quantity": 5}}
    monkeypatch.setattr(catalog_mod, "_get_catalog_product", lambda pid: product)
    monkeypatch.setattr(catalog_mod, "_save_catalog_product",
                        lambda p, mark_dirty=True: saved.append(dict(p)))

    with pytest.raises(HTTPException) as exc:
        _run(catalog_mod.adjust_product_inventory("P1", "L1", -2, None, MANAGER))
    assert exc.value.status_code == 403
    assert saved == [] and product["inventory"]["locations"]["L1"] == 5

    # Adding stock is not a write-off; the manager keeps that.
    out = _run(catalog_mod.adjust_product_inventory("P1", "L1", 1, None, MANAGER))
    assert out["new_quantity"] == 6
    out = _run(catalog_mod.adjust_product_inventory("P1", "L1", -2, None, ADMIN))
    assert out["new_quantity"] == 4


# ---------------------------------------------------------------------------
# Review round 1 (PR #1260): the remaining loss doors
# ---------------------------------------------------------------------------


class _MDB:
    is_connected = True

    def __init__(self, mdb):
        self._m = mdb

    def get_collection(self, name):
        return self._m[name]

    def __getattr__(self, name):
        return self._m[name]


@pytest.fixture
def lens(monkeypatch):
    from api.routers import lens_stock as ls

    mdb = mongomock.MongoClient()["lens"]
    mdb["lens_stock"].insert_one(
        {"line_stock_id": "C1", "lens_line_id": "L1", "store_id": STORE,
         "sph": 0.0, "cyl": 0.0, "add": None, "on_hand": 5, "reserved": 0}
    )
    monkeypatch.setattr(ls, "_stock_coll", lambda: mdb["lens_stock"])
    monkeypatch.setattr(ls, "_write_audit", lambda **k: None)
    monkeypatch.setattr(ls, "_load_lens_line", lambda lid: {"lens_line_id": lid})
    monkeypatch.setattr(
        ls, "validate_bulk_import_payload",
        lambda m, lens_line: [{"sph": r["sph"], "cyl": r["cyl"], "add": r["add"],
                               "on_hand": r["qty"]} for r in m],
    )
    return ls, mdb["lens_stock"]


def test_a_store_manager_cannot_lower_a_lens_stock_figure(lens):
    ls, coll = lens
    with pytest.raises(HTTPException) as exc:
        _run(ls.update_cell("C1", ls.StockCellUpdate(on_hand=3, notes="miscount"), MANAGER))
    assert exc.value.status_code == 403
    assert coll.find_one({"line_stock_id": "C1"})["on_hand"] == 5

    with pytest.raises(HTTPException) as exc:  # raising it is allowed -- with a reason
        _run(ls.update_cell("C1", ls.StockCellUpdate(on_hand=7), MANAGER))
    assert exc.value.status_code == 400
    _run(ls.update_cell("C1", ls.StockCellUpdate(on_hand=7, notes="tray found"), MANAGER))
    assert coll.find_one({"line_stock_id": "C1"})["on_hand"] == 7
    _run(ls.update_cell("C1", ls.StockCellUpdate(on_hand=2, notes="breakage"), ADMIN))
    assert coll.find_one({"line_stock_id": "C1"})["on_hand"] == 2


def test_a_lens_bulk_import_that_lowers_stock_is_refused_whole(lens):
    ls, coll = lens
    body = ls.BulkImportPayload(
        store_id=STORE,
        matrix=[{"sph": 0.0, "cyl": 0.0, "add": None, "qty": 1},
                {"sph": 1.0, "cyl": 0.0, "add": None, "qty": 4}],
    )
    with pytest.raises(HTTPException) as exc:
        _run(ls.bulk_import("L1", body, MANAGER))
    assert exc.value.status_code == 403
    assert coll.find_one({"line_stock_id": "C1"})["on_hand"] == 5
    assert coll.count_documents({}) == 1, "nothing of a refused import is written"


@pytest.fixture
def serial_db(monkeypatch):
    from api.routers import serial_tracking as st

    mdb = mongomock.MongoClient()["ser"]
    mdb["stock_units"].insert_one(
        {"stock_id": "U1", "serial": "SN-1", "product_id": "P1", "store_id": STORE,
         "status": "IN_STOCK"}
    )
    monkeypatch.setattr(st, "_get_db", lambda: _MDB(mdb))
    monkeypatch.setattr(st, "_sync_online_stock", lambda *a, **k: None)
    monkeypatch.setattr(st, "_audit", lambda *a, **k: None)
    return st, mdb


def test_a_manager_marks_a_serial_sold_only_on_a_real_order(serial_db):
    st, mdb = serial_db
    with pytest.raises(HTTPException) as exc:
        _run(st.mark_sold("SN-1", st.MarkSoldBody(order_id="NO-SUCH"), MANAGER))
    assert exc.value.status_code == 403
    mdb["orders"].insert_one({"order_id": "O9", "store_id": "ST-ELSE", "status": "CONFIRMED",
                              "items": [{"product_id": "P1"}]})
    with pytest.raises(HTTPException):  # another shop's order is not this sale
        _run(st.mark_sold("SN-1", st.MarkSoldBody(order_id="O9"), MANAGER))
    mdb["orders"].insert_one({"order_id": "O1", "store_id": STORE, "status": "CONFIRMED",
                              "items": [{"product_id": "P1"}]})
    out = _run(st.mark_sold("SN-1", st.MarkSoldBody(order_id="O1"), MANAGER))
    assert out["status"] == "SOLD"


def test_a_manager_cannot_recall_or_capture_a_serial(serial_db):
    st, mdb = serial_db
    with pytest.raises(HTTPException) as exc:
        _run(st.recall("SN-1", st.RecallBody(reason="batch"), MANAGER))
    assert exc.value.status_code == 403
    assert mdb["stock_units"].find_one({"serial": "SN-1"})["status"] == "IN_STOCK"
    with pytest.raises(HTTPException) as exc:
        _run(st.capture(st.CaptureBody(serial="SN-2", product_id="P1", store_id=STORE), MANAGER))
    assert exc.value.status_code == 403
    assert mdb["stock_units"].count_documents({"serial": "SN-2"}) == 0


def test_the_item_ledger_sell_needs_a_real_order(monkeypatch):
    from api.routers import item_events as ie_mod
    from database.repositories.product_repository import StockRepository

    mdb = mongomock.MongoClient()["sell"]
    mdb["stock_units"].insert_one(
        {"stock_id": "U1", "product_id": "P1", "store_id": STORE, "status": "AVAILABLE"}
    )
    monkeypatch.setattr(ie_mod, "_get_db", lambda: _MDB(mdb))
    monkeypatch.setattr(ie_mod, "get_stock_repository", lambda: StockRepository(mdb["stock_units"]))
    monkeypatch.setattr(ie_mod, "_pos_sell_enabled", lambda s: True)
    with pytest.raises(HTTPException) as exc:
        _run(ie_mod.sell_unit("U1", ie_mod.SellRequest(order_id=None), MANAGER))
    assert exc.value.status_code == 403
    assert mdb["stock_units"].find_one({"stock_id": "U1"})["status"] == "AVAILABLE"


def test_a_sessionless_scan_shows_no_on_hand_to_a_manager(monkeypatch):
    """M5: the count is blind on the server too -- the session-less lookup
    is one request away from a counter mid-count."""
    mdb = mongomock.MongoClient()["scan"]
    mdb["stock_units"].insert_one(
        {"barcode": "BV0000000001", "product_id": "P1", "store_id": STORE, "status": "AVAILABLE"}
    )
    mdb["products"].insert_one({"_id": "P1", "sku": "S1", "brand": "B", "model": "M"})
    monkeypatch.setattr(inv_mod, "_get_db", lambda: _MDB(mdb))
    req = inv_mod.BarcodeScanRequest(barcode="BV0000000001", physical_count=0)
    out = _run(inv_mod.scan_barcode_for_count(req, None, MANAGER))
    assert out["product_id"] == "P1"
    assert "system_count" not in out and "variance" not in out
    assert _run(inv_mod.scan_barcode_for_count(req, None, ADMIN))["system_count"] == 1


def test_two_labels_differing_only_in_case_never_answer_each_others_scan():
    """m5: the case-blind lookup let 'Ab12cd34ef' answer a scan of
    'AB12CD34EF' (and vice versa)."""
    from api.services.barcode import find_unit_by_code

    coll = mongomock.MongoClient()["bc"]["stock_units"]
    coll.insert_one({"stock_id": "LEGACY", "barcode": "Ab12cd34ef"})
    coll.insert_one({"stock_id": "MINTED", "barcode": "AB12CD34EF"})
    assert find_unit_by_code(coll, "AB12CD34EF")["stock_id"] == "MINTED"
    assert find_unit_by_code(coll, "ab12cd34ef")["stock_id"] == "MINTED"  # typed lower = minted
    assert find_unit_by_code(coll, "Ab12cd34ef")["stock_id"] == "LEGACY"
    coll.delete_one({"stock_id": "MINTED"})
    assert find_unit_by_code(coll, "ab12CD34ef")["stock_id"] == "LEGACY"  # only one fits
    coll.insert_one({"stock_id": "LEGACY2", "barcode": "aB12CD34ef"})
    assert find_unit_by_code(coll, "ab12cd34ef") is None, "ambiguous: refused, not guessed"


# ---------------------------------------------------------------------------
# Review round 2 (PR #1260)
# ---------------------------------------------------------------------------


def test_lens_reserve_and_commit_over_http_are_admin_only(lens, monkeypatch):
    """Reserve then commit lowers on_hand -- a write-off driven by hand. Over
    HTTP: ADMIN only. The till / workshop reach the same functions through
    the server-side hook, which keeps working for them."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.auth import get_current_user

    ls, coll = lens
    app = FastAPI()
    app.include_router(ls.router, prefix="/lens-stock")
    who = {"user": MANAGER}
    app.dependency_overrides[get_current_user] = lambda: who["user"]
    http = TestClient(app)
    body = {"store_id": STORE, "sph": 0.0, "cyl": 0.0, "add": None, "qty": 1}
    for door in ("reserve", "commit"):
        assert http.post(f"/lens-stock/L1/{door}", json=body).status_code == 403, door
    assert coll.find_one({"line_stock_id": "C1"})["on_hand"] == 5

    # the server-side hook path (a direct call) still works for the till
    monkeypatch.setattr(ls, "_ledger_lens_event", lambda *a, **k: None, raising=False)
    payload = ls.ReserveCommitReleasePayload(**body)
    _run(ls.reserve_cell("L1", payload, MANAGER))
    assert coll.find_one({"line_stock_id": "C1"})["reserved"] == 1


def test_one_order_line_cannot_cover_more_pieces_than_it_sold(serial_db):
    """Round 2: the same order used to be reusable for any number of hand
    mark-solds. Its line sold ONE piece of P1, and one is already sold."""
    st, mdb = serial_db
    mdb["orders"].insert_one({"order_id": "O1", "store_id": STORE, "status": "CONFIRMED",
                              "items": [{"product_id": "P1", "quantity": 1}]})
    mdb["stock_units"].insert_one({"stock_id": "U0", "serial": "SN-0", "product_id": "P1",
                                   "store_id": STORE, "status": "SOLD", "order_id": "O1"})
    with pytest.raises(HTTPException) as exc:
        _run(st.mark_sold("SN-1", st.MarkSoldBody(order_id="O1"), MANAGER))
    assert exc.value.status_code == 403
    assert mdb["stock_units"].find_one({"serial": "SN-1"})["status"] == "IN_STOCK"

    mdb["orders"].update_one({"order_id": "O1"}, {"$set": {"items": [{"product_id": "P1", "quantity": 2}]}})
    assert _run(st.mark_sold("SN-1", st.MarkSoldBody(order_id="O1"), MANAGER))["status"] == "SOLD"


def test_a_manager_creates_only_an_empty_lens_cell_with_a_reason(lens, monkeypatch):
    ls, coll = lens
    monkeypatch.setattr(ls, "validate_lens_stock_line_payload", lambda raw, lens_line: dict(raw))
    base = {"lens_line_id": "L1", "store_id": STORE, "sph": 2.0, "cyl": 0.0}
    with pytest.raises(HTTPException) as exc:
        _run(ls.create_cell(ls.StockCellCreate(**base, on_hand=3, notes="opening"), MANAGER))
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        _run(ls.create_cell(ls.StockCellCreate(**base), MANAGER))
    assert exc.value.status_code == 400
    assert coll.count_documents({"sph": 2.0}) == 0
    _run(ls.create_cell(ls.StockCellCreate(**base, notes="new power range"), MANAGER))
    assert coll.find_one({"sph": 2.0})["on_hand"] == 0
    _run(ls.create_cell(ls.StockCellCreate(**{**base, "sph": 3.0}, on_hand=4), ADMIN))
    assert coll.find_one({"sph": 3.0})["on_hand"] == 4
