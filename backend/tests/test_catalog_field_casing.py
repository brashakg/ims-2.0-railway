# ============================================================================
# Type in capitals, store the right case
# ============================================================================
# Owner request (2026-09-02): "on the catalog page make all data entry fields
# CAPS LOCK, so that entry is simple and use your formulas to make them proper
# grammer".
#
# It cannot be one rule, which is the whole point of this file. A blanket title
# pass stores model number RB4350 as "Rb4350" and wrecks colour code 002/20. So
# codes stay in capitals, words get title-cased, and anything unclassified is
# left EXACTLY as received - the field registry is rewritten at runtime from the
# server dictionary, so an unlisted key must degrade to today's behaviour rather
# than to a guess.
#
# Three rules earn their own tests below because getting them wrong is silent:
#   * MIXED CASE IS INTENT. "Radar EV Path" and "boAt" carry information no rule
#     can reconstruct, so a value already containing both cases is untouched.
#   * ONLY THE SUBMITTED KEYS. Both edit doors merge the stored attribute bag
#     with the patch before normalising, so casing the merged dict would rewrite
#     every value the owner had corrected by hand, on every save.
#   * CASE BEFORE THE DICTIONARY. enforce_dictionary_values canonicalises brand
#     values to the owner's Settings spelling; casing afterwards would turn its
#     "boAt" back into "Boat".

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.services.product_master import apply_field_casing  # noqa: E402
from api.services.product_naming import smart_title  # noqa: E402


# --- the caser itself, on real eyewear strings ---------------------------

@pytest.mark.parametrize(
    "typed,stored",
    [
        # codes and part numbers survive untouched
        ("RB-4350", "RB-4350"),
        ("CR-39", "CR-39"),
        ("G-15", "G-15"),
        ("UV400", "UV400"),
        ("TR90", "TR90"),
        ("002/20", "002/20"),
        ("5G", "5G"),
        # a word glued to a code still gets cased
        ("BLACK/002", "Black/002"),
        ("GREY/G-15", "Grey/G-15"),
        # plain words
        ("MATTE BLACK", "Matte Black"),
        ("RED", "Red"),
        ("ACETATE", "Acetate"),
        ("STAINLESS STEEL", "Stainless Steel"),
        ("CAT-EYE", "Cat-Eye"),
        # acronyms are not words
        ("USA", "USA"),
        ("IND", "IND"),
        ("XXL", "XXL"),
        ("XS", "XS"),
        # small words go down only in the middle
        ("MADE IN ITALY", "Made in Italy"),
        ("GOLD ON BROWN", "Gold on Brown"),
        # possessives and known spellings
        ("WOMEN'S", "Women's"),
        ("O'NEILL", "O'Neill"),
        ("RAY-BAN", "Ray-Ban"),
        ("RAY BAN", "Ray-Ban"),
        ("RAYBAN", "Ray-Ban"),
        ("VINCENT CHASE", "Vincent Chase"),
        ("BAUSCH & LOMB", "Bausch & Lomb"),
        # a grade printed on the lens: the digit rule protects the whole token
        ("POLARISED 3P", "Polarised 3P"),
    ],
)
def test_smart_title_on_real_eyewear_strings(typed, stored):
    assert smart_title(typed) == stored


def test_a_model_number_is_never_title_cased():
    """The single failure that would make this feature worse than nothing."""
    assert smart_title("RB4350") == "RB4350"
    assert apply_field_casing({"model_no": "RB4350"})["model_no"] == "RB4350"
    assert apply_field_casing({"model_no": "rb4350"})["model_no"] == "RB4350"


# --- the field classification --------------------------------------------

def test_codes_go_up_and_words_go_title():
    got = apply_field_casing({
        "model_no": "0rb 4350",
        "colour_code": "002/20",
        "brand_name": "RAY-BAN",
        "frame_color": "MATTE BLACK",
        "frame_material": "ACETATE",
    })
    assert got["model_no"] == "0RB 4350"
    assert got["colour_code"] == "002/20"
    assert got["brand_name"] == "Ray-Ban"
    assert got["frame_color"] == "Matte Black"
    assert got["frame_material"] == "Acetate"


