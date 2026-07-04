"""GET /vendors/last-cost -- last-paid price per product for a vendor, from PO
history (procurement Phase 2C: pre-fill the cost box instead of guessing).

Read-only, fail-soft, store-scoped. Calls the router function directly with a
fake PO repo (the test_grn_void / test_po_store_boundary style).
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from api.routers import vendors as v  # noqa: E402


class _PORepo:
    def __init__(self, pos):
        self._pos = pos

    def find_many(self, flt, sort=None, skip=0, limit=100):
        # Honour the vendor filter + newest-first (the docs are pre-sorted here).
        return [p for p in self._pos if p.get("vendor_id") == flt.get("vendor_id")]


def _po(po_id, po_number, created_at, items, store="S1"):
    return {
        "po_id": po_id,
        "po_number": po_number,
        "vendor_id": "V1",
        "delivery_store_id": store,
        "created_at": created_at,
        "items": items,
    }


def _user(roles=("STORE_MANAGER",), active="S1", stores=None):
    return {
        "user_id": "u1",
        "username": "t",
        "roles": list(roles),
        "active_store_id": active,
        "store_ids": stores if stores is not None else [active],
    }


def _call(**kw):
    return asyncio.run(v.get_last_purchase_cost(**kw))


def test_returns_most_recent_price_per_product(monkeypatch):
    # Newest PO first (the repo returns them in order). P1 last paid 420 on the
    # newest PO, P2 only on the older one.
    pos = [
        _po("PO2", "PO-2", "2026-06-12", [{"product_id": "P1", "unit_price": 420}]),
        _po(
            "PO1",
            "PO-1",
            "2026-05-01",
            [
                {"product_id": "P1", "unit_price": 400},
                {"product_id": "P2", "unit_price": 999},
            ],
        ),
    ]
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _PORepo(pos))
    out = _call(vendor_id="V1", product_ids="P1,P2", current_user=_user())
    assert out["costs"]["P1"]["unit_price"] == 420.0
    assert out["costs"]["P1"]["po_number"] == "PO-2"
    assert out["costs"]["P1"]["date"] == "2026-06-12"
    assert out["costs"]["P2"]["unit_price"] == 999.0  # only on the older PO


def test_cross_store_price_not_leaked(monkeypatch):
    # The only PO carrying P1 is for STORE-B; a STORE-A manager must not see it.
    pos = [
        _po(
            "PO9",
            "PO-9",
            "2026-06-12",
            [{"product_id": "P1", "unit_price": 420}],
            store="STORE-B",
        )
    ]
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _PORepo(pos))
    out = _call(vendor_id="V1", product_ids="P1", current_user=_user(active="STORE-A"))
    assert out["costs"] == {}


def test_admin_sees_any_store(monkeypatch):
    pos = [
        _po(
            "PO9",
            "PO-9",
            "2026-06-12",
            [{"product_id": "P1", "unit_price": 420}],
            store="STORE-B",
        )
    ]
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _PORepo(pos))
    out = _call(
        vendor_id="V1",
        product_ids="P1",
        current_user=_user(roles=("ADMIN",), active="STORE-A"),
    )
    assert out["costs"]["P1"]["unit_price"] == 420.0


def test_zero_and_missing_prices_skipped(monkeypatch):
    pos = [
        _po(
            "PO1",
            "PO-1",
            "2026-06-12",
            [
                {"product_id": "P1", "unit_price": 0},
                {"product_id": "P2"},  # no price
            ],
        ),
    ]
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _PORepo(pos))
    out = _call(vendor_id="V1", product_ids="P1,P2,P3", current_user=_user())
    assert out["costs"] == {}


def test_empty_inputs_and_no_repo(monkeypatch):
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: _PORepo([]))
    assert _call(vendor_id="", product_ids="P1", current_user=_user())["costs"] == {}
    assert _call(vendor_id="V1", product_ids="", current_user=_user())["costs"] == {}
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: None)
    assert _call(vendor_id="V1", product_ids="P1", current_user=_user())["costs"] == {}


def test_rbac_row_catalogued():
    from api.services.rbac_policy import POLICY

    rows = [
        r
        for r in POLICY
        if r.get("path") == "/api/v1/vendors/last-cost" and r.get("method") == "GET"
    ]
    assert len(rows) == 1
    assert set(rows[0]["allowed"]) == {
        "ACCOUNTANT",
        "ADMIN",
        "AREA_MANAGER",
        "STORE_MANAGER",
    }
