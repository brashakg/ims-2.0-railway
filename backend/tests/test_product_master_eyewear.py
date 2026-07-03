"""
Rich eyewear field set for FRAME + SUNGLASS (catalog PM).

Locks the additive, backward-compatible eyewear feature:

  * FRAME and SUNGLASS carry a shared rich attribute set (shape, frame_color,
    temple_color/material, lens_size/bridge_width/temple_length, USPs, gender,
    country_of_origin, warranty, and the MANUFACTURER codes UPC/GTIN), with a
    frame-specific split (blue_cut_lens) and a sunglass-specific split
    (lens_colour + lens_material).
  * These keys are ADDITIVE optional attributes: supplying them persists them
    under the product's `attributes` dict (canonical home), and NOT supplying
    them changes nothing (an old FRAME/SUNGLASS without them still validates).
  * A create WITHOUT a supplied SKU auto-mints the clean semantic SKU (the FE no
    longer fabricates a client-side SKU).

NOTE: our internal per-unit barcode is generated at Goods-Receipt (one per
physical unit), NOT at catalogue creation, so this suite does NOT assert an
auto-barcode at create time.

CI-robust: uses the in-memory MockCollection (no live Mongo). Mirrors
test_unification_9_canonical_product_create.py.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest \
        backend/tests/test_product_master_eyewear.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from database.connection import MockCollection  # noqa: E402
from database.repositories.product_repository import ProductRepository  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.catalog_variant_repository import (  # noqa: E402
    CatalogVariantRepository,
)
from api.services import product_master as pm  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mirror_off(monkeypatch):
    """Mirror OFF for these service-level tests (env beats the registry default)."""
    monkeypatch.setenv("PM_MIRROR_ENABLED", "")
    monkeypatch.delenv("DISPATCH_MODE", raising=False)
    yield


@pytest.fixture
def product_repo():
    return ProductRepository(MockCollection("products"))


@pytest.fixture
def variant_repo():
    return CatalogVariantRepository(MockCollection("catalog_variants"))


@pytest.fixture
def audit_repo():
    return AuditRepository(MockCollection("audit_logs"))


# The rich eyewear keys we expect on BOTH FRAME and SUNGLASS.
_SHARED_EYEWEAR_KEYS = (
    "label",
    "full_model_no",
    "shape",
    "frame_color",
    "temple_color",
    "temple_material",
    "bridge_width",
    "temple_length",
    "lens_usp",
    "product_usp",
    "usp_1",
    "usp_2",
    "usp",
    "gender",
    "gender_label",
    "country_of_origin",
    "warranty",
    "upc",
    "gtin",
)

# A representative rich FRAME attribute payload (shared keys + frame-only key).
_FRAME_RICH_ATTRS = {
    "brand_name": "Ray-Ban",
    "model_no": "RB-2140",
    "colour_code": "BLK",
    "label": "Wayfarer Classic",
    "full_model_no": "RB2140-901-50",
    "shape": "Wayfarer",
    "frame_color": "Black",
    "temple_color": "Black",
    "frame_material": "Acetate",
    "temple_material": "Acetate",
    "frame_type": "Full Rim",
    "lens_size": "50",
    "bridge_width": "22",
    "temple_length": "150",
    "blue_cut_lens": "Yes",
    "lens_usp": "Anti-glare",
    "product_usp": "Iconic classic",
    "usp_1": "Durable",
    "usp_2": "UV block",
    "usp": "Timeless",
    "gender": "Unisex",
    "gender_label": "Men & Women",
    "country_of_origin": "Italy",
    "warranty": "1 year",
    "upc": "805289126575",
    "gtin": "8053672000000",
}

# A representative rich SUNGLASS attribute payload (shared keys + sunglass-only).
_SUNGLASS_RICH_ATTRS = {
    "brand_name": "Oakley",
    "model_no": "OO9208",
    "colour_code": "PRIZM",
    "label": "Radar EV Path",
    "full_model_no": "OO9208-51-38",
    "shape": "Wrap",
    "frame_color": "Matte Black",
    "temple_color": "Black",
    "frame_material": "O-Matter",
    "temple_material": "O-Matter",
    "frame_type": "Half Rim",
    "lens_size": "38",
    "bridge_width": "12",
    "temple_length": "128",
    "polarization": "Yes",
    "uv_protection": "UV400",
    "tint": "Prizm Road",
    "lens_colour": "Rose",
    "lens_material": "Polycarbonate",
    "lens_usp": "Prizm contrast",
    "product_usp": "Sport performance",
    "usp_1": "Impact resistant",
    "usp_2": "Grippy",
    "usp": "Elite sport",
    "gender": "Men",
    "gender_label": "Men",
    "country_of_origin": "USA",
    "warranty": "2 years",
    "upc": "888392287533",
    "gtin": "8888392287500",
}


# ---------------------------------------------------------------------------
# 1. The new keys are registered as OPTIONAL on FRAME + SUNGLASS
# ---------------------------------------------------------------------------


def test_frame_optional_list_includes_new_eyewear_keys():
    opt = set(pm.optional_fields("FRAME"))
    for key in _SHARED_EYEWEAR_KEYS + ("blue_cut_lens",):
        assert key in opt, f"FRAME optional missing '{key}'"
    # The pre-existing FRAME optionals are preserved (additive, not a rewrite).
    for key in ("subbrand", "size", "frame_material", "frame_type"):
        assert key in opt


def test_sunglass_optional_list_includes_new_eyewear_keys():
    opt = set(pm.optional_fields("SUNGLASS"))
    for key in _SHARED_EYEWEAR_KEYS + ("lens_colour", "lens_material"):
        assert key in opt, f"SUNGLASS optional missing '{key}'"
    # The pre-existing SUNGLASS optionals are preserved.
    for key in ("subbrand", "lens_size", "polarization", "uv_protection", "tint"):
        assert key in opt


def test_all_category_specs_surfaces_new_keys_with_labels():
    specs = {s["code"]: s for s in pm.all_category_specs()}
    frame = specs["FRAME"]
    sunglass = specs["SUNGLASS"]
    assert "blue_cut_lens" in frame["optional_fields"]
    assert "lens_colour" in sunglass["optional_fields"]
    assert "lens_material" in sunglass["optional_fields"]
    # The render-ready field entries carry human labels (not just the raw key).
    frame_fields = {f["name"]: f for f in frame["fields"]}
    assert frame_fields["shape"]["label"] == "Shape"
    assert frame_fields["upc"]["label"] == "UPC (mfr)"
    assert frame_fields["gtin"]["label"] == "GTIN (mfr)"
    assert frame_fields["blue_cut_lens"]["label"] == "Blue-Cut Lens"
    assert frame_fields["shape"]["required"] is False
    sun_fields = {f["name"]: f for f in sunglass["fields"]}
    assert sun_fields["lens_colour"]["label"] == "Lens Colour"
    assert sun_fields["lens_material"]["label"] == "Lens Material"


# ---------------------------------------------------------------------------
# 2. Rich attributes PERSIST under `attributes`, and the SKU auto-mints
# ---------------------------------------------------------------------------


def test_frame_with_rich_attrs_persists_them_and_auto_mints_sku():
    doc = pm.build_canonical_product(
        {
            "category": "FRAME",
            "attributes": dict(_FRAME_RICH_ATTRS),
            "mrp": 5000.0,
            "offer_price": 4500.0,
            # NO sku supplied -> the door must mint the clean semantic SKU.
        },
        source="FORM",
    )
    # (a) every rich key round-trips under the canonical `attributes` home.
    attrs = doc["attributes"]
    for key, value in _FRAME_RICH_ATTRS.items():
        assert attrs.get(key) == value, f"FRAME attribute '{key}' not persisted"
    # (b) a non-empty SKU was auto-minted (no client SKU was supplied).
    assert isinstance(doc.get("sku"), str) and doc["sku"].strip()
    assert doc["category"] == "FRAME"
    assert doc["sku_prefix"] == "FR"


def test_sunglass_with_rich_attrs_persists_them_and_auto_mints_sku():
    doc = pm.build_canonical_product(
        {
            "category": "SUNGLASS",
            "attributes": dict(_SUNGLASS_RICH_ATTRS),
            "mrp": 8000.0,
            "offer_price": 7200.0,
        },
        source="FORM",
    )
    attrs = doc["attributes"]
    for key, value in _SUNGLASS_RICH_ATTRS.items():
        assert attrs.get(key) == value, f"SUNGLASS attribute '{key}' not persisted"
    assert isinstance(doc.get("sku"), str) and doc["sku"].strip()
    assert doc["category"] == "SUNGLASS"
    assert doc["sku_prefix"] == "SG"


def test_supplied_sku_is_kept_as_is():
    doc = pm.build_canonical_product(
        {
            "category": "FRAME",
            "sku": "LEGACY/FR/RB2140",
            "attributes": {
                "brand_name": "Ray-Ban",
                "model_no": "RB-2140",
                "colour_code": "BLK",
            },
            "mrp": 5000.0,
            "offer_price": 4500.0,
        },
        source="FORM",
    )
    assert doc["sku"] == "LEGACY/FR/RB2140"


# ---------------------------------------------------------------------------
# 3. Full create path (create_via_door) persists the rich attributes to the spine
# ---------------------------------------------------------------------------


def test_create_via_door_persists_rich_frame_to_the_spine(
    product_repo, variant_repo, audit_repo
):
    created = pm.create_via_door(
        {
            "category": "FRAME",
            "attributes": dict(_FRAME_RICH_ATTRS),
            "mrp": 5000.0,
            "offer_price": 4500.0,
        },
        source="FORM",
        actor="tester",
        product_repo=product_repo,
        variant_repo=variant_repo,
        audit_repo=audit_repo,
    )
    assert created["product_id"]
    assert created["sku"].strip()
    # The persisted spine row carries the rich attributes.
    stored = product_repo.find_by_sku(created["sku"])
    assert stored is not None
    stored_attrs = stored.get("attributes") or {}
    assert stored_attrs.get("shape") == "Wayfarer"
    assert stored_attrs.get("blue_cut_lens") == "Yes"
    assert stored_attrs.get("upc") == "805289126575"
    assert stored_attrs.get("gtin") == "8053672000000"


# ---------------------------------------------------------------------------
# 4. Backward compatibility: a plain FRAME/SUNGLASS (no rich keys) still works
# ---------------------------------------------------------------------------


def test_plain_frame_without_rich_keys_still_validates():
    doc = pm.build_canonical_product(
        {
            "category": "FRAME",
            "attributes": {
                "brand_name": "Titan",
                "model_no": "T-1",
                "colour_code": "BRN",
            },
            "mrp": 2000.0,
            "offer_price": 1800.0,
        },
        source="FORM",
    )
    assert doc["category"] == "FRAME"
    # None of the new keys were fabricated onto a payload that didn't send them.
    for key in _SHARED_EYEWEAR_KEYS + ("blue_cut_lens",):
        assert key not in doc["attributes"]
