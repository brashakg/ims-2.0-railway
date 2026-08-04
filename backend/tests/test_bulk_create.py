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


def _errors(row, seen=None) -> List[str]:
    """_validate_bulk_row now returns (errors, resolved_sku) -- SKU became
    OPTIONAL (auto-minted), so the validator has to report the SKU the row will
    actually be created with. These pure tests only care about the errors."""
    from api.routers.products import _validate_bulk_row

    return _validate_bulk_row(row, set() if seen is None else seen)[0]


class TestValidateBulkRow:
    """Exhaustive, deterministic tests of the pure per-row validator."""

    def test_valid_row_has_no_errors(self):
        assert _errors(_row()) == []

    def test_blank_category_rejected(self):
        errors = _errors(_row(category="   "))
        assert errors
        assert any("category" in e.lower() for e in errors)

    def test_unknown_category_rejected(self):
        errors = _errors(_row(category="NONSENSE"))
        assert any("category" in e.lower() for e in errors)

    def test_category_normalized_in_place_on_success(self):
        # Lowercase/whitespace -> normalized to the canonical key.
        row = _row(category="  frame  ")
        assert _errors(row) == []
        assert row.category == "frame".strip() or row.category.upper() == "FRAME"

    def test_offer_above_mrp_rejected(self):
        errors = _errors(_row(mrp=1000.0, offer_price=1200.0))
        assert any("mrp" in e.lower() for e in errors)

    def test_offer_equal_mrp_ok(self):
        assert _errors(_row(mrp=1000.0, offer_price=1000.0)) == []

    def test_bad_modality_rejected(self):
        errors = _errors(_cl_row(modality="WEEKLY"))
        assert any("modality" in e.lower() for e in errors)

    def test_good_modality_ok(self):
        # A complete CL row (model_name + power + expiry_date) with a good
        # modality has no errors.
        assert _errors(_cl_row(modality="DAILY")) == []

    def test_duplicate_sku_in_batch_rejected(self):
        errors = _errors(_row(sku="RG-1"), {"RG-1"})
        assert any("duplicate" in e.lower() for e in errors)

    def test_multiple_errors_accumulate(self):
        # Bad category AND offer > MRP -> both reported (validation does not
        # short-circuit on the first failure).
        errors = _errors(_row(category="", mrp=100.0, offer_price=200.0))
        assert len(errors) >= 2

    # -- SKU is now OPTIONAL (owner ask: auto-generate) --------------------

    def test_blank_sku_is_accepted_and_minted(self):
        """A blank SKU is no longer 'SKU is required' -- the validator resolves
        the SKU the canonical door will mint and reports it back."""
        from api.routers.products import _validate_bulk_row

        errors, resolved = _validate_bulk_row(_row(sku=None), set())
        assert errors == []
        assert resolved, "a blank SKU must resolve to a minted SKU"
        assert not any("sku is required" in e.lower() for e in errors)

    def test_supplied_sku_wins_verbatim(self):
        from api.routers.products import _validate_bulk_row

        errors, resolved = _validate_bulk_row(_row(sku="LEGACY-SKU-9"), set())
        assert errors == []
        assert resolved == "LEGACY-SKU-9"

    def test_two_blank_sku_rows_resolving_to_same_sku_collide_in_batch(self):
        """PANEL P1: identical blank-SKU rows mint the SAME base SKU. The Hub
        identity guard cannot catch this for a category with no brand+model
        (compute_identity_key returns None), so the in-batch dedupe MUST key on
        the RESOLVED sku or two duplicate billing masters land silently."""
        from api.routers.products import _validate_bulk_row

        first_errors, first_sku = _validate_bulk_row(_row(sku=None), set())
        assert first_errors == []
        seen = {first_sku}
        second_errors, second_sku = _validate_bulk_row(_row(sku=None), seen)
        assert second_sku == first_sku
        assert any("duplicate" in e.lower() for e in second_errors)

    def test_two_blank_sku_rows_with_distinct_identity_do_not_collide(self):
        """Different products with blank SKUs mint DISTINCT SKUs and both pass."""
        from api.routers.products import _validate_bulk_row

        e1, sku1 = _validate_bulk_row(_row(sku=None, model="M1"), set())
        e2, sku2 = _validate_bulk_row(_row(sku=None, model="M2"), {sku1})
        assert e1 == [] and e2 == []
        assert sku1 and sku2 and sku1 != sku2


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

    # -- blank-SKU rows at the ENDPOINT layer (P1: this layer was untested) --

    def test_blank_sku_rows_created_with_distinct_minted_skus(self, mongo_db, patch_db):
        """Two blank-SKU rows with DISTINCT identities are both created, and
        the response carries distinct, non-empty minted SKUs."""
        from api.routers.products import bulk_create_products

        rows = [
            {
                "category": "FRAME",
                "brand": "MintBrand",
                "model": "MM1",
                "color": "BLK",
                "mrp": 1000.0,
                "offer_price": 900.0,
            },
            {
                "category": "FRAME",
                "brand": "MintBrand",
                "model": "MM2",
                "color": "BLK",
                "mrp": 1100.0,
                "offer_price": 1000.0,
            },
        ]
        res = asyncio.run(bulk_create_products(_body(rows), ADMIN_USER))

        assert res["summary"] == {"total": 2, "created": 2, "failed": 0}
        skus = [r["sku"] for r in res["results"]]
        assert all(skus), f"minted SKUs must be non-empty: {skus}"
        assert len(set(skus)) == 2, f"minted SKUs must be distinct: {skus}"
        # The response sku is the PERSISTED sku (iii).
        for r in res["results"]:
            doc = mongo_db["products"].find_one({"sku": r["sku"]})
            assert doc is not None
            assert doc["product_id"] == r["product_id"]

    def test_blank_sku_base_collision_rejected_with_explicit_message(
        self, mongo_db, patch_db
    ):
        """DECISION (panel): a blank-SKU row whose deterministic base collides
        with an existing product is HARD-REJECTED (never silently
        suffix-minted -- deliberate divergence from the FORM door, which
        suffixes), and the error names the auto-generated SKU + the way out."""
        from api.routers.products import bulk_create_products
        from api.services import product_master as pm
        from database.repositories.product_repository import ProductRepository

        base = pm.build_sku(
            "FRAME",
            {"brand_name": "ClashBrand", "model_no": "CB1", "colour_code": "BLK"},
        )
        ProductRepository(mongo_db["products"]).create(
            {
                "sku": base,
                "category": "FRAME",
                "brand": "ClashBrand",
                "model": "CB1",
                "color": "BLK",
                "mrp": 999.0,
                "offer_price": 999.0,
                "is_active": True,
            }
        )

        rows = [
            {
                # NO sku supplied -- the deterministic base collides.
                "category": "FRAME",
                "brand": "ClashBrand",
                "model": "CB1",
                "color": "BLK",
                "mrp": 1000.0,
                "offer_price": 900.0,
            },
        ]
        res = asyncio.run(bulk_create_products(_body(rows), ADMIN_USER))

        assert res["summary"] == {"total": 1, "created": 0, "failed": 1}
        errors = res["results"][0]["errors"]
        assert any(
            f"auto-generated SKU {base}" in e and "supply an explicit SKU" in e
            for e in errors
        ), errors
        # Nothing new was persisted under the base.
        assert mongo_db["products"].count_documents({"sku": base}) == 1

    def test_response_sku_field_carries_the_minted_value(self, mongo_db, patch_db):
        """A single blank-SKU row: the response `sku` is the minted value, not
        None/blank, and it matches the persisted doc."""
        from api.routers.products import bulk_create_products

        rows = [
            {
                "category": "SUNGLASS",
                "brand": "MintBrand",
                "model": "SG9",
                "color": "GRY",
                "mrp": 2000.0,
                "offer_price": 1800.0,
            },
        ]
        res = asyncio.run(bulk_create_products(_body(rows), ADMIN_USER))

        assert res["summary"]["created"] == 1
        minted = res["results"][0]["sku"]
        assert minted and str(minted).strip()
        doc = mongo_db["products"].find_one({"sku": minted})
        assert doc is not None

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
# Layer 2b -- ENDPOINT tests with NO Mongo (in-memory repo + fake db)
# ============================================================================
# WHY THIS LAYER EXISTS ALONGSIDE Layer 2: the Layer-2 endpoint tests are
# mongo-gated and SKIP on any machine without a mongo:7.0 (every laptop run),
# so the auto-mint endpoint behaviour would be exercised in CI only. The
# blank-SKU contract is the one the reviewers flagged as untested, so it gets a
# layer that runs EVERYWHERE: the real handler, the real canonical door, the
# real ProductRepository -- only the storage underneath is in-memory.
# ============================================================================


