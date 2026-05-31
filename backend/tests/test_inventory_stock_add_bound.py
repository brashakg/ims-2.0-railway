"""
IMS 2.0 — /inventory/stock/add quantity must be bounded
========================================================
add_stock mints ONE serialized stock row per unit in a `for _ in range(quantity)`
loop (each iteration = an atomic counter call + a DB insert). An unbounded
quantity (fat-finger or malicious 1e9) would flood the DB and hang the worker.
StockAddRequest.quantity carries a generous upper bound (10k) — far above any
real single-SKU intake but enough to stop a runaway loop. Mirrors orders.py C-3.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-stock-add")


def test_huge_quantity_rejected():
    from pydantic import ValidationError

    from api.routers.inventory import StockAddRequest

    with pytest.raises(ValidationError):
        StockAddRequest(product_id="P1", quantity=1_000_000_000)


def test_just_over_cap_rejected():
    from pydantic import ValidationError

    from api.routers.inventory import StockAddRequest

    with pytest.raises(ValidationError):
        StockAddRequest(product_id="P1", quantity=10_001)


def test_zero_and_negative_rejected():
    from pydantic import ValidationError

    from api.routers.inventory import StockAddRequest

    for bad in (0, -5):
        with pytest.raises(ValidationError):
            StockAddRequest(product_id="P1", quantity=bad)


def test_normal_quantities_accepted():
    from api.routers.inventory import StockAddRequest

    for good in (1, 50, 10_000):
        req = StockAddRequest(product_id="P1", quantity=good)
        assert req.quantity == good
