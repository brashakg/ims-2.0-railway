"""
IMS 2.0 — Per-category GST recompute regression tests
======================================================
Locks in the Phase 6.15 fix for the "every order's grand_total = 0"
bug. The bug: orders.py:698 read `it.get("subtotal")` while items_data
was built with key "item_total" — `line_taxable=0`, `tax_amount=0`,
`grand_total = 0 + 0 = 0`. Phase 6.15 was supposed to fix per-cat
GST and instead silently zeroed every order. The phantom cart-discount
test passed because it never asserted on grand_total.

These tests assert grand_total > 0 directly, plus the per-category
math, plus the recompute paths (add_order_item / remove_order_item)
that previously stamped a flat tax_rate from the order doc.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Calculator pure-function tests (no DB)
# ============================================================================


def test_helper_returns_zero_on_empty_items():
    from api.routers.orders import _compute_per_category_gst

    out = _compute_per_category_gst([], 0)
    assert out["subtotal"] == 0
    assert out["taxable"] == 0
    assert out["tax"] == 0
    assert out["total_discount"] == 0
    assert out["dominant_rate"] == 18.0  # fallback


def test_helper_single_frame_item_5pct():
    """FRAME at ₹1000 GST-INCLUSIVE → taxable 952.38 + GST 47.62 = 1000 paid."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "FRAME"}]
    out = _compute_per_category_gst(items, 0)
    assert out["subtotal"] == 1000.0
    assert out["taxable"] == 952.38
    assert out["tax"] == 47.62
    assert out["dominant_rate"] == 5.0
    # taxable + tax == the inclusive price the customer pays.
    assert round(out["taxable"] + out["tax"], 2) == 1000.0
    # Per-line stamps
    assert items[0]["gst_rate"] == 5.0
    assert items[0]["taxable_value"] == 952.38
    assert items[0]["tax_amount"] == 47.62


def test_helper_sunglass_item_18pct():
    """SUNGLASS at ₹1000 GST-INCLUSIVE → taxable 847.46 + GST 152.54 = 1000."""
    from api.routers.orders import _compute_per_category_gst

    out = _compute_per_category_gst(
        [{"item_total": 1000.0, "category": "SUNGLASSES"}],
        0,
    )
    assert out["taxable"] == 847.46
    assert out["tax"] == 152.54
    assert out["dominant_rate"] == 18.0


def test_helper_mixed_categories():
    """₹1000 FRAME (5%, incl) + ₹500 SUNGLASS (18%, incl):
    taxable = 952.38 + 423.73 = 1376.11; GST = 47.62 + 76.27 = 123.89;
    grand = 1500 (the sum of the two inclusive prices)."""
    from api.routers.orders import _compute_per_category_gst

    items = [
        {"item_total": 1000.0, "category": "FRAME"},
        {"item_total": 500.0, "category": "SUNGLASS"},
    ]
    out = _compute_per_category_gst(items, 0)
    assert out["subtotal"] == 1500.0
    assert out["taxable"] == 1376.11
    assert out["tax"] == 123.89
    assert round(out["taxable"] + out["tax"], 2) == 1500.0
    # Dominant rate = highest-revenue rate = 5% (FRAME bucket taxable is more)
    assert out["dominant_rate"] == 5.0


