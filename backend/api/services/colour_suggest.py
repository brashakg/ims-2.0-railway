"""Suggested colour name from the product's OWN photo (owner 2026-10-23).

Reads photo 1 (THE main-photo rule, product_master.main_photo), sends its
bytes to Claude as base64 (never a URL: the IMS image URL is closed to
outside fetchers by robots.txt) and returns a short retail colour name in
the house style of the 630 names drafted on 2026-10-23 ("Gold / Green",
"Havana / Brown Gradient", "Matte Black / Green Mirror Polarized").

It NEVER writes: Accept on the edit screen saves the name through the normal
product save, so the website twin and queue follow the existing rule. Each
call's tokens and estimated cost are logged and kept in ``ai_usage_log``.
"""

import base64
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"
# USD per million tokens (input, output). Unknown model -> cost not estimated.
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.0, 20.0)}
MAX_LEN = 40
PHOTO_EDGE = 800  # px; plenty to read a colour, keeps the call cheap

SYSTEM = (
    "You name the colour of eyewear for an Indian optical retailer's website. "
    "Look at the photo and reply with ONLY the colour name, nothing else: no "
    "quotes, no full stop, no explanation. Name what the PHOTO shows. House "
    "style (match it exactly): Title Case, at most 30 characters. Sunglasses: "
    "'Frame / Lens', e.g. 'Gold / Green', 'Black / Grey', 'Havana / Brown "
    "Gradient', 'Gunmetal / Grey Gradient', 'Matte Black / Green Mirror', "
    "'Transparent / Blue Gradient'. Add 'Gradient' or 'Mirror' only when the "
    "lens clearly is one. Add 'Polarized' at the end only when told the lens "
    "is polarized. Optical frames (clear demo lenses): the frame colour only, "
    "e.g. 'Matte Black', 'Havana', 'Gold'; a two-tone frame as 'Front / "
    "Temple', e.g. 'Grey / Black'. Prefer the trade words Havana (tortoise), "
    "Gunmetal, Transparent, Matte, Shiny only when visible. Any other product: "
    "its main colour in two or three words."
)


def model_name() -> str:
    return os.getenv("CATALOG_VISION_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _prompt(product: Optional[Dict[str, Any]], category: str, polarized: bool) -> str:
    a = (product or {}).get("attributes") or {}
    bits = [
        f"Product type: {category or 'eyewear'}",
        f"Brand: {a.get('brand_name') or (product or {}).get('brand') or 'unknown'}",
        f"Model: {a.get('model_no') or a.get('model_name') or (product or {}).get('model') or 'unknown'}",
        "Lens: polarized" if polarized else "Lens: not marked polarized",
    ]
    return "\n".join(bits) + "\nThe colour name:"


def clean_name(text: Optional[str]) -> str:
    """The model's reply as a colour name: first line, quotes and a trailing
    full stop dropped, capped at MAX_LEN. '' when nothing usable."""
    line = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
    line = line.strip("\"'`* ").rstrip(".").strip()
    if line.lower().startswith("colour name:") or line.lower().startswith("color name:"):
        line = line.split(":", 1)[1].strip()
    return line[:MAX_LEN].strip()


def cost_usd(model: str, usage: Dict[str, Any]) -> Optional[float]:
    price = PRICES.get(model)
    if price is None:
        return None
    tin = int(usage.get("input_tokens") or 0)
    tout = int(usage.get("output_tokens") or 0)
    return round((tin * price[0] + tout * price[1]) / 1_000_000, 6)


async def _photo_bytes(url: str, self_hosted_only: bool) -> Optional[bytes]:
    """Raw bytes of one product photo via the catalogue PDF's fetchers: the
    file store for an IMS photo (kind-scoped), else the stored http(s) URL.
    A form-supplied URL (no product yet) is read from the file store only."""
    from .catalogue_pdf import _fetch_local_bytes, fetch_images

    if self_hosted_only:
        return _fetch_local_bytes(url)
    return (await fetch_images([{"image": url}])).get(0)


def _log_cost(db, entry: Dict[str, Any]) -> None:
    logger.info(
        "[COLOUR-SUGGEST] user=%s product=%s model=%s in=%s out=%s cost_usd=%s",
        entry.get("user_id"), entry.get("product_id"), entry.get("model"),
        entry.get("input_tokens"), entry.get("output_tokens"), entry.get("cost_usd"),
    )
    if db is None:
        return
    try:
        db.get_collection("ai_usage_log").insert_one(dict(entry))
    except Exception as e:  # noqa: BLE001 - the cost line above is already out
        logger.warning("[COLOUR-SUGGEST] usage row not stored: %s", e)


async def suggest_colour_name(
    *,
    product: Optional[Dict[str, Any]] = None,
    image_url: Optional[str] = None,
    user_id: Optional[str] = None,
    db=None,
) -> Dict[str, Any]:
    """{"status", "suggestion", "message", ...}. Status: SUGGESTED, NO_PHOTO,
    FAILED_NO_KEY, FAILED_GENERATION. ``product`` is the saved product, or
    for Add product just {category, attributes} with ``image_url`` = the
    form's photo 1. Never writes the product."""
    from agents import llm_provider
    from .catalogue_pdf import _prepare_image
    from .integration_config import get_anthropic_config
    from .product_master import category_spec, main_photo
    from api.utils.event_time import utc_now_iso

    # The form's photo 1 wins (it may not be saved yet). One the product
    # already holds as photo 1 is read like any stored photo; any other is
    # read from the IMS file store only (never fetched from outside).
    saved = main_photo(product)
    form_photo = (image_url or "").strip() or None
    if form_photo == saved:
        form_photo = None
    url = form_photo or saved
    if not url:
        return {"status": "NO_PHOTO", "suggestion": "",
                "message": "Add a photo first - the colour name is read from photo 1."}
    if not get_anthropic_config().get("api_key"):
        return {"status": "FAILED_NO_KEY", "suggestion": "",
                "message": "No Anthropic key is set. Add it in Settings -> Integrations -> Anthropic."}

    raw = await _photo_bytes(url, self_hosted_only=form_photo is not None)
    prepared = _prepare_image(raw, max_edge=PHOTO_EDGE) if raw else None
    if prepared is None:
        return {"status": "NO_PHOTO", "suggestion": "",
                "message": "Photo 1 could not be read. Re-upload it and try again."}
    jpeg = prepared[0].getvalue()

    attrs = (product or {}).get("attributes") or {}
    spec = category_spec((product or {}).get("category") or "")
    polarized = str(attrs.get("polarization") or "").strip().lower() in ("yes", "true", "polarized", "polarised")
    model = model_name()
    out = await llm_provider.complete_image(
        SYSTEM,
        _prompt(product, spec.display if spec else "", polarized),
        base64.standard_b64encode(jpeg).decode("ascii"),
        model=model,
        media_type="image/jpeg",
        max_tokens=1024,
    )
    if out is None:
        return {"status": "FAILED_GENERATION", "suggestion": "",
                "message": "The colour could not be read just now. Try again in a minute."}

    usage = out.get("usage") or {}
    cost = cost_usd(out.get("model") or model, usage)
    _log_cost(db, {
        "feature": "suggest_colour_name",
        "user_id": user_id,
        "product_id": (product or {}).get("product_id"),
        "model": out.get("model") or model,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cost_usd": cost,
        "created_at": utc_now_iso(),
    })
    name = clean_name(out.get("text"))
    if not name:
        return {"status": "FAILED_GENERATION", "suggestion": "",
                "message": "The colour could not be read just now. Try again in a minute."}
    return {"status": "SUGGESTED", "suggestion": name, "message": "", "cost_usd": cost}
