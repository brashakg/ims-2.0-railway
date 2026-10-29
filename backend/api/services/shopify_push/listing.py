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
never shared by two variants. A FIRST publish omits siblings missing an
option (named by SKU; the edit that fills it in queues the page again --
never a re-queue every run, review of #1276). An incomplete lead,
clashing identities or unpriced sent variants still REFUSE publication
(OPTION_MISSING / OPTION_CLASH / UNPRICED_SIZE). A LIVE listing holds
back only the colour(s) at fault, names them by SKU and sends everything
else (review of #1233: one bad colour never freezes a live listing).

ONLY FINISHED COLOURS GO ON BY THEMSELVES (review of #1233): a colour not
yet on the listing joins it only when it is active, not archived, not a
purchase-order draft, not a catalogue draft (a missing cost price aside:
the purchase brings it, owner 2026-10-19) and not in Needs review -- the
catalogue review's approval is the human press. The others WAIT (named in
the plan). A colour already on the listing keeps syncing whatever its state
(switched off or archived: it sells 0, never deleted); an archived one never
counts as a clash -- a colour catalogued again in its place takes over its
variant.

ONE PRICE RULE for every variant, the lead's too: its own engine price, else
its own in-store offer (<= its MRP), else its own MRP, the MRP struck through
when higher (product_input._resolve_variant_pricing).

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
from .media import PHOTO_COLOURS
from .product_input import LISTING_TAGS

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
# A colour catalogued again in the place of an archived one still on the
# listing: it takes over that variant (the archived row lets go of it).
ADOPTS = "_adopts"
ADOPT_GID = "_adopt_variant_id"
ARCHIVED = "_archived"
# A colour held back from a LIVE listing this press (named on the result).
HELD = "_held"
# On a held entry: the option ("colour" / "lens size") the sibling has no
# value for -- the ONE marker missing_option_holds reads (review of #1276:
# never the reason's wording).
MISSING_OPTION = "missing_option"
UNPRICED_SIZE = "UNPRICED_SIZE"
# ecom field on the lead's twin: [colours, sizes] the AI paragraph was written
# for (the colours / sizes ON the listing, never linked drafts that wait).
PARAGRAPH_COUNTS = "model_paragraph_counts"

_ID_FIELDS = ("shopify_variant_id", "shopify_inventory_item_id")
_BARCODE_KEYS = ("gtin", "upc")


def missing_option_holds(listing: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """THE rule for "a sibling left off the page because it has no value for
    an option the model is chosen by". Every reader (the scheduled run's
    warnings, the bulk press's count) uses this, never the reason text."""
    return [h for h in ((listing or {}).get("held") or []) if h.get(MISSING_OPTION)]


def only_missing_options(result: Optional[Dict[str, Any]]) -> bool:
    """A press whose ONLY trouble is siblings left off for a missing option:
    a partial success named by sibling, not a failed lead. Any other hold
    (listing or seeding) keeps the press on its own line."""
    r = result or {}
    omitted = missing_option_holds(r.get("listing"))
    return bool(
        r.get("ok")
        and r.get("code") == OPTION_MISSING
        and omitted
        and len(omitted) == len((r.get("listing") or {}).get("held") or [])
        and not (r.get("variants_seeded") or {}).get("held_data")
    )


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
    if lead is None:
        return None
    if lead.get("variant_of"):
        # The SPINE says this is a colour/size of a model even though its
        # twin's half of the link is missing: it owns no listing either.
        return {"variant_of": lead["variant_of"]}
    children = [c for c in db["products"].find({"variant_of": lead.get("product_id")}) if c.get("sku")]
    if not children:
        return None
    ecom = product.get("ecom") or {}
    live = bool(ecom.get("shopify_product_id"))
    twin_id = str(product.get("id") or lead.get("pim_product_id") or lead.get("product_id"))
    lead_sku = str(product.get("sku") or lead.get("sku"))
    rows_by_sku = {str(r.get("sku")): r for r in variants or [] if isinstance(r, dict) and r.get("sku")}
    twins: Dict[str, Any] = {lead_sku: product}

    def _twin_of(m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        sku = str(m["sku"])
        if sku not in twins:
            twins[sku] = db["catalog_products"].find_one({"sku": sku})
        return twins[sku]

    # Who is on the listing: the lead, every member already on it (an
    # archived colour stays a variant -- never delete a live variant; it
    # sells 0), and every colour READY to go on (owner, review of #1233): a
    # colour joins only once a person has finished it -- active, not
    # archived, not a purchase-order draft, not a catalogue draft, not in
    # Needs review (the catalogue review's approval is the human press). The
    # rest WAIT (named in the plan); nothing is created for them.
    members: List[Dict[str, Any]] = []
    waiting: List[Dict[str, str]] = []
    for m in [lead] + sorted(children, key=pm.lead_order):
        row = rows_by_sku.get(str(m.get("sku")))
        if m is lead or (row and row.get("shopify_variant_id")):
            members.append(m)
            continue
        why = _not_ready(pm, m, _twin_of(m))
        if why:
            waiting.append({"sku": str(m["sku"]), "reason": why})
        else:
            members.append(m)
    # An archived colour still on the listing is taken down (sells 0, never
    # deleted): it never makes a clash with a colour catalogued again in its
    # place -- that one takes over its variant instead (ADOPTS, below).
    archived_listed = {str(m["sku"]) for m in members if m is not lead and pm._is_archived(m)}
    values, clashes = pm.family_option_values(members)
    clashes = [g for g in ([x for x in grp if x not in archived_listed] for grp in clashes) if len(g) > 1]

    rows: List[Dict[str, Any]] = []
    for m in members:
        sku = str(m["sku"])
        full = dict(values[sku])
        row = rows_by_sku.get(sku)
        if row is not None:
            r = copy.deepcopy(row)
            if m is lead and not r.get("shopify_variant_id") and _ecom_ids_are_the_leads(db, ecom, lead_sku):
                # A live listing made before the lead had a row with its ids:
                # the listing's default variant IS the lead's own -- unless
                # another IMS row already owns it (never adopted then).
                ids = {f: ecom.get(f) for f in _ID_FIELDS if ecom.get(f)}
                r.update(ids)
                r[ROW_SET] = {**ids, **full}
        elif m is lead:
            ids = (
                {f: ecom.get(f) for f in _ID_FIELDS if ecom.get(f)}
                if _ecom_ids_are_the_leads(db, ecom, lead_sku)
                else {}
            )
            r = {"sku": sku, "parent_product_id": twin_id, "parent_sku": lead_sku, **ids}
            r[ROW_SET] = {"parent_product_id": twin_id, "parent_sku": lead_sku, **ids, **full}
        else:
            # PENDING: on the model in IMS, not yet on this listing. Built
            # from its spine -- never a copy of its row's Shopify ids.
            attrs = m.get("attributes") or {}
            r = {"sku": sku, "parent_product_id": twin_id, "parent_sku": lead_sku, "mrp": m.get("mrp")}
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
        # ONE price rule for every variant, the lead's too (owner, review of
        # #1233; product_input._resolve_variant_pricing): its own engine
        # price, else its own in-store offer (<= its MRP), else its own MRP.
        # Never another item's price.
        r[OWN_PRICE] = True
        if m is lead:
            pricing = product.get("pricing") or {}
            r["mrp"] = product.get("mrp") or pricing.get("mrp") or lead.get("mrp")
            r["offer_price"] = product.get("offer_price") or pricing.get("offer_price") or lead.get("offer_price")
        else:
            r["mrp"] = r.get("mrp") or m.get("mrp")
            r["offer_price"] = m.get("offer_price")
        if pm._is_archived(m):
            r[ARCHIVED] = True
        rows.append(r)

    # A row on this listing that belongs to no member we keep: on Shopify it
    # stays exactly as it is; never created (an archived or waiting colour)
    # otherwise.
    member_skus = {str(m["sku"]) for m in members}
    family_skus = member_skus | {str(c["sku"]) for c in children}
    for sku, row in rows_by_sku.items():
        if sku in member_skus or (sku in family_skus and not row.get("shopify_variant_id")):
            continue
        rows.append(copy.deepcopy(row))

    _price_pending(db, product, rows)

    # WHAT EACH NEW COLOUR NEEDS. First publication keeps its lead and refuses
    # ambiguous identities, but can omit a sibling missing an option.
    # a LIVE listing holds back only the colour(s) at fault and sends the
    # rest -- one bad colour never freezes a live listing (owner, review of
    # #1233). Held colours are named by SKU on the result.
    from .product_input import _resolve_variant_pricing

    held: List[Dict[str, str]] = []
    by_sku = {str(r.get("sku")): r for r in rows}

    def _creating(sku: str) -> bool:
        return sku in member_skus and not (by_sku.get(sku) or {}).get("shopify_variant_id")

    def _hold(sku: str, code: str, reason: str, missing: Optional[str] = None) -> None:
        if not any(h["sku"] == sku for h in held):
            held.append({"sku": sku, "code": code, "reason": reason, **({MISSING_OPTION: missing} if missing else {})})
            by_sku[sku][HELD] = True

    def _not_held(ms: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [m for m in ms if not (by_sku[str(m["sku"])].get(HELD) and _creating(str(m["sku"])))]

    refusal = None
    if live:
        for m in members:
            sku = str(m["sku"])
            if not by_sku[sku].get(ARCHIVED) and _resolve_variant_pricing(product, by_sku[sku])[0] <= 0:
                # No MRP is no price (Indian MRP display rule, review of #1233).
                why = "no MRP" if not _positive(by_sku[sku].get("mrp")) else "no selling price"
                _hold(sku, UNPRICED_SIZE, why if _creating(sku)
                      else f"{why} -- keeps its last price and MRP on the website")
        for grp in clashes:
            for sku in grp:
                if _creating(sku):
                    _hold(sku, OPTION_CLASH, "looks the same as " + " / ".join(x for x in grp if x != sku))
        kept = _not_held(members)
        axes = _axes_of(kept, values)
        for m in kept:
            sku = str(m["sku"])
            for name, field in AXES:
                if _creating(sku) and name in axes and not str(values[sku][field] or "").strip():
                    what = "colour" if name == "Color" else "lens size"
                    _hold(sku, OPTION_MISSING, f"no {what}", missing=what)
        members = _not_held(kept)
        axes = _axes_of(members, values)
        # A colour that would ADD an option the listing's own variants have no
        # value for (a live lead with no size, a new colour with one) is held
        # -- the listing cannot take that option; the lead's own edits still
        # go out (review of #1233; was a refusal re-queued every run).
        listed = [m for m in members if not _creating(str(m["sku"]))]
        for name, field in AXES:
            if name not in axes or not listed:
                continue
            have = {str(values[str(m["sku"])][field] or "").strip().lower() for m in listed}
            if have != {""}:
                continue
            for m in members:
                sku = str(m["sku"])
                if _creating(sku) and str(values[sku][field] or "").strip():
                    what = "colour" if name == "Color" else "size"
                    _hold(sku, OPTION_MISSING, f"adds a {what} the listing has none of -- fill the {what} of "
                          + ", ".join(str(x["sku"]) for x in listed) + " first")
        members = _not_held(members)
        axes = _axes_of(members, values)
        dropped = {h["sku"] for h in held if _creating(h["sku"])}
        rows = [r for r in rows if str(r.get("sku")) not in dropped]
    else:
        axes = _axes_of(members, values)
        if len(members) > 1 and any(_creating(str(m["sku"])) for m in members):
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
                    (str(m['sku']), 'colour' if name == 'Color' else 'lens size')
                    for m in members
                    if _creating(str(m["sku"]))
                    for name, field in AXES
                    if name in axes and not str(values[str(m["sku"])][field] or "").strip()
                ]
                if any(sku == lead_sku for sku, _ in missing):
                    refusal = (
                        OPTION_MISSING,
                        "refused: the website chooses this model by "
                        + " and ".join(a.lower() for a in axes)
                        + ", and there is no "
                        + ", no ".join(f"{field} on {sku}" for sku, field in missing)
                        + " -- fill it in, then press again. Nothing was sent",
                    )
                else:
                    for sku, field in missing:
                        _hold(sku, OPTION_MISSING, f"no {field}", missing=field)
                    members = _not_held(members)
                    axes = _axes_of(members, values)
                    dropped = {h["sku"] for h in held}
                    rows = [r for r in rows if str(r.get("sku")) not in dropped]

    # The option values each row is chosen by on the website: exactly the
    # axes the model (as sent) varies on.
    for r in rows:
        if FULL_OPTIONS in r:
            for name, field in AXES:
                r[field] = r[FULL_OPTIONS][field] if name in axes else ""

    # A colour catalogued AGAIN in the place of an archived one still on the
    # listing (same colour and size) takes over its variant: the slot is
    # reused, nothing is deleted. It is handed the archived row's STORED
    # variant id up front (ADOPT_GID -- matched by id, never by today's
    # label, which may differ from the one Shopify shows: review of #1233),
    # and the archived row lets go of it only when the id written back is
    # that one (writeback._writeback_variant).
    # Never from a colour whose permanent delete holds its claim (#1273): its
    # variant is about to be deleted on Shopify, and adopting it now would
    # hand the new colour a variant that disappears under it.
    from ..product_purge import claim_is_live

    being_purged = {str(m["sku"]) for m in members if claim_is_live(m)}
    for r in list(rows):
        if r.get("shopify_variant_id") or not r.get(FULL_OPTIONS) or str(r.get("sku")) not in member_skus:
            continue
        mine = {k: str(v).strip().lower() for k, v in r[FULL_OPTIONS].items()}
        for a in sorted(archived_listed - being_purged):
            other = by_sku.get(a) or {}
            if other.get(FULL_OPTIONS) and {k: str(v).strip().lower() for k, v in other[FULL_OPTIONS].items()} == mine:
                r[ADOPTS] = a
                r[ADOPT_GID] = other.get("shopify_variant_id")
                rows = [x for x in rows if str(x.get("sku")) != a]
                archived_listed = archived_listed - {a}
                break

    # The model's words, photos, tags and metafields: its colours on sale
    # (an archived one still on the listing is not offered any more).
    on_offer = [m for m in members if m is lead or not pm._is_archived(m)]
    content = _model_content(db, product, lead, on_offer, values, axes, twins)
    return {
        "lead_sku": lead_sku,
        "members": [str(m["sku"]) for m in members],
        "rows": rows,
        "axes": axes,
        "refusal": refusal,
        "held": held,
        "waiting": waiting,
        **content,
    }


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _axes_of(members: List[Dict[str, Any]], values: Dict[str, Dict[str, str]]) -> List[str]:
    """The option names these members VARY on (a blank counts as a value)."""
    return [
        name
        for name, field in AXES
        if len({str(values[str(m["sku"])][field] or "").strip().lower() for m in members}) > 1
    ]


def _not_ready(pm, spine: Dict[str, Any], twin: Optional[Dict[str, Any]]) -> Optional[str]:
    """Why a colour not yet on the listing may not go on it by itself (the
    catalogue review's approval is the human press), or None."""
    if pm._is_archived(spine):
        return "archived"
    if spine.get("is_active") is False:
        return "switched off"
    if pm.is_ordered_draft(spine):
        return "a purchase-order draft -- finish it in Needs review"
    # Finished = every catalogue field but the cost price (owner 2026-10-19:
    # cost is internal; the purchase brings it) -- gaps_before_cost, the one
    # rule the bill gate and send_po already ask.
    if pm.effective_catalog_status(spine) != "ACTIVE" and pm.gaps_before_cost(spine):
        return "a catalogue draft -- finish it first"
    if (twin or {}).get("needs_review"):
        return "waiting in Needs review"
    return None


def first_publish_not_ready(db, product: Dict[str, Any]) -> Optional[str]:
    """Why this listing (a model's lead or a single product) may not go on the
    website for the FIRST time, or None -- the SAME rule a colour joining a
    listing is held to (_not_ready). A listing already on Shopify keeps
    syncing whatever its state (it is taken down by its own door). Raises on
    a read failure."""
    if (product.get("ecom") or {}).get("shopify_product_id"):
        return None
    from .. import product_master as pm

    if pm._is_archived(product):
        return "archived"
    # A caller holding the product row itself (product_id, no twin id) asks
    # about it directly; a catalogue twin reads its product.
    is_spine = bool(product.get("product_id")) and not product.get("id")
    spine = product if is_spine else lead_spine(db, product)
    if spine is None:
        # No product row (none on prod, 2026-10-19): the twin is judged as
        # before this gate -- its archived check above.
        return None
    return _not_ready(pm, spine, None if is_spine else product)


def _ecom_ids_are_the_leads(db, ecom: Dict[str, Any], lead_sku: str) -> bool:
    """The listing's stored default variant may be adopted as the LEAD's own
    only when no other IMS row already owns it (review of #1233: a colour's
    variant id once landed there). Raises on a read failure."""
    gid = ecom.get("shopify_variant_id")
    if not gid:
        return False
    from agents.nexus_providers import _as_shopify_gid

    full = _as_shopify_gid(gid, "ProductVariant")
    forms = sorted({str(gid), full, full.rsplit("/", 1)[-1]})
    owner = db["catalog_variants"].find_one({"shopify_variant_id": {"$in": forms}, "sku": {"$ne": lead_sku}})
    return owner is None


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


def _model_content(db, product, lead, members, values, axes, twins=None) -> Dict[str, Any]:
    """Title, SEO, handle, body, tags, photos and metafields of the MODEL."""
    from .. import product_master as pm
    from ..product_description import build_model_description_html, default_model_paragraph
    from ..product_naming import (
        build_model_handle,
        build_model_seo_description,
        build_model_seo_title,
        build_model_title,
        readable_colour,
        resolve_category_word,
    )
    from ..shopify_tag_gen import listing_tags
    from .media import product_photo_urls
    from .product_input import build_product_metafields, ims_product_tags

    ecom = product.get("ecom") or {}
    lead_doc = {**lead, "attributes": lead.get("attributes") or {}}
    spec = pm.category_spec(lead.get("category"))
    # The shopper's word ("Sunglasses", "Eyeglasses") -- the model line and
    # paragraph read as a page title, not an internal category.
    category_name = resolve_category_word(lead.get("category")) or (
        spec.display if spec is not None else str(lead.get("category") or "")
    )
    colours = list(dict.fromkeys(values[m["sku"]]["option_color"] for m in members if values[m["sku"]]["option_color"]))
    sizes = list(dict.fromkeys(values[m["sku"]]["option_size"] for m in members if values[m["sku"]]["option_size"]))

    known = dict(twins or {})
    twins = {}
    for m in members:
        sku = str(m["sku"])
        if sku in known:
            twin = known[sku]
        else:
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
    # The AI paragraph says how many colours / sizes the model comes in: it is
    # used only while that is still what the listing carries (the count it
    # was written for, scripts/write_model_paragraphs.py) -- else the
    # deterministic one, which counts this listing's own.
    written_for = ecom.get(PARAGRAPH_COUNTS)
    paragraph = (
        ecom.get("model_paragraph") if written_for == [len(colours), len(sizes)] else None
    ) or default_model_paragraph(category_name, lead_doc["attributes"], len(colours), len(sizes))
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

    photos: List[str] = []
    variant_photos: Dict[str, str] = {}
    photo_colours: Dict[str, str] = {}
    for m in members:
        twin = twins[str(m["sku"])]
        own = product_photo_urls(twin)
        if own:
            variant_photos[str(m["sku"])] = own[0]
        colour = readable_colour(twin) or values[m["sku"]]["option_color"]
        for u in own:
            if u not in photos:
                photos.append(u)
                photo_colours[u] = colour
    # ONE rule for a listing's tags: the union over its colours, 'Polarized'
    # only when every colour is (shopify_tag_gen.listing_tags).
    tags = listing_tags([ims_product_tags(twins[str(m["sku"])]) for m in members])

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
        "photo_colours": photo_colours,
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
    view[LISTING_TAGS] = list(listing["tags"])
    view[PHOTO_COLOURS] = dict(listing.get("photo_colours") or {})
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
        # Held back from a live listing this press (fix them and press again)
        # and colours waiting for a person (catalogue review) -- by SKU.
        "held": list(listing.get("held") or []),
        "waiting": list(listing.get("waiting") or []),
    }
