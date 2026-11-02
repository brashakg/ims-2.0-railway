"""Read-only SKU parity and owned-content comparison for the sync screen.

Reuse the push planner and payload builders: diagnostics must not invent a
second family, price or tag rule. No writer or live push is called here.
"""

from collections import Counter
from decimal import Decimal

from . import online_block
from .online_catalog import is_archived_row, product_online_state, skus_on_shopify, variant_rows_for_product
from .shopify_push._shared import is_variant_of, product_push_refusal
from .shopify_push.listing import UNPRICED_SIZE, model_listing, listing_view
from .shopify_push.media import product_photo_urls
from .shopify_push.product import press_unpriced_sizes
from .shopify_push.product_input import build_product_input, ims_product_tags
from .shopify_push.tags import plan_product_tags
from .shopify_push.variants import build_variant_seed_rows


def listing_plan(db, product):
    """The writer's in-memory family plan, with strict variant reads."""
    rows = variant_rows_for_product(db, product, strict=True)
    return rows, model_listing(db, product, rows)


def colour_size_parity(db):
    """A catalog SKU is online iff a Shopify variant is mapped to it --
    online_catalog.skus_on_shopify, the one rule Store Health reads too.

    Counts include archived catalog rows, as the legacy denominator does.
    'Online' means mapped, not storefront visibility or sellable stock.
    Missing reasons are evaluated now, never guessed from an old run failure.
    """
    unknown = {"checked": False, "reason": "Colour/size mappings could not be read"}
    if db is None:
        return unknown
    try:
        products = list(db["catalog_products"].find({}, {"_id": 0}))
        spines = list(db["products"].find({}, {"_id": 0, "sku": 1, "product_id": 1, "variant_of": 1,
                                                   "deleted_at": 1, "is_archived": 1, "is_active": 1, "status": 1}))
        online = skus_on_shopify(db, [p.get("sku") for p in products])
    except Exception:  # fail soft, never present an unreadable join as zero online
        return unknown
    twins_by_id = {str(p.get("id") or p.get("product_id")): p for p in products}
    twins_by_sku = {str(p.get("sku") or "").strip(): p for p in products}
    spine_by_sku = {str(p.get("sku") or "").strip(): p for p in spines}
    spine_by_id = {str(p.get("product_id")): p for p in spines}
    verdicts = {}

    def verdict_of(lead):
        key = str(lead.get("id") or lead.get("sku"))
        if key not in verdicts:
            verdicts[key] = _press_verdict(db, lead)
        return verdicts[key]

    missing = []
    for product in products:
        sku = str(product.get("sku") or "").strip()
        if sku and sku in online:
            continue
        spine = spine_by_sku.get(sku, {})
        lead = model_lead(product, spine, twins_by_id.get, twins_by_sku.get, spine_by_id.get)
        try:
            code, reason = sku_hold(product, spine, lead, verdict_of) or verdict_of(lead)[:2]
        except Exception:
            code, reason = None, "family readiness could not be read"
        missing.append({"sku": sku, "product_id": product.get("id") or product.get("product_id"),
                        "reason": reason, "code": code})
    pages = {str((p.get("ecom") or {}).get("shopify_product_id")) for p in products
             if (p.get("ecom") or {}).get("shopify_product_id")}
    return {"checked": True, "total": len(products), "online": len(products) - len(missing),
            "website_pages": len(pages), "missing": len(missing), "missing_items": missing}


def model_lead(product, spine, twin_by_id, twin_by_sku, spine_by_id):
    """The lead twin whose press carries this SKU: its twin's ecom.variant_of
    (twin_id, else the parent's product_id / sku), else its spine's
    variant_of; the product itself when it is no colour/size of another;
    None when the lead cannot be found. The lookups are the caller's -- the
    sweep's in-memory maps, the form's reads (model_hold)."""
    link = (product.get("ecom") or {}).get("variant_of") or spine.get("variant_of")
    parent_id = link.get("product_id") if isinstance(link, dict) else link
    lead = twin_by_id(str(link.get("twin_id"))) if isinstance(link, dict) else None
    if lead is not None:
        return lead
    if not parent_id:
        return product
    parent_sku = str((spine_by_id(str(parent_id)) or {}).get("sku") or "")
    return twin_by_id(str(parent_id)) or (twin_by_sku(parent_sku) if parent_sku else None)


