"""Shopify push -- one website listing per MODEL (G2, owner 2026-10-18)

THE RULING. Same model number in other colours / sizes = variants of ONE
product, on the website as in IMS (G1 #1226 holds the family: spine
``variant_of`` = the lead's product_id). The lead -- the FIRST catalogued --
owns the listing (its twin carries ``ecom.shopify_product_id``); every
colour / size keeps its own SKU, maker GTIN, price and per-shop stock as a
Shopify variant of that listing. The listing's title, SEO, handle and body
are written for the MODEL (model-level fields only, never one colour); the
per-colour attributes ride each variant (options, metafields, its photo).

``model_listing`` is the ONE reader of a model for the push: given the lead's
twin and its catalog_variants rows it returns the rows the listing must have
(lead first, then first-catalogued order), each carrying the option values it
is chosen by, plus the model-level content. A product that leads no model
returns None and pushes exactly as before.

OPTIONS come from what VARIES in the model: colour only -> Color, size only
-> Size, both -> Color + Size. The values are G1's (product_master.
family_option_values: 'Gold (001)'; two members of one frame colour get their
lens label, 'Gold (001) / Green Polarized'). A value is never left blank and
never shared by two variants: a model that would need either is REFUSED
before anything is sent (OPTION_MISSING / OPTION_CLASH), but only when the
press would create a variant -- a press that adds nothing never asks.

A COLOUR NOT YET ON THE LISTING (linked under a live lead by G1 with its own
row) rides the press as a PENDING row: built fresh from its spine (its own
mrp, maker GTIN, engine price), never carrying a Shopify id, so it can never
share the lead's inventory item. Its IMS row moves under the listing in the
SAME write that stores the Shopify variant id Shopify just minted for it
(writeback._writeback_variant applies ``ROW_SET``) -- the IMS row and the
Shopify variant appear together, never one without the other.

Read failures RAISE (the push refuses, fail-closed): a model that could not
be read is never pushed as a one-colour listing.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from ._shared import logger

OPTION_CLASH = "OPTION_CLASH"
OPTION_MISSING = "OPTION_MISSING"
FAMILY_UNREADABLE = "FAMILY_UNREADABLE"

# (Shopify option name, catalog_variants field) -- in listing order.
AXES = (("Color", "option_color"), ("Size", "option_size"))
# The fields a listing row writes onto its catalog_variants row WHEN its
# Shopify variant id lands (writeback._writeback_variant).
ROW_SET = "_listing_set"
# The row's option values on every axis (what it is, whatever the listing
# shows) -- the seeding step re-keys on the listing's real axes with it.
FULL_OPTIONS = "_full_options"
PENDING = "_pending"
OWN_PRICE = "_own_price"

_ID_FIELDS = ("shopify_variant_id", "shopify_inventory_item_id")
_BARCODE_KEYS = ("gtin", "upc")


def _metafield_key(key: Any) -> str:
    """The ims.* metafield key build_product_metafields derives from an
    attribute key."""
    return str(key).strip().lower().replace(" ", "_").replace("-", "_")[:30]


def _same(a: Any, b: Any) -> bool:
    return str(a if a is not None else "").strip().lower() == str(b if b is not None else "").strip().lower()


def lead_spine(db, product: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The spine of the twin being pushed (by sku, then pim_product_id).
    Raises on a read failure."""
    products = db["products"]
    sku = str((product or {}).get("sku") or "").strip()
    spine = products.find_one({"sku": sku}) if sku else None
    if spine is None and product.get("id"):
        spine = products.find_one({"pim_product_id": product["id"]})
    return spine


