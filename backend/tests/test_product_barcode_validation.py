"""
IMS 2.0 -- product barcode validation + uniqueness (PUT /products/{id})
======================================================================
Owner ruling 2026-09-28: the product-level barcode holds ONLY the
manufacturer's UPC / EAN (GTIN). Our own IMS barcodes live on each unit
(stock_units.barcode), never here. (The GTIN that goes to Shopify and Google is
the `gtin` attribute -- see TestGtinAttributeOnTheEditDoor at the bottom.) Two
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

from database.repositories.product_repository import ProductRepository

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


def _FakeRepo(products: List[Dict[str, Any]]) -> ProductRepository:
    """The REAL ProductRepository over an in-memory mongomock collection holding
    `products`, so the real find_by_barcode query runs."""
    import mongomock

    coll = mongomock.MongoClient().db.products
    for p in products:
        coll.insert_one(dict(p))
    return ProductRepository(coll)


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

    @pytest.mark.parametrize(
        "held, typed",
        [
            (_UPC_A, "0" + _UPC_A),  # UPC-A held, its 13-digit form typed
            ("0" + _UPC_A, _UPC_A),
            (_UPC_A, "00" + _UPC_A),  # the GTIN-14 form
            (_EAN_8, "000000" + _EAN_8),
        ],
    )
    def test_one_gtin_however_padded_is_one_barcode(self, held, typed):
        """GS1 reads a GTIN right-aligned in 14 digits: 036000291452 and
        0036000291452 are one code, so the second product is refused."""
        from api.routers.products import _validate_product_barcode_or_400

        for field in ({"barcode": held}, {"attributes": {"gtin": held}}):
            repo = _FakeRepo([{"product_id": "OTHER", "sku": "SKU-X", **field}])
            with pytest.raises(HTTPException) as ei:
                _validate_product_barcode_or_400(typed, repo, "p1")
            assert ei.value.status_code == 409, (field, typed)

    def test_different_gtins_never_clash(self):
        from api.routers.products import _validate_product_barcode_or_400

        repo = _FakeRepo([{"product_id": "OTHER", "sku": "SKU-X", "barcode": _EAN_8}])
        _validate_product_barcode_or_400(_VALID_A, repo, "p1")
        _validate_product_barcode_or_400(_UPC_A, repo, "p1")
        # Ends in the held EAN-8 but is a different 13-digit GTIN: only
        # leading ZEROS are padding.
        _validate_product_barcode_or_400("4000696385074", repo, "p1")

    def test_a_code_held_as_another_products_gtin_is_rejected_409(self):
        from api.routers.products import _validate_product_barcode_or_400

        repo = _FakeRepo(
            [{"product_id": "OTHER", "sku": "SKU-X", "attributes": {"gtin": _VALID_A}}]
        )
        with pytest.raises(HTTPException) as ei:
            _validate_product_barcode_or_400(_VALID_A, repo, "p1")
        assert ei.value.status_code == 409


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
    return _point_get_db_at(mongo_db, monkeypatch)


@pytest.fixture
def mock_db(monkeypatch):
    """A fresh in-memory mongomock db behind get_db(): these tests run
    everywhere, with or without a mongo server."""
    import mongomock

    db = mongomock.MongoClient().db
    _point_get_db_at(db, monkeypatch)
    return db


def _point_get_db_at(mongo_db, monkeypatch):
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


# ============================================================================
# The GTIN attribute -- the manufacturer barcode that actually goes out
# ============================================================================
# Manage Barcode edits attributes.gtin, the one field the Add-Product form's
# "GTIN (mfr)" box writes and the Shopify push reads (through the catalog twin's
# `gtin`). products.barcode never reached the twin, so a code saved there was
# never sent anywhere.


class TestGtinAttributeOnTheEditDoor:
    @pytest.mark.parametrize(
        "junk", [_INTERNAL, "TW003HG14", _BAD_CHECK, _RANDOM_GENERATED]
    )
    def test_edit_door_refuses_a_junk_gtin(self, mock_db, junk):
        pid = _create("GT-JUNK")["product_id"]
        with pytest.raises(HTTPException) as ei:
            _update(pid, attributes={"gtin": junk})
        assert ei.value.status_code == 422
        assert "not a valid GTIN" in str(ei.value.detail)
        saved = mock_db["products"].find_one({"product_id": pid})
        assert "gtin" not in (saved.get("attributes") or {})

    def test_edit_door_refuses_a_junk_upc(self, mock_db):
        """The 'UPC (mfr)' attribute is a manufacturer barcode too."""
        pid = _create("GT-UPC")["product_id"]
        with pytest.raises(HTTPException) as ei:
            _update(pid, attributes={"upc": _INTERNAL})
        assert ei.value.status_code == 422
        saved = mock_db["products"].find_one({"product_id": pid})
        assert "upc" not in (saved.get("attributes") or {})
        _update(pid, attributes={"upc": _UPC_A})

    def test_a_saved_gtin_reaches_the_shopify_push(self, mock_db):
        from api.services.shopify_push.product_input import (
            _variants_for_price_push,
            build_variant_price_inputs,
        )

        created = _create("GT-OK")
        pid = created["product_id"]
        _update(pid, attributes={"gtin": _VALID_A})

        spine = mock_db["products"].find_one({"product_id": pid})
        assert spine["attributes"]["gtin"] == _VALID_A
        twin_id = spine.get("pim_product_id") or pid
        twin = mock_db["catalog_products"].find_one({"id": twin_id})
        assert twin is not None, "the create door makes the catalog twin"
        assert twin["gtin"] == _VALID_A
        assert (twin.get("ecom") or {}).get("locally_modified") is True
        # Once the product is on Shopify, the push sends it as the barcode.
        twin.setdefault("ecom", {})["shopify_variant_id"] = "gid://shopify/ProductVariant/1"
        rows, _ = build_variant_price_inputs(twin, _variants_for_price_push(twin, []))
        assert rows and rows[0].get("barcode") == _VALID_A

    def test_clearing_the_gtin_is_allowed(self, mock_db):
        pid = _create("GT-CLR")["product_id"]
        _update(pid, attributes={"gtin": _VALID_A})
        _update(pid, attributes={"gtin": ""})
        saved = mock_db["products"].find_one({"product_id": pid})
        assert not saved["attributes"].get("gtin")

    def test_remove_barcode_clears_every_barcode_the_push_reads(self, mock_db):
        """Manage Barcode > Remove (attributes.gtin = '') cleared only the twin's
        gtin; the push falls back to the twin's legacy top-level `barcode`, so
        it went on sending that code."""
        from api.services.shopify_push.product_input import (
            _variants_for_price_push,
            build_variant_price_inputs,
            build_removed_metafields,
        )

        pid = _create("GT-RM")["product_id"]
        spine = mock_db["products"].find_one({"product_id": pid})
        twin_id = spine.get("pim_product_id") or pid
        mock_db["catalog_products"].update_one(
            {"id": twin_id},
            {"$set": {"barcode": _VALID_B,
                      "ecom.shopify_variant_id": "gid://shopify/ProductVariant/1"}},
        )
        _update(pid, attributes={"gtin": _VALID_A})
        _update(pid, attributes={"gtin": ""})
        twin = mock_db["catalog_products"].find_one({"id": twin_id})
        assert not twin.get("gtin") and not twin.get("barcode")
        rows, _ = build_variant_price_inputs(twin, _variants_for_price_push(twin, []))
        assert "barcode" not in rows[0]
        # ...and the ims.gtin metafield is deleted on the next push.
        assert build_removed_metafields(twin) == [{"namespace": "ims", "key": "gtin"}]

    def test_remove_on_a_size_variant_clears_its_row_barcode(self, mock_db):
        """A size variant's barcode reaches Shopify from its catalog_variants
        row (gtin, then barcode): Remove clears both."""
        from api.services.product_master import _mirror_variant_row_update

        mock_db["catalog_variants"].insert_one(
            {"sku": "CH-1", "gtin": _VALID_A, "barcode": _VALID_B}
        )
        _mirror_variant_row_update(
            current={"sku": "CH-1"}, patch={"attributes": {"gtin": ""}},
            db=mock_db, mark_dirty=False,
        )
        row = mock_db["catalog_variants"].find_one({"sku": "CH-1"})
        assert row["gtin"] is None and row["barcode"] is None

    def test_stock_row_offers_the_gtin_not_the_unit_code(self):
        """Manage Barcode opens pre-filled from the row's `gtin`. It used to be
        pre-filled with the row's `barcode` -- a unit's IMS code such as
        BV0000000042 -- which the server then refused as not a GTIN."""
        from api.routers.inventory.stock import _ledger_row

        row = _ledger_row(
            {"product_id": "P1", "attributes": {"gtin": _VALID_A}},
            1,
            0,
            {"barcode": "BV0000000042"},
            "BV-TEST-01",
        )
        assert row["gtin"] == _VALID_A
        assert row["barcode"] == "BV0000000042"

    def test_a_gtin_already_on_another_product_is_refused_409(self, mock_db):
        """Manage Barcode moved from products.barcode (409 on a duplicate) to
        attributes.gtin, which only checked the format: two products could both
        hold 4006381333931 and both went to Shopify/Google with it. A GTIN names
        ONE manufacturer item, however it is typed."""
        p1 = _create("GT-DUP-1")["product_id"]
        p2 = _create("GT-DUP-2")["product_id"]
        _update(p1, attributes={"gtin": _VALID_A})
        for typed in (_VALID_A, "4006381 333931"):
            with pytest.raises(HTTPException) as ei:
                _update(p2, attributes={"gtin": typed})
            assert ei.value.status_code == 409, typed
            assert "GT-DUP-1" in str(ei.value.detail)
        spine2 = mock_db["products"].find_one({"product_id": p2})
        assert not (spine2.get("attributes") or {}).get("gtin")
        # Re-saving a product's own GTIN (editing another field) is no clash.
        _update(p1, attributes={"gtin": _VALID_A, "frame_material": "Acetate"})

    def test_both_manufacturer_barcode_fields_share_one_uniqueness_rule(self, mock_db):
        """products.barcode and attributes.gtin hold the same kind of code: a
        code held in either field on one product is refused in either field on
        another."""
        p1 = _create("GT-X-1")["product_id"]
        p2 = _create("GT-X-2")["product_id"]
        _update(p1, barcode=_VALID_B)
        with pytest.raises(HTTPException) as ei:
            _update(p2, attributes={"gtin": _VALID_B})
        assert ei.value.status_code == 409
        _update(p2, attributes={"gtin": _VALID_A})
        with pytest.raises(HTTPException) as ei:
            _update(p1, barcode=_VALID_A)
        assert ei.value.status_code == 409

    def test_a_spaced_gtin_is_stored_bare_on_spine_and_twin(self, mock_db):
        """'4006381 333931' from the box is one GTIN: stored bare on the product
        AND its catalog twin, so the Inventory row, exact-match lookups and the
        uniqueness check all see 4006381333931."""
        pid = _create("GT-SP")["product_id"]
        _update(pid, attributes={"gtin": "4006381 333931"})
        spine = mock_db["products"].find_one({"product_id": pid})
        assert spine["attributes"]["gtin"] == _VALID_A
        twin = mock_db["catalog_products"].find_one(
            {"id": spine.get("pim_product_id") or pid}
        )
        assert twin["gtin"] == _VALID_A


class TestGtinAttributeOnTheCreateDoor:
    def test_create_refuses_a_gtin_another_product_holds(self, mock_db):
        """Uniqueness ran on the edit door only: POST /products with the GTIN
        of an existing product saved a second holder, and both went to
        Shopify/Google with it."""
        from api.routers.products import create_product, ProductCreate

        p1 = _create("GT-C-1")["product_id"]
        _update(p1, attributes={"gtin": _UPC_A})
        for typed in (_UPC_A, "0" + _UPC_A):
            body = ProductCreate(
                sku=f"GT-C-2-{len(typed)}", category="FRAME", brand="B",
                model=f"M-C2-{len(typed)}", color="Black", mrp=1000.0,
                offer_price=900.0, attributes={"gtin": typed},
            )
            with pytest.raises(HTTPException) as ei:
                asyncio.run(create_product(body, _ADMIN))
            assert ei.value.status_code == 409, typed
            assert "GT-C-1" in str(ei.value.detail), typed
        holders = mock_db["products"].count_documents(
            {"attributes.gtin": {"$in": [_UPC_A, "0" + _UPC_A]}}
        )
        assert holders == 1

    def test_create_takes_a_gtin_nobody_holds(self, mock_db):
        from api.routers.products import create_product, ProductCreate

        body = ProductCreate(
            sku="GT-C-OK", category="FRAME", brand="B", model="M-C-OK",
            color="Black", mrp=1000.0, offer_price=900.0,
            attributes={"gtin": _VALID_B},
        )
        pid = asyncio.run(create_product(body, _ADMIN))["product_id"]
        spine = mock_db["products"].find_one({"product_id": pid})
        assert spine["attributes"]["gtin"] == _VALID_B