class _MemUpsertCollection:
    """MockCollection + the two things the production create path needs that
    MockCollection lacks: `update_one(..., upsert=True)` and being reachable via
    `db.get_collection(name)`."""

    def __init__(self, name: str):
        from database.connection import MockCollection

        self._inner = MockCollection(name)

    def __getattr__(self, item):
        return getattr(self._inner, item)

    def update_one(self, filter, update, upsert: bool = False):  # noqa: A002
        existing = self._inner.find_one(filter)
        if existing is None and upsert:
            doc: Dict[str, Any] = {}
            for key, val in (filter or {}).items():
                if not key.startswith("$") and not isinstance(val, dict):
                    doc[key] = val
            doc.update(dict(update.get("$set") or {}))
            self._inner.insert_one(doc)
            return type("obj", (object,), {"modified_count": 0, "upserted_id": 1})()
        return self._inner.update_one(filter, update)


class _MemDb:
    """get_db() shape over in-memory collections. `is_connected` True so the
    handler takes the REAL repo path, not the stub-mode branch."""

    def __init__(self):
        self.is_connected = True
        self._colls: Dict[str, _MemUpsertCollection] = {}

    def get_collection(self, name: str) -> _MemUpsertCollection:
        if name not in self._colls:
            self._colls[name] = _MemUpsertCollection(name)
        return self._colls[name]

    def __getitem__(self, name: str):
        return self.get_collection(name)

    def __getattr__(self, name: str):
        # db.products / db.catalog_variants attribute style.
        return self.get_collection(name)


