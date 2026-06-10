#!/usr/bin/env python3
"""
IMS 2.0 -- BVI PIM Migration: Postgres (BVI) -> Mongo (IMS)
============================================================
Migrates the four PIM entities from the BVI Next.js/Postgres catalog into
the IMS Mongo collections built in BVI Phases 1-4:

  ProductVariant       -> catalog_variants   (NO qty/location cols)
  Collection+CP        -> ecom_collections   (embedded products[{sku,position}])
  Menu+MenuItem        -> ecom_menus         (recursive items tree)
  ProductImage+Variant -> product_images     (kind/status/url/alt/position)

SAFETY CONTRACT
---------------
- Read-ONLY on Postgres; NEVER writes or deletes a BVI row.
- --dry-run is the DEFAULT.  Nothing is written to Mongo unless you also
  pass --commit.
- Upserts are IDEMPOTENT keyed on stable natural keys (sku, handle, bvi_image_id).
  Re-running with --commit on the same data is safe.
- Requires ECOMMERCE_DATABASE_URL (Railway env) for the Postgres connection and
  MONGODB_URL / MONGO_URL for Mongo.  Missing env -> the affected mapper is
  skipped (fail-soft), the others continue.
- psycopg2 and pymongo are lazy-imported so a missing driver only fails the
  path that needs it, not import time.

Usage
-----
  # Dry run (default): print row counts + sample docs, write nothing
  python scripts/migrate_bvi_pim.py --dry-run

  # Live upsert into Mongo:
  python scripts/migrate_bvi_pim.py --commit

  # Single entity (variants only):
  python scripts/migrate_bvi_pim.py --commit --entities variants

  # With explicit connection strings:
  python scripts/migrate_bvi_pim.py --commit \\
    --pg-url postgresql://... \\
    --mongo-url mongodb://... \\
    --db ims_2_0

--entities accepts a comma-separated subset of: variants,collections,menus,images
(default: all four).

Exit codes: 0 = success or dry-run OK;  1 = fatal import/connection error.

Notes
-----
- The 4 mapper functions (map_variant, map_collection, map_menu, map_image) are
  PURE functions: (pg_row_dict) -> mongo_doc.  They have no side effects and are
  unit-testable without a database (see backend/tests/test_migrate_bvi_pim.py).
- No emojis -- Windows cp1252 safe.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("migrate_bvi_pim")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _str(v: Any, default: str = "") -> str:
    """Coerce to str, return default for None/empty."""
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return default


def _json(v: Any) -> Optional[Any]:
    """Parse a JSON string from Postgres, or return the value as-is if already
    a dict/list, or None on failure."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(str(v))
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# MAPPER 1: ProductVariant -> catalog_variants
# ---------------------------------------------------------------------------

def map_variant(row: Dict) -> Dict:
    """Pure mapper: BVI ProductVariant PG row -> IMS catalog_variants doc.

    Key design decisions (from BVI_MERGE_PLAN.md A.2):
    - NO quantity fields: IMS stock_units is the on-hand source of truth.
    - Two-barcode model: barcode (GTIN, pushed to Shopify) kept as `gtin`;
      storeBarcode (physical, never pushed) kept as `store_barcode`.
    - Natural key / upsert key: `sku`.
    - parent_product_id is the BVI Product.id (a CUID string).
    """
    doc = {
        # Identity
        "sku": _str(row.get("sku")),
        "parent_product_id": _str(row.get("productId")),
        "parent_sku": _str(row.get("parentSku")),  # may be absent in older rows
        # Variant-defining attributes
        "color_code": _str(row.get("colorCode")),
        "color_name": _str(row.get("colorName")),
        "frame_color": _str(row.get("frameColor")),
        "temple_color": _str(row.get("templeColor")),
        "frame_size": _str(row.get("frameSize")),
        "bridge": _str(row.get("bridge")),
        "temple_length": _str(row.get("templeLength")),
        "weight": _str(row.get("weight")),
        # Lens attrs
        "lens_colour": _str(row.get("lensColour")),
        "tint": _str(row.get("tint")),
        # Pricing (variant-level overrides; parent base pricing not duplicated)
        "mrp": _float(row.get("mrp")),
        "discounted_price": _float(row.get("discountedPrice")),
        "compare_at_price": _float(row.get("compareAtPrice")),
        # Two-barcode model
        "gtin": _str(row.get("barcode")),              # GTIN/UPC -> Shopify
        "store_barcode": _str(row.get("storeBarcode")),# physical, never pushed
        # Display
        "title": _str(row.get("title")),
        # Category-specific variant fields
        "power": _str(row.get("power")),
        "pack_size": _str(row.get("packSize")),
        "cylinder": _str(row.get("cylinder")),
        "axis": _str(row.get("axis")),
        "strap_color": _str(row.get("strapColor")),
        "case_size": _str(row.get("caseSize")),
        "dial_color": _str(row.get("dialColor")),
        "extras": _json(row.get("extras")),
        # Shopify GIDs (Phases 5-6 push targets)
        "shopify_variant_id": _str(row.get("shopifyVariantId")),
        "shopify_inventory_item_id": _str(row.get("shopifyInventoryItemId")),
        # Audit
        "source": "bvi_migration",
        "migrated_at": _now_utc(),
        "bvi_variant_id": _str(row.get("id")),
    }
    # store_barcode and shopify_variant_id both carry UNIQUE+SPARSE indexes in
    # prod (verified live) -- writing "" for the thousands of variants without a
    # physical barcode / not-yet-pushed collides them all on the first such doc
    # (E11000 dup on ""). OMIT blank values entirely: sparse indexes skip docs
    # missing the field, so only real values enforce uniqueness.
    for uniq in ("store_barcode", "shopify_variant_id"):
        if not doc.get(uniq):
            doc.pop(uniq, None)
    return doc
    # NOTE: VariantLocation.quantity is deliberately NOT mapped.
    # On-hand = COUNT(stock_units WHERE sku, store, AVAILABLE).
    # Online qty = stock_allocation.recommend_allocation(on_hand, buffer).


