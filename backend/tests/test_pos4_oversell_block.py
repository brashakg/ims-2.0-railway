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
SKU_PID = "FR-BY-SKU"  # canonical id; the till may send its SKU instead
SKU = "CA3-BLU-50"


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
    sku_doc = _product(SKU_PID, "Carrera CA 3 Blue 50", "FRAME")
    sku_doc["sku"] = SKU
    prod_repo.create(sku_doc)
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
    return {"db": db, "stock": stock_repo, "orders": order_repo}


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


# ---------------------------------------------------------------------------
# Review round 1 (PR #1300)
# ---------------------------------------------------------------------------


def _draft(client, till):
    """A DRAFT bill holding one service line, ready for the add-line door."""
    r = _sell(client, [_line(SVC_PID, item_type="SERVICE", name="Frame fitting")])
    assert r.status_code in (200, 201), r.text
    return _order_id(r)


def _add(client, order_id, line):
    return client.post(f"/api/v1/orders/{order_id}/items", json=line, headers=_token())


def _sold(till):
    return list(till["db"].get_collection("stock_units").find({"status": "SOLD"}, {"_id": 0}))


def _claimed_minutes_ago(till, order_id, minutes):
    from datetime import datetime, timedelta

    till["db"].get_collection("stock_units").update_many(
        {"order_id": order_id},
        {"$set": {"sold_at": datetime.now() - timedelta(minutes=minutes)}},
    )


def test_the_guard_refuses_a_never_stocked_line_directly(till):
    from fastapi import HTTPException

    from api.routers.orders import stock as guard

    with pytest.raises(HTTPException) as exc:
        guard._assert_serialized_stock_available(
            [_line(NEW_PID, name="Carrera CA 2 Black 52")], STORE
        )
    assert exc.value.status_code == 409


def test_a_bill_saved_before_the_error_keeps_its_unit(client, till, monkeypatch):
    real = till["orders"].create_unique

    def saved_then_raised(*a, **k):
        real(*a, **k)
        raise RuntimeError("socket closed after the write")

    monkeypatch.setattr(till["orders"], "create_unique", saved_then_raised)
    r = _sell(client, [_line()])

    assert r.status_code == 500, r.text
    saved = _orders(till)
    assert len(saved) == 1
    unit = _unit(till)
    assert unit["status"] == "SOLD" and unit["order_id"] == saved[0]["order_id"], unit


def test_a_bill_saved_but_reported_unsaved_keeps_its_unit(client, till, monkeypatch):
    real = till["orders"].create_unique

    def saved_returned_none(*a, **k):
        real(*a, **k)
        return None

    monkeypatch.setattr(till["orders"], "create_unique", saved_returned_none)
    r = _sell(client, [_line()])

    assert r.status_code == 500, r.text
    saved = _orders(till)
    assert len(saved) == 1
    assert _unit(till)["order_id"] == saved[0]["order_id"]


def test_a_line_saved_before_the_error_keeps_its_unit(client, till, monkeypatch):
    order_id = _draft(client, till)
    real = till["orders"].update

    def saved_then_raised(*a, **k):
        real(*a, **k)
        raise RuntimeError("socket closed after the write")

    monkeypatch.setattr(till["orders"], "update", saved_then_raised)
    r = _add(client, order_id, _line())

    assert r.status_code >= 400, r.text
    unit = _unit(till)
    assert unit["status"] == "SOLD" and unit["order_id"] == order_id, unit


def test_the_add_line_door_takes_a_product_named_by_its_sku(client, till):
    till["stock"].create(
        {"stock_id": "SU-SKU", "barcode": "BVL00000009", "product_id": SKU_PID,
         "store_id": STORE, "status": "AVAILABLE", "quantity": 1, "cost_price": 4000.0}
    )
    order_id = _draft(client, till)

    r = _add(client, order_id, _line(SKU, name="Carrera CA 3 Blue 50"))

    assert r.status_code in (200, 201), r.text
    unit = till["db"].get_collection("stock_units").find_one({"stock_id": "SU-SKU"})
    assert unit["status"] == "SOLD" and unit["order_id"] == order_id, unit
    lines = till["orders"].find_by_id(order_id)["items"]
    assert lines[-1]["product_id"] == SKU_PID  # the canonical id, as create stamps


