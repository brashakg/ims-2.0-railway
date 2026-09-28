"""
F26 / F27 -- every received unit is listable and printable, and the ledger no
longer passes one unit's barcode off as the product's.

F27: receiving serialises every piece (9 Carrera = 9 stock_units, 9 barcodes)
but no screen listed them; the ledger row, drawer and CSV showed ONE unit's
code as if it were the product's, and it changed when that unit shipped.
F26: "Print labels" after receiving toasted success and printed nothing, so
barcode_printed stayed false on every unit.

These drive the REAL inventory router over a real Mongo (CI) or mongomock
(dev box). Expected values are written out by hand.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import inventory as inv_mod  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from tests.test_on_hand_is_one_rule import mongo_db  # noqa: E402,F401  (fixture)

STORE = "BV-DHN-02"
OTHER = "BV-BOK-01"
GRN = "GRN-9"

MANAGER = {
    "user_id": "mgr_dhn2",
    "roles": ["STORE_MANAGER"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}
ADMIN = {"user_id": "adm", "roles": ["ADMIN"], "store_ids": [], "active_store_id": STORE}
SALES = {
    "user_id": "sales",
    "roles": ["SALES_STAFF"],
    "store_ids": [STORE],
    "active_store_id": STORE,
}


@pytest.fixture
def world(mongo_db, monkeypatch):  # noqa: F811
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    for name in ("products", "stock_units"):
        mongo_db[name].delete_many({})
    monkeypatch.setattr(inv_mod, "_get_db", lambda: None)
    monkeypatch.setattr(
        inv_mod, "get_stock_repository", lambda: StockRepository(mongo_db["stock_units"])
    )
    monkeypatch.setattr(
        inv_mod, "get_product_repository", lambda: ProductRepository(mongo_db["products"])
    )

    pid = f"P-{uuid.uuid4().hex[:6]}"
    mongo_db["products"].insert_one(
        {
            "product_id": pid,
            "sku": "FR-CAR-8895",
            "name": "Carrera CA 8895 Black 54",
            "brand": "Carrera",
            "model": "CA 8895",
            "color": "807",
            "size": "54",
            "category": "FRAME",
            "mrp": 8990.0,
            "offer_price": 8990.0,
            "cost_price": 4200.0,
            "is_active": True,
        }
    )

    def unit(barcode, status="AVAILABLE", store=STORE, grn=GRN, printed=False):
        sid = f"STK-{uuid.uuid4().hex[:8]}"
        mongo_db["stock_units"].insert_one(
            {
                "stock_id": sid,
                "product_id": pid,
                "store_id": store,
                "barcode": barcode,
                "quantity": 1,
                "status": status,
                "location_code": "DEFAULT",
                "source_type": "GRN",
                "source_id": grn,
                "grn_number": f"RCPT/{store}/26-27/{grn[-1]}",
                "cost_price": 4200.0,
                "barcode_printed": printed,
                "created_at": datetime(2026, 9, 27, 10, 0, 0),
            }
        )
        return sid

    state = {"user": MANAGER}
    app = FastAPI()
    app.include_router(inv_mod.router, prefix="/inventory")

    async def _user():
        return state["user"]

    app.dependency_overrides[get_current_user] = _user
    return {
        "db": mongo_db,
        "pid": pid,
        "unit": unit,
        "http": TestClient(app),
        "as_user": lambda u: state.__setitem__("user", u),
    }


def _ledger_row(world):
    r = world["http"].get("/inventory/stock", params={"store_id": STORE})
    assert r.status_code == 200, r.text
    rows = [i for i in r.json()["items"] if i["product_id"] == world["pid"]]
    assert len(rows) == 1
    return rows[0]


# ---------------------------------------------------------------------------
# (5) the ledger stops presenting one unit's barcode as the product's
# ---------------------------------------------------------------------------


def test_ledger_row_never_carries_a_unit_barcode(world):
    world["unit"]("BV--00F1D2CC")
    world["unit"]("BV--91FA3858")
    row = _ledger_row(world)
    assert row["stock"] == 2
    assert row["barcode"] == ""  # the product has no barcode of its own


def test_ledger_row_keeps_the_products_own_barcode(world):
    world["db"]["products"].update_one(
        {"product_id": world["pid"]}, {"$set": {"barcode": "8056597000000"}}
    )
    world["unit"]("BV--00F1D2CC")
    assert _ledger_row(world)["barcode"] == "8056597000000"


# ---------------------------------------------------------------------------
# (1) the units view
# ---------------------------------------------------------------------------


def test_units_view_lists_every_unit_of_the_product_at_this_shop(world):
    world["unit"]("BV--AAAA0001")
    world["unit"]("BV--AAAA0002")
    world["unit"]("BV--AAAA0003", status="SOLD")
    world["unit"]("BV--OTHER001", store=OTHER)
    r = world["http"].get("/inventory/units", params={"product_id": world["pid"]})
    assert r.status_code == 200, r.text
    units = r.json()["units"]
    assert [u["barcode"] for u in units] == ["BV--AAAA0001", "BV--AAAA0002", "BV--AAAA0003"]
    assert [u["status"] for u in units] == ["AVAILABLE", "AVAILABLE", "SOLD"]
    first = units[0]
    assert first["grn_number"] == "RCPT/BV-DHN-02/26-27/9"
    assert first["barcode_printed"] is False
    # Everything the label needs rides on the unit.
    assert first["brand"] == "Carrera"
    assert first["model"] == "CA 8895"
    assert first["colour"] == "807"
    assert first["size"] == "54"
    assert first["mrp"] == 8990.0


def test_units_view_by_receipt_lists_only_that_receipt(world):
    world["unit"]("BV--RCPT0009")
    world["unit"]("BV--RCPT0010", grn="GRN-7")
    r = world["http"].get("/inventory/units", params={"grn_id": GRN})
    assert r.status_code == 200, r.text
    assert [u["barcode"] for u in r.json()["units"]] == ["BV--RCPT0009"]


def test_units_view_shows_cost_only_to_roles_that_see_cost(world):
    world["unit"]("BV--COST0001")
    r = world["http"].get("/inventory/units", params={"product_id": world["pid"]})
    assert "cost_price" not in r.json()["units"][0]
    world["as_user"](ADMIN)
    r = world["http"].get("/inventory/units", params={"product_id": world["pid"]})
    assert r.json()["units"][0]["cost_price"] == 4200.0


def test_units_view_refuses_another_shop(world):
    r = world["http"].get(
        "/inventory/units", params={"product_id": world["pid"], "store_id": OTHER}
    )
    assert r.status_code == 403


def test_units_view_needs_a_product_or_a_receipt(world):
    assert world["http"].get("/inventory/units").status_code == 400


def test_per_unit_stock_read_no_longer_leaks_cost(world):
    world["unit"]("BV--LEAK0001")
    r = world["http"].get(
        "/inventory/stock", params={"store_id": STORE, "product_id": world["pid"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] and "cost_price" not in r.json()["items"][0]


# ---------------------------------------------------------------------------
# (4) barcode_printed is recorded on exactly the units sent to print
# ---------------------------------------------------------------------------


def _printed(world, sid):
    doc = world["db"]["stock_units"].find_one({"stock_id": sid})
    return bool(doc.get("barcode_printed")), doc.get("barcode_printed_at")


def test_barcode_printed_is_recorded_on_the_units_sent(world):
    sent = world["unit"]("BV--SENT0001")
    not_sent = world["unit"]("BV--KEPT0001")
    other_shop = world["unit"]("BV--ELSE0001", store=OTHER)
    r = world["http"].post(
        "/inventory/units/barcode-printed", json={"stock_ids": [sent, other_shop]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1
    printed, at = _printed(world, sent)
    assert printed is True and at is not None
    assert _printed(world, not_sent)[0] is False
    assert _printed(world, other_shop)[0] is False  # not this manager's shop


def test_barcode_printed_is_a_stock_role_write(world):
    sid = world["unit"]("BV--SALE0001")
    world["as_user"](SALES)
    r = world["http"].post("/inventory/units/barcode-printed", json={"stock_ids": [sid]})
    assert r.status_code == 403
    assert _printed(world, sid)[0] is False
