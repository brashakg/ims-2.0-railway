"""BUG-119 / BUG-118: server-side order price integrity.

OrderItemCreate carries no mrp/offer_price, so the old offer<=MRP guards were
dead and a client could set ANY unit_price. create_order + add_order_item now
look up the catalog product and enforce: unit_price <= MRP (or offer_price when
HQ-discounted), unit_price >= cost (priced lines), the effective discount
(max of explicit + implied-from-unit-price) <= cap, and NO further store
discount on an HQ-discounted (offer<MRP) item (SYSTEM_INTENT s3)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


@pytest.fixture
def priced_orders(monkeypatch):
    from tests.test_walkouts import FakeDB
    from api.routers import orders as orders_module
    from api import dependencies as deps_module
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.audit_repository import AuditRepository

    fake_db = FakeDB()
    order_repo = OrderRepository(fake_db.get_collection("orders"))
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))

    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(orders_module, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(deps_module, "get_audit_repository", lambda: audit_repo)

    customer_repo.create(
        {"customer_id": "cust-x", "name": "Test", "mobile": "9100000099", "phone": "9100000099"}
    )
    return {"db": fake_db, "monkeypatch": monkeypatch}


def _seed_product(priced, *, pid="FR-PRICED-1", mrp=10000.0, offer_price=None,
                  cost_price=4000.0, discount_category="MASS", brand=None):
    from api.routers import orders as orders_module
    from database.repositories.product_repository import ProductRepository

    coll = priced["db"].get_collection("products")
    repo = ProductRepository(coll)
    doc = {
        "product_id": pid, "name": "Priced Frame", "category": "FRAME",
        "mrp": mrp, "cost_price": cost_price, "discount_category": discount_category,
        "is_active": True,
    }
    if offer_price is not None:
        doc["offer_price"] = offer_price
    if brand:
        doc["brand"] = brand
    repo.create(doc)
    priced["monkeypatch"].setattr(orders_module, "get_product_repository", lambda: repo)
    return pid


def _item(pid, unit_price, **over):
    it = {"product_id": pid, "product_name": "Priced Frame", "item_type": "FRAME",
          "category": "FRAME", "quantity": 1, "unit_price": unit_price}
    it.update(over)
    return it


def _post(client, headers, items, **extra):
    return client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-x", "items": items, **extra},
        headers=headers,
    )


def test_bug119_unit_price_above_mrp_blocked(client, auth_headers, priced_orders):
    pid = _seed_product(priced_orders, mrp=10000.0)
    r = _post(client, auth_headers, [_item(pid, 15000.0)])
    assert r.status_code == 400, r.text
    assert "mrp" in r.text.lower()


def test_bug119_unit_price_below_cost_blocked_even_for_admin(client, auth_headers, priced_orders):
    pid = _seed_product(priced_orders, mrp=10000.0, cost_price=4000.0)
    r = _post(client, auth_headers, [_item(pid, 1000.0)])  # below cost
    assert r.status_code == 400, r.text
    assert "below cost" in r.text.lower()


def test_bug119_unit_price_at_mrp_ok(client, auth_headers, priced_orders):
    pid = _seed_product(priced_orders, mrp=10000.0, cost_price=4000.0)
    r = _post(client, auth_headers, [_item(pid, 10000.0)])
    assert r.status_code in (200, 201), r.text


def test_bug119_virtual_item_unconstrained(client, auth_headers, priced_orders):
    _seed_product(priced_orders)  # repo present, but item is virtual
    r = _post(client, auth_headers, [_item("custom-lens-x", 50000.0)])
    assert r.status_code in (200, 201), r.text


def test_bug119_implied_discount_exceeds_cap_blocked(client, staff_headers, priced_orders):
    # SALES_STAFF cap 10%. unit_price 8000 on MRP 10000 = 20% implied discount.
    pid = _seed_product(priced_orders, mrp=10000.0, cost_price=4000.0, discount_category="MASS")
    r = _post(client, staff_headers, [_item(pid, 8000.0)])
    assert r.status_code == 403, r.text
    assert "exceeds your limit" in r.text.lower()


def test_bug119_within_cap_ok(client, staff_headers, priced_orders):
    # 5% off (unit 9500 on MRP 10000) is within the 10% staff cap.
    pid = _seed_product(priced_orders, mrp=10000.0, cost_price=4000.0, discount_category="MASS")
    r = _post(client, staff_headers, [_item(pid, 9500.0)])
    assert r.status_code in (200, 201), r.text


def test_bug118_hq_discounted_no_further_discount(client, staff_headers, priced_orders):
    pid = _seed_product(priced_orders, mrp=10000.0, offer_price=9000.0, cost_price=4000.0)
    r = _post(client, staff_headers, [_item(pid, 9000.0, discount_percent=5.0)])
    assert r.status_code == 403, r.text
    assert "hq" in r.text.lower() or "further" in r.text.lower()


def test_bug118_hq_discounted_at_offer_ok(client, staff_headers, priced_orders):
    # Selling exactly at offer_price with no extra discount is allowed.
    pid = _seed_product(priced_orders, mrp=10000.0, offer_price=9000.0, cost_price=4000.0)
    r = _post(client, staff_headers, [_item(pid, 9000.0)])
    assert r.status_code in (200, 201), r.text


def test_bug118_hq_discounted_below_offer_blocked(client, staff_headers, priced_orders):
    # Pricing BELOW the HQ offer is a further discount -> blocked.
    pid = _seed_product(priced_orders, mrp=10000.0, offer_price=9000.0, cost_price=4000.0)
    r = _post(client, staff_headers, [_item(pid, 8500.0)])
    assert r.status_code == 403, r.text


def test_bug118_cart_discount_on_hq_discounted_blocked(client, staff_headers, priced_orders):
    pid = _seed_product(priced_orders, mrp=10000.0, offer_price=9000.0, cost_price=4000.0)
    r = _post(client, staff_headers, [_item(pid, 9000.0)], cart_discount_percent=5.0)
    assert r.status_code == 403, r.text
    assert "cart discount" in r.text.lower()
