"""
IMS 2.0 - Shopify-format product description builder
====================================================
Owner 2026-07-05: the live Shopify descriptions were DELIBERATELY structured
(HTML sections + spec tables) because Shopify lacks structured fields; the
Add-Product "Auto-fill with AI" button must emit the SAME format so IMS-born
products look identical to the existing store.

The template (verified against live published BVI/Shopify products):

  <h4>Product Details</h4>
  <h5>Model Number: {display model line}</h5>
  <p>{one marketing paragraph -- the ONLY AI-written part}</p>
  <h4>Technical Specifications</h4>
  <table>...key/value rows from the filled fields...</table>
  <h4>General Information</h4>
  <table>...key/value rows from the filled fields...</table>
  <h4>Warranty</h4>
  <p>...warranty sentence + link to the store warranty page...</p>

Everything except the paragraph is DETERMINISTIC (pure code from the filled
attributes), so the structure can never drift, only rows whose fields are
actually filled are rendered, and the AI can never invent a spec.

No emojis (Windows cp1252 safe).
"""

from __future__ import annotations

import re
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from .product_naming import drop_doubled_brand, model_number_line

WARRANTY_URL = "https://bettervision.in/pages/warranty"

# Attribute key -> row label, in render order, per section. Only rows whose
# attribute is filled are rendered. Keys are the IMS Add-Product field names
# (CATEGORY_FIELDS in frontend/src/domain/catalog/productAdd/categoryFields.ts
# / the product_master registry).
TECH_SPEC_ROWS: List[Tuple[str, str]] = [
    ("frame_type", "Frame Type"),
    ("frame_material", "Frame Material"),
    ("_category", "Product Category"),  # injected from the category display
    ("lens_size", "Size"),
    ("bridge_size", "Bridge"),
    ("bridge_width", "Bridge"),
    ("temple_length", "Temple Length"),
    ("weight", "Weight"),
    ("lens_material", "Lens Material"),
    ("polarization", "Polarization"),
    ("uv_protection", "UV Protection"),
    ("tint", "Tint"),
    ("blue_cut_lens", "Blue Cut Lens"),
    ("power", "Power"),
    ("base_curve", "Base Curve"),
    ("diameter", "Diameter"),
    ("modality", "Modality"),
    ("pack", "Pack Size"),
    ("gtin", "GTIN"),
    ("upc", "UPC Code"),
]

GENERAL_INFO_ROWS: List[Tuple[str, str]] = [
    ("brand_name", "Brand"),
    ("subbrand", "Sub Brand"),
    ("model_no", "Model No"),
    ("model_name", "Model Name"),
    ("shape", "Shape"),
    ("colour_code", "Frame Code"),
    ("frame_colour", "Frame Colour"),
    ("temple_colour", "Temple Colour"),
    ("lens_colour", "Lens Colour"),
    ("colour_name", "Lens Colour"),
    ("gender", "Gender"),
    ("country_of_origin", "Country Of Origin"),
]


def _clean(attrs: Dict[str, Any]) -> Dict[str, str]:
    """Filled, stringified attributes only."""
    out: Dict[str, str] = {}
    for k, v in (attrs or {}).items():
        if v is None:
            continue
        s = str(v).strip()
        if s:
            out[str(k).strip().lower()] = s
    return out


def build_model_line(category_name: str, attrs: Dict[str, Any]) -> str:
    """The <h5> 'Model Number' line, e.g. 'Ray-Ban RB5279' -- the shared
    product_naming.model_number_line: brand + model, the brand never doubled,
    never a colour code, size or category (``category_name`` is kept for the
    callers' signature)."""
    return model_number_line({"attributes": _clean(attrs)})


# Measurements shown in millimetres (a bare number gets ' mm').
_MM_KEYS = frozenset({"lens_size", "bridge_size", "bridge_width", "temple_length"})
_BARE_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_NOT_POLARIZED = frozenset({"no", "false", "non-polarized", "non-polarised", "non polarized", "non polarised", "none"})
# A lens that is NOT polarized is described without any 'polar...' word, so a
# shopper's 'polarized' search never finds it (Shopify search stems words).
NOT_POLARIZED_ROW = ("Lens Type", "Standard")


def _mm(key: str, value: str) -> str:
    """A bare-number measurement in millimetres ('145' -> '145 mm')."""
    value = str(value or "").strip()
    return value + " mm" if key in _MM_KEYS and _BARE_NUMBER.fullmatch(value) else value


def _row(key: str, label: str, value: str) -> Tuple[str, str]:
    """One spec row as the shopper reads it: millimetres on a measurement, a
    non-polarized lens as NOT_POLARIZED_ROW."""
    if key == "polarization" and value.strip().lower() in _NOT_POLARIZED:
        return NOT_POLARIZED_ROW
    return label, _mm(key, value)


