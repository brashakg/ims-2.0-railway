"""
IMS 2.0 - Bulk product create test suite (Rapid Grid, Phase B)
==============================================================
Covers POST /api/v1/products/bulk-create -- the in-app Rapid Grid endpoint
that creates many products in one call (NO CSV / file import).

Two layers:

  1. PURE row-validation tests (no DB) -- _validate_bulk_row reuses the SAME
     validators as the single-create path (_validate_category_or_422,
     _assert_mrp_ge_offer, the CL modality set, in-batch SKU dedupe), so its
     reject/accept behaviour is exercised deterministically with zero infra.

  2. ENDPOINT tests against a REAL mongo:7.0 (CI provides one; local dev may
     fall back to localhost). Skipped fail-soft when Mongo is unreachable so
     the unit-test sweep still passes on a laptop without Mongo. These prove
     the batch contract:
       - valid rows are created; invalid rows are SKIPPED + reported with why
       - the summary counts {total, created, failed}
       - a duplicate SKU within the batch is rejected
       - a SKU that already exists in the catalog is rejected
       - the persisted doc shares the single-create shape (HSN/GST defaults)
       - role gating: only ADMIN / CATALOG_MANAGER / SUPERADMIN may write

Business rules enforced (CLAUDE.md "Non-negotiable business rules"):
  MRP >= offer_price (blocked at DB) ; per-category GST/HSN defaults
  (FRAME 5%, SUNGLASS 18%) come from the canonical gst_rates table so the
  master rate equals what POS bills.
"""

# pylint: disable=redefined-outer-name,unused-argument

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Layer 1 -- pure row-validation tests (no DB)
# ============================================================================


def _row(**overrides) -> Any:
    """Build a valid ProductCreate, overriding any field.

    Step-9: the canonical registry is now the rulebook at the bulk door, so a
    VALID frame row must carry its category-conditional required fields. A FRAME
    needs colour_code -- supplied here via `color` (the flat schema folds
    color -> colour_code) so the default row is genuinely complete.
    """
    from api.routers.products import ProductCreate

    base = {
        "sku": "RG-1",
        "category": "FRAME",
        "brand": "Acme",
        "model": "M1",
        "color": "BLK",
        "mrp": 1000.0,
        "offer_price": 900.0,
    }
    base.update(overrides)
    return ProductCreate(**base)


def _cl_row(**overrides) -> Any:
    """Build a VALID contact-lens ProductCreate. A CONTACT_LENS needs
    model_name + power + expiry_date (step-9 reconcile) in `attributes`."""
    from api.routers.products import ProductCreate

    base = {
        "sku": "RG-CL-1",
        "category": "CONTACT_LENS",
        "brand": "Acuvue",
        "model": "Oasys",
        "mrp": 1200.0,
        "offer_price": 1100.0,
        "attributes": {
            "brand_name": "Acuvue",
            "model_name": "Oasys",
            "power": "-2.00",
            "expiry_date": "2027-01-01",
        },
    }
    base.update(overrides)
    return ProductCreate(**base)


class TestValidateBulkRow:
    """Exhaustive, deterministic tests of the pure per-row validator."""

    def test_valid_row_has_no_errors(self):
        from api.routers.products import _validate_bulk_row

        assert _validate_bulk_row(_row(), set()) == []

    def test_blank_category_rejected(self):
        from api.routers.products import _validate_bulk_row

        errors = _validate_bulk_row(_row(category="   "), set())
        assert errors
        assert any("category" in e.lower() for e in errors)

    def test_unknown_category_rejected(self):
        from api.routers.products import _validate_bulk_row

        errors = _validate_bulk_row(_row(category="NONSENSE"), set())
        assert any("category" in e.lower() for e in errors)

    def test_category_normalized_in_place_on_success(self):
        from api.routers.products import _validate_bulk_row

        # Lowercase/whitespace -> normalized to the canonical key.
        row = _row(category="  frame  ")
        assert _validate_bulk_row(row, set()) == []
        assert row.category == "frame".strip() or row.category.upper() == "FRAME"

    def test_offer_above_mrp_rejected(self):
        from api.routers.products import _validate_bulk_row

        errors = _validate_bulk_row(_row(mrp=1000.0, offer_price=1200.0), set())
        assert any("mrp" in e.lower() for e in errors)

    def test_offer_equal_mrp_ok(self):
        from api.routers.products import _validate_bulk_row

        assert _validate_bulk_row(_row(mrp=1000.0, offer_price=1000.0), set()) == []

    def test_bad_modality_rejected(self):
        from api.routers.products import _validate_bulk_row

        errors = _validate_bulk_row(_cl_row(modality="WEEKLY"), set())
        assert any("modality" in e.lower() for e in errors)

    def test_good_modality_ok(self):
        from api.routers.products import _validate_bulk_row

        # A complete CL row (model_name + power + expiry_date) with a good
        # modality has no errors.
        assert _validate_bulk_row(_cl_row(modality="DAILY"), set()) == []

    def test_duplicate_sku_in_batch_rejected(self):
        from api.routers.products import _validate_bulk_row

        seen = {"RG-1"}
        errors = _validate_bulk_row(_row(sku="RG-1"), seen)
        assert any("duplicate" in e.lower() for e in errors)

    def test_multiple_errors_accumulate(self):
        from api.routers.products import _validate_bulk_row

        # Bad category AND offer > MRP -> both reported (validation does not
        # short-circuit on the first failure).
        errors = _validate_bulk_row(
            _row(category="", mrp=100.0, offer_price=200.0), set()
        )
        assert len(errors) >= 2


