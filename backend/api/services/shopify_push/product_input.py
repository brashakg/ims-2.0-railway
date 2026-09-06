"""Shopify push -- product input

Payload builders for a product push: ProductInput,
metafields, options, variant pricing resolution, the publishable-price
predicate, GTIN sanitising and the variant price inputs. Pure; testable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from agents.nexus_providers import _as_shopify_gid
from ..ecom_category_map import ims_to_shopify_type
from ..gtin import sanitise_gtin
from ..shopify_tag_gen import generate_attribute_tags, merge_tag_lists

from ._shared import logger
from .transport import _graphql

# ===========================================================================
# Payload builders -- map IMS ecom docs -> Shopify GraphQL input. Pure; testable.
# ===========================================================================


def ims_product_tags(product: Dict[str, Any]) -> List[str]:
    """THE tags IMS wants on this product's Shopify listing. Pure.

    Union of the product's own tags (ecom.seo.tags -- the ONLY spelling any
    door writes, product_master.set_twin_tags) and the attribute-derived
    `<prefix>_<value>` filter tags the storefront facets on (shopify_tag_gen,
    BVI parity). Lower-cased + deduped by merge_tag_lists. Both the create
    payload and the update-time tag diff read this one list, so what IMS
    records as "sent" is always what it computed."""
    ecom = product.get("ecom") or {}
    seo = ecom.get("seo") or {}
    attrs = product.get("attributes") or {}
    extras: Dict[str, Any] = {}
    if product.get("brand"):
        # Brand lives top-level on the product doc; feed it so the brand_ tag is
        # emitted even when `attributes` has no brand_name.
        extras["brand_name"] = product["brand"]
    generated_tags = generate_attribute_tags(product.get("category"), attrs, extras)
    return merge_tag_lists(seo.get("tags") or [], generated_tags)


def build_product_input(
    product: Dict[str, Any], variants: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Build a Shopify ProductInput from a catalog_products doc (+ its ecom
    sub-doc) and its catalog_variants. The Shopify gid (if already mapped) is set
    so the SAME object is updated rather than duplicated."""
    ecom = product.get("ecom") or {}
    seo = ecom.get("seo") or {}
    title = (
        product.get("title")
        or product.get("name")
        or product.get("model")
        or product.get("sku")
        or "Untitled product"
    )
    # OWNER RULING 2026-08-25 -- "one press, goes live": pressing push IS the act
    # of putting the product in front of customers, so the payload goes out
    # ACTIVE. This used to map ecom.status through {DRAFT->DRAFT, PUBLISHED->
    # ACTIVE, ARCHIVED->ARCHIVED} defaulting to DRAFT -- and since every product
    # is BORN ecom.status=DRAFT and nothing in IMS ever advanced it, every push
    # ever made sent a DRAFT. The press reported "sent, failed 0" and the brand
    # page stayed empty. ARCHIVED is the one status still honoured: it is a
    # deliberate retirement, not an un-advanced create-time default.
    inp: Dict[str, Any] = {
        "title": title,
        "status": (
            "ARCHIVED"
            if str(ecom.get("status") or "").upper() == "ARCHIVED"
            else "ACTIVE"
        ),
    }
    sid = ecom.get("shopify_product_id")
    if sid:
        inp["id"] = _as_shopify_gid(sid, "Product")
    if ecom.get("handle"):
        inp["handle"] = ecom["handle"]
    if product.get("brand"):
        inp["vendor"] = product["brand"]
    if product.get("category"):
        # The STOREFRONT's productType vocabulary, not IMS's enum. Sending the
        # raw enum ("SUNGLASS", "FRAME", "SMARTGLASSES") put every pushed
        # product outside every smart collection on bettervision.in, which all
        # rule on TYPE = "Sunglass" / "Spectacles" / "Contact Lenses"; the 36
        # live smart glasses read "SmartGlass". ecom_category_map is the
        # declared single source of truth for that translation and this is its
        # door -- unknown categories still pass through unchanged (fail-soft).
        inp["productType"] = ims_to_shopify_type(product["category"])
    body_html = seo.get("html") or product.get("description")
    if body_html:
        inp["descriptionHtml"] = body_html
    if ecom.get("theme_suffix"):
        inp["templateSuffix"] = ecom["theme_suffix"]
    if seo.get("title") or seo.get("description"):
        inp["seo"] = {
            "title": seo.get("title") or title,
            "description": seo.get("description") or "",
        }
    # TAGS RIDE THE CREATE ONLY (sync audit gap #4, owner 2026-09-06). On a
    # productUpdate the `tags` field REPLACES Shopify's whole list, which wiped
    # every tag a human had added in the Shopify admin (measured on the 36
    # connector-uploaded Ray-Ban Meta products). An existing product's tags are
    # diffed by tags.sync_product_tags (tagsAdd / tagsRemove of the tags IMS
    # itself sent) after the write; the create still carries the full list.
    merged_tags = ims_product_tags(product)
    if merged_tags and not sid:
        inp["tags"] = merged_tags
    # Variant identity is carried as options/skus only (price/qty stay BVI/stock
    # owned -- online qty is the derived allocation, not pushed from here).
    # CREATE ONLY: Shopify rejects productOptions on productUpdate
    # ("product_options cannot be specified during update"), which failed a
    # LIVE press outright. Changed option axes on an existing product need the
    # separate productOptions* mutations -- not built; the update goes out
    # without them and the skip is logged.
    if variants:
        if sid:
            logger.info(
                "[SHOPIFY_PUSH] update %s: productOptions not re-synced "
                "(create-only field)",
                sid,
            )
        else:
            inp["productOptions"] = _derive_options(variants)
    return inp


