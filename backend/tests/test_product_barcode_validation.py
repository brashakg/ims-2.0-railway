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


def _FakeRepo(
    products: List[Dict[str, Any]], twins: List[Dict[str, Any]] = ()
) -> ProductRepository:
    """The REAL ProductRepository over an in-memory mongomock collection holding
    `products` (and `twins` in the same db's catalog_products), so the real
    find_by_barcode / find_twin_by_barcode queries run."""
    import mongomock

    coll = mongomock.MongoClient().db.products
    for p in products:
        coll.insert_one(dict(p))
    for t in twins:
        coll.database["catalog_products"].insert_one(dict(t))
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


    @pytest.mark.parametrize(
        "own, other, typed",
        [
            ({"barcode": _VALID_A}, {"attributes": {"gtin": _VALID_A}}, _VALID_A),
            ({"attributes": {"gtin": _UPC_A}}, {"attributes": {"gtin": "0" + _UPC_A}}, _UPC_A),
        ],
    )
    def test_the_edited_products_own_code_never_hides_another_holder(
        self, own, other, typed
    ):
        """find_one returned ONE holder: when it was the product being edited
        the check passed, and a second holder went unseen (Manage Barcode
        re-saving A's legacy barcode as its gtin while B held it). The verdict
        no longer depends on which document Mongo returns first."""
        from api.routers.products import _validate_product_barcode_or_400

        repo = _FakeRepo(
            [
                {"product_id": "A", "sku": "SKU-A", **own},
                {"product_id": "B", "sku": "SKU-B", **other},
            ]
        )
        for this, holder in (("A", "SKU-B"), ("B", "SKU-A")):
            with pytest.raises(HTTPException) as ei:
                _validate_product_barcode_or_400(typed, repo, this)
            assert ei.value.status_code == 409 and holder in str(ei.value.detail)

    @pytest.mark.parametrize(
        "held", [{"attributes": {"gtin": _VALID_A}}, {"gtin": "0" + _VALID_A}]
    )
    def test_a_catalogue_twin_with_no_spine_holds_its_gtin(self, held):
        """POST /catalog/products/import writes only a catalog_products twin,
        and the push sends a twin's gtin as its variant barcode: the one-holder
        rule reads twins too, in every spelling."""
        from api.routers.products import _validate_product_barcode_or_400

        repo = _FakeRepo([], twins=[{"id": "imp-1", "sku": "IMP-1", **held}])
        for this in (None, "p1"):
            with pytest.raises(HTTPException) as ei:
                _validate_product_barcode_or_400(_VALID_A, repo, this)
            assert ei.value.status_code == 409 and "IMP-1" in str(ei.value.detail)
        # The twin itself (the review editor on a spineless twin) is no clash.
        _validate_product_barcode_or_400(_VALID_A, repo, "imp-1")

    def test_a_products_own_twin_is_not_a_clash(self):
        """A spine's twin carries the same gtin; it is keyed on the spine's
        pim_product_id (door-created), its product_id (legacy) or its sku."""
        from api.routers.products import _validate_product_barcode_or_400

        repo = _FakeRepo(
            [
                {"product_id": "p1", "sku": "S1", "pim_product_id": "pim-1",
                 "attributes": {"gtin": _VALID_A}},
                {"product_id": "p2", "attributes": {"gtin": _VALID_B}},
                {"product_id": "p3", "sku": "S3", "attributes": {"gtin": _UPC_A}},
            ],
            twins=[
                {"id": "pim-1", "sku": "S1", "gtin": _VALID_A},
                {"id": "p2", "gtin": _VALID_B},
                {"id": "bvi-3", "sku": "S3", "attributes": {"gtin": _UPC_A}},
            ],
        )
        _validate_product_barcode_or_400(_VALID_A, repo, "p1")
        _validate_product_barcode_or_400(_VALID_B, repo, "p2")
        _validate_product_barcode_or_400(_UPC_A, repo, "p3")
        # ... while another product's twin still clashes.
        repo.collection.database["catalog_products"].insert_one(
            {"id": "imp-9", "sku": "IMP-9", "gtin": _EAN_8}
        )
        with pytest.raises(HTTPException):
            _validate_product_barcode_or_400(_EAN_8, repo, "p1")


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


