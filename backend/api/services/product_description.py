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

from html import escape
from typing import Any, Dict, List, Optional, Tuple

WARRANTY_URL = "https://bettervision.in/pages/warranty"

# Attribute key -> row label, in render order, per section. Only rows whose
# attribute is filled are rendered. Keys are the IMS Add-Product field names
# (productAddShared CATEGORY_FIELDS / product_master registry).
TECH_SPEC_ROWS: List[Tuple[str, str]] = [
    ("frame_type", "Frame Type"),
    ("frame_material", "Frame Material"),
    ("_category", "Product Category"),  # injected from the category display
    ("lens_size", "Size"),
    ("bridge_size", "Bridge"),
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
    """The <h5> display line, e.g.
    'Rayban RB 5279 2000 53 Black Eyeglass Frame'."""
    a = _clean(attrs)
    parts = [
        a.get("brand_name"),
        a.get("model_no"),
        a.get("model_name"),
        a.get("colour_code"),
        a.get("lens_size"),
        a.get("frame_colour") or a.get("colour_name"),
        category_name,
    ]
    return " ".join(p for p in parts if p)


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
        value = category_name if key == "_category" else attrs.get(key)
        if not value or label in seen_labels:
            continue
        rows.append((label, value))
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
