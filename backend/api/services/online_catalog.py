"""
IMS 2.0 - Online catalog source (IMS Mongo -- post-BVI)
=======================================================
BVI (the separate e-commerce app) and its Postgres were DELETED on 2026-07-20;
IMS Mongo is now the SOLE source of online truth. This module answers, per
SKU/barcode, "is this product online (on Shopify)?" and resolves the Shopify
inventory targets the stock write-back needs -- reading ONLY the IMS catalog:

  catalog_products.ecom   -- status (DRAFT/PUBLISHED/ARCHIVED) +
                             shopify_product_id (set on first LIVE push)
  catalog_variants        -- sku / store_barcode / barcode / gtin identity +
                             shopify_variant_id / shopify_inventory_item_id /
                             shopify_location_id (the write-back mapping,
                             the same fields online_sync_health + the parity
                             monitor already consume)

Design rules (unchanged from the old bridge contract):
- Fully FAIL-SOFT. Missing DB / collection -> empty result, never raise,
  never break the existing IMS endpoints.
- Read-only. Nothing here mutates the catalog.
- Match key: the caller may pass any mix of SKUs and barcodes; each requested
  identifier is matched against catalog_variants.sku / store_barcode / barcode
  / gtin and catalog_products.sku / barcode, and the result is keyed by the
  REQUESTED identifier so the caller can map straight back.
- The live LISTED quantity is NOT tracked here: Shopify owns it, and IMS reads
  it live where needed (online_sync_health.live_listed_qty_for_skus) or via the
  nightly shopify_stock_parity check. online_status_for_skus therefore reports
  online_stock=None (unknown) -- never a fake 0.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def normalize_sku(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _coll(db, name: str):
    """Collection access tolerant of DatabaseConnection (get_collection), a real
    pymongo Database (both) and the in-memory MockDatabase (subscript only).
    Fail-soft -> None."""
    if db is None:
        return None
    try:
        getter = getattr(db, "get_collection", None)
        if callable(getter):
            coll = getter(name)
            if coll is not None:
                return coll
    except Exception:  # noqa: BLE001
        pass
    try:
        return db[name]
    except Exception:  # noqa: BLE001
        return None


# Identifier fields a requested key is matched against, per collection.
_VARIANT_KEY_FIELDS = ("sku", "store_barcode", "barcode", "gtin")
_PRODUCT_KEY_FIELDS = ("sku", "barcode")


def _clean_keys(skus: Optional[List[str]]) -> List[str]:
    return sorted({normalize_sku(s) for s in (skus or []) if normalize_sku(s)})


def _match_query(fields: Tuple[str, ...], keys: List[str]) -> Dict[str, Any]:
    return {"$or": [{f: {"$in": keys}} for f in fields]}


def _variants_by_key(db, keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """{requested_key: catalog_variants doc} for every key that matches a
    variant identifier (sku > store_barcode > barcode > gtin). Fail-soft {}."""
    coll = _coll(db, "catalog_variants")
    if coll is None or not keys:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        cursor = coll.find(
            _match_query(_VARIANT_KEY_FIELDS, keys),
            {
                "_id": 0,
                "sku": 1,
                "store_barcode": 1,
                "barcode": 1,
                "gtin": 1,
                "parent_product_id": 1,
                "parent_sku": 1,
                "shopify_variant_id": 1,
                "shopify_inventory_item_id": 1,
                "shopify_location_id": 1,
            },
        )
        keyset = set(keys)
        for doc in cursor:
            for field in _VARIANT_KEY_FIELDS:
                ident = normalize_sku(doc.get(field))
                if ident and ident in keyset and ident not in out:
                    out[ident] = doc
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_CATALOG] variant lookup failed: %s", exc)
        return {}
    return out


def _products_by_key(db, keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """{requested_key: catalog_products doc} for keys matching a product's
    sku/barcode directly. Fail-soft {}."""
    coll = _coll(db, "catalog_products")
    if coll is None or not keys:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        cursor = coll.find(
            _match_query(_PRODUCT_KEY_FIELDS, keys),
            {"_id": 0, "id": 1, "sku": 1, "barcode": 1, "ecom": 1},
        )
        keyset = set(keys)
        for doc in cursor:
            for field in _PRODUCT_KEY_FIELDS:
                ident = normalize_sku(doc.get(field))
                if ident and ident in keyset and ident not in out:
                    out[ident] = doc
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_CATALOG] product lookup failed: %s", exc)
        return {}
    return out


def _parents_for_variants(db, variants: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Fetch the parent catalog_products docs for matched variants, keyed by
    BOTH product id and product sku (so either linkage resolves). Fail-soft {}."""
    coll = _coll(db, "catalog_products")
    if coll is None or not variants:
        return {}
    ids = sorted({v.get("parent_product_id") for v in variants if v.get("parent_product_id")})
    skus = sorted({v.get("parent_sku") for v in variants if v.get("parent_sku")})
    if not ids and not skus:
        return {}
    clauses: List[Dict[str, Any]] = []
    if ids:
        clauses.append({"id": {"$in": ids}})
    if skus:
        clauses.append({"sku": {"$in": skus}})
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for doc in coll.find(
            {"$or": clauses}, {"_id": 0, "id": 1, "sku": 1, "ecom": 1}
        ):
            if doc.get("id"):
                out.setdefault(str(doc["id"]), doc)
            if doc.get("sku"):
                out.setdefault(normalize_sku(doc.get("sku")), doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_CATALOG] parent lookup failed: %s", exc)
        return {}
    return out


