"""
IMS 2.0 - Shipping store scope (Codex audit L2-INTEGRATION-F04)
===============================================================
Every shipping door resolves the shipment's CANONICAL order and authorises
the caller against that order's shop before it reads a shipment, books, or
calls the courier:

  - track: a shipment of another shop's order is the same 404 as a missing
    one, and the courier is never asked;
  - book: a missing order (prepaid or COD) and a failed order read fail
    CLOSED; body.store_id can never stamp the shipment into another shop;
  - list: a store-level role (AREA_MANAGER included) reads only its own shops,
    a login with no shop reads nothing.

Denials are asserted to make NO provider call and NO shipments write (the
courier service is stubbed to count calls). DISPATCH_MODE stays off.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ["DISPATCH_MODE"] = "off"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import auth as auth_mod  # noqa: E402
from api.routers import shipping as shipping_router  # noqa: E402
from api.services import shiprocket  # noqa: E402
from tests.test_shipping import _FakeColl, _FakeCustomerRepo  # noqa: E402

A, B = "AUDTA", "AUDTB"
URL = "/api/v1/shipping/shipments"


def _order(oid, store, number):
    return {
        "order_id": oid, "order_number": number, "customer_id": "CUST-1",
        "customer_name": "Asha", "customer_phone": "9876543210",
        "store_id": store, "status": "CONFIRMED", "grand_total": 1000.0,
        "payment_status": "PAID", "amount_paid": 1000.0, "balance_due": 0.0,
        "items": [{"product_name": "Frame", "sku": "F-1", "quantity": 1, "item_total": 1000}],
    }


class _Orders:
    def __init__(self, *orders):
        self.by_id = {o["order_id"]: o for o in orders}
        self.broken = False

    def find_by_id(self, oid):
        if self.broken:
            raise RuntimeError("orders read failed")
        return self.by_id.get(oid)

    def find_by_order_number(self, num):
        if self.broken:
            raise RuntimeError("orders read failed")
        return next((o for o in self.by_id.values() if o["order_number"] == num), None)


class _CountingColl(_FakeColl):
    def __init__(self):
        super().__init__()
        self.writes = 0

    def insert_one(self, doc):
        self.writes += 1
        return super().insert_one(doc)

    def update_one(self, flt, update):
        self.writes += 1
        return super().update_one(flt, update)


def _tok(roles, active=None, store_ids=None):
    return {"Authorization": "Bearer " + jwt.encode(
        {"sub": "u1", "user_id": "u1", "username": "t", "roles": roles,
         "active_store_id": active, "store_ids": store_ids or ([active] if active else []),
         "exp": datetime.utcnow() + timedelta(hours=1)},
        auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM)}


@pytest.fixture
def ctx(monkeypatch):
    app = FastAPI()
    app.include_router(shipping_router.router, prefix="/api/v1/shipping")
    orders = _Orders(_order("ORD-A", A, "INV-A"), _order("ORD-B", B, "INV-B"),
                     _order("ORD-NOSHOP", None, "INV-N"))
    coll = _CountingColl()
    # A booked shipment of B's order, and a legacy orderless one stamped B.
    coll.docs += [
        {"shipment_id": "SHP-B", "order_id": "ORD-B", "store_id": B, "awb": "AWB-B",
         "courier": "Delhivery", "tracking_url": "https://t/B", "status": "BOOKED",
         "created_at": "2026-11-04T00:00:00"},
        {"shipment_id": "SHP-GHOST", "order_id": "ORD-GONE", "store_id": B, "awb": "AWB-G",
         "status": "SIMULATED", "created_at": "2026-11-04T00:00:00"},
    ]
    calls = {"create": 0, "track": 0}

    async def _create(*_a, **_k):
        calls["create"] += 1
        return await real_create(*_a, **_k)

    async def _track(*_a, **_k):
        calls["track"] += 1
        return await real_track(*_a, **_k)

    real_create, real_track = shiprocket.create_shipment, shiprocket.track

    class _DB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            return coll if name == "shipments" else _FakeColl()

    db = _DB()
    monkeypatch.setattr(shipping_router, "get_order_repository", lambda: orders)
    monkeypatch.setattr(shipping_router, "get_customer_repository", _FakeCustomerRepo)
    monkeypatch.setattr("api.dependencies.get_db", lambda: db, raising=False)
    monkeypatch.setattr(shiprocket, "dispatch_mode", lambda: "off")
    monkeypatch.setattr(shiprocket, "create_shipment", _create)
    monkeypatch.setattr(shiprocket, "track", _track)
    return {"c": TestClient(app), "orders": orders, "coll": coll, "calls": calls}


def _untouched(ctx, docs_before=2):
    assert ctx["calls"] == {"create": 0, "track": 0}
    assert ctx["coll"].writes == 0
    assert len(ctx["coll"].docs) == docs_before


# ---------------------------------------------------------------- track ----


def test_track_other_shops_shipment_is_404_and_no_courier_call(ctx):
    r = ctx["c"].get(f"{URL}/SHP-B/track", headers=_tok(["SALES_STAFF"], A))
    assert r.status_code == 404, r.text
    assert "AWB-B" not in r.text and "ORD-B" not in r.text
    _untouched(ctx)


def test_track_area_manager_outside_own_shops_is_404(ctx):
    r = ctx["c"].get(f"{URL}/SHP-B/track", headers=_tok(["AREA_MANAGER"], A, [A]))
    assert r.status_code == 404, r.text
    _untouched(ctx)


def test_track_orderless_shipment_fails_closed(ctx):
    # Stamped B, but no order to authorise - even B's own staff get nothing.
    r = ctx["c"].get(f"{URL}/SHP-GHOST/track", headers=_tok(["SALES_STAFF"], B))
    assert r.status_code == 404, r.text
    _untouched(ctx)


def test_track_failed_order_read_fails_closed(ctx):
    ctx["orders"].broken = True
    r = ctx["c"].get(f"{URL}/SHP-B/track", headers=_tok(["SALES_STAFF"], B))
    assert r.status_code == 503, r.text
    _untouched(ctx)


@pytest.mark.parametrize("hdr", [
    _tok(["SALES_STAFF"], B),
    _tok(["ADMIN"], A),
    _tok(["AREA_MANAGER"], A, [A, B]),
])
def test_track_same_shop_and_hq_still_work(ctx, hdr):
    r = ctx["c"].get(f"{URL}/SHP-B/track", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.json()["awb"] == "AWB-B"
    assert ctx["calls"]["track"] == 1


# ----------------------------------------------------------------- book ----


@pytest.mark.parametrize("method", ["Prepaid", "COD"])
def test_book_missing_order_fails_closed(ctx, method):
    r = ctx["c"].post(URL, json={"order_id": "ORD-NOPE", "store_id": B,
                                 "address": {"payment_method": method}},
                      headers=_tok(["SALES_STAFF"], A))
    assert r.status_code == 404, r.text
    _untouched(ctx)


@pytest.mark.parametrize("method", ["Prepaid", "COD"])
def test_book_other_shops_order_denied(ctx, method):
    # Already refused before this fix (403); pinned so the rewrite keeps it.
    r = ctx["c"].post(URL, json={"order_id": "ORD-B", "address": {"payment_method": method}},
                      headers=_tok(["SALES_STAFF"], A))
    assert r.status_code in (403, 404), r.text
    _untouched(ctx)


def test_book_failed_order_read_fails_closed(ctx):
    ctx["orders"].broken = True
    r = ctx["c"].post(URL, json={"order_id": "ORD-A"}, headers=_tok(["SALES_STAFF"], A))
    assert r.status_code == 503, r.text
    _untouched(ctx)


def test_book_unattributed_order_denied_to_store_staff(ctx):
    r = ctx["c"].post(URL, json={"order_id": "ORD-NOSHOP"}, headers=_tok(["STORE_MANAGER"], A))
    assert r.status_code == 404, r.text
    _untouched(ctx)


def test_book_body_store_cannot_relocate_shipment(ctx):
    # Own order, but the caller names another shop: refused, nothing written.
    r = ctx["c"].post(URL, json={"order_id": "ORD-A", "store_id": B},
                      headers=_tok(["ADMIN"], A))
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "SHIPMENT_STORE_MISMATCH"
    _untouched(ctx)


def test_book_same_shop_stamps_orders_shop(ctx):
    r = ctx["c"].post(URL, json={"order_id": "ORD-A", "store_id": A},
                      headers=_tok(["SALES_STAFF"], A))
    assert r.status_code == 201, r.text
    assert ctx["coll"].docs[-1]["store_id"] == A
    assert ctx["calls"]["create"] == 1


def test_book_hq_other_shop_stamps_orders_shop(ctx):
    # ADMIN in A books B's order: the shipment belongs to B, not the top-bar A.
    r = ctx["c"].post(URL, json={"order_id": "ORD-B", "rebook": True}, headers=_tok(["ADMIN"], A))
    assert r.status_code == 201, r.text
    assert ctx["coll"].docs[-1]["store_id"] == B


def test_book_area_manager_own_shops_only(ctx):
    r = ctx["c"].post(URL, json={"order_id": "ORD-B", "rebook": True},
                     headers=_tok(["AREA_MANAGER"], A, [A, B]))
    assert r.status_code == 201, r.text
    r2 = ctx["c"].post(URL, json={"order_id": "ORD-A"}, headers=_tok(["AREA_MANAGER"], B, [B]))
    assert r2.status_code == 404, r2.text


# ----------------------------------------------------------------- list ----


def test_list_no_shop_login_reads_nothing(ctx):
    r = ctx["c"].get(URL, headers=_tok(["SALES_STAFF"]))
    assert r.status_code == 200, r.text
    assert r.json()["shipments"] == []


def test_list_area_manager_cannot_name_another_shop(ctx):
    r = ctx["c"].get(URL, params={"store_id": B}, headers=_tok(["AREA_MANAGER"], A, [A]))
    assert r.status_code == 403, r.text
    r2 = ctx["c"].get(URL, headers=_tok(["AREA_MANAGER"], A, [A]))
    assert r2.json()["shipments"] == []


def test_list_same_shop_and_admin_see_shipments(ctx):
    r = ctx["c"].get(URL, params={"order_id": "ORD-B", "store_id": B},
                     headers=_tok(["SALES_STAFF"], B))
    assert r.json()["total"] == 1
    r2 = ctx["c"].get(URL, headers=_tok(["ADMIN"], A))
    assert r2.json()["total"] == 2