def sku_hold(product, spine, lead, verdict_of):
    """What keeps ONE catalogue SKU off its listing's press, as (code, reason)
    -- its own row, its lead, the press's refusal (verdict_of(lead) ->
    _press_verdict), then this SKU's hold or wait in the plan -- or None when
    the press carries it. The sync screen's per-SKU rule; the form's website
    line asks it too (model_hold). Raises on a read failure."""
    sku = str(product.get("sku") or "").strip()
    if not sku:
        return None, "SKU missing - cannot match a website colour/size"
    if is_archived_row(product, spine, product.get("ecom") or {}):
        return None, "archived"
    if lead is None:
        return None, "family lead could not be found"
    if (lead.get("ecom") or {}).get("taken_down_at"):
        return None, "taken down - not queued for automatic publish"
    code, reason, plan = verdict_of(lead)
    if code is not None:
        return code, reason
    held = next((h for h in (plan or {}).get("held", []) if h.get("sku") == sku), None)
    if held:
        return held.get("code"), "colour/size held - " + (held.get("code") or "") + ": " + held.get("reason", "")
    waiting = next((h for h in (plan or {}).get("waiting", []) if h.get("sku") == sku), None)
    if waiting:
        return None, "not ready - " + waiting.get("reason", "")
    return None


def model_hold(db, product, spine):
    """sku_hold for ONE colour/size of a model, read from the database (the
    Add/Edit form's website line): the sync screen's reason, or None when its
    model's press carries it. Raises on a read failure."""
    twins, spines = db["catalog_products"], db["products"]
    lead = model_lead(product, spine, lambda i: twins.find_one({"id": i}),
                      lambda s: twins.find_one({"sku": s}), lambda i: spines.find_one({"product_id": i}))
    hold = sku_hold(product, spine, lead, lambda lead: _press_verdict(db, lead))
    return hold[1] if hold else None


PUSH_THE_MODEL = "a colour/size of another model - it goes on the website with its model's page"


def _press_verdict(db, lead):
    """(code, reason, plan) for a press of this listing, asked of the writer's
    OWN gates in push_product's order -- never a re-derived queue rule. A
    refusal code means nothing on the listing ships; code None means the
    press goes through and the reason says whether it is queued
    (online_catalog.product_online_state: the sweep's own queue test).
    Raises on a read failure."""
    refusal = product_push_refusal(db, lead)
    if refusal:
        return "NOT_FOR_WEBSITE", "not for the website - " + refusal, None
    if is_variant_of(lead):
        return "variant_of", PUSH_THE_MODEL, None
    gates, plan = press_gates(db, lead)
    if gates:
        code, reason, _need = gates[0]  # the press stops at the first
        return code, reason, plan
    if product_online_state(lead, None)["queued"]:
        return None, "queued - awaiting publish; no current refusal found", plan
    return None, "not queued", plan


def press_gates(db, lead):
    """Every press gate past the writer's own refusal and variant_of checks
    that this listing fails, in push_product's order, as
    ([(code, reason, need)], plan). _press_verdict reports the first (the
    press stops there); the Add/Edit form's website line lists every `need`
    (_website_needs) -- one rule, two screens. A listing-level stop (block
    status, another lead's listing, a family refusal) ends the list; photo
    and price are both listed. Raises on a read failure."""
    blocked = online_block.is_blocked_from_online_strict(lead, db)
    if blocked is None:
        why = "block status could not be read - the push skips it"
        return [("block_status_unverifiable", why, why)], None
    if blocked:
        why = "blocked from online - in an online_sync_blocked collection"
        return [("online_sync_blocked", why, why)], None
    rows, plan = listing_plan(db, lead)
    if plan and plan.get("variant_of"):
        return [("variant_of", PUSH_THE_MODEL, PUSH_THE_MODEL)], None
    if plan and plan.get("refusal"):
        why = "family push refused - " + plan["refusal"][0]
        return [(plan["refusal"][0], why, why)], plan
    gates = []
    view = listing_view(lead, plan) if plan else lead
    if not product_photo_urls(view):
        gates.append(("no_photo", "no photo - the push refuses a product with no photograph", "a photo"))
    unpriced = press_unpriced_sizes(view, plan["rows"] if plan else rows, plan)
    if unpriced:
        gates.append((UNPRICED_SIZE, "no selling price on " + ", ".join(unpriced)
                      + " - the push is refused until it is priced", "a selling price (" + ", ".join(unpriced) + ")"))
    return gates, plan


