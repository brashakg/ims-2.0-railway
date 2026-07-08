#!/usr/bin/env python3
"""
IMS 2.0 -- BVI PIM Migration: Postgres (BVI) -> Mongo (IMS)
============================================================
Migrates the seven BVI entities from the BVI Next.js/Postgres catalog into
the IMS Mongo collections built in BVI Phases 1-4:

  Product              -> catalog_products   (parent PIM master; runs FIRST)
  ProductVariant       -> catalog_variants   (NO qty/location cols)
  Collection+CP        -> ecom_collections   (embedded products[{sku,position}])
  Menu+MenuItem        -> ecom_menus         (recursive items tree)
  ProductImage+Variant -> product_images     (kind/status/url/alt/position)
  Customer             -> customers          (DEDUPE by normalized mobile/email;
                                              ENRICH matches, never duplicate)
  Order+OrderLineItem  -> orders             (HISTORICAL records; runs LAST)

CUSTOMERS LEG (dedupe contract)
-------------------------------
An IMS customer matching the BVI row (by canonical bare-10-digit mobile FIRST,
then case-insensitive email) is ENRICHED in place: shopify_customer_id and any
MISSING fields are filled; a non-empty IMS value is NEVER overwritten and NO
duplicate is created. Unmatched rows become new docs tagged source="bvi_import"
with the same canonical skeleton shape customer_service._build_skeleton mints
(mobile normalized exactly like the IMS write path: api/services/phone.py).

ORDERS LEG SAFETY (pre-IMS history; finance must NEVER count these)
-------------------------------------------------------------------
The 9 BVI orders were transacted + settled OUTSIDE IMS books (Shopify era,
pre-migration). They are imported as PURE historical records so they (a) show
in the customer's order history (customers.py get_customer_orders applies NO
status filter) but (b) never count in IMS revenue / GST / P&L:
  * status="HISTORICAL" -- a terminal, display-only status. Every $in-based
    sold-status site (inventory/crm _SOLD_STATUSES) excludes it naturally, and
    "HISTORICAL" is added to every $nin-based excluded-status list (finance.py
    _EXCLUDED_ORDER_STATUSES + ticker/reports/payout/collection_insights/
    oracle/order_repository) -- a BVI order that was cancelled in Shopify keeps
    the honest CANCELLED status instead.
  * source="bvi_import" + historical=True belt-and-braces markers.
  * NO invoice_number (IMS never invoiced these), NO stock decrement, NO tasks.
  * shopify_order_id is stored in the BARE-numeric form the webhook ingest
    uses, so shopify_ingest's Layer-1 guard treats any future webhook replay of
    these orders as a duplicate (naturally idempotent); a live webhook-created
    order with the same shopify_order_id is NEVER clobbered (pre-checked, the
    unique partial index uniq_shopify_order_id is the hard backstop).
    online_order_mapper._sync_existing_order_status also SKIPS historical
    docs so a late orders/updated webhook cannot flip them into a counted
    status.

PRODUCTS LEG SAFETY (billing exposure)
--------------------------------------
Imported catalog_products docs are NOT POS-billable: the order-create path
(backend/api/routers/orders.py, products-convergence guard 3) resolves the
`products` SPINE first and fails LOUD (400 "exists only in the catalog and has
no billing master record") for anything that resolves only from
catalog_products. This migration writes ONLY catalog_products (no spine row),
and additionally stamps pos_ready=False + needs_review=True on every imported
doc. ecom.locally_modified is False so the Shopify push queue does NOT try to
re-push ~4k products that are already live on Shopify.

SAFETY CONTRACT
---------------
- Read-ONLY on Postgres; NEVER writes or deletes a BVI row.
- --dry-run is the DEFAULT.  Nothing is written to Mongo unless you also
  pass --commit.
- Upserts are IDEMPOTENT keyed on stable natural keys (bvi_product_id, sku,
  handle, bvi_image_id).  Re-running with --commit on the same data is safe.
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

--entities accepts a comma-separated subset of:
products,variants,collections,menus,images,customers,orders (default: all
seven; products FIRST since variants reference their parent product, customers
BEFORE orders since orders link to the deduped customer ids).

Exit codes: 0 = success or dry-run OK;  1 = fatal import/connection error.

Notes
-----
- The 7 mapper functions (map_product, map_variant, map_collection, map_menu,
  map_image, map_customer, map_order) are PURE functions:
  (pg_row_dict) -> mongo_doc.  They have no side effects and are unit-testable
  without a database (see backend/tests/test_migrate_bvi_pim.py).
- No emojis -- Windows cp1252 safe.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
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

def _normalize_rules_for_ims(rules: List[Dict]) -> List[Dict]:
    """Convert Shopify-shape smart rules ({column, relation, condition}) to the
    IMS engine shape ({field, relation, value}) using the CANONICAL normalizer
    in backend/api/services/ecom_smart_rules.py (single source of truth for the
    column map). Fail-soft: if the backend package is unavailable (standalone
    run with a stripped checkout), return the rules as-is -- the engine's
    read-side normalizer still revives them at evaluation time."""
    if not rules:
        return []
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
        _backend = os.path.join(os.path.dirname(_here), "backend")
        if _backend not in sys.path:
            sys.path.insert(0, _backend)
        from api.services.ecom_smart_rules import normalize_rules  # noqa: PLC0415

        return normalize_rules(rules)
    except Exception:  # noqa: BLE001 -- fail-soft, never block the migration
        return list(rules)


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

    rules_raw = _json(collection_row.get("rules"))
    if isinstance(rules_raw, list):
        rules = rules_raw
    elif isinstance(rules_raw, str):
        try:
            rules = json.loads(rules_raw)
        except Exception:  # noqa: BLE001
            rules = []
    else:
        rules = []
    if not isinstance(rules, list):
        rules = []
    # IMS engine shape ({field, relation, value}) is what gets stored under
    # `rules`; the original BVI/Shopify shape ({column, relation, condition})
    # is preserved verbatim under `rules_shopify` for push-side fidelity.
    rules_normalized = _normalize_rules_for_ims(rules)

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
        # SMART rules: IMS engine shape under `rules` (so the resolver + FE
        # editor work out of the box); original Shopify shape kept for fidelity.
        "rules": rules_normalized,
        "rules_shopify": rules,
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
# MAPPER 5: Product -> catalog_products  (parent leg; runs FIRST)
# ---------------------------------------------------------------------------

def _backend_on_path() -> None:
    """Make backend/ importable (same pattern as _normalize_rules_for_ims)."""
    _here = os.path.dirname(os.path.abspath(__file__))
    _backend = os.path.join(os.path.dirname(_here), "backend")
    if _backend not in sys.path:
        sys.path.insert(0, _backend)


# Inline fail-soft fallbacks, used ONLY when the backend package is unavailable
# (standalone run with a stripped checkout). They mirror the canonical tables in
# api/services/ecom_category_map.py (_TABLE) and api/services/gst_rates.py
# (GST_CATEGORY_TABLE); the canonical modules win whenever importable.
_BVI_TO_IMS_FALLBACK: Dict[str, str] = {
    "SPECTACLES": "FRAME",
    "SUNGLASSES": "SUNGLASS",
    "LENSES": "OPTICAL_LENS",
    "CONTACT_LENSES": "CONTACT_LENS",
    "COLOR_CONTACT_LENSES": "COLORED_CONTACT_LENS",
    "READING_GLASSES": "READING_GLASSES",
    "WATCHES": "WATCH",
    "SMARTWATCHES": "SMARTWATCH",
    "SMARTGLASSES": "SMARTGLASSES",
    "CLOCKS": "WALL_CLOCK",
    "ACCESSORIES": "ACCESSORIES",
    "SOLUTIONS": "ACCESSORIES",
    "SERVICES": "SERVICES",
}
_HSN_GST_FALLBACK: Dict[str, Tuple[str, float]] = {
    "FRAME": ("900311", 5.0),
    "OPTICAL_LENS": ("900150", 5.0),
    "READING_GLASSES": ("900490", 5.0),
    "CONTACT_LENS": ("900130", 5.0),
    "COLORED_CONTACT_LENS": ("900130", 5.0),
    "SUNGLASS": ("900410", 18.0),
    "WATCH": ("910111", 18.0),
    "SMARTWATCH": ("910221", 18.0),
    "SMARTGLASSES": ("852580", 18.0),
    "WALL_CLOCK": ("910500", 18.0),
    "ACCESSORIES": ("392690", 18.0),
    "SERVICES": ("998599", 18.0),
}


def map_bvi_category(bvi_category: Any) -> Tuple[str, bool]:
    """BVI category -> (IMS category, unmapped_flag).

    Uses the CANONICAL bridge in api/services/ecom_category_map.py. A BVI
    category the bridge does not know falls back to the safe default
    "ACCESSORIES" and is FLAGGED (category_unmapped=True on the doc) so it can
    be reviewed instead of silently passing through as a non-IMS enum value
    (the bridge's own passthrough contract is wrong for catalog_products docs,
    which must carry a real IMS category for GST/HSN derivation)."""
    key = _str(bvi_category).upper().replace("-", "_").replace(" ", "_")
    try:
        _backend_on_path()
        from api.services.ecom_category_map import bvi_to_ims, is_known_bvi  # noqa: PLC0415

        if is_known_bvi(key):
            return bvi_to_ims(key), False
        return "ACCESSORIES", True
    except ImportError:
        if key in _BVI_TO_IMS_FALLBACK:
            return _BVI_TO_IMS_FALLBACK[key], False
        return "ACCESSORIES", True


def hsn_gst_for_ims_category(ims_category: str) -> Tuple[Optional[str], float]:
    """(hsn_code, gst_rate) for an IMS category via the canonical
    api/services/gst_rates.py table (the same table POS bills from), with the
    inline fallback for a stripped standalone run."""
    try:
        _backend_on_path()
        from api.services.gst_rates import (  # noqa: PLC0415
            gst_rate_for_category,
            hsn_for_category,
        )

        return hsn_for_category(ims_category), gst_rate_for_category(ims_category)
    except ImportError:
        entry = _HSN_GST_FALLBACK.get(_str(ims_category).upper())
        if entry:
            return entry
        return None, 5.0  # optical-dominant default (see gst_rates.py)


def fold_variant_prices(variant_rows: Optional[List[Dict]]) -> Tuple[float, float]:
    """Fold a product's ProductVariant pricing rows into (offer_price, mrp).

    - offer_price = MIN variant selling price > 0 (discountedPrice, falling
      back to the variant's own mrp when discountedPrice is 0/absent).
    - mrp         = MAX compareAtPrice > 0 across variants; when no variant
      carries a compareAtPrice, mrp = offer_price (no strikethrough).
    - offer <= mrp is ENFORCED by lifting mrp to offer (never lowering the
      real selling price).
    Returns (0.0, 0.0) when no variant carries a usable price."""
    prices: List[float] = []
    compare_ats: List[float] = []
    for v in variant_rows or []:
        price = _float(v.get("discountedPrice"))
        if price <= 0:
            price = _float(v.get("mrp"))
        if price > 0:
            prices.append(price)
        cap = _float(v.get("compareAtPrice"))
        if cap > 0:
            compare_ats.append(cap)
    offer = min(prices) if prices else 0.0
    mrp = max(compare_ats) if compare_ats else offer
    if offer > mrp:
        mrp = offer
    return offer, mrp


def _handle_from_page_url(page_url: str) -> str:
    """Derive the storefront handle from BVI's pageUrl (last path segment,
    query/hash stripped). BVI has no dedicated handle column."""
    url = _str(page_url)
    if not url:
        return ""
    path = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    seg = path.rsplit("/", 1)[-1] if "/" in path else path
    return seg.strip().lower()


def map_product(
    row: Dict,
    variant_rows: Optional[List[Dict]] = None,
    image_rows: Optional[List[Dict]] = None,
) -> Dict:
    """Pure mapper: BVI Product PG row (+ its variant pricing rows + its
    ProductImage rows) -> IMS catalog_products doc.

    Key design decisions (owner-approved 2026-07):
    - `id` = the BVI Product.id (CUID). catalog_variants.parent_product_id
      already carries this exact value (variants leg committed first), and the
      push engine + catalog CRUD key on `id` -- so the parent<->variant link
      works with NO backfill.
    - Natural key / upsert key: `bvi_product_id` (same CUID, kept explicit for
      reversibility; `source: "bvi_import"` marks every doc).
    - category via map_bvi_category (canonical bridge; unmapped -> ACCESSORIES
      + category_unmapped=True). hsn_code/gst_rate derived from the MAPPED IMS
      category via the canonical gst_rates table.
    - Pricing folded from variants (fold_variant_prices); falls back to the
      Product row's own base pricing when no variant carries a price.
    - is_active from BVI status: ACTIVE/PUBLISHED -> True, else False.
    - pos_ready=False + needs_review=True: billing's catalog_products fallback
      exists (orders.py C-1) but guard 3 already 400s catalog-only products;
      these flags are the explicit belt-and-braces marker.
    - ecom.locally_modified=False: these products are ALREADY live on Shopify;
      the push queue must not consider them dirty.
    - sku is OMITTED when blank: catalog_products.sku carries a UNIQUE SPARSE
      index (connection.py) -- writing "" for many docs would E11000-collide on
      the first blank (same gotcha as catalog_variants.store_barcode).
    """
    bvi_id = _str(row.get("id"))
    brand = _str(row.get("brand"))
    model_no = _str(row.get("modelNo")) or _str(row.get("fullModelNo"))
    title = (
        _str(row.get("title"))
        or _str(row.get("productName"))
        or " ".join(x for x in (brand, model_no) if x)
    )
    status = _str(row.get("status")).upper()
    is_active = status in ("ACTIVE", "PUBLISHED")

    ims_category, unmapped = map_bvi_category(row.get("category"))
    hsn_code, gst_rate = hsn_gst_for_ims_category(ims_category)

    # Pricing: variants first; Product base pricing as the no-variant fallback.
    offer, mrp = fold_variant_prices(variant_rows)
    if offer <= 0:
        offer = _float(row.get("discountedPrice"))
        if offer <= 0:
            offer = _float(row.get("mrp"))
        base_cap = _float(row.get("compareAtPrice"))
        mrp = base_cap if base_cap > 0 else _float(row.get("mrp"))
        if mrp <= 0:
            mrp = offer
        if offer > mrp:
            mrp = offer

    # First few durable image URLs (Shopify CDN) so list pages render without
    # a join; the full image set lives in product_images (images leg).
    sorted_imgs = sorted(image_rows or [], key=lambda r: _int(r.get("position")))
    images = [u for u in (_str(r.get("url")) for r in sorted_imgs) if u][:3]

    tags_list = [t.strip() for t in _str(row.get("tags")).split(",") if t.strip()]

    # Shared attributes block (catalog list filters read attributes.brand_name).
    attributes: Dict[str, Any] = {}
    for attr_key, col in (
        ("brand_name", "brand"),
        ("sub_brand", "subBrand"),
        ("product_name", "productName"),
        ("gender", "gender"),
        ("shape", "shape"),
        ("frame_material", "frameMaterial"),
        ("frame_type", "frameType"),
    ):
        val = _str(row.get(col))
        if val:
            attributes[attr_key] = val
    if model_no:
        attributes["model_no"] = model_no

    handle = _handle_from_page_url(_str(row.get("pageUrl")))
    imported_at = _now_utc().isoformat()

    seo: Dict[str, Any] = {}
    if _str(row.get("seoTitle")):
        seo["title"] = _str(row.get("seoTitle"))
    if _str(row.get("seoDescription")):
        seo["description"] = _str(row.get("seoDescription"))
    if tags_list:
        seo["tags"] = tags_list

    ecom: Dict[str, Any] = {
        # As stored in BVI (incl. gid form if that is what BVI keeps) -- the
        # push engine updates the SAME Shopify object instead of duplicating.
        "shopify_product_id": _str(row.get("shopifyProductId")) or None,
        "handle": handle,
        "status": status,
        "page_url": _str(row.get("pageUrl")) or None,
        "theme_suffix": _str(row.get("themeSuffix")) or None,
        "seo": seo,
        "source": "bvi_import",
        "imported_at": imported_at,
        # CRITICAL: born CLEAN. These ~4k products are already live on Shopify;
        # locally_modified=True would flood the push queue with no-op re-pushes.
        "locally_modified": False,
        "last_synced_at": None,
    }

    doc: Dict[str, Any] = {
        # Identity (id == BVI CUID == catalog_variants.parent_product_id)
        "id": bvi_id,
        "bvi_product_id": bvi_id,
        "sku": _str(row.get("sku")),
        # Display
        "title": title,
        "name": title,
        "brand": brand,
        "description": _str(row.get("htmlDescription"))
        or _str(row.get("aboutProduct")),
        # Category + tax (derived from the MAPPED IMS category)
        "category": ims_category,
        "bvi_category": _str(row.get("category")),
        "hsn_code": hsn_code,
        "gst_rate": gst_rate,
        # Pricing: top-level for generic consumers + the nested `pricing` block
        # the order-create catalog fallback reads (orders.py C-1).
        "mrp": mrp,
        "offer_price": offer,
        "pricing": {"mrp": mrp, "offer_price": offer},
        # Media / attrs / tags
        "images": images,
        "attributes": attributes,
        "tags": tags_list,
        "rxable": _bool(row.get("rxable"), False),
        # Lifecycle + safety flags
        "is_active": is_active,
        "pos_ready": False,     # NOT a billing master (no `products` spine row)
        "needs_review": True,   # imported half-configured; review before POS use
        "reorder_quantity": -1,  # auto-reorder OFF (catalog default policy)
        # Online-store sub-doc (push engine reads ecom.*)
        "ecom": ecom,
        # Audit / reversibility
        "source": "bvi_import",
        "migrated_at": _now_utc(),
    }
    if unmapped:
        doc["category_unmapped"] = True
    # UNIQUE SPARSE index on catalog_products.sku: omit blanks entirely so the
    # sparse index skips these docs (writing "" would dup-collide).
    if not doc.get("sku"):
        doc.pop("sku", None)
    return doc


# ---------------------------------------------------------------------------
# MAPPER 6: Customer -> customers
# ---------------------------------------------------------------------------

def _default_online_store_id() -> str:
    """The store bucket ONLINE customers/orders are homed to. Mirrors the
    fallback chain tail of online_order_mapper._resolve_online_store_id."""
    return _str(os.getenv("ONLINE_STORE_ID")) or "BV-ONLINE-01"


def _normalize_mobile_safe(phone: Any) -> str:
    """Canonical Indian-mobile normalization -- the SAME rules the IMS write
    path uses (api/services/phone.normalize_indian_mobile), so a BVI phone
    dedupes against natively-created customers. Fail-soft: an unparseable /
    foreign number returns '' (no fake mobile is ever stored). The inline
    fallback is an exact copy of the canonical rules for a stripped
    standalone checkout; the canonical module wins whenever importable."""
    raw = _str(phone)
    if not raw:
        return ""
    try:
        _backend_on_path()
        from api.services.phone import normalize_indian_mobile  # noqa: PLC0415

        try:
            return normalize_indian_mobile(raw) or ""
        except ValueError:
            return ""
    except ImportError:
        digits = re.sub(r"\D", "", raw)
        if not digits:
            return ""
        if len(digits) == 13 and digits.startswith("091"):
            digits = digits[3:]
        elif len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        return digits if re.match(r"^[6-9]\d{9}$", digits) else ""


def map_customer(row: Dict) -> Dict:
    """Pure mapper: BVI Customer PG row -> IMS customers doc (create shape).

    Key design decisions:
    - customer_id is STABLE ("bvi-" + the BVI CUID) so re-runs are idempotent
      on every field, not just the upsert key.
    - mobile is normalized EXACTLY like the IMS write path (canonical
      api/services/phone.py); stored under BOTH `mobile` and `phone` (the
      repo's find_by_mobile ORs the two), verbatim input kept in raw_phone.
      An unparseable number stores '' -- mirroring the online_order_mapper
      email-only create -- so a junk number can never fake-dedupe.
    - Skeleton mirrors customer_service._build_skeleton (all four store keys,
      loyalty/credit/purchases zeroed, patients []) + the Shopify linkage
      (shopify_customer_id, email) online_order_mapper stamps.
    - BVI's own lifetime stats land in bvi_orders_count / bvi_total_spent
      (NOT the IMS total_purchases counter -- IMS counters stay untouched).
    - Natural key: bvi_customer_id. The runner dedupes by normalized mobile
      then case-insensitive email BEFORE creating (see run_customers).
    """
    bvi_id = _str(row.get("id"))
    first = _str(row.get("firstName"))
    last = _str(row.get("lastName"))
    name = " ".join(x for x in (first, last) if x)
    raw_phone = _str(row.get("phone"))
    mobile = _normalize_mobile_safe(raw_phone)
    email = _str(row.get("email"))
    store = _default_online_store_id()
    tags_list = [t.strip() for t in _str(row.get("tags")).split(",") if t.strip()]

    billing: Dict[str, Any] = {}
    for key, col in (
        ("line1", "address1"),
        ("line2", "address2"),
        ("city", "city"),
        ("state", "state"),
        ("pincode", "zip"),
        ("country", "country"),
    ):
        val = _str(row.get(col))
        if val:
            billing[key] = val

    doc: Dict[str, Any] = {
        # Identity (stable across re-runs; bvi_customer_id is the natural key)
        "customer_id": f"bvi-{bvi_id}",
        "bvi_customer_id": bvi_id,
        "name": name or email or mobile or "Online Customer",
        # Canonical phone shape (both keys, like every IMS door)
        "mobile": mobile,
        "phone": mobile,
        "raw_phone": raw_phone,
        "email": email,
        # Canonical skeleton (mirrors customer_service._build_skeleton)
        "customer_type": "B2C",
        "source": "bvi_import",
        "channel": "ONLINE",
        "home_store_id": store,
        "preferred_store_id": store,
        "primary_store_id": store,
        "store_ids": [store],
        "is_active": True,
        "loyalty_points": 0,
        "store_credit": 0.0,
        "total_purchases": 0,
        "patients": [],
        # Shopify-era metadata (informational; NOT the IMS consent flag)
        "accepts_marketing": _bool(row.get("acceptsMarketing"), False),
        "tax_exempt": _bool(row.get("taxExempt"), False),
        "tags": tags_list,
        # BVI lifetime stats kept under bvi_* so IMS counters stay honest
        "bvi_orders_count": _int(row.get("ordersCount")),
        "bvi_total_spent": _float(row.get("totalSpent")),
        "shopify_customer_id": _str(row.get("shopifyCustomerId")),
        # Audit / reversibility
        "imported_at": _now_utc().isoformat(),
        "migrated_at": _now_utc(),
        # Historical creation time (run_customers uses it on INSERT only)
        "created_at": row.get("createdAt") or _now_utc(),
        "updated_at": _now_utc(),
    }
    if billing:
        doc["billing_address"] = billing
    note = _str(row.get("note"))
    if note:
        doc["note"] = note
    if not doc["shopify_customer_id"]:
        doc.pop("shopify_customer_id", None)
    return doc


# Fields a matched existing IMS customer may be ENRICHED with (target empty
# only). Deliberately EXCLUDES identity/money/store keys: mobile, phone,
# source, channel, store homing, loyalty_points, store_credit and
# total_purchases are NEVER touched on an existing record.
_CUSTOMER_ENRICH_FIELDS = (
    "email",
    "shopify_customer_id",
    "billing_address",
)


def customer_enrichment_fields(existing: Dict, mapped: Dict) -> Dict:
    """Pure merge: the $set payload that ENRICHES an existing IMS customer
    with BVI data. Never overwrites a non-empty IMS value; never touches
    identity (mobile/phone), source/channel, store homing or money counters.
    Returns {} when there is nothing to add (caller then skips the write)."""
    update: Dict[str, Any] = {}
    for field in _CUSTOMER_ENRICH_FIELDS:
        new_val = mapped.get(field)
        if new_val in (None, "", [], {}):
            continue
        if existing.get(field) in (None, "", [], {}):
            update[field] = new_val
    # Name only when the existing record has none (placeholder-free rule:
    # an existing non-empty name always wins).
    if not _str(existing.get("name")) and _str(mapped.get("name")):
        update["name"] = mapped["name"]
    # BVI linkage stamped once (first BVI row to match wins; a second BVI row
    # deduping onto the same IMS customer keeps the original link).
    if not _str(existing.get("bvi_customer_id")) and _str(mapped.get("bvi_customer_id")):
        update["bvi_customer_id"] = mapped["bvi_customer_id"]
        update["bvi_orders_count"] = mapped.get("bvi_orders_count", 0)
        update["bvi_total_spent"] = mapped.get("bvi_total_spent", 0.0)
        update["bvi_enriched_at"] = _now_utc()
    return update


# ---------------------------------------------------------------------------
# MAPPER 7: Order + OrderLineItem -> orders  (HISTORICAL records; runs LAST)
# ---------------------------------------------------------------------------

# BVI financialStatus -> canonical IMS payment_status (same table as
# online_order_mapper._PAYMENT_STATUS_MAP). Blank -> PAID: these orders are
# pre-IMS history, already settled outside IMS books (owner decision).
_BVI_FINANCIAL_TO_PAYMENT = {
    "paid": "PAID",
    "partially_paid": "PARTIAL",
    "authorized": "UNPAID",
    "pending": "UNPAID",
    "voided": "CANCELLED",
    "refunded": "REFUNDED",
    "partially_refunded": "PARTIAL_REFUND",
}

# BVI fulfillmentStatus -> canonical IMS fulfillment_status (same table as
# online_order_mapper._FULFILLMENT_STATUS_MAP).
_BVI_FULFILLMENT_MAP = {
    "fulfilled": "FULFILLED",
    "partial": "PARTIAL",
    "restocked": "RESTOCKED",
    "": "UNFULFILLED",
}


def normalize_shopify_order_id(v: Any) -> str:
    """Reduce a Shopify order id to the BARE numeric string form the webhook
    ingest stores (shopify_ingest uses str(payload['id'])). BVI may keep the
    gid form ('gid://shopify/Order/123...'); stripping it here means the
    ingest Layer-1 guard (find_one on orders.shopify_order_id) matches our
    imported doc, so a future webhook replay is naturally idempotent."""
    s = _str(v)
    if s.startswith("gid://"):
        s = s.rsplit("/", 1)[-1]
    return s


def map_order_item(row: Dict) -> Dict:
    """Pure mapper: BVI OrderLineItem row -> embedded IMS order item dict.

    Display-shaped only (product_name/sku/qty/price/discount/item_total) --
    deliberately NO hsn/gst/taxable fields: IMS never invoiced these orders,
    so minting per-line tax would fabricate a GST liability that was settled
    outside IMS books. product_id is the BVI Product CUID, which IS the
    imported catalog_products.id (products leg), so the line links to the
    real catalog record."""
    qty = _int(row.get("quantity"), 1) or 1
    unit_price = _float(row.get("price"))
    line_discount = _float(row.get("totalDiscount"))
    gross = round(unit_price * qty - line_discount, 2)
    if gross < 0:
        gross = 0.0
    return {
        "item_id": f"bvi-{_str(row.get('id'))}",
        "product_id": _str(row.get("productId")) or None,
        "product_name": _str(row.get("title")),
        "variant_title": _str(row.get("variantTitle")) or None,
        "sku": _str(row.get("sku")),
        "quantity": qty,
        "unit_price": unit_price,
        "discount_percent": 0.0,
        "discount_amount": line_discount,
        "item_total": gross,
        "shopify_line_item_id": _str(row.get("shopifyLineItemId")) or None,
        "shopify_variant_id": _str(row.get("variantId")) or None,
    }


def map_order(
    row: Dict,
    item_rows: Optional[List[Dict]] = None,
    ims_customer_id: Optional[str] = None,
    customer_row: Optional[Dict] = None,
) -> Dict:
    """Pure mapper: BVI Order PG row (+ its OrderLineItem rows + the resolved
    IMS customer id) -> IMS orders doc, as a PURE HISTORICAL record.

    Key design decisions (see module docstring, ORDERS LEG SAFETY):
    - status="HISTORICAL" (terminal, display-only; excluded from every
      revenue/GST/P&L aggregation) -- except a BVI-cancelled order, which
      keeps the honest "CANCELLED" (also excluded everywhere).
    - invoice_number=None (IMS never minted a GST invoice for these),
      payments=[] and balance_due=0.0 (settled outside IMS books) -- so AR,
      cash-flow and Tally export can never pick them up.
    - shopify_order_id normalized to the bare-numeric webhook form (omitted
      entirely when blank: uniq_shopify_order_id is a UNIQUE partial index on
      $type string, so a '' value would dup-collide across docs).
    - customer_phone stores the NORMALIZED mobile so the customer-360 orders
      endpoint (which ORs customer_id and customer_phone) matches even when
      the customer link is missing.
    - created_at = the ORIGINAL Shopify order time (processedAt, falling back
      to createdAt) so the order sorts into its true place in history; the
      runner moves it into $setOnInsert so re-runs never shift it.
    - Natural key / upsert key: bvi_order_id.
    """
    bvi_id = _str(row.get("id"))
    fin = _str(row.get("financialStatus")).lower()
    ful = _str(row.get("fulfillmentStatus")).lower()
    cancelled = bool(row.get("cancelledAt")) or (
        _str(row.get("orderStatus")).upper() == "CANCELLED"
    )

    items = [map_order_item(r) for r in (item_rows or [])]
    items_total = round(sum(_float(i.get("item_total")) for i in items), 2)

    order_number = (
        _str(row.get("orderNumber"))
        or _str(row.get("name")).lstrip("#")
        or bvi_id
    )

    grand_total = _float(row.get("totalPrice"))
    payment_status = _BVI_FINANCIAL_TO_PAYMENT.get(fin, "PAID" if not fin else "UNPAID")

    customer_name = ""
    customer_email = _str(row.get("email"))
    raw_phone = _str(row.get("phone"))
    if customer_row:
        customer_name = " ".join(
            x
            for x in (
                _str(customer_row.get("firstName")),
                _str(customer_row.get("lastName")),
            )
            if x
        )
        customer_email = customer_email or _str(customer_row.get("email"))
        raw_phone = raw_phone or _str(customer_row.get("phone"))
    customer_phone = _normalize_mobile_safe(raw_phone)
    if not customer_name:
        customer_name = customer_email or customer_phone or "Online Customer"

    doc: Dict[str, Any] = {
        # Identity (stable across re-runs; bvi_order_id is the upsert key)
        "order_id": f"bvi-{bvi_id}",
        "bvi_order_id": bvi_id,
        "order_number": f"BVI-{order_number}",
        # Channel + historical markers ((b): NEVER counted as IMS revenue)
        "channel": "ONLINE",
        "source": "bvi_import",
        "historical": True,
        "status": "CANCELLED" if cancelled else "HISTORICAL",
        "payment_status": payment_status,
        "fulfillment_status": _BVI_FULFILLMENT_MAP.get(ful, "UNFULFILLED"),
        # Raw BVI statuses preserved verbatim for audit
        "bvi_order_status": _str(row.get("orderStatus")),
        "bvi_financial_status": fin,
        "bvi_fulfillment_status": ful,
        # Shopify linkage (bare-numeric; webhook-replay idempotency key)
        "shopify_order_id": normalize_shopify_order_id(row.get("shopifyOrderId")),
        "shopify_order_name": _str(row.get("name")) or None,
        "store_id": _default_online_store_id(),
        # Customer linkage (deduped IMS id resolved by the runner) + snapshot
        "customer_id": ims_customer_id,
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "customer_email": customer_email,
        # Lines + money (as BVI/Shopify recorded them; informational)
        "items": items,
        "subtotal": _float(row.get("subtotalPrice")) or items_total,
        "tax_amount": _float(row.get("totalTax")),
        "total_discount": _float(row.get("totalDiscount")),
        "grand_total": grand_total,
        "currency": _str(row.get("currency"), "INR"),
        "pricing_model": "inclusive",
        # NEVER invoiced by IMS; settled outside IMS books
        "invoice_number": None,
        "amount_paid": (
            grand_total
            if payment_status in ("PAID", "PARTIAL", "REFUNDED", "PARTIAL_REFUND")
            else 0.0
        ),
        "balance_due": 0.0,
        "payments": [],
        # Addresses (BVI stores them as JSON strings)
        "shipping_address": _json(row.get("shippingAddress")),
        "billing_address": _json(row.get("billingAddress")),
        # Meta
        "note": _str(row.get("note")) or None,
        "tags": [t.strip() for t in _str(row.get("tags")).split(",") if t.strip()],
        "cancelled_at": row.get("cancelledAt"),
        "closed_at": row.get("closedAt"),
        "processed_at": row.get("processedAt"),
        # Original order time (runner moves this into $setOnInsert)
        "created_at": row.get("processedAt") or row.get("createdAt") or _now_utc(),
        # Audit / reversibility
        "imported_at": _now_utc().isoformat(),
        "migrated_at": _now_utc(),
    }
    # uniq_shopify_order_id is UNIQUE+partial on $type string: omit blanks so
    # the index skips these docs ('' would dup-collide, same gotcha as
    # catalog_variants.store_barcode).
    if not doc.get("shopify_order_id"):
        doc.pop("shopify_order_id", None)
    return doc


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
    created_at: Optional[datetime] = None,
) -> bool:
    """Idempotent upsert using update_one with upsert=True.
    Returns True on success.

    `created_at` (optional) overrides the insert-time stamp -- the orders leg
    passes the ORIGINAL Shopify order time so a historical order sorts into
    its true place in history. Default (None) keeps the import time."""
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
            {"$set": doc, "$setOnInsert": {"created_at": created_at or now}},
            upsert=True,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[UPSERT] failed on filter %s: %s", filter_doc, e)
        return False


# ---------------------------------------------------------------------------
# Migration runners (one per entity)
# ---------------------------------------------------------------------------

def run_products(
    pg_conn,
    mongo_db,
    *,
    dry_run: bool,
    sample_n: int = 3,
) -> Dict:
    """Migrate Product rows to catalog_products (runs FIRST: variants,
    collections and images all reference their BVI parent product id)."""
    logger.info("[PRODUCTS] fetching from Postgres...")
    rows = _pg_fetchall(pg_conn, 'SELECT * FROM "Product" ORDER BY id')
    total = len(rows)
    logger.info("[PRODUCTS] %d rows found", total)

    # Variant pricing aggregation: only the three price columns, grouped by
    # productId in Python so the fold logic stays in ONE testable pure function.
    price_rows = _pg_fetchall(
        pg_conn,
        'SELECT "productId", mrp, "discountedPrice", "compareAtPrice" '
        'FROM "ProductVariant"',
    )
    prices_by_pid: Dict[str, List[Dict]] = {}
    for pr in price_rows:
        prices_by_pid.setdefault(_str(pr.get("productId")), []).append(pr)

    # First few product-level image URLs for the doc's images[] convenience list.
    img_rows = _pg_fetchall(
        pg_conn,
        'SELECT "productId", url, position FROM "ProductImage" '
        'ORDER BY "productId", position',
    )
    imgs_by_pid: Dict[str, List[Dict]] = {}
    for ir in img_rows:
        imgs_by_pid.setdefault(_str(ir.get("productId")), []).append(ir)

    logger.info(
        "[PRODUCTS] %d variant price rows, %d image rows joined",
        len(price_rows),
        len(img_rows),
    )

    docs = [
        map_product(
            r,
            prices_by_pid.get(_str(r.get("id")), []),
            imgs_by_pid.get(_str(r.get("id")), []),
        )
        for r in rows
    ]
    docs = [d for d in docs if d.get("bvi_product_id")]  # guard null-id rows

    if dry_run:
        logger.info(
            "[PRODUCTS] [DRY-RUN] would upsert %d docs (keyed on bvi_product_id)",
            len(docs),
        )
        unmapped = sum(1 for d in docs if d.get("category_unmapped"))
        if unmapped:
            logger.info(
                "[PRODUCTS] %d docs have an UNMAPPED BVI category "
                "(fell back to ACCESSORIES, flagged category_unmapped)",
                unmapped,
            )
        if docs:
            logger.info("[PRODUCTS] sample (first %d):", min(sample_n, len(docs)))
            for d in docs[:sample_n]:
                logger.info(
                    "  id=%s title=%.40s category=%s%s offer=%s mrp=%s "
                    "active=%s shopify=%s",
                    d.get("id"),
                    d.get("title", ""),
                    d.get("category"),
                    " (UNMAPPED)" if d.get("category_unmapped") else "",
                    d.get("offer_price"),
                    d.get("mrp"),
                    d.get("is_active"),
                    "yes" if (d.get("ecom") or {}).get("shopify_product_id") else "no",
                )
        return {"entity": "products", "pg_rows": total, "upserted": 0, "dry_run": True}

    coll = mongo_db["catalog_products"]
    upserted = sum(
        1 for d in docs
        if _upsert_one(coll, {"bvi_product_id": d["bvi_product_id"]}, d)
    )
    logger.info("[PRODUCTS] upserted %d / %d", upserted, len(docs))
    return {"entity": "products", "pg_rows": total, "upserted": upserted, "dry_run": False}


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


def run_customers(
    pg_conn,
    mongo_db,
    *,
    dry_run: bool,
    sample_n: int = 3,
) -> Dict:
    """Migrate Customer rows to customers with DEDUPE-then-ENRICH semantics.

    For every mapped BVI customer, the FIRST match wins in this order:
      1. bvi_customer_id (already imported by a previous run -> idempotent);
      2. normalized mobile against `phone` OR `mobile` (exactly the repo's
         find_by_mobile contract);
      3. case-insensitive exact email.
    A match is ENRICHED (customer_enrichment_fields: only fills EMPTY fields,
    never overwrites, never duplicates). No match -> a fresh doc tagged
    source="bvi_import" is inserted with the BVI creation time preserved.
    """
    logger.info("[CUSTOMERS] fetching from Postgres...")
    rows = _pg_fetchall(pg_conn, 'SELECT * FROM "Customer" ORDER BY id')
    total = len(rows)
    logger.info("[CUSTOMERS] %d rows found", total)

    docs = [map_customer(r) for r in rows]
    docs = [d for d in docs if d.get("bvi_customer_id")]  # guard null-id rows

    if dry_run:
        with_mobile = sum(1 for d in docs if d.get("mobile"))
        with_email = sum(1 for d in docs if d.get("email"))
        logger.info(
            "[CUSTOMERS] [DRY-RUN] would process %d docs "
            "(dedupe by mobile-then-email happens at commit time): "
            "%d carry a valid Indian mobile, %d carry an email",
            len(docs), with_mobile, with_email,
        )
        if docs:
            logger.info("[CUSTOMERS] sample (first %d):", min(sample_n, len(docs)))
            for d in docs[:sample_n]:
                logger.info(
                    "  bvi_id=%s name=%.30s mobile=%s email=%s shopify=%s",
                    d.get("bvi_customer_id"),
                    d.get("name", ""),
                    d.get("mobile") or "-",
                    d.get("email") or "-",
                    "yes" if d.get("shopify_customer_id") else "no",
                )
        return {"entity": "customers", "pg_rows": total, "upserted": 0, "dry_run": True}

    coll = mongo_db["customers"]
    created = enriched = unchanged = failed = 0
    now = datetime.now(tz=timezone.utc)
    for d in docs:
        existing = None
        try:
            existing = coll.find_one({"bvi_customer_id": d["bvi_customer_id"]})
            if existing is None and d.get("mobile"):
                # Same contract as CustomerRepository.find_by_mobile.
                existing = coll.find_one(
                    {"$or": [{"phone": d["mobile"]}, {"mobile": d["mobile"]}]}
                )
            if existing is None and d.get("email"):
                existing = coll.find_one(
                    {"email": {"$regex": f"^{re.escape(d['email'])}$", "$options": "i"}}
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("[CUSTOMERS] dedupe lookup failed for %s: %s",
                           d.get("bvi_customer_id"), e)
            failed += 1
            continue

        if existing is not None:
            update = customer_enrichment_fields(existing, d)
            if not update:
                unchanged += 1
                continue
            update["updated_at"] = now
            try:
                coll.update_one({"_id": existing["_id"]}, {"$set": update})
                enriched += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("[CUSTOMERS] enrich failed for %s: %s",
                               d.get("bvi_customer_id"), e)
                failed += 1
            continue

        try:
            coll.insert_one(dict(d))
            created += 1
        except Exception as e:  # noqa: BLE001
            # E.g. a unique-mobile race / index edge: log + continue, never
            # abort the whole leg for one row.
            logger.warning("[CUSTOMERS] insert failed for %s: %s",
                           d.get("bvi_customer_id"), e)
            failed += 1

    logger.info(
        "[CUSTOMERS] created=%d enriched=%d unchanged=%d failed=%d (of %d)",
        created, enriched, unchanged, failed, len(docs),
    )
    return {
        "entity": "customers",
        "pg_rows": total,
        "upserted": created + enriched,
        "created": created,
        "enriched": enriched,
        "unchanged": unchanged,
        "failed": failed,
        "dry_run": False,
    }


def _resolve_order_customer(coll, customer_row: Optional[Dict]) -> Optional[str]:
    """The IMS customer_id for a BVI order's Customer row: by bvi_customer_id
    first (the customers leg just wrote/enriched it), then normalized mobile,
    then case-insensitive email. Fail-soft -> None (the order still carries
    the name/phone snapshot, and customer-360 also matches on phone)."""
    if coll is None or not customer_row:
        return None
    try:
        found = coll.find_one({"bvi_customer_id": _str(customer_row.get("id"))})
        if found is None:
            mobile = _normalize_mobile_safe(customer_row.get("phone"))
            if mobile:
                found = coll.find_one(
                    {"$or": [{"phone": mobile}, {"mobile": mobile}]}
                )
        if found is None:
            email = _str(customer_row.get("email"))
            if email:
                found = coll.find_one(
                    {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}}
                )
        if found:
            return _str(found.get("customer_id")) or None
    except Exception as e:  # noqa: BLE001
        logger.warning("[ORDERS] customer resolve failed: %s", e)
    return None


def run_orders(
    pg_conn,
    mongo_db,
    *,
    dry_run: bool,
    sample_n: int = 3,
) -> Dict:
    """Migrate Order + OrderLineItem rows to orders as HISTORICAL records
    (runs LAST: orders link to the customer ids the customers leg deduped).

    Safety (see map_order):
      - upsert keyed on bvi_order_id;
      - a LIVE (webhook-created, non-bvi_import) order with the same
        shopify_order_id is NEVER clobbered -- the row is skipped loudly;
      - NO stock decrement, NO invoice minting, NO task creation.
    """
    logger.info("[ORDERS] fetching from Postgres...")
    rows = _pg_fetchall(pg_conn, 'SELECT * FROM "Order" ORDER BY "createdAt"')
    item_rows = _pg_fetchall(
        pg_conn, 'SELECT * FROM "OrderLineItem" ORDER BY "orderId", id'
    )
    cust_rows = _pg_fetchall(pg_conn, 'SELECT * FROM "Customer" ORDER BY id')
    total = len(rows)
    logger.info(
        "[ORDERS] %d orders, %d line items, %d customers joined",
        total, len(item_rows), len(cust_rows),
    )

    items_by_order: Dict[str, List[Dict]] = {}
    for ir in item_rows:
        items_by_order.setdefault(_str(ir.get("orderId")), []).append(ir)
    cust_by_id: Dict[str, Dict] = {_str(c.get("id")): c for c in cust_rows}

    customers_coll = None
    if not dry_run and mongo_db is not None:
        customers_coll = mongo_db["customers"]

    docs: List[Dict] = []
    for r in rows:
        customer_row = cust_by_id.get(_str(r.get("customerId")))
        ims_customer_id = _resolve_order_customer(customers_coll, customer_row)
        docs.append(
            map_order(
                r,
                items_by_order.get(_str(r.get("id")), []),
                ims_customer_id=ims_customer_id,
                customer_row=customer_row,
            )
        )
    docs = [d for d in docs if d.get("bvi_order_id")]  # guard null-id rows

    if dry_run:
        logger.info(
            "[ORDERS] [DRY-RUN] would upsert %d HISTORICAL docs (keyed on "
            "bvi_order_id; customer ids resolve at commit time)",
            len(docs),
        )
        if docs:
            logger.info("[ORDERS] sample (first %d):", min(sample_n, len(docs)))
            for d in docs[:sample_n]:
                logger.info(
                    "  order=%s status=%s total=%s items=%d shopify=%s customer=%.30s",
                    d.get("order_number"),
                    d.get("status"),
                    d.get("grand_total"),
                    len(d.get("items") or []),
                    "yes" if d.get("shopify_order_id") else "no",
                    d.get("customer_name", ""),
                )
        return {"entity": "orders", "pg_rows": total, "upserted": 0, "dry_run": True}

    coll = mongo_db["orders"]
    upserted = skipped_live = 0
    for d in docs:
        sid = d.get("shopify_order_id")
        if sid:
            live = None
            try:
                live = coll.find_one(
                    {"shopify_order_id": sid, "source": {"$ne": "bvi_import"}}
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("[ORDERS] live-order pre-check failed for %s: %s",
                               sid, e)
            if live:
                # A webhook already booked this Shopify order as a REAL IMS
                # order (with invoice + revenue). NEVER overwrite it with a
                # historical record; the uniq_shopify_order_id index would
                # also physically block the insert.
                logger.warning(
                    "[ORDERS] SKIP bvi_order_id=%s: shopify_order_id=%s is "
                    "already a LIVE IMS order (%s) -- not clobbering",
                    d.get("bvi_order_id"), sid, live.get("order_id"),
                )
                skipped_live += 1
                continue
        # Original Shopify order time -> $setOnInsert (stable across re-runs).
        hist_created = d.pop("created_at", None)
        if _upsert_one(
            coll, {"bvi_order_id": d["bvi_order_id"]}, d, created_at=hist_created
        ):
            upserted += 1
    logger.info(
        "[ORDERS] upserted %d / %d (skipped %d already-live)",
        upserted, len(docs), skipped_live,
    )
    return {
        "entity": "orders",
        "pg_rows": total,
        "upserted": upserted,
        "skipped_live": skipped_live,
        "dry_run": False,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_ALL_ENTITIES = (
    "products",
    "variants",
    "collections",
    "menus",
    "images",
    "customers",
    "orders",
)


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
        default="products,variants,collections,menus,images,customers,orders",
        help="Comma-separated subset to migrate "
             "(products,variants,collections,menus,images,customers,orders). "
             "Default: all; products first (variants reference their parent "
             "product), customers before orders (orders link to the deduped "
             "customer ids).",
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
        "products": run_products,
        "variants": run_variants,
        "collections": run_collections,
        "menus": run_menus,
        "images": run_images,
        "customers": run_customers,
        "orders": run_orders,
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