# ---------------------------------------------------------------------------
# MAPPER 2: Collection + CollectionProduct -> ecom_collections
# ---------------------------------------------------------------------------

def map_collection(
    collection_row: Dict,
    cp_rows: List[Dict],
) -> Dict:
    """Pure mapper: BVI Collection + its CollectionProduct rows -> ecom_collections doc.

    CollectionProducts are flattened to an embedded `products: [{sku, position}]`
    array (CUSTOM collections only; SMART collections have no manual membership).

    Natural key / upsert key: `handle`.
    """
    # Build ordered embedded products from CollectionProduct rows.
    # Each cp_row is expected to have at least {position, productSku} (or we
    # look for a joined sku column from the query).
    products: List[Dict] = []
    for cp in cp_rows:
        sku = _str(cp.get("sku") or cp.get("productSku") or cp.get("variant_sku"))
        pos = _int(cp.get("position"))
        if sku:
            products.append({"sku": sku, "position": pos})
    # Sort by position for a stable embedding order.
    products.sort(key=lambda p: p["position"])
    # Re-number to ensure gapless 0-based positions.
    for i, p in enumerate(products):
        p["position"] = i

    rules_raw = row_rules = _json(collection_row.get("rules"))
    if isinstance(rules_raw, list):
        rules = rules_raw
    elif isinstance(rules_raw, str):
        try:
            rules = json.loads(rules_raw)
        except Exception:  # noqa: BLE001
            rules = []
    else:
        rules = []

    return {
        # Identity (handle is the upsert key)
        "handle": _str(collection_row.get("handle")),
        "title": _str(collection_row.get("title")),
        # Collection type
        "collection_type": _str(collection_row.get("collectionType"), "CUSTOM").upper(),
        "published": _bool(collection_row.get("published"), True),
        "sort_order": _str(collection_row.get("sortOrder")),
        "template_suffix": _str(collection_row.get("templateSuffix")),
        # Images / SEO
        "image_url": _str(collection_row.get("imageUrl")),
        "image_alt": _str(collection_row.get("imageAlt")),
        "seo_title": _str(collection_row.get("seoTitle")),
        "seo_description": _str(collection_row.get("seoDescription")),
        "description": _str(collection_row.get("description")),
        "description_html": _str(collection_row.get("descriptionHtml")),
        # Banner / sort / metafields
        "banner_image": _str(collection_row.get("bannerImage")),
        "short_description": _str(collection_row.get("shortDescription")),
        "sort_priority": _int(collection_row.get("sortPriority"), 100),
        "metafields": _json(collection_row.get("metafields")),
        # Auto-collection lineage
        "auto_source": _str(collection_row.get("autoSource")),
        "category_anchor": _str(collection_row.get("categoryAnchor")),
        # SMART rules
        "rules": rules,
        "disjunctive": _bool(collection_row.get("disjunctive"), False),
        # Manual membership (flattened from CollectionProduct join table)
        "products": products,
        "products_count": len(products),
        # Shopify GID + sync flags
        "shopify_collection_id": _str(collection_row.get("shopifyCollectionId")),
        "locally_modified": True,  # born dirty -> push queue picks it up in Phase 5
        "last_synced_at": None,
        # Audit
        "source": "bvi_migration",
        "migrated_at": _now_utc(),
        "bvi_collection_id": _str(collection_row.get("id")),
    }


