"""
IMS 2.0 - Order delivery + cart-discount schema tests (Phase 6.7)
===================================================================
Verifies the OrderCreate pydantic model accepts the new Phase 6.7
fields and defaults them sanely when omitted:

  - delivery_date (optional)
  - delivery_time_slot (optional)
  - delivery_priority (NORMAL | EXPRESS | URGENT, default NORMAL)
  - cart_discount_percent / cart_discount_amount (default 0)
  - cart_discount_reason / cart_discount_approved_by (optional)

Also verifies cart_discount_percent is clamped to [0, 100] by pydantic.

These are model-level tests — they don't require the DB or full app
boot, which keeps them fast.
"""

from __future__ import annotations

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Routers use os.environ at import time — set the JWT secret before that.
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

from api.routers.orders import OrderCreate, OrderItemCreate


def _base_item():
    return {
        "product_id": "P1",
        "product_name": "Test Product",
        "sku": "TEST-01",
        "brand": "Ray-Ban",
        "category": "SUNGLASSES",
        "quantity": 1,
        "unit_price": 5000.0,
        "discount_percent": 0,
        "item_type": "SUNGLASSES",
    }


def test_order_create_defaults_phase_67_fields_when_omitted():
    order = OrderCreate(
        customer_id="C1",
        items=[OrderItemCreate(**_base_item())],
    )
    assert order.delivery_date is None
    assert order.delivery_time_slot is None
    assert order.delivery_priority == "NORMAL"
    assert order.cart_discount_percent == 0.0
    assert order.cart_discount_amount == 0.0
    assert order.cart_discount_reason is None
    assert order.cart_discount_approved_by is None


def test_order_create_accepts_delivery_fields():
    order = OrderCreate(
        customer_id="C1",
        items=[OrderItemCreate(**_base_item())],
        delivery_date=date(2026, 5, 15),
        delivery_time_slot="14:00-16:00",
        delivery_priority="URGENT",
    )
    assert order.delivery_date == date(2026, 5, 15)
    assert order.delivery_time_slot == "14:00-16:00"
    assert order.delivery_priority == "URGENT"


def test_order_create_accepts_cart_discount():
    order = OrderCreate(
        customer_id="C1",
        items=[OrderItemCreate(**_base_item())],
        cart_discount_percent=7.5,
        cart_discount_amount=375.0,
        cart_discount_reason="Loyal customer",
        cart_discount_approved_by="U-manager-01",
    )
    assert order.cart_discount_percent == 7.5
    assert order.cart_discount_amount == 375.0
    assert order.cart_discount_reason == "Loyal customer"
    assert order.cart_discount_approved_by == "U-manager-01"


def test_order_create_rejects_negative_cart_discount_percent():
    with pytest.raises(Exception):  # pydantic ValidationError
        OrderCreate(
            customer_id="C1",
            items=[OrderItemCreate(**_base_item())],
            cart_discount_percent=-5.0,
        )


def test_order_create_rejects_cart_discount_over_100():
    with pytest.raises(Exception):
        OrderCreate(
            customer_id="C1",
            items=[OrderItemCreate(**_base_item())],
            cart_discount_percent=150.0,
        )


def test_order_create_rejects_negative_cart_discount_amount():
    with pytest.raises(Exception):
        OrderCreate(
            customer_id="C1",
            items=[OrderItemCreate(**_base_item())],
            cart_discount_amount=-1.0,
        )