def expected_content(db, product):
    """Expected content uses exactly the push's family view and price builder.
    Raises (-> unknown, with this reason) where the writer would refuse."""
    if is_variant_of(product):  # push_product's own variant_of refusal
        link = (product.get("ecom") or {}).get("variant_of")
        lead = (link.get("sku") or link.get("twin_id")) if isinstance(link, dict) else link
        raise ValueError("separate listing for a colour/size of %s -- it belongs to another lead" % lead)
    rows, plan = listing_plan(db, product)
    if plan:
        if plan.get("variant_of"):
            raise ValueError("this listing belongs to another lead (%s)" % plan["variant_of"])
        if plan.get("refusal"):
            raise ValueError("family push refused - " + plan["refusal"][0])
        product = listing_view(product, plan)
        rows = plan["rows"]
    payload = build_product_input(product, rows)
    if (product.get("ecom") or {}).get("taken_down_at"):
        payload["status"] = "DRAFT"  # the delist writer, not the publish mapper
    seeds = build_variant_seed_rows(product, rows)
    return {"product": product, "title": payload["title"], "status": payload["status"],
            "tags": ims_product_tags(product), "variants": seeds}


def _money(value):
    return None if value is None or value == "" else Decimal(str(value)).normalize()


def _options(options):
    # Shopify's legacy Colour spelling and option order do not change identity.
    return tuple(sorted((str(o.get("optionName") or o.get("name") or "").lower().replace("colour", "color"),
                         str(o.get("value") if "value" in o else o.get("name", "")).strip().lower())
                        for o in options if str(o.get("optionName") or o.get("name") or "").lower() != "title"))


def content_differences(expected, node):
    """Return named real differences; missing remote fields raise as unknown."""
    for key in ("title", "status", "tags", "variants"):
        if key not in node or node[key] is None:
            raise ValueError("Shopify content incomplete: " + key)
    fields = []
    for key in ("title", "status"):
        if expected[key] != node[key]:
            fields.append(key)
    # THE tag-ownership rule is the writer's: drift only when its next press
    # would add or remove a tag (a ledger tag staff deleted is never re-added).
    tags = plan_product_tags(expected["product"], expected["tags"], node["tags"])
    if tags["add"] or tags["remove"]:
        fields.append("tags")
    connection = node["variants"]
    if (not isinstance(connection.get("nodes"), list)
            or not isinstance(connection.get("pageInfo"), dict)
            or connection["pageInfo"].get("hasNextPage") is not False):
        raise ValueError("Shopify variant content incomplete")
    remote = connection["nodes"]
    for row in remote:
        if any(k not in row for k in ("sku", "price", "compareAtPrice", "selectedOptions")):
            raise ValueError("Shopify variant fields incomplete")
    seeds = expected["variants"]
    desired_skus = [r["row"].get("inventoryItem", {}).get("sku", "") for r in seeds]
    if Counter(desired_skus) != Counter(r["sku"] for r in remote):
        fields.append("variant SKUs")
    by_id = {str(r.get("id")): r for r in remote if r.get("id")}
    by_sku = {r["sku"]: r for r in remote}
    for seed, sku in zip(seeds, desired_skus):
        variant = seed.get("variant") or {}
        row = by_id.get(str(variant.get("shopify_variant_id"))) or by_sku.get(sku)
        if row is None:
            continue
        if _options(seed["option_values"]) != _options(row["selectedOptions"]):
            fields.append("variant options: " + sku)
        # Held/unpriced rows are deliberately not priced by the writer.
        # Archived mapped variants still carry their own prices (stock is 0).
        if variant.get("_held"):
            continue
        for key, label in (("price", "price"), ("compareAtPrice", "compare-at price")):
            if key in seed["row"] and _money(seed["row"][key]) != _money(row[key]):
                fields.append(label + ": " + sku)
    return fields