# ---------------------------------------------------------------------------
# MAPPER 3: Menu + MenuItem (recursive) -> ecom_menus
# ---------------------------------------------------------------------------

def _build_item_tree(
    all_items: List[Dict],
    parent_id: Optional[str],
    position_offset: int = 0,
) -> List[Dict]:
    """Recursively build the embedded item tree from a flat list of BVI MenuItem
    rows, threading children under their parent by the BVI `parentId` FK.

    Each node gets a fresh `id` (uuid4) so IMS owns the id space; the original
    BVI MenuItem `id` is preserved as `bvi_item_id`. Parent links are tracked
    in memory only during this build; the Mongo doc uses the embedded structure,
    not a flat parentId column.
    """
    children: List[Dict] = []
    siblings = [r for r in all_items if _str(r.get("parentId")) == (parent_id or "")]
    # Sort by position within the sibling set.
    siblings.sort(key=lambda r: _int(r.get("position")))
    for pos_idx, row in enumerate(siblings):
        bvi_id = _str(row.get("id"))
        node: Dict = {
            "id": str(uuid.uuid4()),
            "bvi_item_id": bvi_id,
            "parent_id": None,          # embedded tree -- parent is structural
            "position": pos_idx,
            "title": _str(row.get("title")),
            "item_type": _str(row.get("itemType")),
            "url": _str(row.get("url")),
            "resource_id": _str(row.get("resourceId")),
            "tags_filter": _str(row.get("tagsFilter")),
            "icon_url": _str(row.get("iconUrl")),
            "banner_url": _str(row.get("bannerUrl")),
            "badge_text": _str(row.get("badgeText")),
            "badge_color": _str(row.get("badgeColor")),
            "pinned_to_top": _bool(row.get("pinnedToTop"), False),
            "shopify_item_id": _str(row.get("shopifyItemId")),
            # Recurse into children of this node.
            "children": _build_item_tree(all_items, bvi_id),
        }
        children.append(node)
    return children


def map_menu(menu_row: Dict, item_rows: List[Dict]) -> Dict:
    """Pure mapper: BVI Menu + its flat MenuItem rows -> ecom_menus doc.

    MenuItem.parentId self-relation is reconstructed as a nested `children`
    array (Mongo-natural embedded tree, same shape as EcomMenuRepository expects).

    Natural key / upsert key: `handle`.
    """
    # All items belonging to this menu, parent=None = top-level.
    items = _build_item_tree(item_rows, parent_id=None)

    return {
        # Identity (handle is the upsert key)
        "handle": _str(menu_row.get("handle")),
        "title": _str(menu_row.get("title")),
        # Flags
        "is_default": _bool(menu_row.get("isDefault"), False),
        "active": _bool(menu_row.get("active"), True),
        # Embedded recursive item tree
        "items": items,
        # Shopify GID + sync flags
        "shopify_menu_id": _str(menu_row.get("shopifyMenuId")),
        "locally_modified": True,
        "last_synced_at": None,
        # Audit
        "source": "bvi_migration",
        "migrated_at": _now_utc(),
        "bvi_menu_id": _str(menu_row.get("id")),
    }


# ---------------------------------------------------------------------------
# MAPPER 4: ProductImage + VariantImage -> product_images
# ---------------------------------------------------------------------------

