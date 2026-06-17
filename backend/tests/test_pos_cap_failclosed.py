"""POS discount-cap FAIL-CLOSED on resolver error (council go-live blocker).

Ruling: docs/reference/SETTINGS_PERMISSIONS_COUNCIL_RULING.md section 1.

THE BUG (orders.py create_order, per-line discount cap): the category /
luxury-brand cap lookup was wrapped in `except Exception: pass # fall back to
user cap only`. So when the product/category resolver threw, the effective cap
silently stayed at the LOOSE user/role cap with NO luxury-brand floor -> a
luxury frame (Cartier 2%, luxury 5%) could take the full 10-25% role cap. That
is a live compliance under-enforcement on the billing path.

THE FIX: on a resolver exception, do NOT loosen the cap back to the user cap.
Tighten using the strongest signal still on the line-item payload (its brand,
via the pure DB-free brand_cap_for lookup) so the luxury floor cannot vanish.
A plain MASS line (no luxury brand on the payload) keeps the normal user/role
cap so an ordinary legitimate sale is NOT blocked.

Test matrix (the three cases the ruling requires):
  1. resolver throws on a LUXURY/Cartier line -> capped tight (2%), NOT 10%.
  2. resolver throws on a MASS line -> the normal 10% staff cap STILL applies.
  3. happy path unchanged: min(user_cap, cat/brand_cap) still binds.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


@pytest.fixture
def priced_orders(monkeypatch):
    """Minimal order-create harness with a FakeDB + a real product repo, mirrors
    test_order_pricing_integrity.priced_orders."""
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
        {"customer_id": "cust-x", "name": "Test", "mobile": "9100000099",
         "phone": "9100000099"}
    )
    return {"db": fake_db, "monkeypatch": monkeypatch}


def _seed_product(priced, *, pid, mrp=10000.0, cost_price=4000.0,
                  discount_category="MASS", brand=None):
    from api.routers import orders as orders_module
    from database.repositories.product_repository import ProductRepository

    coll = priced["db"].get_collection("products")
    repo = ProductRepository(coll)
    doc = {
        "product_id": pid, "name": "Priced Frame", "category": "FRAME",
        "mrp": mrp, "cost_price": cost_price,
        "discount_category": discount_category, "is_active": True,
    }
    if brand:
        doc["brand"] = brand
    repo.create(doc)
    priced["monkeypatch"].setattr(
        orders_module, "get_product_repository", lambda: repo
    )
    return pid


def _break_resolver(priced):
    """Force the per-line discount cap-tightening lookup to THROW -- the exact
    real-world failure mode the bug describes.

    The cap block resolves the product, then computes its category/luxury-brand
    cap via `pricing_caps.effective_discount_cap` (imported inside the block as
    `product_discount_cap`). Patching that function to raise drives the
    `except Exception:` branch we hardened WITHOUT disturbing the unrelated
    existence check / price prefetch (which also call _resolve_product_doc and
    must keep working, else the order 500s before the cap is ever evaluated)."""
    from api.services import pricing_caps as caps_module

    def _boom(*_a, **_k):
        raise RuntimeError("simulated product/category cap-resolver failure")

    priced["monkeypatch"].setattr(
        caps_module, "effective_discount_cap", _boom
    )


def _item(pid, *, discount_percent, brand=None, unit_price=10000.0):
    it = {
        "product_id": pid, "product_name": "Priced Frame", "item_type": "FRAME",
        "category": "FRAME", "quantity": 1, "unit_price": unit_price,
        "discount_percent": discount_percent,
    }
    if brand:
        it["brand"] = brand
    return it


def _post(client, headers, items, **extra):
    return client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-x", "items": items, **extra},
        headers=headers,
    )


# --- 1. resolver throws on a LUXURY line -> tight cap, NOT the loose user cap -
def test_failclosed_luxury_brand_floor_survives_resolver_error(
    client, staff_headers, priced_orders
):
    """SALES_STAFF cap 10%, Cartier brand cap 2%. With the resolver throwing,
    an 8% discount (inside the loose 10% staff cap, ABOVE the 2% Cartier floor)
    must STILL be blocked -- the luxury floor cannot vanish on a resolver error.
    Before the fix this returned 200 (the loose 10% cap leaked through)."""
    pid = _seed_product(
        priced_orders, pid="FR-CARTIER-1", discount_category="LUXURY",
        brand="Cartier",
    )
    _break_resolver(priced_orders)
    r = _post(
        client, staff_headers,
        [_item(pid, discount_percent=8.0, brand="Cartier")],
    )
    assert r.status_code == 403, r.text
    assert "exceeds your limit" in r.text.lower()


# --- 2. resolver throws on a MASS line -> the normal user cap STILL applies ---
def test_failclosed_mass_item_not_blocked_on_resolver_error(
    client, staff_headers, priced_orders
):
    """A plain MASS line with no luxury brand: when the resolver throws we must
    NOT start 403'ing legitimate ordinary sales. An 8% discount is inside the
    10% staff cap, so it must go through (don't trade under-enforcement for a
    can't-sell bug)."""
    pid = _seed_product(
        priced_orders, pid="FR-MASS-1", discount_category="MASS", brand=None
    )
    _break_resolver(priced_orders)
    r = _post(
        client, staff_headers,
        [_item(pid, discount_percent=8.0)],
    )
    assert r.status_code in (200, 201), r.text


def test_failclosed_mass_item_over_user_cap_still_blocked(
    client, staff_headers, priced_orders
):
    """Belt-and-braces: the user/role cap is not loosened either. A 12%
    discount on a MASS line still exceeds the 10% staff cap even when the
    resolver throws."""
    pid = _seed_product(
        priced_orders, pid="FR-MASS-2", discount_category="MASS", brand=None
    )
    _break_resolver(priced_orders)
    r = _post(
        client, staff_headers,
        [_item(pid, discount_percent=12.0)],
    )
    assert r.status_code == 403, r.text
    assert "exceeds your limit" in r.text.lower()


# --- 3. happy path unchanged: min(user_cap, cat/brand_cap) still binds --------
def test_happy_path_luxury_cap_binds_when_resolver_works(
    client, staff_headers, priced_orders
):
    """Resolver WORKING: a 3% discount on a Cartier line (2% brand cap) is
    blocked by the normal min(user_cap, cat/brand_cap) path -- proves the fix
    did not break the ordinary resolution."""
    pid = _seed_product(
        priced_orders, pid="FR-CARTIER-OK", discount_category="LUXURY",
        brand="Cartier",
    )
    r = _post(
        client, staff_headers,
        [_item(pid, discount_percent=3.0, brand="Cartier")],
    )
    assert r.status_code == 403, r.text
    assert "exceeds your limit" in r.text.lower()


def test_happy_path_within_luxury_cap_ok_when_resolver_works(
    client, staff_headers, priced_orders
):
    """Resolver WORKING: a 1% discount on a Cartier line is within the 2% brand
    cap -> allowed. Confirms the cap binds at the right number, not just always
    rejecting."""
    pid = _seed_product(
        priced_orders, pid="FR-CARTIER-OK2", discount_category="LUXURY",
        brand="Cartier",
    )
    r = _post(
        client, staff_headers,
        [_item(pid, discount_percent=1.0, brand="Cartier")],
    )
    assert r.status_code in (200, 201), r.text
