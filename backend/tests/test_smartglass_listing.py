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
# "Rayban META RW4006 601/7150 50 Black SmartGlasses" states in its own bullets
# -- with ONE deliberate divergence: `shape` is a DIFFERENT word from
# `model_name`. The live Meta models happen to be named after their own
# silhouette ("Wayfarer" is both), and while the fixture said "Wayfarer" twice
# no test could tell which field the code had read: build_seo_title was reading
# the OPTIONAL shape in preference to the REQUIRED model_name and the suite
# passed anyway. `Rectangle` is live vocabulary too (the Blayzer Optics models
# carry shape_rectangle), and it is what makes the two fields separable.
#
# One shared value REMAINS, because the live product genuinely answers "Yes"
# twice: charging_case and prescription_ready. This dict cannot say which of
# the two a builder read -- test_charging_case_and_prescription_are_answered_
# by_their_own_fields splits them, and is the ONLY test that can.
FULL_ATTRS = {
    "brand_name": "Ray-Ban",
    "subbrand": "Meta",
    "model_name": "Wayfarer",
    "model_no": "RW4006",
    "colour_code": "601/7150",
    "generation": "Gen 2",
    "shape": "Rectangle",
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
    "Classic rectangle silhouette in acetate",
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


def test_a_minimal_old_style_save_still_publishes_nothing():
    """The PR's claim that a smart glass catalogued the old way still saves
    exactly as it does today is about what the save PRODUCES, not merely about
    whether validation passes. Brand + model + colour code and
    nothing else has no spec sheet to publish, so the storefront body must stay
    ABSENT, exactly as before this module existed.

    It must certainly never be the colour CODE: colour_code is the REQUIRED
    field and holds a manufacturer code ("601/7150") that no customer reads,
    while the optional frame_color holds the words the live listings print.
    Reading the code as a fallback made the DEFAULT path -- the one where only
    the required fields are filled -- publish "<h2>Ray-Ban Wayfarer - 601</h2>"
    as the entire product page body."""
    minimal = {"brand_name": "Ray-Ban", "model_name": "Wayfarer", "colour_code": "601"}
    pm.validate_attributes("SMARTGLASSES", minimal)

    assert sgl.build_description_html(minimal) == ""
    doc = pm._build_pim_doc(
        {
            "category": "SMARTGLASSES",
            "brand": "Ray-Ban",
            "attributes": dict(minimal),
            "sku": "SMTFRRAYWAY1",
            "pim_product_id": "pim-minimal",
        }
    )
    assert doc["description"] is None, doc["description"]

    # ...and the code is still not printed as a colour once there IS a spec
    # sheet for it to be printed on.
    with_spec = dict(minimal, camera_mp=12)
    html = sgl.build_description_html(with_spec)
    assert html.startswith("<h2>Ray-Ban Wayfarer</h2>"), html
    assert "601" not in html, html

    # Degenerate input publishes nothing rather than "<h2>(Gen)</h2>".
    assert sgl.build_headline({"generation": "Gen"}) == ""
    assert sgl.build_description_html({"generation": "Gen"}) == ""


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
        "rayban_meta",
        # 30 of the 36 live listings carry the generation token, and the 8
        # Optics models carry the prescription flag. Those 8 carry NO
        # product_sunglass -- see the next test.
        "gen2",
        "prescription_ready",
    }
    # No product line -> no brand-line token, and never a dangling "rayban_";
    # no generation and no prescription answer -> neither of those tokens.
    assert set(sgl.build_tags({"brand_name": "Ray-Ban"})) == {
        "product_smartglass",
        "product_sunglass",
    }


def test_a_prescription_model_is_not_filed_on_the_sunglasses_page():
    """The live `sunglass` collection (handle "sunglass", 23 products) is
    DISJUNCTIVE and one of its three rules is TAG = product_sunglass -- so that
    tag ALONE puts a product on the Sunglasses page. Read off the store
    2026-08-25: all 8 prescription_ready smart glasses carry NO
    product_sunglass, while the tinted Gen-2 models do. Emitting it
    unconditionally filed every clear-lens prescription model as a sunglass."""
    rx = dict(FULL_ATTRS, prescription_ready="Yes")
    tinted = dict(FULL_ATTRS, prescription_ready="No")

    assert "product_sunglass" not in sgl.build_tags(rx)
    assert "prescription_ready" in sgl.build_tags(rx)
    assert "product_sunglass" in sgl.build_tags(tinted)
    assert "prescription_ready" not in sgl.build_tags(tinted)
    # It is a smart glass either way.
    assert "product_smartglass" in sgl.build_tags(rx)
    assert "product_smartglass" in sgl.build_tags(tinted)