def test_the_add_line_door_loses_the_last_unit_race_cleanly(client, till, monkeypatch):
    order_id = _draft(client, till)
    till["db"].get_collection("stock_units").update_one(
        {"stock_id": "SU-LAST"}, {"$set": {"status": "SOLD", "order_id": "OTHER-BILL"}}
    )
    monkeypatch.setattr(till["stock"], "find_available", lambda *a, **k: 1)
    before = till["orders"].find_by_id(order_id)["items"]

    r = _add(client, order_id, _line())

    assert r.status_code == 409, r.text
    assert till["orders"].find_by_id(order_id)["items"] == before
    assert _unit(till)["order_id"] == "OTHER-BILL"


def test_the_refusal_counts_the_whole_bill(client, till, monkeypatch):
    # Two lines of the one product, one unit on the shelf, the gate fooled.
    monkeypatch.setattr(till["stock"], "find_available", lambda *a, **k: 2)

    r = _sell(client, [_line(), _line()])

    assert r.status_code == 409, r.text
    assert "1 available at this shop, 2 on this bill" in r.json()["detail"], r.text
    assert _unit(till)["status"] == "AVAILABLE"


def test_a_stock_backend_error_during_the_claim_is_not_out_of_stock(client, till, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("mongo unreachable")

    monkeypatch.setattr(till["stock"], "claim_one_available", down)
    r = _sell(client, [_line()])

    assert r.status_code == 503, r.text
    assert "could not be checked" in r.json()["detail"]
    assert _orders(till) == []


def test_the_real_claim_raises_a_backend_error(till, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("mongo unreachable")

    monkeypatch.setattr(till["stock"].collection, "find_one_and_update", down)
    with pytest.raises(RuntimeError):
        till["stock"].claim_one_available(PID, STORE, "ORD-X")


def test_the_sweep_gives_back_a_unit_claimed_for_a_bill_never_saved(till):
    from api.routers.orders import stock as guard

    guard._claim_units("ORD-GHOST", [_line()], STORE)
    assert _unit(till)["status"] == "SOLD"
    _claimed_minutes_ago(till, "ORD-GHOST", 20)

    freed = guard.sweep_unsaved_claims()

    assert freed == ["SU-LAST"]
    assert _unit(till)["status"] == "AVAILABLE"
    assert guard.sweep_unsaved_claims() == []  # idempotent


def test_the_sweep_leaves_a_fresh_claim_and_a_saved_bill_alone(client, till):
    from api.routers.orders import stock as guard

    r = _sell(client, [_line()])
    assert r.status_code in (200, 201), r.text
    till["stock"].create(
        {"stock_id": "SU-FRESH", "barcode": "BVL00000003", "product_id": PID,
         "store_id": STORE, "status": "AVAILABLE", "quantity": 1, "cost_price": 4000.0}
    )
    guard._claim_units("ORD-IN-FLIGHT", [_line()], STORE)
    _claimed_minutes_ago(till, _order_id(r), 20)  # the saved bill's, aged too

    assert guard.sweep_unsaved_claims() == []  # a claim under 15 minutes waits
    _claimed_minutes_ago(till, "ORD-IN-FLIGHT", 20)
    assert guard.sweep_unsaved_claims() == ["SU-FRESH"]
    assert _unit(till)["order_id"] == _order_id(r)  # the saved bill's unit stays


def test_a_crashed_sale_retried_after_the_sweep_sells_once(client, till):
    from api.routers.orders import stock as guard

    # The till's first press claimed the last unit and the server died before
    # the bill was written (no compensating release ran).
    guard._claim_units("ORD-CRASHED", [_line()], STORE)
    _claimed_minutes_ago(till, "ORD-CRASHED", 20)
    guard.sweep_unsaved_claims()

    r = client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-till", "items": [_line()]},
        headers={**_token(), "Idempotency-Key": "press-1"},
    )

    assert r.status_code in (200, 201), r.text
    sold = _sold(till)
    assert len(sold) == 1 and sold[0]["order_id"] == _order_id(r), sold

