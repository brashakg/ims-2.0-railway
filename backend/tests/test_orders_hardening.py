"""
IMS 2.0 - Orders/POS hardening regression tests
=================================================
Locks in the QA stress-run fixes for the revenue-critical order path. Each
test maps to one finding:

  C-1  Seeded catalog products (catalog_products collection) can be ordered
       even when the `products` collection misses.
  C-2  An unknown/typo `category` no longer undercharges GST: it falls back to
       the `item_type` rate (when item_type maps) before the optical default.
  C-3  Non-finite / absurd-magnitude unit_price or quantity is a clean 422,
       not a 500 from Infinity overflowing JSON serialisation.
  C-4  A Rs 0 / 100%-discount order stamps an approver + writes an audit row.
  C-7  delivery_priority is constrained to {NORMAL, EXPRESS, URGENT}.
  C-8  delivery_date cannot be strictly before today.
  C-9  A CREDIT over-tender is allowed (pay-later promise); CASH/UPI/CARD
       over-tender is still blocked 400.

The pure-schema + pure-helper tests need no DB. The end-to-end create/payment
tests reuse the in-memory FakeDB harness from test_walkouts.
"""

from __future__ import annotations

import math
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Routers read os.environ at import time.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")


# ============================================================================
# C-3 - order-item bounds (pure pydantic, no DB)
# ============================================================================


def _make_item(**over):
    from api.routers.orders import OrderItemCreate

    base = {
        "product_id": "custom-frame",
        "item_type": "FRAME",
        "category": "FRAME",
        "quantity": 1,
        "unit_price": 5000.0,
    }
    base.update(over)
    return OrderItemCreate(**base)


def test_c3_normal_high_value_frame_qty2_accepted():
    """A real Rs 2,00,000 frame, qty 2, must still validate (no false reject)."""
    item = _make_item(unit_price=200000.0, quantity=2)
    assert item.unit_price == 200000.0
    assert item.quantity == 2


def test_c3_unit_price_at_upper_bound_accepted():
    item = _make_item(unit_price=10_000_000.0)
    assert item.unit_price == 10_000_000.0


def test_c3_unit_price_infinity_float_rejected():
    with pytest.raises(Exception):  # pydantic ValidationError -> 422 at the API
        _make_item(unit_price=1.7e308)


def test_c3_unit_price_literal_inf_rejected():
    with pytest.raises(Exception):
        _make_item(unit_price=float("inf"))


def test_c3_unit_price_nan_rejected():
    with pytest.raises(Exception):
        _make_item(unit_price=float("nan"))


def test_c3_quantity_one_billion_rejected():
    with pytest.raises(Exception):
        _make_item(quantity=1_000_000_000)


def test_c3_quantity_at_upper_bound_accepted():
    assert _make_item(quantity=1000).quantity == 1000


# ============================================================================
# C-7 / C-8 - OrderCreate delivery field validators (pure pydantic)
# ============================================================================


def _make_order(**over):
    from api.routers.orders import OrderCreate

    base = {"customer_id": "C1", "items": [_make_item()]}
    base.update(over)
    return OrderCreate(**base)


def test_c7_delivery_priority_valid_values_accepted():
    assert _make_order(delivery_priority="NORMAL").delivery_priority == "NORMAL"
    assert _make_order(delivery_priority="EXPRESS").delivery_priority == "EXPRESS"
    assert _make_order(delivery_priority="URGENT").delivery_priority == "URGENT"


def test_c7_delivery_priority_lowercase_normalised():
    assert _make_order(delivery_priority="express").delivery_priority == "EXPRESS"


def test_c7_delivery_priority_none_allowed():
    # None is permitted (the order doc defaults it to NORMAL).
    assert _make_order(delivery_priority=None).delivery_priority is None


def test_c7_delivery_priority_arbitrary_string_rejected():
    with pytest.raises(Exception):
        _make_order(delivery_priority="SUPER_DUPER_RUSH")


