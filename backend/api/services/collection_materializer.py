"""
IMS 2.0 - Smart-Collection Materializer  (unification step-13)
==============================================================
Materialises `ecom_collections` membership into a fast-read `collection_products`
view so a storefront browse is a single indexed query instead of a full-catalog
rule scan on every request.

WHAT THIS DELIVERS
  * `materialize_collection(db, collection)` -- compute a collection's member SKUs
    (CUSTOM = its manual membership in position order; SMART = the shared
    `ecom_smart_rules` resolver over the UNION of the `products` spine +
    `catalog_products`) and persist one row per (collection_id, sku) into
    `collection_products`, replacing any prior membership for that collection.
  * `browse(db, collection, skip, limit)` -- page the materialised membership,
    joined to a rich product detail row (title/brand/category/price/image).
  * `refresh_for_product(db, product)` -- recompute every SMART collection after a
    canonical product create/update (the product's tags/category/brand may have
    just changed which collections it belongs to). Fail-soft.
  * `refresh_collection(db, collection_id)` -- recompute one collection after its
    rules change. Fail-soft.

WHY THE UNION OF products + catalog_products
  The audit (step-13) flagged that the rule engine scanned only `catalog_products`,
  but the BVI migration brought VARIANTS while the in-store catalogue lives in
  `products` -- and step-12's governed `tags` (the backbone of BVI's generated
  collections) are written onto the `products` spine. Resolving over the union,
  spine-wins on a SKU clash, puts both catalogues in the haystack and makes the
  step-12 tags addressable by tag rules.

CONTRACT
  * Standalone Mongo, NO cross-collection transactions. The materialised view is a
    derived cache; a stale view is self-healing on the next refresh and is never a
    source of truth.
  * Every entry point is FAIL-SOFT: no DB / any error -> a safe no-op (0) so a
    materialise failure never blocks a product create or a rule edit.
  * No emoji (Windows cp1252). No comms, no Shopify push (that stays Phase 5).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import ecom_smart_rules

logger = logging.getLogger(__name__)

# A SMART collection can resolve against a large catalogue; cap the scan + the
# stored membership so a runaway rule can never explode the view.
_SCAN_MAX = 5000
_MEMBER_MAX = 5000

_VIEW = "collection_products"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _all_products(db) -> List[Dict]:
    """UNION of the `products` spine + `catalog_products`, de-duped by SKU with
    the SPINE WINNING (so step-12 governed `tags` are present on the doc the rule
    engine sees). Fail-soft -> []. Capped at _SCAN_MAX."""
    if db is None:
        return []
    by_sku: Dict[str, Dict] = {}
    order: List[str] = []
    # catalog_products first, then products -- so the spine overwrites on a clash.
    for coll_name in ("catalog_products", "products"):
        try:
            coll = db[coll_name]
        except Exception:  # noqa: BLE001
            continue
        try:
            cursor = coll.find({})
        except Exception:  # noqa: BLE001
            continue
        for doc in cursor:
            if not isinstance(doc, dict):
                continue
            sku = doc.get("sku")
            if not sku:
                continue
            doc.pop("_id", None)
            if sku not in by_sku:
                order.append(sku)
            by_sku[sku] = doc
            if len(order) >= _SCAN_MAX and coll_name == "products":
                break
    return [by_sku[s] for s in order]


def _member_skus(db, collection: Dict) -> List[str]:
    """The effective ordered member SKUs for a collection.

    CUSTOM -> its stored manual membership in position order.
    SMART  -> the shared resolver over the product union (Shopify-shape rules are
              normalised on the fly; native IMS rules pass through)."""
    if not isinstance(collection, dict):
        return []
    ctype = str(collection.get("collection_type") or "CUSTOM").upper()
    if ctype == "CUSTOM":
        members = sorted(
            (collection.get("products") or []),
            key=lambda p: int((p or {}).get("position", 0) or 0),
        )
        skus: List[str] = []
        seen: set = set()
        for p in members:
            sku = (p or {}).get("sku")
            if sku and sku not in seen:
                skus.append(sku)
                seen.add(sku)
        return skus[:_MEMBER_MAX]
    rules = ecom_smart_rules.normalize_rules(collection.get("rules") or [])
    disjunctive = bool(collection.get("disjunctive", False))
    products = _all_products(db)
    return ecom_smart_rules.resolve_skus(
        products, rules, disjunctive=disjunctive, limit=_MEMBER_MAX
    )


def materialize_collection(db, collection: Dict) -> int:
    """Recompute + persist a collection's membership into `collection_products`.

    Replaces ALL prior rows for the collection with one row per member SKU:
        {collection_id, handle, sku, position, computed_at}
    Also writes back `products_count` + `materialized_at` onto the collection doc
    (best-effort). Returns the member count. Fail-soft -> 0."""
    if db is None or not isinstance(collection, dict):
        return 0
    collection_id = collection.get("collection_id") or collection.get("id")
    if not collection_id:
        return 0
    handle = collection.get("handle")
    try:
        skus = _member_skus(db, collection)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[COLL-MAT] resolve failed for %s: %s", collection_id, exc)
        return 0

    try:
        view = db[_VIEW]
    except Exception:  # noqa: BLE001
        return 0

    computed_at = _now()
    try:
        # Wipe-and-rewrite: the membership for ONE collection is small + bounded,
        # and a full replace keeps the view exactly in step with the rules (no
        # orphaned members after a rule narrows). Standalone Mongo, no txn.
        view.delete_many({"collection_id": collection_id})
        if skus:
            rows = [
                {
                    "collection_id": collection_id,
                    "handle": handle,
                    "sku": sku,
                    "position": idx,
                    "computed_at": computed_at,
                }
                for idx, sku in enumerate(skus)
            ]
            view.insert_many(rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[COLL-MAT] persist failed for %s: %s", collection_id, exc)
        return 0

    # Best-effort: stamp the count back on the collection so list views can show
    # it without re-counting the view. Never fatal.
    try:
        db["ecom_collections"].update_one(
            {"collection_id": collection_id},
            {"$set": {"products_count": len(skus), "materialized_at": computed_at}},
        )
    except Exception:  # noqa: BLE001
        pass
    return len(skus)


def materialize_all(db, only_smart: bool = False) -> Dict[str, int]:
    """Recompute every collection (or only SMART ones). Returns
    {collection_id: member_count}. Fail-soft -> {}."""
    if db is None:
        return {}
    try:
        query: Dict = {"collection_type": "SMART"} if only_smart else {}
        collections = list(db["ecom_collections"].find(query))
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, int] = {}
    for coll in collections:
        if not isinstance(coll, dict):
            continue
        cid = coll.get("collection_id") or coll.get("id")
        if not cid:
            continue
        out[cid] = materialize_collection(db, coll)
    return out


def refresh_collection(db, collection_id: str) -> int:
    """Recompute ONE collection by id (after its rules / membership change).
    Fail-soft -> 0."""
    if db is None or not collection_id:
        return 0
    try:
        coll = db["ecom_collections"].find_one({"collection_id": collection_id})
    except Exception:  # noqa: BLE001
        return 0
    if not coll:
        return 0
    return materialize_collection(db, coll)


def refresh_for_product(db, product: Optional[Dict] = None) -> Dict[str, int]:
    """Recompute SMART collections after a product create/update.

    A new/edited product may newly satisfy (or stop satisfying) any SMART rule, so
    we re-materialise the SMART collections. CUSTOM collections are membership-by-
    hand and are untouched here. Fail-soft -> {} (NEVER raises into the create
    path). `product` is accepted for signature stability / future scoping; the
    current implementation recomputes all SMART collections."""
    try:
        return materialize_all(db, only_smart=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[COLL-MAT] refresh_for_product failed: %s", exc)
        return {}


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
                    d.pop("_id", None)
                    out[sku] = d
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
    """Page a collection's MATERIALISED membership into rich rows.

    Reads `collection_products` (sorted by position), joins product detail, and
    returns {products, total, skip, limit}. If the view has no rows yet (never
    materialised), it transparently materialises once then reads. Fail-soft ->
    an empty page (never raises)."""
    empty = {"products": [], "total": 0, "skip": skip, "limit": limit}
    if db is None or not isinstance(collection, dict):
        return empty
    collection_id = collection.get("collection_id") or collection.get("id")
    if not collection_id:
        return empty
    try:
        view = db[_VIEW]
        total = view.count_documents({"collection_id": collection_id})
        if total == 0:
            # Lazy first-materialise so a freshly-created collection browses
            # without an explicit refresh call.
            materialize_collection(db, collection)
            total = view.count_documents({"collection_id": collection_id})
        rows = list(
            view.find({"collection_id": collection_id})
            .sort("position", 1)
            .skip(int(skip))
            .limit(int(limit))
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[COLL-MAT] browse failed for %s: %s", collection_id, exc)
        return empty

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
    except Exception as exc:  # noqa: BLE001
        logger.warning("[COLL-MAT] ensure_indexes failed: %s", exc)