# Owner 2026-07-05 ("CREATE WITH METAFIELDS"): category attributes (frame
# material, temple length, UV protection, ...) push to Shopify as STRUCTURED
# metafields under the `ims` namespace -- not only baked into the description.
# Storefront filtering then only needs the owner to add metafield definitions
# in Shopify admin (Search & Discovery); the data is already on every product.
_METAFIELD_NAMESPACE = "ims"
_METAFIELDS_PER_CALL = 25  # Shopify metafieldsSet hard cap per mutation
_MAX_METAFIELDS = 50  # sanity cap per product (attributes are short lists)

_METAFIELDS_SET = """
mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { id key }
    userErrors { field message }
  }
}
"""


def build_product_metafields(product: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map the product's `attributes` dict (the canonical home of the
    category-specific fields) onto Shopify MetafieldsSetInput rows (without
    ownerId -- the pusher stamps that once the product gid is known).

    Pure + deterministic: scalar attributes only (dict/list/None/blank
    skipped), keys lowercased snake_case truncated to Shopify's 30-char key
    limit, values stringified, sorted by key, capped at _MAX_METAFIELDS."""
    attrs = product.get("attributes") or {}
    if not isinstance(attrs, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for k, v in attrs.items():
        if v is None or isinstance(v, (dict, list, tuple)):
            continue
        value = str(v).strip()
        if not value:
            continue
        key = str(k).strip().lower().replace(" ", "_").replace("-", "_")[:30]
        if not key:
            continue
        rows.append(
            {
                "namespace": _METAFIELD_NAMESPACE,
                "key": key,
                "type": "single_line_text_field",
                "value": value[:500],
            }
        )
    # Deterministic order on the NORMALIZED key (raw keys mix cases/spaces).
    rows.sort(key=lambda r: r["key"])
    return rows[:_MAX_METAFIELDS]


async def _set_product_metafields(
    db, product_gid: str, metafields: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """LIVE-only: upsert the product's attribute metafields via metafieldsSet
    (idempotent on owner+namespace+key), chunked at the Shopify per-call cap.
    Fail-SOFT: a metafield error must never undo/fail the product push itself --
    returns {"set": n, "errors": [...]} for the result/audit row."""
    set_count = 0
    errors: List[str] = []
    for i in range(0, len(metafields), _METAFIELDS_PER_CALL):
        chunk = [
            {**m, "ownerId": product_gid}
            for m in metafields[i : i + _METAFIELDS_PER_CALL]
        ]
        try:
            body = await _graphql(db, _METAFIELDS_SET, {"metafields": chunk})
            field_obj = (body.get("data") or {}).get("metafieldsSet") or {}
            errs = field_obj.get("userErrors") or []
            if errs:
                errors.extend(
                    f"{(e.get('field') or '?')}: {e.get('message')}" for e in errs
                )
            set_count += len(field_obj.get("metafields") or [])
        except Exception as e:  # noqa: BLE001 -- fail-soft side channel
            errors.append(str(e))
    return {"set": set_count, "errors": errors}


def _derive_options(variants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build Shopify productOptions (Color / Size) from the variant option axes
    that are actually present. Returns [] when no variant carries an option."""
    colors = [v.get("option_color") for v in variants if v.get("option_color")]
    sizes = [v.get("option_size") for v in variants if v.get("option_size")]
    options: List[Dict[str, Any]] = []
    if colors:
        options.append(
            {"name": "Color", "values": [{"name": c} for c in _dedupe(colors)]}
        )
    if sizes:
        options.append(
            {"name": "Size", "values": [{"name": s} for s in _dedupe(sizes)]}
        )
    return options


