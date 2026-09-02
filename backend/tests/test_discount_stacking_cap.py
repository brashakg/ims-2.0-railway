# ============================================================================
# A cap two controls can each satisfy while their PRODUCT breaks it is not a cap
# ============================================================================
# The per-line discount cap and the bill (cart) discount cap were checked
# INDEPENDENTLY and never against their combination. Both could sit exactly at
# the ceiling and the customer still walked out with roughly twice it:
#
#   role cap 10%:  10% on the line + 10% on the bill -> 19.0% off, accepted 201
#   Cartier   2%:   2% on the line +  2% on the bill ->  3.96% off, accepted 201
#
# The bill discount lands on an ALREADY discounted line, so the two terms
# multiply rather than add. Found by an adversarial money review, reproduced
# against this very route, and live on the classic POS - which has exposed both
# controls all along.
#
# Reuses the harness from test_pos_cap_failclosed so the caps under test are the
# real canonical ones, not a fixture's idea of them.

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_pos_cap_failclosed import (  # noqa: E402
    _item,
    _post,
    _seed_product,
    priced_orders,  # noqa: F401  (pytest fixture, imported for its side effect)
)


def _grand_total(resp):
    return (resp.json() or {}).get("grand_total")


# --- the two proven leaks -------------------------------------------------


def test_role_cap_cannot_be_doubled_by_stacking(client, staff_headers, priced_orders):
    """10% line + 10% bill = 19%, under a 10% role cap. Must be refused."""
    pid = _seed_product(priced_orders, pid="FR-MASS-STACK", discount_category="MASS")
    resp = _post(
        client, staff_headers, [_item(pid, discount_percent=10)],
        cart_discount_percent=10,
        cart_discount_reason="test: stacking probe",
    )
    assert resp.status_code == 403, (
        f"stacked to 19% under a 10% cap and was ACCEPTED: "
        f"{resp.status_code} grand_total={_grand_total(resp)}"
    )
    assert "together" in resp.text.lower() or "19" in resp.text


def test_luxury_brand_cap_cannot_be_doubled_by_stacking(
    client, staff_headers, priced_orders
):
    """Cartier caps at 2%. 2% + 2% = 3.96%, nearly double. Must be refused."""
    pid = _seed_product(
        priced_orders, pid="FR-CARTIER-STACK",
        discount_category="LUXURY", brand="Cartier",
    )
    resp = _post(
        client, staff_headers,
        [_item(pid, discount_percent=2, brand="Cartier")],
        cart_discount_percent=2,
        cart_discount_reason="test: stacking probe",
    )
    assert resp.status_code == 403, (
        f"stacked to 3.96% under a 2% Cartier cap and was ACCEPTED: "
        f"{resp.status_code} grand_total={_grand_total(resp)}"
    )


# --- and the legitimate sales it must NOT start refusing ------------------


def test_a_line_discount_alone_at_the_cap_still_sells(
    client, staff_headers, priced_orders
):
    pid = _seed_product(priced_orders, pid="FR-MASS-LINE-ONLY", discount_category="MASS")
    resp = _post(client, staff_headers, [_item(pid, discount_percent=10)])
    assert resp.status_code in (200, 201), resp.text


def test_a_bill_discount_alone_at_the_cap_still_sells(
    client, staff_headers, priced_orders
):
    pid = _seed_product(priced_orders, pid="FR-MASS-CART-ONLY", discount_category="MASS")
    resp = _post(
        client, staff_headers, [_item(pid, discount_percent=0)],
        cart_discount_percent=10,
        cart_discount_reason="test: cart only",
    )
    assert resp.status_code in (200, 201), resp.text


def test_a_combination_that_stays_under_the_cap_still_sells(
    client, staff_headers, priced_orders
):
    """5% + 5% = 9.75%, still inside a 10% cap. The rule caps the PRODUCT, not
    the presence of two discounts - stacking is legal while it stays under."""
    pid = _seed_product(priced_orders, pid="FR-MASS-OK", discount_category="MASS")
    resp = _post(
        client, staff_headers, [_item(pid, discount_percent=5)],
        cart_discount_percent=5,
        cart_discount_reason="test: under cap",
    )
    assert resp.status_code in (200, 201), resp.text


# --- the maths itself, so the ceiling cannot be quietly re-derived as a sum -


