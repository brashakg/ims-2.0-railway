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
from tests.test_shipping import _FakeColl, _FakeCustomerRepo, _OrdersDistinct  # noqa: E402

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
        self.collection = _OrdersDistinct(lambda: list(self.by_id.values()), lambda: self.broken)

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


# ------------------------------------------- list: canonical-order scope ----
# Codex re-review of #1377: the list filtered on the shipment's SAVED
# store_id, so a legacy row stamped A for B's order showed B's AWB, address
# and phone to A, and an orderless row showed to whichever shop it was
# stamped. A shipment now lists for a store-level caller only when its
# canonical order resolves into the caller's shop; orderless / unresolvable
# rows are HQ-only. Real Mongo semantics (mongomock), totals counted after scope.


@pytest.fixture
def mctx(monkeypatch):
    import mongomock

    from database.repositories.order_repository import OrderRepository

    mdb = mongomock.MongoClient().db
    mdb.orders.insert_many([_order("ORD-A", A, "INV-A"), _order("ORD-B", B, "INV-B"),
                            _order("ORD-B2", B, "INV-B2")])
    mdb.shipments.insert_many([
        # Legacy mis-stamp: B's order, saved under A (pre-fix body-store fallback).
        {"shipment_id": "SHP-MIS", "order_id": "ORD-B", "store_id": A, "awb": "AWB-MIS",
         "phone": "9000000201", "created_at": "2026-11-04T03:00:00"},
        {"shipment_id": "SHP-B2", "order_id": "ORD-B2", "store_id": B, "awb": "AWB-B2",
         "created_at": "2026-11-04T02:00:00"},
        {"shipment_id": "SHP-A", "order_id": "ORD-A", "store_id": A, "awb": "AWB-A",
         "created_at": "2026-11-04T01:00:00"},
        # Orderless / gone-order rows stamped B.
        {"shipment_id": "SHP-NOORD", "store_id": B, "awb": "AWB-N",
         "created_at": "2026-11-04T00:30:00"},
        {"shipment_id": "SHP-GONE", "order_id": "ORD-GONE", "store_id": B, "awb": "AWB-G",
         "created_at": "2026-11-04T00:00:00"},
    ])

    class _DB:
        is_connected = True
        db = mdb

    app = FastAPI()
    app.include_router(shipping_router.router, prefix="/api/v1/shipping")
    orders = OrderRepository(mdb.orders)
    monkeypatch.setattr(shipping_router, "get_order_repository", lambda: orders)
    monkeypatch.setattr("api.dependencies.get_db", lambda: _DB(), raising=False)
    return {"c": TestClient(app), "orders": orders}


def _ids(r):
    assert r.status_code == 200, r.text
    return [s["shipment_id"] for s in r.json()["shipments"]], r.json()["total"]


def test_list_mis_stamped_row_hidden_from_saved_shop(mctx):
    for params in ({"order_id": "ORD-B"}, {}, {"store_id": ""}, {"store_id": A}):
        r = mctx["c"].get(URL, params=params, headers=_tok(["SALES_STAFF"], A))
        ids, total = _ids(r)
        assert "SHP-MIS" not in ids and "AWB-MIS" not in r.text and "9000000201" not in r.text
        assert total == len(ids) == (0 if "order_id" in params else 1)


def test_list_mis_stamped_row_shown_to_canonical_shop(mctx):
    ids, total = _ids(mctx["c"].get(URL, headers=_tok(["STORE_MANAGER"], B)))
    assert ids == ["SHP-MIS", "SHP-B2"] and total == 2
    ids, total = _ids(mctx["c"].get(URL, params={"order_id": "ORD-B"},
                                    headers=_tok(["STORE_MANAGER"], B)))
    assert ids == ["SHP-MIS"] and total == 1


def test_list_orderless_rows_hq_only(mctx):
    for hdr in (_tok(["SALES_STAFF"], B), _tok(["AREA_MANAGER"], A, [A, B])):
        r = mctx["c"].get(URL, params={"store_id": B}, headers=hdr)
        ids, _ = _ids(r)
        assert "SHP-NOORD" not in ids and "SHP-GONE" not in ids
    ids, total = _ids(mctx["c"].get(URL, headers=_tok(["ADMIN"], A)))
    assert total == 5 and {"SHP-NOORD", "SHP-GONE"} <= set(ids)


