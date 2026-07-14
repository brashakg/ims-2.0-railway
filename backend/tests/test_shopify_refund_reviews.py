"""
IMS 2.0 - Shopify refund-review QUEUE consumer (HTTP layer)
===========================================================
Exercises the accountant-facing consumer for `shopify_refund_review`
(routers/online_store_refund_reviews.py) via a FastAPI TestClient with a real JWT
+ monkeypatched fake DB / repos: GET list, POST confirm (posts the credit note +
restock from the STORED row and stamps {status:POSTED, resolved:true}), POST
reject. This is the surface that closes finding #1 (the queue was a write-only
dead letter with no consumer).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import online_store_refund_reviews as reviews_router  # noqa: E402
from api.routers import returns as returns_router  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402
from api import dependencies as deps  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------


def _match(doc, filter_):
    if not filter_:
        return True
    for k, expected in filter_.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            if "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            else:
                return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *a, **k):
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter([dict(d) for d in self._docs])


class _Coll:
    def __init__(self):
        self.docs = []

    def create_index(self, *a, **k):
        return None

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": 1})()

    def find_one(self, filter_=None, projection=None):
        for d in self.docs:
            if _match(d, filter_):
                return dict(d)
        return None

    def find(self, filter_=None, projection=None):
        return _Cursor([d for d in self.docs if _match(d, filter_)])

    def count_documents(self, filter_=None):
        return len([d for d in self.docs if _match(d, filter_)])

    def update_one(self, filter_, update, upsert=False):
        for d in self.docs:
            if _match(d, filter_):
                for kk, vv in (update.get("$set") or {}).items():
                    d[kk] = vv
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()

    def delete_many(self, filter_=None):
        self.docs = [d for d in self.docs if not _match(d, filter_)]
        return type("R", (), {"deleted_count": 0})()


class _DB:
    is_connected = True

    def __init__(self):
        self._c = {}
        self.db = self

    def get_collection(self, name):
        return self._c.setdefault(name, _Coll())


class _FakeCustomerRepo:
    def __init__(self, db):
        self._db = db

    def find_by_id(self, cid):
        return self._db.get_collection("customers").find_one({"customer_id": cid})

    def update(self, cid, patch):
        self._db.get_collection("customers").update_one(
            {"customer_id": cid}, {"$set": patch}
        )
        return True


class _FakeStockRepo:
    def __init__(self):
        self.units = []
        self._seq = 0

    def find_many(self, query):
        return [dict(u) for u in self.units if all(u.get(k) == v for k, v in query.items())]

    def update(self, sid, data):
        for u in self.units:
            if u.get("stock_id") == sid:
                u.update(data)
                return True
        return False

    def create(self, data):
        self._seq += 1
        d = dict(data)
        d.setdefault("stock_id", f"stk-{self._seq}")
        self.units.append(d)
        return d


def _token(roles, store_id="BV-ONLINE-01", uid="acc-1"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "accountant",
            "roles": roles,
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


@pytest.fixture
def ctx(monkeypatch):
    app = FastAPI()
    app.include_router(
        reviews_router.router, prefix="/api/v1/online-store/refund-reviews"
    )
    db = _DB()
    conn = db
    customer_repo = _FakeCustomerRepo(db)
    stock_repo = _FakeStockRepo()

    monkeypatch.setattr(deps, "get_db", lambda: conn, raising=False)
    monkeypatch.setattr(returns_router, "get_customer_repository", lambda: customer_repo)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: None, raising=False)

    return {
        "client": TestClient(app),
        "db": db,
        "review": db.get_collection("shopify_refund_review"),
        "returns": db.get_collection("returns"),
        "ledger": db.get_collection("credit_note_ledger"),
        "customers": db.get_collection("customers"),
        "stock_repo": stock_repo,
    }


def _seed_pending_review(ctx, *, refund_id="700001", store_id="BV-ONLINE-01"):
    ctx["customers"].insert_one(
        {"customer_id": "CUST-1", "name": "Ravi", "mobile": "9", "store_credit": 0.0}
    )
    ctx["review"].insert_one(
        {
            "review_id": "rev-1",
            "shopify_refund_id": refund_id,
            "shopify_order_id": "5001",
            "order_id": "ord-abc",
            "order_number": "ONL-5001",
            "customer_id": "CUST-1",
            "customer_name": "Ravi",
            "store_id": store_id,
            "restock_store_id": "BV-GANGA-01",
            "credit_note": {
                "gross_refund": 1000.0,
                "net_refund": 1000.0,
                "gst_breakup": {"gross": 1000.0, "taxable": 952.38, "tax": 47.62, "gst_rate": 5.0},
                "lines": [],
            },
            "gross_refund": 1000.0,
            "proposed_restock": [
                {
                    "order_item_id": "it-1",
                    "product_id": "IMS-P-1",
                    "product_name": "Ray-Ban",
                    "sku": "RB-1234",
                    "return_qty": 1,
                    "unit_price": 0.0,
                    "condition": "GOOD",
                    "restock": True,
                    "reason": "Shopify refund",
                }
            ],
            "status": "PENDING",
            "resolved": False,
            "created_at": "2026-07-01T00:00:00Z",
        }
    )


def test_list_requires_role(ctx):
    # A SALES_STAFF token is outside the gate -> 403.
    r = ctx["client"].get(
        "/api/v1/online-store/refund-reviews",
        headers={"Authorization": f"Bearer {_token(['SALES_STAFF'])}"},
    )
    assert r.status_code == 403


def test_list_returns_pending_rows(ctx):
    _seed_pending_review(ctx)
    r = ctx["client"].get(
        "/api/v1/online-store/refund-reviews?status=PENDING",
        headers={"Authorization": f"Bearer {_token(['ACCOUNTANT'])}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["reviews"][0]["shopify_refund_id"] == "700001"


def test_confirm_posts_from_stored_doc_and_resolves(ctx):
    _seed_pending_review(ctx)
    ctx["stock_repo"].units.append(
        {"stock_id": "stk-1", "product_id": "IMS-P-1", "store_id": "BV-GANGA-01",
         "order_id": "ord-abc", "status": "SOLD"}
    )
    r = ctx["client"].post(
        "/api/v1/online-store/refund-reviews/rev-1/confirm",
        headers={"Authorization": f"Bearer {_token(['ACCOUNTANT'])}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "POSTED"
    # The credit note + restock were posted from the stored doc.
    assert ctx["ledger"].count_documents({"customer_id": "CUST-1"}) == 1
    ret = ctx["returns"].find_one({"shopify_refund_id": "700001"})
    assert ret is not None and ret["status"] == "COMPLETED"
    assert ctx["stock_repo"].units[0]["status"] == "AVAILABLE"
    # The review row is stamped resolved / POSTED.
    row = ctx["review"].find_one({"review_id": "rev-1"})
    assert row["status"] == "POSTED" and row["resolved"] is True
    assert row["resolved_by"] == "acc-1"


def test_confirm_twice_is_conflict(ctx):
    _seed_pending_review(ctx)
    hdr = {"Authorization": f"Bearer {_token(['ADMIN'])}"}
    first = ctx["client"].post("/api/v1/online-store/refund-reviews/rev-1/confirm", headers=hdr)
    assert first.status_code == 200
    second = ctx["client"].post("/api/v1/online-store/refund-reviews/rev-1/confirm", headers=hdr)
    assert second.status_code == 409


def test_reject_marks_resolved_without_posting(ctx):
    _seed_pending_review(ctx)
    r = ctx["client"].post(
        "/api/v1/online-store/refund-reviews/rev-1/reject",
        headers={"Authorization": f"Bearer {_token(['ACCOUNTANT'])}"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "REJECTED"
    row = ctx["review"].find_one({"review_id": "rev-1"})
    assert row["status"] == "REJECTED" and row["resolved"] is True
    # Nothing was posted.
    assert ctx["ledger"].count_documents({}) == 0
    assert ctx["returns"].count_documents({}) == 0


def test_confirm_missing_row_is_404(ctx):
    r = ctx["client"].post(
        "/api/v1/online-store/refund-reviews/nope/confirm",
        headers={"Authorization": f"Bearer {_token(['ACCOUNTANT'])}"},
    )
    assert r.status_code == 404