@pytest.fixture
def mirror_on(monkeypatch):
    """The production default (pm.mirror_enabled ON): the create door writes the
    catalog_variants row. Pinned, because another module's import-time
    os.environ.setdefault can have switched it off for the whole run."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "1")


def _pushed_barcodes(db, twin):
    """What the price push sends for `twin` over the catalog_variants rows the
    create door REALLY wrote (variant_rows_for_product, as push_product loads
    them), once each row is on Shopify: {sku: barcode or None (omitted)}."""
    from api.services.online_catalog import variant_rows_for_product
    from api.services.shopify_push.product_input import (
        _variants_for_price_push,
        build_variant_price_inputs,
    )

    rows = variant_rows_for_product(db, twin)
    assert rows, "the create door writes a catalog_variants row"
    for r in rows:  # the first-publish seed writes each variant gid back
        r["shopify_variant_id"] = "gid://shopify/ProductVariant/" + r["sku"]
    out, _ = build_variant_price_inputs(twin, _variants_for_price_push(twin, rows))
    return {o["id"].rsplit("/", 1)[-1]: o.get("barcode") for o in out}


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

    def test_a_saved_gtin_reaches_the_shopify_push(self, mock_db, mirror_on):
        from api.services.online_catalog import variant_rows_for_product

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
        # The door's SELF row holds no copy of the GTIN to keep in sync ...
        rows = variant_rows_for_product(mock_db, twin)
        assert [r["sku"] for r in rows] == ["GT-OK"] and not rows[0].get("gtin")
        # ... and once the product is on Shopify the push sends the product's.
        assert _pushed_barcodes(mock_db, twin) == {"GT-OK": _VALID_A}

    def test_a_size_variant_keeps_its_own_gtin_on_the_parents_push(self, mock_db, mirror_on):
        """The parent's push carries every row the create door wrote under
        it: its self row ships the PARENT's GTIN, the size variant's row its
        own -- never the parent's (a GTIN names one trade item)."""
        from api.routers.products import create_product, ProductCreate
        from api.services import product_master as pm
        from database.repositories.catalog_variant_repository import (
            CatalogVariantRepository,
        )

        body = ProductCreate(
            sku="GT-PAR", category="FRAME", brand="B", model="M-GT-PAR",
            color="Black", mrp=1000.0, offer_price=900.0,
            attributes={"gtin": _VALID_A},
        )
        parent_id = asyncio.run(create_product(body, _ADMIN))["product_id"]
        pm.create_via_door(
            {
                "category": "FRAME",
                "sku": "GT-PAR-L",
                "attributes": {
                    "brand_name": "B", "model_no": "M-GT-PAR",
                    "colour_code": "Black", "size": "Large", "gtin": _VALID_B,
                },
                "mrp": 1100.0,
                "offer_price": 1100.0,
                "variant_of": parent_id,
            },
            source="MASTER",
            actor="u-admin",
            product_repo=ProductRepository(mock_db["products"]),
            variant_repo=CatalogVariantRepository(mock_db["catalog_variants"]),
            db=mock_db,
        )
        spine = mock_db["products"].find_one({"product_id": parent_id})
        twin = mock_db["catalog_products"].find_one({"id": spine["pim_product_id"]})
        assert _pushed_barcodes(mock_db, twin) == {
            "GT-PAR": _VALID_A,
            "GT-PAR-L": _VALID_B,
        }

    def test_clearing_the_gtin_is_allowed(self, mock_db):
        pid = _create("GT-CLR")["product_id"]
        _update(pid, attributes={"gtin": _VALID_A})
        _update(pid, attributes={"gtin": ""})
        saved = mock_db["products"].find_one({"product_id": pid})
        assert not saved["attributes"].get("gtin")

    def test_remove_barcode_clears_every_barcode_the_push_reads(self, mock_db, mirror_on):
        """Manage Barcode > Remove (attributes.gtin = '') cleared only the twin's
        gtin; the push falls back to the twin's legacy top-level `barcode`, so
        it went on sending that code."""
        from api.services.shopify_push.product_input import build_removed_metafields

        pid = _create("GT-RM")["product_id"]
        spine = mock_db["products"].find_one({"product_id": pid})
        twin_id = spine.get("pim_product_id") or pid
        mock_db["catalog_products"].update_one(
            {"id": twin_id},
            {"$set": {"barcode": _VALID_B}},
        )
        _update(pid, attributes={"gtin": _VALID_A})
        _update(pid, attributes={"gtin": ""})
        twin = mock_db["catalog_products"].find_one({"id": twin_id})
        assert not twin.get("gtin") and not twin.get("barcode")
        assert _pushed_barcodes(mock_db, twin) == {"GT-RM": None}
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
        BV0000000042 -- which the server then refused as not a GTIN. Since
        #1164 (F27) the row's `barcode` is the product's own, never a unit's."""
        from api.routers.inventory.stock import _ledger_row

        row = _ledger_row(
            {"product_id": "P1", "attributes": {"gtin": _VALID_A}},
            1,
            0,
            {"barcode": "BV0000000042"},
            "BV-TEST-01",
        )
        assert row["gtin"] == _VALID_A
        assert row["barcode"] == ""

    @pytest.mark.parametrize("attrs", [["FRAME"], "FRAME", 7])
    def test_a_product_whose_attributes_are_not_a_dict_keeps_the_ledger_up(
        self, attrs
    ):
        """The product list is chain-wide and has no try around each row: one
        legacy product with attributes=["FRAME"] raised AttributeError and
        took Inventory > Stock down (500) at every shop."""
        from api.routers.inventory.stock import _ledger_row

        product = {"product_id": "P1", "attributes": attrs, "barcode": _VALID_A}
        row = _ledger_row(product, 1, 0, {}, "BV-TEST-01")
        assert (row["gtin"], row["unverified_barcode"]) == ("", _VALID_A)
        product.pop("barcode")
        row = _ledger_row(product, 1, 0, {}, "BV-TEST-01")
        assert (row["gtin"], row["unverified_barcode"]) == ("", "")

    @pytest.mark.parametrize("new_gtin", ["", _VALID_B])
    def test_a_legacy_product_barcode_shows_and_moves_off_with_the_gtin(
        self, mock_db, new_gtin
    ):
        """Main's old Manage Barcode wrote products.barcode. The modal now edits
        the gtin attribute only, so that code showed as 'Not set', Remove left
        it in place, and the one-holder rule still refused it on the right
        frame (409) with no screen able to clear it."""
        from api.routers.inventory.stock import _ledger_row

        p1 = _create("PR-L-1")["product_id"]
        p2 = _create("PR-L-2")["product_id"]
        mock_db["products"].update_one(
            {"product_id": p1}, {"$set": {"barcode": _VALID_A}}
        )
        spine = mock_db["products"].find_one({"product_id": p1})
        assert _ledger_row(spine, 1, 0, {}, "BV-TEST-01")["unverified_barcode"] == _VALID_A
        _update(p1, attributes={"gtin": new_gtin})  # Remove old code, or a new code
        assert "barcode" not in mock_db["products"].find_one({"product_id": p1})
        _update(p2, attributes={"gtin": _VALID_A})  # the box EAN moves frames
        spine2 = mock_db["products"].find_one({"product_id": p2})
        assert spine2["attributes"]["gtin"] == _VALID_A

    def test_a_legacy_generated_code_never_becomes_the_shopify_barcode(
        self, mock_db, mirror_on
    ):
        """Main's old Manage Barcode > Generate wrote random EAN-13s to
        products.barcode: 12 random digits + a correct check digit, so most
        pass the format check. The stock row offered that code as the saved
        GTIN, and Save Barcode on the untouched box sent it to Shopify as the
        maker's barcode. It is now an UNVERIFIED value apart from the gtin: the
        box opens empty, and what the box holds pushes no barcode."""
        from api.routers.inventory.stock import _ledger_row

        generated = "5260181590836"
        pid = _create("PRB-LEG")["product_id"]
        mock_db["products"].update_one(
            {"product_id": pid}, {"$set": {"barcode": generated}}
        )
        spine = mock_db["products"].find_one({"product_id": pid})
        row = _ledger_row(spine, 1, 0, {}, "BV-TEST-01")
        assert row["unverified_barcode"] == generated
        _update(pid, attributes={"gtin": row["gtin"]})  # the box, untouched
        twin = mock_db["catalog_products"].find_one(
            {"id": spine.get("pim_product_id") or pid}
        )
        assert _pushed_barcodes(mock_db, twin) == {"PRB-LEG": None}

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
        # The check reads the FOLDED key: a 'GTIN' / 'Gtin' key is the same
        # barcode once the door folds it onto 'gtin'.
        sends = [("gtin", _UPC_A), ("gtin", "0" + _UPC_A), ("GTIN", _UPC_A), ("Gtin", "0" + _UPC_A)]
        for n, (key, typed) in enumerate(sends):
            body = ProductCreate(
                sku=f"GT-C-2-{n}", category="FRAME", brand="B",
                model=f"M-C2-{n}", color="Black", mrp=1000.0,
                offer_price=900.0, attributes={key: typed},
            )
            with pytest.raises(HTTPException) as ei:
                asyncio.run(create_product(body, _ADMIN))
            assert ei.value.status_code == 409, (key, typed)
            assert "GT-C-1" in str(ei.value.detail), (key, typed)
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