def _dedupe(seq: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _price_float(v: Any) -> float:
    """A usable positive price, else 0.0. Fail-soft on junk."""
    try:
        f = float(v)
        return f if f > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _resolve_variant_pricing(
    product: Dict[str, Any], variant: Dict[str, Any]
) -> Tuple[float, float]:
    """Resolve (selling_price, mrp) for ONE variant.

    Selling price: the variant's own discounted_price, else its mrp, else the
    ONLINE rule price on the ecom sub-doc (ecom.online_offer_price -- what the
    online discount engine writes for a no-variant product), else the parent
    product's offer_price / pricing.offer_price, else the product mrp.
    MRP (the compare-at side): the variant's compare_at_price, else its mrp, else
    the ecom online compare-at, else the product mrp. Returns 0.0 legs when nothing
    usable exists.

    NOTE (online discount engine): ecom.online_offer_price is preferred ABOVE the
    in-store offer_price ONLY when the online discount engine actually computed it
    (ecom.online_price_source in {"rule","manual"}). An unstamped / "none" online
    price is a hand-set BVI value or a stale save -- preferring it would silently
    FLIP a no-variant product's shipped price (e.g. from its in-store offer up to
    MRP) at the Phase-B cutover. When it is not engine-stamped we fall back to the
    existing in-store offer (pre-batch behaviour) so nothing flips unexpectedly. The
    engine NEVER writes offer_price, so in-store POS pricing is untouched. Variant-
    carrying products are unaffected: the variant's own discounted_price (which the
    engine writes) still wins first."""
    pricing = product.get("pricing") or {}
    ecom = product.get("ecom") or {}
    # Only trust the product-level online offer/compare-at when the engine stamped
    # it (a real rule- or manual-derived price); ignore unstamped / "none" values.
    _ecom_stamped = ecom.get("online_price_source") in ("rule", "manual")
    ecom_online_offer = _price_float(ecom.get("online_offer_price")) if _ecom_stamped else 0.0
    ecom_online_compare = (
        _price_float(ecom.get("online_compare_at_price")) if _ecom_stamped else 0.0
    )
    price = (
        _price_float(variant.get("discounted_price"))
        or _price_float(variant.get("mrp"))
        or ecom_online_offer
        or _price_float(product.get("offer_price"))
        or _price_float(pricing.get("offer_price"))
        or _price_float(product.get("mrp"))
        or _price_float(pricing.get("mrp"))
    )
    mrp = (
        _price_float(variant.get("compare_at_price"))
        or _price_float(variant.get("mrp"))
        or ecom_online_compare
        or _price_float(product.get("mrp"))
        or _price_float(pricing.get("mrp"))
    )
    return price, mrp


def _has_publishable_price(
    product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]
) -> bool:
    """EVERY variant resolves to a positive selling price. Pure.

    The publish precondition for a press that did no seeding (an update whose
    variants all already carry their gid). One priceless variant is enough to
    put a 0.00 buy-button on the storefront, so this is `all`, not `any`. A
    product with no catalog_variants rows is judged on its single implied
    default variant, which _resolve_variant_pricing resolves from the product /
    ecom pricing."""
    rows = list(variants or []) or [{}]
    return all(_resolve_variant_pricing(product, v)[0] > 0 for v in rows)