def test_tags_do_not_duplicate_what_the_attribute_tag_generator_emits():
    """shopify_tag_gen owns brand_/shape_/framematerial_/... -- this module must
    not grow a second copy of that registry."""
    from api.services.shopify_tag_gen import generate_attribute_tags

    attribute_tags = set(generate_attribute_tags("SMARTGLASSES", FULL_ATTRS))
    # brand_rayban, NOT brand_ray-ban: the live "RAY - BAN" smart collection on
    # bettervision.in rules on TAG = brand_rayban, and all 36 live smart glasses
    # carry it. The dashed slug still applies to every OTHER value (the live
    # store has an origin_made-in-italy collection) -- brand is the one
    # exception, and shopify_tag_gen.slugify_brand_value is the only place it
    # is spelled.
    assert "brand_rayban" in attribute_tags
    assert "brand_ray-ban" not in attribute_tags
    assert "shape_rectangle" in attribute_tags
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


def test_the_listing_names_the_model_never_the_generic_silhouette():
    """model_name is REQUIRED on SMARTGLASSES; `shape` is OPTIONAL and the form
    offers generic silhouettes (Round, Square, Cat-Eye, Aviator, ...). Reading
    shape FIRST put "Ray-Ban Meta Cat-Eye" on Google for a Skyler -- the
    product's own name never appeared -- while all 36 live seo.titles name the
    model. The silhouette already has its own spec bullet; it is a last-resort
    stand-in for the name, never an addition to it."""
    skyler = {
        "brand_name": "Ray-Ban",
        "subbrand": "Meta",
        "model_name": "Skyler",
        "shape": "Cat-Eye",
        "frame_color": "Shiny Black",
    }
    assert (
        sgl.build_seo_title(skyler)
        == "Ray-Ban Meta Skyler Shiny Black Smart Glasses | Better Vision"
    )
    assert sgl.build_headline(skyler) == "Ray-Ban Meta Skyler - Shiny Black"

    # No model name at all -> the shape is better than nothing, but only then.
    assert (
        sgl.build_seo_title({"brand_name": "Ray-Ban", "shape": "Cat-Eye"})
        == "Ray-Ban Cat-Eye Smart Glasses | Better Vision"
    )


def test_yes_no_fields_read_as_yes_no_not_as_text():
    no_case = dict(FULL_ATTRS, charging_case="No", prescription_ready="No")
    bullets = sgl.build_spec_bullets(no_case)
    assert "Up to ~4 hours per charge" in bullets
    assert not any("charging case" in b.lower() for b in bullets)
    assert not any("prescription" in b.lower() for b in bullets)


def test_charging_case_and_prescription_are_answered_by_their_own_fields():
    """FULL_ATTRS answers "Yes" to BOTH yes/no questions (the live RW4006
    genuinely does), and the test above answers "No" to both -- so a builder
    reading the WRONG field was invisible: _bullet_battery reading
    prescription_ready, and _bullet_prescription reading charging_case, each
    survived the whole suite. A fixture value shared by two fields cannot say
    which field the code read; split answers can, so both splits are pinned."""
    case_no_rx = dict(FULL_ATTRS, charging_case="Yes", prescription_ready="No")
    bullets = sgl.build_spec_bullets(case_no_rx)
    assert "Up to ~4 hours per charge, plus a portable charging case" in bullets
    assert not any("prescription" in b.lower() for b in bullets)

    rx_no_case = dict(FULL_ATTRS, charging_case="No", prescription_ready="Yes")
    bullets = sgl.build_spec_bullets(rx_no_case)
    assert "Up to ~4 hours per charge" in bullets  # exact bullet: no case clause
    assert not any("charging case" in b.lower() for b in bullets)
    assert "Prescription-ready (single-vision and progressive lenses)" in bullets


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
    assert {"product_smartglass", "rayban_meta"} <= set(doc["ecom"]["seo"]["tags"])
    assert "product_sunglass" not in doc["ecom"]["seo"]["tags"]  # prescription


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


# ---------------------------------------------------------------------------
# 6. The description the form sends is not dropped on the floor
# ---------------------------------------------------------------------------
# Everything above is worthless if the text never leaves the browser.
# ProductCreate did not model `description`, so pydantic silently discarded it
# on every create and shopify_push.build_product_input pushed an EMPTY body --
# the exact bug the `images` field had, pinned by test_product_images_persist.
# This is that same pin for `description`.


def _smartglass_create(**over):
    from api.routers import products as pr

    base = dict(
        category="SMTFR",
        brand="Ray-Ban",
        model="RW4006",
        attributes=dict(FULL_ATTRS),
        # Different on purpose: two fields sharing one value is the fixture
        # trap that hid the charging_case/prescription_ready mutants.
        mrp=41900,
        offer_price=37900,
    )
    base.update(over)
    return pr.ProductCreate(**base)


def test_a_written_description_rides_the_create_payload_to_the_door():
    from api.routers import products as pr

    body = "<h2>Ray-Ban Meta Wayfarer</h2>\n<ul>\n<li>12MP camera</li>\n</ul>"
    p = _smartglass_create(description=body)
    assert p.description == body
    assert pr._form_extra_fields(p)["description"] == body


