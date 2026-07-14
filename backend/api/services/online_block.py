"""
IMS 2.0 - "Block a collection from online sale"  (BVI-retirement, SUPERADMIN)
============================================================================
Some brands contractually forbid ONLINE sale. The owner (SUPERADMIN) flags a
collection with ``online_sync_blocked=True`` on its ``ecom_collections`` doc;
from then on EVERY product that belongs to that collection is excluded from
Shopify -- never pushed, and (if already synced) delisted.

THE MULTI-COLLECTION RULE (a HARD block):
  A product is blocked from online if it belongs to AT LEAST ONE
  ``online_sync_blocked`` collection -- REGARDLESS of its other (unblocked)
  collection memberships. A brand ban wins: being in one clean collection does
  NOT rescue a product that is also in a banned brand's collection.

MEMBERSHIP RESOLUTION (covers BOTH kinds of collection):
  * CUSTOM -> the embedded manual ``products[].sku`` list.
  * SMART  -> the rule set, evaluated with the shared ``ecom_smart_rules``
    engine (the same one the storefront browse uses).
  The fast path also consults the materialised ``collection_products`` view
  (which already holds CUSTOM + SMART membership); the direct manual / rule
  checks are the fallback so a stale-or-unmaterialised view never mis-classifies.

CONTRACT: 100% FAIL-SOFT. Any error / no DB -> ``False`` (never wrongly HIDE a
product on a lookup blip; the owner re-runs the block sweep). No exceptions
escape. No Shopify network here -- this is a pure classifier the push +
availability paths call. No emoji (Windows cp1252).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from . import ecom_smart_rules

logger = logging.getLogger(__name__)


def _blocked_collections(db) -> List[Dict[str, Any]]:
    """All ``ecom_collections`` docs flagged ``online_sync_blocked=True``.

    Usually a SMALL set (a handful of contract-banned brands), so iterating them
    per product is cheap. Fail-soft -> []."""
    if db is None:
        return []
    try:
        return list(db["ecom_collections"].find({"online_sync_blocked": True}))
    except Exception:  # noqa: BLE001 -- a config read must never raise into a push
        return []


def _product_in_custom(collection: Dict[str, Any], sku: Optional[str]) -> bool:
    """True if `sku` is a manual member of a CUSTOM collection's embedded list."""
    if not sku:
        return False
    for p in collection.get("products") or []:
        if isinstance(p, dict) and p.get("sku") == sku:
            return True
    return False


def _product_in_smart(collection: Dict[str, Any], product: Dict[str, Any]) -> bool:
    """True if `product` satisfies a SMART collection's rules (shared engine)."""
    rules = ecom_smart_rules.normalize_rules(collection.get("rules") or [])
    if not rules:
        return False
    return ecom_smart_rules.matches_product(
        product, rules, disjunctive=bool(collection.get("disjunctive", False))
    )


def is_blocked_from_online(product: Dict[str, Any], db) -> bool:
    """Is this product blocked from Shopify / online sale?

    True iff it belongs to AT LEAST ONE ``online_sync_blocked`` collection (the
    HARD multi-collection rule documented at module top -- one banned membership
    is enough, other clean memberships do NOT rescue it). Fail-soft -> False.
    """
    if not isinstance(product, dict):
        return False
    try:
        blocked = _blocked_collections(db)
        if not blocked:
            return False
        sku = product.get("sku")
        blocked_cids = [c.get("collection_id") for c in blocked if c.get("collection_id")]

        # Fast path: the materialised membership view already holds CUSTOM +
        # SMART membership for every collection.
        if sku and db is not None and blocked_cids:
            try:
                hit = db["collection_products"].find_one(
                    {"collection_id": {"$in": blocked_cids}, "sku": sku}
                )
                if hit is not None:
                    return True
            except Exception:  # noqa: BLE001 -- view is a cache; fall through
                pass

        # Fallback (view stale / never materialised): check membership directly.
        for c in blocked:
            ctype = str(c.get("collection_type") or "CUSTOM").upper()
            if ctype == "SMART":
                if _product_in_smart(c, product):
                    return True
            else:
                if _product_in_custom(c, sku):
                    return True
        return False
    except Exception:  # noqa: BLE001 -- classifier must never raise
        return False


def blocked_skus(db, skus: List[str]) -> Set[str]:
    """Batch form of ``is_blocked_from_online`` for the availability paths: given
    a list of SKUs, return the subset that is blocked from online sale.

    Efficient: loads the blocked collections ONCE, resolves membership via the
    materialised view + the manual list + (for SMART) a single batched product
    load. Fail-soft -> empty set (never wrongly mark a SKU blocked on error).
    """
    out: Set[str] = set()
    clean = [s for s in (skus or []) if s]
    if not clean or db is None:
        return out
    try:
        blocked = _blocked_collections(db)
        if not blocked:
            return out
        sku_set = set(clean)
        blocked_cids = [c.get("collection_id") for c in blocked if c.get("collection_id")]

        # 1. Materialised membership view (CUSTOM + SMART).
        if blocked_cids:
            try:
                for row in db["collection_products"].find(
                    {"collection_id": {"$in": blocked_cids}, "sku": {"$in": clean}}
                ):
                    s = row.get("sku")
                    if s in sku_set:
                        out.add(s)
            except Exception:  # noqa: BLE001
                pass

        # 2. Manual CUSTOM membership (catches an un-materialised collection).
        for c in blocked:
            if str(c.get("collection_type") or "CUSTOM").upper() != "CUSTOM":
                continue
            for p in c.get("products") or []:
                s = (p or {}).get("sku") if isinstance(p, dict) else None
                if s in sku_set:
                    out.add(s)

        # 3. SMART rules against the still-unresolved SKUs (one batched load).
        remaining = sku_set - out
        smart = [
            c for c in blocked
            if str(c.get("collection_type") or "").upper() == "SMART"
        ]
        if remaining and smart:
            prod_by_sku = _load_products_by_sku(db, list(remaining))
            for c in smart:
                rules = ecom_smart_rules.normalize_rules(c.get("rules") or [])
                if not rules:
                    continue
                disj = bool(c.get("disjunctive", False))
                for s in list(remaining):
                    doc = prod_by_sku.get(s)
                    if doc and ecom_smart_rules.matches_product(
                        doc, rules, disjunctive=disj
                    ):
                        out.add(s)
                        remaining.discard(s)
        return out
    except Exception:  # noqa: BLE001
        return out


def _load_products_by_sku(db, skus: List[str]) -> Dict[str, Dict[str, Any]]:
    """sku -> a product doc (spine `products` then `catalog_products`), for SMART
    rule evaluation. Fail-soft -> {}."""
    out: Dict[str, Dict[str, Any]] = {}
    if db is None or not skus:
        return out
    for coll_name in ("products", "catalog_products"):
        try:
            for d in db[coll_name].find({"sku": {"$in": list(skus)}}):
                s = d.get("sku")
                if s and s not in out:
                    out[s] = {k: v for k, v in d.items() if k != "_id"}
        except Exception:  # noqa: BLE001
            continue
    return out
