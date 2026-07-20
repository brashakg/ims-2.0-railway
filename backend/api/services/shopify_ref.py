"""
IMS 2.0 - Storefront-keyed Shopify reference accessor  (Phase 0 -- DARK)
=======================================================================
ONE place that answers "where does the Shopify object id for storefront <sid>
live on this IMS doc?" -- so the push engine can, LATER, publish the same IMS
product/variant/collection to MULTIPLE storefronts (BV + WizOpt) without each
storefront's Shopify id fighting over the same field.

THE BYTE-IDENTICAL-BV INVARIANT (the whole reason for the split):
Better Vision (storefront_id == "BV") reads and writes the EXISTING FLAT fields
that every current caller already uses -- unchanged, in place:
    product ecom sub-doc :  ecom.shopify_product_id
    catalog_variants     :  shopify_variant_id
    ecom_collections     :  shopify_collection_id
    ecom_menus           :  shopify_menu_id
    product_images       :  shopify_image_id
For BV, get/set here are exactly `doc.get(field)` / `doc[field] = value` -- so
routing an existing BV call site through this accessor changes NOTHING on disk.

Any OTHER storefront is namespaced under a `storefronts.<sid>` sub-map on the
SAME container, so its ids never collide with BV's flat fields:
    ecom.storefronts.WZ.shopify_product_id
    <variant>.storefronts.WZ.shopify_variant_id
    ...

CONTRACT: `doc` is the CONTAINER the flat field lives on for BV (the `ecom`
sub-doc for a product; the variant / collection / menu / image doc itself).
`field` is the logical ref key ("shopify_product_id", ...). Pure + fail-soft:
get never raises, set mutates the passed dict in place and returns it. This
module does NOT migrate any existing data and does NOT touch indexes.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .storefronts import DEFAULT_STOREFRONT_ID

# The sub-map that holds every NON-default storefront's ref block on a container.
_STOREFRONTS_KEY = "storefronts"


def _is_default(storefront_id: Optional[str]) -> bool:
    """True for the default storefront (BV) -- the flat-field path. A blank /
    None storefront_id also resolves to the default so an unkeyed caller keeps
    today's flat behaviour."""
    sid = (storefront_id or DEFAULT_STOREFRONT_ID).strip() or DEFAULT_STOREFRONT_ID
    return sid == DEFAULT_STOREFRONT_ID


def get_shopify_ref(
    doc: Optional[Dict[str, Any]], storefront_id: str, field: str
) -> Optional[Any]:
    """Read the Shopify ref `field` for `storefront_id` off `doc`.

    BV -> the flat `doc[field]` (byte-identical to today). Other storefront ->
    the nested `doc["storefronts"][sid][field]`. Missing anything -> None.
    Never raises."""
    if not isinstance(doc, dict):
        return None
    if _is_default(storefront_id):
        return doc.get(field)
    sub = doc.get(_STOREFRONTS_KEY)
    if not isinstance(sub, dict):
        return None
    entry = sub.get(storefront_id)
    if not isinstance(entry, dict):
        return None
    return entry.get(field)


def set_shopify_ref(
    doc: Dict[str, Any], storefront_id: str, field: str, value: Any
) -> Dict[str, Any]:
    """Write the Shopify ref `field` = `value` for `storefront_id` onto `doc`,
    mutating it IN PLACE and returning it.

    BV -> sets the flat `doc[field]` (byte-identical to today; NO `storefronts`
    sub-map is ever created for BV). Other storefront -> sets the nested
    `doc["storefronts"][sid][field]`, leaving BV's flat fields untouched."""
    if _is_default(storefront_id):
        doc[field] = value
        return doc
    sub = doc.get(_STOREFRONTS_KEY)
    if not isinstance(sub, dict):
        sub = {}
        doc[_STOREFRONTS_KEY] = sub
    entry = sub.get(storefront_id)
    if not isinstance(entry, dict):
        entry = {}
        sub[storefront_id] = entry
    entry[field] = value
    return doc
