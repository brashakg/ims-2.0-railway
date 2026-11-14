"""Audit batch b13: list pages that really page, and a reachable print-template
catalogue.

R30  GET /orders?store_id=S&skip=N returned the SAME newest-500 rows for every
     skip (find_by_store hard-capped at 500, no skip) and called that 500 the
     total. Now count, sort and skip/limit run on ONE query.
R31  GET /customers?search=X ignored skip/limit (first 100 matches for every
     page, total = page length) and applied channel/tier AFTER that cap.
L3-29 GET /print-overrides/_meta/templates was swallowed by
     /{entity_id}/{template_key} and answered 400.

Repositories run over mongomock (real query semantics: $and/$or, sort, skip,
count), driven through the real endpoint functions.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.routers.customers as customers_mod  # noqa: E402
import api.routers.orders.lists as lists_mod  # noqa: E402
from database.repositories.customer_repository import CustomerRepository  # noqa: E402
from database.repositories.order_repository import OrderRepository  # noqa: E402

ADMIN = {"user_id": "u-admin", "roles": ["ADMIN"], "active_store_id": "S1", "store_ids": ["S1"]}
MANAGER = {"user_id": "u-sm", "roles": ["STORE_MANAGER"], "active_store_id": "S1", "store_ids": ["S1"]}


# ---------------------------------------------------------------------------
# R30 orders
# ---------------------------------------------------------------------------

def _orders_repo(monkeypatch, docs):
    coll = mongomock.MongoClient().db.orders
    if docs:
        coll.insert_many(docs)
    repo = OrderRepository(coll)
    monkeypatch.setattr(lists_mod, "get_order_repository", lambda: repo)
    return repo


def _list(user, **kw):
    args = dict(store_id=None, status=None, customer_id=None, from_date=None,
                to_date=None, skip=0, limit=50, current_user=user)
    args.update(kw)
    return asyncio.run(lists_mod.list_orders(**args))


def _store_orders(n, store="S1", customer=None, start=None):
    start = start or datetime.utcnow() - timedelta(hours=1)
    rows = []
    for i in range(n):
        rows.append({
            "order_id": f"{store}-O{i:04d}",
            "store_id": store,
            "customer_id": customer or f"C{i % 7}",
            "status": "CONFIRMED",
            # pairs share a timestamp: the _id tie-breaker must keep pages disjoint
            "created_at": start - timedelta(minutes=i // 2),
            "items": [],
        })
    return rows


def _ids(res):
    return [o.get("orderId") or o.get("order_id") or o.get("id") for o in res["orders"]]


def test_store_orders_pages_are_distinct_and_total_is_true(monkeypatch):
    _orders_repo(monkeypatch, _store_orders(620) + _store_orders(30, store="S2"))
    seen = []
    for skip in range(0, 650, 50):
        res = _list(ADMIN, store_id="S1", skip=skip)
        assert res["pagination"]["total"] == 620
        assert res["pagination"]["total_pages"] == 13
        seen.extend(_ids(res))
    assert len(seen) == 620 and len(set(seen)) == 620  # every order reachable, once
    assert all(i.startswith("S1-") for i in seen)
    # deep page is the oldest rows, not a repeat of page 1
    assert set(_ids(_list(ADMIN, store_id="S1", skip=600))[-2:]) == {"S1-O0618", "S1-O0619"}
    # out of range: explicit empty page, true total
    beyond = _list(ADMIN, store_id="S1", skip=4000)
    assert beyond["orders"] == [] and beyond["pagination"]["total"] == 620


def test_customer_orders_page_and_stay_inside_the_callers_shops(monkeypatch):
    docs = _store_orders(130, customer="CX") + _store_orders(20, store="S2", customer="CX")
    _orders_repo(monkeypatch, docs)
    # a store manager naming one customer sees that customer's S1 orders only
    first = _list(MANAGER, customer_id="CX", limit=100)
    second = _list(MANAGER, customer_id="CX", skip=100, limit=100)
    assert first["pagination"]["total"] == 130
    ids = _ids(first) + _ids(second)
    assert len(ids) == len(set(ids)) == 130
    assert all(i.startswith("S1-") for i in ids)
    # head office sees both shops
    assert _list(ADMIN, customer_id="CX", limit=10)["pagination"]["total"] == 150


def test_no_shop_branch_counts_and_keeps_the_30_day_horizon(monkeypatch):
    old = _store_orders(5, start=datetime.utcnow() - timedelta(days=31))
    for d in old:
        d["order_id"] = "OLD-" + d["order_id"]
    _orders_repo(monkeypatch, _store_orders(60) + old)
    # ADMIN with no shop chosen: all rows, true total, distinct pages
    res = _list(ADMIN, limit=40)
    res2 = _list(ADMIN, skip=40, limit=40)
    assert res["pagination"]["total"] == 65
    assert len(set(_ids(res)) | set(_ids(res2))) == 65
    # the store manager browsing their shop never sees the 31-day-old rows,
    # and they are not counted either
    mine = _list(MANAGER, store_id="S1", limit=500)
    assert mine["pagination"]["total"] == 60
    assert not any(i.startswith("OLD-") for i in _ids(mine))


# ---------------------------------------------------------------------------
# R31 customers
# ---------------------------------------------------------------------------

def _customers_repo(monkeypatch, docs):
    coll = mongomock.MongoClient().db.customers
    coll.insert_many(docs)
    repo = CustomerRepository(coll)
    monkeypatch.setattr(customers_mod, "get_customer_repository", lambda: repo)
    return repo


def _customers(user, **kw):
    args = dict(search=None, customer_type=None, exclude_marketing=False, channel=None,
                store_id=None, skip=0, limit=50, current_user=user)
    args.update(kw)
    return asyncio.run(customers_mod.list_customers(**args))


def _people(n, prefix="AUDIT", store="S1", **extra):
    return [
        {"customer_id": f"{prefix}-{store}-{i:04d}", "name": f"{prefix} Person {i}",
         "mobile": f"9{i:09d}", "home_store_id": store, **extra}
        for i in range(n)
    ]


def _cids(res):
    return [c["customer_id"] for c in res["customers"]]


def test_customer_search_pages_are_distinct_and_total_is_true(monkeypatch):
    _customers_repo(
        monkeypatch,
        _people(230) + _people(40, store="S2") + _people(10, prefix="OTHER"),
    )
    seen = []
    for skip in (0, 50, 100, 150, 200):
        res = _customers(MANAGER, search="AUDIT", skip=skip)
        assert res["pagination"]["total"] == 230
        seen.extend(_cids(res))
    assert len(seen) == len(set(seen)) == 230
    assert all("-S1-" in c for c in seen)  # the manager's own shop only
    assert _customers(MANAGER, search="AUDIT", skip=4000)["customers"] == []
    # caller's limit is honoured (it used to be ignored: always up to 100)
    assert len(_cids(_customers(MANAGER, search="AUDIT", limit=8))) == 8


def test_customer_search_filters_before_counting_and_paging(monkeypatch):
    docs = (
        _people(120, prefix="AUDIT", channel="ONLINE")
        + _people(120, prefix="AUDIT", store="S1", customer_type="B2B")
        + [dict(d, customer_id=d["customer_id"] + "-M", contact_tier="MARKETING")
           for d in _people(15, prefix="AUDIT")]
    )
    # unique ids across the three sets
    for i, d in enumerate(docs):
        d["customer_id"] = f"{d['customer_id']}-{i}"
    _customers_repo(monkeypatch, docs)
    online = _customers(ADMIN, search="AUDIT", channel="ONLINE", limit=500)
    # channel used to be applied AFTER a 100-row cap: 100 at most, total wrong
    assert online["pagination"]["total"] == 120 and len(online["customers"]) == 120
    b2b = _customers(ADMIN, search="AUDIT", customer_type="B2B", limit=50)
    assert b2b["pagination"]["total"] == 120  # customer_type was ignored on search
    full = _customers(ADMIN, search="AUDIT", exclude_marketing=True, limit=10)
    assert full["pagination"]["total"] == 240


# ---------------------------------------------------------------------------
# L3-29 print-template catalogue
# ---------------------------------------------------------------------------

def test_print_template_catalogue_is_reachable():
    import api.routers.print_overrides as po
    from api.routers.auth import get_current_user

    app = FastAPI()
    app.include_router(po.router, prefix="/api/v1/print-overrides")
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    c = TestClient(app)
    r = c.get("/api/v1/print-overrides/_meta/templates")
    assert r.status_code == 200, r.text
    keys = [t["key"] for t in r.json()["templates"]]
    assert "tax_invoice" in keys and r.json()["fields"]
    # the generic route still validates its template key
    assert c.get("/api/v1/print-overrides/ENT1/not_a_template").status_code == 400
