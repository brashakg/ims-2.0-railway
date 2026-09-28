"""
IMS 2.0 -- product barcode validation + uniqueness (PUT /products/{id})
======================================================================
Owner ruling 2026-09-28: the product-level barcode holds ONLY the
manufacturer's UPC / EAN (GTIN) -- it is what goes to Shopify and Google. Our
own IMS barcodes live on each unit (stock_units.barcode), never here. Two
guards back that:

  - format: anything that is not a publishable GTIN (services/gtin.py) is
    rejected (HTTP 400): wrong check digit, wrong length, and our own GS1
    20-29 in-store range, which is by definition not a manufacturer code.
  - uniqueness: a barcode already on a DIFFERENT product is rejected (HTTP 409).
    The DB unique sparse index on products.barcode is the backstop; this check
    gives a clear message before the write.

Layer 1 is pure (no DB) and exercises the validator directly.
Layer 2 drives the real router functions against a throwaway mongo:7.0 db,
mirroring tests/test_bulk_create.py; it skips fail-soft when no mongo is present.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, List

import pytest

from fastapi import HTTPException

# Real manufacturer GTINs (valid GS1 check digits).
_VALID_A = "4006381333931"  # EAN-13
_VALID_B = "5901234123457"  # EAN-13
_UPC_A = "036000291452"  # UPC-A, 12 digits
_EAN_8 = "96385074"  # GTIN-8
# Same payload as _VALID_A but a deliberately WRONG check digit.
_BAD_CHECK = "4006381333932"
_TOO_SHORT = "40063813339"  # 11 digits: no GTIN has that length
# Our own in-store range (GS1 20-29): a valid EAN-13, never a manufacturer code.
_INTERNAL = "2000000000015"
# What Inventory > Manage Barcode > Generate produced in the audit (F115).
_RANDOM_GENERATED = "930713281508"


# ============================================================================
# Layer 1 -- pure validator (no DB)
# ============================================================================


class _FakeRepo:
    """Minimal repo exposing find_one over an in-memory product list."""

    def __init__(self, products: List[Dict[str, Any]]):
        self._products = products

    def find_one(self, flt: Dict[str, Any]):
        for p in self._products:
            if all(p.get(k) == v for k, v in flt.items()):
                return p
        return None


class TestBarcodeValidatorPure:
    def test_valid_ean13_passes(self):
        from api.routers.products import _validate_product_barcode_or_400

        # No repo clash -> no exception.
        _validate_product_barcode_or_400(_VALID_A, _FakeRepo([]), "p1")

    def test_blank_and_none_are_skipped(self):
        from api.routers.products import _validate_product_barcode_or_400

        # Clearing the barcode (None / "" / whitespace) is allowed -- no raise.
        _validate_product_barcode_or_400(None, _FakeRepo([]), "p1")
        _validate_product_barcode_or_400("", _FakeRepo([]), "p1")
        _validate_product_barcode_or_400("   ", _FakeRepo([]), "p1")

    def test_wrong_check_digit_rejected_400(self):
        from api.routers.products import _validate_product_barcode_or_400

        with pytest.raises(HTTPException) as ei:
            _validate_product_barcode_or_400(_BAD_CHECK, _FakeRepo([]), "p1")
        assert ei.value.status_code == 400

    def test_too_short_rejected_400(self):
        from api.routers.products import _validate_product_barcode_or_400

        with pytest.raises(HTTPException) as ei:
            _validate_product_barcode_or_400(_TOO_SHORT, _FakeRepo([]), "p1")
        assert ei.value.status_code == 400

    def test_manufacturer_upc_and_ean8_accepted(self):
        from api.routers.products import _validate_product_barcode_or_400

        _validate_product_barcode_or_400(_UPC_A, _FakeRepo([]), "p1")
        _validate_product_barcode_or_400(_EAN_8, _FakeRepo([]), "p1")

    def test_our_internal_20_prefix_code_is_refused(self):
        from api.routers.products import _validate_product_barcode_or_400

        with pytest.raises(HTTPException) as ei:
            _validate_product_barcode_or_400(_INTERNAL, _FakeRepo([]), "p1")
        assert ei.value.status_code == 400

    def test_a_random_generated_number_is_refused(self):
        from api.routers.products import _validate_product_barcode_or_400

        with pytest.raises(HTTPException) as ei:
            _validate_product_barcode_or_400(_RANDOM_GENERATED, _FakeRepo([]), "p1")
        assert ei.value.status_code == 400

    def test_separators_are_dropped_so_one_gtin_is_one_value(self):
        from api.routers.products import _validate_product_barcode_or_400

        # '4006381 333931' is the same GTIN as _VALID_A: stored bare, and it
        # clashes with the product that already carries it.
        assert (
            _validate_product_barcode_or_400("4006381 333931", _FakeRepo([]), "p1")
            == _VALID_A
        )
        repo = _FakeRepo([{"product_id": "OTHER", "sku": "SKU-X", "barcode": _VALID_A}])
        with pytest.raises(HTTPException) as ei:
            _validate_product_barcode_or_400("4006381-333931", repo, "p1")
        assert ei.value.status_code == 409

    def test_duplicate_on_other_product_rejected_409(self):
        from api.routers.products import _validate_product_barcode_or_400

        repo = _FakeRepo([{"product_id": "OTHER", "sku": "SKU-X", "barcode": _VALID_A}])
        with pytest.raises(HTTPException) as ei:
            _validate_product_barcode_or_400(_VALID_A, repo, "p1")
        assert ei.value.status_code == 409

    def test_same_barcode_on_same_product_allowed(self):
        from api.routers.products import _validate_product_barcode_or_400

        # Re-saving the SAME product's existing barcode must NOT clash (idempotent).
        repo = _FakeRepo([{"product_id": "p1", "sku": "SKU-1", "barcode": _VALID_A}])
        _validate_product_barcode_or_400(_VALID_A, repo, "p1")


# ============================================================================
# Layer 2 -- real router functions against a throwaway mongo:7.0
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

    db_name = f"ims_test_barcode_{uuid.uuid4().hex[:8]}"
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
    """Point both get_db() entrypoints at the test mongo db. Wipes products
    before each test so rows don't leak."""
    try:
        mongo_db["products"].delete_many({})
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


