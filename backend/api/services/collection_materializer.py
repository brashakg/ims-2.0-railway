"""
IMS 2.0 - Smart-Collection Materializer  (unification step-13)
==============================================================
Materialises `ecom_collections` membership into a fast-read `collection_products`
view so a storefront browse is a single indexed query instead of a full-catalog
rule scan on every request.

WHAT THIS DELIVERS
  * `materialize_collection(db, collection)` -- compute a collection's member SKUs
    (CUSTOM and SMART alike: THE website membership rule,
    services/collection_membership -- the listings on the website the
    collection holds, read exactly as Shopify reads them) and persist one row
    per (collection_id, sku) for EVERY SKU those listings carry (colours,
    sizes) into `collection_products`, replacing any prior membership for that
    collection. The view is a CACHE of that rule and nothing else.
  * `browse(db, collection, skip, limit)` -- page the materialised membership,
    joined to a rich product detail row (title/brand/category/price/image).
  * (no per-product-save refresh: the view is a cache the live sync rebuilds
    every run -- shopify_live_sync -- and a rule edit rebuilds its collection)
  * `refresh_collection(db, collection_id)` -- recompute one collection after its
    rules change. Fail-soft.

CONTRACT
  * Standalone Mongo, NO cross-collection transactions. The materialised view is a
    derived cache; a stale view is self-healing on the next refresh and is never a
    source of truth.
  * Every per-collection entry point is FAIL-SOFT: no DB / any error -> a safe
    no-op (0) that keeps the last view, so a materialise failure never blocks a
    rule edit. materialize_all RAISES on an unreadable catalogue (the live
    sync reports it) -- it never rewrites the view as empty.
  * No emoji (Windows cp1252). No comms, no Shopify push (that stays Phase 5).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)

# Cap the stored membership so a runaway rule can never explode the view.
_MEMBER_MAX = 5000

_VIEW = "collection_products"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _member_rows(db, collection: Dict, facts: Optional[List[Dict]] = None) -> List[Tuple[str, bool]]:
    """(sku, is_lead) for every member of a collection, CUSTOM or SMART, from
    THE website membership rule (services/collection_membership): every SKU
    each member listing on the website carries (its lead first -- the one
    flagged, so a COUNT is of listings, the number the menu shows -- then
    its colours / sizes, kept for the insights' stock joins), listings in
    the collection's order. ``facts`` lets materialize_all read the listings
    once for every collection; without it the read is STRICT (an unreadable
    catalogue raises -- the caller keeps the last view instead of writing an
    empty one)."""
    if not isinstance(collection, dict):
        return []
    from . import collection_membership

    if facts is None:
        facts = collection_membership.online_facts(db, strict=True)
    out: List[Tuple[str, bool]] = []
    seen = set()
    for f in collection_membership.members(db, collection, facts)[0]:
        for s in f.get("skus") or [f.get("sku")]:
            if s and str(s) not in seen:
                seen.add(str(s))
                out.append((str(s), str(s) == str(f.get("sku"))))
    return out[:_MEMBER_MAX]


def _product_ids_by_sku(db, skus: List[str]) -> Dict[str, Any]:
    """sku -> the `products` SPINE doc's product_id, resolved in ONE batched
    find over {"sku": {"$in": [...]}} (projected to sku + product_id).

    Collections Phase 1 groundwork: rows in the materialised view carry a
    denormalised product_id so downstream joins (orders.items.product_id,
    analytics) are a direct indexed lookup instead of a per-row sku hop.
    FAIL-SOFT: any error / a sku with no spine doc simply resolves to None --
    a catalog-only member never blocks materialisation."""
    out: Dict[str, Any] = {}
    if db is None or not skus:
        return out
    try:
        cursor = db["products"].find(
            {"sku": {"$in": list(skus)}}, {"sku": 1, "product_id": 1}
        )
        for doc in cursor:
            if not isinstance(doc, dict):
                continue
            sku = doc.get("sku")
            if sku and doc.get("product_id") is not None and sku not in out:
                out[sku] = doc.get("product_id")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[COLL-MAT] product_id resolve failed: %s", exc)
    return out


def materialize_collection(db, collection: Dict, facts: Optional[List[Dict]] = None) -> Optional[int]:
    """Recompute + persist a collection's membership into `collection_products`.

    Replaces ALL prior rows for the collection with one row per member SKU:
        {collection_id, handle, sku, lead, product_id, position, computed_at}
    `product_id` is denormalised from the `products` spine (batched, fail-soft
    -> None when the sku has no spine doc). Also writes back `products_count`
    (LISTINGS -- the website's number, the menu's) + `materialized_at` onto
    the collection doc (best-effort). Returns the listing count. Fail-soft:
    unreadable listings or a failed write -> None (the last view stands;
    never reported as "0 products")."""
    if db is None or not isinstance(collection, dict):
        return 0
    collection_id = collection.get("collection_id") or collection.get("id")
    if not collection_id:
        return 0
    handle = collection.get("handle")
    try:
        members = _member_rows(db, collection, facts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[COLL-MAT] resolve failed for %s: %s", collection_id, exc)
        return None

    try:
        view = db[_VIEW]
    except Exception:  # noqa: BLE001
        return None

    computed_at = _now()
    try:
        # Wipe-and-rewrite: the membership for ONE collection is small + bounded,
        # and a full replace keeps the view exactly in step with the rules (no
        # orphaned members after a rule narrows). Standalone Mongo, no txn.
        view.delete_many({"collection_id": collection_id})
        if members:
            product_ids = _product_ids_by_sku(db, [sku for sku, _lead in members])
            rows = [
                {
                    "collection_id": collection_id,
                    "handle": handle,
                    "sku": sku,
                    "lead": lead,
                    "product_id": product_ids.get(sku),
                    "position": idx,
                    "computed_at": computed_at,
                }
                for idx, (sku, lead) in enumerate(members)
            ]
            view.insert_many(rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[COLL-MAT] persist failed for %s: %s", collection_id, exc)
        return None

    # Best-effort: stamp the count back on the collection so list views can show
    # it without re-counting the view. Never fatal.
    listings = sum(1 for _sku, lead in members if lead)
    try:
        db["ecom_collections"].update_one(
            {"collection_id": collection_id},
            {"$set": {"products_count": listings, "materialized_at": computed_at}},
        )
    except Exception:  # noqa: BLE001
        pass
    return listings


def materialize_all(db, only_smart: bool = False, facts: Optional[List[Dict]] = None) -> Dict[str, Optional[int]]:
    """Recompute every LIVE website collection (or only SMART ones). Returns
    {collection_id: member_count}. A temporary Quick Share set (a hand-picked
    list -- its count is its own length) and a retired collection are never
    touched (collection_membership.live_collections). ``facts`` (the live
    sync passes the one read it shares with the menu) or a STRICT read: an
    unreadable catalogue raises and nothing is rewritten (the last view
    stands)."""
    if db is None:
        return {}
    from . import collection_membership

    collections = [
        c for c in collection_membership.live_collections(db)
        if not only_smart or str(c.get("collection_type") or "").upper() == "SMART"
    ]
    if facts is None:
        facts = collection_membership.online_facts(db, strict=True)  # read ONCE for every collection
    out: Dict[str, Optional[int]] = {}
    for coll in collections:
        if not isinstance(coll, dict):
            continue
        cid = coll.get("collection_id") or coll.get("id")
        if not cid:
            continue
        out[cid] = materialize_collection(db, coll, facts)
    return out


def refresh_collection(db, collection_id: str) -> Optional[int]:
    """Recompute ONE collection by id (after its rules / membership change).
    Fail-soft -> 0 (no such collection) / None (could not recompute)."""
    if db is None or not collection_id:
        return 0
    try:
        doc = db["ecom_collections"].find_one({"collection_id": collection_id})
    except Exception:  # noqa: BLE001
        return 0
    if doc is None:
        return 0
    return materialize_collection(db, doc)


def _product_detail_by_sku(db, skus: List[str]) -> Dict[str, Dict]:
    """sku -> rich product row, looked up in `products` then `catalog_products`
    (spine wins). Fail-soft -> {}."""
    out: Dict[str, Dict] = {}
    if db is None or not skus:
        return out
    for coll_name in ("products", "catalog_products"):
        try:
            coll = db[coll_name]
            for d in coll.find({"sku": {"$in": skus}}):
                sku = d.get("sku")
                if sku and sku not in out:
                    out[sku] = {k: v for k, v in d.items() if k != "_id"}
        except Exception:  # noqa: BLE001
            continue
    return out


def _detail_row(sku: str, d: Dict) -> Dict:
    """Shape ONE browse row from a (possibly missing) product detail doc."""
    d = d or {}
    images = d.get("images")
    image = images[0] if isinstance(images, list) and images else d.get("image")
    pricing = d.get("pricing") if isinstance(d.get("pricing"), dict) else {}
    price = (
        d.get("offer_price")
        or pricing.get("offer_price")
        or d.get("price")
        or d.get("mrp")
        or pricing.get("mrp")
    )
    return {
        "product_id": d.get("product_id") or d.get("id") or sku,
        "sku": sku,
        "title": d.get("title") or d.get("name") or d.get("model"),
        "brand": d.get("brand") or (d.get("attributes") or {}).get("brand"),
        "category": d.get("category"),
        "mrp": d.get("mrp"),
        "offer_price": price,
        "image": image,
    }


def browse(db, collection: Dict, skip: int = 0, limit: int = 24) -> Dict[str, Any]:
    """Page a collection's MATERIALISED membership into rich rows -- one
    row per LISTING (the lead rows), so ``total`` is the products_count the
    menu and the website show.

    Reads `collection_products` (sorted by position), joins product detail, and
    returns {products, total, skip, limit}. If the view has no rows yet (never
    materialised), it transparently materialises once then reads. A failed
    read never raises and is never "0 products": it answers
    {total: None, unreadable: True} (no db / no id stays an empty page)."""
    empty = {"products": [], "total": 0, "skip": skip, "limit": limit}
    if db is None or not isinstance(collection, dict):
        return empty
    collection_id = collection.get("collection_id") or collection.get("id")
    if not collection_id:
        return empty
    try:
        view = db[_VIEW]
        leads = {"collection_id": collection_id, "lead": True}
        total = view.count_documents(leads)
        if total == 0 and not collection.get("materialized_at"):
            # Lazy FIRST materialise so a freshly-created collection browses
            # without an explicit refresh call (one already materialised and
            # empty stays empty until the next sync -- never a rescan per view).
            if materialize_collection(db, collection) is None:
                return {**empty, "total": None, "unreadable": True}
            total = view.count_documents(leads)
        rows = list(
            view.find(leads)
            .sort("position", 1)
            .skip(int(skip))
            .limit(int(limit))
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[COLL-MAT] browse failed for %s: %s", collection_id, exc)
        return {**empty, "total": None, "unreadable": True}

    skus = [r.get("sku") for r in rows if r.get("sku")]
    detail = _product_detail_by_sku(db, skus)
    products = [_detail_row(sku, detail.get(sku, {})) for sku in skus]
    return {"products": products, "total": int(total), "skip": int(skip), "limit": int(limit)}


def ensure_indexes(db) -> None:
    """Create the `collection_products` indexes (idempotent, fail-soft)."""
    if db is None:
        return
    try:
        view = db[_VIEW]
        view.create_index([("collection_id", 1), ("position", 1)])
        view.create_index([("collection_id", 1), ("sku", 1)], unique=True)
        view.create_index([("sku", 1)])
        # Collections Phase 1: product_id joins (orders.items.product_id /
        # analytics) hit the view directly. NON-unique -- product_id may be
        # None for catalog-only members and repeats across collections.
        view.create_index([("product_id", 1)])
        view.create_index([("collection_id", 1), ("product_id", 1)])
    except Exception as exc:  # noqa: BLE001
        logger.warning("[COLL-MAT] ensure_indexes failed: %s", exc)
