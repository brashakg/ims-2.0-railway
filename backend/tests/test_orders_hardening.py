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
# C-2 - item_type is AUTHORITATIVE for GST ("item_type wins") (pure helper)
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
    """item_type SUNGLASS + no category -> 18% (lock it in)."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "item_type": "SUNGLASS"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 18.0
    assert out["dominant_rate"] == 18.0


def test_c2_item_type_wins_over_valid_category():
    """DELTA 1: item_type is AUTHORITATIVE -- a SUNGLASS (18%) item_type beats a
    VALID but conflicting FRAMES (5%) category. The catalog category is a
    merchandising bucket; the item_type is the line's true tax nature, so the
    bill must be 18%, not 5%."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "FRAMES", "item_type": "SUNGLASS"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 18.0
    assert out["dominant_rate"] == 18.0


def test_c2_frame_item_type_no_category_bills_5pct():
    """FRAME item_type + no category -> 5% (the frame rate via item_type)."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "item_type": "FRAME"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 5.0
    assert out["dominant_rate"] == 5.0


def test_c2_known_category_only_still_correct():
    """When there is NO item_type, a known `category` still resolves correctly.
    WATCH category alone -> 18%."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "WATCH"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 18.0
    assert out["dominant_rate"] == 18.0


def test_c2_unknown_item_type_falls_back_to_valid_category():
    """When item_type is junk but `category` is valid, the category wins (the
    item_type-authoritative rule only applies when item_type is a KNOWN GST
    type). SUNGLASS category (18%) + junk item_type -> 18%."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "SUNGLASS", "item_type": "BAR"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 18.0


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


def test_c2_wellformed_single_rate_cart_grand_total_unchanged():
    """A well-formed order (item_type and category AGREE) is unaffected by the
    item_type-wins rule: FRAME+FRAME bills 5% with the same money math."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "FRAME", "item_type": "FRAME"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 5.0
    assert out["tax"] == 47.62  # 1000 incl @ 5% -> 952.38 + 47.62
    assert round(out["taxable"] + out["tax"], 2) == 1000.0  # grand_total intact


def test_c2_wellformed_mixed_rate_cart_grand_total_unchanged():
    """A well-formed MIXED cart (each line's item_type agrees with its category)
    bills exactly as before: FRAME (5%) + SUNGLASS (18%); grand_total intact."""
    from api.routers.orders import _compute_per_category_gst

    items = [
        {"item_total": 1000.0, "category": "FRAME", "item_type": "FRAME"},
        {"item_total": 500.0, "category": "SUNGLASS", "item_type": "SUNGLASS"},
    ]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 5.0
    assert items[1]["gst_rate"] == 18.0
    # Identical to test_helper_mixed_categories in test_orders_gst_recompute.
    assert out["tax"] == 123.89
    assert round(out["taxable"] + out["tax"], 2) == 1500.0


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
    """PRODUCTS-CONVERGENCE ③: a product that exists ONLY in catalog_products
    (no `products` spine row) is NOT billable -- order-create FAILS LOUD with a
    400 instead of silently billing off the catalog (the path the discount-cap
    could not fully govern). Post step-10 the catalog door writes the spine, so
    this only happens for a legacy un-converged catalog-only product. (This test
    previously asserted such a product was orderable; the convergence reverses
    that contract -- billing requires the spine.)"""
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
    # ③: catalog-only (no spine) -> loud 400, NOT a silent sale.
    assert resp.status_code == 400, resp.text
    assert "no billing master" in resp.text.lower() or "only in the catalog" in resp.text.lower()


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


def test_c4_zero_total_without_approver_rejected_400(
    client, auth_headers, hardening_orders
):
    """DELTA 2: a 100% cart discount -> Rs 0 order with NO approver/reason is
    REJECTED with 400 (no longer silently auto-stamped). Nothing persists."""
    resp = _post_order(
        client,
        auth_headers,
        [_frame_item(1000.0)],
        cart_discount_percent=100.0,
    )
    assert resp.status_code == 400, resp.text
    assert "approver" in resp.text.lower() and "reason" in resp.text.lower()

    # No order row was created.
    assert not [
        d
        for d in hardening_orders["order_repo"].collection.docs
        if d.get("status") == "DRAFT"
    ]