def test_the_terms_multiply_they_do_not_add():
    from api.routers.orders import combined_discount_pct, effective_line_discount_pct

    assert combined_discount_pct(10, 10) == pytest.approx(19.0)
    assert combined_discount_pct(2, 2) == pytest.approx(3.96)
    assert combined_discount_pct(0, 10) == pytest.approx(10.0)
    assert combined_discount_pct(10, 0) == pytest.approx(10.0)
    # A line marked down by price rather than percent is still discounted.
    assert effective_line_discount_pct(0, 9000.0, 10000.0) == pytest.approx(10.0)
    # The larger of the two wins; an explicit percent is not a way to hide it.
    assert effective_line_discount_pct(15, 9000.0, 10000.0) == pytest.approx(15.0)


# --- the two holes the first fix left open --------------------------------
#
# The stacking cap landed on ONE door and skipped the biggest line. Both of
# these returned 201 with the fix already in the tree.


def test_a_lens_line_is_not_exempt_from_stacking(client, staff_headers, priced_orders):
    """A lens line resolves to no product doc -- and was skipped ENTIRELY.

    The cart-discount loop `continue`d on any `lens-` / `custom-` id, so the
    stacking cap was enforced on the frame and not on the lens. In an optical
    ticket the lens is usually the larger half of the money, which made the
    exemption the cheaper way to buy the same 19%.
    """
    _seed_product(priced_orders, pid="FR-LENSPAIR", discount_category="MASS")
    resp = _post(
        client, staff_headers,
        [{
            "product_id": "lens-cl-monthly-1", "product_name": "Acuvue Monthly",
            # A CONTACT lens: exempt from the hard Rx-required gate (owner
            # ruling), so the probe reaches the discount check instead of
            # stopping at 422. Same virtual-id branch, same money.
            "item_type": "CONTACT_LENS", "category": "CONTACT_LENS", "quantity": 1,
            "unit_price": 8000.0, "discount_percent": 10,
            "discount_reason": "test: stacking probe on a lens",
        }],
        cart_discount_percent=10,
        cart_discount_reason="test: stacking probe",
    )
    assert resp.status_code == 403, (
        f"a LENS line stacked to 19% under a 10% cap and was ACCEPTED: "
        f"{resp.status_code} grand_total={_grand_total(resp)}"
    )


def test_stacking_cannot_be_bypassed_by_adding_the_item_afterwards(
    client, staff_headers, priced_orders
):
    """Set the bill discount, save the DRAFT, THEN add the discounted line.

    POST /orders/{id}/items checked the line against its own cap and never
    against the bill discount already sitting on the order, so the exact
    combination create_order refuses was reachable in two calls instead of one.
    """
    pid = _seed_product(priced_orders, pid="FR-ADDLATER", discount_category="MASS")
    from api.routers import orders as orders_module

    orders_module.get_order_repository().create({
        "order_id": "ORD-STACK-DRAFT", "store_id": "BV-TEST-01",
        "customer_id": "cust-x", "status": "DRAFT", "items": [],
        "cart_discount_percent": 10.0,
        "cart_discount_reason": "test: bill discount set first",
    })

    resp = client.post(
        "/api/v1/orders/ORD-STACK-DRAFT/items",
        json=_item(pid, discount_percent=10),
        headers=staff_headers,
    )
    assert resp.status_code == 403, (
        f"added a 10% line to a DRAFT already carrying a 10% bill discount "
        f"(=19% under a 10% cap) and it was ACCEPTED: {resp.status_code} "
        f"{resp.text[:200]}"
    )


def test_the_add_door_still_accepts_a_line_that_stays_under_the_cap(
    client, staff_headers, priced_orders
):
    """The negative control: 5% line + 4% bill = 8.8%, inside 10%. Must pass.

    Without this, the test above would also pass with the add door blocking
    every discounted line outright.
    """
    pid = _seed_product(priced_orders, pid="FR-ADDOK", discount_category="MASS")
    from api.routers import orders as orders_module

    orders_module.get_order_repository().create({
        "order_id": "ORD-STACK-OK", "store_id": "BV-TEST-01",
        "customer_id": "cust-x", "status": "DRAFT", "items": [],
        "cart_discount_percent": 4.0,
        "cart_discount_reason": "test: modest bill discount",
    })

    resp = client.post(
        "/api/v1/orders/ORD-STACK-OK/items",
        json=_item(pid, discount_percent=5),
        headers=staff_headers,
    )
    assert resp.status_code == 200, resp.text
