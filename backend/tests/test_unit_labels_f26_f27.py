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

    def unit(
        barcode,
        status="AVAILABLE",
        store=STORE,
        grn=GRN,
        printed=False,
        created=datetime(2026, 9, 27, 10, 0, 0),
        **extra,
    ):
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
                "created_at": created,
                **extra,
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


def test_units_view_by_receipt_follows_the_receipts_shop(world):
    # An admin whose active store is elsewhere receives a PO for BV-DHN-02: the
    # dialog after receiving must list that receipt's units, not the (empty)
    # set at the admin's active store.
    world["unit"]("BV--RCPTSHOP")
    world["as_user"](dict(ADMIN, active_store_id=OTHER))
    r = world["http"].get("/inventory/units", params={"grn_id": GRN})
    assert r.status_code == 200, r.text
    assert [u["barcode"] for u in r.json()["units"]] == ["BV--RCPTSHOP"]


def test_units_view_by_receipt_never_leaks_another_shops_units(world):
    world["unit"]("BV--NOTYOURS")
    world["as_user"](
        dict(MANAGER, user_id="mgr_bok", store_ids=[OTHER], active_store_id=OTHER)
    )
    r = world["http"].get("/inventory/units", params={"grn_id": GRN})
    assert r.status_code == 200, r.text
    assert r.json()["units"] == []


# Every role that reaches these reads, split by the cost rule (cost_mask.py:
# SUPERADMIN / ADMIN / ACCOUNTANT see cost; everyone else never gets it). A
# test that only sent STORE_MANAGER vs ADMIN let a leak to the counter roles
# (SALES_STAFF, CASHIER) pass.
_NO_COST_ROLES = ["SALES_STAFF", "CASHIER", "STORE_MANAGER", "CATALOG_MANAGER"]
_COST_ROLES = ["ADMIN", "ACCOUNTANT"]


def _as_role(role):
    return {
        "user_id": f"u_{role.lower()}",
        "roles": [role],
        "store_ids": [STORE],
        "active_store_id": STORE,
    }


@pytest.mark.parametrize(
    "role,sees_cost",
    [(r, False) for r in _NO_COST_ROLES] + [(r, True) for r in _COST_ROLES],
)
def test_units_view_shows_cost_only_to_roles_that_see_cost(world, role, sees_cost):
    world["unit"]("BV--COST0001")
    world["as_user"](_as_role(role))
    r = world["http"].get("/inventory/units", params={"product_id": world["pid"]})
    assert r.status_code == 200, r.text
    first = r.json()["units"][0]
    if sees_cost:
        assert first["cost_price"] == 4200.0
    else:
        assert "cost_price" not in first


def test_units_view_refuses_another_shop(world):
    r = world["http"].get(
        "/inventory/units", params={"product_id": world["pid"], "store_id": OTHER}
    )
    assert r.status_code == 403


def test_units_view_needs_a_product_or_a_receipt(world):
    assert world["http"].get("/inventory/units").status_code == 400


@pytest.mark.parametrize(
    "role,sees_cost",
    [(r, False) for r in _NO_COST_ROLES] + [(r, True) for r in _COST_ROLES],
)
def test_per_unit_stock_read_no_longer_leaks_cost(world, role, sees_cost):
    world["unit"]("BV--LEAK0001", unit_cost=4200.0)
    world["as_user"](_as_role(role))
    r = world["http"].get(
        "/inventory/stock", params={"store_id": STORE, "product_id": world["pid"]}
    )
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    if sees_cost:
        assert item["cost_price"] == 4200.0 and item["unit_cost"] == 4200.0
    else:
        assert "cost_price" not in item and "unit_cost" not in item


# ---------------------------------------------------------------------------
# a transferred-in unit reads as received HERE, by the transfer, on its day
# ---------------------------------------------------------------------------


def test_units_view_shows_a_transferred_in_unit_by_its_transfer(world):
    # Minted at BV-DHN-02 on 17 Sep by RCPT/1, shipped, then re-homed at
    # BV-BOK-01 by transfers._rehome (these are the fields it writes; its
    # received_at is datetime.now().isoformat(), naive UTC).
    world["unit"](
        "BV--TRANSFER",
        store=OTHER,
        created=datetime(2026, 9, 17, 10, 0, 0),
        grn_number="RCPT/1",
        source_type="TRANSFER",
        source_id="TR-1",
        transfer_number="TRF/BV-DHN-02/0001",
        from_store_id=STORE,
        received_at="2026-09-28T20:00:00",  # 01:30 IST on 29 Sep
    )
    world["as_user"](
        dict(MANAGER, user_id="mgr_bok", store_ids=[OTHER], active_store_id=OTHER)
    )
    r = world["http"].get("/inventory/units", params={"product_id": world["pid"]})
    assert r.status_code == 200, r.text
    (u,) = r.json()["units"]
    assert u["source"] == "TRANSFER"
    assert u["received_on"] == "2026-09-29"  # the day it arrived here, IST
    assert u["transfer_number"] == "TRF/BV-DHN-02/0001"
    assert u["from_store_id"] == STORE


def test_units_view_received_on_is_the_ist_day_of_minting(world):
    world["unit"]("BV--NIGHT001", created=datetime(2026, 9, 26, 20, 0, 0))
    (u,) = world["http"].get(
        "/inventory/units", params={"product_id": world["pid"]}
    ).json()["units"]
    assert u["received_on"] == "2026-09-27"  # 20:00 UTC = 01:30 IST next day


# ---------------------------------------------------------------------------
# the read cap never drops a unit still on the shelf
# ---------------------------------------------------------------------------


def test_units_on_the_shelf_survive_the_read_cap(world, monkeypatch):
    from api.routers.inventory import stock as stock_mod

    monkeypatch.setattr(stock_mod, "_UNITS_LIMIT", 3)
    for i in range(3):  # three boxes sold long ago...
        world["unit"](f"BV--SOLD000{i}", status="SOLD", created=datetime(2026, 9, 1, 10, i))
    world["unit"]("BV--SHELF001", created=datetime(2026, 9, 20, 10, 0))  # ...one still here
    units = world["http"].get(
        "/inventory/units", params={"product_id": world["pid"]}
    ).json()["units"]
    assert len(units) == 3
    assert units[0]["barcode"] == "BV--SHELF001"
    assert units[0]["status"] == "AVAILABLE"


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


def test_barcode_printed_policy_row_matches_the_route_gate(world):
    # The RBAC policy row is what the middleware and the access matrix read;
    # the route's own require_roles is what actually answers. Adding a role to
    # one and not the other passed every rbac suite, so pin them to each other.
    from api.services.rbac_policy import ALL_ROLES, check_access

    sid = world["unit"]("BV--GATE0001")
    for role in ALL_ROLES:
        world["as_user"](_as_role(role))
        r = world["http"].post("/inventory/units/barcode-printed", json={"stock_ids": [sid]})
        route_allows = r.status_code != 403
        policy_allows = check_access(
            "POST", "/api/v1/inventory/units/barcode-printed", [role]
        )
        assert route_allows == policy_allows, (role, r.status_code)