def test_c4_zero_total_reason_without_approver_rejected_400(
    client, auth_headers, hardening_orders
):
    """A reason alone is not enough -- an approver is also required."""
    resp = _post_order(
        client,
        auth_headers,
        [_frame_item(1000.0)],
        cart_discount_percent=100.0,
        cart_discount_reason="Full comp - warranty replacement",
    )
    assert resp.status_code == 400, resp.text


def test_c4_zero_total_with_approver_and_reason_allowed_and_audited(
    client, auth_headers, hardening_orders
):
    """DELTA 2: a Rs 0 order WITH both an approver and a reason is ALLOWED
    (201) and writes the immutable ORDER_ZERO_TOTAL_APPROVED audit row."""
    resp = _post_order(
        client,
        auth_headers,
        [_frame_item(1000.0)],
        cart_discount_percent=100.0,
        cart_discount_approved_by="mgr-007",
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
    # The supplied approver + reason are preserved (never overwritten).
    assert saved["cart_discount_approved_by"] == "mgr-007"
    assert saved["zero_total_approved_by"] == "mgr-007"
    assert saved["cart_discount_reason"] == "Full comp - warranty replacement"

    # An immutable audit row was written for the zero-total approval.
    audit_docs = hardening_orders["audit_repo"].collection.docs
    zt = [a for a in audit_docs if a.get("action") == "ORDER_ZERO_TOTAL_APPROVED"]
    assert len(zt) == 1
    assert zt[0]["entity_id"] == saved["order_id"]
    assert zt[0]["details"]["approved_by"] == "mgr-007"
    assert zt[0]["details"]["reason"] == "Full comp - warranty replacement"


def test_c4_full_line_discount_needs_line_approver_and_reason(
    client, auth_headers, hardening_orders
):
    """A 100% LINE discount may be approved via the line's own approver +
    reason ('whichever applies'); the order is then allowed + audited."""
    resp = _post_order(
        client,
        auth_headers,
        [
            _frame_item(
                1000.0,
                discount_percent=100.0,
                discount_approved_by="mgr-009",
                discount_reason="Staff replacement frame",
            )
        ],
    )
    assert resp.status_code in (200, 201), resp.text
    saved = next(
        d
        for d in hardening_orders["order_repo"].collection.docs
        if d.get("status") == "DRAFT"
    )
    assert saved["zero_total"] is True
    assert saved["zero_total_approved_by"] == "mgr-009"
    audit_docs = hardening_orders["audit_repo"].collection.docs
    zt = [a for a in audit_docs if a.get("action") == "ORDER_ZERO_TOTAL_APPROVED"]
    assert len(zt) == 1


def test_c4_full_line_discount_without_approval_rejected_400(
    client, auth_headers, hardening_orders
):
    """A 100% LINE discount with no approver/reason anywhere -> 400."""
    resp = _post_order(
        client,
        auth_headers,
        [_frame_item(1000.0, discount_percent=100.0)],
    )
    assert resp.status_code == 400, resp.text


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


# ============================================================================
# C-5 (DELTA 3) - order-create idempotency via the Idempotency-Key header
# ============================================================================


def _post_order_idem(client, auth_headers, items, key=None, **extra):
    """POST /orders with an optional Idempotency-Key header."""
    headers = dict(auth_headers)
    if key is not None:
        headers["Idempotency-Key"] = key
    payload = {"customer_id": "cust-x", "items": items, **extra}
    return client.post("/api/v1/orders", json=payload, headers=headers)


def _draft_orders(hardening_orders):
    return [
        d
        for d in hardening_orders["order_repo"].collection.docs
        if d.get("status") == "DRAFT"
    ]


def test_c5_same_key_returns_existing_single_order(
    client, auth_headers, hardening_orders
):
    """Two POSTs with the SAME Idempotency-Key create ONE order; the 2nd call
    returns the 1st order's envelope (same order_id / order_number)."""
    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-1")
    assert r1.status_code in (200, 201), r1.text
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-1")
    assert r2.status_code in (200, 201), r2.text

    b1, b2 = r1.json(), r2.json()
    assert b1["order_id"] == b2["order_id"]
    assert b1["order_number"] == b2["order_number"]
    assert b1["grand_total"] == b2["grand_total"]
    # Exactly ONE order persisted despite two POSTs.
    assert len(_draft_orders(hardening_orders)) == 1
    # The key is persisted on the order doc.
    assert _draft_orders(hardening_orders)[0]["idempotency_key"] == "K-1"


def test_c5_different_keys_create_two_orders(
    client, auth_headers, hardening_orders
):
    """Distinct Idempotency-Keys create distinct orders."""
    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-A")
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="K-B")
    assert r1.status_code in (200, 201), r1.text
    assert r2.status_code in (200, 201), r2.text
    assert r1.json()["order_id"] != r2.json()["order_id"]
    assert len(_draft_orders(hardening_orders)) == 2


