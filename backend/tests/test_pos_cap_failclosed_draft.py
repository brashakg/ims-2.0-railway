"""POS discount-cap FAIL-CLOSED on resolver error — the ADD-ITEM-TO-DRAFT path.

Sibling of test_pos_cap_failclosed.py (which covers create_order). Same class of
bug in orders.py add_order_item (POST /orders/{id}/items): the product lookup is
`try: product = pr.find_by_id(pid) except Exception: product = None`. When the
fetch throws, product is None, so the category/luxury-brand cap-tightening block
is skipped and the per-line cap stays at the LOOSE role cap with NO luxury floor
-> a Cartier (2%) / luxury (5%) frame added to a DRAFT order could take the full
10-25% role cap.

THE FIX (mirror create_order ~line 1443): on a fetch failure, do NOT loosen the
cap. Tighten using the pure DB-free brand_cap_for(item.brand) so a named luxury
brand on the line payload keeps its floor; a plain MASS line keeps the normal
role cap so an ordinary add is NOT blocked.

Cases: (1) fetch throws on a Cartier line -> capped tight (2%), blocked at 8%.
(2) fetch throws on a MASS line -> normal 10% staff cap still applies (8% ok).
(3) fetch throws on a MASS line at 12% -> still blocked (role cap not loosened).
(4) happy path (fetch works): Cartier 3% blocked / 1% allowed (brand cap binds).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


@pytest.fixture
def draft_orders(monkeypatch):
    from tests.test_walkouts import FakeDB
    from api.routers import orders as orders_module
    from api import dependencies as deps_module
    from database.repositories.order_repository import OrderRepository
    from database.repositories.audit_repository import AuditRepository

    fake_db = FakeDB()
    order_repo = OrderRepository(fake_db.get_collection("orders"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(deps_module, "get_audit_repository", lambda: audit_repo)
    return {"db": fake_db, "order_repo": order_repo, "monkeypatch": monkeypatch}


def _seed_draft(draft, order_id="ORD-DRAFT-1", store_id="BV-TEST-01"):
    draft["order_repo"].create(
        {
            "order_id": order_id,
            "store_id": store_id,
            "status": "DRAFT",
            "customer_id": "cust-x",
            "items": [],
            "cart_discount_percent": 0,
        }
    )
    return order_id


def _seed_product(draft, *, pid, mrp=10000.0, cost_price=4000.0,
                  discount_category="MASS", brand=None):
    from api.routers import orders as orders_module
    from database.repositories.product_repository import ProductRepository

    repo = ProductRepository(draft["db"].get_collection("products"))
    doc = {"product_id": pid, "name": "Priced Frame", "category": "FRAME",
           "mrp": mrp, "cost_price": cost_price,
           "discount_category": discount_category, "is_active": True}
    if brand:
        doc["brand"] = brand
    repo.create(doc)
    draft["monkeypatch"].setattr(orders_module, "get_product_repository", lambda: repo)
    return pid


def _break_fetch(draft):
    """Force the product FETCH (pr.find_by_id) to THROW -> product=None -> the
    cap-tightening block is skipped (the exact bug). Drives the new `else`
    fail-closed branch."""
    from api.routers import orders as orders_module

    class _BoomRepo:
        def find_by_id(self, *_a, **_k):
            raise RuntimeError("simulated product fetch failure")

    draft["monkeypatch"].setattr(orders_module, "get_product_repository", lambda: _BoomRepo())


def _item(pid, *, discount_percent, brand=None, unit_price=10000.0):
    it = {"product_id": pid, "product_name": "Priced Frame", "item_type": "FRAME",
          "category": "FRAME", "quantity": 1, "unit_price": unit_price,
          "discount_percent": discount_percent}
    if brand:
        it["brand"] = brand
    return it


def _add(client, headers, order_id, item):
    return client.post(f"/api/v1/orders/{order_id}/items", json=item, headers=headers)


# --- 1. fetch throws on a LUXURY line -> tight brand cap survives -------------
def test_draft_failclosed_luxury_floor_survives_fetch_error(client, staff_headers, draft_orders):
    oid = _seed_draft(draft_orders)
    _break_fetch(draft_orders)  # product fetch throws -> product=None
    r = _add(client, staff_headers, oid, _item("FR-CARTIER-1", discount_percent=8.0, brand="Cartier"))
    assert r.status_code == 403, r.text
    assert "exceeds your limit" in r.text.lower()


# --- 2. fetch throws on a MASS line -> normal role cap still applies ----------
def test_draft_failclosed_mass_not_blocked_on_fetch_error(client, staff_headers, draft_orders):
    oid = _seed_draft(draft_orders)
    _break_fetch(draft_orders)
    r = _add(client, staff_headers, oid, _item("FR-MASS-1", discount_percent=8.0))
    assert r.status_code in (200, 201), r.text


# --- 3. fetch throws on a MASS line over the role cap -> still blocked --------
def test_draft_failclosed_mass_over_cap_still_blocked(client, staff_headers, draft_orders):
    oid = _seed_draft(draft_orders)
    _break_fetch(draft_orders)
    r = _add(client, staff_headers, oid, _item("FR-MASS-2", discount_percent=12.0))
    assert r.status_code == 403, r.text
    assert "exceeds your limit" in r.text.lower()


# --- 4. happy path (fetch works): brand cap binds normally -------------------
def test_draft_happy_path_luxury_cap_binds(client, staff_headers, draft_orders):
    oid = _seed_draft(draft_orders)
    _seed_product(draft_orders, pid="FR-CARTIER-OK", discount_category="LUXURY", brand="Cartier")
    r = _add(client, staff_headers, oid, _item("FR-CARTIER-OK", discount_percent=3.0, brand="Cartier"))
    assert r.status_code == 403, r.text


def test_draft_happy_path_within_luxury_cap_ok(client, staff_headers, draft_orders):
    oid = _seed_draft(draft_orders)
    _seed_product(draft_orders, pid="FR-CARTIER-OK2", discount_category="LUXURY", brand="Cartier")
    r = _add(client, staff_headers, oid, _item("FR-CARTIER-OK2", discount_percent=1.0, brand="Cartier"))
    assert r.status_code in (200, 201), r.text