def model_listing(
    db, product: Dict[str, Any], variants: Optional[List[Dict[str, Any]]]
) -> Optional[Dict[str, Any]]:
    """The model listing for the lead twin ``product``, or None when it leads
    no model (no linked colour / size). Raises on a read failure."""
    from .. import product_master as pm

    lead = lead_spine(db, product)
    if lead is None or lead.get("variant_of"):
        return None
    children = [c for c in db["products"].find({"variant_of": lead.get("product_id")}) if c.get("sku")]
    if not children:
        return None
    ecom = product.get("ecom") or {}
    twin_id = str(product.get("id") or lead.get("pim_product_id") or lead.get("product_id"))
    lead_sku = str(product.get("sku") or lead.get("sku"))
    rows_by_sku = {str(r.get("sku")): r for r in variants or [] if isinstance(r, dict) and r.get("sku")}

    # Who is on the listing: the lead, every member already on it (an
    # archived colour stays a variant -- never delete a live variant; it
    # sells 0), and every member not archived.
    members: List[Dict[str, Any]] = []
    for m in [lead] + sorted(children, key=pm.lead_order):
        row = rows_by_sku.get(str(m.get("sku")))
        on_listing = bool(row and row.get("shopify_variant_id"))
        if m is lead or on_listing or not pm._is_archived(m):
            members.append(m)
    values, clashes = pm.family_option_values(members)

    distinct = {
        field: {str(values[m["sku"]][field] or "").strip().lower() for m in members}
        for _name, field in AXES
    }
    axes = [name for name, field in AXES if len(distinct[field]) > 1]

    rows: List[Dict[str, Any]] = []
    for m in members:
        sku = str(m["sku"])
        full = dict(values[sku])
        shown = {field: (full[field] if name in axes else "") for name, field in AXES}
        row = rows_by_sku.get(sku)
        if row is not None:
            r = copy.deepcopy(row)
            r.update(shown)
            if m is lead and not r.get("shopify_variant_id") and ecom.get("shopify_variant_id"):
                # A live listing made before the lead had a row with its ids:
                # the listing's default variant IS the lead's own.
                ids = {f: ecom.get(f) for f in _ID_FIELDS if ecom.get(f)}
                r.update(ids)
                r[ROW_SET] = {**ids, **full}
        elif m is lead:
            ids = {f: ecom.get(f) for f in _ID_FIELDS if ecom.get(f)}
            r = {"sku": sku, "parent_product_id": twin_id, "parent_sku": lead_sku, **shown, **ids}
            r[ROW_SET] = {"parent_product_id": twin_id, "parent_sku": lead_sku, **ids, **full}
        else:
            # PENDING: on the model in IMS, not yet on this listing. Built
            # from its spine -- never a copy of its row's Shopify ids.
            attrs = m.get("attributes") or {}
            r = {"sku": sku, "parent_product_id": twin_id, "parent_sku": lead_sku, "mrp": m.get("mrp"), **shown}
            if attrs.get("gtin"):
                r["gtin"] = attrs["gtin"]
            r[PENDING] = True
            r[ROW_SET] = {
                "parent_product_id": twin_id,
                "parent_sku": lead_sku,
                "mrp": m.get("mrp"),
                **({"gtin": attrs["gtin"]} if attrs.get("gtin") else {}),
                **full,
            }
        r[FULL_OPTIONS] = full
        if m is not lead:
            # Its OWN price, never the lead's (prices may differ per colour /
            # size): product_input._resolve_variant_pricing reads only the row.
            r[OWN_PRICE] = True
            if not r.get("mrp"):
                r["mrp"] = m.get("mrp")
        rows.append(r)

    # A row on this listing that belongs to no member we keep: on Shopify it
    # stays exactly as it is; never created (an archived colour) otherwise.
    member_skus = {str(m["sku"]) for m in members}
    family_skus = member_skus | {str(c["sku"]) for c in children}
    for sku, row in rows_by_sku.items():
        if sku in member_skus or (sku in family_skus and not row.get("shopify_variant_id")):
            continue
        rows.append(copy.deepcopy(row))

    _price_pending(db, product, rows)

    refusal = None
    creating = [r for r in rows if not r.get("shopify_variant_id")]
    if creating and len(members) > 1:
        if clashes or not axes:
            groups = clashes or [[str(m["sku"]) for m in members]]
            refusal = (
                OPTION_CLASH,
                "refused: two colours/sizes of this model would look the same on the website ("
                + "; ".join(" and ".join(g) for g in groups)
                + ") -- give one a different colour name, lens colour or size, then press again. "
                "Nothing was sent",
            )
        else:
            missing = [
                f"{'colour' if name == 'Color' else 'size'} on {r['sku']}"
                for r in creating
                for name, field in AXES
                if name in axes and not str(r.get(field) or "").strip()
            ]
            if missing:
                refusal = (
                    OPTION_MISSING,
                    "refused: the website chooses this model by "
                    + " and ".join(a.lower() for a in axes)
                    + ", and there is no "
                    + ", no ".join(missing)
                    + " -- fill it in, then press again. Nothing was sent",
                )

    content = _model_content(db, product, lead, members, values, axes)
    return {
        "lead_sku": lead_sku,
        "members": [str(m["sku"]) for m in members],
        "rows": rows,
        "axes": axes,
        "refusal": refusal,
        **content,
    }


