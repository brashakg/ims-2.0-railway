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