def _table(rows: List[Tuple[str, str]]) -> str:
    body = "\n".join(
        "<tr>\n<td>{}</td>\n<td>{}</td>\n</tr>".format(escape(k), escape(v))
        for k, v in rows
    )
    return "<table>\n<tbody>\n{}\n</tbody>\n</table>".format(body)


def _rows_for(
    spec_map: List[Tuple[str, str]], attrs: Dict[str, str], category_name: str
) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    seen_labels = set()
    for key, label in spec_map:
        # colour_name is the LENS colour only on a contact lens; on eyewear it
        # is the whole retail name ('Gold / Green'), never a spec row.
        if key == "colour_name" and "contact lens" not in str(category_name).lower():
            continue
        value = category_name if key == "_category" else attrs.get(key)
        if key == "model_no" and value:
            value = model_number_line({"attributes": attrs}) or value
        if not value or label in seen_labels:
            continue
        rows.append(_row(key, label, value))
        seen_labels.add(label)
    return rows


def build_shopify_description_html(
    category_name: str,
    attributes: Dict[str, Any],
    paragraph: str,
    warranty: Optional[str] = None,
) -> str:
    """Assemble the full Shopify-format HTML description. Pure/deterministic:
    the AI contributes ONLY `paragraph`; every table row comes from a filled
    field; sections with no rows are omitted entirely."""
    a = _clean(attributes)
    model_line = build_model_line(category_name, attributes)

    parts: List[str] = ["<h4>Product Details</h4>"]
    if model_line:
        parts.append("<h5>Model Number: {}</h5>".format(escape(model_line)))
    if paragraph and paragraph.strip():
        parts.append("<p>{}</p>".format(escape(paragraph.strip())))

    tech = _rows_for(TECH_SPEC_ROWS, a, category_name)
    if tech:
        parts.append("<h4>Technical Specifications</h4>")
        parts.append(_table(tech))

    general = _rows_for(GENERAL_INFO_ROWS, a, category_name)
    if general:
        parts.append("<h4>General Information</h4>")
        parts.append(_table(general))

    w = (warranty or a.get("warranty") or "").strip()
    if w:
        parts.append("<h4>Warranty</h4>")
        parts.append(
            "<p><span>This product comes with a manufacturer's warranty of "
            "{} from the date of sale. For more details on warranty, "
            '<a href="{}" title="Warranty" target="_blank">Click Here</a>.'
            "</span></p>".format(escape(w), WARRANTY_URL)
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# THE MODEL'S DESCRIPTION (G2, owner 2026-10-18: one website listing per
# model -- "both SEO and website made accordingly")
# ---------------------------------------------------------------------------
# A model's listing carries every colour and size, so its body is written for
# the MODEL: the model line has no colour or size, the spec tables keep only
# what EVERY colour/size shares, the choices are listed ('Available Colours',
# 'Sizes'), and what differs between them (lens colour, polarization,
# materials...) sits in one 'Colours and Sizes' table, never in the shared
# body. The barcode is per colour/size (a variant metafield), never here.

# Per-variant by nature: never a shared row and never a column of their own
# (the Colour / Size columns already name them; the barcode is a variant
# metafield).
_VARIANT_ONLY_KEYS = frozenset({"gtin", "upc", "colour_code", "lens_size", "frame_colour", "colour_name"})

# We launch at full MRP (2026-10-22): no discount promise.
STORE_PROMISE = "100% genuine, brand-authorised. Discover your perfect pair at Better Vision today!"


def build_model_name_line(category_name: str, attrs: Dict[str, Any]) -> str:
    """The model's <h5> line -- the same model_number_line as every other
    model line (brand + model, never a colour code, size or category)."""
    return build_model_line(category_name, attrs)


def default_model_paragraph(
    category_name: str, attrs: Dict[str, Any], colours: int, sizes: int
) -> str:
    """The deterministic model paragraph (used until an AI paragraph is
    written for the model): names the model and how many colours / sizes it
    comes in, never one colour."""
    a = _clean(attrs)
    who = {"MEN": "men", "WOMEN": "women", "KIDS": "kids", "UNISEX": "men and women"}.get(
        str(a.get("gender") or "").upper().replace("'", "").replace(" ", ""), ""
    )
    name = " ".join(p for p in (build_model_name_line(category_name, attrs), category_name) if p)
    first = f"Reflect your style with the {name}" + (f", made for {who}." if who else ".")
    choice = []
    if colours > 1:
        choice.append(f"{colours} colours")
    if sizes > 1:
        choice.append(f"{sizes} sizes")
    second = f" Available in {' and '.join(choice)} -- choose yours above." if choice else ""
    return f"{first}{second} {STORE_PROMISE}"


def _same_everywhere(members: List[Dict[str, Any]], key: str) -> Optional[str]:
    """The value of ``key`` when EVERY member has it filled and equal (case
    insensitive), else None."""
    values = [_clean(m.get("attributes") or {}).get(key) or "" for m in members]
    if not values or not values[0]:
        return None
    first = values[0].lower()
    return values[0] if all(v.lower() == first for v in values) else None


def build_model_description_html(
    category_name: str,
    model_attrs: Dict[str, Any],
    members: List[Dict[str, Any]],
    paragraph: str,
) -> str:
    """The model listing's body. ``members`` = [{sku, attributes, colour,
    size}] in listing order (lead first). Pure/deterministic -- only
    ``paragraph`` may come from the AI."""
    colours = list(dict.fromkeys(m.get("colour") for m in members if m.get("colour")))
    sizes = list(dict.fromkeys(m.get("size") for m in members if m.get("size")))
    parts: List[str] = ["<h4>Product Details</h4>"]
    line = build_model_name_line(category_name, model_attrs)
    if line:
        parts.append("<h5>Model Number: {}</h5>".format(escape(line)))
    if paragraph and paragraph.strip():
        parts.append("<p>{}</p>".format(escape(paragraph.strip())))

    spec_rows = TECH_SPEC_ROWS + GENERAL_INFO_ROWS
    shared: Dict[str, str] = {}
    for key, _label in spec_rows:
        if key in _VARIANT_ONLY_KEYS or key in shared:
            continue
        value = _same_everywhere(members, key)
        if value:
            shared[key] = value
    tech = _rows_for(TECH_SPEC_ROWS, shared, category_name)
    if len(colours) > 1:
        tech.append(("Available Colours", ", ".join(colours)))
    if sizes:
        tech.append(("Sizes" if len(sizes) > 1 else "Size", ", ".join(_mm("lens_size", z) for z in sizes)))
    if tech:
        parts.append("<h4>Technical Specifications</h4>")
        parts.append(_table(tech))
    general = _rows_for(GENERAL_INFO_ROWS, shared, category_name)
    if general:
        parts.append("<h4>General Information</h4>")
        parts.append(_table(general))

    table = variant_table_html(members)
    if table:
        parts.append(table)

    warranty = _same_everywhere(members, "warranty")
    if warranty:
        parts.append("<h4>Warranty</h4>")
        parts.append(
            "<p><span>This product comes with a manufacturer's warranty of "
            "{} from the date of sale. For more details on warranty, "
            '<a href="{}" title="Warranty" target="_blank">Click Here</a>.'
            "</span></p>".format(escape(warranty), WARRANTY_URL)
        )
    return "\n".join(parts)


def variant_table_html(members: List[Dict[str, Any]]) -> str:
    """'Colours and Sizes': one row per colour/size, a column only for what is
    NOT the same on every member ('' for a model of one)."""
    if len(members) < 2:
        return ""
    sizes = [m.get("size") for m in members if m.get("size")]
    label_of: Dict[str, str] = {}
    for key, label in TECH_SPEC_ROWS + GENERAL_INFO_ROWS:
        label_of.setdefault(key, label)
    differing = [
        key
        for key in label_of
        if key not in _VARIANT_ONLY_KEYS
        and key != "_category"
        and not _same_everywhere(members, key)
        and any(_clean(m.get("attributes") or {}).get(key) for m in members)
    ]
    head = ["Colour"] + (["Size"] if sizes else []) + [label_of[k] for k in differing]
    body_rows = []
    for m in members:
        a = _clean(m.get("attributes") or {})
        cells = [m.get("colour") or ""] + ([m.get("size") or ""] if sizes else [])
        body_rows.append(cells + [_mm(k, a.get(k, "")) for k in differing])
    return "<h4>Colours and Sizes</h4>\n<table>\n<thead>\n<tr>{}</tr>\n</thead>\n<tbody>\n{}\n</tbody>\n</table>".format(
        "".join("<th>{}</th>".format(escape(h)) for h in head),
        "\n".join(
            "<tr>{}</tr>".format("".join("<td>{}</td>".format(escape(c)) for c in cells))
            for cells in body_rows
        ),
    )


def names_a_colour(text: str, colours: List[str]) -> bool:
    """True when ``text`` names any of the model's colours (a colour name or
    code of 3+ characters, as a whole word) -- a model paragraph names none."""
    low = str(text or "").lower()
    for c in colours or []:
        for piece in re.split(r"[()/]", str(c or "")):
            word = piece.strip().lower()
            if len(word) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", low):
                return True
    return False


async def generate_model_paragraph(
    category_name: str,
    model_attrs: Dict[str, Any],
    colours: List[str],
    sizes: List[str],
    max_length: int = 350,
) -> Optional[str]:
    """The AI paragraph for a MODEL (the 'Auto-fill with AI' voice, model
    level): written from the model-level fields and the NUMBER of colours /
    sizes only -- no colour name is sent, and an answer that names one anyway
    is thrown away. None (the caller keeps the deterministic paragraph) when
    no model is configured, the call fails, or the answer names a colour."""
    try:
        from agents.claude_client import call_claude, is_claude_available
    except Exception:  # noqa: BLE001
        return None
    if not is_claude_available():
        return None
    model_keys = ("brand_name", "model_no", "model_name", "shape", "gender")
    filled = {k: v for k, v in _clean(model_attrs).items() if k in model_keys}
    if not filled:
        return None
    system = (
        "You are the product copywriter for Better Vision, an Indian optical "
        "retail chain. Write ONE marketing paragraph for a MODEL that is sold "
        "in several colours and sizes on one product page. Open with a line "
        "like 'Reflect your style with {brand + model + product type}', say "
        "how many colours (and sizes) it comes in, who it suits (gender) and "
        "its shape, then close with: '" + STORE_PROMISE + "'. Rules: 3-4 "
        f"sentences, under {max_length} characters, PLAIN TEXT only (no "
        "markdown, no HTML, no emojis). NEVER name a colour, a lens colour or "
        "a size -- the page carries many. Use ONLY the attributes given; never "
        "invent specifications or claims."
    )
    lines = "\n".join(f"{k}: {v}" for k, v in sorted(filled.items()))
    user_msg = (
        f"Product category: {category_name}\nAttributes:\n{lines}\n"
        f"Number of colours: {len(colours)}\nNumber of sizes: {len(sizes)}"
    )
    try:
        text = await call_claude(system, user_msg, max_tokens=300, timeout=20.0)
    except Exception:  # noqa: BLE001
        return None
    text = (text or "").strip()
    if not text or names_a_colour(text, colours):
        return None
    return clip_to_sentence(text, max_length)


def clip_to_sentence(text: str, max_length: int) -> Optional[str]:
    """``text`` cut to its last WHOLE sentence that fits ``max_length`` --
    never mid-sentence ('Discover your.'). None when not even the first
    sentence fits (the caller keeps the deterministic paragraph). Pure."""
    text = (text or "").strip()
    if len(text) <= max_length:
        return text or None
    ends = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", text) if m.end() <= max_length]
    return text[: ends[-1]].strip() if ends else None


# ---------------------------------------------------------------------------
# THE OUTBOUND BODY (listing-quality audit 2026-10-22). Stored bodies were
# written by older builders (a doubled brand, a colour code and size on the
# model line, a discount promise, bare-number measurements, 'Polarization:
# No'). The push sends every body through this ONE pass, so stored copy reads
# exactly as the builders above write it today. Idempotent; touches only the
# deterministic parts (the model line, the spec rows, the store promise).
# ---------------------------------------------------------------------------

_MODEL_H5 = re.compile(r"(<h5>\s*Model Number:\s*)(.*?)(\s*</h5>)", re.S | re.I)
_OLD_PROMISE = re.compile(
    r"Enjoy best discounts[^.!?]*[.!?](\s*Discover your perfect pair at Better Vision today!)?", re.I
)
_SPEC_ROW = re.compile(r"(<td>)\s*([^<]*?)\s*(</td>\s*<td>)([^<]*)(</td>)", re.I)
_LABEL_KEY = {
    "model no": "model_no",
    "size": "lens_size",
    "lens width": "lens_size",
    "bridge": "bridge_width",
    "temple length": "temple_length",
    "polarization": "polarization",
    "polarisation": "polarization",
}


def normalise_body_html(html: str, product: Dict[str, Any]) -> str:
    """``html`` as the push sends it for ``product`` (a catalog doc / listing
    view): the brand never doubled, the 'Model Number' line and 'Model No' row
    = product_naming.model_number_line, measurements in mm, a non-polarized
    lens without a 'polar...' word, the full-MRP store promise. Pure."""
    if not html:
        return html or ""
    product = product or {}
    attrs = product.get("attributes") or {}
    brand = str(product.get("brand") or attrs.get("brand_name") or "").strip()
    out = drop_doubled_brand(html, brand)
    line = model_number_line(product)
    if line:
        out = _MODEL_H5.sub(lambda m: m.group(1) + escape(line) + m.group(3), out)
    out = _OLD_PROMISE.sub(STORE_PROMISE, out)

    def _fix_row(m: "re.Match[str]") -> str:
        key = _LABEL_KEY.get(m.group(2).strip().lower())
        if key is None:
            return m.group(0)
        value = m.group(4).strip()
        if key == "model_no":
            label, value = m.group(2), (escape(line) if line else value)
        else:
            label, value = _row(key, m.group(2), value)
        return m.group(1) + label + m.group(3) + value + m.group(5)

    return _SPEC_ROW.sub(_fix_row, out)
