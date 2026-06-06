"""BUG-097: a serialized non-lens line (FRAME/SUNGLASS/ACCESSORY...) must not
oversell -- order creation is rejected (409) when the product is serialized-
tracked at the store but has fewer AVAILABLE units than requested. A product that
is NOT serialized-tracked (no stock_units) or a virtual item is never blocked."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


class _FakeStock:
    """by_product: pid -> {'total': N (any status), 'available': M}."""
    def __init__(self, by_product):
        self._b = by_product

    def count(self, query):
        return self._b.get(query.get("product_id"), {}).get("total", 0)

    def find_available(self, pid, store_id):
        return self._b.get(pid, {}).get("available", 0)

    def find_by_product_store(self, pid, store_id):
        n = self._b.get(pid, {}).get("available", 0)
        return [{"stock_id": f"{pid}-U{i}", "status": "AVAILABLE"} for i in range(n)]

    def mark_sold(self, sid, oid):
        return True


@pytest.fixture
def oversell(monkeypatch):
    from tests.test_walkouts import FakeDB
    from api.routers import orders as om
    from api import dependencies as dm
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.audit_repository import AuditRepository
    from database.repositories.product_repository import ProductRepository

    db = FakeDB()
    order_repo = OrderRepository(db.get_collection("orders"))
    cust_repo = CustomerRepository(db.get_collection("customers"))
    audit_repo = AuditRepository(db.get_collection("audit_logs"))
    prod_repo = ProductRepository(db.get_collection("products"))
    monkeypatch.setattr(om, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(om, "get_customer_repository", lambda: cust_repo)
    monkeypatch.setattr(om, "get_product_repository", lambda: prod_repo)
    monkeypatch.setattr(om, "get_walkin_counter_repository", lambda: None)
    monkeypatch.setattr(dm, "get_audit_repository", lambda: audit_repo)
    cust_repo.create({"customer_id": "cust-x", "name": "T", "mobile": "9100000099", "phone": "9100000099"})
    prod_repo.create({"product_id": "FR-1", "name": "Frame", "category": "FRAME",
                      "mrp": 10000.0, "cost_price": 1000.0, "is_active": True})

    def set_stock(by_product):
        monkeypatch.setattr(om, "get_stock_repository", lambda: _FakeStock(by_product))

    return {"set_stock": set_stock}


def _post(client, headers, items, **extra):
    return client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-x", "items": items, **extra},
        headers=headers,
    )


def _frame(qty=1, **over):
    it = {"product_id": "FR-1", "product_name": "Frame", "item_type": "FRAME",
          "category": "FRAME", "quantity": qty, "unit_price": 5000.0}
    it.update(over)
    return it


def test_oversell_blocked_when_zero_available(client, auth_headers, oversell):
    oversell["set_stock"]({"FR-1": {"total": 3, "available": 0}})  # tracked, none free
    r = _post(client, auth_headers, [_frame(1)])
    assert r.status_code == 409, r.text
    assert "insufficient stock" in r.text.lower()


def test_sale_ok_when_stock_available(client, auth_headers, oversell):
    oversell["set_stock"]({"FR-1": {"total": 3, "available": 2}})
    r = _post(client, auth_headers, [_frame(1)])
    assert r.status_code in (200, 201), r.text


def test_oversell_blocked_when_qty_exceeds_available(client, auth_headers, oversell):
    oversell["set_stock"]({"FR-1": {"total": 5, "available": 1}})
    r = _post(client, auth_headers, [_frame(2)])
    assert r.status_code == 409, r.text


def test_untracked_product_not_blocked(client, auth_headers, oversell):
    # FR-1 has NO stock_units rows (count 0) -> not serialized-tracked -> allowed.
    oversell["set_stock"]({"FR-1": {"total": 0, "available": 0}})
    r = _post(client, auth_headers, [_frame(1)])
    assert r.status_code in (200, 201), r.text


def test_virtual_item_not_blocked(client, auth_headers, oversell):
    oversell["set_stock"]({})  # no stock at all
    r = _post(client, auth_headers, [_frame(1, product_id="custom-frame")])
    assert r.status_code in (200, 201), r.text


def test_explicit_stock_id_line_not_blocked_by_assert(client, auth_headers, oversell):
    # A line carrying an explicit stock_id flows through mark_sold, not the assert.
    oversell["set_stock"]({"FR-1": {"total": 3, "available": 0}})
    r = _post(client, auth_headers, [_frame(1, stock_id="FR-1-U0")])
    assert r.status_code in (200, 201), r.text
