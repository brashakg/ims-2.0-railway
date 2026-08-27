"""
IMS 2.0 - Smart-glasses listing builder (description bullets + tags + SEO)
=========================================================================
Owner 2026-08-25: "modify the smartglasses entry section on the cataloguing
page to be able to accordingly add new smartglasses in future".

THE PROBLEM THIS SOLVES
-----------------------
The 36 Ray-Ban Meta listings live on bettervision.in today carry all of their
smart-glass detail as PROSE somebody typed by hand -- an <h2>, a couple of
paragraphs, then a <ul> of spec bullets (camera, audio, assistant, battery,
connectivity, storage, prescription-ready). None of it came from IMS: the
cataloguing form never had a field for any of it, and (see products.py) the
free-text `description` was not even modelled on the create payload, so it was
silently dropped. Listing a new model therefore meant writing HTML.

WHAT THIS MODULE DOES
---------------------
Turns the NEW smart-glass fields on the SMARTGLASSES registry entry
(product_master._SMARTGLASS_TECH) into the same listing shape the live
products already use:

  * spec bullets    -- one <li> per filled field group; a BLANK FIELD OMITS ITS
                       BULLET ENTIRELY (never an empty or "undefined" line),
  * storefront tags -- product_smartglass / product_sunglass (the latter only
                       when the model is not prescription) / <brand>_<line>,
                       the tokens the storefront facets on that the attribute
                       tag generator (shopify_tag_gen) does NOT emit,
  * SEO title/desc  -- "... | Better Vision" + the one-sentence genuine /
                       authorised / call-for-best-price / pan-India line.

PURE + DETERMINISTIC + NETWORK-FREE. No AI, no invented specs: every bullet is
assembled from fields the cataloguer filled, so nothing can be claimed about a
product that nobody entered.

BLAST RADIUS (deliberate): the only caller is the product-create door's PIM
projection (product_master._build_pim_doc), and it only fills fields the new
product does not already carry. Nothing here runs on an existing product, so
the 36 live listings keep the description and tags they have unless somebody
deliberately edits and pushes one.

No emojis (Windows cp1252).
"""

from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional

from .product_description import _clean  # filled + stringified attrs only
from .shopify_tag_gen import slugify_brand_value

STORE_NAME = "Better Vision"

# Storefront facet tokens a smart glass carries on the live store. They are NOT
# emitted by shopify_tag_gen (which only builds `<attr>_<value>` tokens), so
# they are generated here instead of duplicating that module's registry.
# `product_sunglass` is CONDITIONAL: the live `sunglass` collection is
# disjunctive on TAG = product_sunglass, so it is what puts a product on the
# Sunglasses page -- and none of the 8 live prescription models carry it (read
# off the store 2026-08-25). See build_tags.
SUNGLASS_TAG = "product_sunglass"
BASE_TAGS = ("product_smartglass", SUNGLASS_TAG)

# Bare (prefix-less) facet tokens the live listings carry that no
# `<attr>_<value>` rule would ever produce: the generation 30 of the 36 live
# listings are grouped by, and the flag the 8 prescription Optics models carry.
# Both read off the live store on 2026-08-25.
GENERATION_TAG_PREFIX = "gen"
PRESCRIPTION_TAG = "prescription_ready"

MAX_SEO_TITLE = 70
MAX_SEO_DESCRIPTION = 320

_TRUTHY = {"yes", "true", "y", "1", "included", "included in box"}