def test_helper_cart_discount_applied_before_tax():
    """₹1000 FRAME (5%, incl) − 10% cart discount → inclusive 900 paid:
    taxable 857.14 + GST 42.86 = 900."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "FRAME"}]
    out = _compute_per_category_gst(items, 10.0)
    assert out["subtotal"] == 1000.0
    assert out["taxable"] == 857.14
    assert out["tax"] == 42.86
    assert round(out["taxable"] + out["tax"], 2) == 900.0
    assert out["cart_discount_amount"] == 100.0


def test_helper_total_discount_sums_item_and_cart():
    """item discount ₹200 + cart 10% on remainder = item-only discount + 10% of post-item-discount."""
    from api.routers.orders import _compute_per_category_gst

    items = [
        {
            "item_total": 800.0,  # post-item-discount line subtotal
            "discount_amount": 200.0,  # the item-level discount
            "category": "FRAME",
        }
    ]
    out = _compute_per_category_gst(items, 10.0)
    # cart_discount_amount = 800 - (800 × 0.9) = 80
    # total_discount = 200 + 80 = 280
    assert out["cart_discount_amount"] == 80.0
    assert out["total_discount"] == 280.0


def test_helper_falls_back_to_18pct_for_unknown_category():
    """ACCESSORIES → not in LOW_GST list → 18%."""
    from api.routers.orders import _compute_per_category_gst

    out = _compute_per_category_gst(
        [{"item_total": 1000.0, "category": "ACCESSORIES"}],
        0,
    )
    assert out["dominant_rate"] == 18.0
    assert out["tax"] == 152.54  # 1000 incl @ 18% -> 847.46 + 152.54


def test_helper_contact_lens_5pct():
    """CONTACT_LENSES at ₹1000 incl → 5% GST extracted = 47.62 (HSN 9001, GST 2.0)."""
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "CONTACT_LENSES"}]
    out = _compute_per_category_gst(items, 0)
    assert out["tax"] == 47.62
    assert out["dominant_rate"] == 5.0
    assert items[0]["gst_rate"] == 5.0


def test_helper_colour_contacts_5pct():
    """COLOUR_CONTACTS resolves to 5% (same CONTACT_LENS hint); incl tax 47.62."""
    from api.routers.orders import _compute_per_category_gst

    out = _compute_per_category_gst(
        [{"item_total": 1000.0, "category": "COLOUR_CONTACTS"}],
        0,
    )
    assert out["tax"] == 47.62


def test_master_override_flows_into_order_tax(monkeypatch):
    """If the editable HSN->GST master sets contacts to 12%, the POS recompute
    must bill 12% — proving the master overrides the static table in billing."""
    from api.services import gst_rates

    monkeypatch.setattr(
        gst_rates,
        "_load_lookup",
        lambda: {"by_hsn": {}, "by_cat": {"CONTACT_LENS": 12.0}},
    )
    from api.routers.orders import _compute_per_category_gst

    items = [{"item_total": 1000.0, "category": "CONTACT_LENSES"}]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 12.0
    assert out["tax"] == 107.14  # 1000 incl @ 12% -> 892.86 + 107.14


def test_item_hsn_overrides_category(monkeypatch):
    """An explicit item hsn_code wins over the category hint."""
    from api.services import gst_rates

    monkeypatch.setattr(
        gst_rates,
        "_load_lookup",
        lambda: {"by_hsn": {"900410": 18.0}, "by_cat": {"CONTACT_LENS": 5.0}},
    )
    from api.routers.orders import _compute_per_category_gst

    items = [
        {
            "item_total": 1000.0,
            "category": "CONTACT_LENSES",
            "hsn_code": "900410",
        }
    ]
    out = _compute_per_category_gst(items, 0)
    assert items[0]["gst_rate"] == 18.0
    assert out["tax"] == 152.54  # 1000 incl @ 18% -> 847.46 + 152.54


# ============================================================================
# E2E regression tests via TestClient
# ============================================================================


@pytest.fixture
def patched_orders(monkeypatch):
    """Wire fake DB + repos into the orders router."""
    # Reuse the FakeDB from the walkouts tests (it already has the
    # unique-index emulator + $aggregate handler).
    from tests.test_walkouts import FakeDB

    fake_db = FakeDB()
    from api.routers import orders as orders_module
    from api.routers import payout as payout_module

    # Wire repos
    from database.repositories.order_repository import OrderRepository
    from database.repositories.customer_repository import CustomerRepository
    from database.repositories.audit_repository import AuditRepository

    order_repo = OrderRepository(fake_db.get_collection("orders"))
    customer_repo = CustomerRepository(fake_db.get_collection("customers"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))
    monkeypatch.setattr(orders_module, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(orders_module, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(orders_module, "get_product_repository", lambda: None)
    monkeypatch.setattr(orders_module, "get_walkin_counter_repository", lambda: None)
    # payout uses get_db() so wire that for the auto-derive test
    monkeypatch.setattr(payout_module, "get_db", lambda: fake_db)
    monkeypatch.setattr(payout_module, "get_user_repository", lambda: None)
    # Seed a customer
    customer_repo.create(
        {
            "customer_id": "cust-x",
            "name": "Test",
            "mobile": "9100000099",
            "phone": "9100000099",
        }
    )

    return {"db": fake_db, "order_repo": order_repo, "audit_repo": audit_repo}


def _post_order(client, auth_headers, items, **extra):
    payload = {"customer_id": "cust-x", "items": items, **extra}
    return client.post("/api/v1/orders", json=payload, headers=auth_headers)


def _frame_item(unit_price=1000.0):
    return {
        "product_id": "custom-frame",
        "product_name": "Test Frame",
        "item_type": "FRAME",
        "category": "FRAME",
        "quantity": 1,
        "unit_price": unit_price,
    }


def _sunglass_item(unit_price=2000.0):
    return {
        "product_id": "custom-sg",
        "product_name": "Test Sunglass",
        "item_type": "SUNGLASSES",
        "category": "SUNGLASSES",
        "quantity": 1,
        "unit_price": unit_price,
    }


def test_create_order_grand_total_is_nonzero(client, auth_headers, patched_orders):
    """Regression: previously every order had grand_total=0 because of
    the items_data["subtotal"] vs ["item_total"] key mismatch."""
    resp = _post_order(client, auth_headers, [_frame_item(1000.0)])
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    # Frame at ₹1000 GST-INCLUSIVE -> customer pays 1000 (GST 47.62 is within)
    assert body["grand_total"] == 1000.0


def test_create_order_mixed_categories_per_cat_gst(
    client, auth_headers, patched_orders
):
    """Mixed cart: FRAME (5%) + SUNGLASS (18%), GST-INCLUSIVE.
    The customer pays the sum of the two inclusive prices = 3000."""
    resp = _post_order(
        client,
        auth_headers,
        [
            _frame_item(1000.0),
            _sunglass_item(2000.0),
        ],
    )
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    # grand_total = 1000 + 2000 = 3000 (GST 47.62 + 305.08 = 352.70 is within)
    assert body["grand_total"] == 3000.0


def test_create_order_persists_total_discount(client, auth_headers, patched_orders):
    """The Module (iii) payout aggregator reads `total_discount` off
    the order doc — verify it gets stamped on create."""
    resp = _post_order(
        client,
        auth_headers,
        [_frame_item(1000.0)],
        cart_discount_percent=10.0,
        cart_discount_reason="Loyalty",
        cart_discount_approved_by="user-mgr",
    )
    assert resp.status_code in (200, 201), resp.text
    # Read the saved doc directly from the fake collection
    docs = patched_orders["order_repo"].collection.docs
    saved = next(d for d in docs if d.get("status") == "DRAFT")
    # cart_discount = 100, item discount = 0 → total_discount = 100
    assert saved["total_discount"] == 100.0
    assert saved["cart_discount_amount"] == 100.0


def test_add_order_item_uses_per_category_gst(client, auth_headers, patched_orders):
    """Add a SUNGLASS to a FRAME-only order; recompute must use 18%
    on the new line and 5% on the existing line, not stamp a flat rate."""
    resp = _post_order(client, auth_headers, [_frame_item(1000.0)])
    order_id = resp.json()["order_id"]

    resp = client.post(
        f"/api/v1/orders/{order_id}/items",
        json={
            "product_id": "custom-sg2",
            "item_type": "SUNGLASSES",
            "category": "SUNGLASSES",
            "quantity": 1,
            "unit_price": 2000.0,
            "discount_percent": 0,
        },
        headers=auth_headers,
    )
    # The endpoint may return 200/201 depending on FastAPI default
    assert resp.status_code in (200, 201), resp.text

    saved = next(
        d
        for d in patched_orders["order_repo"].collection.docs
        if d.get("order_id") == order_id
    )
    # subtotal = 3000; GST-INCLUSIVE: tax extracted = 47.62 + 305.08 = 352.70;
    # grand_total = the inclusive sum 3000.
    assert saved["subtotal"] == 3000.0
    assert saved["tax_amount"] == 352.70
    assert saved["grand_total"] == 3000.0


def test_remove_order_item_recomputes_per_category(
    client, auth_headers, patched_orders
):
    """Remove the SUNGLASS leaving only the FRAME → tax should drop
    back to 5% on 1000 = 50, not the stale 18% from the previous mix."""
    resp = _post_order(
        client,
        auth_headers,
        [
            _frame_item(1000.0),
            _sunglass_item(2000.0),
        ],
    )
    order_id = resp.json()["order_id"]

    saved = next(
        d
        for d in patched_orders["order_repo"].collection.docs
        if d.get("order_id") == order_id
    )
    sg_item_id = next(
        i["item_id"] for i in saved["items"] if i["category"] == "SUNGLASSES"
    )

    resp = client.delete(
        f"/api/v1/orders/{order_id}/items/{sg_item_id}",
        headers=auth_headers,
    )
    assert resp.status_code in (200, 204), resp.text

    saved = next(
        d
        for d in patched_orders["order_repo"].collection.docs
        if d.get("order_id") == order_id
    )
    assert saved["subtotal"] == 1000.0
    assert saved["tax_amount"] == 47.62  # 1000 incl @ 5% -> 952.38 + 47.62
    assert saved["grand_total"] == 1000.0


# ============================================================================
# Payout auto-derive regression
# ============================================================================


def test_payout_auto_derive_reads_grand_total_and_created_at(
    client, auth_headers, patched_orders
):
    """Regression: payout aggregation summed `$total` (orders have
    `grand_total`) and filtered on `date_str` (orders don't have it).
    Result: every preview returned this_year_sale=0 silently. Now
    the aggregator uses created_at + grand_total."""
    from datetime import datetime as _dt

    fake_db = patched_orders["db"]
    orders_coll = fake_db.get_collection("orders")
    target_year = 2026
    target_month = 6
    in_window = _dt(target_year, target_month, 15, 12, 0)
    out_window = _dt(target_year, target_month - 1, 28, 12, 0)
    orders_coll.insert_one(
        {
            "_id": "ord-in1",
            "order_id": "ord-in1",
            "store_id": "BV-TEST-01",
            "status": "CONFIRMED",
            "grand_total": 5000.0,
            "total_discount": 200.0,
            "created_at": in_window,
        }
    )
    orders_coll.insert_one(
        {
            "_id": "ord-in2",
            "order_id": "ord-in2",
            "store_id": "BV-TEST-01",
            "status": "DELIVERED",
            "grand_total": 7000.0,
            "total_discount": 100.0,
            "created_at": in_window,
        }
    )
    orders_coll.insert_one(
        {
            "_id": "ord-out1",
            "order_id": "ord-out1",
            "store_id": "BV-TEST-01",
            "status": "CONFIRMED",
            "grand_total": 99999.0,
            "total_discount": 0,
            "created_at": out_window,  # previous month — must not be summed
        }
    )
    orders_coll.insert_one(
        {
            "_id": "ord-cancel",
            "order_id": "ord-cancel",
            "store_id": "BV-TEST-01",
            "status": "CANCELLED",
            "grand_total": 50000.0,
            "total_discount": 0,
            "created_at": in_window,  # cancelled — must not be summed
        }
    )

    # Settings + minimal eligible staff
    fake_db.get_collection("incentive_settings").insert_one(
        {
            "_id": "BV-TEST-01",
            "store_id": "BV-TEST-01",
            "growth_targets": {"L1": 0.20, "L2": 0.25, "L3": 0.30},
            "base_rates": {"L1": 0.01, "L2": 0.0125, "L3": 0.015},
            "discount_kill_threshold": 0.15,
            "discount_multipliers": [
                {"max_pct": 0.10, "multiplier": 1.5},
                {"max_pct": 0.15, "multiplier": 1.0},
            ],
            "staff_weightages": {},
            "supervisor_bonuses": [],
        }
    )

    resp = client.get(
        "/api/v1/payout/preview"
        f"?year={target_year}&month={target_month}"
        "&last_year_sale=10000",  # explicit so growth math is testable
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Only the 2 in-window non-CANCELLED orders should be summed:
    # 5000 + 7000 = 12000
    assert body["inputs"]["this_year_sale"] == 12000.0
    # avg_discount_pct = (200 + 100) / 12000 = 0.025
    assert body["inputs"]["avg_discount_pct"] == 0.025
