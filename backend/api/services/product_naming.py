"""Deterministic product display-name + SEO builders (PURE service).

No DB, no IO, no side effects. Given a product-like dict -- the `products`
spine doc, a catalog_products PIM doc, or a create payload -- these functions
derive a human-readable display name plus SEO title/handle/description. They
are consumed by:

  * the create door (product_master.normalise_payload) to auto-mint a `name`
    when the payload leaves it blank, so a product never lands on a bill / POS
    line / online catalog with an empty name; and
  * the online-catalog staging (product_master._stage_catalog_draft) + the
    backfill runbook to fill ecom.seo.title / ecom.handle.

SEO shape (good eyewear e-commerce practice):

    {Brand} {Model} {Shape} {Gender} {Category-word} - {Colour}

e.g.  "Ray-Ban RB3025 Aviator Sunglasses - Polished Gold"
      "Vogue VO5239 Cat Eye Women's Eyeglasses - Tortoise"

Every input degrades gracefully -- a missing brand/model/shape/colour simply
drops from the string; the result is title-cased, single-spaced, deterministic
and clamped to MAX_NAME_LEN (70) characters. Tokens that carry a digit (model
numbers like RB3025, sizes like 52) are preserved verbatim so title-casing can
never mangle a model number into "Rb3025".

ASCII only (Windows cp1252) -- no emoji, no smart quotes.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Google truncates a page <title> around 60-70 chars; a display name on a bill
# stays readable well under that. 70 is the shared hard ceiling.
MAX_NAME_LEN = 70
# Meta descriptions are truncated by Google around 155-160 chars.
MAX_DESC_LEN = 160


# ---------------------------------------------------------------------------
# Category -> shopper-facing SEO word
# ---------------------------------------------------------------------------
# The `products.category` long-form value maps to the term a shopper actually
# searches (Lenskart / EyeMyEye taxonomy). SERVICES carries no category word --
# a service's name IS its display identity (attributes.name).
_CATEGORY_WORD: Dict[str, str] = {
    "FRAME": "Eyeglasses",
    "SUNGLASS": "Sunglasses",
    "OPTICAL_LENS": "Lenses",
    "READING_GLASSES": "Reading Glasses",
    "CONTACT_LENS": "Contact Lenses",
    "COLORED_CONTACT_LENS": "Colour Contact Lenses",
    "WATCH": "Watch",
    "SMARTWATCH": "Smartwatch",
    "SMARTGLASSES": "Smart Glasses",
    "WALL_CLOCK": "Wall Clock",
    "ACCESSORIES": "Accessory",
    "SERVICES": "",
    "HEARING_AID": "Hearing Aid",
}

# Short SKU-prefix codes + common alternates -> canonical key. Kept local so
# this module has ZERO dependency on product_master (product_master imports
# THIS module; the reverse would be a cycle). Only the forms that plausibly
# reach a name builder are covered; anything unknown falls back to a
# title-cased raw category.
_CATEGORY_ALIASES: Dict[str, str] = {
    "FR": "FRAME",
    "FRAMES": "FRAME",
    "SG": "SUNGLASS",
    "SUNGLASSES": "SUNGLASS",
    "LS": "OPTICAL_LENS",
    "LENS": "OPTICAL_LENS",
    "OPTICAL_LENSES": "OPTICAL_LENS",
    "RX_LENS": "OPTICAL_LENS",
    "RX_LENSES": "OPTICAL_LENS",
    "SPECTACLE_LENS": "OPTICAL_LENS",
    "RG": "READING_GLASSES",
    "CL": "CONTACT_LENS",
    "CONTACT_LENSES": "CONTACT_LENS",
    "CCL": "COLORED_CONTACT_LENS",
    "COLOUR_CONTACT_LENS": "COLORED_CONTACT_LENS",
    "COLOUR_CONTACT_LENSES": "COLORED_CONTACT_LENS",
    "COLORED_CONTACT_LENSES": "COLORED_CONTACT_LENS",
    "WT": "WATCH",
    "WATCHES": "WATCH",
    "WRIST_WATCH": "WATCH",
    "SMTWT": "SMARTWATCH",
    "SMART_WATCH": "SMARTWATCH",
    "SMTFR": "SMARTGLASSES",
    "SMTSG": "SMARTGLASSES",
    "SMART_FRAME": "SMARTGLASSES",
    "CK": "WALL_CLOCK",
    "CLOCK": "WALL_CLOCK",
    "ACC": "ACCESSORIES",
    "ACCESSORY": "ACCESSORIES",
    "HA": "HEARING_AID",
    "SVC": "SERVICES",
    "SERVICE": "SERVICES",
}

# Gender token -> qualifier inserted before the category word. Unisex is the
# neutral default and is deliberately OMITTED (adding "Unisex" to every name is
# SEO noise). Only a meaningful gender narrows the title.
_GENDER_QUALIFIER: Dict[str, str] = {
    "MEN": "Men's",
    "MENS": "Men's",
    "MAN": "Men's",
    "MALE": "Men's",
    "GENTS": "Men's",
    "GENT": "Men's",
    "M": "Men's",
    "WOMEN": "Women's",
    "WOMENS": "Women's",
    "WOMAN": "Women's",
    "FEMALE": "Women's",
    "LADIES": "Women's",
    "LADY": "Women's",
    "W": "Women's",
    "F": "Women's",
    "KID": "Kids",
    "KIDS": "Kids",
    "CHILD": "Kids",
    "CHILDREN": "Kids",
    "BOY": "Kids",
    "BOYS": "Kids",
    "GIRL": "Kids",
    "GIRLS": "Kids",
    "JUNIOR": "Kids",
}

# Colour source keys, readable NAMES before opaque CODES. A frame_color like
# "Polished Gold" wins over a colour_code like "1109/71".
_COLOUR_KEYS = (
    "frame_color",
    "frame_colour",
    "colour_name",
    "color_name",
    "lens_colour",
    "lens_color",
    "dial_color",
    "dial_colour",
    "strap_color",
    "strap_colour",
    "colour_code",
    "color",
    "colour",
)

# A "bare code" colour (e.g. "1109/71", "071", "C3") reads badly in a title, so
# it is skipped when a readable colour is unavailable -- better no colour than
# "- 1109/71".
_BARE_CODE_RE = re.compile(r"^[0-9][0-9/\-.\s]*$")

_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Low-level helpers (pure)
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    """Trim + collapse internal whitespace; non-strings coerced. '' for blanks."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    return _WS_RE.sub(" ", s)