class TestSearchFindsTheMakersCode:
    def test_a_code_saved_by_manage_barcode_is_found_by_search(self, mock_db):
        """Manage Barcode saves attributes.gtin (and drops the legacy
        products.barcode); search read only `barcode`, so the maker's code
        stopped finding its product (command palette, Returns, Quick Add's
        clone-by-barcode)."""
        from api.routers.admin_catalog import list_products

        pid = _create("SR-1")["product_id"]
        mock_db["products"].update_one(
            {"product_id": pid}, {"$set": {"barcode": _VALID_A}}
        )
        _update(pid, attributes={"gtin": _VALID_A})
        assert "barcode" not in mock_db["products"].find_one({"product_id": pid})
        repo = ProductRepository(mock_db["products"])
        assert [p["product_id"] for p in repo.search_products(_VALID_A)] == [pid]
        assert repo.count_search_products(_VALID_A) == 1
        found = asyncio.run(list_products(search=_VALID_A))["products"]
        assert [p["product_id"] for p in found] == [pid]


# The GTIN is stored SANITISED at every write door (digits only), so a scan --
# an exact match -- finds a code typed with spaces or hyphens.
_SPACED = ("8 056597 720373", "805-6597-72037-3")
_EAN_SANITISED = "8056597720373"


class TestEveryDoorStoresTheGtinDigitsOnly:
    @pytest.mark.parametrize("typed", _SPACED)
    def test_the_create_door_stores_digits_and_a_scan_finds_it(self, mock_db, typed):
        from api.routers.products import create_product, ProductCreate

        body = ProductCreate(
            sku="SAN-C", category="FRAME", brand="B", model="M-SAN-C",
            color="Black", mrp=1000.0, offer_price=900.0,
            attributes={"gtin": typed},
        )
        pid = asyncio.run(create_product(body, _ADMIN))["product_id"]
        spine = mock_db["products"].find_one({"product_id": pid})
        assert spine["attributes"]["gtin"] == _EAN_SANITISED
        repo = ProductRepository(mock_db["products"])
        assert repo.find_by_barcode(_EAN_SANITISED)["product_id"] == pid

    @pytest.mark.parametrize("typed", _SPACED)
    def test_the_edit_door_stores_gtin_and_upc_digits_only(self, mock_db, typed):
        pid = _create("SAN-E")["product_id"]
        _update(pid, attributes={"gtin": typed, "upc": typed})
        attrs = mock_db["products"].find_one({"product_id": pid})["attributes"]
        assert (attrs["gtin"], attrs["upc"]) == (_EAN_SANITISED, _EAN_SANITISED)


