"""
Unification step-8 -- ONE category registry as the single source of truth.

Locks the behaviour-preservation contract for step-8 of
docs/reference/UNIFICATION_AUDIT_2026-06-10.md: the product_master registry
(`canonical_categories` / `resolve_category` / `validate_attributes` + the
per-category attribute schema) is THE single source for the product-category
taxonomy, and every consumer that used to hardcode a category list / cap map /
GST map now resolves the SAME value via the registry / its companion single-
source modules (pricing_caps, gst_rates).

These are PURE tests: no Mongo, no HTTP, nothing seeded. They import the code
constants directly and assert equality. The point is NOT to exercise plumbing
but to FREEZE the documented values so a future edit that drifts a cap or a GST
rate fails loudly here.

Documented source of truth (docs/SYSTEM_INTENT.md sec 3 + CLAUDE.md
"Non-negotiable business rules"):

  Category discount caps (keyed on discount_category, NOT product category):
    MASS 15 | PREMIUM 20 | LUXURY 5 | SERVICE 10 | NON_DISCOUNTABLE 0
  Luxury brand caps (override category when lower):
    Cartier/Chopard/Bvlgari 2 | Gucci/Prada/Versace/Burberry 5
  GST by product category:
    5%  frames / optical (spectacle) lenses / corrective readers / contacts
    18% sunglasses / watches / smartwatches / smart glasses / clocks /
        accessories / services
    0%  hearing aids (HSN 9021 NIL/exempt)

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_unification_8_category_registry.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import product_master as pm  # noqa: E402
from api.services.pricing_caps import (  # noqa: E402
    CATEGORY_DISCOUNT_CAPS,
    LUXURY_BRAND_CAPS,
    effective_discount_cap,
)
from api.services.gst_rates import gst_rate_for_category  # noqa: E402


# ===========================================================================
# Documented (SYSTEM_INTENT / CLAUDE.md) values -- the behaviour to preserve.
# ===========================================================================

# The 13 canonical product categories the registry owns.
EXPECTED_CANONICAL_CATEGORIES = {
    "FRAME",
    "SUNGLASS",
    "OPTICAL_LENS",
    "READING_GLASSES",
    "CONTACT_LENS",
    "COLORED_CONTACT_LENS",
    "WATCH",
    "SMARTWATCH",
    "SMARTGLASSES",
    "WALL_CLOCK",
    "ACCESSORIES",
    "SERVICES",
    "HEARING_AID",
}

# discount_category -> documented cap %.
DOC_CATEGORY_CAPS = {
    "MASS": 15.0,
    "PREMIUM": 20.0,
    "LUXURY": 5.0,
    "SERVICE": 10.0,
    "NON_DISCOUNTABLE": 0.0,
}

# luxury brand -> documented cap %.
DOC_LUXURY_BRAND_CAPS = {
    "CARTIER": 2.0,
    "CHOPARD": 2.0,
    "BVLGARI": 2.0,
    "GUCCI": 5.0,
    "PRADA": 5.0,
    "VERSACE": 5.0,
    "BURBERRY": 5.0,
}

# product category -> documented GST rate %. The single source of revenue truth.
DOC_GST_BY_CATEGORY = {
    "FRAME": 5.0,
    "OPTICAL_LENS": 5.0,
    "READING_GLASSES": 5.0,
    "CONTACT_LENS": 5.0,
    "COLORED_CONTACT_LENS": 5.0,
    "SUNGLASS": 18.0,
    "WATCH": 18.0,
    "SMARTWATCH": 18.0,
    "SMARTGLASSES": 18.0,
    "WALL_CLOCK": 18.0,
    "ACCESSORIES": 18.0,
    "SERVICES": 18.0,
    "HEARING_AID": 0.0,
}


# ===========================================================================
# 1. The registry is THE single source -- authoritative accessors.
# ===========================================================================


def test_canonical_categories_are_the_documented_thirteen():
    cats = pm.canonical_categories()
    assert isinstance(cats, list)
    assert set(cats) == EXPECTED_CANONICAL_CATEGORIES
    assert len(cats) == len(EXPECTED_CANONICAL_CATEGORIES) == 13


def test_canonical_categories_returns_a_copy_not_the_live_registry():
    a = pm.canonical_categories()
    a.append("HACKED")
    b = pm.canonical_categories()
    assert "HACKED" not in b


@pytest.mark.parametrize("canonical", sorted(EXPECTED_CANONICAL_CATEGORIES))
def test_resolve_category_is_idempotent_on_canonical_keys(canonical):
    assert pm.resolve_category(canonical) == canonical
    assert pm.is_known_category(canonical) is True


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("FR", "FRAME"),
        ("fr", "FRAME"),
        ("SG", "SUNGLASS"),
        ("LS", "OPTICAL_LENS"),
        ("LENS", "OPTICAL_LENS"),
        ("CL", "CONTACT_LENS"),
        ("WT", "WATCH"),
        ("CK", "WALL_CLOCK"),
        ("HA", "HEARING_AID"),
        ("SVC", "SERVICES"),
        ("frames", "FRAME"),
        ("Optical-Lenses", "OPTICAL_LENS"),
    ],
)
def test_resolve_category_normalises_aliases(alias, expected):
    assert pm.resolve_category(alias) == expected


@pytest.mark.parametrize("junk", ["", None, "  ", "NOT_A_CATEGORY", "XYZ"])
def test_resolve_category_rejects_unknown(junk):
    assert pm.resolve_category(junk) is None
    assert pm.is_known_category(junk) is False


def test_validate_attributes_is_authoritative_required_field_gate():
    # A FRAME without colour_code is rejected (category-conditional required).
    with pytest.raises(pm.ProductMasterError) as exc:
        pm.validate_attributes(
            "FRAME", {"brand_name": "Ray-Ban", "model_no": "RB123"}
        )
    assert exc.value.field == "colour_code"
    # A complete FRAME passes.
    pm.validate_attributes(
        "FRAME",
        {"brand_name": "Ray-Ban", "model_no": "RB123", "colour_code": "BLK"},
    )
    # An unknown category is a 422 on the category field.
    with pytest.raises(pm.ProductMasterError) as exc2:
        pm.validate_attributes("NOPE", {})
    assert exc2.value.field == "category"


def test_validate_attributes_accepts_any_input_form_of_the_category():
    # Short code resolves to the same required-field gate as the long form.
    with pytest.raises(pm.ProductMasterError) as exc:
        pm.validate_attributes("HA", {"brand_name": "Phonak"})
    # HEARING_AID requires model_no + serial_no on top of brand_name.
    assert exc.value.field in {"model_no", "serial_no"}


# ===========================================================================
# 2. Behaviour-preservation: every category's cap + GST via the registry-era
#    single-source modules equals the documented SYSTEM_INTENT value.
# ===========================================================================


@pytest.mark.parametrize("tier,cap", sorted(DOC_CATEGORY_CAPS.items()))
def test_category_discount_cap_matches_documented(tier, cap):
    # The cap table is the single source POS enforces; it must equal the doc.
    assert CATEGORY_DISCOUNT_CAPS[tier] == cap
    # And resolving via the public resolver (no luxury brand) returns the same.
    assert effective_discount_cap(tier) == cap


def test_category_cap_table_has_exactly_the_documented_tiers():
    assert set(CATEGORY_DISCOUNT_CAPS.keys()) == set(DOC_CATEGORY_CAPS.keys())


@pytest.mark.parametrize("brand,cap", sorted(DOC_LUXURY_BRAND_CAPS.items()))
def test_luxury_brand_cap_matches_documented(brand, cap):
    assert LUXURY_BRAND_CAPS[brand] == cap


def test_luxury_brand_caps_have_exactly_the_documented_brands():
    assert set(LUXURY_BRAND_CAPS.keys()) == set(DOC_LUXURY_BRAND_CAPS.keys())


def test_luxury_brand_cap_overrides_category_when_lower():
    # A LUXURY-tier Cartier watch is capped at the 2% brand cap, not 5%.
    assert effective_discount_cap("LUXURY", "Cartier") == 2.0
    # A MASS-tier Gucci is capped at min(15, 5) = 5.
    assert effective_discount_cap("MASS", "Gucci") == 5.0


@pytest.mark.parametrize(
    "category,rate", sorted(DOC_GST_BY_CATEGORY.items())
)
def test_gst_rate_per_canonical_category_matches_documented(category, rate):
    # gst_rates is the single source the master + POS billing both read.
    assert gst_rate_for_category(category) == rate


def test_every_canonical_category_has_a_known_gst_rate():
    # No registry category should fall through to the unknown-category fallback
    # silently; each maps to an explicit documented rate.
    for cat in pm.canonical_categories():
        assert cat in DOC_GST_BY_CATEGORY
        assert gst_rate_for_category(cat) == DOC_GST_BY_CATEGORY[cat]


# ===========================================================================
# 3. Each repointed consumer resolves the SAME taxonomy as the registry.
# ===========================================================================


def test_products_router_category_display_sources_from_registry():
    from api.routers import products as P

    # The create/update guard's category list now reads from the registry.
    assert list(P._VALID_CATEGORY_DISPLAY) == pm.canonical_categories()
    # And the accepted-category superset still contains every registry category
    # (behaviour-preserving: nothing the registry knows is rejected).
    for cat in pm.canonical_categories():
        assert cat in P._VALID_CATEGORY_KEYS


def test_admin_discount_rules_caps_source_from_pricing_caps_byte_shape():
    # The /admin/discounts/rules defaults must equal what the OLD hardcoded
    # literal produced -- same values, same int presentation, same brand casing.
    import asyncio
    from api.routers.admin_extras import get_discount_rules

    # No DB override doc in this pure test -> returns just the canonical
    # defaults (now sourced from pricing_caps). asyncio.run gives a fresh loop
    # (Py3.12+/3.14 removed the implicit get_event_loop()).
    result = asyncio.run(get_discount_rules())

    expected_category = {
        "MASS": 15,
        "PREMIUM": 20,
        "LUXURY": 5,
        "SERVICE": 10,
        "NON_DISCOUNTABLE": 0,
    }
    expected_brands = {
        "Cartier": 2,
        "Chopard": 2,
        "Bvlgari": 2,
        "Gucci": 5,
        "Prada": 5,
        "Versace": 5,
        "Burberry": 5,
    }
    assert result["category_caps"] == expected_category
    assert result["luxury_brand_caps"] == expected_brands
    # Whole-number presentation preserved (int, not float).
    assert all(isinstance(v, int) for v in result["category_caps"].values())
    assert all(isinstance(v, int) for v in result["luxury_brand_caps"].values())
    # And the NUMBERS equal the single-source cap constants.
    assert result["category_caps"]["MASS"] == int(CATEGORY_DISCOUNT_CAPS["MASS"])
    assert result["luxury_brand_caps"]["Cartier"] == int(LUXURY_BRAND_CAPS["CARTIER"])


def test_product_schema_category_enum_mirrors_registry():
    from database.schemas import PRODUCT_SCHEMA

    enum = PRODUCT_SCHEMA["properties"]["category"]["enum"]
    assert set(enum) == set(pm.canonical_categories())


# ===========================================================================
# 4. Regression-lock the two KNOWN divergences between the /catalog door and
#    the registry, so neither side is silently "fixed" without an owner call.
#    (These are FLAGGED in catalog.py + the step-8 return notes, not resolved.)
# ===========================================================================


def test_catalog_contact_lens_required_field_still_diverges_from_registry():
    from api.routers.catalog import CATEGORY_FIELDS, ProductCategory

    catalog_req = set(CATEGORY_FIELDS[ProductCategory.CONTACT_LENS]["required"])
    registry_req = set(pm.required_fields("CONTACT_LENS"))
    # Documented divergence: /catalog requires power; registry requires expiry.
    assert "power" in catalog_req
    assert "expiry_date" in registry_req
    assert catalog_req != registry_req


def test_catalog_hearing_aid_required_field_still_diverges_from_registry():
    from api.routers.catalog import CATEGORY_FIELDS, ProductCategory

    catalog_req = set(CATEGORY_FIELDS[ProductCategory.HEARING_AID]["required"])
    registry_req = set(pm.required_fields("HEARING_AID"))
    # Documented divergence: registry adds serial_no, /catalog does not.
    assert "serial_no" not in catalog_req
    assert "serial_no" in registry_req


def test_catalog_short_codes_all_resolve_through_the_registry():
    # Every /catalog short code is a registered alias the registry knows, so the
    # registry is a strict superset of the /catalog door's taxonomy.
    from api.routers.catalog import ProductCategory

    for cat in ProductCategory:
        assert pm.resolve_category(cat.value) is not None