def test_c5_no_key_creates_each_time(client, auth_headers, hardening_orders):
    """No Idempotency-Key header -> behaviour unchanged: each POST is a new
    order (two POSTs -> two orders)."""
    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)])
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)])
    assert r1.status_code in (200, 201), r1.text
    assert r2.status_code in (200, 201), r2.text
    assert r1.json()["order_id"] != r2.json()["order_id"]
    assert len(_draft_orders(hardening_orders)) == 2
    # No key persisted on a header-less create.
    assert all(d.get("idempotency_key") is None for d in _draft_orders(hardening_orders))


def test_c5_empty_key_treated_as_absent(client, auth_headers, hardening_orders):
    """An empty Idempotency-Key string is treated as no key (two new orders)."""
    r1 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="")
    r2 = _post_order_idem(client, auth_headers, [_frame_item(1000.0)], key="")
    assert r1.status_code in (200, 201), r1.text
    assert r2.status_code in (200, 201), r2.text
    assert r1.json()["order_id"] != r2.json()["order_id"]
    assert len(_draft_orders(hardening_orders)) == 2


# ============================================================================
# C-6 (DELTA 4) - invoice CGST/SGST/IGST split (pure helper)
# ============================================================================

# Two stored lines: a 5% frame + an 18% sunglass, inclusive-mode taxable/tax
# (the shape _compute_per_category_gst stamps onto each line at create).
_SPLIT_ITEMS = [
    {"gst_rate": 5.0, "taxable_value": 952.38, "tax_amount": 47.62, "hsn_code": "900311"},
    {"gst_rate": 18.0, "taxable_value": 423.73, "tax_amount": 76.27, "hsn_code": "900410"},
]
_STORE_JH = {"state_code": "20", "gstin": "20ABCDE1234F1Z5"}  # Jharkhand


def test_c6_intrastate_when_customer_state_absent_defaults_cgst_sgst():
    """No customer state -> safe default INTRA: CGST+SGST (each rate/2), no
    IGST, and the assumption is flagged."""
    from api.routers.orders import _build_invoice_gst_split

    out = _build_invoice_gst_split(_SPLIT_ITEMS, _STORE_JH, None)
    assert out["interstate"] is False
    assert out["place_of_supply_assumed"] is True
    assert out["totals"]["igst"] == 0.0
    # Each row's cgst + sgst == that row's tax; cgst == round(rate-tax / 2).
    for row in out["rows"]:
        assert round(row["cgst"] + row["sgst"], 2) == row["tax"]
        assert row["igst"] == 0.0
        assert row["cgst"] == round(row["tax"] / 2.0, 2)
    # GSTINs surfaced.
    assert out["store_gstin"] == "20ABCDE1234F1Z5"


def test_c6_intrastate_explicit_same_state_cgst_sgst():
    """Customer explicitly in the SAME state (via billing_address) -> INTRA,
    NOT assumed."""
    from api.routers.orders import _build_invoice_gst_split

    customer = {"billing_address": {"state": "Jharkhand"}}
    out = _build_invoice_gst_split(_SPLIT_ITEMS, _STORE_JH, customer)
    assert out["interstate"] is False
    assert out["place_of_supply_assumed"] is False
    assert out["place_of_supply"] == "20"
    assert out["totals"]["igst"] == 0.0
    assert out["totals"]["cgst"] > 0 and out["totals"]["sgst"] > 0


