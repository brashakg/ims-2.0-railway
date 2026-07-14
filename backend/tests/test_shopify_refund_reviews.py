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
from api.services import shopify_refund as refund_svc  # noqa: E402


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
            elif "$ne" in expected:
                if actual == expected["$ne"]:
                    return False
            elif "$type" in expected:
                if not isinstance(actual, str):
                    return False
            else:
                return False
        elif actual != expected:
            # A None filter value matches a missing OR null field (Mongo semantics).
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


class DuplicateKeyError(Exception):
    """Named exactly like pymongo's so shopify_refund._is_dup_key recognises it."""


class _UniqueReturnsColl(_Coll):
    """Fake `returns` collection that ENFORCES the unique partial index on
    shopify_refund_id (a second insert of the same non-null id raises a
    DuplicateKeyError-like error) AND supports find_one_and_update -- so the
    claim-first idempotency + the retry-claim path are actually exercised. The
    plain _Coll silently allowed duplicate inserts, which HID the swallowed-refund
    bug this test file now covers."""

    def insert_one(self, doc):
        rid = doc.get("shopify_refund_id")
        if isinstance(rid, str) and rid:
            for d in self.docs:
                if d.get("shopify_refund_id") == rid:
                    raise DuplicateKeyError(
                        f"E11000 duplicate key error: shopify_refund_id {rid}"
                    )
        return super().insert_one(doc)

    def find_one_and_update(self, filter_, update, **kwargs):
        # Atomic in real Mongo; here it is a single-threaded emulation. The service
        # only checks truthiness of the return value, so post-image is fine.
        for d in self.docs:
            if _match(d, filter_):
                for kk, vv in (update.get("$set") or {}).items():
                    d[kk] = vv
                return dict(d)
        return None


class _DB:
    is_connected = True

    def __init__(self):
        self._c = {}
        self.db = self

    def get_collection(self, name):
        if name not in self._c:
            self._c[name] = _UniqueReturnsColl() if name == "returns" else _Coll()
        return self._c[name]


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