def _variants_for_price_push(
    product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """Effective variant rows for a price/barcode push. Pure.

    The passed catalog_variants rows when there are any; otherwise ONE
    synthesized product-level pseudo-variant carrying the stored DEFAULT-variant
    gid (ecom.shopify_variant_id -- written back by the create-side seeding).
    A product with no catalog_variants rows maps to a single Shopify "Default
    Title" variant, and without this synthesis its price changes (e.g. a
    discount-rule recompute -- OS-016) could never ride the variant-price push:
    build_variant_price_inputs only reads the rows it is given, and
    _resolve_variant_pricing already falls back to the product/ecom pricing for
    an option-less row. UPDATE-only stays true: no stored default gid -> []
    (a clean noop downstream, never a create).

    The pseudo carries the product's gtin AND barcode as SEPARATE candidate
    fields (never pre-collapsed), so build_variant_price_inputs' _publishable_gtin
    gate (#948) evaluates them exactly as for a real variant -- an internally
    minted GS1 20-29 code in product.barcode is rejected, never shipped as a
    public GTIN."""
    rows = list(variants or [])
    if rows:
        return rows
    ecom = product.get("ecom") or {}
    default_gid = ecom.get("shopify_variant_id")
    if not default_gid:
        return []
    pseudo: Dict[str, Any] = {
        "shopify_variant_id": default_gid,
        "gtin": product.get("gtin"),
        "barcode": product.get("barcode"),
    }
    return [pseudo]


def _publishable_gtin(*candidates: Any) -> Optional[str]:
    """The first candidate that is a REAL GTIN, normalised. None when none is.

    This is the last gate before a value becomes customer-visible: whatever we
    put in Shopify's `ProductVariant.barcode` is republished into the Google
    and Meta Shopping feeds. An empty barcode is always safer than a wrong one
    -- a missing GTIN costs some feed match-rate, a WRONG GTIN gets the item
    rejected or matched to another manufacturer's product.

    Two behaviours worth calling out:
      - it picks the first VALID candidate, not the first non-empty one, so a
        junk value on the variant no longer shadows a good GTIN on the parent
        product (the old `or` chain stopped at the first truthy string);
      - `product["barcode"]` is in the fallback chain and, on many rows, holds
        our INTERNALLY minted GS1 20-29 code. classify_gtin rejects that whole
        prefix range as RESTRICTED, so an in-store barcode can no longer leak
        out as if it were a manufacturer GTIN.
    """
    for candidate in candidates:
        cleaned = sanitise_gtin(candidate)
        if cleaned:
            return cleaned
    return None


def build_variant_price_inputs(
    product: Dict[str, Any], variants: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], int]:
    """Build the ProductVariantsBulkInput rows for a price/barcode push. Pure.

    Per variant: id (the stored shopify_variant_id gid), price (selling),
    compareAtPrice (mrp when > price, else EXPLICIT null so a stale
    strikethrough on Shopify is cleared), barcode (the variant's `gtin` --
    the two-barcode model: gtin/barcode IS the GTIN pushed to Shopify;
    `store_barcode` is the physical join key and is NEVER pushed).

    SKIPS (counted, returned as the second tuple member):
      - variants with no stored shopify_variant_id -- they get their gid when
        the product's variants first sync; we never CREATE variants here.
      - variants with no resolvable positive price (never push a 0 price).
    """
    rows: List[Dict[str, Any]] = []
    skipped = 0
    for v in variants or []:
        gid = v.get("shopify_variant_id")
        if not gid:
            skipped += 1
            continue
        price, mrp = _resolve_variant_pricing(product, v)
        if price <= 0:
            skipped += 1
            continue
        row: Dict[str, Any] = {
            "id": _as_shopify_gid(gid, "ProductVariant"),
            "price": f"{price:.2f}",
            "compareAtPrice": f"{mrp:.2f}" if mrp > price else None,
        }
        barcode = _publishable_gtin(v.get("gtin"), v.get("barcode"))
        if barcode:
            row["barcode"] = barcode
        rows.append(row)
    return rows, skipped