def test_c8_delivery_date_today_allowed():
    today = date.today()
    assert _make_order(delivery_date=today).delivery_date == today


def test_c8_delivery_date_future_allowed():
    future = date.today() + timedelta(days=30)
    assert _make_order(delivery_date=future).delivery_date == future


def test_c8_delivery_date_past_rejected():
    past = date.today() - timedelta(days=1)
    with pytest.raises(Exception):
        _make_order(delivery_date=past)


def test_c8_delivery_date_none_allowed():
    assert _make_order(delivery_date=None).delivery_date is None


# ============================================================================
# C-2 - unknown category falls back to item_type rate (pure helper)
# ============================================================================


def test_c2_watch_with_junk_category_bills_18pct():
    """item_type WATCH + category 'FOOBAR' (junk) -> 18%, not the 5% default."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "FOOBAR", "item_type": "WATCH"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 18.0
    assert out["dominant_rate"] == 18.0
    # 1000 inclusive @ 18% -> 847.46 taxable + 152.54 tax
    assert out["tax"] == 152.54


def test_c2_sunglass_no_category_bills_18pct():
    """item_type SUNGLASS + no category -> 18% (already worked; lock it in)."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "item_type": "SUNGLASS"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 18.0
    assert out["dominant_rate"] == 18.0


def test_c2_valid_category_unchanged_even_if_item_type_differs():
    """When `category` IS a valid table entry, it stays authoritative even if
    item_type would resolve differently. FRAME (5%) category wins over a
    SUNGLASS item_type -> 5% (no behaviour change for valid categories)."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "FRAME", "item_type": "SUNGLASS"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 5.0
    assert out["dominant_rate"] == 5.0


def test_c2_junk_category_and_junk_item_type_falls_to_default():
    """Both unknown -> the existing optical-dominant 5% default (unchanged)."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "FOO", "item_type": "BAR"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 5.0


def test_c2_explicit_hsn_still_authoritative_over_item_type(monkeypatch):
    """An explicit HSN still wins; the C-2 guard must not override it."""
    from api.services import gst_rates

    monkeypatch.setattr(
        gst_rates,
        "_load_lookup",
        lambda: {"by_hsn": {"900410": 18.0}, "by_cat": {}},
    )
    from api.routers.orders import _compute_per_category_gst

    items = [
        {
            "item_total": 1000.0,
            "category": "FOOBAR",  # junk
            "item_type": "FRAME",  # would be 5%
            "hsn_code": "900410",  # explicit HSN -> 18%
        }
    ]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 18.0


# ============================================================================
# C-1 - catalog_products mapping (pure helper)
# ============================================================================


class _CatalogColl:
    """Minimal collection stub that supports the $or lookup C-1 uses."""

    def __init__(self, docs):
        self.docs = docs

    def find_one(self, query):
        clauses = query.get("$or", [query])
        for d in self.docs:
            for c in clauses:
                if all(d.get(k) == v for k, v in c.items()):
                    return d
        return None


def test_c1_resolve_catalog_product_maps_fields(monkeypatch):
    """A product present ONLY in catalog_products resolves with price/category/
    GST mapped to the flat order-path shape."""
    from api.routers import orders as orders_module

    catalog_doc = {
        "id": "CAT-FR-001",
        "sku": "FR-RB-3025-1000",
        "title": "Ray-Ban Aviator (Frame)",
        "category": "FR",  # short code -> FRAME -> 5%
        "hsn_code": "900311",
        "gst_rate": 5.0,
        "pricing": {
            "mrp": 7000.0,
            "offer_price": 6500.0,
            "cost_price": 3000.0,
            "discount_category": "PREMIUM",
        },
        "is_active": True,
    }
    monkeypatch.setattr(
        orders_module,
        "_get_catalog_collection",
        lambda: _CatalogColl([catalog_doc]),
    )

    mapped = orders_module._resolve_catalog_product_doc("CAT-FR-001")
    assert mapped is not None
    assert mapped["product_id"] == "CAT-FR-001"
    assert mapped["name"] == "Ray-Ban Aviator (Frame)"
    assert mapped["category"] == "FR"
    assert mapped["gst_rate"] == 5.0
    assert mapped["mrp"] == 7000.0
    assert mapped["offer_price"] == 6500.0
    assert mapped["cost_price"] == 3000.0
    assert mapped["discount_category"] == "PREMIUM"
    assert mapped["_resolved_from"] == "catalog_products"