def _seed_no_customer_review(ctx, *, refund_id="700050", store_id="BV-ONLINE-01"):
    """A guest / no-customer online refund: the review row carries a computed
    credit note but NO customer_id -> _issue_store_credit cannot run, so a confirm
    must honestly re-fail (CREDIT_FAILED) rather than falsely POST."""
    ctx["review"].insert_one(
        {
            "review_id": "rev-nc",
            "shopify_refund_id": refund_id,
            "shopify_order_id": "5050",
            "order_id": "ord-nc",
            "order_number": "ONL-5050",
            "customer_id": None,
            "customer_name": None,
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


# ---------------------------------------------------------------------------
# Retry-must-re-issue regression (the swallowed-refund money bug)
# The `returns` collection here ENFORCES the unique shopify_refund_id index, so
# the claim-first insert on a retry genuinely raises DuplicateKeyError -- the
# exact condition the plain fake never reproduced.
# ---------------------------------------------------------------------------


def _confirm(ctx, review_id="rev-nc", role="ACCOUNTANT"):
    return ctx["client"].post(
        f"/api/v1/online-store/refund-reviews/{review_id}/confirm",
        headers={"Authorization": f"Bearer {_token([role])}"},
    )


def test_no_customer_first_confirm_is_credit_failed_not_posted(ctx):
    # (a) First confirm on a no-customer refund: NO credit note can be issued ->
    # the review must NOT close as POSTED; a returns doc exists but uncredited.
    _seed_no_customer_review(ctx)
    r = _confirm(ctx)
    assert r.status_code == 422, r.text

    row = ctx["review"].find_one({"review_id": "rev-nc"})
    assert row["status"] == "CREDIT_FAILED"
    assert row["resolved"] is False

    ret = ctx["returns"].find_one({"shopify_refund_id": "700050"})
    assert ret is not None
    assert ret["credit_note_issued"] is False
    assert ret["status"] == "CREDIT_FAILED"
    # No GST reversal was written.
    assert ctx["ledger"].count_documents({}) == 0


def test_retry_still_no_customer_refails_not_silently_posted(ctx):
    # (b) The bug: on retry the claim-first insert hits the unique index. The OLD
    # code returned "duplicate" -> router stamped POSTED with NO credit note. Now
    # the retry re-attempts; still no customer -> it must re-fail honestly, and it
    # must NOT create a second returns doc or any ledger row.
    _seed_no_customer_review(ctx)
    assert _confirm(ctx).status_code == 422

    retry = _confirm(ctx)
    assert retry.status_code == 422, retry.text

    row = ctx["review"].find_one({"review_id": "rev-nc"})
    assert row["status"] == "CREDIT_FAILED"
    assert row["resolved"] is False
    # Exactly one returns doc, still uncredited; no credit note ledger row ever.
    assert ctx["returns"].count_documents({"shopify_refund_id": "700050"}) == 1
    assert ctx["returns"].find_one({"shopify_refund_id": "700050"})["credit_note_issued"] is False
    assert ctx["ledger"].count_documents({}) == 0


def test_retry_after_customer_available_issues_credit_and_posts(ctx):
    # (c) Once a customer is attached, the SAME retry path re-issues the credit
    # note (a real credit_note_ledger row) and the row resolves POSTED. This is the
    # whole point of not permanently consuming the refund id.
    _seed_no_customer_review(ctx)
    assert _confirm(ctx).status_code == 422
    # The returns doc is claimed-but-uncredited at this point.
    assert ctx["returns"].find_one({"shopify_refund_id": "700050"})["credit_note_issued"] is False

    # Accountant fixes the cause: link the online order to a real customer.
    ctx["customers"].insert_one(
        {"customer_id": "CUST-9", "name": "Meera", "mobile": "8", "store_credit": 0.0}
    )
    ctx["review"].update_one({"review_id": "rev-nc"}, {"$set": {"customer_id": "CUST-9"}})

    ok = _confirm(ctx)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "POSTED"

    ret = ctx["returns"].find_one({"shopify_refund_id": "700050"})
    assert ret["credit_note_issued"] is True
    assert ret["status"] == "COMPLETED"
    # The GST reversal (CDNR) row was finally written -- exactly once.
    assert ctx["ledger"].count_documents({"customer_id": "CUST-9"}) == 1
    row = ctx["review"].find_one({"review_id": "rev-nc"})
    assert row["status"] == "POSTED" and row["resolved"] is True


def test_genuine_redelivery_of_credited_refund_is_duplicate_no_double_issue(ctx):
    # (d) A redelivery of an ALREADY-credited refund must return "duplicate" and
    # NOT issue a second credit note (service-level, bypassing the router's
    # resolved-guard to prove the claim-first index is the real protection).
    ctx["customers"].insert_one(
        {"customer_id": "CUST-1", "name": "Ravi", "mobile": "9", "store_credit": 0.0}
    )
    review = {
        "shopify_refund_id": "700077",
        "order_id": "ord-xyz",
        "order_number": "ONL-5077",
        "customer_id": "CUST-1",
        "customer_name": "Ravi",
        "store_id": "BV-ONLINE-01",
        "restock_store_id": "BV-GANGA-01",
        "shopify_order_id": "5077",
        "credit_note": {
            "gross_refund": 1000.0,
            "net_refund": 1000.0,
            "gst_breakup": {"gross": 1000.0, "taxable": 952.38, "tax": 47.62, "gst_rate": 5.0},
            "lines": [],
        },
        "proposed_restock": [],
    }

    first = refund_svc.post_from_review(ctx["db"], review)
    assert first["status"] == "credited"
    assert ctx["ledger"].count_documents({"customer_id": "CUST-1"}) == 1
    assert ctx["returns"].count_documents({"shopify_refund_id": "700077"}) == 1

    dup = refund_svc.post_from_review(ctx["db"], review)
    assert dup["status"] == "duplicate"
    # No second credit note, no second returns doc.
    assert ctx["ledger"].count_documents({"customer_id": "CUST-1"}) == 1
    assert ctx["returns"].count_documents({"shopify_refund_id": "700077"}) == 1


def test_router_never_posts_a_duplicate_without_a_real_credit_note(ctx, monkeypatch):
    # (e) Belt-and-braces: even if the service layer ever hands back a bare
    # "duplicate", the router must re-read the returns doc and REFUSE to stamp
    # POSTED when credit_note_issued is not True.
    ctx["review"].insert_one(
        {
            "review_id": "rev-dup",
            "shopify_refund_id": "700099",
            "order_id": "ord-dup",
            "store_id": "BV-ONLINE-01",
            "credit_note": {"gross_refund": 1000.0, "gst_breakup": {}, "lines": []},
            "status": "PENDING",
            "resolved": False,
            "created_at": "2026-07-01T00:00:00Z",
        }
    )
    # A returns doc that was claimed but never credited (the stale-claim state).
    ctx["returns"].insert_one(
        {"shopify_refund_id": "700099", "status": "CREDIT_FAILED", "credit_note_issued": False}
    )
    monkeypatch.setattr(
        refund_svc,
        "post_from_review",
        lambda db, row: {"status": "duplicate", "refund_id": "700099", "return_id": "RET-x"},
    )

    r = _confirm(ctx, review_id="rev-dup")
    assert r.status_code == 422, r.text
    row = ctx["review"].find_one({"review_id": "rev-dup"})
    assert row["status"] != "POSTED"
    assert row["resolved"] is False