def test_an_unclassified_field_is_left_exactly_as_received():
    """The default must be DO NOTHING. getCategoryFields rewrites the field list
    at runtime from the server registry, so a key nobody classified will appear
    - and it must not start storing capitals for ever."""
    got = apply_field_casing({"some_registry_field_added_in_settings": "BLUE CUT"})
    assert got["some_registry_field_added_in_settings"] == "BLUE CUT"


def test_non_strings_and_blanks_pass_through():
    got = apply_field_casing({"lens_size": 54, "tint": "", "shape": None})
    assert got["lens_size"] == 54
    assert got["tint"] == ""
    assert got["shape"] is None


def test_an_iso_country_code_is_not_turned_into_a_word():
    """"IT" is Italy, not the pronoun. Any real country name is longer."""
    for code in ("IT", "US", "CN", "JP", "IND", "UAE"):
        assert apply_field_casing({"country_of_origin": code})["country_of_origin"] == code
    assert apply_field_casing({"country_of_origin": "ITALY"})["country_of_origin"] == "Italy"


# --- the three silent rules ----------------------------------------------

def test_a_value_already_mixed_case_is_never_rewritten():
    """Mixed case is INTENT. "Radar EV Path" and "boAt" carry information the
    rule cannot reconstruct once destroyed."""
    for value in ("Radar EV Path", "boAt", "ic! berlin", "McQ", "Ray-Ban"):
        assert apply_field_casing({"model_name": value})["model_name"] == value


def test_only_the_keys_this_submit_carries_are_cased():
    """The edit doors merge the stored bag with the patch. Casing the merged
    dict would rewrite hand-corrected values on every save - so the doors pass
    the patch keys and everything else must survive untouched."""
    stored_plus_patch = {
        "brand_name": "RAY-BAN",      # not in this submit -> untouched
        "model_name": "JUSTIN",       # not in this submit -> untouched
        "frame_color": "MATTE BLACK",  # this submit -> cased
    }
    got = apply_field_casing(stored_plus_patch, only={"frame_color"})
    assert got["frame_color"] == "Matte Black"
    assert got["brand_name"] == "RAY-BAN"
    assert got["model_name"] == "JUSTIN"


def test_the_create_door_cases_everything():
    """only=None is the create door, where every value is new."""
    got = apply_field_casing({"brand_name": "RAY-BAN", "frame_color": "MATTE BLACK"})
    assert got == {"brand_name": "Ray-Ban", "frame_color": "Matte Black"}


def test_casing_runs_before_the_dictionary_pass_not_after():
    """Order is load-bearing. enforce_dictionary_values canonicalises a brand to
    the owner's Settings spelling; running the caser afterwards would turn its
    "boAt" back into "Boat". Guard the ORDER, since both orderings produce a
    plausible-looking result on any brand that IS plain title case."""
    import inspect

    from api.services import product_master as pm

    # Comments are stripped first. The comment ABOVE the casing call explains
    # why it must precede the dictionary pass, and so it mentions
    # enforce_dictionary_values earlier in the file than the call it guards -
    # an assertion over the raw source matches the prose and reports the wrong
    # order. Match the CALLS, not the explanation of them.
    src = "\n".join(
        line for line in inspect.getsource(pm.normalise_payload).splitlines()
        if not line.strip().startswith("#")
    )
    cased = src.index("apply_field_casing(")
    enforced = src.index("enforce_dictionary_values(")
    assert cased < enforced, (
        "the casing pass must run BEFORE enforce_dictionary_values, or it "
        "re-mangles the values Brand Master just canonicalised"
    )


def test_separators_inside_a_code_are_preserved_verbatim():
    """The SKU builder keeps "/" and "-" in the colour segment, so normalising
    separators here would change the SKU minted for every new frame."""
    assert apply_field_casing({"colour_code": "002/20"})["colour_code"] == "002/20"
    assert apply_field_casing({"colour_code": "002-20"})["colour_code"] == "002-20"


def test_casing_cannot_move_a_product_identity():
    """The duplicate key lower-cases and strips everything, so casing must be
    invisible to it. If this ever fails, re-casing would start creating
    duplicates."""
    from api.services.product_master import compute_identity_key as K

    typed = {"brand_name": "RAY-BAN", "model_no": "RB4350", "colour_code": "002/20"}
    cased = apply_field_casing(typed)
    assert K(typed["brand_name"], typed["model_no"], typed["colour_code"]) == K(
        cased["brand_name"], cased["model_no"], cased["colour_code"]
    )