def test_an_absent_description_stays_absent():
    """Nothing invented: no description sent means no description key, so the
    smart-glass generator in _build_pim_doc still sees a blank and fills it."""
    from api.routers import products as pr

    p = _smartglass_create()
    assert p.description is None
    assert "description" not in pr._form_extra_fields(p)


def test_the_description_reaches_shopifys_storefront_body():
    """The whole point of modelling it: shopify_push reads
    `ecom.seo.html or description`, so the projection must carry it through."""
    from api.services import shopify_push

    body = "<h2>Hand-written</h2>"
    doc = pm._build_pim_doc(
        {
            "category": "SMARTGLASSES",
            "brand": "Ray-Ban",
            "attributes": dict(FULL_ATTRS),
            "description": body,
            "sku": "SMTFRRAYMETA1",
            "pim_product_id": "pim-desc",
        }
    )
    assert doc["description"] == body
    assert shopify_push.build_product_input(doc, [])["descriptionHtml"] == body


# ---------------------------------------------------------------------------
# 7. The PUSH payload has to look like the listings already on the storefront
# ---------------------------------------------------------------------------
# Fetched from the Better Vision Shopify admin on 2026-08-25 -- all 36 live
# smart glasses, no exceptions:
#   productType : "SmartGlass"      (never "SMARTGLASSES", never "Smart Glasses")
#   brand token : "brand_rayban"    (never "brand_ray-ban")
#   generation  : "gen2"            on 30 of 36
#   prescription: "prescription_ready" on 8 of 36 (the Optics models)
# A model catalogued through the new form must land in the same buckets, or it
# is the only one of its kind the storefront filters cannot find.
LIVE_PRODUCT_TYPE = "SmartGlass"
LIVE_TAGS_SEEN = {
    "brand_rayban",
    "framematerial_acetate",
    "gen2",
    "prescription_ready",
    "product_smartglass",
    "product_sunglass",
    "rayban_meta",
    "shape_wayfarer",
}


def _pushed(attrs=None):
    from api.services import shopify_push

    doc = pm._build_pim_doc(
        {
            "category": "SMARTGLASSES",
            "brand": "Ray-Ban",
            "attributes": dict(FULL_ATTRS if attrs is None else attrs),
            "sku": "SMTFRRAYMETA1",
            "pim_product_id": "pim-push",
        }
    )
    return shopify_push.build_product_input(doc, [])


def test_pushed_product_type_is_the_one_the_storefront_uses():
    assert _pushed()["productType"] == LIVE_PRODUCT_TYPE


def test_the_brand_is_spelled_the_same_way_twice_in_one_payload():
    """The generated `rayban_meta` token and the attribute `brand_` token are
    the SAME brand; the live store spells both without the hyphen."""
    tags = set(_pushed()["tags"])
    assert "brand_rayban" in tags
    assert "brand_ray-ban" not in tags
    assert "rayban_meta" in tags


def test_generation_and_prescription_reach_the_storefront_facets():
    """30 of the 36 live listings carry `gen2`; the 8 Optics models carry
    `prescription_ready`. A Gen-2 model catalogued through the new form used to
    be the only Gen-2 product on the store without the tag."""
    tags = set(_pushed()["tags"])
    assert {"gen2", "prescription_ready"} <= tags


def test_no_generation_no_gen_tag():
    attrs = {k: v for k, v in FULL_ATTRS.items() if k != "generation"}
    attrs["prescription_ready"] = "No"
    tags = set(_pushed(attrs)["tags"])
    assert not [t for t in tags if t.startswith("gen")]
    assert "prescription_ready" not in tags


def test_every_generated_tag_is_vocabulary_the_live_store_already_uses():
    """Nothing invented: every token this payload adds beyond the attribute
    generator's own `<prefix>_<value>` tokens must be one the store has."""
    generated = set(sgl.build_tags(FULL_ATTRS))
    assert generated <= LIVE_TAGS_SEEN, sorted(generated - LIVE_TAGS_SEEN)


def test_no_bullet_ever_starts_lower_case():
    """Every bullet on the live listings opens on a capital. With a camera TYPE
    but no megapixel count the sentence used to open on the lower-cased type
    ("ultra-wide camera for photos and 1080p video") -- one lower-case line in
    an otherwise capitalised list. Swept over every fill pattern of the fields
    that feed a bullet, not just the one that was reported."""
    import itertools

    keys = [
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
        "shape",
        "frame_material",
        "prescription_ready",
    ]
    offenders = []
    for mask in itertools.product((True, False), repeat=len(keys)):
        attrs = {k: FULL_ATTRS[k] for k, keep in zip(keys, mask) if keep}
        for bullet in sgl.build_spec_bullets(attrs):
            if bullet[:1].isalpha() and not bullet[:1].isupper():
                offenders.append(bullet)
    assert not set(offenders), sorted(set(offenders))
