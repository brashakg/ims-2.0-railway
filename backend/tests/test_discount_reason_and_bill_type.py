"""Owner rulings 2026-08-30 (POS Wave 4 groundwork, PR A).

1. COMPULSORY DISCOUNT REASON: a MANUAL discount (line-level or bill-level)
   always carries a written reason -- "no applicable offer means someone chose
   to give it". Offer-price/MRP pricing is not a manual discount and promo
   engine discounts ride applied_promos; neither trips the guard. Enforced in
   create_order's item loop, the cart-discount block, AND the add_order_item
   mirror (branch-drift defence).

2. BILL TYPE FOLLOWS THE MONEY: no separate order-type step at POS. Derived
   from payment_status in ONE place (order_repository.derive_bill_type):
   PAID->FINAL, PARTIAL->ADVANCE, CREDIT->CREDIT, UNPAID->PENDING. Stamped at
   create, in add_payment, and by the superadmin-edit recomputes.

Discriminating power: every test here fails if its guard/stamp is reverted
(the reason tests reuse the LIVE create_order path via the fcostfloor
harness; the bill-type ladder runs the REAL add_payment against the fake
Mongo collection).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

# Reuse the full monkeypatched create-order environment (repos, policy engine,
# GST mode, seeded customers) and its helpers -- house style, see
# test_fcostfloor.py which imports FakeDB from test_walkouts the same way.
from tests.test_fcostfloor import (  # noqa: F401,E402  (floor_env is a fixture)
    floor_env,
    _seed_product,
    _item,
    _post,
)
from tests.test_credit_billing_ar import _repo  # noqa: E402
from database.repositories.order_repository import derive_bill_type  # noqa: E402


def _created_order(env, resp):
    """Pull the persisted order doc for a successful create response."""
    data = resp.json()
    order_id = data.get("order_id") or (data.get("order") or {}).get("order_id")
    assert order_id, f"no order_id in create response: {list(data)[:8]}"
    return env["order_repo"].find_by_id(order_id)


# ---------------------------------------------------------------------------
# 1. Compulsory discount reason
# ---------------------------------------------------------------------------


def _raw_post(client, headers, items, **extra):
    """Post WITHOUT the fcostfloor helpers — those now auto-inject default
    discount reasons (per this very ruling), which would make the negative
    tests below impossible to express."""
    return client.post(
        "/api/v1/orders",
        json={"customer_id": "cust-x", "items": items, **extra},
        headers=headers,
    )


def _raw_item(pid, unit_price, **over):
    it = {"product_id": pid, "product_name": "Floor Frame", "item_type": "FRAME",
          "category": "FRAME", "quantity": 1, "unit_price": unit_price}
    it.update(over)
    return it


def test_line_discount_without_reason_is_400(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="p-r1", cost_price=50.0, mrp=200.0)
    r = _raw_post(
        client,
        auth_headers,
        [_raw_item(pid, 200.0, discount_percent=10)],
    )
    assert r.status_code == 400
    assert "is required" in r.json()["detail"].lower()


def test_line_discount_with_reason_is_accepted(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="p-r2", cost_price=50.0, mrp=200.0)
    r = _post(
        client,
        auth_headers,
        [_item(pid, 200.0, discount_percent=10, discount_reason="price match - Titan quote")],
    )
    assert r.status_code == 201, r.text


def test_cart_discount_without_reason_is_400(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="p-r3", cost_price=50.0, mrp=200.0)
    r = _raw_post(
        client,
        auth_headers,
        [_raw_item(pid, 200.0)],
        cart_discount_percent=2,
    )
    assert r.status_code == 400
    assert "is required" in r.json()["detail"].lower()


def test_cart_discount_with_reason_is_accepted(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="p-r4", cost_price=50.0, mrp=200.0)
    r = _post(
        client,
        auth_headers,
        [_item(pid, 200.0)],
        cart_discount_percent=2,
        cart_discount_reason="festival goodwill - repeat customer",
    )
    assert r.status_code == 201, r.text


def test_undiscounted_sale_needs_no_reason(client, auth_headers, floor_env):
    """Full-sticker sales are untouched by the guard (and offer_price-vs-MRP
    pricing never trips it -- there is no manual discount_percent)."""
    pid = _seed_product(floor_env, pid="p-r5", cost_price=50.0, mrp=200.0)
    r = _post(client, auth_headers, [_item(pid, 200.0)])
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# 2. Bill type follows the money
# ---------------------------------------------------------------------------


def test_created_order_is_pending(client, auth_headers, floor_env):
    pid = _seed_product(floor_env, pid="p-b0", cost_price=50.0, mrp=200.0)
    r = _post(client, auth_headers, [_item(pid, 200.0)])
    assert r.status_code == 201, r.text
    doc = _created_order(floor_env, r)
    assert doc["payment_status"] == "UNPAID"
    assert doc["bill_type"] == "PENDING"


def test_full_payment_stamps_final():
    repo, doc = _repo(5000)
    assert repo.add_payment("ord-1", {"method": "CASH", "amount": 5000})
    assert doc["payment_status"] == "PAID"
    assert doc["bill_type"] == "FINAL"


def test_part_payment_stamps_advance():
    repo, doc = _repo(5000)
    assert repo.add_payment("ord-1", {"method": "UPI", "amount": 2000})
    assert doc["payment_status"] == "PARTIAL"
    assert doc["bill_type"] == "ADVANCE"


def test_credit_tender_stamps_credit():
    repo, doc = _repo(5000)
    assert repo.add_payment("ord-1", {"method": "CREDIT", "amount": 5000})
    assert doc["payment_status"] == "CREDIT"
    assert doc["bill_type"] == "CREDIT"


def test_settling_a_credit_flips_to_final():
    repo, doc = _repo(5000)
    assert repo.add_payment("ord-1", {"method": "CREDIT", "amount": 5000})
    assert repo.add_payment("ord-1", {"method": "CASH", "amount": 5000})
    assert doc["payment_status"] == "PAID"
    assert doc["bill_type"] == "FINAL"
    assert doc["credit_sale"] is True  # sticky audit flag unchanged


@pytest.mark.parametrize(
    "status,expected",
    [
        ("PAID", "FINAL"),
        ("PARTIAL", "ADVANCE"),
        ("CREDIT", "CREDIT"),
        ("UNPAID", "PENDING"),
        ("", "PENDING"),
        (None, "PENDING"),
        ("garbage", "PENDING"),
    ],
)
def test_derive_bill_type_table(status, expected):
    assert derive_bill_type(status) == expected


def test_typed_price_below_ceiling_without_reason_is_400(
    client, auth_headers, floor_env
):
    """A unit_price typed under the catalog MRP is a discount in disguise —
    the reason guard must fire even with discount_percent 0. Fails if the
    `or _below_ceiling` clause is reverted."""
    pid = _seed_product(floor_env, pid="p-bc1", cost_price=50.0, mrp=200.0)
    r = _raw_post(client, auth_headers, [_raw_item(pid, 180.0)])
    assert r.status_code == 400
    assert "is required" in r.json()["detail"].lower()
    assert "below the current catalog price" in r.json()["detail"].lower()


def test_typed_price_below_ceiling_with_reason_is_accepted(
    client, auth_headers, floor_env
):
    pid = _seed_product(floor_env, pid="p-bc2", cost_price=50.0, mrp=200.0)
    r = _raw_post(
        client,
        auth_headers,
        [_raw_item(pid, 180.0, discount_reason="price honored from display")],
    )
    assert r.status_code == 201, r.text


def test_three_char_reason_is_400_four_is_accepted(
    client, auth_headers, floor_env
):
    """Boundary: the 4-char floor is real, not a truthiness check. Fails if
    `< 4` regresses to `not reason`."""
    pid = _seed_product(floor_env, pid="p-bc3", cost_price=50.0, mrp=200.0)
    r3 = _raw_post(
        client, auth_headers,
        [_raw_item(pid, 200.0, discount_percent=10, discount_reason="abc")],
    )
    assert r3.status_code == 400
    r4 = _raw_post(
        client, auth_headers,
        [_raw_item(pid, 200.0, discount_percent=10, discount_reason="abcd")],
    )
    assert r4.status_code == 201, r4.text
