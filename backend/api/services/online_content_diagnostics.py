"""Read-only SKU parity and owned-content comparison for the sync screen.

Reuse the push planner and payload builders: diagnostics must not invent a
second family, price or tag rule. No writer or live push is called here.
"""

from collections import Counter
from decimal import Decimal

from .online_catalog import variant_rows_for_product
from .shopify_push.listing import model_listing, listing_view
from .shopify_push.product_input import build_product_input, ims_product_tags
from .shopify_push.variants import build_variant_seed_rows


def listing_plan(db, product):
    """The writer's in-memory family plan, with strict variant reads."""
    rows = variant_rows_for_product(db, product, strict=True)
    return rows, model_listing(db, product, rows)


def colour_size_parity(db):
    """A catalog SKU is online iff its variant mapping exists (not its page id).

    Counts include archived catalog rows, as the legacy denominator does.
    'Online' means mapped, not storefront visibility or sellable stock.
    Missing reasons are evaluated now, never guessed from an old run failure.
    """
    unknown = {"checked": False, "reason": "Colour/size mappings could not be read"}
    if db is None:
        return unknown
    try:
        products = list(db["catalog_products"].find({}, {"_id": 0}))
        variants = list(db["catalog_variants"].find({}, {"_id": 0, "sku": 1, "shopify_variant_id": 1}))
        spines = list(db["products"].find({}, {"_id": 0, "sku": 1, "product_id": 1, "variant_of": 1,
                                                   "deleted_at": 1, "is_archived": 1, "is_active": 1, "status": 1}))
    except Exception:  # fail soft, never present an unreadable join as zero online
        return unknown
    online = {str(v.get("sku") or "").strip() for v in variants if v.get("shopify_variant_id")}
    twins_by_id = {str(p.get("id") or p.get("product_id")): p for p in products}
    twins_by_sku = {str(p.get("sku") or "").strip(): p for p in products}
    spine_by_sku = {str(p.get("sku") or "").strip(): p for p in spines}
    spine_by_id = {str(p.get("product_id")): p for p in spines}
    plans = {}
    missing = []
    for product in products:
        sku = str(product.get("sku") or "").strip()
        if sku and sku in online:
            continue
        ecom = product.get("ecom") or {}
        spine = spine_by_sku.get(sku, {})
        parent_link = ecom.get("variant_of") or spine.get("variant_of")
        parent_id = (parent_link.get("product_id") if isinstance(parent_link, dict) else parent_link)
        parent_spine = spine_by_id.get(str(parent_id), {})
        lead = (twins_by_id.get(str(parent_link.get("twin_id"))) if isinstance(parent_link, dict) else None)
        if lead is None:
            lead = (twins_by_id.get(str(parent_id)) or twins_by_sku.get(str(parent_spine.get("sku") or ""))) if parent_id else product
        code, reason = None, "not queued"
        archived = any(p.get("deleted_at") or p.get("is_archived") or str(p.get("status") or "").upper() == "ARCHIVED"
                       for p in (product, spine, ecom))
        if not sku:
            reason = "SKU missing - cannot match a website colour/size"
        elif archived:
            reason = "archived"
        elif lead is None:
            reason = "family lead could not be found"
        elif (lead.get("ecom") or {}).get("taken_down_at"):
            reason = "taken down - not queued for automatic publish"
        else:
            key = str(lead.get("id") or lead.get("sku"))
            try:
                if key not in plans:
                    plans[key] = listing_plan(db, lead)[1]
                plan = plans[key]
                if plan and plan.get("refusal"):
                    code = plan["refusal"][0]
                    reason = "family push refused - " + code
                else:
                    held = next((h for h in (plan or {}).get("held", []) if h.get("sku") == sku), None)
                    waiting = next((h for h in (plan or {}).get("waiting", []) if h.get("sku") == sku), None)
                    if held:
                        code = held.get("code")
                        reason = "colour/size held - " + (code or "") + ": " + held.get("reason", "")
                    elif waiting:
                        reason = "not ready - " + waiting.get("reason", "")
                    elif (lead.get("ecom") or {}).get("locally_modified"):
                        reason = "queued - awaiting publish; no current family refusal found"
            except Exception:
                reason = "family readiness could not be read"
        missing.append({"sku": sku, "product_id": product.get("id") or product.get("product_id"),
                        "reason": reason, "code": code})
    pages = {str((p.get("ecom") or {}).get("shopify_product_id")) for p in products
             if (p.get("ecom") or {}).get("shopify_product_id")}
    return {"checked": True, "total": len(products), "online": len(products) - len(missing),
            "website_pages": len(pages), "missing": len(missing), "missing_items": missing}


def expected_content(db, product):
    """Expected content uses exactly the push's family view and price builder."""
    rows, plan = listing_plan(db, product)
    if plan:
        if plan.get("variant_of") or plan.get("refusal"):
            raise ValueError("family plan is refused or belongs to another lead")
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
    wanted = {str(t).strip().lower() for t in expected["tags"]}
    owned = {str(t).strip().lower() for t in ((expected["product"].get("ecom") or {}).get("shopify_tags_sent") or [])}
    actual = {str(t).strip().lower() for t in node["tags"]}
    if wanted - actual or (owned - wanted) & actual:
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