def test_c6_interstate_customer_other_state_uses_igst():
    """Customer in a DIFFERENT state (GSTIN-derived) -> INTER: full tax as IGST,
    no CGST/SGST."""
    from api.routers.orders import _build_invoice_gst_split

    customer = {"gstin": "27ABCDE1234F1Z5"}  # Maharashtra
    out = _build_invoice_gst_split(_SPLIT_ITEMS, _STORE_JH, customer)
    assert out["interstate"] is True
    assert out["place_of_supply"] == "27"
    assert out["customer_gstin"] == "27ABCDE1234F1Z5"
    assert out["totals"]["cgst"] == 0.0
    assert out["totals"]["sgst"] == 0.0
    for row in out["rows"]:
        assert row["igst"] == row["tax"]
        assert row["cgst"] == 0.0 and row["sgst"] == 0.0


def test_c6_cgst_sgst_sums_to_total_tax_intrastate():
    """INTRA: CGST + SGST across all rates == the order's total tax."""
    from api.routers.orders import _build_invoice_gst_split

    out = _build_invoice_gst_split(_SPLIT_ITEMS, _STORE_JH, None)
    t = out["totals"]
    assert round(t["cgst"] + t["sgst"], 2) == t["tax"]
    # And the split tax equals the sum of the stored per-line tax (123.89).
    assert t["tax"] == 123.89


def test_c6_igst_sums_to_total_tax_interstate():
    """INTER: IGST across all rates == the order's total tax."""
    from api.routers.orders import _build_invoice_gst_split

    customer = {"gstin": "27ABCDE1234F1Z5"}
    out = _build_invoice_gst_split(_SPLIT_ITEMS, _STORE_JH, customer)
    assert out["totals"]["igst"] == out["totals"]["tax"] == 123.89


def test_c6_totals_reconcile_to_grand_total():
    """taxable + total tax == the order grand_total (1000 + 500 inclusive)."""
    from api.routers.orders import _build_invoice_gst_split

    out = _build_invoice_gst_split(_SPLIT_ITEMS, _STORE_JH, None)
    t = out["totals"]
    assert round(t["taxable"] + t["tax"], 2) == 1500.0


def test_c6_invoice_endpoint_includes_gst_split(
    client, auth_headers, hardening_orders, monkeypatch
):
    """End-to-end: GET /orders/{id}/invoice carries the CGST/SGST split, place
    of supply, and both GSTINs -- while PRESERVING the existing fields."""
    from api import dependencies as deps_module

    # A store with a GSTIN + state so the invoice can be generated + split.
    class _StoreRepo:
        def find_by_id(self, sid):
            return {
                "store_id": sid,
                "gstin": "20ABCDE1234F1Z5",
                "state_code": "20",
            }

    monkeypatch.setattr(deps_module, "get_store_repository", lambda: _StoreRepo())

    # Create a normal mixed-rate order, then flip it out of DRAFT so the
    # invoice endpoint will serve it.
    resp = _post_order(
        client,
        auth_headers,
        [
            _frame_item(1000.0),  # FRAME 5%
            {
                "product_id": "custom-sg",
                "product_name": "Sunnies",
                "item_type": "SUNGLASS",
                "category": "SUNGLASS",
                "quantity": 1,
                "unit_price": 500.0,
            },
        ],
    )
    assert resp.status_code in (200, 201), resp.text
    order_id = resp.json()["order_id"]
    grand_total = resp.json()["grand_total"]

    saved = next(
        d
        for d in hardening_orders["order_repo"].collection.docs
        if d.get("order_id") == order_id
    )
    saved["status"] = "CONFIRMED"  # invoice refuses DRAFT

    inv = client.get(f"/api/v1/orders/{order_id}/invoice", headers=auth_headers)
    assert inv.status_code == 200, inv.text
    body = inv.json()
    # Preserved fields.
    assert body["orderId"] == order_id
    assert body["grandTotal"] == grand_total
    assert "items" in body and body["invoiceNumber"]
    # New C-6 fields.
    assert body["storeGstin"] == "20ABCDE1234F1Z5"
    assert body["interstate"] is False  # walk-in customer -> intra default
    assert body["placeOfSupplyAssumed"] is True
    totals = body["taxTotals"]
    assert totals["igst"] == 0.0
    assert round(totals["cgst"] + totals["sgst"], 2) == totals["tax"]
    # The split reconciles to grand_total.
    assert round(totals["taxable"] + totals["tax"], 2) == grand_total
    # Per-rate rows present (5% + 18%).
    rates = sorted(r["rate"] for r in body["taxSummary"])
    assert rates == [5.0, 18.0]


