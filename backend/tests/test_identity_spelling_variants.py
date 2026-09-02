# ============================================================================
# One frame, written five ways, is ONE product
# ============================================================================
# Owner report (2026-09-02), verbatim:
#
#   "Ray-Ban RB4350 002/20, Ray-Ban 0RB 4350 002/20, Ray-Ban RB4350 002-20
#    do not become 3 different products"
#
# Measured against the identity key before the fix: those three spellings, plus
# two more that occur naturally, produced FOUR separate products. The separator
# fold already handled "/" vs "-", so cases 1 and 3 collided; everything else
# did not.
#
# Two things were wrong, both about how eyewear codes are really written:
#
#   * separators were folded to a SPACE rather than removed, so "RB4350" and
#     "RB 4350" were different identities, and
#   * Luxottica prints its catalogue codes with a leading zero -- 0RB4350 IS
#     RB4350 -- while the frame's own temple, the vendor invoice and the
#     purchase order disagree about whether to include it.
#
# The second rule is deliberately narrow: a leading zero is dropped only when a
# LETTER follows it. "0123" and "123" stay two products, because there is no
# evidence they are the same one.
#
# Verified against the live catalogue before landing: 77 rows resolved to 77
# distinct identities under both the old and the new rule, so nothing that is
# separate today was merged.

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.services import product_master as pm  # noqa: E402

K = pm.compute_identity_key

# Exactly the owner's three, plus two spellings that arrive from vendor sheets.
ONE_FRAME = [
    ("Ray-Ban", "RB4350", "002/20"),
    ("Ray-Ban", "0RB 4350", "002/20"),
    ("Ray-Ban", "RB4350", "002-20"),
    ("RayBan", "RB4350", "002 20"),
    ("RAY BAN", "0rb4350", "00220"),
]


def test_the_owners_three_spellings_are_one_product():
    keys = {K(*c) for c in ONE_FRAME[:3]}
    assert len(keys) == 1, (
        "the owner's three spellings of one Ray-Ban still produce "
        f"{len(keys)} products: {keys}"
    )


def test_every_natural_spelling_of_that_frame_is_one_product():
    keys = {K(*c) for c in ONE_FRAME}
    assert len(keys) == 1, {c: K(*c) for c in ONE_FRAME}


def test_the_luxottica_zero_prefix_is_dropped_only_before_a_letter():
    """The narrow half of the rule, in both directions."""
    assert K("Ray-Ban", "0RB4350", "002") == K("Ray-Ban", "RB4350", "002")
    # ...but a numeric model keeps its zero: nothing says 0123 is 123.
    assert K("Ray-Ban", "0123", "002") != K("Ray-Ban", "123", "002")
    # ...and only a LEADING zero goes; an internal one is part of the code.
    assert K("Ray-Ban", "RB0350", "002") != K("Ray-Ban", "RB350", "002")


def test_products_that_are_genuinely_different_stay_different():
    """The negative control. Without it, a normaliser that returned a constant
    would pass every test above."""
    base = K("Ray-Ban", "RB4350", "002/20")
    assert base != K("Ray-Ban", "RB4350", "003/20"), "different colour merged"
    assert base != K("Ray-Ban", "RB4351", "002/20"), "different model merged"
    assert base != K("Oakley", "RB4350", "002/20"), "different brand merged"
    assert base != K("Ray-Ban", "RB4350", "002/20", "55"), "sized variant merged"


def test_size_still_separates_two_sizes_of_one_frame():
    """Size is folded in only when present, so a sizeless row keeps the 3-part
    key. Both halves of that contract."""
    assert K("Ray-Ban", "RB4350", "002", "52") != K("Ray-Ban", "RB4350", "002", "55")
    assert K("Ray-Ban", "RB4350", "002") != K("Ray-Ban", "RB4350", "002", "52")


def test_an_identity_still_needs_a_brand_and_a_model():
    """Unchanged contract: a category without both (e.g. SERVICES) is not
    identity-deduped at all, rather than being deduped on an empty key."""
    assert K("", "RB4350", "002") is None
    assert K("Ray-Ban", "", "002") is None
    assert K(None, None, None) is None
    # A separators-only value normalises to empty and must not fake an identity.
    assert K("---", "RB4350", "002") is None


def test_the_similar_lookup_uses_the_same_normaliser():
    """The rule must not exist twice. If the live 'similar products' lookup
    normalised its query separately, the two would answer differently for
    exactly the spellings this file is about."""
    import inspect

    src = inspect.getsource(pm.find_similar_products)
    assert "normalise_identity_component" in src, (
        "find_similar_products is normalising its query some other way"
    )
    assert "compute_identity_key" in src


def test_no_regex_metacharacter_can_reach_the_key():
    """The prefix scan builds a regex from the normalised brand+model. The
    normaliser now strips everything outside [a-z0-9], so a product named
    'Ray-Ban (+) [x]' cannot inject one."""
    key = K("Ray-Ban (+) [x]", "RB^4350$", "002")
    assert key is not None
    # "|" is the key's OWN delimiter, so check the components, not the join.
    for part in key.split("|"):
        for ch in "^$.*+?()[]{}\\":
            assert ch not in part, (ch, part, key)
        assert part == "" or part.isalnum(), part