def _ecom_online(ecom: Dict[str, Any]) -> bool:
    """Same semantics as the old bridge: a product is 'online' when it has been
    pushed to Shopify (gid present) OR is staged PUBLISHED."""
    if not isinstance(ecom, dict):
        return False
    pushed = bool(normalize_sku(ecom.get("shopify_product_id")))
    return pushed or str(ecom.get("status") or "").upper() == "PUBLISHED"


def online_mapping_available(db) -> bool:
    """True when the IMS catalog carries at least one Shopify-mapped object
    (a pushed product OR a variant with an inventory-item id) -- i.e. the
    Mongo online-truth source is populated. Replaces the retired
    ecommerce_db_configured() env check (the BVI Postgres is gone).
    Fail-soft -> False."""
    try:
        prods = _coll(db, "catalog_products")
        if prods is not None and prods.find_one(
            {"ecom.shopify_product_id": {"$exists": True, "$nin": [None, ""]}},
            {"_id": 0, "id": 1},
        ):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        variants = _coll(db, "catalog_variants")
        if variants is not None and variants.find_one(
            {"shopify_inventory_item_id": {"$exists": True, "$nin": [None, ""]}},
            {"_id": 0, "sku": 1},
        ):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def online_status_for_skus(db, skus: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return {requested_key: {online, online_stock, status}} for identifiers
    that exist in the IMS online catalog (catalog_products.ecom, resolved
    directly or via a matching catalog_variants row).

    online       -- pushed to Shopify (gid present) OR staged PUBLISHED.
    online_stock -- ALWAYS None: Shopify owns the live listed quantity and IMS
                    does not mirror it; use online_sync_health's live readers
                    for a real number. Never a fake 0.
    status       -- the ecom.status (DRAFT/PUBLISHED/ARCHIVED) when known.

    Empty dict on any failure (fail-soft)."""
    keys = _clean_keys(skus)
    if not keys or db is None:
        return {}

    products = _products_by_key(db, keys)
    variants = _variants_by_key(db, [k for k in keys if k not in products])
    parents = _parents_for_variants(db, list(variants.values()))

    out: Dict[str, Dict[str, Any]] = {}
    for key, doc in products.items():
        ecom = doc.get("ecom") or {}
        if not ecom:
            continue
        out[key] = {
            "online": _ecom_online(ecom),
            "online_stock": None,
            "status": ecom.get("status"),
        }
    for key, var in variants.items():
        if key in out:
            continue
        parent = (
            parents.get(str(var.get("parent_product_id") or ""))
            or parents.get(normalize_sku(var.get("parent_sku")))
            or {}
        )
        ecom = parent.get("ecom") or {}
        # A variant carrying Shopify ids is itself proof of a push even when
        # the parent linkage is missing.
        var_pushed = bool(
            normalize_sku(var.get("shopify_variant_id"))
            or normalize_sku(var.get("shopify_inventory_item_id"))
        )
        if not ecom and not var_pushed:
            continue
        out[key] = {
            "online": _ecom_online(ecom) or var_pushed,
            "online_stock": None,
            "status": ecom.get("status"),
        }
    return out


def inventory_items_for_skus(db, skus: List[str]) -> Dict[str, str]:
    """{requested_key: shopify_inventory_item_id} for identifiers that map to an
    online variant carrying an InventoryItem gid (catalog_variants first, then
    the catalog_products ecom sub-doc fallback -- the same two sources
    online_sync_health._inventory_item_id_for_sku reads). Fail-soft {}."""
    keys = _clean_keys(skus)
    if not keys or db is None:
        return {}
    out: Dict[str, str] = {}
    variants = _variants_by_key(db, keys)
    for key, var in variants.items():
        inv = normalize_sku(var.get("shopify_inventory_item_id"))
        if inv:
            out[key] = inv
    remaining = [k for k in keys if k not in out]
    if remaining:
        for key, doc in _products_by_key(db, remaining).items():
            inv = normalize_sku((doc.get("ecom") or {}).get("shopify_inventory_item_id"))
            if inv:
                out[key] = inv
    return out


def _online_location_id(db) -> str:
    """The Shopify location gid stock write-backs target: the
    SHOPIFY_ONLINE_LOCATION_ID env wins (authoritative single online location),
    else the integrations.shopify config's online_location_id. Fail-soft ''."""
    env_val = (os.getenv("SHOPIFY_ONLINE_LOCATION_ID") or "").strip()
    if env_val:
        return env_val
    try:
        from agents.nexus_providers import _load_integration_config

        cfg = _load_integration_config(db, "shopify") or {}
        return normalize_sku(cfg.get("online_location_id"))
    except Exception:  # noqa: BLE001
        return ""


def online_variant_targets_for_skus(db, skus: List[str]) -> Dict[str, Dict[str, Any]]:
    """Return {requested_key: {inventory_item_id, location_id}} for identifiers
    that map to an online variant carrying a Shopify InventoryItem gid -- the
    targets the POS-sale -> Shopify stock write-back pushes to.

    Source is IMS Mongo (catalog_variants.shopify_inventory_item_id, with the
    catalog_products ecom fallback). The location gid resolves, in priority
    order: SHOPIFY_ONLINE_LOCATION_ID env -> the variant's own
    shopify_location_id -> integrations.shopify online_location_id. A key with
    no usable location is skipped (the caller treats a missing target as "not
    online" -- but see online_stock_writeback's guard-gap alert, which now
    makes that loud for genuinely-online SKUs). Empty dict on any failure."""
    keys = _clean_keys(skus)
    if not keys or db is None:
        return {}
    env_location = (os.getenv("SHOPIFY_ONLINE_LOCATION_ID") or "").strip()
    fallback_location = "" if env_location else _online_location_id(db)

    out: Dict[str, Dict[str, Any]] = {}
    variants = _variants_by_key(db, keys)
    for key, var in variants.items():
        inv = normalize_sku(var.get("shopify_inventory_item_id"))
        if not inv:
            continue
        loc = env_location or normalize_sku(var.get("shopify_location_id")) or fallback_location
        if not loc:
            continue
        out[key] = {"inventory_item_id": inv, "location_id": loc}

    remaining = [k for k in keys if k not in out]
    if remaining:
        loc = env_location or fallback_location
        if loc:
            for key, doc in _products_by_key(db, remaining).items():
                inv = normalize_sku(
                    (doc.get("ecom") or {}).get("shopify_inventory_item_id")
                )
                if inv:
                    out[key] = {"inventory_item_id": inv, "location_id": loc}
    return out


def online_summary(db) -> Dict[str, Any]:
    """Small health/summary for diagnostics: is the Mongo online-truth source
    populated + counts. Shape keeps the old configured/reachable keys the FE
    types, now meaning: configured = at least one Shopify-mapped object exists;
    reachable = the catalog collections are readable."""
    out: Dict[str, Any] = {"configured": False, "reachable": False}
    prods = _coll(db, "catalog_products")
    variants = _coll(db, "catalog_variants")
    if prods is None:
        return out
    try:
        pushed_products = int(
            prods.count_documents(
                {"ecom.shopify_product_id": {"$exists": True, "$nin": [None, ""]}}
            )
        )
        published = int(prods.count_documents({"ecom.status": "PUBLISHED"}))
        draft = int(prods.count_documents({"ecom.status": "DRAFT"}))
        mapped_variants = 0
        if variants is not None:
            mapped_variants = int(
                variants.count_documents(
                    {"shopify_inventory_item_id": {"$exists": True, "$nin": [None, ""]}}
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ONLINE_CATALOG] summary failed: %s", exc)
        return out
    return {
        "configured": bool(pushed_products or mapped_variants),
        "reachable": True,
        "source": "ims_mongo",
        "online_products": pushed_products,
        "online_variants": mapped_variants,
        "published_products": published,
        "draft_products": draft,
    }


def reconcile_store_barcodes(
    pairs: Dict[str, Any], apply: bool = False, only_empty: bool = True
) -> Dict[str, Any]:
    """RETIRED (2026-07-20). This tool filled ProductVariant.storeBarcode in the
    BVI Postgres, which has been deleted -- IMS catalog_variants.store_barcode
    is now the physical join key and is maintained by the catalog itself.
    Kept as an honest no-op so the existing SUPERADMIN endpoint keeps its
    contract instead of crashing. Never writes anything."""
    return {
        "retired": True,
        "applied": False,
        "error": (
            "The BVI e-commerce Postgres was deleted on 2026-07-20; store "
            "barcodes now live on IMS catalog_variants.store_barcode and "
            "there is nothing left to reconcile."
        ),
        "input_pairs": len(pairs or {}),
    }
