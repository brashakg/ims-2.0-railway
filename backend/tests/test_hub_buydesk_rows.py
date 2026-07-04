"""
Hub Buy Desk -- the one-screen rows endpoint. Locks the row contract: per product
the screen shows catalog readiness + honest ecom state (incl PUSH_LOCKED) + on-hand
+ on-order + a buy signal NETTED against open POs (never double-order).

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_hub_buydesk_rows.py -q
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import buy_desk as bd  # noqa: E402
from api.routers import buy_desk as bdr  # noqa: E402

# ---------------------------------------------------------------------------
# buy_signal (pure) -- netting against on_order is the whole point
# ---------------------------------------------------------------------------


def test_buy_signal_none_without_velocity():
    assert bd.buy_signal(None, 0, 0) is None
    assert bd.buy_signal(0, 0, 0) is None  # no sales -> no signal (not a 0)


def test_buy_signal_nets_on_hand_and_on_order():
    # 2 units/day * 14 lead = 28 need; minus 10 on-hand minus 15 on-order = 3
    assert bd.buy_signal(2.0, 10, 15, lead_days=14) == 3


def test_buy_signal_floors_at_zero_when_covered():
    # already over-covered by stock + open POs -> 0, never negative
    assert bd.buy_signal(1.0, 50, 50, lead_days=14) == 0


# ---------------------------------------------------------------------------
# ecom_state (pure)
# ---------------------------------------------------------------------------


def test_ecom_state_push_locked_wins():
    assert (
        bd.ecom_state({"ecom": {"shopify_product_id": "g"}}, True)
        == bd.ECOM_PUSH_LOCKED
    )


def test_ecom_state_live_and_staged_and_not_listed():
    assert (
        bd.ecom_state({"ecom": {"shopify_product_id": "gid://x"}}, False)
        == bd.ECOM_LIVE
    )
    assert bd.ecom_state({"ecom": {"status": "STAGED"}}, False) == bd.ECOM_STAGED
    assert bd.ecom_state({}, False) == bd.ECOM_NOT_LISTED


# ---------------------------------------------------------------------------
# build_row (pure)
# ---------------------------------------------------------------------------


def test_build_row_shape():
    product = {
        "product_id": "P1",
        "sku": "RB-1",
        "brand": "Ray-Ban",
        "category": "FRAME",
        "catalog_status": "ACTIVE",
        "attributes": {"name": "RB Frame"},
    }
    readiness = {"complete": True, "missing": [], "blockers": [], "purchasable": True}
    row = bd.build_row(
        product,
        readiness=readiness,
        push_locked=False,
        on_hand=4,
        on_order=2,
        velocity_per_day=1.0,
        lead_days=14,
    )
    assert row["product_id"] == "P1" and row["sku"] == "RB-1"
    assert row["readiness"]["purchasable"] is True
    assert row["ecom_state"] == bd.ECOM_NOT_LISTED
    assert row["on_hand"] == 4 and row["on_order"] == 2
    assert row["buy_signal"] == 14 - 4 - 2  # 8
    assert row["purchasable"] is True
    # Additive Phase-1 field: absent on the product -> None (never a KeyError).
    assert row["preferred_vendor_id"] is None


def test_build_row_preferred_vendor_passthrough():
    """preferred_vendor_id rides along when the product carries one (the
    draft-PO modal preselects it); blank/None normalises to None."""
    readiness = {"complete": True, "missing": [], "blockers": [], "purchasable": True}
    row = bd.build_row(
        {"product_id": "P2", "preferred_vendor_id": "V-9"},
        readiness=readiness,
        push_locked=False,
        on_hand=0,
        on_order=0,
        velocity_per_day=None,
    )
    assert row["preferred_vendor_id"] == "V-9"

    row_blank = bd.build_row(
        {"product_id": "P3", "preferred_vendor_id": ""},
        readiness=readiness,
        push_locked=False,
        on_hand=0,
        on_order=0,
        velocity_per_day=None,
    )
    assert row_blank["preferred_vendor_id"] is None


# ---------------------------------------------------------------------------
# router integration (fakes) -- shape + PUSH_LOCKED surfacing
# ---------------------------------------------------------------------------

_VIEWER = {"user_id": "u", "roles": ["CATALOG_MANAGER"]}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Repo:
    def __init__(self, products):
        self._p = products

    def find_many(self, flt, skip=0, limit=200):
        return self._p[skip : skip + limit]


class _Coll:
    def aggregate(self, _p):
        return iter([])

    def find(self, *a, **k):
        return iter([])


class _DB:
    def get_collection(self, _n):
        return _Coll()


def test_rows_endpoint_assembles_and_surfaces_push_lock(monkeypatch):
    products = [
        {
            "product_id": "P1",
            "sku": "RB-1",
            "brand": "Ray-Ban",
            "category": "FRAME",
            "mrp": 5000,
            "offer_price": 4500,
            "cost_price": 2000,
            "hsn_code": "9003",
            "gst_rate": 5,
            "attributes": {
                "brand_name": "Ray-Ban",
                "model_no": "M",
                "colour_code": "BLK",
            },
            "catalog_status": "ACTIVE",
        },
        {
            "product_id": "P2",
            "sku": "CART-1",
            "brand": "Cartier",
            "category": "FRAME",
            "attributes": {"brand_name": "Cartier"},
            "catalog_status": "DRAFT",
        },
    ]
    monkeypatch.setattr(bdr, "get_product_repository", lambda: _Repo(products))
    monkeypatch.setattr(bdr, "_get_db", lambda: _DB())
    # P2's brand Cartier is push-locked
    monkeypatch.setattr(
        bdr._sp,
        "push_lock_reason",
        lambda db, entity, doc: "locked" if (doc.get("brand") == "Cartier") else None,
    )
    out = _run(
        bdr.buy_desk_rows(store_id=None, limit=200, skip=0, current_user=_VIEWER)
    )
    assert out["total"] == 2
    by_id = {r["product_id"]: r for r in out["rows"]}
    assert by_id["P1"]["ecom_state"] == bd.ECOM_NOT_LISTED
    assert by_id["P2"]["ecom_state"] == bd.ECOM_PUSH_LOCKED
    assert (
        by_id["P1"]["on_hand"] == 0 and by_id["P1"]["buy_signal"] is None
    )  # no velocity
