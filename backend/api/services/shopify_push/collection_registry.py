"""Shopify push -- the IMS collection registry (owner 2026-10-19).

ONE set of website collections, DERIVED from the listings IMS has on the
website and regenerated on every push-all, scheduled sync and rebuild:

  per category      Sunglasses, Eyeglasses ... (productType, as the push sends it)
  per gender        Men's / Women's / Unisex <category>   (type AND gender_ tag)
  per shape         Aviator / Round ... <category>        (type AND shape_ tag)
  polarized         Polarized <category>                  (type AND polarization_yes)
  per brand         title from Brand Master               (brand_ tag)
  price bands       Under Rs 3,000 ... Over Rs 10,000     (variant price)
  New Arrivals      every listing, newest first

Every rule uses IMS's OWN tags / productType / prices exactly as the product
push sends them, so the collections fill themselves. A definition whose rules
match no listing is never created; it appears on the first push after its
first product goes live. Each one is an ecom_collections row marked
IMS_MANAGED with a stable IMS_KEY; IMS creates and updates only those.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from agents.nexus_providers import _as_shopify_gid

from .collections import IMS_KEY, IMS_MANAGED, RETIRED, push_collection
from .transport import _graphql, _user_errors


# Shopify productType -> the collection's word for it.
_CATEGORY_TITLES = {"Sunglass": "Sunglasses", "Spectacles": "Eyeglasses", "SmartGlass": "Smart Glasses"}
_GENDER_TITLES = {"men": "Men's", "women": "Women's", "unisex": "Unisex", "kids": "Kids'"}
# (handle, title, lower bound inclusive, upper bound exclusive)
PRICE_BANDS = (
    ("under-3000", "Under Rs 3,000", None, 3000),
    ("3000-5000", "Rs 3,000-5,000", 3000, 5000),
    ("5000-10000", "Rs 5,000-10,000", 5000, 10000),
    ("over-10000", "Over Rs 10,000", 10000, None),
)


def _rule(field: str, relation: str, value: Any) -> Dict[str, str]:
    return {"field": field, "relation": relation, "value": str(value)}


def _handle(title: str) -> str:
    from ..shopify_tag_gen import slugify_tag_value

    return slugify_tag_value(title.replace("'", ""))


def _words(slug: str) -> str:
    return slug.replace("-", " ").title()


# ---------------------------------------------------------------------------
# Listings -> facts -> definitions. Pure below listing_facts.
# ---------------------------------------------------------------------------


def _facts(db, doc: Dict[str, Any]) -> Dict[str, Any]:
    """What the product push sends for this listing: productType, tags, prices."""
    from ..ecom_category_map import ims_to_shopify_type
    from ..online_catalog import variant_rows_for_product
    from ._shared import _product_brand
    from .product_input import _resolve_variant_pricing, ims_product_tags

    rows = variant_rows_for_product(db, doc) or [{}]
    # ponytail: the lead's price rule per variant row, not listing.model_listing's
    # per-colour prices -- close enough to decide whether a price band is empty.
    prices = [p for p in (_resolve_variant_pricing(doc, r)[0] for r in rows) if p]
    return {
        "sku": doc.get("sku"),
        "type": ims_to_shopify_type(doc["category"]) if doc.get("category") else "",
        "tags": set(ims_product_tags(doc)),
        "prices": prices,
        "brand": str(_product_brand(doc) or "").strip(),
    }


def listing_facts(db, include_ready: bool = False) -> List[Dict[str, Any]]:
    """Facts for every listing ON the website (a lead or single product with a
    Shopify id, not taken down, archived or deleted). include_ready also counts
    the listings a push-all would publish for the first time (the rebuild's
    dry-run projection only; collections are made from live listings)."""
    from ._shared import is_variant_of, product_push_refusal

    out = []
    for doc in db["catalog_products"].find({}):
        ecom = doc.get("ecom") or {}
        if (
            is_variant_of(doc)
            or doc.get("deleted_at")
            or ecom.get("taken_down_at")
            or str(ecom.get("status") or "").upper() == "ARCHIVED"
        ):
            continue
        if not ecom.get("shopify_product_id") and not (include_ready and product_push_refusal(db, doc) is None):
            continue
        out.append(_facts(db, doc))
    return out


def brand_names(db) -> Dict[str, str]:
    """{brand tag: Brand Master name} for every active brand."""
    from ..shopify_tag_gen import slugify_brand_value

    names: Dict[str, str] = {}
    for row in db["brand_masters"].find({}):
        if row.get("is_active") is not False and row.get("name"):
            names.setdefault(f"brand_{slugify_brand_value(row['name'])}", str(row["name"]).strip())
    return names


def _matches(fact: Dict[str, Any], rules: List[Dict[str, str]]) -> bool:
    """Shopify's reading of an all-AND rule set, over what the push sends."""
    for r in rules:
        field, rel, value = r["field"], r["relation"], r["value"]
        if field == "tag":
            ok = value.lower() in fact["tags"]
        elif field == "product_type":
            ok = fact["type"].lower() == value.lower()
        else:  # variant_price: any variant
            v = float(value)
            ok = any(p > v if rel == "GREATER_THAN" else p < v for p in fact["prices"])
        if not ok:
            return False
    return True


