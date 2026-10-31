"""POS 4 (owner ruling 2026-08-25 "oversell BLOCKS"; fix approved 2026-10-23).

Nothing may be sold at the till that is not on the shelf:
  * a stock product with NO stock unit at this shop at all (never stocked)
    used to sail through -- the gate skipped any product it had never seen a
    unit of. It is now refused with a plain message;
  * the last unit could be sold twice: both tills passed the read-only gate,
    both bills were saved, and the loser's first-available claim (after the
    save) found nothing and only logged it. The first-available unit is now
    claimed atomically BEFORE the bill is saved, so the loser gets a 409 and
    no bill exists;
  * made-to-order lenses, the lens grid's lines, services and eye tests never
    take a stock unit and are never blocked (the till's one classification,
    orders/stock._takes_serialized_stock);
  * cancelling the sale puts the claimed unit back on the shelf.

Engine: mongomock with the REAL StockRepository / OrderRepository, so the atomic
claim and the cancel's release are the real filters. No emoji (Windows cp1252).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

mongomock = pytest.importorskip("mongomock")

STORE = "BV-TEST-01"  # the conftest admin token's active store
PID = "FR-LAST-1"
NEW_PID = "FR-NEVER-STOCKED"
SVC_PID = "SVC-FITTING"


def _token():
    from api.routers.auth import create_access_token

    return {
        "Authorization": "Bearer "
        + create_access_token(
            {
                "user_id": "till-user",
                "username": "till_user",
                "roles": ["SUPERADMIN"],
                "store_ids": [STORE],
                "active_store_id": STORE,
                "discount_cap": 10.0,
            }
        )
    }


def _product(pid, name, category):
    return {
        "product_id": pid,
        "name": name,
        "sku": pid,
        "brand": "Carrera",
        "category": category,
        "hsn_code": "900311",
        "mrp": 10000.0,
        "offer_price": 9000.0,
        "cost_price": 4000.0,
        "pricing": {"mrp": 10000.0, "cost_price": 4000.0},
        "is_active": True,
    }


@pytest.fixture
def till(monkeypatch):
    from api import dependencies as dm
    from api.routers import orders as om
    from database.repositories.audit_repository import AuditRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.order_repository import OrderRepository
    from database.repositories.product_repository import (
        ProductRepository,
        StockRepository,
    )

    db = mongomock.MongoClient()["pos4_oversell"]
    order_repo = OrderRepository(db.get_collection("orders"))
    cust_repo = CustomerRepository(db.get_collection("customers"))
    prod_repo = ProductRepository(db.get_collection("products"))
    stock_repo = StockRepository(db.get_collection("stock_units"))
    audit_repo = AuditRepository(db.get_collection("audit_logs"))

    monkeypatch.setattr(om, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(om, "get_product_repository", lambda: prod_repo)
    monkeypatch.setattr(om, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(om, "get_customer_repository", lambda: cust_repo)
    monkeypatch.setattr(om, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(dm, "get_audit_repository", lambda: audit_repo)

    cust_repo.create(
        {"customer_id": "cust-till", "name": "Asha", "mobile": "9100000078", "phone": "9100000078"}
    )
    prod_repo.create(_product(PID, "Carrera CA 1 Havana 54", "FRAME"))
    prod_repo.create(_product(NEW_PID, "Carrera CA 2 Black 52", "FRAME"))
    prod_repo.create(_product(SVC_PID, "Frame fitting", "SERVICES"))
    stock_repo.create(
        {
            "stock_id": "SU-LAST",
            "barcode": "BVL00000001",
            "product_id": PID,
            "store_id": STORE,
            "status": "AVAILABLE",
            "quantity": 1,
            "cost_price": 4000.0,
        }
    )
    return {"db": db, "stock": stock_repo}


def _line(pid=PID, item_type="FRAME", name="Carrera CA 1 Havana 54", **over):
    line = {
        "item_type": item_type,
        "product_id": pid,
        "product_name": name,
        "sku": pid,
        "category": item_type,
        "quantity": 1,
        "unit_price": 9000.0,
    }
    line.update(over)
    return line


def _sell(client, items):
    return client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-till", "items": items},
        headers=_token(),
    )


def _orders(till):
    return list(till["db"].get_collection("orders").find({}, {"_id": 0}))


def _unit(till):
    return till["db"].get_collection("stock_units").find_one({"stock_id": "SU-LAST"}, {"_id": 0})


def _order_id(resp):
    body = resp.json()
    return body.get("order_id") or (body.get("order") or {}).get("order_id")


def test_a_never_stocked_product_is_refused_with_a_plain_message(client, till):
    r = _sell(client, [_line(NEW_PID, name="Carrera CA 2 Black 52")])

    assert r.status_code == 409, r.text
    detail = r.json().get("detail") or ""
    assert "Insufficient stock for 'Carrera CA 2 Black 52'" in detail, detail
    assert "0 available at this shop, 1 on this bill" in detail, detail
    assert _orders(till) == []  # no bill exists


def test_two_tills_selling_the_last_unit_only_one_bill_is_made(client, till, monkeypatch):
    # Both tills read the shelf before either sold: each saw 1 unit, so both
    # pass the read-only gate. The atomic claim is what must decide.
    monkeypatch.setattr(till["stock"], "find_available", lambda *a, **k: 1)

    first = _sell(client, [_line()])
    second = _sell(client, [_line()])

    codes = sorted([first.status_code, second.status_code])
    assert codes[0] in (200, 201) and codes[1] == 409, (first.text, second.text)
    assert len(_orders(till)) == 1  # the loser made no bill
    unit = _unit(till)
    assert unit["status"] == "SOLD" and unit["order_id"] == _order_id(first), unit
    assert "Insufficient stock" in (second.json().get("detail") or "")


def test_a_quantity_beyond_the_shelf_on_one_line_is_refused(client, till, monkeypatch):
    monkeypatch.setattr(till["stock"], "find_available", lambda *a, **k: 2)

    r = _sell(client, [_line(quantity=2)])

    assert r.status_code == 409, r.text
    assert _orders(till) == []
    assert _unit(till)["status"] == "AVAILABLE"  # the one unit it did claim went back


def test_a_scanned_unit_is_not_taken_by_an_earlier_typed_line(client, till, monkeypatch):
    till["stock"].create(
        {"stock_id": "SU-SECOND", "barcode": "BVL00000002", "product_id": PID,
         "store_id": STORE, "status": "AVAILABLE", "quantity": 1, "cost_price": 4000.0}
    )
    # A typed line first, then a line naming SU-LAST (first on the shelf): the
    # typed line must take the OTHER unit, not the one the second line scanned.
    r = _sell(client, [_line(), _line(stock_id="SU-LAST")])

    assert r.status_code in (200, 201), r.text
    order_id = _order_id(r)
    second = till["db"].get_collection("stock_units").find_one({"stock_id": "SU-SECOND"})
    assert _unit(till)["order_id"] == order_id and second["order_id"] == order_id


def test_one_typed_line_takes_exactly_one_unit(client, till):
    till["stock"].create(
        {"stock_id": "SU-SECOND", "barcode": "BVL00000002", "product_id": PID,
         "store_id": STORE, "status": "AVAILABLE", "quantity": 1, "cost_price": 4000.0}
    )
    r = _sell(client, [_line()])

    assert r.status_code in (200, 201), r.text
    sold = list(till["db"].get_collection("stock_units").find({"status": "SOLD"}))
    assert len(sold) == 1, sold  # the after-save pass reuses the claimed unit


def test_a_cancelled_sale_puts_the_unit_back_on_the_shelf(client, till):
    r = _sell(client, [_line()])
    assert r.status_code in (200, 201), r.text
    assert _unit(till)["status"] == "SOLD"

    c = client.post(
        f"/api/v1/orders/{_order_id(r)}/cancel",
        params={"reason": "customer changed their mind"},
        headers=_token(),
    )
    assert c.status_code == 200, c.text
    assert _unit(till)["status"] == "AVAILABLE"

    again = _sell(client, [_line()])
    assert again.status_code in (200, 201), again.text


def test_a_service_product_with_no_stock_still_sells(client, till):
    r = _sell(client, [_line(SVC_PID, item_type="SERVICE", name="Frame fitting")])

    assert r.status_code in (200, 201), r.text


@pytest.mark.parametrize(
    "line",
    [
        _line("lens-1730000000", item_type="LENS", name="Made-to-order 1.6 SV"),
        _line("LENS-REAL-ID", item_type="LENS", name="Stock lens (lens grid)"),
        _line(SVC_PID, item_type="SERVICE", name="Frame fitting"),
        _line("EYE-TEST-1", item_type="EYE_TEST", name="Eye test"),
        _line("custom-1", item_type="FRAME", name="Custom item"),
    ],
    ids=["made-to-order-lens", "lens-grid", "service", "eye-test", "custom"],
)
def test_non_stock_lines_are_never_blocked_or_claimed(till, line):
    from api.routers.orders import stock as guard

    guard._assert_serialized_stock_available([line], STORE)
    assert not guard._claim_units("ORD-X", [line], STORE)
    assert _unit(till)["status"] == "AVAILABLE"