# A barcode attribute KEY in another letter case ('GTIN', 'Upc') publishes as
# ims.gtin / ims.upc all the same (the push lower-cases keys), so it is guarded
# like gtin / upc, and the metafields carry only a publishable GTIN.


class TestBarcodeKeysInAnyLetterCase:
    @pytest.mark.parametrize("key", ["GTIN", "Upc", " gtin "])
    def test_an_in_store_code_under_another_spelling_is_refused(
        self, mock_db, mirror_on, key
    ):
        from api.services.shopify_push.product_input import build_product_metafields

        pid = _create(f"KC-{key.strip()}")["product_id"]
        with pytest.raises(HTTPException) as ei:
            _update(pid, attributes={key: _INTERNAL})
        assert ei.value.status_code == 422
        spine = mock_db["products"].find_one({"product_id": pid})
        assert _INTERNAL not in str(spine.get("attributes"))
        twin = mock_db["catalog_products"].find_one(
            {"id": spine.get("pim_product_id") or pid}
        )
        assert _INTERNAL not in str(build_product_metafields(twin))

    def test_a_good_code_under_another_spelling_is_the_one_gtin(
        self, mock_db, mirror_on
    ):
        p1 = _create("KC-OK-1")["product_id"]
        _update(p1, attributes={"GTIN": "4006381 333931"})
        spine = mock_db["products"].find_one({"product_id": p1})
        assert spine["attributes"]["gtin"] == _VALID_A
        assert "GTIN" not in spine["attributes"]
        twin = mock_db["catalog_products"].find_one(
            {"id": spine.get("pim_product_id") or p1}
        )
        assert _pushed_barcodes(mock_db, twin) == {"KC-OK-1": _VALID_A}
        p2 = _create("KC-OK-2")["product_id"]
        with pytest.raises(HTTPException) as ei:
            _update(p2, attributes={"Gtin": _VALID_A})
        assert ei.value.status_code == 409

    def test_the_guard_folds_every_spelling_onto_the_one_key(self):
        from api.services.product_master import _guard_gtin_attribute

        for attrs in ({"gtin": _VALID_A, "GTIN": _VALID_B}, {"GTIN": _VALID_B, "gtin": _VALID_A}):
            assert _guard_gtin_attribute(attrs, strict=True) == {"gtin": _VALID_A}
        assert _guard_gtin_attribute({"Upc": " 0360-0029-1452"}, strict=True) == {"upc": _UPC_A}
        # A draft/import row drops a junk code under any spelling.
        assert _guard_gtin_attribute({"GTIN": _INTERNAL, "frame_material": "TR90"}, strict=False) == {
            "frame_material": "TR90"
        }

    def test_the_metafields_carry_only_a_publishable_gtin(self):
        from api.services.shopify_push.product_input import (
            build_product_metafields,
            build_removed_metafields,
        )

        def mf(attrs):
            return [(m["key"], m["value"]) for m in build_product_metafields({"attributes": attrs})]

        # A stored in-store code (any spelling) never goes; a spaced UPC goes bare.
        assert mf({"GTIN": _INTERNAL, "Upc": "0360-0029-1452", "frame_material": "Acetate"}) == [
            ("frame_material", "Acetate"),
            ("upc", _UPC_A),
        ]
        assert mf({"gtin": _INTERNAL}) == [] and mf({"upc": "TW003HG14"}) == []
        # One ims.gtin, and the exact key wins over another spelling.
        assert mf({"gtin": _VALID_A, "GTIN": _VALID_B}) == [("gtin", _VALID_A)]
        assert mf({"GTIN": _VALID_B, "gtin": _VALID_A}) == [("gtin", _VALID_A)]
        assert build_removed_metafields({"attributes": {"GTIN": ""}}) == [
            {"namespace": "ims", "key": "gtin"}
        ]
