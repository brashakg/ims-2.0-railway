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