# ============================================================================
# D-1 / D-2 -- POS discount caps via canonical pricing_caps (not the old wrong
# local table). The local CATEGORY_DISCOUNT_CAPS under-capped PREMIUM(5 vs 20)/
# MASS(10 vs 15)/LUXURY(2 vs 5) and applied NO luxury brand cap, blocking legit
# discounts (SYSTEM_INTENT s3). create_order (D-1) + add-to-draft (D-2) now both
# call pricing_caps.effective_discount_cap(discount_category, brand).
# ============================================================================


def _role_headers(role, cap):
    from api.routers.auth import create_access_token

    tok = create_access_token(
        {
            "user_id": "cap-" + role.lower(),
            "username": "captest",
            "roles": [role],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
            "discount_cap": cap,
        }
    )
    return {"Authorization": "Bearer " + tok}


def _seed_capped_product(hardening_orders, monkeypatch, pid, discount_category=None, brand=None, price=2000.0):
    from api.routers import orders as orders_module
    from database.repositories.product_repository import ProductRepository

    repo = ProductRepository(hardening_orders["db"].get_collection("products"))
    repo.create(
        {
            "product_id": pid,
            "name": "Cap Test",
            "item_type": "FRAME",
            "category": "FRAME",
            "discount_category": discount_category,
            "brand": brand,
            "offer_price": price,
            "mrp": price,
            "gst_rate": 5.0,
            "is_active": True,
        }
    )
    monkeypatch.setattr(orders_module, "get_product_repository", lambda: repo)
    return pid


def _capped_item(pid, price, pct):
    return {
        "product_id": pid,
        "product_name": "Cap Test",
        "item_type": "FRAME",
        "category": "FRAME",
        "quantity": 1,
        "unit_price": price,
        "discount_percent": pct,
    }


def test_d1_premium_18pct_now_allowed(client, hardening_orders, monkeypatch):
    # PREMIUM canonical cap = 20%, StoreMgr role cap = 20%. 18% was WRONGLY
    # blocked at the old table's 5%; now allowed.
    pid = _seed_capped_product(hardening_orders, monkeypatch, "PROD-PREM-1", discount_category="PREMIUM")
    r = _post_order(client, _role_headers("STORE_MANAGER", 20.0), [_capped_item(pid, 2000.0, 18.0)])
    assert r.status_code in (200, 201), r.text


def test_d1_premium_over_cap_blocked(client, hardening_orders, monkeypatch):
    pid = _seed_capped_product(hardening_orders, monkeypatch, "PROD-PREM-2", discount_category="PREMIUM")
    r = _post_order(client, _role_headers("STORE_MANAGER", 20.0), [_capped_item(pid, 2000.0, 22.0)])
    assert r.status_code == 403 and "exceeds" in r.text.lower(), r.text


def test_d1_luxury_cap_5pct(client, hardening_orders, monkeypatch):
    pid = _seed_capped_product(hardening_orders, monkeypatch, "PROD-LUX-1", discount_category="LUXURY", price=5000.0)
    ok = _post_order(client, _role_headers("STORE_MANAGER", 20.0), [_capped_item(pid, 5000.0, 4.0)])
    assert ok.status_code in (200, 201), ok.text
    bad = _post_order(client, _role_headers("STORE_MANAGER", 20.0), [_capped_item(pid, 5000.0, 6.0)])
    assert bad.status_code == 403, bad.text


def test_d1_luxury_brand_cap_dominates(client, hardening_orders, monkeypatch):
    # Cartier brand cap = 2%, even on a MASS discount_category + 10% role cap.
    pid = _seed_capped_product(hardening_orders, monkeypatch, "PROD-CART-1", discount_category="MASS", brand="Cartier", price=3000.0)
    bad = _post_order(client, _role_headers("SALES_STAFF", 10.0), [_capped_item(pid, 3000.0, 3.0)])
    assert bad.status_code == 403, bad.text
    ok = _post_order(client, _role_headers("SALES_STAFF", 10.0), [_capped_item(pid, 3000.0, 1.0)])
    assert ok.status_code in (200, 201), ok.text


