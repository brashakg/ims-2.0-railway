"""GTIN validation -- the public barcode that reaches Shopify + Shopping feeds.

Regression cover for the 2026-07-29 prod audit: 353 of 2,815 gtin-bearing
`catalog_variants` held a value that was not a GTIN at all. The writer was an
unvalidated passthrough -- every door `$set` whatever string it was handed --
so these tests pin the guard at all three layers:

    entry    product_master.normalise_payload   (form + autopilot + import)
    storage  CatalogVariantRepository.upsert    (the Mongo write door)
    exit     shopify_push._publishable_gtin     (last gate before customers)

Real bad values from the audit backup are used verbatim as fixtures.
No emojis (Windows cp1252).
"""
import pytest

from api.services.gtin import (
    REASON_BADCHECK,
    REASON_BADLEN,
    REASON_NONNUMERIC,
    REASON_RESTRICTED,
    REASON_ZEROS,
    check_digit_ok,
    classify_gtin,
    is_valid_gtin,
    sanitise_gtin,
)

# The row that started the hunt: the comma-joined Shopify browse-TAG string
# found in catalog_variants.gtin for sku=SGBOSS1771/GJ5G54.
TAG_STRING = (
    "brand_boss, shape_square, framecolor_golden, templecolor_golden, "
    "framematerial_betatitanium, templematerial_betatitanium, "
    "frametype_fullframe, framesize_54, origin_, product_, lensusp_, "
    "productusp_, gender_men,"
)

# Genuine, check-digit-correct codes (Ray-Ban EAN-13s from the same catalog).
VALID_EAN13 = "8056597720373"
VALID_EAN13_B = "8056597626088"
VALID_UPCA = "036000291452"  # the canonical GS1 UPC-A worked example
VALID_EAN8 = "96385074"


# ---------------------------------------------------------------------------
# The tag string -- the specific defect this work exists to prevent
# ---------------------------------------------------------------------------


def test_tag_string_is_rejected():
    assert classify_gtin(TAG_STRING) == REASON_NONNUMERIC
    assert is_valid_gtin(TAG_STRING) is False
    assert sanitise_gtin(TAG_STRING) is None


def test_tag_string_survives_separator_stripping():
    """Stripping spaces must not accidentally rescue a tag list into digits."""
    assert sanitise_gtin(TAG_STRING.replace(",", "")) is None


# ---------------------------------------------------------------------------
# Valid codes are accepted (the guard must not be a blanket "reject everything")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code", [VALID_EAN13, VALID_EAN13_B, VALID_UPCA, VALID_EAN8]
)
def test_valid_gtins_accepted(code):
    assert classify_gtin(code) is None
    assert is_valid_gtin(code) is True
    assert sanitise_gtin(code) == code


def test_valid_gtin_is_trimmed_and_dehyphenated():
    assert sanitise_gtin("  8056597720373  ") == VALID_EAN13
    assert sanitise_gtin("805-6597-72037-3") == VALID_EAN13


def test_gtin14_indicator_digit_is_not_read_as_a_gs1_prefix():
    """A GTIN-14 leads with a packaging indicator, so '2' there is NOT the
    restricted 20-29 GS1 range -- it must not be a false RESTRICTED."""
    code = "2" + VALID_EAN13[:12]
    code += str((10 - sum(int(c) * (3 if i % 2 == 0 else 1)
                          for i, c in enumerate(reversed(code))) % 10) % 10)
    assert len(code) == 14
    assert classify_gtin(code) is None


# ---------------------------------------------------------------------------
# Each rejection reason, using values actually found in prod
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,reason",
    [
        # 244 of the 280 BADLEN rows were 7-digit supplier item codes.
        ("2511661", REASON_BADLEN),
        ("70924", REASON_BADLEN),
        # Two EANs pasted into one spec cell -> 26 digits once joined.
        ("8056597720373 8056597720380", REASON_BADLEN),
        # Model / colour codes that were never GTINs.
        ("TW003HG14", REASON_NONNUMERIC),
        ("VPLD94 510509", REASON_NONNUMERIC),
        ("20280908652QT", REASON_NONNUMERIC),
        # A repeated 4-digit stub with no real check digit.
        ("92939293", REASON_BADCHECK),
        ("051805685621", REASON_BADCHECK),
        # Placeholder.
        ("0000000000000", REASON_ZEROS),
    ],
)
def test_prod_junk_is_rejected_with_the_expected_reason(raw, reason):
    assert classify_gtin(raw) == reason
    assert sanitise_gtin(raw) is None


def test_short_code_rejected():
    assert classify_gtin("1234567") == REASON_BADLEN
    assert sanitise_gtin("1234567") is None


def test_bad_checksum_rejected():
    broken = VALID_EAN13[:-1] + ("0" if VALID_EAN13[-1] != "0" else "1")
    assert check_digit_ok(broken) is False
    assert classify_gtin(broken) == REASON_BADCHECK


def test_restricted_gs1_prefix_rejected():
    """GS1 20-29 is in-store / restricted distribution -- and is exactly the
    range services/barcode.py mints our own store_barcode in, so it must never
    escape as a public GTIN."""
    from api.services.barcode import format_ean13

    internal = format_ean13(4242, prefix="20")
    assert check_digit_ok(internal) is True  # well-formed, but not publishable
    assert classify_gtin(internal) == REASON_RESTRICTED
    assert sanitise_gtin(internal) is None


