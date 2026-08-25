"""
Smart glasses: catalogue the fields -> GET the listing (owner 2026-08-25).

WHAT THIS LOCKS
---------------
Until now every smart-glass detail on bettervision.in (camera, audio, Meta AI,
touch controls, battery, charging case, Wi-Fi/Bluetooth, storage,
prescription-ready) existed ONLY as prose somebody typed into Shopify by hand,
because the cataloguing form had no field for any of it. This suite locks the
replacement:

  * the SMARTGLASSES registry entry carries the SUNGLASS eyewear vocabulary
    (REUSED, not a second copy) plus the electronics fields,
  * those fields GENERATE the storefront listing -- spec bullets, the
    product_smartglass/product_sunglass/<brand>_<line> tags, and the
    "... | Better Vision" SEO title + the genuine/authorised/call-for-best-price/
    pan-India sentence -- in the shape the 36 LIVE listings already use,
  * a blank field OMITS its bullet (never an empty or "undefined" line),
  * the new fields are ADDITIVE: a smart glass catalogued the old way, with only
    brand/model/colour, still creates,
  * the frontend field list and the backend registry agree FIELD-FOR-FIELD
    (parsed out of the .ts, so the two cannot drift),
  * SUNGLASS is byte-identical to before the shared-tail hoist,
  * generation is CREATE-ONLY, so the 36 live listings are never re-derived.

Run: JWT_SECRET_KEY=test ENVIRONMENT=test python -m pytest \
        backend/tests/test_smartglass_listing.py -q
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from api.services import product_master as pm  # noqa: E402
from api.services import smartglass_listing as sgl  # noqa: E402


# A full Ray-Ban Meta payload, transcribed from what the LIVE listing
# "Rayban META RW4006 601/7150 50 Black SmartGlasses" states in its own bullets.
FULL_ATTRS = {
    "brand_name": "Ray-Ban",
    "subbrand": "Meta",
    "model_name": "Wayfarer",
    "model_no": "RW4006",
    "colour_code": "601/7150",
    "generation": "Gen 2",
    "shape": "Wayfarer",
    "frame_material": "Acetate",
    "frame_color": "Black",
    "lens_colour": "Green",
    "lens_size": 50,
    "camera_mp": 12,
    "camera_type": "Ultra-wide",
    "video_resolution": "1080p",
    "audio_type": "Open-ear speakers",
    "microphone_count": 5,
    "voice_assistant": "Meta AI",
    "controls": "Capacitive touch controls on the temple",
    "battery_life_hours": 4,
    "charging_case": "Yes",
    "connectivity": "Wi-Fi 6 and Bluetooth 5.2",
    "storage_gb": 32,
    "prescription_ready": "Yes",
}

# The exact bullets that payload must produce, in listing order.
EXPECTED_BULLETS = [
    "12MP ultra-wide camera for photos and 1080p video",
    "Open-ear speakers with a discreet 5-microphone array",
    "Meta AI voice assistant",
    "Capacitive touch controls on the temple",
    "Up to ~4 hours per charge, plus a portable charging case",
    "Wi-Fi 6 and Bluetooth 5.2 connectivity; 32GB on-board storage",
    "Classic wayfarer silhouette in acetate",
    "Prescription-ready (single-vision and progressive lenses)",
]


# ---------------------------------------------------------------------------
# 1. The registry actually holds what a listing needs
# ---------------------------------------------------------------------------


def test_smartglasses_registry_carries_the_eyewear_base_and_the_electronics():
    """Before: optional was ("subbrand",) -- literally nothing else. Every
    eyewear question AND every electronics spec must now be capturable."""
    fields = set(pm.optional_fields("SMARTGLASSES")) | set(
        pm.required_fields("SMARTGLASSES")
    )

    eyewear = {
        "shape",
        "frame_type",
        "frame_color",
        "temple_color",
        "lens_colour",
        "frame_material",
        "temple_material",
        "lens_material",
        "lens_size",
        "bridge_width",
        "temple_length",
        "gender",
        "polarization",
        "uv_protection",
        "tint",
        "country_of_origin",
        "warranty",
        "upc",
        "gtin",
    }
    electronics = {
        "generation",
        "camera_mp",
        "camera_type",
        "video_resolution",
        "audio_type",
        "microphone_count",
        "voice_assistant",
        "controls",
        "battery_life_hours",
        "charging_case",
        "connectivity",
        "storage_gb",
        "prescription_ready",
    }
    assert eyewear <= fields, sorted(eyewear - fields)
    assert electronics <= fields, sorted(electronics - fields)


def test_smartglasses_reuses_the_sunglass_tail_rather_than_copying_it():
    """A second copy of the eyewear vocabulary is the duplication this repo
    keeps getting bitten by: assert the SAME tuple object backs both."""
    assert pm._EYEWEAR_SUN_TAIL in (tuple(pm._CATEGORY_SPECS["SUNGLASS"].optional[3:]),)
    sg_optional = pm._CATEGORY_SPECS["SMARTGLASSES"].optional
    assert sg_optional[3 : 3 + len(pm._EYEWEAR_SUN_TAIL)] == pm._EYEWEAR_SUN_TAIL


def test_sunglass_registry_is_unchanged_by_the_hoist():
    """The hoist must not have moved, renamed or dropped a SUNGLASS field."""
    assert pm._CATEGORY_SPECS["SUNGLASS"].required == (
        "brand_name",
        "model_no",
        "colour_code",
    )
    assert pm._CATEGORY_SPECS["SUNGLASS"].optional == (
        "subbrand",
        "label",
        "model_name",
        "lens_size",
        "bridge_width",
        "temple_length",
        "gender",
        "shape",
        "frame_type",
        "lens_colour",
        "tint",
        "polarization",
        "uv_protection",
        "frame_color",
        "temple_color",
        "lens_material",
        "frame_material",
        "temple_material",
        "usp_1",
        "usp_2",
        "country_of_origin",
        "warranty",
        "upc",
        "gtin",
    )


def test_every_new_field_has_a_plain_english_label():
    """A shop person reads 'Battery Life (hours)', never 'battery_life_hours'."""
    for key in pm._SMARTGLASS_TECH:
        label = pm.field_label(key)
        assert "_" not in label, f"{key} -> {label!r} is a raw field name"
    assert pm.field_label("battery_life_hours") == "Battery Life (hours)"
    assert pm.field_label("camera_mp") == "Camera (megapixels)"
    assert pm.field_label("prescription_ready") == "Prescription Lenses Possible"


def test_new_fields_are_additive_not_suddenly_mandatory():
    """A smart glass catalogued the old way -- brand + model + colour only --
    must still validate. The new fields are optional, every one of them."""
    pm.validate_attributes(
        "SMARTGLASSES",
        {"brand_name": "Ray-Ban", "model_name": "Wayfarer", "colour_code": "BLK"},
    )
    assert pm.required_fields("SMARTGLASSES") == [
        "brand_name",
        "model_name",
        "colour_code",
    ]


# ---------------------------------------------------------------------------
# 2. The listing generates from the fields
# ---------------------------------------------------------------------------


def test_spec_bullets_match_the_fields_given_exactly():
    assert sgl.build_spec_bullets(FULL_ATTRS) == EXPECTED_BULLETS
    assert len(sgl.build_spec_bullets(FULL_ATTRS)) == 8


def test_a_blank_field_omits_its_bullet_and_never_prints_an_empty_line():
    """The whole point: leave the camera blank on a camera-less model and the
    camera bullet disappears -- it does not print '' or 'undefined' or 'None'."""
    attrs = dict(FULL_ATTRS)
    for blank in ("camera_mp", "camera_type", "video_resolution"):
        attrs[blank] = ""
    attrs["voice_assistant"] = None

    bullets = sgl.build_spec_bullets(attrs)

    assert not any("camera" in b.lower() for b in bullets)
    assert not any("voice assistant" in b.lower() for b in bullets)
    assert len(bullets) == 6
    assert all(b.strip() for b in bullets)
    joined = " ".join(bullets).lower()
    for poison in ("undefined", "none", "null", "nan"):
        assert poison not in joined


def test_html_has_no_empty_list_items():
    attrs = {k: "" for k in FULL_ATTRS}
    attrs.update(
        {"brand_name": "Ray-Ban", "subbrand": "Meta", "voice_assistant": "Meta AI"}
    )
    html = sgl.build_description_html(attrs)
    assert "<li></li>" not in html
    assert html.count("<li>") == 1
    assert "<li>Meta AI voice assistant</li>" in html


def test_description_html_is_headline_then_bullets():
    html = sgl.build_description_html(FULL_ATTRS)
    assert html.startswith("<h2>")
    assert "Ray-Ban Meta Wayfarer (Gen 2) - Black with Green lenses" in html
    assert html.count("<li>") == len(EXPECTED_BULLETS)
    for bullet in EXPECTED_BULLETS:
        assert f"<li>{bullet}</li>" in html
    # No AI, no invented prose: a paragraph appears only when one was written.
    assert "<p>" not in html
    assert "<p>Handsome and clever.</p>" in sgl.build_description_html(
        FULL_ATTRS, paragraph="Handsome and clever."
    )


def test_nothing_filled_yields_no_description_rather_than_an_empty_shell():
    assert sgl.build_description_html({}) == ""


def test_tag_set_matches_the_live_storefront_vocabulary():
    assert set(sgl.build_tags(FULL_ATTRS)) == {
        "product_smartglass",
        "product_sunglass",
        "rayban_meta",
    }
    # No product line -> no brand-line token, and never a dangling "rayban_".
    assert set(sgl.build_tags({"brand_name": "Ray-Ban"})) == {
        "product_smartglass",
        "product_sunglass",
    }


def test_tags_do_not_duplicate_what_the_attribute_tag_generator_emits():
    """shopify_tag_gen owns brand_/shape_/framematerial_/... -- this module must
    not grow a second copy of that registry."""
    from api.services.shopify_tag_gen import generate_attribute_tags

    attribute_tags = set(generate_attribute_tags("SMARTGLASSES", FULL_ATTRS))
    assert "brand_ray-ban" in attribute_tags
    assert "shape_wayfarer" in attribute_tags
    assert "framematerial_acetate" in attribute_tags
    assert attribute_tags.isdisjoint(set(sgl.build_tags(FULL_ATTRS)))


def test_seo_title_and_description_follow_the_store_pattern():
    title = sgl.build_seo_title(FULL_ATTRS)
    assert title == "Ray-Ban Meta Wayfarer Black Smart Glasses | Better Vision"
    assert title.endswith("| Better Vision")
    assert len(title) <= sgl.MAX_SEO_TITLE

    desc = sgl.build_seo_description(FULL_ATTRS)
    assert "genuine" in desc
    assert "authorised" in desc
    assert "best price" in desc
    assert "pan-India" in desc
    assert len(desc) <= sgl.MAX_SEO_DESCRIPTION


def test_yes_no_fields_read_as_yes_no_not_as_text():
    no_case = dict(FULL_ATTRS, charging_case="No", prescription_ready="No")
    bullets = sgl.build_spec_bullets(no_case)
    assert "Up to ~4 hours per charge" in bullets
    assert not any("charging case" in b.lower() for b in bullets)
    assert not any("prescription" in b.lower() for b in bullets)


def test_html_is_escaped():
    html = sgl.build_description_html(
        {"brand_name": "<script>x</script>", "voice_assistant": "A & B"}
    )
    assert "<script>" not in html
    assert "A &amp; B" in html


# ---------------------------------------------------------------------------
# 3. The form and the server agree, field for field
# ---------------------------------------------------------------------------

_FE_SHARED = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "pages"
    / "catalog"
    / "productAddShared.ts"
)


def _fe_smartglass_field_names():
    """Parse the SMTFR field list out of productAddShared.ts. Reading the real
    file (not a fixture that would just restate the answer) is what makes this
    a PIN: the two lists cannot drift without this failing."""
    src = _FE_SHARED.read_text(encoding="utf-8")
    start = src.index("\n  SMTFR: [")
    end = src.index("\n  ],", start)
    return re.findall(r"\{ name: '([a-z0-9_]+)'", src[start:end])


@pytest.mark.skipif(not _FE_SHARED.exists(), reason="frontend not checked out")
def test_frontend_list_and_backend_registry_agree_field_for_field():
    fe = _fe_smartglass_field_names()
    be = list(pm.required_fields("SMARTGLASSES")) + list(
        pm.optional_fields("SMARTGLASSES")
    )
    assert sorted(fe) == sorted(be), {
        "only_in_form": sorted(set(fe) - set(be)),
        "only_in_registry": sorted(set(be) - set(fe)),
    }
    assert len(fe) == len(be) == len(set(be))


@pytest.mark.skipif(not _FE_SHARED.exists(), reason="frontend not checked out")
def test_there_is_only_one_smartglasses_tile_in_the_picker():
    """There used to be two tiles (SMTSG + SMTFR) for the ONE canonical
    category, so half the operators filled the shorter form."""
    src = _FE_SHARED.read_text(encoding="utf-8")
    picker = src[src.index("export const CATEGORIES = [") : src.index("] as const;")]
    tiles = re.findall(r"\{ code: '([A-Z]+)', name: '([^']+)'", picker)
    smart = [name for code, name in tiles if name.startswith("Smartglasses")]
    assert smart == ["Smartglasses"], smart
    assert "SMTSG" not in [code for code, _ in tiles]


# ---------------------------------------------------------------------------
# 4. The 36 live listings are not rewritten out from under anybody
# ---------------------------------------------------------------------------


def test_listing_copy_is_generated_at_create_only():
    """_build_pim_doc is where the listing is derived. Both of its callers are
    inside create_product, so no sweep/backfill/edit path can re-derive the
    description or tags of a product that is already live."""
    src = Path(pm.__file__).read_text(encoding="utf-8")
    callers = [
        line.strip()
        for line in src.splitlines()
        if "_build_pim_doc(" in line and not line.strip().startswith("def ")
    ]
    assert callers, "the listing builder lost its call site"
    assert all(
        c.startswith("pim_doc = _build_pim_doc(spine)") for c in callers
    ), callers
    # ...and those two helpers are only ever called from create_product.
    assert src.count("_write_mirror(") == 2  # def + the create_product call
    assert src.count("_stage_catalog_draft(") == 2


def test_generated_description_never_overwrites_one_that_was_written():
    """A typed / AI-written description wins -- generation only FILLS A BLANK."""
    spine = {
        "category": "SMARTGLASSES",
        "brand": "Ray-Ban",
        "attributes": dict(FULL_ATTRS),
        "description": "<p>Hand-written, do not touch.</p>",
        "sku": "SMTFRRAYMETA1",
        "pim_product_id": "pim-1",
    }
    doc = pm._build_pim_doc(spine)
    assert doc["description"] == "<p>Hand-written, do not touch.</p>"


def test_a_catalogued_smart_glass_carries_a_full_listing_into_the_pim_doc():
    spine = {
        "category": "SMARTGLASSES",
        "brand": "Ray-Ban",
        "attributes": dict(FULL_ATTRS),
        "sku": "SMTFRRAYMETA1",
        "pim_product_id": "pim-1",
        "tags": ["clearance"],
    }
    doc = pm._build_pim_doc(spine)

    for bullet in EXPECTED_BULLETS:
        assert f"<li>{bullet}</li>" in doc["description"]
    assert doc["ecom"]["seo"]["title"].endswith("| Better Vision")
    assert "pan-India" in doc["ecom"]["seo"]["description"]
    # The product's own tags survive; the generated ones are UNIONED on.
    assert doc["ecom"]["seo"]["tags"][0] == "clearance"
    assert {"product_smartglass", "product_sunglass", "rayban_meta"} <= set(
        doc["ecom"]["seo"]["tags"]
    )


def test_a_sunglass_pim_doc_is_untouched_by_the_smartglass_generator():
    spine = {
        "category": "SUNGLASS",
        "brand": "Ray-Ban",
        "attributes": {
            "brand_name": "Ray-Ban",
            "model_no": "RB2140",
            "colour_code": "BLK",
        },
        "sku": "SGRAYRB2140BLK",
        "pim_product_id": "pim-2",
    }
    doc = pm._build_pim_doc(spine)
    assert doc["description"] is None
    assert doc["ecom"]["seo"]["tags"] == []
    assert "Better Vision" not in (doc["ecom"]["seo"]["title"] or "")


# ---------------------------------------------------------------------------
# 5. The "Auto-fill with AI" button -- the path everybody actually takes
# ---------------------------------------------------------------------------


async def _ai_description(category, attrs, monkeypatch, paragraph):
    """Drive the REAL /products/generate-description handler. Only the network
    call to the model is faked (it is the INPUT, not the subject): everything
    that assembles the HTML is the shipped code."""
    from agents import claude_client as cc
    from api.routers import products as pr

    async def _fake_call(system, user, **kw):
        return paragraph

    monkeypatch.setattr(cc, "is_claude_available", lambda: True)
    monkeypatch.setattr(cc, "call_claude", _fake_call)
    return await pr.generate_product_description(
        pr.DescriptionGenerateRequest(category=category, attributes=dict(attrs)),
        current_user={"user_id": "u1", "username": "tester"},
    )


_PARAGRAPH = "Reflect your style with the Ray-Ban Meta Wayfarer in Black."


async def test_the_ai_button_keeps_every_smart_glass_spec(monkeypatch):
    """QuickAddPage's Auto-fill button OVERWRITES the description box with what
    this endpoint returns, and (since ProductCreate models `description`) that
    text IS the storefront body. Before this fix it came back as the generic
    eyewear table -- no camera, no audio, no battery, no assistant, no
    prescription -- so the most obvious path through the form produced a listing
    nothing like the 36 live ones."""
    res = await _ai_description("SMTFR", FULL_ATTRS, monkeypatch, _PARAGRAPH)
    assert res["status"] == "GENERATED"
    html = res["description"]
    # The SET and the COUNT of spec bullets, in the live listing order.
    assert re.findall(r"<li>(.*?)</li>", html) == EXPECTED_BULLETS
    # ...and the AI prose it was pressed for is still there.
    assert _PARAGRAPH in html


async def test_the_ai_written_smart_glass_body_survives_onto_the_pushed_doc(
    monkeypatch,
):
    """End to end: press the button, save, and the bullets are what Shopify's
    `descriptionHtml` gets. `_build_pim_doc` must not blank or replace them."""
    res = await _ai_description("SMTFR", FULL_ATTRS, monkeypatch, _PARAGRAPH)
    doc = pm._build_pim_doc(
        {
            "category": "SMARTGLASSES",
            "brand": "Ray-Ban",
            "attributes": dict(FULL_ATTRS),
            "description": res["description"],
            "sku": "SMTFRRAYMETA1",
            "pim_product_id": "pim-ai",
        }
    )
    assert re.findall(r"<li>(.*?)</li>", doc["description"]) == EXPECTED_BULLETS


async def test_the_ai_button_is_unchanged_for_every_other_category(monkeypatch):
    """Only smart glasses get the bullet shape. A sunglass still gets the
    store's deliberate Product Details / spec-table format."""
    res = await _ai_description(
        "SG",
        {"brand_name": "Ray-Ban", "model_no": "RB2140", "frame_material": "Acetate"},
        monkeypatch,
        _PARAGRAPH,
    )
    html = res["description"]
    assert "<h4>Product Details</h4>" in html
    assert "<h4>Technical Specifications</h4>" in html
    assert "<li>" not in html