def test_c1_resolve_falls_back_to_catalog_when_products_misses(monkeypatch):
    """_resolve_product_doc: a `products` miss flows through to catalog_products."""
    from api.routers import orders as orders_module

    class _EmptyRepo:
        class _Coll:
            def find_one(self, q):
                return None

        def __init__(self):
            self.collection = self._Coll()

        def find_by_id(self, pid):
            return None

    catalog_doc = {"id": "CAT-9", "title": "Catalog Only", "category": "SG"}
    monkeypatch.setattr(
        orders_module,
        "_get_catalog_collection",
        lambda: _CatalogColl([catalog_doc]),
    )
    resolved = orders_module._resolve_product_doc(_EmptyRepo(), "CAT-9")
    assert resolved is not None
    assert resolved["product_id"] == "CAT-9"
    assert resolved["name"] == "Catalog Only"


def test_c1_resolve_missing_in_both_returns_none(monkeypatch):
    from api.routers import orders as orders_module

    class _EmptyRepo:
        class _Coll:
            def find_one(self, q):
                return None

        def __init__(self):
            self.collection = self._Coll()

        def find_by_id(self, pid):
            return None

    monkeypatch.setattr(
        orders_module, "_get_catalog_collection", lambda: _CatalogColl([])
    )
    assert orders_module._resolve_product_doc(_EmptyRepo(), "NOPE") is None


# ============================================================================
# End-to-end (TestClient + FakeDB) - C-1, C-4, C-9
# ============================================================================


@pytest.fixture
def hardening_orders(monkeypatch):
    """Wire fake DB + repos into the orders router (mirrors the GST-recompute
    fixture) plus a catalog_products collection for C-1."""
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
    monkeypatch.setattr(
        orders_module, "get_customer_repository", lambda: customer_repo
    )
    monkeypatch.setattr(orders_module, "get_product_repository", lambda: None)
    monkeypatch.setattr(orders_module, "get_walkin_counter_repository", lambda: None)
    # orders.py imports get_audit_repository lazily from ..dependencies, so the
    # patch must land on the source module, not orders_module.
    monkeypatch.setattr(deps_module, "get_audit_repository", lambda: audit_repo)

    customer_repo.create(
        {
            "customer_id": "cust-x",
            "name": "Test",
            "mobile": "9100000099",
            "phone": "9100000099",
        }
    )

    return {
        "db": fake_db,
        "order_repo": order_repo,
        "audit_repo": audit_repo,
    }


def _post_order(client, auth_headers, items, **extra):
    payload = {"customer_id": "cust-x", "items": items, **extra}
    return client.post("/api/v1/orders", json=payload, headers=auth_headers)


def _frame_item(unit_price=1000.0, **over):
    item = {
        "product_id": "custom-frame",
        "product_name": "Test Frame",
        "item_type": "FRAME",
        "category": "FRAME",
        "quantity": 1,
        "unit_price": unit_price,
    }
    item.update(over)
    return item


# ---- C-1 end-to-end -------------------------------------------------------