def _attrs(product: Dict[str, Any]) -> Dict[str, Any]:
    a = product.get("attributes") if isinstance(product, dict) else None
    return a if isinstance(a, dict) else {}


def _first(*values: Any) -> str:
    """First non-blank cleaned value."""
    for v in values:
        c = _clean(v)
        if c:
            return c
    return ""


# Tokens that are NOT words and must survive a title pass untouched. Length is
# not a usable proxy: "RED" and "USA" are both three letters and only one of
# them is a word, so the set is explicit. Sizes live here too -- XXL would
# otherwise come back "Xxl".
_ACRONYMS = frozenset({
    # places
    "USA", "UK", "UAE", "PRC", "IND", "EU", "US", "IT", "CN", "JP", "DE", "FR",
    "VN", "TH", "KR", "TW", "MY", "ID", "PH", "BD", "LK", "NP",
    # optical + materials
    "UV", "PC", "TR", "CR", "AR", "HC", "HMC", "SHMC", "EMI", "PD", "SPH",
    "CYL", "ADD", "BC", "DIA", "OD", "OS", "OU", "RX", "CT", "MG", "ML",
    # sizes
    "XS", "XL", "XXL", "XXXL",
    # roman numerals that appear in model names
    "II", "III", "IV", "VI", "VII", "VIII", "IX",
    # commerce
    "MRP", "GST", "HSN", "SKU", "UPC", "GTIN", "EAN", "QC", "OEM", "ODM",
})

# Spellings a plain title pass gets wrong. Keyed on the lowercase letters-and-
# digits form so one row catches every separator spelling ("RAY-BAN", "RAY BAN",
# "RayBan"). Deliberately STATIC: seeding this from the database at runtime
# would make the module's title-caser impure and make a generated product name
# depend on which brands happen to be configured.
_EXACT_CASE = {
    "rayban": "Ray-Ban",
    "boat": "boAt",
    "bauschlomb": "Bausch & Lomb",
    "johnjacobs": "John Jacobs",
    "vincentchase": "Vincent Chase",
    "icberlin": "ic! berlin",
    "oneill": "O'Neill",
    "mcq": "McQ",
}

# Lowercased mid-phrase only, never first or last.
_SMALL_WORDS = frozenset({
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
    "or", "the", "to", "with",
})