def map_image(row: Dict, *, is_variant_image: bool = False) -> Dict:
    """Pure mapper: BVI ProductImage or VariantImage row -> product_images doc.

    Both BVI tables merge into ONE IMS collection discriminated by `variant_id`
    (None = product-level image; set = variant-level image).

    - kind defaults to the BVI `role` field (RAW / EDITED).
    - status defaults to QUEUED for RAW images, APPROVED for EDITED
      (EDITED = a designer already processed them; QUEUED = needs design work).
    - source = SHOPIFY when the image came from Shopify (isProcessed=true and
      a shopifyMediaId is present), else UPLOAD.

    Natural key / upsert key: `bvi_image_id` (the BVI row id).
    """
    # VariantImage has no `role` column; ProductImage defaults to "EDITED".
    # When role is absent (None/empty), default to RAW (safer: goes to design queue).
    raw_role = row.get("role")
    if raw_role is None or _str(raw_role) == "":
        bvi_role = "RAW"
    else:
        bvi_role = _str(raw_role, "RAW").upper()
    kind = bvi_role if bvi_role in ("RAW", "EDITED") else "RAW"

    # Derive status from role:
    # RAW images are QUEUED (waiting for a designer to work on them).
    # EDITED images are already designer-processed; mark APPROVED so they
    # appear as go-live in the IMS design queue without forcing re-approval.
    status = "APPROVED" if kind == "EDITED" else "QUEUED"

    # Source: if Shopify already knows about this image (shopifyMediaId present
    # and isProcessed=true), mark SHOPIFY; otherwise UPLOAD.
    shopify_media_id = _str(row.get("shopifyMediaId"))
    is_processed = _bool(row.get("isProcessed"), False)
    source = "SHOPIFY" if (shopify_media_id and is_processed) else "UPLOAD"

    return {
        # Product + variant linkage
        "product_id": _str(row.get("productId")),   # BVI Product.id (CUID)
        "variant_id": _str(row.get("variantId")) if is_variant_image else None,
        # URLs
        "url": _str(row.get("url")),
        "edited_url": _str(row.get("originalUrl")) or None,  # originalUrl = pre-edit
        # Classification
        "kind": kind,
        "status": status,
        "source": source,
        # Display
        "position": _int(row.get("position"), 0),
        "alt_text": None,      # BVI images have no alt text column
        "design_notes": None,
        # Shopify
        "shopify_image_id": shopify_media_id or None,
        # Design queue lifecycle fields (unset on migration; designers start fresh)
        "assigned_to": None,
        "reviewed_by": None,
        "approved_at": _now_utc() if status == "APPROVED" else None,
        # Audit
        "source": source,
        "migrated_at": _now_utc(),
        "bvi_image_id": _str(row.get("id")),
    }


# ---------------------------------------------------------------------------
# Postgres connection (mirrors online_catalog._connect)
# ---------------------------------------------------------------------------

def _pg_connect(pg_url: str):
    """Open a short-lived read-only Postgres connection or None on failure."""
    try:
        import psycopg2  # noqa: PLC0415 -- lazy import
        import psycopg2.extras  # noqa: PLC0415
    except ImportError as e:
        logger.error("[PG] psycopg2 not available: %s", e)
        return None
    try:
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        conn.set_session(readonly=True, autocommit=True)
        return conn
    except Exception as e:  # noqa: BLE001
        logger.error("[PG] connect failed: %s", e)
        return None