def test_d1_missing_discount_category_defaults_mass_15(client, hardening_orders, monkeypatch):
    # No discount_category (only item-type category=FRAME). Old code keyed the
    # table on "FRAME" -> 10% default -> 14% blocked. Canonical defaults MASS 15%
    # -> StoreMgr min(20,15)=15% -> 14% allowed.
    pid = _seed_capped_product(hardening_orders, monkeypatch, "PROD-NOCAT-1", discount_category=None)
    r = _post_order(client, _role_headers("STORE_MANAGER", 20.0), [_capped_item(pid, 2000.0, 14.0)])
    assert r.status_code in (200, 201), r.text


def test_d2_add_to_draft_applies_brand_cap(client, hardening_orders, monkeypatch):
    # D-2: add-item-to-draft now composes category/brand caps (was role-cap only).
    pid = _seed_capped_product(hardening_orders, monkeypatch, "PROD-CART-2", discount_category="MASS", brand="Cartier", price=3000.0)
    hdr = _role_headers("SALES_STAFF", 10.0)
    created = _post_order(
        client, hdr,
        [{"product_id": "custom-x", "item_type": "FRAME", "category": "FRAME", "quantity": 1, "unit_price": 100.0}],
    )
    assert created.status_code in (200, 201), created.text
    body = created.json()
    oid = body.get("order_id") or body.get("id")
    assert oid, body
    add = client.post("/api/v1/orders/" + str(oid) + "/items", json=_capped_item(pid, 3000.0, 5.0), headers=hdr)
    # 5% > Cartier 2% brand cap -> blocked (previously allowed under the 10% role cap)
    assert add.status_code == 403 and "exceeds" in add.text.lower(), add.text


# ============================================================================
# W1.4 / OS-005 - ONLINE-store POS guard (owner-approved 2026-07-23)
# ============================================================================
# An ONLINE store (BV-ONLINE-01 / WO-ONLINE-01, store_type == "ONLINE") owns no
# stock, has no till and no walk-ins. create_order must 400 for it BEFORE any
# validation/persist; physical stores are untouched; the Shopify ingest path
# never calls this route (locked separately in test_online_order_mapper).


def _online_store_headers(store_id="BV-ONLINE-01"):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "test-admin-online",
            "username": "testadminonline",
            "roles": ["SUPERADMIN"],
            "store_ids": [store_id],
            "active_store_id": store_id,
        }
    )
    return {"Authorization": f"Bearer {token}"}


def test_os005_create_order_blocked_for_online_store(client, hardening_orders):
    """POS create under BV-ONLINE-01 -> 400 with the plain-English detail and
    NOTHING persisted. Uses the known-id fast path (no store doc needed)."""
    resp = _post_order(client, _online_store_headers(), [_frame_item()])
    assert resp.status_code == 400, resp.text
    low = resp.text.lower()
    assert "online store" in low and "shopify" in low
    assert hardening_orders["order_repo"].collection.docs == []


def test_os005_create_order_blocked_via_store_type_doc(
    client, hardening_orders, monkeypatch
):
    """A store whose DOC says store_type=ONLINE (id NOT in the known list) is
    blocked too -- the guard prefers the store_type field over the id list."""
    from api.routers import orders as orders_module

    class _Coll:
        def __init__(self, doc=None):
            self.doc = doc

        def find_one(self, *_a, **_k):
            return self.doc

    class _Db:
        def get_collection(self, name):
            if name == "stores":
                return _Coll({"store_id": "ZZ-ONLINE-99", "store_type": "ONLINE"})
            return _Coll(None)

    monkeypatch.setattr(orders_module, "_get_db", lambda: _Db())
    resp = _post_order(
        client, _online_store_headers("ZZ-ONLINE-99"), [_frame_item()]
    )
    assert resp.status_code == 400, resp.text
    assert "online store" in resp.text.lower()
    assert hardening_orders["order_repo"].collection.docs == []


def test_os005_physical_store_unaffected(client, auth_headers, hardening_orders):
    """The SAME payload under the physical BV-TEST-01 still creates (201)."""
    resp = _post_order(client, auth_headers, [_frame_item()])
    assert resp.status_code in (200, 201), resp.text