_SEP_SPLIT = re.compile(r"([^0-9A-Za-z]+)")


def _case_segment(seg: str, *, digit_token: bool) -> str:
    """Case ONE alphanumeric run inside a token.

    In a token that carries a digit somewhere, a short run is almost always a
    code prefix ("RB" in RB-4350, "G" in G-15, "CR" in CR-39) and must survive
    verbatim, while a long run is a word ("BLACK" in BLACK/002). Four characters
    is the boundary: no eyewear model prefix is that long and no colour word is
    shorter. In a token with NO digit the acronym set decides instead, because
    there length tells you nothing -- "RED" and "USA" are both three letters.
    """
    if not seg or not seg.isalpha():
        return seg
    if digit_token:
        return seg.capitalize() if len(seg) >= 4 else seg
    if seg.upper() in _ACRONYMS:
        return seg.upper()
    return seg.capitalize()


def _title_token(token: str) -> str:
    """Title-case one whitespace-free token.

    A model number, colour code or measurement must never be mangled, so a
    token containing a digit is cased run-by-run under the rule in
    _case_segment rather than word-wise: "RB-4350" survives, "CR-39" survives,
    and "BLACK/002" still becomes "Black/002" instead of staying in capitals.
    Separators are preserved exactly as typed -- "002/20" must not become
    "002-20", because the SKU builder keeps those characters.
    """
    if not token:
        return token
    exact = _EXACT_CASE.get(re.sub(r"[^a-z0-9]+", "", token.lower()))
    if exact:
        return exact
    digit_token = any(ch.isdigit() for ch in token)
    parts = [p for p in _SEP_SPLIT.split(token) if p != ""]
    out = []
    for i, part in enumerate(parts):
        if _SEP_SPLIT.fullmatch(part):
            out.append(part)
            continue
        # A short run straight after an apostrophe is a possessive or a
        # contraction, not a new word: "WOMEN'S" is "Women's", never "Women'S".
        prev = parts[i - 1] if i else ""
        if i and prev and prev[-1] in "'’" and part.isalpha() and len(part) <= 2:
            out.append(part.lower())
            continue
        out.append(_case_segment(part, digit_token=digit_token))
    return "".join(out)


def _smart_title(text: str) -> str:
    """Title-case a full string word-by-word, preserving the ' - ' separator
    and any digit-bearing tokens.

    A whole-string exception is tried first so a multi-word brand written in
    capitals ("VINCENT CHASE") is recognised before it is split into tokens
    that individually mean nothing.
    """
    exact = _EXACT_CASE.get(re.sub(r"[^a-z0-9]+", "", str(text or "").lower()))
    if exact:
        return exact
    words = [w for w in str(text or "").split(" ") if w]
    out = [w if w == "-" else _title_token(w) for w in words]
    # Small words go lowercase in the middle of a phrase, never at either end,
    # and never if the token was matched as an acronym or a code.
    for i in range(1, len(out) - 1):
        w = out[i]
        if w.isalpha() and w.upper() not in _ACRONYMS and w.lower() in _SMALL_WORDS:
            out[i] = w.lower()
    return " ".join(out)


def smart_title(text: str) -> str:
    """Public name for the ONE title-caser. See _smart_title."""
    return _smart_title(text)


def _clamp(text: str, limit: int) -> str:
    """Clamp to `limit` chars on a word boundary, trimming a dangling
    separator/hyphen so a truncated title never ends in ' -'."""
    text = _WS_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" -/,").strip()


def resolve_category_word(category: Any) -> str:
    """Shopper-facing SEO word for a category (any form). Unknown -> a
    title-cased version of the raw value; blank -> ''."""
    raw = _clean(category)
    if not raw:
        return ""
    key = raw.upper().replace("-", "_").replace(" ", "_")
    canonical = key if key in _CATEGORY_WORD else _CATEGORY_ALIASES.get(key)
    if canonical is not None:
        return _CATEGORY_WORD.get(canonical, "")
    return _smart_title(raw.replace("_", " ").lower())


def _gender_qualifier(product: Dict[str, Any]) -> str:
    a = _attrs(product)
    raw = _first(a.get("gender"), a.get("gender_label"), product.get("gender"))
    if not raw:
        return ""
    return _GENDER_QUALIFIER.get(raw.upper().replace(" ", "").replace("'", ""), "")