def _pg_fetchall(conn, sql: str, params: Tuple = ()) -> List[Dict]:
    """Execute a SELECT and return rows as list-of-dicts (RealDictCursor).
    Returns [] on any error (fail-soft)."""
    try:
        import psycopg2.extras  # noqa: PLC0415
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        logger.error("[PG] query failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Mongo connection + upsert helpers
# ---------------------------------------------------------------------------

def _mongo_connect(mongo_url: str, db_name: str):
    """Return (client, db) or (None, None) on failure."""
    try:
        from pymongo import MongoClient  # noqa: PLC0415 -- lazy import
        client = MongoClient(mongo_url, serverSelectionTimeoutMS=10_000)
        # Ping to confirm connectivity before starting.
        client.admin.command("ping")
        return client, client[db_name]
    except ImportError as e:
        logger.error("[MONGO] pymongo not available: %s", e)
        return None, None
    except Exception as e:  # noqa: BLE001
        logger.error("[MONGO] connect failed: %s", e)
        return None, None


def _upsert_one(
    collection,
    filter_doc: Dict,
    doc: Dict,
) -> bool:
    """Idempotent upsert using update_one with upsert=True.
    Returns True on success."""
    try:
        now = datetime.now(tz=timezone.utc)
        # created_at must live ONLY in $setOnInsert -- having it in BOTH $set
        # (via doc) and $setOnInsert is a Mongo path conflict (error 40) that
        # failed EVERY upsert on the first live run. Stamped once at insert,
        # never overwritten on a re-run; updated_at refreshes every run.
        doc.pop("created_at", None)
        doc["updated_at"] = now
        collection.update_one(
            filter_doc,
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[UPSERT] failed on filter %s: %s", filter_doc, e)
        return False


# ---------------------------------------------------------------------------
# Migration runners (one per entity)
# ---------------------------------------------------------------------------

def run_variants(
    pg_conn,
    mongo_db,
    *,
    dry_run: bool,
    sample_n: int = 3,
) -> Dict:
    """Migrate ProductVariant rows to catalog_variants."""
    logger.info("[VARIANTS] fetching from Postgres...")
    rows = _pg_fetchall(pg_conn, 'SELECT * FROM "ProductVariant" ORDER BY sku')
    total = len(rows)
    logger.info("[VARIANTS] %d rows found", total)

    docs = [map_variant(r) for r in rows]
    docs = [d for d in docs if d.get("sku")]  # guard against null-sku rows

    if dry_run:
        logger.info("[VARIANTS] [DRY-RUN] would upsert %d docs (keyed on sku)", len(docs))
        if docs:
            logger.info("[VARIANTS] sample (first %d):", min(sample_n, len(docs)))
            for d in docs[:sample_n]:
                logger.info("  sku=%s parent=%s gtin=%s store_barcode=%s",
                            d.get("sku"), d.get("parent_product_id"),
                            d.get("gtin"), d.get("store_barcode"))
        return {"entity": "variants", "pg_rows": total, "upserted": 0, "dry_run": True}

    coll = mongo_db["catalog_variants"]
    upserted = sum(
        1 for d in docs
        if _upsert_one(coll, {"sku": d["sku"]}, d)
    )
    logger.info("[VARIANTS] upserted %d / %d", upserted, len(docs))
    return {"entity": "variants", "pg_rows": total, "upserted": upserted, "dry_run": False}


def run_collections(
    pg_conn,
    mongo_db,
    *,
    dry_run: bool,
    sample_n: int = 2,
) -> Dict:
    """Migrate Collection + CollectionProduct to ecom_collections."""
    logger.info("[COLLECTIONS] fetching from Postgres...")
    col_rows = _pg_fetchall(pg_conn, 'SELECT * FROM "Collection" ORDER BY "sortPriority", handle')
    # Fetch CollectionProduct with the variant SKU so we can build the embedded array.
    # BVI: CollectionProduct joins to Product via productId; Product.sku is the identifier.
    cp_rows = _pg_fetchall(
        pg_conn,
        """
        SELECT cp."collectionId", cp.position, p.sku
        FROM   "CollectionProduct" cp
        JOIN   "Product"           p ON p.id = cp."productId"
        ORDER  BY cp."collectionId", cp.position
        """,
    )
    # Group CollectionProduct rows by collectionId.
    cp_by_coll: Dict[str, List[Dict]] = {}
    for cp in cp_rows:
        cid = _str(cp.get("collectionId"))
        cp_by_coll.setdefault(cid, []).append(cp)

    total = len(col_rows)
    logger.info("[COLLECTIONS] %d collections, %d memberships", total, len(cp_rows))

    docs = [
        map_collection(r, cp_by_coll.get(_str(r.get("id")), []))
        for r in col_rows
    ]
    docs = [d for d in docs if d.get("handle")]

    if dry_run:
        logger.info("[COLLECTIONS] [DRY-RUN] would upsert %d docs (keyed on handle)", len(docs))
        if docs:
            logger.info("[COLLECTIONS] sample (first %d):", min(sample_n, len(docs)))
            for d in docs[:sample_n]:
                logger.info("  handle=%s type=%s products=%d rules=%d",
                            d.get("handle"), d.get("collection_type"),
                            len(d.get("products") or []), len(d.get("rules") or []))
        return {"entity": "collections", "pg_rows": total, "upserted": 0, "dry_run": True}

    coll = mongo_db["ecom_collections"]
    upserted = sum(
        1 for d in docs
        if _upsert_one(coll, {"handle": d["handle"]}, d)
    )
    logger.info("[COLLECTIONS] upserted %d / %d", upserted, len(docs))
    return {"entity": "collections", "pg_rows": total, "upserted": upserted, "dry_run": False}


def run_menus(
    pg_conn,
    mongo_db,
    *,
    dry_run: bool,
    sample_n: int = 2,
) -> Dict:
    """Migrate Menu + MenuItem recursive tree to ecom_menus."""
    logger.info("[MENUS] fetching from Postgres...")
    menu_rows = _pg_fetchall(pg_conn, 'SELECT * FROM "Menu" ORDER BY handle')
    item_rows = _pg_fetchall(
        pg_conn,
        'SELECT * FROM "MenuItem" ORDER BY "menuId", "parentId" NULLS FIRST, position',
    )
    # Group items by menuId.
    items_by_menu: Dict[str, List[Dict]] = {}
    for it in item_rows:
        mid = _str(it.get("menuId"))
        items_by_menu.setdefault(mid, []).append(it)

    total = len(menu_rows)
    logger.info("[MENUS] %d menus, %d items total", total, len(item_rows))

    docs = [
        map_menu(r, items_by_menu.get(_str(r.get("id")), []))
        for r in menu_rows
    ]
    docs = [d for d in docs if d.get("handle")]

    if dry_run:
        logger.info("[MENUS] [DRY-RUN] would upsert %d docs (keyed on handle)", len(docs))
        if docs:
            logger.info("[MENUS] sample (first %d):", min(sample_n, len(docs)))
            for d in docs[:sample_n]:
                top_items = d.get("items") or []
                total_items = sum(
                    1 + len(i.get("children") or []) for i in top_items
                )
                logger.info("  handle=%s is_default=%s top_items=%d total_nodes~=%d",
                            d.get("handle"), d.get("is_default"),
                            len(top_items), total_items)
        return {"entity": "menus", "pg_rows": total, "upserted": 0, "dry_run": True}

    coll = mongo_db["ecom_menus"]
    upserted = sum(
        1 for d in docs
        if _upsert_one(coll, {"handle": d["handle"]}, d)
    )
    logger.info("[MENUS] upserted %d / %d", upserted, len(docs))
    return {"entity": "menus", "pg_rows": total, "upserted": upserted, "dry_run": False}


def run_images(
    pg_conn,
    mongo_db,
    *,
    dry_run: bool,
    sample_n: int = 3,
) -> Dict:
    """Migrate ProductImage + VariantImage to product_images."""
    logger.info("[IMAGES] fetching from Postgres...")
    prod_imgs = _pg_fetchall(pg_conn, 'SELECT * FROM "ProductImage" ORDER BY "productId", position')
    var_imgs = _pg_fetchall(pg_conn, 'SELECT * FROM "VariantImage" ORDER BY "variantId", position')
    logger.info("[IMAGES] %d product images, %d variant images", len(prod_imgs), len(var_imgs))

    docs: List[Dict] = []
    docs.extend(map_image(r, is_variant_image=False) for r in prod_imgs)
    docs.extend(map_image(r, is_variant_image=True) for r in var_imgs)
    docs = [d for d in docs if d.get("product_id") and d.get("url")]
    total_pg = len(prod_imgs) + len(var_imgs)

    if dry_run:
        logger.info("[IMAGES] [DRY-RUN] would upsert %d docs (keyed on bvi_image_id)", len(docs))
        if docs:
            logger.info("[IMAGES] sample (first %d):", min(sample_n, len(docs)))
            for d in docs[:sample_n]:
                logger.info("  product_id=%s variant_id=%s kind=%s status=%s url=%.60s",
                            d.get("product_id"), d.get("variant_id"),
                            d.get("kind"), d.get("status"), d.get("url", ""))
        return {"entity": "images", "pg_rows": total_pg, "upserted": 0, "dry_run": True}

    coll = mongo_db["product_images"]
    upserted = sum(
        1 for d in docs
        if _upsert_one(coll, {"bvi_image_id": d["bvi_image_id"]}, d)
    )
    logger.info("[IMAGES] upserted %d / %d", upserted, len(docs))
    return {"entity": "images", "pg_rows": total_pg, "upserted": upserted, "dry_run": False}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_ALL_ENTITIES = ("variants", "collections", "menus", "images")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print row counts + sample docs; write NOTHING (default: ON).",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Actually upsert into Mongo (disables --dry-run). "
             "Idempotent; safe to re-run.",
    )
    parser.add_argument(
        "--entities",
        default="variants,collections,menus,images",
        help="Comma-separated subset to migrate "
             "(variants,collections,menus,images). Default: all.",
    )
    parser.add_argument(
        "--pg-url",
        default=os.getenv("ECOMMERCE_DATABASE_URL"),
        help="BVI Postgres URL (default: $ECOMMERCE_DATABASE_URL).",
    )
    parser.add_argument(
        "--mongo-url",
        default=os.getenv("MONGODB_URL") or os.getenv("MONGO_URL"),
        help="IMS Mongo URL (default: $MONGODB_URL / $MONGO_URL).",
    )
    parser.add_argument(
        "--db",
        default=os.getenv("MONGO_DATABASE", "ims_2_0"),
        help="Mongo database name (default: ims_2_0).",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=3,
        help="Number of sample docs to print in dry-run mode (default: 3).",
    )
    args = parser.parse_args()

    # --commit wins over --dry-run so the explicit flag is always respected.
    dry_run = not args.commit

    entities_raw = [e.strip().lower() for e in args.entities.split(",")]
    entities = [e for e in entities_raw if e in _ALL_ENTITIES]
    if not entities:
        logger.error("No valid entities specified. Choose from: %s", ", ".join(_ALL_ENTITIES))
        sys.exit(1)

    mode = "DRY-RUN (no writes)" if dry_run else "COMMIT (live upserts)"
    logger.info("=" * 60)
    logger.info("BVI PIM Migration  --  mode: %s", mode)
    logger.info("entities: %s", ", ".join(entities))
    logger.info("pg_url: %s", "SET" if args.pg_url else "NOT SET")
    logger.info("mongo_url: %s", "SET" if args.mongo_url else "NOT SET")
    logger.info("db: %s", args.db)
    logger.info("=" * 60)

    if not args.pg_url:
        logger.error(
            "ECOMMERCE_DATABASE_URL is not set. "
            "Set it (or pass --pg-url) to connect to BVI Postgres."
        )
        sys.exit(1)

    # Postgres connection (read-only; shared across all mappers).
    pg_conn = _pg_connect(args.pg_url)
    if pg_conn is None:
        logger.error("Cannot connect to BVI Postgres. Aborting.")
        sys.exit(1)
    logger.info("[PG] connected (read-only)")

    # Mongo connection (only needed when --commit).
    mongo_db = None
    mongo_client = None
    if not dry_run:
        if not args.mongo_url:
            logger.error(
                "MONGODB_URL is not set. "
                "Set it (or pass --mongo-url) to connect to IMS Mongo."
            )
            try:
                pg_conn.close()
            except Exception:  # noqa: BLE001
                pass
            sys.exit(1)
        mongo_client, mongo_db = _mongo_connect(args.mongo_url, args.db)
        if mongo_db is None:
            logger.error("Cannot connect to IMS Mongo. Aborting.")
            try:
                pg_conn.close()
            except Exception:  # noqa: BLE001
                pass
            sys.exit(1)
        logger.info("[MONGO] connected to db=%s", args.db)

    # Run each requested mapper.
    results: List[Dict] = []
    runner_map = {
        "variants": run_variants,
        "collections": run_collections,
        "menus": run_menus,
        "images": run_images,
    }
    for entity in entities:
        try:
            result = runner_map[entity](
                pg_conn,
                mongo_db,
                dry_run=dry_run,
                sample_n=args.sample,
            )
            results.append(result)
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] UNEXPECTED ERROR: %s", entity.upper(), e)
            results.append({"entity": entity, "error": str(e)})

    # Tidy up connections.
    try:
        pg_conn.close()
    except Exception:  # noqa: BLE001
        pass
    if mongo_client is not None:
        try:
            mongo_client.close()
        except Exception:  # noqa: BLE001
            pass

    # Summary report.
    logger.info("")
    logger.info("=" * 60)
    logger.info("SUMMARY  --  mode: %s", mode)
    logger.info("%-16s  %8s  %8s", "entity", "pg_rows", "upserted")
    logger.info("-" * 36)
    for r in results:
        if "error" in r:
            logger.info("%-16s  ERROR: %s", r.get("entity", "?"), r["error"])
        else:
            logger.info(
                "%-16s  %8s  %8s",
                r["entity"],
                r.get("pg_rows", "-"),
                r.get("upserted", "N/A (dry-run)") if r.get("dry_run") else r.get("upserted", 0),
            )
    logger.info("=" * 60)
    if dry_run:
        logger.info(
            "REMINDER: This was a DRY-RUN. "
            "Re-run with --commit to actually write to Mongo."
        )
    else:
        logger.info("Migration complete. Verify with the parity oracle (Phase 0).")


if __name__ == "__main__":
    main()