_ADMIN = {
    "user_id": "test-admin-barcode",
    "username": "barcodeadmin",
    "roles": ["SUPERADMIN"],
    "active_store_id": "BV-TEST-01",
}


def _create(sku: str):
    from api.routers.products import create_product, ProductCreate

    body = ProductCreate(
        sku=sku,
        category="FRAME",
        brand="B",
        # Distinct model per SKU so each product has a UNIQUE brand+model+colour
        # identity. The Hub Phase-1 duplicate guard 409s two products that share an
        # identity, so a fixed model would block the second create in the
        # barcode-dup test before it could exercise the barcode path. These tests
        # isolate barcode behaviour, so they use distinct identities by design.
        model=f"M-{sku}",
        color="Black",  # FRAME requires colour_code under the step-9 strict gate
        mrp=1000.0,
        offer_price=900.0,
    )
    return asyncio.run(create_product(body, _ADMIN))


def _update(product_id: str, **fields):
    from api.routers.products import update_product, ProductUpdate

    return asyncio.run(update_product(product_id, ProductUpdate(**fields), _ADMIN))


class TestBarcodeUpdateEndpoint:
    def test_valid_barcode_persists(self, mongo_db, patch_db):
        pid = _create("BC-OK")["product_id"]
        res = _update(pid, barcode=_VALID_A)
        assert res["product_id"] == pid
        saved = mongo_db["products"].find_one({"product_id": pid})
        assert saved["barcode"] == _VALID_A

    def test_gtin_is_stored_without_separators(self, mongo_db, patch_db):
        """One GTIN is one value: '4006381 333931' typed from the box is saved
        as 4006381333931, so the uniqueness check and the Shopify push see it."""
        pid = _create("BC-SEP")["product_id"]
        _update(pid, barcode="4006381 333931")
        saved = mongo_db["products"].find_one({"product_id": pid})
        assert saved["barcode"] == _VALID_A

    def test_malformed_barcode_rejected_400(self, mongo_db, patch_db):
        pid = _create("BC-BAD")["product_id"]
        with pytest.raises(HTTPException) as ei:
            _update(pid, barcode=_BAD_CHECK)
        assert ei.value.status_code == 400
        # Nothing was written.
        saved = mongo_db["products"].find_one({"product_id": pid})
        assert saved.get("barcode") in (None, "")

    def test_duplicate_barcode_rejected_409(self, mongo_db, patch_db):
        p1 = _create("BC-1")["product_id"]
        p2 = _create("BC-2")["product_id"]
        _update(p1, barcode=_VALID_A)  # claims _VALID_A
        with pytest.raises(HTTPException) as ei:
            _update(p2, barcode=_VALID_A)  # p2 cannot reuse it
        assert ei.value.status_code == 409
        # p2 stayed barcode-less.
        saved2 = mongo_db["products"].find_one({"product_id": p2})
        assert saved2.get("barcode") in (None, "")

    def test_resave_same_barcode_same_product_ok(self, mongo_db, patch_db):
        pid = _create("BC-IDEM")["product_id"]
        _update(pid, barcode=_VALID_A)
        # Re-saving the same product's own barcode (e.g. editing another field)
        # must not 409 against itself.
        res = _update(pid, barcode=_VALID_A, brand="NewBrand")
        assert res["product_id"] == pid