def _price_pending(db, product: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    """The online discount engine's price for each PENDING row (the family's
    rule over its own MRP), in memory -- persisted with the row when its
    variant lands. Fail-soft: no rule, or the engine down -> the row's MRP."""
    pending = [r for r in rows if r.get(PENDING)]
    if not pending:
        return
    try:
        from ..online_discount_engine import _load_rules, recompute_online_price

        for r in pending:
            for f in ("discounted_price", "compare_at_price", "online_price_meta"):
                r.pop(f, None)
        recompute_online_price(
            copy.deepcopy(product), db=None, variants=pending, rules=_load_rules(db), persist=False
        )
        for r in pending:
            r[ROW_SET].update(
                {f: r.get(f) for f in ("discounted_price", "compare_at_price", "online_price_meta")}
            )
    except Exception:  # noqa: BLE001 -- the price falls back to the row's MRP
        logger.warning("[SHOPIFY_PUSH] engine price for new colours skipped", exc_info=True)


def _model_content(db, product, lead, members, values, axes) -> Dict[str, Any]:
    """Title, SEO, handle, body, tags, photos and metafields of the MODEL."""
    from .. import product_master as pm
    from ..product_description import build_model_description_html, default_model_paragraph
    from ..product_naming import (
        build_model_handle,
        build_model_seo_description,
        build_model_seo_title,
        build_model_title,
    )
    from .media import product_photo_urls
    from .product_input import build_product_metafields, ims_product_tags

    ecom = product.get("ecom") or {}
    lead_doc = {**lead, "attributes": lead.get("attributes") or {}}
    spec = pm.category_spec(lead.get("category"))
    category_name = spec.display if spec is not None else str(lead.get("category") or "")
    colours = list(dict.fromkeys(values[m["sku"]]["option_color"] for m in members if values[m["sku"]]["option_color"]))
    sizes = list(dict.fromkeys(values[m["sku"]]["option_size"] for m in members if values[m["sku"]]["option_size"]))

    twins: Dict[str, Dict[str, Any]] = {}
    for m in members:
        sku = str(m["sku"])
        twin = product if sku == str(product.get("sku")) else db["catalog_products"].find_one({"sku": sku})
        twins[sku] = twin or {"sku": sku, "attributes": m.get("attributes") or {}, "brand": m.get("brand"),
                              "category": m.get("category"), "ecom": {}}

    info = [
        {
            "sku": str(m["sku"]),
            "attributes": m.get("attributes") or {},
            "colour": values[m["sku"]]["option_color"],
            "size": values[m["sku"]]["option_size"] if "Size" in axes else "",
        }
        for m in members
    ]
    paragraph = ecom.get("model_paragraph") or default_model_paragraph(
        category_name, lead_doc["attributes"], len(colours), len(sizes)
    )
    attr_sets = [m.get("attributes") or {} for m in members]
    keys = list(dict.fromkeys(k for a in attr_sets for k in a))
    shared = {
        k: attr_sets[0].get(k)
        for k in keys
        if k.lower() not in _BARCODE_KEYS and all(k in a and _same(a.get(k), attr_sets[0].get(k)) for a in attr_sets)
    }
    html = ecom.get("model_description_html") or ""
    if not html and pm.resolve_category(lead.get("category")) == "SMARTGLASSES":
        # A smart glass's body is its spec bullets (smartglass_listing, as
        # the live ones are) -- built from what every colour shares.
        from ..product_description import variant_table_html
        from ..smartglass_listing import build_description_html

        body = build_description_html(shared, paragraph)
        html = "\n".join(p for p in (body, variant_table_html(info)) if p) if body else ""
    if not html:
        html = build_model_description_html(category_name, lead_doc["attributes"], info, paragraph)

    tags: List[str] = []
    photos: List[str] = []
    variant_photos: Dict[str, str] = {}
    for m in members:
        twin = twins[str(m["sku"])]
        for t in ims_product_tags(twin):
            if t not in tags:
                tags.append(t)
        own = product_photo_urls(twin)
        if own:
            variant_photos[str(m["sku"])] = own[0]
        for u in own:
            if u not in photos:
                photos.append(u)

    # Model-level metafields carry what EVERY member shares; the rest (and
    # the maker barcode, always) are per colour/size -- variant metafields,
    # and taken off the product so the listing never shows one colour's.
    per_variant = [k for k in keys if k not in shared]
    variant_metafields = {
        str(m["sku"]): build_product_metafields(
            {"attributes": {k: v for k, v in (m.get("attributes") or {}).items() if k in per_variant}}
        )
        for m in members
    }
    removed = sorted({_metafield_key(k) for k in per_variant} | set(_BARCODE_KEYS))
    return {
        "title": build_model_title(lead_doc),
        "seo_title": build_model_seo_title(lead_doc),
        "seo_description": build_model_seo_description(lead_doc, len(colours), len(sizes)),
        # A live listing keeps its handle (no URL ever breaks); a new one is
        # the slug of the model title.
        "handle": None if ecom.get("shopify_product_id") else build_model_handle(lead_doc),
        "description_html": html,
        "tags": tags,
        "photos": photos,
        "variant_photos": variant_photos,
        "metafields": build_product_metafields({"attributes": shared}),
        "removed_metafields": [{"namespace": "ims", "key": k} for k in removed],
        "variant_metafields": variant_metafields,
        "colours": colours,
        "sizes": sizes,
    }


def listing_view(product: Dict[str, Any], listing: Dict[str, Any]) -> Dict[str, Any]:
    """The lead twin as the push sends it for its model: the model title,
    SEO, body and photos; the handle kept (live) or the model's (new). Every
    other field -- the Shopify ids, the queue revision, the ledgers -- is the
    twin's own, so every write-back lands on the twin."""
    view = copy.deepcopy(product)
    ecom = dict(view.get("ecom") or {})
    seo = dict(ecom.get("seo") or {})
    # tags: every member's own tags + attribute tags (colours, sizes), so a
    # collection filter finds the model by any of its variants.
    seo.update(
        {
            "title": listing["seo_title"],
            "description": listing["seo_description"],
            "html": listing["description_html"],
            "tags": list(listing["tags"]),
        }
    )
    ecom["seo"] = seo
    if listing["handle"]:
        ecom["handle"] = listing["handle"]
    else:
        ecom.pop("handle", None)
    view["ecom"] = ecom
    view["title"] = view["name"] = listing["title"]
    view["images"] = list(listing["photos"])
    for legacy in ("image_url", "image"):
        view.pop(legacy, None)
    return view


def plan_summary(listing: Dict[str, Any]) -> Dict[str, Any]:
    """What the dry-run shows of the model: its options, every variant with
    its option values, whether it is already on Shopify, and its photo."""
    return {
        "lead_sku": listing["lead_sku"],
        "title": listing["title"],
        "handle": listing["handle"],
        "options": [
            {
                "name": name,
                "values": list(dict.fromkeys(str(r.get(field)) for r in listing["rows"] if r.get(field))),
            }
            for name, field in AXES
            if name in listing["axes"]
        ],
        "variants": [
            {
                "sku": r.get("sku"),
                **{name: r.get(field) for name, field in AXES if name in listing["axes"]},
                "on_shopify": bool(r.get("shopify_variant_id")),
                "photo": listing["variant_photos"].get(str(r.get("sku"))),
            }
            for r in listing["rows"]
        ],
        "refusal": (listing["refusal"] or (None, None))[1],
    }