def test_c1_order_create_resolves_catalog_only_product(
    client, auth_headers, hardening_orders, monkeypatch
):
    """A product that exists ONLY in catalog_products (and a NON-virtual id, so
    the existence check actually runs) can be ordered. Previously this 400'd
    'Product not found'."""
    from api.routers import orders as orders_module
    from database.repositories.product_repository import ProductRepository

    fake_db = hardening_orders["db"]
    # products collection exists but is EMPTY (lookup misses) ...
    products_coll = fake_db.get_collection("products")
    product_repo = ProductRepository(products_coll)
    monkeypatch.setattr(
        orders_module, "get_product_repository", lambda: product_repo
    )
    # ... while the product DOES exist in catalog_products.
    monkeypatch.setattr(
        orders_module,
        "_get_catalog_collection",
        lambda: _CatalogColl(
            [
                {
                    "id": "CAT-FR-77",
                    "title": "Catalog Frame",
                    "category": "FR",
                    "gst_rate": 5.0,
                    "hsn_code": "900311",
                    "pricing": {"mrp": 3000.0, "offer_price": 3000.0},
                    "is_active": True,
                }
            ]
        ),
    )

    resp = _post_order(
        client,
        auth_headers,
        [
            {
                "product_id": "CAT-FR-77",
                "product_name": "Catalog Frame",
                "item_type": "FRAME",
                "category": "FRAME",
                "quantity": 1,
                "unit_price": 3000.0,
            }
        ],
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["grand_total"] == 3000.0  # 5% GST is within the inclusive price


def test_c1_order_create_unknown_product_still_404s(
    client, auth_headers, hardening_orders, monkeypatch
):
    """Guard: a non-virtual id absent from BOTH collections still 400s."""
    from api.routers import orders as orders_module
    from database.repositories.product_repository import ProductRepository

    fake_db = hardening_orders["db"]
    product_repo = ProductRepository(fake_db.get_collection("products"))
    monkeypatch.setattr(
        orders_module, "get_product_repository", lambda: product_repo
    )
    monkeypatch.setattr(
        orders_module, "_get_catalog_collection", lambda: _CatalogColl([])
    )

    resp = _post_order(
        client,
        auth_headers,
        [
            {
                "product_id": "GHOST-1",
                "item_type": "FRAME",
                "category": "FRAME",
                "quantity": 1,
                "unit_price": 1000.0,
            }
        ],
    )
    assert resp.status_code == 400, resp.text
    assert "not found" in resp.text.lower()


# ---- C-4 end-to-end -------------------------------------------------------


def test_c4_zero_total_order_audited_and_approver_stamped(
    client, auth_headers, hardening_orders
):
    """A 100% cart discount -> Rs 0 order persists an approver + audit entry,
    and is NOT blocked."""
    resp = _post_order(
        client,
        auth_headers,
        [_frame_item(1000.0)],
        cart_discount_percent=100.0,
        cart_discount_reason="Full comp - warranty replacement",
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["grand_total"] == 0.0

    saved = next(
        d
        for d in hardening_orders["order_repo"].collection.docs
        if d.get("status") == "DRAFT"
    )
    assert saved["zero_total"] is True
    # No approver supplied -> acting user (the SUPERADMIN test token) stamped.
    assert saved["cart_discount_approved_by"] == "test-admin-001"
    assert saved["zero_total_approved_by"] == "test-admin-001"

    # An immutable audit row was written for the zero-total approval.
    audit_docs = hardening_orders["audit_repo"].collection.docs
    zt = [a for a in audit_docs if a.get("action") == "ORDER_ZERO_TOTAL_APPROVED"]
    assert len(zt) == 1
    assert zt[0]["entity_id"] == saved["order_id"]
    assert zt[0]["details"]["approver_auto_stamped"] is True


def test_c4_zero_total_keeps_supplied_approver(
    client, auth_headers, hardening_orders
):
    """When the POS DOES supply an approver, it is preserved (not overwritten),
    and the audit marks it as not auto-stamped."""
    resp = _post_order(
        client,
        auth_headers,
        [_frame_item(1000.0, discount_percent=100.0)],
        cart_discount_approved_by="mgr-007",
    )
    assert resp.status_code in (200, 201), resp.text

    saved = next(
        d
        for d in hardening_orders["order_repo"].collection.docs
        if d.get("status") == "DRAFT"
    )
    assert saved["zero_total"] is True
    assert saved["cart_discount_approved_by"] == "mgr-007"
    audit_docs = hardening_orders["audit_repo"].collection.docs
    zt = [a for a in audit_docs if a.get("action") == "ORDER_ZERO_TOTAL_APPROVED"]
    assert len(zt) == 1
    assert zt[0]["details"]["approver_auto_stamped"] is False


def test_c4_normal_order_no_zero_total_flag_or_audit(
    client, auth_headers, hardening_orders
):
    """A normal priced order must NOT be flagged zero_total or audited."""
    resp = _post_order(client, auth_headers, [_frame_item(1000.0)])
    assert resp.status_code in (200, 201), resp.text
    saved = next(
        d
        for d in hardening_orders["order_repo"].collection.docs
        if d.get("status") == "DRAFT"
    )
    assert saved["zero_total"] is False
    audit_docs = hardening_orders["audit_repo"].collection.docs
    assert not [
        a for a in audit_docs if a.get("action") == "ORDER_ZERO_TOTAL_APPROVED"
    ]


# ---- C-9 end-to-end -------------------------------------------------------


def _make_paid_order(client, auth_headers):
    """Create an order and return (order_id, grand_total)."""
    resp = _post_order(client, auth_headers, [_frame_item(5000.0)])
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    return body["order_id"], body["grand_total"]


def test_c9_credit_over_tender_allowed(client, auth_headers, hardening_orders):
    """A CREDIT tender ABOVE balance_due is a pay-later promise -> allowed."""
    order_id, grand_total = _make_paid_order(client, auth_headers)
    resp = client.post(
        f"/api/v1/orders/{order_id}/payments",
        json={"method": "CREDIT", "amount": grand_total + 5000.0},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("method", ["CASH", "UPI", "CARD"])
def test_c9_cash_like_over_tender_still_blocked(
    client, auth_headers, hardening_orders, method
):
    """CASH / UPI / CARD above balance_due is still a 400 over-tender."""
    order_id, grand_total = _make_paid_order(client, auth_headers)
    resp = client.post(
        f"/api/v1/orders/{order_id}/payments",
        json={"method": method, "amount": grand_total + 0.5},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    assert "exceeds balance due" in resp.text.lower()


def test_c9_credit_exact_balance_marks_credit(
    client, auth_headers, hardening_orders
):
    """Sanity: a CREDIT tender equal to balance leaves the order as a
    receivable (CREDIT), proving the tender was recorded, not rejected."""
    order_id, grand_total = _make_paid_order(client, auth_headers)
    resp = client.post(
        f"/api/v1/orders/{order_id}/payments",
        json={"method": "CREDIT", "amount": grand_total},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payment_status"] == "CREDIT"


# C-3 at the HTTP layer: a non-finite price returns 422, never 500.
def test_c3_http_create_infinity_price_is_422_not_500(
    client, auth_headers, hardening_orders
):
    resp = _post_order(
        client,
        auth_headers,
        [_frame_item(unit_price=1.7e308)],
    )
    assert resp.status_code == 422, resp.text


def test_c3_http_create_huge_quantity_is_422(
    client, auth_headers, hardening_orders
):
    resp = _post_order(
        client,
        auth_headers,
        [_frame_item(quantity=1_000_000_000)],
    )
    assert resp.status_code == 422, resp.text


def test_c3_http_create_normal_large_order_is_201(
    client, auth_headers, hardening_orders
):
    """The generous bounds never reject a real order: Rs 2L frame, qty 2."""
    resp = _post_order(
        client,
        auth_headers,
        [_frame_item(unit_price=200000.0, quantity=2)],
    )
    assert resp.status_code in (200, 201), resp.text


# Defensive: math import used so the file's intent (finite checks) is explicit.
def test_c3_math_isfinite_contract():
    assert math.isfinite(5000.0)
    assert not math.isfinite(float("inf"))
    assert not math.isfinite(float("nan"))