def _colour(product: Dict[str, Any]) -> str:
    """First READABLE colour name across the known keys. A bare code (e.g.
    "1109/71", "071") is skipped -- better no colour than an opaque code in the
    title."""
    a = _attrs(product)
    for key in _COLOUR_KEYS:
        val = _clean(a.get(key))
        if not val and key in ("color", "colour"):
            val = _clean(product.get(key))
        if not val or _BARE_CODE_RE.match(val):
            continue
        return val
    return ""


def _brand(product: Dict[str, Any]) -> str:
    a = _attrs(product)
    return _first(product.get("brand"), a.get("brand_name"), a.get("brand"))


def _model(product: Dict[str, Any]) -> str:
    a = _attrs(product)
    return _first(
        a.get("model_no"),
        product.get("model"),
        a.get("model_name"),
        a.get("model"),
    )


def _explicit_name(product: Dict[str, Any]) -> str:
    """An already-set name wins (SERVICES carry their name in attributes.name)."""
    a = _attrs(product)
    return _first(product.get("name"), product.get("title"), a.get("name"))


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_product_name(product: Dict[str, Any]) -> str:
    """Deterministic display name for a product-like dict.

    "{Brand} {Model} {Shape} {Gender} {Category-word} - {Colour}", title-cased,
    single-spaced, <= MAX_NAME_LEN. Missing pieces drop out. An explicit name /
    title on the doc (or attributes.name for a SERVICE) is honoured verbatim
    (cleaned + clamped) rather than rebuilt.
    """
    if not isinstance(product, dict):
        return ""

    explicit = _explicit_name(product)
    if explicit:
        return _clamp(_smart_title(explicit), MAX_NAME_LEN)

    a = _attrs(product)
    brand = _brand(product)
    model = _model(product)
    shape = _first(a.get("shape"), a.get("frame_type"))
    gender = _gender_qualifier(product)
    category_word = resolve_category_word(product.get("category"))
    colour = _colour(product)

    # Identity tokens, de-duped case-insensitively so a model_name that equals
    # the shape ("Aviator"/"Aviator") is not repeated.
    identity: list[str] = []
    seen: set[str] = set()
    for piece in (brand, model, shape):
        if not piece:
            continue
        low = piece.lower()
        if low in seen:
            continue
        seen.add(low)
        identity.append(piece)

    core_parts = identity + [p for p in (gender, category_word) if p]
    core = " ".join(core_parts).strip()

    if colour and core:
        name = f"{core} - {colour}"
    elif colour:
        name = colour
    else:
        name = core

    return _clamp(_smart_title(name), MAX_NAME_LEN)


def build_seo_title(product: Dict[str, Any]) -> str:
    """Storefront <title> / og:title. Identical to the display name -- the
    display name is already the SEO-optimised identity, and keeping a single
    source of truth guarantees the bill line and the online title never drift.
    """
    return build_product_name(product)


def build_handle(product: Dict[str, Any]) -> str:
    """URL slug: lowercase, ASCII, hyphen-separated, no double hyphens.

    Derived from the display name so the handle mirrors the title (good SEO).
    Non-alphanumerics collapse to single hyphens; edges trimmed.
    """
    base = build_product_name(product)
    if not base:
        base = _first(product.get("sku")) if isinstance(product, dict) else ""
    slug = base.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug


def build_seo_description(product: Dict[str, Any]) -> str:
    """A deterministic SEO meta-description PLACEHOLDER (<= MAX_DESC_LEN).

    Intentionally generic (no store name / price, which drift): a one-line hook
    the catalog team can later replace. Returns '' when there is nothing to
    describe.
    """
    if not isinstance(product, dict):
        return ""
    name = build_product_name(product)
    if not name:
        return ""
    category_word = resolve_category_word(product.get("category")) or "product"
    brand = _brand(product)
    lead = f"Buy {name} online"
    tail = (
        f" -- genuine {brand} {category_word.lower()} with authenticity assured."
        if brand
        else f" -- genuine {category_word.lower()} with authenticity assured."
    )
    return _clamp(lead + tail, MAX_DESC_LEN)


def needs_name(product: Dict[str, Any]) -> bool:
    """True when the doc has no usable top-level display name yet (blank /
    whitespace). Used by the create door + backfill to decide whether to mint;
    never overwrites a non-blank name."""
    if not isinstance(product, dict):
        return False
    return not _clean(product.get("name"))
