"""
IMS 2.0 - GST default + block-save + backfill tests
===================================================
Covers the P1 GST-correctness fix (QA found an uncategorized product billed at
18% GST):

  1. services/gst_rates.py default fallback is now 5% (optical-dominant), not 18%.
  2. routers/products.py create + update reject a blank/null/missing category
     with HTTP 422 (server-side guard; AddProductPage already enforces it).
  3. scripts/backfill_uncategorized_to_frame.py is dry-run by default, sets
     blank-category rows to FRAME/5%/9003 on --apply, audit-logs each change, and
     is idempotent.

Async handler tests use pytest-asyncio. Tests that exercise the create/update
routes go through the FastAPI TestClient. Tests that need a real Mongo will skip
locally (no DB) and run in CI (mongo:7.0 service) - that's fine; the guard fires
*before* the DB path so the 422 assertions hold with or without a DB.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Async tests in this file are driven by pytest-asyncio (asyncio_mode=auto is
# also set in pytest.ini; this marker makes the intent explicit per the spec).
pytestmark = pytest.mark.asyncio

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402
from api.services import gst_rates  # noqa: E402
from api.services.gst_rates import gst_rate_for_category  # noqa: E402
from api.routers.auth import create_access_token  # noqa: E402
from scripts import backfill_uncategorized_to_frame as backfill  # noqa: E402


# ============================================================================
# helpers
# ============================================================================


def _catalog_headers():
    """JWT for a catalog-write-capable role (ADMIN)."""
    token = create_access_token(
        {
            "user_id": "t-admin",
            "username": "tadmin",
            "roles": ["ADMIN"],
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _client() -> TestClient:
    return TestClient(app)


_BASE_BODY = {
    "sku": "SKU-GSTTEST-1",
    "brand": "Fastrack",
    "model": "P357BK1",
    "mrp": 1000.0,
    "offer_price": 900.0,
}


# ============================================================================
# 1. create rejects blank/null category, accepts FRAME
# ============================================================================


async def test_create_rejects_blank_category():
    with _client() as client:
        body = dict(_BASE_BODY, category="")
        resp = client.post("/api/v1/products", headers=_catalog_headers(), json=body)
        assert resp.status_code == 422
        assert "categor" in resp.json()["detail"].lower()


async def test_create_rejects_whitespace_category():
    with _client() as client:
        body = dict(_BASE_BODY, category="   ")
        resp = client.post("/api/v1/products", headers=_catalog_headers(), json=body)
        assert resp.status_code == 422


async def test_create_rejects_null_category():
    # Explicit null -> pydantic accepts (field is Optional in the model? No:
    # ProductCreate.category is required `str`, so null is a 422 from pydantic).
    # Either way the contract is "blank/null category cannot be saved" -> 422.
    with _client() as client:
        body = dict(_BASE_BODY, category=None)
        resp = client.post("/api/v1/products", headers=_catalog_headers(), json=body)
        assert resp.status_code == 422


async def test_create_rejects_missing_category():
    with _client() as client:
        body = dict(_BASE_BODY)  # no category key at all
        resp = client.post("/api/v1/products", headers=_catalog_headers(), json=body)
        assert resp.status_code == 422


async def test_create_rejects_unknown_category():
    with _client() as client:
        body = dict(_BASE_BODY, category="WIDGET")
        resp = client.post("/api/v1/products", headers=_catalog_headers(), json=body)
        assert resp.status_code == 422


async def test_create_accepts_frame():
    with _client() as client:
        body = dict(_BASE_BODY, category="FRAME")
        resp = client.post("/api/v1/products", headers=_catalog_headers(), json=body)
        # 201 (created / stub) with no DB; in CI with a DB it persists. The point
        # is it is NOT rejected by the category guard.
        assert resp.status_code not in (403, 422)


# ============================================================================
# 2. update cannot blank-out an existing category
# ============================================================================


async def test_update_rejects_blanking_category():
    with _client() as client:
        resp = client.put(
            "/api/v1/products/p1", headers=_catalog_headers(), json={"category": ""}
        )
        assert resp.status_code == 422


async def test_update_without_category_not_blocked_by_guard():
    # An update that omits category must not be rejected by the category guard.
    with _client() as client:
        resp = client.put(
            "/api/v1/products/p1", headers=_catalog_headers(), json={"brand": "X"}
        )
        assert resp.status_code != 422


# ============================================================================
# 3. default fallback rate is now 5% (optical-dominant)
# ============================================================================


def test_default_rate_for_blank_is_5():
    assert gst_rate_for_category("") == 5.0


def test_default_rate_for_unknown_is_5():
    assert gst_rate_for_category("WIDGET") == 5.0
    assert gst_rate_for_category(None) == 5.0  # type: ignore[arg-type]


def test_default_constant_is_5():
    assert gst_rates.DEFAULT_GST_RATE == 5.0


def test_known_categories_unchanged():
    # Sanity: the fallback change must not have moved the explicit rates.
    assert gst_rate_for_category("FRAME") == 5.0
    assert gst_rate_for_category("SUNGLASS") == 18.0
    assert gst_rate_for_category("WATCH") == 18.0


# ============================================================================
# 4. backfill: dry-run mutates nothing, --apply sets FRAME/5%, audit-logs,
#    second run is a no-op (idempotent)
# ============================================================================


class _FakeProducts:
    """Minimal products collection supporting the ops the backfill uses:
    find(filter) with $or / $exists / null / "" / whitespace-regex, and
    update_one({product_id|_id}, {$set}). Mirrors a backfill-relevant slice of
    real pymongo semantics."""

    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    def _is_blank(self, doc):
        if "category" not in doc:
            return True
        cat = doc.get("category")
        if cat is None:
            return True
        return str(cat).strip() == ""

    def find(self, flt=None):
        # The backfill only ever passes the BLANK_CATEGORY_FILTER.
        return [dict(d) for d in self.docs if self._is_blank(d)]

    def update_one(self, match_q, update):
        set_ = update.get("$set", {})
        for d in self.docs:
            if "product_id" in match_q and d.get("product_id") == match_q["product_id"]:
                d.update(set_)
                return type("R", (), {"modified_count": 1})()
            if "_id" in match_q and d.get("_id") == match_q["_id"]:
                d.update(set_)
                return type("R", (), {"modified_count": 1})()
        return type("R", (), {"modified_count": 0})()


class _FakeAudit:
    def __init__(self):
        self.rows = []

    def insert_one(self, doc):
        self.rows.append(dict(doc))
        return type("R", (), {"inserted_id": len(self.rows)})()


class _FakeDB:
    def __init__(self, products, audit):
        self._p = products
        self._a = audit
        self.is_connected = True

    def get_collection(self, name):
        if name == "products":
            return self._p
        if name == "audit_log":
            return self._a
        return None


def _seed_fake():
    products = _FakeProducts(
        [
            {"product_id": "P-BLANK-1", "sku": "S1", "brand": "Fastrack",
             "model": "P357BK1", "category": "", "gst_rate": 18.0, "hsn_code": "0"},
            {"product_id": "P-NULL-2", "sku": "S2", "category": None, "gst_rate": 18.0},
            {"product_id": "P-MISSING-3", "sku": "S3", "brand": "X"},  # no category key
            {"product_id": "P-OK-4", "sku": "S4", "category": "SUNGLASS",
             "gst_rate": 18.0, "hsn_code": "900410"},  # must NOT be touched
        ]
    )
    audit = _FakeAudit()
    return products, audit


def test_backfill_dry_run_mutates_nothing(monkeypatch):
    products, audit = _seed_fake()
    monkeypatch.setattr(backfill, "_connect", lambda: _FakeDB(products, audit))

    rc = backfill.run(apply=False)
    assert rc == 0
    # No category changed; no audit rows.
    cats = {d["product_id"]: d.get("category") for d in products.docs}
    assert cats["P-BLANK-1"] == ""
    assert cats["P-NULL-2"] is None
    assert "category" not in next(d for d in products.docs if d["product_id"] == "P-MISSING-3")
    assert cats["P-OK-4"] == "SUNGLASS"
    assert audit.rows == []


def test_backfill_apply_sets_frame_and_audits(monkeypatch):
    products, audit = _seed_fake()
    monkeypatch.setattr(backfill, "_connect", lambda: _FakeDB(products, audit))

    rc = backfill.run(apply=True)
    assert rc == 0

    by_id = {d["product_id"]: d for d in products.docs}
    # All three blank rows -> FRAME / 5 / 9003
    for pid in ("P-BLANK-1", "P-NULL-2", "P-MISSING-3"):
        assert by_id[pid]["category"] == "FRAME"
        assert by_id[pid]["gst_rate"] == 5.0
        assert by_id[pid]["hsn_code"] == "9003"
    # Already-categorized row untouched.
    assert by_id["P-OK-4"]["category"] == "SUNGLASS"
    assert by_id["P-OK-4"]["gst_rate"] == 18.0

    # One audit row per changed product, correct kind + captured prior values.
    assert len(audit.rows) == 3
    assert all(r["kind"] == "gst_backfill_2026_05_28" for r in audit.rows)
    blank_audit = next(r for r in audit.rows if r["product_id"] == "P-BLANK-1")
    assert blank_audit["prior"]["category"] == ""
    assert blank_audit["prior"]["gst_rate"] == 18.0
    assert blank_audit["new"]["category"] == "FRAME"


def test_backfill_is_idempotent(monkeypatch):
    products, audit = _seed_fake()
    monkeypatch.setattr(backfill, "_connect", lambda: _FakeDB(products, audit))

    backfill.run(apply=True)
    audit_after_first = len(audit.rows)
    assert audit_after_first == 3

    # Second apply: nothing blank remains -> 0 changes, no new audit rows.
    rc = backfill.run(apply=True)
    assert rc == 0
    assert len(audit.rows) == audit_after_first  # no new rows written


def test_backfill_fails_loud_without_db(monkeypatch):
    monkeypatch.setattr(backfill, "_connect", lambda: None)
    rc = backfill.run(apply=False)
    assert rc == 2  # non-zero -> fail-loud, nothing changed