@pytest.fixture
def mem_world(monkeypatch):
    """Wire the bulk endpoint + the canonical door onto in-memory storage.

    Returns the (repo, db) pair so a test can seed a pre-existing product and
    read back what actually persisted."""
    from database.repositories.product_repository import ProductRepository
    from database.repositories.audit_repository import AuditRepository
    import api.dependencies as deps
    import api.routers.products as products_router

    db = _MemDb()
    repo = ProductRepository(db.get_collection("products"))
    audit_repo = AuditRepository(db.get_collection("audit_logs"))

    monkeypatch.setattr(deps, "DATABASE_AVAILABLE", True, raising=False)
    monkeypatch.setattr(deps, "get_db", lambda: db)
    monkeypatch.setattr(deps, "get_audit_repository", lambda: audit_repo)
    # The handler AND _create_via_canonical_door both call the name bound in
    # the router module, so one patch covers the endpoint and the door.
    monkeypatch.setattr(products_router, "get_product_repository", lambda: repo)
    return repo, db


class TestBulkCreateEndpointBlankSku:
    """POST /products/bulk-create, blank-SKU rows -- no Mongo required."""

    def test_two_blank_sku_rows_both_created_with_distinct_minted_skus(
        self, mem_world
    ):
        """(i) Two blank-SKU rows with DISTINCT identities are both created and
        the response carries distinct, non-empty MINTED skus."""
        from api.routers.products import bulk_create_products

        repo, _db = mem_world
        rows = [
            {
                "category": "FRAME",
                "brand": "MemBrand",
                "model": "MB1",
                "color": "BLK",
                "mrp": 1000.0,
                "offer_price": 900.0,
            },
            {
                "category": "FRAME",
                "brand": "MemBrand",
                "model": "MB2",
                "color": "BLK",
                "mrp": 1100.0,
                "offer_price": 1000.0,
            },
        ]
        res = asyncio.run(bulk_create_products(_body(rows), ADMIN_USER))

        assert res["summary"] == {"total": 2, "created": 2, "failed": 0}
        skus = [r["sku"] for r in res["results"]]
        assert all(s and str(s).strip() for s in skus), f"minted skus empty: {skus}"
        assert len(set(skus)) == 2, f"minted skus must be distinct: {skus}"
        # (iii) the response sku is the PERSISTED sku.
        for r in res["results"]:
            doc = repo.find_by_sku(r["sku"])
            assert doc is not None, f"{r['sku']} did not persist"
            assert doc.get("product_id") == r["product_id"]

    def test_blank_sku_base_collision_is_rejected_with_the_explicit_message(
        self, mem_world
    ):
        """(ii) DECISION (panel): a blank-SKU row whose deterministic base
        collides with an EXISTING product is HARD-REJECTED -- never silently
        suffix-minted (the deliberate divergence from the FORM door, which
        suffixes) -- and the error names the auto-generated SKU + the way out.
        """
        from api.routers.products import bulk_create_products
        from api.services import product_master as pm

        repo, _db = mem_world
        base = pm.build_sku(
            "FRAME",
            {"brand_name": "MemClash", "model_no": "MC1", "colour_code": "BLK"},
        )
        repo.create(
            {
                "sku": base,
                "category": "FRAME",
                "brand": "MemClash",
                "model": "MC1",
                "color": "BLK",
                "mrp": 999.0,
                "offer_price": 999.0,
                "is_active": True,
            }
        )

        rows = [
            {
                # NO sku supplied -- the deterministic base collides.
                "category": "FRAME",
                "brand": "MemClash",
                "model": "MC1",
                "color": "BLK",
                "mrp": 1000.0,
                "offer_price": 900.0,
            }
        ]
        res = asyncio.run(bulk_create_products(_body(rows), ADMIN_USER))

        assert res["summary"] == {"total": 1, "created": 0, "failed": 1}
        errors = res["results"][0]["errors"]
        assert any(
            f"auto-generated SKU {base}" in e and "supply an explicit SKU" in e
            for e in errors
        ), errors
        # Not suffix-minted behind our back: still exactly one row on the base,
        # and no "<base>-<counter>" sibling was created.
        assert repo.find_by_sku(base) is not None
        all_skus = sorted(
            str(d.get("sku") or "")
            for d in _db.get_collection("products")._inner._data.values()
        )
        assert all_skus == [base], all_skus

    def test_supplied_sku_collision_keeps_the_original_message(self, mem_world):
        """The SUPPLIED-sku collision message is unchanged -- the new wording is
        scoped to auto-generated SKUs only."""
        from api.routers.products import bulk_create_products

        repo, _db = mem_world
        repo.create(
            {
                "sku": "MEM-TAKEN",
                "category": "FRAME",
                "brand": "B",
                "model": "M",
                "color": "BLK",
                "mrp": 999.0,
                "offer_price": 999.0,
                "is_active": True,
            }
        )
        rows = [
            {
                "sku": "MEM-TAKEN",
                "category": "FRAME",
                "brand": "B",
                "model": "M",
                "color": "BLK",
                "mrp": 1000.0,
                "offer_price": 900.0,
            }
        ]
        res = asyncio.run(bulk_create_products(_body(rows), ADMIN_USER))

        assert res["summary"]["failed"] == 1
        assert "Product with this SKU already exists" in res["results"][0]["errors"]


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
