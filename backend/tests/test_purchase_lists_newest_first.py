"""Review round 2, finding #18: the Purchase lists are the NEWEST page, and the
count beside them is the real count.

GET /vendors/purchase-orders and GET /vendors/grn returned the first 50
documents in the order they were written (find_many with no sort), and called
the page's length the "total". On All stores -- an admin's default since F63 --
the Purchase Orders, Analytics and Goods received screens therefore held the
chain's 50 OLDEST records: this week's orders were missing, and the GRN tile
read "Total GRNs 50 . all stores" for a chain with hundreds.

Pinned here, through the real vendors router on a real collection (Mongo when
CI has one, mongomock otherwise):
  * the page is newest first (created_at descending);
  * `total` is every document matching the same filter (shop scope, status),
    not the page's length -- so a screen can say "latest N of M";
  * the next page (skip) meets the first with no overlap and no gap;
  * the shop rule is unchanged: an admin's omitted store_id is every shop, a
    store manager's is his own shop, and the total obeys the same scope.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest
     backend/tests/test_purchase_lists_newest_first.py -q
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import vendors as vendors_pkg  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

DHN = "BV-DHN-01"
PUN = "WO-PUN-01"
ONLINE = "BV-ONLINE-01"

# 70 orders: 40 Dhanbad + 30 Pune, interleaved, written OLDEST first. The 6
# newest are still open (SENT); everything older was received.
N_PO = 70
# 60 receipts: 45 Dhanbad + 15 Pune, written oldest first.
N_GRN = 60
T0 = datetime(2026, 1, 1, 9, 0, 0)


def _po_shop(i: int) -> str:
    return PUN if i % 7 in (1, 3, 5) else DHN


def _grn_shop(i: int) -> str:
    return PUN if i % 4 == 1 else DHN


def _user(role: str, store: str | None) -> dict:
    return {
        "user_id": f"u-{role.lower()}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": [store] if store else [],
        "active_store_id": store,
    }


ADMIN = _user("ADMIN", ONLINE)
MANAGER_PUNE = _user("STORE_MANAGER", PUN)


def _seed(db) -> None:
    for i in range(N_PO):
        db["purchase_orders"].insert_one(
            {
                "_id": f"po-{i:03d}",
                "po_id": f"po-{i:03d}",
                "po_number": f"PO-{i:03d}",
                "vendor_id": "V-1",
                "delivery_store_id": _po_shop(i),
                "status": "SENT" if i >= N_PO - 6 else "RECEIVED",
                "total_amount": 1000.0 + i,
                # The repository stamps a real datetime on create.
                "created_at": T0 + timedelta(hours=i),
            }
        )
    for i in range(N_GRN):
        db["grns"].insert_one(
            {
                "_id": f"grn-{i:03d}",
                "grn_id": f"grn-{i:03d}",
                "grn_number": f"GRN-{i:03d}",
                "vendor_id": "V-1",
                "store_id": _grn_shop(i),
                "status": "ACCEPTED",
                "created_at": T0 + timedelta(hours=i),
            }
        )


def _fresh_db():
    from pymongo import MongoClient

    uri = os.getenv("MONGODB_URL") or os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
    name = f"ims_test_newest_{uuid.uuid4().hex[:8]}"
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except Exception:
        try:
            import mongomock
        except ImportError:
            pytest.skip("no Mongo and no mongomock available")
            return
        client = mongomock.MongoClient()
    db = client[name]
    _seed(db)
    try:
        yield db
    finally:
        try:
            client.drop_database(name)
        except Exception:
            pass
        client.close()


@pytest.fixture(scope="module")
def mongo_db():
    yield from _fresh_db()


class _DBProxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getitem__(self, name):
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


@pytest.fixture
def get(mongo_db, monkeypatch):
    from database.repositories.vendor_repository import GRNRepository, PurchaseOrderRepository

    proxy = _DBProxy(mongo_db)
    monkeypatch.setattr(vendors_pkg, "_get_db", lambda: proxy)
    monkeypatch.setattr(
        vendors_pkg,
        "get_purchase_order_repository",
        lambda: PurchaseOrderRepository(mongo_db["purchase_orders"]),
    )
    monkeypatch.setattr(vendors_pkg, "get_grn_repository", lambda: GRNRepository(mongo_db["grns"]))
    app = FastAPI()
    app.include_router(vendors_pkg.router, prefix="/vendors")
    client = TestClient(app)

    def _get(path: str, user: dict, **params):
        async def _as_user():
            return dict(user)

        app.dependency_overrides[get_current_user] = _as_user
        resp = client.get(path, params={k: v for k, v in params.items() if v is not None})
        assert resp.status_code == 200, resp.text
        return resp.json()

    return _get


def _newest(n: int, ids: list[str]) -> list[str]:
    """The n newest of ids written oldest first (index order = time order)."""
    return list(reversed(ids))[:n]


PO_IDS = [f"po-{i:03d}" for i in range(N_PO)]
GRN_IDS = [f"grn-{i:03d}" for i in range(N_GRN)]


# ============================================================================
# Purchase orders
# ============================================================================


def test_admin_on_all_stores_gets_the_newest_orders_and_the_real_total(get):
    body = get("/vendors/purchase-orders", ADMIN)
    got = [p["po_id"] for p in body["purchase_orders"]]
    assert got == _newest(50, PO_IDS), (
        "#18: the page is not the 50 newest orders, newest first: "
        f"first {got[:3]}, last {got[-3:]}"
    )
    # This week's open orders are on the first page.
    assert {f"po-{i:03d}" for i in range(N_PO - 6, N_PO)} <= set(got)
    assert body["total"] == N_PO, f"#18: total {body['total']} is the page, not the {N_PO} orders"


def test_the_next_page_meets_the_first_with_no_overlap_and_no_gap(get):
    first = get("/vendors/purchase-orders", ADMIN)
    rest = get("/vendors/purchase-orders", ADMIN, skip=50)
    a = [p["po_id"] for p in first["purchase_orders"]]
    b = [p["po_id"] for p in rest["purchase_orders"]]
    assert not set(a) & set(b)
    assert a + b == list(reversed(PO_IDS))
    assert first["total"] == rest["total"] == N_PO


def test_the_order_total_obeys_the_shop_and_the_status(get):
    pune = [i for i in PO_IDS if _po_shop(int(i[3:])) == PUN]
    # An admin who picks Pune.
    body = get("/vendors/purchase-orders", ADMIN, store_id=PUN)
    assert body["total"] == len(pune) == 30
    assert [p["po_id"] for p in body["purchase_orders"]] == _newest(30, pune)
    # A Pune store manager who asks for no shop gets his own, and its count.
    mine = get("/vendors/purchase-orders", MANAGER_PUNE)
    assert {p["delivery_store_id"] for p in mine["purchase_orders"]} == {PUN}
    assert mine["total"] == len(pune)
    # The status filter narrows the total too (the GRN picker reads per status).
    sent = get("/vendors/purchase-orders", ADMIN, status="SENT")
    assert sent["total"] == 6
    assert [p["po_id"] for p in sent["purchase_orders"]] == _newest(6, PO_IDS)


def test_a_smaller_page_size_still_reports_every_order(get):
    body = get("/vendors/purchase-orders", ADMIN, limit=10)
    assert [p["po_id"] for p in body["purchase_orders"]] == _newest(10, PO_IDS)
    assert body["total"] == N_PO


# ============================================================================
# Goods receipts
# ============================================================================


def test_admin_on_all_stores_gets_the_newest_receipts_and_the_real_total(get):
    body = get("/vendors/grn", ADMIN)
    got = [g["grn_id"] for g in body["grns"]]
    assert got == _newest(50, GRN_IDS), (
        "#18: the page is not the 50 newest receipts, newest first: "
        f"first {got[:3]}, last {got[-3:]}"
    )
    assert body["total"] == N_GRN, f"#18: total {body['total']} is the page, not the {N_GRN} receipts"


def test_the_receipt_total_obeys_the_shop(get):
    pune = [g for g in GRN_IDS if _grn_shop(int(g[4:])) == PUN]
    body = get("/vendors/grn", MANAGER_PUNE)
    assert {g["store_id"] for g in body["grns"]} == {PUN}
    assert [g["grn_id"] for g in body["grns"]] == _newest(len(pune), pune)
    assert body["total"] == len(pune) == 15
    dhn = get("/vendors/grn", ADMIN, store_id=DHN)
    assert dhn["total"] == N_GRN - 15
    assert len(dhn["grns"]) == 45


def test_the_next_receipt_page_meets_the_first(get):
    a = [g["grn_id"] for g in get("/vendors/grn", ADMIN)["grns"]]
    b = [g["grn_id"] for g in get("/vendors/grn", ADMIN, skip=50)["grns"]]
    assert a + b == list(reversed(GRN_IDS))
