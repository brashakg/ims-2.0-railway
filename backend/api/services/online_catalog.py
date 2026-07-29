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
        docs = list(
            coll.find(
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
        )
        keyset = set(keys)
        # DETERMINISTIC precedence: assign keys by FIELD priority (sku beats
        # store_barcode beats barcode beats gtin), NOT document order -- an
        # identifier that is one variant's sku and another variant's barcode
        # must always resolve to the sku owner. sku and store_barcode carry
        # unique indexes; within the non-unique barcode/gtin tiers the first
        # matching document wins (fail-soft).
        for field in _VARIANT_KEY_FIELDS:
            for doc in docs:
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
        docs = list(
            coll.find(
                _match_query(_PRODUCT_KEY_FIELDS, keys),
                {"_id": 0, "id": 1, "sku": 1, "barcode": 1, "ecom": 1},
            )
        )
        keyset = set(keys)
        # Same deterministic precedence as variants: sku assignments first,
        # then barcode fills the remainder (catalog_products.sku is unique).
        for field in _PRODUCT_KEY_FIELDS:
            for doc in docs:
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
    """DISPLAY semantics (same as the old bridge): a product is 'online' when
    it has been pushed to Shopify (gid present) OR is staged PUBLISHED. NOTE:
    this includes unpurchasable Shopify DRAFTs -- for anything guard/alert
    related use the sellable_online flag instead."""
    if not isinstance(ecom, dict):
        return False
    pushed = bool(normalize_sku(ecom.get("shopify_product_id")))
    return pushed or str(ecom.get("status") or "").upper() == "PUBLISHED"


def _ecom_sellable(ecom: Dict[str, Any]) -> bool:
    """SELLABLE semantics, ecom-status leg only: ecom.status PUBLISHED (push
    maps PUBLISHED -> Shopify ACTIVE).

    THE ACTUAL RULE lives at the call sites in online_status_for_skus, where
    this is OR-ed with "the product's own variant carries a live Shopify
    variant gid". Net effect, stated plainly: a product PUSHED to Shopify --
    even as a Shopify DRAFT -- is treated as sellable for alarm purposes,
    because shopify_push writes variant gids back for DRAFT pushes too and the
    variant-gid write-back precedes the staged-draft publish on this project's
    own roadmap. TRADE-OFF: that can raise an oversell alert for a pushed,
    not-yet-purchasable draft; it deliberately errs toward ALERTING, because
    the alternative (keying on ecom.status alone) went silent for 27 live
    Ray-Ban Meta SKUs whose IMS status said DRAFT while Shopify said ACTIVE.
    Only a staged-but-never-pushed DRAFT (no gid anywhere) stays silent."""
    if not isinstance(ecom, dict):
        return False
    return str(ecom.get("status") or "").upper() == "PUBLISHED"


def _own_variant(doc: Dict[str, Any], var: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return `var` only when it is DOC'S OWN variant, else {}.

    _variants_by_key matches a requested key on sku > store_barcode > barcode >
    gtin -- so the variant resolved for a key can be an UNRELATED variant whose
    barcode merely equals this product's sku. Lending that variant's Shopify
    gids to the product would fabricate online/sellable flags. Ownership =
    variant.parent_product_id == doc.id, or the variant's sku/parent_sku equals
    the product's sku (a PM-created parent has exactly one variant whose sku ==
    the spine sku)."""
    if not var or not isinstance(var, dict):
        return {}
    doc_id = str(doc.get("id") or "")
    if doc_id and str(var.get("parent_product_id") or "") == doc_id:
        return var
    doc_sku = normalize_sku(doc.get("sku"))
    if doc_sku and (
        normalize_sku(var.get("sku")) == doc_sku
        or normalize_sku(var.get("parent_sku")) == doc_sku
    ):
        return var
    return {}


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
    """Return {requested_key: {online, sellable_online, online_stock, status}}
    for identifiers that exist in the IMS online catalog (catalog_products.ecom,
    resolved directly or via a matching catalog_variants row).

    online          -- DISPLAY + assessment flag: pushed to Shopify (product
                       gid, or the product's OWN variant carrying a variant /
                       inventory-item gid -- resolved the SAME way on the
                       product and the variant path) OR staged PUBLISHED.
                       Includes unpurchasable Shopify DRAFTs. Gates the
                       stock-tally oversell assessment (online_sync_health)
                       and the reconcile screen.
    sellable_online -- GUARD flag: ecom.status PUBLISHED, or the product's OWN
                       variant carrying a live variant gid (same resolution on
                       both paths). PLAINLY: anything PUSHED to Shopify, even
                       as a DRAFT, counts -- errs toward alerting (see
                       _ecom_sellable). Use THIS for oversell alarms, never
                       `online` alone; a staged-but-never-pushed DRAFT has no
                       gid anywhere, so it alone stays silent.
    online_stock    -- ALWAYS None: Shopify owns the live listed quantity and
                       IMS does not mirror it; use online_sync_health's live
                       readers for a real number. Never a fake 0.
    status          -- the ecom.status (DRAFT/PUBLISHED/ARCHIVED) when known.

    Empty dict on any failure (fail-soft)."""
    keys = _clean_keys(skus)
    if not keys or db is None:
        return {}

    products = _products_by_key(db, keys)
    # ALL keys, not just the ones the product branch missed. The product branch
    # is resolved FIRST and wins, so scoping this to the residue would let a
    # catalog_products row SHADOW its own variant's live Shopify gid -- and the
    # `or live variant gid` clause below (mirrored from the variant branch) is
    # the only thing that keeps a stale-DRAFT-in-IMS-but-ACTIVE-on-Shopify SKU
    # tripping the post-sale oversell-guard alarm. Before catalog_products rows
    # carried a top-level `sku` these keys fell through to the variant branch by
    # accident; populating `sku` must not silently disarm the alarm.
    variants = _variants_by_key(db, keys)
    parents = _parents_for_variants(db, list(variants.values()))

    out: Dict[str, Dict[str, Any]] = {}
    for key, doc in products.items():
        ecom = doc.get("ecom") or {}
        if not ecom:
            continue
        # ONLY the product's OWN variant may lend it Shopify ids: the key
        # lookup matches sku/store_barcode/barcode/gtin, so an unrelated
        # variant whose barcode equals this product's sku would otherwise
        # fabricate the flags (see _own_variant).
        var = _own_variant(doc, variants.get(key))
        # Mirrors the variant branch below, BOTH flags. THE RULE, plainly: a
        # product pushed to Shopify (even as a DRAFT -- shopify_push writes
        # variant gids back for DRAFT pushes too, and the variant-gid
        # write-back precedes the staged-draft publish on this project's own
        # roadmap) is treated as online AND as sellable for alarm purposes.
        # Deliberate trade-off: errs toward ALERTING (a pushed draft may raise
        # an oversell alert before it is purchasable) rather than silence for
        # live SKUs whose IMS status lags Shopify. Only a staged-but-never-
        # pushed DRAFT (no gid anywhere) stays out of both flags. `online`
        # matters beyond display: it gates the stock-tally oversell assessment
        # (online_sync_health) and the reconcile screen.
        var_pushed = bool(
            normalize_sku(var.get("shopify_variant_id"))
            or normalize_sku(var.get("shopify_inventory_item_id"))
        )
        out[key] = {
            "online": _ecom_online(ecom) or var_pushed,
            "sellable_online": _ecom_sellable(ecom)
            or bool(normalize_sku(var.get("shopify_variant_id"))),
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
            # Sellable when the parent is PUBLISHED, or -- per the audit
            # fix-round instruction -- when the variant carries a live variant
            # gid (BVI-era imported variants whose parent linkage/ecom may be
            # missing belong to live storefront products).
            "sellable_online": _ecom_sellable(ecom)
            or bool(normalize_sku(var.get("shopify_variant_id"))),
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