def _values(facts: Iterable[Dict[str, Any]], prefix: str) -> List[str]:
    return sorted({t[len(prefix):] for f in facts for t in f["tags"] if t.startswith(prefix)})


def derive_definitions(facts: List[Dict[str, Any]], names: Dict[str, str]) -> List[Dict[str, Any]]:
    """Every collection the listings call for, each with its product count.
    A definition matching nothing is left out. Pure."""
    defs: List[Dict[str, Any]] = []

    def add(key, title, rules, handle=None, sort="BEST_SELLING"):
        defs.append({"key": key, "title": title, "handle": handle or _handle(title),
                     "rules": rules, "sort_order": sort})

    for ptype in sorted({f["type"] for f in facts if f["type"]}):
        cat = _CATEGORY_TITLES.get(ptype, ptype)
        mine = [f for f in facts if f["type"] == ptype]
        is_type = _rule("product_type", "EQUALS", ptype)
        add(f"type:{_handle(cat)}", cat, [is_type])
        for g in _values(mine, "gender_"):
            add(f"gender:{_handle(cat)}:{g}", f"{_GENDER_TITLES.get(g, _words(g))} {cat}",
                [is_type, _rule("tag", "EQUALS", f"gender_{g}")])
        for s in _values(mine, "shape_"):
            add(f"shape:{_handle(cat)}:{s}", f"{_words(s)} {cat}", [is_type, _rule("tag", "EQUALS", f"shape_{s}")])
        add(f"polarized:{_handle(cat)}", f"Polarized {cat}", [is_type, _rule("tag", "EQUALS", "polarization_yes")])
    for tag in _values(facts, "brand_"):
        tag = f"brand_{tag}"
        title = names.get(tag) or next((f["brand"] for f in facts if tag in f["tags"] and f["brand"]), _words(tag[6:]))
        add(f"brand:{tag[6:]}", title, [_rule("tag", "EQUALS", tag)])
    for handle, title, low, high in PRICE_BANDS:
        rules = []
        if low is not None:
            rules.append(_rule("variant_price", "GREATER_THAN", f"{low - 0.01:.2f}"))
        if high is not None:
            rules.append(_rule("variant_price", "LESS_THAN", high))
        add(f"price:{handle}", title, rules, handle=handle)
    add("new-arrivals", "New Arrivals", [_rule("variant_price", "GREATER_THAN", 0)], sort="CREATED_DESC")

    out, handles = [], set()
    for d in defs:
        d["count"] = sum(1 for f in facts if _matches(f, d["rules"]))
        if d["count"] and d["handle"] not in handles:
            handles.add(d["handle"])
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# Definitions -> ecom_collections rows (IMS only; the push sends them).
# ---------------------------------------------------------------------------