def test_list_area_manager_and_hq_shop_filter_use_canonical_shop(mctx):
    for hdr in (_tok(["AREA_MANAGER"], A, [A, B]), _tok(["ADMIN"], A)):
        ids, total = _ids(mctx["c"].get(URL, params={"store_id": B}, headers=hdr))
        assert ids == ["SHP-MIS", "SHP-B2"] and total == 2
    ids, total = _ids(mctx["c"].get(URL, headers=_tok(["AREA_MANAGER"], A, [A, B])))
    assert ids == ["SHP-A"] and total == 1


def test_list_pages_and_total_count_only_visible_rows(mctx):
    hdr = _tok(["SALES_STAFF"], B)
    p1, t1 = _ids(mctx["c"].get(URL, params={"limit": 1}, headers=hdr))
    p2, t2 = _ids(mctx["c"].get(URL, params={"limit": 1, "skip": 1}, headers=hdr))
    p3, t3 = _ids(mctx["c"].get(URL, params={"limit": 1, "skip": 2}, headers=hdr))
    assert (p1, p2, p3) == (["SHP-MIS"], ["SHP-B2"], [])
    assert t1 == t2 == t3 == 2


def test_list_failed_order_read_fails_closed(mctx, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("orders read failed")

    monkeypatch.setattr(mctx["orders"].collection, "distinct", _boom)
    r = mctx["c"].get(URL, headers=_tok(["SALES_STAFF"], B))
    assert r.status_code == 503, r.text
    assert "AWB" not in r.text


# --------------------------------------------------------------------------
# PR #1377 Codex round 2: equal creation times must not repeat or drop rows
# across pages. MongoDB returns documents that tie on every sort key in no
# defined order, and a fresh query (the next page) may order them differently.
# mongomock's stable sort hides that, so this stand-in reorders tied rows on
# every query unless the sort carries a unique tie-breaker (_id).
# --------------------------------------------------------------------------

class _TieUnstableShipments:
    def __init__(self, coll):
        self._coll = coll
        self.calls = 0

    def count_documents(self, query):
        return self._coll.count_documents(query)

    def find(self, query, projection=None):
        outer = self

        class _Cursor:
            def __init__(self):
                self._spec, self._skip, self._limit = [], 0, 0

            def sort(self, key, direction=None):
                self._spec = [(key, direction)] if isinstance(key, str) else list(key)
                return self

            def skip(self, n):
                self._skip = n
                return self

            def limit(self, n):
                self._limit = n
                return self

            def __iter__(self):
                outer.calls += 1
                docs = list(outer._coll.find(query))
                keys = [k for k, _ in self._spec]
                if "_id" not in keys:
                    # No unique key: ties come back in a per-query order.
                    docs.reverse() if outer.calls % 2 else None
                for k, d in reversed(self._spec):
                    docs.sort(key=lambda x, k=k: x.get(k) or "", reverse=d == -1)
                page = docs[self._skip:self._skip + self._limit if self._limit else None]
                hide_id = projection and projection.get("_id") == 0
                return iter([{k: v for k, v in d.items() if not (hide_id and k == "_id")}
                             for d in page])

        return _Cursor()


def test_list_pages_each_row_once_when_creation_times_tie(mctx, monkeypatch):
    import mongomock

    mdb = mongomock.MongoClient().db
    mdb.orders.insert_many([_order(f"ORD-T{i}", B, f"INV-T{i}") for i in range(4)])
    same = "2026-11-05T20:11:53.138024+00:00"
    mdb.shipments.insert_many([
        {"shipment_id": f"SHP-T{i}", "order_id": f"ORD-T{i}", "store_id": B,
         "awb": f"AWB-T{i}", "created_at": same} for i in range(4)])
    unstable = _TieUnstableShipments(mdb.shipments)
    from database.repositories.order_repository import OrderRepository

    orders = OrderRepository(mdb.orders)
    monkeypatch.setattr(shipping_router, "get_order_repository", lambda: orders)
    monkeypatch.setattr(shipping_router, "_shipments_coll", lambda: unstable)

    hdr = _tok(["SALES_STAFF"], B)
    seen = []
    for skip in range(5):
        ids, total = _ids(mctx["c"].get(URL, params={"limit": 1, "skip": skip, "store_id": B},
                                        headers=hdr))
        assert total == 4
        seen += ids
    assert sorted(seen) == ["SHP-T0", "SHP-T1", "SHP-T2", "SHP-T3"], seen
    # Same pages again: the same order, page for page.
    again = [(_ids(mctx["c"].get(URL, params={"limit": 1, "skip": s, "store_id": B},
                                  headers=hdr))[0]) for s in range(4)]
    assert sum(again, []) == seen[:4]