# ============================================================================
# Layer 2 -- endpoint tests against a real mongo:7.0 (skip if absent)
# ============================================================================


@pytest.fixture(scope="module")
def mongo_db():
    """Real mongo:7.0 connection. Skip the module fail-soft if absent."""
    try:
        from pymongo import MongoClient
        from pymongo.errors import ServerSelectionTimeoutError
    except ImportError:
        pytest.skip("pymongo unavailable")
        return None

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except (ServerSelectionTimeoutError, Exception):  # noqa: BLE001
        pytest.skip(f"Mongo unavailable at {uri}; skipping integration tests")
        return None

    db_name = f"ims_test_bulk_create_{uuid.uuid4().hex[:8]}"
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:  # noqa: BLE001
            pass
        client.close()


class _DBProxy:
    """Minimal get_db() shape exposing mongo collections by name + attr."""

    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, name):
        return self._db[name]

    def __getattr__(self, name):
        return self._db[name]


@pytest.fixture
def patch_db(mongo_db, monkeypatch):
    """Point both get_db() entrypoints at the test mongo db and force
    DATABASE_AVAILABLE on. Wipes the products collection before each test so
    rows from one test don't leak into the next, and clears the list cache."""
    for coll in ("products",):
        try:
            mongo_db[coll].delete_many({})
        except Exception:  # noqa: BLE001
            pass

    proxy = _DBProxy(mongo_db)
    import api.dependencies as deps
    from database import connection as conn

    monkeypatch.setattr(deps, "DATABASE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(deps, "get_db", lambda: proxy)
    monkeypatch.setattr(conn, "get_db", lambda: proxy, raising=False)

    try:
        from api.services.cache import cache

        cache.clear() if hasattr(cache, "clear") else None
    except Exception:  # noqa: BLE001
        pass
    return proxy


ADMIN_USER = {
    "user_id": "test-admin-bulk-create",
    "username": "bulkcreator",
    "roles": ["SUPERADMIN"],
    "active_store_id": "BV-TEST-01",
}


def _body(rows: List[Dict[str, Any]]):
    from api.routers.products import BulkCreateRequest

    return BulkCreateRequest(products=rows)


class TestBulkCreateEndpoint:
    """POST /products/bulk-create behaviour against a real DB."""

    def test_all_valid_rows_created(self, mongo_db, patch_db):
        from api.routers.products import bulk_create_products

        rows = [
            {
                "sku": "RG-A",
                "category": "FRAME",
                "brand": "B",
                "model": "M1",
                "color": "BLK",
                "mrp": 1000.0,
                "offer_price": 900.0,
            },
            {
                "sku": "RG-B",
                "category": "SUNGLASS",
                "brand": "B",
                "model": "M2",
                "color": "BLK",
                "mrp": 2000.0,
                "offer_price": 1800.0,
            },
        ]
        res = asyncio.run(bulk_create_products(_body(rows), ADMIN_USER))

        assert res["summary"] == {"total": 2, "created": 2, "failed": 0}
        assert all(r["ok"] for r in res["results"])
        assert all(r["product_id"] for r in res["results"])
        # Persisted.
        assert mongo_db["products"].count_documents({}) == 2

    def test_invalid_rows_skipped_valid_created(self, mongo_db, patch_db):
        from api.routers.products import bulk_create_products

        rows = [
            {
                "sku": "RG-OK",
                "category": "FRAME",
                "brand": "B",
                "model": "M1",
                "color": "BLK",
                "mrp": 1000.0,
                "offer_price": 900.0,
            },
            # offer > MRP -> invalid, skipped.
            {
                "sku": "RG-BAD",
                "category": "FRAME",
                "brand": "B",
                "model": "M2",
                "color": "BLK",
                "mrp": 1000.0,
                "offer_price": 1500.0,
            },
            # unknown category -> invalid, skipped.
            {
                "sku": "RG-BAD2",
                "category": "WIDGET",
                "brand": "B",
                "model": "M3",
                "color": "BLK",
                "mrp": 500.0,
                "offer_price": 400.0,
            },
        ]
        res = asyncio.run(bulk_create_products(_body(rows), ADMIN_USER))

        assert res["summary"]["total"] == 3
        assert res["summary"]["created"] == 1
        assert res["summary"]["failed"] == 2
        # Only the valid row was persisted.
        assert mongo_db["products"].count_documents({}) == 1
        assert mongo_db["products"].find_one({"sku": "RG-OK"}) is not None
        # Failed rows carry an error reason + the right index.
        failed = [r for r in res["results"] if not r["ok"]]
        assert {r["index"] for r in failed} == {1, 2}
        assert all(r["errors"] for r in failed)

    def test_duplicate_sku_in_batch_only_first_created(self, mongo_db, patch_db):
        from api.routers.products import bulk_create_products

        rows = [
            {
                "sku": "RG-DUP",
                "category": "FRAME",
                "brand": "B",
                "model": "M1",
                "color": "BLK",
                "mrp": 1000.0,
                "offer_price": 900.0,
            },
            {
                "sku": "RG-DUP",
                "category": "FRAME",
                "brand": "B",
                "model": "M2",
                "color": "BLK",
                "mrp": 1100.0,
                "offer_price": 1000.0,
            },
        ]
        res = asyncio.run(bulk_create_products(_body(rows), ADMIN_USER))

        assert res["summary"]["created"] == 1
        assert res["summary"]["failed"] == 1
        assert mongo_db["products"].count_documents({"sku": "RG-DUP"}) == 1
        assert res["results"][0]["ok"] is True
        assert res["results"][1]["ok"] is False
        assert any("duplicate" in e.lower() for e in res["results"][1]["errors"])

    def test_existing_sku_rejected(self, mongo_db, patch_db):
        from api.routers.products import bulk_create_products
        from database.repositories.product_repository import ProductRepository

        # Seed an existing product.
        ProductRepository(mongo_db["products"]).create(
            {
                "sku": "RG-EXIST",
                "category": "FRAME",
                "brand": "B",
                "model": "M0",
                "color": "BLK",
                "mrp": 999.0,
                "offer_price": 999.0,
                "is_active": True,
            }
        )

        rows = [
            {
                "sku": "RG-EXIST",
                "category": "FRAME",
                "brand": "B",
                "model": "M1",
                "color": "BLK",
                "mrp": 1000.0,
                "offer_price": 900.0,
            },
        ]
        res = asyncio.run(bulk_create_products(_body(rows), ADMIN_USER))

        assert res["summary"]["created"] == 0
        assert res["summary"]["failed"] == 1
        assert any("already exists" in e.lower() for e in res["results"][0]["errors"])
        # The seed is still the only doc with that SKU.
        assert mongo_db["products"].count_documents({"sku": "RG-EXIST"}) == 1

    def test_gst_hsn_defaults_applied(self, mongo_db, patch_db):
        from api.routers.products import bulk_create_products
        from api.services.gst_rates import gst_rate_for_category

        rows = [
            {
                "sku": "RG-GST",
                "category": "FRAME",
                "brand": "B",
                "model": "M1",
                "color": "BLK",
                "mrp": 1000.0,
                "offer_price": 900.0,
            },
        ]
        asyncio.run(bulk_create_products(_body(rows), ADMIN_USER))

        doc = mongo_db["products"].find_one({"sku": "RG-GST"})
        assert doc is not None
        # FRAME defaults to 5% under the canonical table (matches POS billing).
        assert doc["gst_rate"] == gst_rate_for_category("FRAME")
        assert doc["hsn_code"]  # an HSN was resolved from the category


# ============================================================================
# Layer 3 -- role gating (no DB needed; require_roles runs before the handler)
# ============================================================================


def _headers(roles):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "t-1",
            "username": "t",
            "roles": roles,
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


_VALID_BATCH = {
    "products": [
        {
            "sku": "RG-G1",
            "category": "FRAME",
            "brand": "B",
            "model": "M1",
            "color": "BLK",
            "mrp": 1000.0,
            "offer_price": 900.0,
        }
    ]
}


class TestBulkCreateGating:
    def test_sales_staff_blocked(self, client, staff_headers):
        resp = client.post(
            "/api/v1/products/bulk-create", headers=staff_headers, json=_VALID_BATCH
        )
        assert resp.status_code == 403

    def test_store_manager_blocked(self, client):
        resp = client.post(
            "/api/v1/products/bulk-create",
            headers=_headers(["STORE_MANAGER"]),
            json=_VALID_BATCH,
        )
        assert resp.status_code == 403

    def test_catalog_manager_allowed(self, client):
        resp = client.post(
            "/api/v1/products/bulk-create",
            headers=_headers(["CATALOG_MANAGER"]),
            json=_VALID_BATCH,
        )
        assert resp.status_code != 403

    def test_admin_allowed(self, client):
        resp = client.post(
            "/api/v1/products/bulk-create",
            headers=_headers(["ADMIN"]),
            json=_VALID_BATCH,
        )
        assert resp.status_code != 403

    def test_superadmin_allowed(self, client, auth_headers):
        resp = client.post(
            "/api/v1/products/bulk-create", headers=auth_headers, json=_VALID_BATCH
        )
        assert resp.status_code != 403

    def test_empty_batch_422(self, client, auth_headers):
        # min_length=1 -> an empty products list is a validation error.
        resp = client.post(
            "/api/v1/products/bulk-create", headers=auth_headers, json={"products": []}
        )
        assert resp.status_code == 422