def apply_collection_registry(db, definitions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Create / update the IMS-managed ecom_collections rows from the
    definitions (derived from the live listings when not given). A changed
    row is queued (locally_modified) for the push; an unchanged one is left
    alone. A handle still held by an old collection is skipped and named --
    scripts/rebuild_collections.py frees it. Collections whose products have
    all gone stay as they are (Shopify empties a smart collection itself).
    Returns {created, updated, unchanged, skipped}."""
    from database.repositories.ecom_collection_repository import EcomCollectionRepository

    if definitions is None:
        definitions = derive_definitions(listing_facts(db), brand_names(db))
    coll = db["ecom_collections"]
    repo = EcomCollectionRepository(coll)
    out: Dict[str, Any] = {"created": [], "updated": [], "unchanged": 0, "skipped": []}
    for d in definitions:
        want = {
            "title": d["title"], "handle": d["handle"], "collection_type": "SMART",
            "rules": d["rules"], "disjunctive": False, "sort_order": d["sort_order"],
            "published": True, IMS_MANAGED: True, IMS_KEY: d["key"],
        }
        doc = coll.find_one({IMS_KEY: d["key"]})
        holder = coll.find_one({"handle": d["handle"]})
        if holder is not None and (doc is None or holder.get("_id") != doc.get("_id")):
            out["skipped"].append(f"{d['handle']}: handle held by an old collection (run scripts/rebuild_collections.py)")
            continue
        if doc is None:
            if repo.create(want):
                out["created"].append(d["handle"])
            else:
                out["skipped"].append(f"{d['handle']}: could not be saved")
            continue
        diff = {k: v for k, v in want.items() if doc.get(k) != v}
        if diff:
            repo.update(doc["collection_id"], diff)
            out["updated"].append(d["handle"])
        else:
            out["unchanged"] += 1
    return out


async def sync_collections(db) -> Dict[str, Any]:
    """The registry, then a push of every queued IMS collection (create /
    update + Online Store). The scheduled sync's and the rebuild's step; the
    push-all press applies the registry and sends the queue with its own
    sweep. LIVE only: a dark run changes nothing. One collection's failure
    never stops the rest."""
    from ._shared import _live_or_reason

    live, reason = _live_or_reason(db)
    if not live:
        return {"dark": reason}
    out = apply_collection_registry(db)
    out["pushed"], out["failed"] = [], []
    for doc in db["ecom_collections"].find({IMS_MANAGED: True, "locally_modified": True, RETIRED: {"$ne": True}}):
        doc.pop("_id", None)
        res = await push_collection(db, doc)
        (out["pushed"] if res.ok else out["failed"]).append(
            doc.get("handle") if res.ok else f"{doc.get('handle')}: {res.error}"
        )
    return out


# ---------------------------------------------------------------------------
# Shopify reads / deletes for the rebuild (scripts/rebuild_collections.py).
# ---------------------------------------------------------------------------


async def shop_collections(db) -> List[Dict[str, Any]]:
    """Every collection on the shop with its IMS key (None when not IMS's).
    Raises on a read failure: nothing is deleted on a half-read shop."""
    from .queries import _COLLECTIONS_READ

    out: List[Dict[str, Any]] = []
    after = None
    while True:
        body = await _graphql(db, _COLLECTIONS_READ, {"after": after})
        page = ((body or {}).get("data") or {}).get("collections")
        if page is None:
            raise RuntimeError(f"collections read failed: {(body or {}).get('errors')}")
        for c in page.get("nodes") or []:
            out.append({
                "id": c.get("id"), "handle": c.get("handle"), "title": c.get("title"),
                "products": ((c.get("productsCount") or {}).get("count")),
                "ims_key": (c.get("imsKey") or {}).get("value"),
            })
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return out
        after = info.get("endCursor")


def collections_to_delete(db, shop: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every shop collection that is NOT IMS's: no IMS key on Shopify and not
    the Shopify id of an IMS-managed row. Pure over the read."""
    mine = {
        _as_shopify_gid(d["shopify_collection_id"], "Collection")
        for d in db["ecom_collections"].find({IMS_MANAGED: True})
        if d.get("shopify_collection_id")
    }
    return [c for c in shop if not c.get("ims_key") and _as_shopify_gid(c["id"], "Collection") not in mine]


def link_shop_collections(db, shop: List[Dict[str, Any]]) -> int:
    """An IMS collection already on Shopify (its ims.collection_key metafield)
    whose IMS row has no Shopify id gets it back, so it is updated, never
    made twice. Returns how many were linked."""
    n = 0
    for c in shop:
        doc = db["ecom_collections"].find_one({IMS_KEY: c.get("ims_key")}) if c.get("ims_key") else None
        if doc is not None and not doc.get("shopify_collection_id"):
            n += db["ecom_collections"].update_one(
                {"_id": doc["_id"]}, {"$set": {"shopify_collection_id": c["id"], "locally_modified": True}}
            ).modified_count
    return n


async def delete_shop_collection(db, gid: str) -> None:
    """Delete one collection on Shopify. Raises on any error."""
    from .queries import _COLLECTION_DELETE

    body = await _graphql(db, _COLLECTION_DELETE, {"input": {"id": gid}})
    errs = _user_errors(body, "collectionDelete")
    if errs or not (((body or {}).get("data") or {}).get("collectionDelete") or {}).get("deletedCollectionId"):
        raise RuntimeError(f"delete {gid} failed: {errs or (body or {}).get('errors')}")


def retire_old_collections(db) -> int:
    """Mark every IMS row that is not IMS-managed (the old app's collections)
    retired: never pushed again, and its handle / Shopify id moved aside so
    the registry can reuse a natural handle. Temporary PDF collections are
    left alone. Returns how many were retired."""
    from datetime import datetime, timezone

    n = 0
    for d in db["ecom_collections"].find(
        {IMS_MANAGED: {"$ne": True}, RETIRED: {"$ne": True}, "is_temporary": {"$ne": True}}
    ):
        n += db["ecom_collections"].update_one({"_id": d["_id"]}, {
            "$set": {RETIRED: True, "retired_at": datetime.now(timezone.utc), "locally_modified": False,
                     "retired_handle": d.get("handle"), "retired_shopify_collection_id": d.get("shopify_collection_id")},
            "$unset": {"handle": "", "shopify_collection_id": ""},
        }).modified_count
    return n