def _yes(value: Any) -> bool:
    """A Yes/No select (or a real bool) read as a yes."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in _TRUTHY


# The brand-token rule lives in shopify_tag_gen (which builds the `brand_` token
# on the SAME payload) -- imported, not re-implemented, so the two can never
# disagree about how "Ray-Ban" is spelled. 'Ray-Ban' -> 'rayban', giving
# `rayban_meta` here and `brand_rayban` there, exactly as the 36 live listings
# read.
_alnum = slugify_brand_value


def _dedupe_join(*parts: Any) -> str:
    """Join non-blank parts with single spaces, dropping a part that repeats one
    already used (so a model_name of 'Wayfarer' and a shape of 'Wayfarer' do not
    print twice)."""
    out: List[str] = []
    seen = set()
    for p in parts:
        s = str(p or "").strip()
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
    return " ".join(out)


def _clamp(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-") or text[:limit]


# ---------------------------------------------------------------------------
# Spec bullets -- the <ul> that IS the smart-glass spec sheet on the storefront
# ---------------------------------------------------------------------------
# Each builder returns "" when the fields it needs are blank, and the blank
# bullet is dropped. Wording follows the live Ray-Ban Meta listings.


def _bullet_camera(a: Dict[str, str]) -> str:
    mp, kind, video = (
        a.get("camera_mp"),
        a.get("camera_type"),
        a.get("video_resolution"),
    )
    if not (mp or kind or video):
        return ""
    head = _dedupe_join(f"{mp}MP" if mp else "", (kind or "").lower(), "camera")
    text = f"{head} for photos and {video} video" if video else head
    # Every bullet on the live listings opens on a capital. With no megapixel
    # count the sentence starts on the camera TYPE, which is lower-cased so it
    # reads as "12MP ultra-wide camera" when the count IS there.
    return text[:1].upper() + text[1:]


def _bullet_audio(a: Dict[str, str]) -> str:
    speakers, mics = a.get("audio_type"), a.get("microphone_count")
    if speakers and mics:
        return f"{speakers} with a discreet {mics}-microphone array"
    if speakers:
        return speakers
    if mics:
        return f"Discreet {mics}-microphone array"
    return ""


def _bullet_assistant(a: Dict[str, str]) -> str:
    v = a.get("voice_assistant")
    return f"{v} voice assistant" if v else ""


def _bullet_controls(a: Dict[str, str]) -> str:
    return a.get("controls") or ""


def _bullet_battery(a: Dict[str, str]) -> str:
    hours = a.get("battery_life_hours")
    case = _yes(a.get("charging_case"))
    if hours and case:
        return f"Up to ~{hours} hours per charge, plus a portable charging case"
    if hours:
        return f"Up to ~{hours} hours per charge"
    if case:
        return "Includes a portable charging case"
    return ""


def _bullet_connectivity(a: Dict[str, str]) -> str:
    conn, storage = a.get("connectivity"), a.get("storage_gb")
    if conn and storage:
        return f"{conn} connectivity; {storage}GB on-board storage"
    if conn:
        return f"{conn} connectivity"
    if storage:
        return f"{storage}GB on-board storage"
    return ""


def _bullet_silhouette(a: Dict[str, str]) -> str:
    shape, material = a.get("shape"), a.get("frame_material")
    if shape and material:
        return f"Classic {shape.lower()} silhouette in {material.lower()}"
    if shape:
        return f"Classic {shape.lower()} silhouette"
    if material:
        return f"{material} frame"
    return ""


def _bullet_prescription(a: Dict[str, str]) -> str:
    if _yes(a.get("prescription_ready")):
        return "Prescription-ready (single-vision and progressive lenses)"
    return ""


# Render order = the order the live listings use.
_BULLET_BUILDERS = (
    _bullet_camera,
    _bullet_audio,
    _bullet_assistant,
    _bullet_controls,
    _bullet_battery,
    _bullet_connectivity,
    _bullet_silhouette,
    _bullet_prescription,
)


def build_spec_bullets(attributes: Dict[str, Any]) -> List[str]:
    """The storefront spec bullets, in listing order. A field left blank simply
    omits its bullet -- there is no empty or "undefined" line, ever."""
    a = _clean(attributes)
    return [b for b in (build(a) for build in _BULLET_BUILDERS) if b]


# ---------------------------------------------------------------------------
# Headline / SEO
# ---------------------------------------------------------------------------


def build_headline(attributes: Dict[str, Any]) -> str:
    """The listing <h2>: model + generation + colour + lens, as the live ones do."""
    a = _clean(attributes)
    # The product's IDENTITY is its model. `shape` is a generic silhouette the
    # form offers (Round/Square/Cat-Eye/...) and is a LAST-RESORT stand-in, not
    # an extra word: no live listing names a shape next to the model, and the
    # silhouette already has its own spec bullet.
    head = _dedupe_join(
        a.get("brand_name"),
        a.get("subbrand"),
        a.get("model_name") or a.get("model_no") or a.get("shape"),
    )
    gen = a.get("generation")
    if gen and head:
        head = f"{head} ({gen})".strip()
    # NOT colour_code: that is the REQUIRED field and it holds a manufacturer
    # code ("601/7150"), never a colour a customer reads. An unnamed colour is
    # simply not printed.
    colour = a.get("frame_color") or a.get("colour_name")
    if colour:
        head = f"{head} - {colour}".strip(" -")
    lens = a.get("lens_colour")
    if lens:
        head = f"{head} with {lens} lenses"
    return head.strip()


def build_seo_title(attributes: Dict[str, Any]) -> str:
    """e.g. 'Ray-Ban Meta Wayfarer Black Smart Glasses | Better Vision'."""
    a = _clean(attributes)
    core = _dedupe_join(
        a.get("brand_name"),
        a.get("subbrand"),
        a.get("model_name") or a.get("model_no") or a.get("shape"),
        a.get("frame_color") or a.get("colour_name"),
    )
    suffix = f" Smart Glasses | {STORE_NAME}"
    if not core:
        return f"Smart Glasses | {STORE_NAME}"
    return _clamp(core, MAX_SEO_TITLE - len(suffix)) + suffix


def build_seo_description(attributes: Dict[str, Any]) -> str:
    """The store's one-sentence meta description: genuine, authorised, call for
    the best price, delivered pan-India."""
    a = _clean(attributes)
    what = _dedupe_join(
        a.get("brand_name"),
        a.get("subbrand"),
        a.get("model_name") or a.get("model_no"),
    )
    lead = f"Buy the {what} smart glasses" if what else "Buy smart glasses"
    return _clamp(
        f"{lead} at {STORE_NAME} -- genuine stock from an authorised seller. "
        "Call us for our best price. Delivered pan-India.",
        MAX_SEO_DESCRIPTION,
    )


def build_tags(attributes: Dict[str, Any]) -> List[str]:
    """The storefront facet tokens shopify_tag_gen does not emit: the two
    product_* type tokens, the `<brand>_<product line>` token (rayban_meta),
    the generation token (gen2) and the prescription flag. The attribute
    tokens (brand_/shape_/framematerial_/...) still come from shopify_tag_gen
    at push time -- this does not duplicate them."""
    a = _clean(attributes)
    tags = list(BASE_TAGS)
    # A prescription model is NOT a sunglass: the live `sunglass` collection
    # rules on this tag disjunctively, so carrying it would file a clear-lens
    # Rx frame on the Sunglasses page. All 8 live prescription_ready models go
    # without it; the tinted models keep it.
    if _yes(a.get("prescription_ready")):
        tags.remove(SUNGLASS_TAG)
    brand, line = _alnum(a.get("brand_name")), _alnum(a.get("subbrand"))
    if brand and line:
        tags.append(f"{brand}_{line}")
    # "Gen 2" -> gen2, the token 30 of the 36 live listings are grouped by. Only
    # emitted when the digits are actually there, so nothing is invented.
    digits = "".join(ch for ch in str(a.get("generation") or "") if ch.isdigit())
    if digits:
        tags.append(f"{GENERATION_TAG_PREFIX}{digits}")
    if _yes(a.get("prescription_ready")):
        tags.append(PRESCRIPTION_TAG)
    return tags


# ---------------------------------------------------------------------------
# The full description HTML
# ---------------------------------------------------------------------------


def build_description_html(
    attributes: Dict[str, Any], paragraph: Optional[str] = None
) -> str:
    """Assemble the storefront description: <h2> headline, the marketing
    paragraph when one was written (the AI Auto-fill button, or typed by hand --
    it is never invented here), then the <ul> spec bullets.

    Returns "" unless at least ONE spec bullet was filled: this body IS the
    spec sheet, and a lone <h2> that only restates the product title is not a
    description. So a smart glass catalogued the old way (brand + model +
    colour code, no specs) is left with no description at all, exactly as
    before this module existed, rather than being published a wrong one."""
    headline = build_headline(attributes)
    bullets = build_spec_bullets(attributes)
    if not bullets:
        return ""
    parts: List[str] = []
    if headline:
        parts.append("<h2>{}</h2>".format(escape(headline)))
    if paragraph and paragraph.strip():
        parts.append("<p>{}</p>".format(escape(paragraph.strip())))
    if bullets:
        parts.append(
            "<ul>\n{}\n</ul>".format(
                "\n".join("<li>{}</li>".format(escape(b)) for b in bullets)
            )
        )
    return "\n".join(parts)


def build_listing(
    attributes: Dict[str, Any], paragraph: Optional[str] = None
) -> Dict[str, Any]:
    """Everything a smart-glasses Shopify listing needs, derived from the
    catalogued fields: {description, seo_title, seo_description, tags}."""
    return {
        "description": build_description_html(attributes, paragraph),
        "seo_title": build_seo_title(attributes),
        "seo_description": build_seo_description(attributes),
        "tags": build_tags(attributes),
    }