@pytest.mark.parametrize("raw", [None, "", "   ", "  -  "])
def test_empty_is_not_an_error_but_is_not_valid_either(raw):
    assert classify_gtin(raw) is None
    assert is_valid_gtin(raw) is False
    assert sanitise_gtin(raw) is None


# ---------------------------------------------------------------------------
# Entry door: product_master.normalise_payload
# ---------------------------------------------------------------------------


def _payload(gtin):
    return {
        "category": "SUNGLASS",
        "attributes": {
            "brand_name": "Boss",
            "model_no": "1771",
            "colour_code": "J5G",
            "gtin": gtin,
        },
        "mrp": 10000.0,
        "offer_price": 9000.0,
        "sku": "SGBOSS1771/GJ5G54",
    }


def test_form_door_rejects_the_tag_string_with_a_422():
    from api.services.product_master import ProductMasterError, normalise_payload

    with pytest.raises(ProductMasterError) as exc:
        normalise_payload(**_payload(TAG_STRING))
    assert exc.value.status == 422
    assert exc.value.field == "gtin"


def test_form_door_keeps_a_valid_gtin():
    from api.services.product_master import normalise_payload

    doc = normalise_payload(**_payload(VALID_EAN13))
    assert doc["attributes"]["gtin"] == VALID_EAN13


def test_import_door_drops_the_bad_gtin_instead_of_failing_the_row():
    """A 2,000-row import must not die on one bad cell -- but it must not
    persist the bad cell either."""
    from api.services.product_master import normalise_payload

    doc = normalise_payload(force_draft=True, **_payload(TAG_STRING))
    assert "gtin" not in doc["attributes"]
    assert doc["sku"] == "SGBOSS1771/GJ5G54"


def test_draft_door_drops_the_bad_gtin():
    from api.services.product_master import normalise_payload

    doc = normalise_payload(as_draft=True, **_payload("2511661"))
    assert "gtin" not in doc["attributes"]


# ---------------------------------------------------------------------------
# Storage door: CatalogVariantRepository.upsert
# ---------------------------------------------------------------------------


def _repo():
    from database.connection import MockCollection
    from database.repositories.catalog_variant_repository import (
        CatalogVariantRepository,
    )

    return CatalogVariantRepository(MockCollection("catalog_variants"))


def test_repo_drops_an_invalid_gtin_on_insert():
    repo = _repo()
    stored = repo.upsert({"sku": "SG-1", "gtin": TAG_STRING})
    assert stored is not None
    assert not stored.get("gtin")


def test_repo_keeps_a_valid_gtin_on_insert():
    repo = _repo()
    stored = repo.upsert({"sku": "SG-2", "gtin": VALID_EAN13})
    assert stored["gtin"] == VALID_EAN13


def test_repo_update_does_not_overwrite_a_good_gtin_with_junk():
    """The dangerous case: a re-sync handing us garbage must leave the stored
    good value alone rather than $set over it."""
    repo = _repo()
    repo.upsert({"sku": "SG-3", "gtin": VALID_EAN13})
    repo.upsert({"sku": "SG-3", "gtin": "2511661"})
    assert repo.get_by_sku("SG-3")["gtin"] == VALID_EAN13


def test_repo_still_allows_clearing_a_gtin():
    repo = _repo()
    repo.upsert({"sku": "SG-4", "gtin": VALID_EAN13})
    repo.upsert({"sku": "SG-4", "gtin": ""})
    assert not repo.get_by_sku("SG-4").get("gtin")


# ---------------------------------------------------------------------------
# Exit door: shopify_push -- nothing invalid reaches a customer
# ---------------------------------------------------------------------------


def test_push_omits_the_barcode_when_the_stored_gtin_is_junk():
    from api.services.shopify_push import build_variant_price_inputs

    rows, _ = build_variant_price_inputs(
        {"sku": "P1", "mrp": 5000, "offer_price": 4000},
        [{"shopify_variant_id": "gid://shopify/ProductVariant/1", "gtin": TAG_STRING}],
    )
    assert len(rows) == 1
    assert "barcode" not in rows[0]


def test_push_sends_a_valid_barcode():
    from api.services.shopify_push import build_variant_price_inputs

    rows, _ = build_variant_price_inputs(
        {"sku": "P1", "mrp": 5000, "offer_price": 4000},
        [
            {
                "shopify_variant_id": "gid://shopify/ProductVariant/1",
                "gtin": VALID_EAN13,
            }
        ],
    )
    assert rows[0]["barcode"] == VALID_EAN13


def test_push_falls_through_junk_to_the_parent_products_valid_gtin():
    """The old `or` chain stopped at the first TRUTHY value, so a junk variant
    gtin shadowed a good product one. It now picks the first VALID value."""
    from api.services.shopify_push import build_variant_seed_rows

    rows = build_variant_seed_rows(
        {"sku": "P1", "mrp": 5000, "offer_price": 4000, "gtin": VALID_EAN13},
        [{"sku": "P1-A", "gtin": "2511661"}],
    )
    assert rows[0]["row"]["barcode"] == VALID_EAN13


def test_push_never_leaks_our_internal_store_barcode_as_a_gtin():
    """product['barcode'] is in the create-path fallback chain and often holds
    the internally minted GS1 20-29 code -- it must not be published."""
    from api.services.barcode import format_ean13
    from api.services.shopify_push import build_variant_seed_rows

    rows = build_variant_seed_rows(
        {
            "sku": "P1",
            "mrp": 5000,
            "offer_price": 4000,
            "barcode": format_ean13(99, prefix="20"),
        },
        [{"sku": "P1-A"}],
    )
    assert "barcode" not in rows[0]["row"]
