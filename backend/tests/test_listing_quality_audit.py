"""Listing-quality audit (2026-10-22): what IMS sends before ~449 products
auto-publish to bettervision.in. One test per finding; each fails when its
fix is reverted."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("JWT_SECRET_KEY", "test_x")

from bson import ObjectId  # noqa: E402

from api.services.product_description import (  # noqa: E402
    build_model_description_html,
    build_shopify_description_html,
    default_model_paragraph,
)
from api.services.shopify_push import collection_registry as reg  # noqa: E402
from api.services.shopify_push.collections import build_collection_input  # noqa: E402
from api.services.shopify_push.media import PHOTO_COLOURS, photo_alts  # noqa: E402
from api.services.shopify_push.product_input import LISTING_TAGS, build_product_input, ims_product_tags  # noqa: E402
from api.services.shopify_tag_gen import arrival_tag, listing_tags, week_tag  # noqa: E402

# The prod shape of SGCARRERACARRERA804S807IR: stored copy minted before the
# brand was stripped from the model.
CARRERA = {
    "sku": "SGCARRERACARRERA804S807IR",
    "brand": "Carrera",
    "category": "SUNGLASS",
    "title": "Carrera 804/S Square Sunglasses - Black",
    "attributes": {"brand_name": "Carrera", "model_no": "CARRERA 804/S", "colour_code": "807IR",
                   "lens_size": "53", "temple_length": "145", "polarization": "No"},
    "description": (
        "<h4>Product Details</h4>\n<h5>Model Number: Carrera CARRERA 804/S CARRERA 807IR 53 Sunglass</h5>\n"
        "<p>Reflect your style with the Carrera Carrera 804/S. Enjoy best discounts and guaranteed authentic "
        "and genuine products, ensuring you have the ultimate eyewear shopping experience. Discover your "
        "perfect pair at Better Vision today!</p>\n<table>\n<tbody>\n<tr>\n<td>Temple Length</td>\n<td>145</td>\n"
        "</tr>\n<tr>\n<td>Polarization</td>\n<td>No</td>\n</tr>\n<tr>\n<td>Model No</td>\n<td>CARRERA 804/S</td>\n"
        "</tr>\n</tbody>\n</table>"
    ),
    "ecom": {"seo": {"title": "Carrera Carrera 804/S Square Men's Sunglasses - Black",
                     "description": "Buy Carrera Carrera 804/S Square Men's Sunglasses - Black online."}},
}


# 1. Doubled brand / model line ------------------------------------------------


def test_1_the_push_never_sends_a_doubled_brand_and_the_model_line_is_readable():
    inp = build_product_input(CARRERA, [])
    assert build_product_input({**CARRERA, "title": "Carrera Carrera 804/S Square"}, [])["title"] == "Carrera 804/S Square"
    assert inp["seo"]["title"] == "Carrera 804/S Square Men's Sunglasses - Black"
    assert inp["seo"]["description"].startswith("Buy Carrera 804/S Square")
    html = inp["descriptionHtml"]
    assert "<h5>Model Number: Carrera 804/S</h5>" in html
    assert "<td>Model No</td>\n<td>Carrera 804/S</td>" in html
    assert "carrera carrera" not in html.lower()


def test_1_new_bodies_use_the_one_model_line():
    html = build_shopify_description_html("Sunglass", {"brand_name": "Carrera", "model_no": "carrera 3016/s",
                                                       "colour_code": "807/R", "lens_size": "55"}, "")
    assert "<h5>Model Number: Carrera 3016/S</h5>" in html
    model = build_model_description_html("Sunglasses", {"brand_name": "Carrera", "model_no": "Carrera 3016/S"},
                                         [{"sku": "A", "attributes": {"model_no": "Carrera 3016/S"}}], "")
    assert "<h5>Model Number: Carrera 3016/S</h5>" in model


# 2. Alt text --------------------------------------------------------------


def test_2_alt_text_names_the_listing_its_colour_and_view_never_empty():
    view = {"title": "Carrera 3016/S", PHOTO_COLOURS: {"u1": "Black", "u2": "Black", "u3": "Gold"}}
    assert photo_alts(view, ["u1", "u2", "u3"]) == {
        "u1": "Carrera 3016/S - Black - view 1",
        "u2": "Carrera 3016/S - Black - view 2",
        "u3": "Carrera 3016/S - Gold - view 1",
    }
    assert photo_alts({}, ["u"]) == {"u": "Eyewear - view 1"}
    # A one-colour title already names its colour: never "Black - Black".
    one = {"title": "Carrera 1012/S Sunglasses - Black", "attributes": {"frame_color": "Black"}}
    assert photo_alts(one, ["u"]) == {"u": "Carrera 1012/S Sunglasses - Black - view 1"}
    long = photo_alts({"title": "X" * 300}, ["u"])["u"]
    assert len(long) <= 120 and long.endswith(" - view 1")


# 3. Store promise -----------------------------------------------------------


def test_3_no_discount_promise_at_full_mrp():
    para = default_model_paragraph("Sunglasses", {"brand_name": "Carrera", "model_no": "804/S"}, 2, 1)
    assert "100% genuine, brand-authorised." in para and "discount" not in para.lower()
    html = build_product_input(CARRERA, [])["descriptionHtml"]
    assert "discount" not in html.lower() and "100% genuine, brand-authorised." in html


# 4. Collection SEO ------------------------------------------------------------


def _facts():
    now = datetime.now(timezone.utc)
    return [
        {"type": "Sunglass", "prices": [2500.0], "brand": "Carrera",
         "tags": {"gender_men", "shape_aviator", "polarization_yes", "brand_carrera", week_tag(now)}},
        {"type": "Spectacles", "prices": [12000.0], "brand": "Idee",
         "tags": {"gender_women", "shape_round", "brand_idee", week_tag(now - timedelta(days=60))}},
    ]


def test_4_every_collection_kind_has_an_seo_title_and_a_short_description():
    defs = reg.derive_definitions(_facts(), {"brand_carrera": "Carrera"})
    kinds = {d["key"].split(":")[0] for d in defs}
    assert kinds == {"type", "gender", "shape", "polarized", "brand", "price", "new-arrivals"}
    for d in defs:
        assert d["seo_title"].endswith(" | Better Vision") and d["seo_title"] != " | Better Vision", d
        assert 0 < len(d["seo_description"]) <= 155, d
    by = {d["handle"]: d for d in defs}
    assert by["polarized-sunglasses"]["seo_title"] == "Polarized Sunglasses | Better Vision"
    assert by["mens-sunglasses"]["seo_title"] == "Sunglasses for Men | Better Vision"
    sent = build_collection_input({**by["polarized-sunglasses"], "collection_type": "SMART"})
    assert sent["seo"] == {"title": by["polarized-sunglasses"]["seo_title"],
                           "description": by["polarized-sunglasses"]["seo_description"]}


# 5. New Arrivals --------------------------------------------------------------


def test_5_new_arrivals_holds_only_the_last_weeks_catalogue():
    now = datetime.now(timezone.utc)
    weekly = [{"type": "Sunglass", "prices": [1.0], "brand": "", "tags": {week_tag(now - timedelta(weeks=k))}}
              for k in (1, 2, 3)]
    defs = {d["handle"]: d for d in reg.derive_definitions(_facts() + weekly, {})}
    na = defs["new-arrivals"]
    assert na["count"] == 4, "the newest 4 arrival weeks; the 60-day-old listing is out"
    assert na["disjunctive"] is True and len(na["rules"]) == reg.NEW_ARRIVALS_WEEKS
    assert all(r["field"] == "tag" and r["value"].startswith("arrived_") for r in na["rules"])
    # The arrival week comes from created_at, else the Mongo ObjectId's time.
    when = datetime(2026, 10, 22, tzinfo=timezone.utc)
    assert arrival_tag({"created_at": when}) == "arrived_2026w43"
    assert arrival_tag({"_id": ObjectId.from_datetime(when)}) == "arrived_2026w43"
    assert arrival_tag({"created_at": when, "category": "SUNGLASS", "attributes": {}}) in ims_product_tags(
        {"created_at": when, "category": "SUNGLASS", "attributes": {}})


# 6. Spec table ------------------------------------------------------------------


def test_6_measurements_carry_mm_and_bridge_shows_when_filled():
    html = build_shopify_description_html("Sunglass", {"brand_name": "Carrera", "model_no": "CARRERA 804/S",
                                                       "lens_size": "53", "bridge_width": "18",
                                                       "temple_length": "145"}, "")
    for row in ("<td>Size</td>\n<td>53 mm</td>", "<td>Bridge</td>\n<td>18 mm</td>",
                "<td>Temple Length</td>\n<td>145 mm</td>", "<td>Model No</td>\n<td>Carrera 804/S</td>"):
        assert row in html
    # Stored bodies are fixed on the way out (idempotent).
    html = build_product_input(CARRERA, [])["descriptionHtml"]
    assert "<td>Temple Length</td>\n<td>145 mm</td>" in html and "145 mm mm" not in html


# 7. Polarized per colour ----------------------------------------------------------


def test_7_a_listing_is_polarized_only_when_every_colour_is():
    yes = ["polarization_yes", "brand_carrera"]
    no = ["polarization_no", "brand_carrera"]
    assert "polarization_yes" in listing_tags([yes, yes])
    mixed = listing_tags([yes, no])
    assert "polarization_yes" not in mixed and "polarization_no" in mixed
    # The lead's own tags never re-add it on the view the push sends.
    lead = {"category": "SUNGLASS", "attributes": {"polarization": "Yes"}, LISTING_TAGS: mixed}
    assert "polarization_yes" not in ims_product_tags(lead)
    rules = next(d["rules"] for d in reg.derive_definitions(_facts(), {}) if d["key"].startswith("polarized"))
    fact = {"type": "Sunglass", "tags": set(mixed), "prices": [1.0], "brand": ""}
    assert not reg._matches(fact, rules)


# 8. Non-polarized wording -----------------------------------------------------------


def test_8_a_non_polarized_lens_is_described_without_any_polar_word():
    for value in ("No", "Non-Polarized", "false"):
        html = build_shopify_description_html("Sunglass", {"brand_name": "B", "polarization": value}, "")
        assert "polar" not in html.lower(), html
        assert "<td>Lens Type</td>\n<td>Standard</td>" in html
    html = build_shopify_description_html("Sunglass", {"brand_name": "B", "polarization": "Yes"}, "")
    assert "<td>Polarization</td>\n<td>Yes</td>" in html
    assert "polar" not in build_product_input(CARRERA, [])["descriptionHtml"].lower()
