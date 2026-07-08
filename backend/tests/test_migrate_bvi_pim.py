"""
Unit tests for scripts/migrate_bvi_pim.py -- the seven PURE mapper functions.

No database is needed: mappers are pure (pg_row_dict -> mongo_doc) so we can
exercise every branch with simple dicts and assert the exact Mongo doc shape.

Key assertions per mapper:
  1. map_variant   -- qty / VariantLocation fields are ABSENT; two-barcode
                      model preserved; sku is the key.
  2. map_collection -- CollectionProduct rows are FLATTENED to
                       products[{sku, position}], re-numbered 0-based; handle
                       is the key.
  3. map_menu       -- MenuItem parentId tree is rebuilt as nested children;
                       top-level items have parent_id=None; depths are correct.
  4. map_image      -- ProductImage (is_variant_image=False) sets variant_id=None;
                       VariantImage sets variant_id; role->kind->status mapping
                       is correct (RAW->QUEUED, EDITED->APPROVED).
  5. map_product    -- BVI Product -> catalog_products: category via the
                       canonical bridge (unmapped -> ACCESSORIES + flag);
                       HSN/GST from the mapped category; pricing folded from
                       variants (min price / max compareAt, offer<=mrp);
                       status->is_active; ecom.locally_modified=False (already
                       live on Shopify -- push queue must not re-push);
                       pos_ready=False (NOT POS-billable); keyed on
                       bvi_product_id.
  6. map_customer   -- mobile normalized EXACTLY like the IMS write path
                       (canonical api/services/phone rules), stored under BOTH
                       mobile+phone; source="bvi_import"; canonical skeleton;
                       enrichment merge never overwrites a non-empty IMS value.
  7. map_order      -- PURE HISTORICAL record: status="HISTORICAL" (excluded
                       from every revenue/GST/P&L aggregation; CANCELLED kept
                       honest), historical=True, NO invoice_number, payments=[],
                       balance_due=0; shopify_order_id normalized to the bare
                       webhook form (omitted when blank -- unique partial
                       index); keyed on bvi_order_id.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime

# Make the scripts directory importable without installing.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

import pytest
from migrate_bvi_pim import (
    map_variant,
    map_collection,
    map_menu,
    map_image,
    map_product,
    map_customer,
    map_order,
    map_order_item,
    customer_enrichment_fields,
    normalize_shopify_order_id,
    fold_variant_prices,
    _build_item_tree,
    _ALL_ENTITIES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _variant(overrides: dict | None = None) -> dict:
    base = {
        "id": "cuid_variant_001",
        "productId": "cuid_product_001",
        "colorCode": "086",
        "colorName": "Black",
        "frameColor": "Black Matte",
        "templeColor": "Black",
        "frameSize": "55",
        "bridge": "16",
        "templeLength": "140",
        "weight": "32",
        "lensColour": "Grey",
        "tint": "Gradient",
        "mrp": 7500.0,
        "discountedPrice": 6500.0,
        "compareAtPrice": 7500.0,
        "sku": "SP-BOSS-1234-086-55",
        "barcode": "1234567890123",    # GTIN -> shopify
        "storeBarcode": "00050567",    # physical -> never pushed
        "title": "086 / 55",
        "shopifyVariantId": "gid://shopify/ProductVariant/9876",
        "shopifyInventoryItemId": "gid://shopify/InventoryItem/1111",
        "power": None,
        "packSize": None,
        "cylinder": None,
        "axis": None,
        "strapColor": None,
        "caseSize": None,
        "dialColor": None,
        "extras": None,
        # VariantLocation rows (MUST be absent from the mapped doc)
        "_quantity_loc1": 10,
        "_quantity_loc2": 5,
        "createdAt": "2025-01-01T00:00:00",
        "updatedAt": "2025-06-01T00:00:00",
    }
    if overrides:
        base.update(overrides)
    return base


def _collection(overrides: dict | None = None) -> dict:
    base = {
        "id": "cuid_col_001",
        "shopifyCollectionId": "gid://shopify/Collection/111",
        "title": "Ray-Ban Sunglasses",
        "handle": "ray-ban-sunglasses",
        "description": "All Ray-Ban sunglasses",
        "descriptionHtml": "<p>All Ray-Ban sunglasses</p>",
        "collectionType": "CUSTOM",
        "sortOrder": "BEST_SELLING",
        "templateSuffix": None,
        "imageUrl": "https://cdn.shopify.com/col.jpg",
        "imageAlt": "Ray-Ban collection",
        "seoTitle": "Ray-Ban Sunglasses | Better Vision",
        "seoDescription": "Shop Ray-Ban sunglasses",
        "published": True,
        "productsCount": 2,
        "rules": None,
        "disjunctive": False,
        "locallyModified": False,
        "lastSyncedAt": None,
        "bannerImage": "https://cdn.shopify.com/banner.jpg",
        "shortDescription": "Premium Ray-Ban eyewear",
        "sortPriority": 10,
        "metafields": None,
        "autoSource": "brand:Ray-Ban",
        "categoryAnchor": "SUNGLASSES",
        "createdAt": "2025-01-01T00:00:00",
        "updatedAt": "2025-06-01T00:00:00",
    }
    if overrides:
        base.update(overrides)
    return base


def _cp_rows() -> list:
    """CollectionProduct rows (with joined sku from the SQL query)."""
    return [
        {"collectionId": "cuid_col_001", "position": 0, "sku": "SP-RB-WAYFARER-BLK-54"},
        {"collectionId": "cuid_col_001", "position": 1, "sku": "SP-RB-AVIATOR-GLD-58"},
    ]


def _menu(overrides: dict | None = None) -> dict:
    base = {
        "id": "cuid_menu_001",
        "shopifyMenuId": "gid://shopify/Menu/1",
        "handle": "main-menu",
        "title": "Main Menu",
        "isDefault": True,
        "active": True,
        "locallyModified": False,
        "lastSyncedAt": None,
        "createdAt": "2025-01-01T00:00:00",
        "updatedAt": "2025-06-01T00:00:00",
    }
    if overrides:
        base.update(overrides)
    return base


def _menu_items() -> list:
    """Flat MenuItem rows simulating a 2-level tree:
      Sunglasses (pos 0)
        Ray-Ban (pos 0, parent=Sunglasses)
        Oakley  (pos 1, parent=Sunglasses)
      Spectacles (pos 1)
    """
    return [
        {
            "id": "item_sg",
            "menuId": "cuid_menu_001",
            "parentId": "",       # top-level (empty string = no parent)
            "position": 0,
            "title": "Sunglasses",
            "itemType": "COLLECTION",
            "url": None,
            "resourceId": "gid://shopify/Collection/111",
            "tagsFilter": None,
            "shopifyItemId": "gid://shopify/MenuItem/10",
            "iconUrl": None,
            "bannerUrl": None,
            "badgeText": None,
            "badgeColor": None,
            "pinnedToTop": True,
        },
        {
            "id": "item_rb",
            "menuId": "cuid_menu_001",
            "parentId": "item_sg",  # child of Sunglasses
            "position": 0,
            "title": "Ray-Ban",
            "itemType": "COLLECTION",
            "url": None,
            "resourceId": "gid://shopify/Collection/222",
            "tagsFilter": None,
            "shopifyItemId": "gid://shopify/MenuItem/11",
            "iconUrl": "https://cdn.shopify.com/rb.jpg",
            "bannerUrl": None,
            "badgeText": "NEW",
            "badgeColor": "#FF0000",
            "pinnedToTop": False,
        },
        {
            "id": "item_ok",
            "menuId": "cuid_menu_001",
            "parentId": "item_sg",  # child of Sunglasses
            "position": 1,
            "title": "Oakley",
            "itemType": "COLLECTION",
            "url": None,
            "resourceId": "gid://shopify/Collection/333",
            "tagsFilter": None,
            "shopifyItemId": None,
            "iconUrl": None,
            "bannerUrl": None,
            "badgeText": None,
            "badgeColor": None,
            "pinnedToTop": False,
        },
        {
            "id": "item_sp",
            "menuId": "cuid_menu_001",
            "parentId": "",   # top-level
            "position": 1,
            "title": "Spectacles",
            "itemType": "COLLECTION",
            "url": None,
            "resourceId": "gid://shopify/Collection/444",
            "tagsFilter": None,
            "shopifyItemId": "gid://shopify/MenuItem/12",
            "iconUrl": None,
            "bannerUrl": None,
            "badgeText": None,
            "badgeColor": None,
            "pinnedToTop": False,
        },
    ]


def _product_image(overrides: dict | None = None) -> dict:
    base = {
        "id": "cuid_img_001",
        "productId": "cuid_product_001",
        "role": "EDITED",
        "url": "https://cdn.shopify.com/img.jpg",
        "originalUrl": "https://bvi.local/uploads/img_raw.jpg",
        "position": 0,
        "shopifyMediaId": "gid://shopify/MediaImage/99",
        "isProcessed": True,
        "createdAt": "2025-01-01T00:00:00",
    }
    if overrides:
        base.update(overrides)
    return base


def _variant_image(overrides: dict | None = None) -> dict:
    base = {
        "id": "cuid_varimg_001",
        "variantId": "cuid_variant_001",
        "productId": "cuid_product_001",
        "url": "https://cdn.shopify.com/varimg.jpg",
        "originalUrl": None,
        "position": 0,
        "shopifyMediaId": None,
        "isProcessed": False,
        # VariantImage has no 'role' column in BVI -- treated as RAW
        "createdAt": "2025-01-01T00:00:00",
    }
    if overrides:
        base.update(overrides)
    return base


# ===========================================================================
# MAPPER 1: map_variant
# ===========================================================================

class TestMapVariant:
    def test_sku_is_preserved(self):
        doc = map_variant(_variant())
        assert doc["sku"] == "SP-BOSS-1234-086-55"

    def test_no_quantity_fields_present(self):
        """The most critical invariant: stock qty must NOT be carried over."""
        doc = map_variant(_variant())
        qty_keys = {k for k in doc if "qty" in k.lower() or "quantity" in k.lower()
                    or k in ("on_hand", "stock", "available", "reserved")}
        assert qty_keys == set(), f"Quantity fields must be absent: {qty_keys}"

    def test_no_variant_location_fields(self):
        """VariantLocation-style per-location qty must not appear."""
        doc = map_variant(_variant())
        assert "locations" not in doc
        assert "variant_locations" not in doc

    def test_two_barcode_model(self):
        doc = map_variant(_variant())
        # barcode (GTIN) -> gtin (pushed to Shopify)
        assert doc["gtin"] == "1234567890123"
        # storeBarcode (physical) -> store_barcode (never pushed)
        assert doc["store_barcode"] == "00050567"
        # Neither should appear under the PG column names
        assert "barcode" not in doc
        assert "storeBarcode" not in doc

    def test_shopify_gids_preserved(self):
        doc = map_variant(_variant())
        assert doc["shopify_variant_id"] == "gid://shopify/ProductVariant/9876"
        assert doc["shopify_inventory_item_id"] == "gid://shopify/InventoryItem/1111"

    def test_parent_product_id_preserved(self):
        doc = map_variant(_variant())
        assert doc["parent_product_id"] == "cuid_product_001"

    def test_pricing_fields_present(self):
        doc = map_variant(_variant())
        assert doc["mrp"] == 7500.0
        assert doc["discounted_price"] == 6500.0

    def test_source_and_audit_fields(self):
        doc = map_variant(_variant())
        assert doc["source"] == "bvi_migration"
        assert doc["bvi_variant_id"] == "cuid_variant_001"
        assert "migrated_at" in doc

    def test_null_sku_variant(self):
        """A variant with no SKU maps but returns sku='' (caller filters these)."""
        doc = map_variant({})
        assert doc["sku"] == ""

    def test_extras_json_parsed(self):
        v = _variant({"extras": '{"watchFunction": "chronograph"}'})
        doc = map_variant(v)
        assert doc["extras"] == {"watchFunction": "chronograph"}

    def test_extras_already_dict(self):
        v = _variant({"extras": {"watchFunction": "chronograph"}})
        doc = map_variant(v)
        assert doc["extras"] == {"watchFunction": "chronograph"}


# ===========================================================================
# MAPPER 2: map_collection
# ===========================================================================

class TestMapCollection:
    def test_handle_is_key(self):
        doc = map_collection(_collection(), _cp_rows())
        assert doc["handle"] == "ray-ban-sunglasses"

    def test_products_embedded_and_ordered(self):
        """CollectionProduct rows are flattened to [{sku, position}], 0-based."""
        doc = map_collection(_collection(), _cp_rows())
        products = doc["products"]
        assert len(products) == 2
        assert products[0] == {"sku": "SP-RB-WAYFARER-BLK-54", "position": 0}
        assert products[1] == {"sku": "SP-RB-AVIATOR-GLD-58", "position": 1}

    def test_products_count_matches(self):
        doc = map_collection(_collection(), _cp_rows())
        assert doc["products_count"] == 2

    def test_empty_collection_products(self):
        doc = map_collection(_collection(), [])
        assert doc["products"] == []
        assert doc["products_count"] == 0

    def test_products_renumbered_0_based(self):
        """Positions from BVI (e.g. 5, 10) are re-numbered to 0, 1."""
        cp = [
            {"collectionId": "cuid_col_001", "position": 5, "sku": "SKU-A"},
            {"collectionId": "cuid_col_001", "position": 10, "sku": "SKU-B"},
        ]
        doc = map_collection(_collection(), cp)
        assert doc["products"][0]["position"] == 0
        assert doc["products"][1]["position"] == 1

    def test_collection_type_uppercased(self):
        doc = map_collection(_collection({"collectionType": "smart"}), [])
        assert doc["collection_type"] == "SMART"

    def test_rules_parsed_from_json_string(self):
        rules_json = '[{"field":"brand","relation":"EQUALS","value":"Ray-Ban"}]'
        doc = map_collection(_collection({"rules": rules_json, "collectionType": "SMART"}), [])
        assert isinstance(doc["rules"], list)
        assert len(doc["rules"]) == 1
        assert doc["rules"][0]["field"] == "brand"

    def test_locally_modified_born_true(self):
        doc = map_collection(_collection(), _cp_rows())
        assert doc["locally_modified"] is True

    def test_auto_source_and_anchor(self):
        doc = map_collection(_collection(), _cp_rows())
        assert doc["auto_source"] == "brand:Ray-Ban"
        assert doc["category_anchor"] == "SUNGLASSES"

    def test_shopify_gid_preserved(self):
        doc = map_collection(_collection(), _cp_rows())
        assert doc["shopify_collection_id"] == "gid://shopify/Collection/111"

    def test_bvi_collection_id_preserved(self):
        doc = map_collection(_collection(), _cp_rows())
        assert doc["bvi_collection_id"] == "cuid_col_001"

    def test_source_audit(self):
        doc = map_collection(_collection(), _cp_rows())
        assert doc["source"] == "bvi_migration"
        assert "migrated_at" in doc


# ===========================================================================
# MAPPER 3: map_menu (_build_item_tree)
# ===========================================================================

class TestMapMenu:
    def _get_doc(self):
        return map_menu(_menu(), _menu_items())

    def test_handle_is_key(self):
        doc = self._get_doc()
        assert doc["handle"] == "main-menu"

    def test_top_level_items_count(self):
        doc = self._get_doc()
        assert len(doc["items"]) == 2

    def test_top_level_parent_id_is_none(self):
        doc = self._get_doc()
        for item in doc["items"]:
            assert item["parent_id"] is None

    def test_nested_children_under_sunglasses(self):
        doc = self._get_doc()
        sg = doc["items"][0]
        assert sg["title"] == "Sunglasses"
        assert len(sg["children"]) == 2

    def test_grandchildren_have_parent_id_none(self):
        """In the embedded tree structure children have parent_id=None because
        their position in the tree is already expressed by nesting inside the
        parent's `children` array. There is no relational back-link needed."""
        doc = self._get_doc()
        sg = doc["items"][0]
        for child in sg["children"]:
            assert child["parent_id"] is None

    def test_children_position_0_based(self):
        doc = self._get_doc()
        sg = doc["items"][0]
        positions = [c["position"] for c in sg["children"]]
        assert positions == [0, 1]

    def test_leaf_node_has_empty_children(self):
        doc = self._get_doc()
        sg = doc["items"][0]
        for child in sg["children"]:
            assert "children" in child
            assert child["children"] == []

    def test_bvi_item_id_preserved(self):
        doc = self._get_doc()
        # The mapper stores bvi_item_id on each node.
        sg = doc["items"][0]
        assert sg["bvi_item_id"] == "item_sg"

    def test_mega_menu_fields_preserved(self):
        doc = self._get_doc()
        sg = doc["items"][0]
        assert sg["pinned_to_top"] is True
        # Ray-Ban should have badge_text
        rb = sg["children"][0]
        assert rb["badge_text"] == "NEW"
        assert rb["badge_color"] == "#FF0000"
        assert rb["icon_url"] == "https://cdn.shopify.com/rb.jpg"

    def test_is_default_and_active(self):
        doc = self._get_doc()
        assert doc["is_default"] is True
        assert doc["active"] is True

    def test_locally_modified_born_true(self):
        doc = self._get_doc()
        assert doc["locally_modified"] is True

    def test_empty_menu(self):
        doc = map_menu(_menu(), [])
        assert doc["items"] == []

    def test_shopify_menu_id_preserved(self):
        doc = self._get_doc()
        assert doc["shopify_menu_id"] == "gid://shopify/Menu/1"

    def test_source_audit(self):
        doc = self._get_doc()
        assert doc["source"] == "bvi_migration"
        assert "migrated_at" in doc


class TestBuildItemTree:
    """Unit tests for the private recursive tree builder."""

    def test_empty_items(self):
        result = _build_item_tree([], parent_id=None)
        assert result == []

    def test_single_top_level(self):
        items = [
            {"id": "a", "menuId": "m", "parentId": "", "position": 0,
             "title": "Home", "itemType": "FRONTPAGE", "url": None,
             "resourceId": None, "tagsFilter": None, "shopifyItemId": None,
             "iconUrl": None, "bannerUrl": None, "badgeText": None,
             "badgeColor": None, "pinnedToTop": False},
        ]
        result = _build_item_tree(items, parent_id=None)
        assert len(result) == 1
        assert result[0]["title"] == "Home"
        assert result[0]["children"] == []
        assert result[0]["parent_id"] is None

    def test_tree_depth_3(self):
        """Three levels: root -> child -> grandchild."""
        items = [
            {"id": "root", "menuId": "m", "parentId": "", "position": 0,
             "title": "Root", "itemType": "COLLECTION",
             "url": None, "resourceId": None, "tagsFilter": None,
             "shopifyItemId": None, "iconUrl": None, "bannerUrl": None,
             "badgeText": None, "badgeColor": None, "pinnedToTop": False},
            {"id": "child", "menuId": "m", "parentId": "root", "position": 0,
             "title": "Child", "itemType": "COLLECTION",
             "url": None, "resourceId": None, "tagsFilter": None,
             "shopifyItemId": None, "iconUrl": None, "bannerUrl": None,
             "badgeText": None, "badgeColor": None, "pinnedToTop": False},
            {"id": "gc", "menuId": "m", "parentId": "child", "position": 0,
             "title": "Grandchild", "itemType": "COLLECTION",
             "url": None, "resourceId": None, "tagsFilter": None,
             "shopifyItemId": None, "iconUrl": None, "bannerUrl": None,
             "badgeText": None, "badgeColor": None, "pinnedToTop": False},
        ]
        tree = _build_item_tree(items, parent_id=None)
        assert len(tree) == 1
        root = tree[0]
        assert len(root["children"]) == 1
        child = root["children"][0]
        assert len(child["children"]) == 1
        assert child["children"][0]["title"] == "Grandchild"


# ===========================================================================
# MAPPER 4: map_image
# ===========================================================================

class TestMapImage:
    def test_product_image_variant_id_is_none(self):
        doc = map_image(_product_image(), is_variant_image=False)
        assert doc["variant_id"] is None

    def test_variant_image_has_variant_id(self):
        doc = map_image(_variant_image(), is_variant_image=True)
        assert doc["variant_id"] == "cuid_variant_001"

    def test_edited_role_maps_to_approved(self):
        """BVI EDITED = designer processed it; IMS status = APPROVED."""
        doc = map_image(_product_image({"role": "EDITED"}), is_variant_image=False)
        assert doc["kind"] == "EDITED"
        assert doc["status"] == "APPROVED"
        assert doc["approved_at"] is not None

    def test_raw_role_maps_to_queued(self):
        """BVI RAW = awaiting designer; IMS status = QUEUED."""
        doc = map_image(_product_image({"role": "RAW"}), is_variant_image=False)
        assert doc["kind"] == "RAW"
        assert doc["status"] == "QUEUED"
        assert doc["approved_at"] is None

    def test_unknown_role_defaults_to_raw_queued(self):
        doc = map_image(_product_image({"role": "UNKNOWN"}), is_variant_image=False)
        assert doc["kind"] == "RAW"
        assert doc["status"] == "QUEUED"

    def test_source_shopify_when_processed(self):
        doc = map_image(
            _product_image({"shopifyMediaId": "gid://shopify/MediaImage/99", "isProcessed": True}),
            is_variant_image=False,
        )
        assert doc["source"] == "SHOPIFY"

    def test_source_upload_when_not_processed(self):
        doc = map_image(
            _product_image({"shopifyMediaId": None, "isProcessed": False}),
            is_variant_image=False,
        )
        assert doc["source"] == "UPLOAD"

    def test_source_upload_when_no_shopify_id(self):
        doc = map_image(
            _product_image({"shopifyMediaId": "", "isProcessed": True}),
            is_variant_image=False,
        )
        assert doc["source"] == "UPLOAD"

    def test_url_preserved(self):
        doc = map_image(_product_image(), is_variant_image=False)
        assert doc["url"] == "https://cdn.shopify.com/img.jpg"

    def test_position_preserved(self):
        doc = map_image(_product_image({"position": 3}), is_variant_image=False)
        assert doc["position"] == 3

    def test_shopify_image_id_preserved(self):
        doc = map_image(_product_image(), is_variant_image=False)
        assert doc["shopify_image_id"] == "gid://shopify/MediaImage/99"

    def test_bvi_image_id_preserved(self):
        doc = map_image(_product_image(), is_variant_image=False)
        assert doc["bvi_image_id"] == "cuid_img_001"

    def test_product_id_preserved(self):
        doc = map_image(_product_image(), is_variant_image=False)
        assert doc["product_id"] == "cuid_product_001"

    def test_source_audit(self):
        doc = map_image(_product_image(), is_variant_image=False)
        assert "migrated_at" in doc

    def test_design_queue_lifecycle_fields_unset(self):
        """assigned_to and reviewed_by should be None; designers start fresh."""
        doc = map_image(_product_image(), is_variant_image=False)
        assert doc["assigned_to"] is None
        assert doc["reviewed_by"] is None

    def test_variant_image_no_role_column_defaults_raw(self):
        """VariantImage has no role column in BVI; should default to RAW/QUEUED."""
        vi = _variant_image()
        assert "role" not in vi
        doc = map_image(vi, is_variant_image=True)
        assert doc["kind"] == "RAW"
        assert doc["status"] == "QUEUED"


# ===========================================================================
# MAPPER 5: map_product (+ fold_variant_prices)
# ===========================================================================

def _bvi_product(overrides: dict | None = None) -> dict:
    base = {
        "id": "cuid_product_001",
        "category": "SPECTACLES",
        "status": "PUBLISHED",
        "shopifyProductId": "gid://shopify/Product/1234567",
        "imageDesignStatus": None,
        "brand": "BOSS",
        "subBrand": None,
        "label": None,
        "productName": None,
        "modelNo": "1234",
        "fullModelNo": "BOSS 1234/S",
        "shape": "Rectangle",
        "frameMaterial": "Acetate",
        "templeMaterial": None,
        "frameType": "Full Rim",
        "gender": "Men",
        "countryOfOrigin": "Italy",
        "warranty": "1 year",
        "lensMaterial": None,
        "lensUSP": None,
        "polarization": None,
        "uvProtection": None,
        "productUSP": None,
        "recommendedFor": None,
        "instructions": None,
        "ingredients": None,
        "benefits": None,
        "aboutProduct": None,
        "mrp": 7500.0,
        "discountedPrice": 6500.0,
        "compareAtPrice": 7500.0,
        "title": "BOSS 1234 Rectangle Acetate Eyeglasses",
        "sku": "SP-BOSS-1234",
        "seoTitle": "BOSS 1234 | Better Vision",
        "seoDescription": "Shop BOSS 1234 eyeglasses",
        "pageUrl": "https://bettervision.in/products/boss-1234-rectangle",
        "tags": "boss, rectangle, acetate",
        "htmlDescription": "<p>BOSS 1234 premium acetate frame</p>",
        "gtin": None,
        "upc": None,
        "rxable": True,
        "themeSuffix": None,
        "categorySpecific": None,
        "createdById": None,
        "createdAt": "2025-01-01T00:00:00",
        "updatedAt": "2025-06-01T00:00:00",
    }
    if overrides:
        base.update(overrides)
    return base


def _price_rows() -> list:
    """ProductVariant pricing rows for cuid_product_001 (two colourways)."""
    return [
        {"productId": "cuid_product_001", "mrp": 7500.0,
         "discountedPrice": 6500.0, "compareAtPrice": 7500.0},
        {"productId": "cuid_product_001", "mrp": 7900.0,
         "discountedPrice": 6900.0, "compareAtPrice": 7900.0},
    ]


class TestFoldVariantPrices:
    def test_min_price_and_max_compare_at(self):
        offer, mrp = fold_variant_prices(_price_rows())
        assert offer == 6500.0   # min selling price
        assert mrp == 7900.0     # max compareAtPrice

    def test_zero_discounted_price_falls_back_to_variant_mrp(self):
        rows = [{"productId": "p", "mrp": 5000.0, "discountedPrice": 0,
                 "compareAtPrice": 0}]
        offer, mrp = fold_variant_prices(rows)
        assert offer == 5000.0
        assert mrp == 5000.0     # no compareAt -> mrp = offer

    def test_no_compare_at_mrp_equals_offer(self):
        rows = [{"productId": "p", "mrp": 0, "discountedPrice": 3000.0,
                 "compareAtPrice": 0}]
        offer, mrp = fold_variant_prices(rows)
        assert (offer, mrp) == (3000.0, 3000.0)

    def test_offer_never_exceeds_mrp(self):
        """compareAt below the selling price -> mrp is LIFTED to offer."""
        rows = [{"productId": "p", "mrp": 0, "discountedPrice": 4000.0,
                 "compareAtPrice": 3500.0}]
        offer, mrp = fold_variant_prices(rows)
        assert offer == 4000.0
        assert mrp == 4000.0
        assert offer <= mrp

    def test_zero_prices_excluded_from_min(self):
        rows = _price_rows() + [
            {"productId": "cuid_product_001", "mrp": 0,
             "discountedPrice": 0, "compareAtPrice": 0},
        ]
        offer, mrp = fold_variant_prices(rows)
        assert offer == 6500.0   # the all-zero variant does not drag min to 0

    def test_empty_rows(self):
        assert fold_variant_prices([]) == (0.0, 0.0)
        assert fold_variant_prices(None) == (0.0, 0.0)


class TestMapProduct:
    def test_happy_path_identity_and_category(self):
        doc = map_product(_bvi_product(), _price_rows())
        # id == BVI CUID == catalog_variants.parent_product_id (no backfill)
        assert doc["id"] == "cuid_product_001"
        assert doc["bvi_product_id"] == "cuid_product_001"
        assert doc["sku"] == "SP-BOSS-1234"
        assert doc["title"] == "BOSS 1234 Rectangle Acetate Eyeglasses"
        assert doc["name"] == doc["title"]
        assert doc["brand"] == "BOSS"
        # SPECTACLES -> FRAME via the canonical bridge; not flagged
        assert doc["category"] == "FRAME"
        assert doc["bvi_category"] == "SPECTACLES"
        assert "category_unmapped" not in doc

    def test_hsn_gst_derived_from_mapped_category(self):
        doc = map_product(_bvi_product(), _price_rows())
        # FRAME -> 9003 frames -> 5% (gst_rates.py canonical table)
        assert doc["hsn_code"] == "900311"
        assert doc["gst_rate"] == 5.0

    def test_sunglasses_category_gets_18_percent(self):
        doc = map_product(_bvi_product({"category": "SUNGLASSES"}), _price_rows())
        assert doc["category"] == "SUNGLASS"
        assert doc["gst_rate"] == 18.0

    def test_unmapped_category_falls_back_flagged(self):
        doc = map_product(_bvi_product({"category": "GADGETS"}), _price_rows())
        assert doc["category"] == "ACCESSORIES"
        assert doc["category_unmapped"] is True
        assert doc["bvi_category"] == "GADGETS"
        # HSN/GST follow the FALLBACK category (accessories -> 18%)
        assert doc["gst_rate"] == 18.0

    def test_solutions_maps_to_accessories_not_flagged(self):
        """SOLUTIONS is a KNOWN BVI category (lens care fluids -> ACCESSORIES)."""
        doc = map_product(_bvi_product({"category": "SOLUTIONS"}), _price_rows())
        assert doc["category"] == "ACCESSORIES"
        assert "category_unmapped" not in doc

    def test_pricing_folded_from_variants(self):
        doc = map_product(_bvi_product(), _price_rows())
        assert doc["offer_price"] == 6500.0   # min variant price
        assert doc["mrp"] == 7900.0           # max compareAtPrice
        assert doc["offer_price"] <= doc["mrp"]
        # Nested pricing block (orders.py catalog fallback reads this shape)
        assert doc["pricing"] == {"mrp": 7900.0, "offer_price": 6500.0}

    def test_pricing_falls_back_to_product_base_when_no_variants(self):
        doc = map_product(_bvi_product(), [])
        assert doc["offer_price"] == 6500.0   # Product.discountedPrice
        assert doc["mrp"] == 7500.0           # Product.compareAtPrice
        assert doc["offer_price"] <= doc["mrp"]

    def test_status_published_is_active(self):
        doc = map_product(_bvi_product({"status": "PUBLISHED"}), _price_rows())
        assert doc["is_active"] is True

    def test_status_active_is_active(self):
        doc = map_product(_bvi_product({"status": "ACTIVE"}), _price_rows())
        assert doc["is_active"] is True

    def test_status_draft_and_archived_inactive(self):
        for status in ("DRAFT", "ARCHIVED"):
            doc = map_product(_bvi_product({"status": status}), _price_rows())
            assert doc["is_active"] is False, status

    def test_ecom_locally_modified_false(self):
        """CRITICAL: already live on Shopify -- push queue must NOT re-push."""
        doc = map_product(_bvi_product(), _price_rows())
        assert doc["ecom"]["locally_modified"] is False

    def test_ecom_subdoc_shape(self):
        doc = map_product(_bvi_product(), _price_rows())
        ecom = doc["ecom"]
        # gid kept exactly as BVI stores it
        assert ecom["shopify_product_id"] == "gid://shopify/Product/1234567"
        assert ecom["handle"] == "boss-1234-rectangle"  # from pageUrl
        assert ecom["status"] == "PUBLISHED"
        assert ecom["source"] == "bvi_import"
        assert "imported_at" in ecom
        assert ecom["seo"]["title"] == "BOSS 1234 | Better Vision"

    def test_pos_safety_flags(self):
        """Imported docs must NOT silently become POS-billable half-configured
        items: no `products` spine row is written (order-create guard 3 fails
        loud), and the explicit flags mark them for review."""
        doc = map_product(_bvi_product(), _price_rows())
        assert doc["pos_ready"] is False
        assert doc["needs_review"] is True

    def test_reorder_quantity_disabled(self):
        doc = map_product(_bvi_product(), _price_rows())
        assert doc["reorder_quantity"] == -1

    def test_source_bvi_import_top_level(self):
        doc = map_product(_bvi_product(), _price_rows())
        assert doc["source"] == "bvi_import"
        assert "migrated_at" in doc

    def test_blank_sku_omitted(self):
        """catalog_products.sku has a UNIQUE SPARSE index: blank skus must be
        OMITTED entirely, never written as '' (E11000 on the first blank)."""
        doc = map_product(_bvi_product({"sku": None}), _price_rows())
        assert "sku" not in doc

    def test_images_first_three_by_position(self):
        imgs = [
            {"productId": "cuid_product_001", "url": f"https://cdn/img{i}.jpg",
             "position": i}
            for i in (3, 0, 2, 1)
        ]
        doc = map_product(_bvi_product(), _price_rows(), imgs)
        assert doc["images"] == [
            "https://cdn/img0.jpg",
            "https://cdn/img1.jpg",
            "https://cdn/img2.jpg",
        ]

    def test_no_images_leaves_empty_list(self):
        doc = map_product(_bvi_product(), _price_rows())
        assert doc["images"] == []

    def test_tags_split_to_list(self):
        doc = map_product(_bvi_product(), _price_rows())
        assert doc["tags"] == ["boss", "rectangle", "acetate"]
        assert doc["ecom"]["seo"]["tags"] == ["boss", "rectangle", "acetate"]

    def test_title_fallback_brand_model(self):
        doc = map_product(
            _bvi_product({"title": None, "productName": None}), _price_rows()
        )
        assert doc["title"] == "BOSS 1234"

    def test_attributes_brand_name_for_catalog_filter(self):
        """Catalog list filters on attributes.brand_name."""
        doc = map_product(_bvi_product(), _price_rows())
        assert doc["attributes"]["brand_name"] == "BOSS"
        assert doc["attributes"]["model_no"] == "1234"

    def test_null_id_product_maps_to_blank_key(self):
        """A row with no id maps but bvi_product_id='' (caller filters these)."""
        doc = map_product({}, [])
        assert doc["bvi_product_id"] == ""


# ===========================================================================
# MAPPER 6: map_customer (+ customer_enrichment_fields)
# ===========================================================================

def _bvi_customer(overrides: dict | None = None) -> dict:
    base = {
        "id": "cuid_cust_001",
        "shopifyCustomerId": "gid://shopify/Customer/555",
        "email": "Asha.Rao@Example.com",
        "phone": "+91 98765 43210",
        "firstName": "Asha",
        "lastName": "Rao",
        "address1": "12 MG Road",
        "address2": "Flat 3B",
        "city": "Ranchi",
        "state": "Jharkhand",
        "zip": "834001",
        "country": "India",
        "ordersCount": 3,
        "totalSpent": 12500.0,
        "tags": "vip, newsletter",
        "note": "Prefers WhatsApp",
        "acceptsMarketing": True,
        "taxExempt": False,
        "verified": True,
        "createdAt": datetime(2024, 5, 1, 10, 0, 0),
        "updatedAt": datetime(2025, 1, 1, 10, 0, 0),
    }
    if overrides:
        base.update(overrides)
    return base


class TestMapCustomer:
    def test_mobile_normalized_like_ims_write_path(self):
        """'+91 98765 43210' collapses to the bare 10-digit canonical form --
        the SAME rule the customers router / customer_service apply -- so the
        dedupe key matches natively-created customers."""
        doc = map_customer(_bvi_customer())
        assert doc["mobile"] == "9876543210"
        # Stored under BOTH keys (find_by_mobile ORs phone|mobile).
        assert doc["phone"] == "9876543210"
        # Verbatim input preserved for traceability.
        assert doc["raw_phone"] == "+91 98765 43210"

    def test_invalid_phone_stores_empty_never_fake(self):
        """A foreign / junk number must NOT be persisted as a fake mobile that
        can never dedupe (mirrors the online_order_mapper contract)."""
        doc = map_customer(_bvi_customer({"phone": "+1 555 0100"}))
        assert doc["mobile"] == ""
        assert doc["phone"] == ""
        assert doc["raw_phone"] == "+1 555 0100"

    def test_zero_trunk_prefix_stripped(self):
        doc = map_customer(_bvi_customer({"phone": "09876543210"}))
        assert doc["mobile"] == "9876543210"

    def test_source_and_channel_tags(self):
        doc = map_customer(_bvi_customer())
        assert doc["source"] == "bvi_import"
        assert doc["channel"] == "ONLINE"
        assert "imported_at" in doc
        assert "migrated_at" in doc

    def test_stable_customer_id_and_natural_key(self):
        """customer_id derives from the BVI CUID (stable across re-runs, NOT a
        fresh uuid) and bvi_customer_id is the runner's idempotency key."""
        doc = map_customer(_bvi_customer())
        assert doc["customer_id"] == "bvi-cuid_cust_001"
        assert doc["bvi_customer_id"] == "cuid_cust_001"

    def test_canonical_skeleton_shape(self):
        """Mirrors customer_service._build_skeleton: all four store keys +
        zeroed counters, so the doc shows in every store-scoped list."""
        doc = map_customer(_bvi_customer())
        assert doc["customer_type"] == "B2C"
        assert doc["is_active"] is True
        assert doc["loyalty_points"] == 0
        assert doc["store_credit"] == 0.0
        assert doc["total_purchases"] == 0
        assert doc["patients"] == []
        store = doc["home_store_id"]
        assert store  # online bucket
        assert doc["preferred_store_id"] == store
        assert doc["primary_store_id"] == store
        assert doc["store_ids"] == [store]

    def test_bvi_stats_do_not_touch_ims_counters(self):
        """BVI lifetime stats land under bvi_* -- IMS total_purchases stays 0
        so no pre-IMS rupee ever leaks into IMS-side counters."""
        doc = map_customer(_bvi_customer())
        assert doc["bvi_orders_count"] == 3
        assert doc["bvi_total_spent"] == 12500.0
        assert doc["total_purchases"] == 0

    def test_name_email_and_address(self):
        doc = map_customer(_bvi_customer())
        assert doc["name"] == "Asha Rao"
        assert doc["email"] == "Asha.Rao@Example.com"
        assert doc["billing_address"] == {
            "line1": "12 MG Road",
            "line2": "Flat 3B",
            "city": "Ranchi",
            "state": "Jharkhand",
            "pincode": "834001",
            "country": "India",
        }

    def test_tags_split_and_shopify_id(self):
        doc = map_customer(_bvi_customer())
        assert doc["tags"] == ["vip", "newsletter"]
        assert doc["shopify_customer_id"] == "gid://shopify/Customer/555"

    def test_blank_shopify_id_omitted(self):
        doc = map_customer(_bvi_customer({"shopifyCustomerId": None}))
        assert "shopify_customer_id" not in doc

    def test_original_creation_time_preserved(self):
        """created_at carries the BVI creation time (runner uses it on INSERT
        only) so the customer sorts into their true place in history."""
        doc = map_customer(_bvi_customer())
        assert doc["created_at"] == datetime(2024, 5, 1, 10, 0, 0)

    def test_name_falls_back_when_blank(self):
        doc = map_customer(
            _bvi_customer({"firstName": None, "lastName": None, "email": None})
        )
        assert doc["name"] == "9876543210"

    def test_null_id_maps_to_blank_key(self):
        doc = map_customer({})
        assert doc["bvi_customer_id"] == ""


class TestCustomerEnrichment:
    """customer_enrichment_fields: the pure merge that ENRICHES a matched
    existing IMS customer. Contract: fill EMPTY fields only, never overwrite a
    non-empty IMS value, never touch identity/store/money keys, {} = no-op."""

    def test_fills_missing_email_and_shopify_id(self):
        existing = {"customer_id": "u-1", "name": "Asha", "mobile": "9876543210"}
        update = customer_enrichment_fields(existing, map_customer(_bvi_customer()))
        assert update["email"] == "Asha.Rao@Example.com"
        assert update["shopify_customer_id"] == "gid://shopify/Customer/555"
        assert update["billing_address"]["city"] == "Ranchi"

    def test_never_overwrites_non_empty_ims_values(self):
        existing = {
            "customer_id": "u-1",
            "name": "Asha R (POS)",
            "mobile": "9876543210",
            "email": "asha@ims.example",
            "shopify_customer_id": "gid://shopify/Customer/999",
            "billing_address": {"line1": "IMS Street"},
        }
        update = customer_enrichment_fields(existing, map_customer(_bvi_customer()))
        assert "email" not in update
        assert "shopify_customer_id" not in update
        assert "billing_address" not in update
        assert "name" not in update

    def test_identity_and_money_keys_never_touched(self):
        existing = {"customer_id": "u-1", "name": "X", "mobile": "9876543210"}
        update = customer_enrichment_fields(existing, map_customer(_bvi_customer()))
        for forbidden in (
            "mobile", "phone", "customer_id", "source", "channel",
            "loyalty_points", "store_credit", "total_purchases",
            "home_store_id", "store_ids",
        ):
            assert forbidden not in update, forbidden

    def test_bvi_link_stamped_once(self):
        existing = {"customer_id": "u-1", "name": "X", "mobile": "9876543210"}
        update = customer_enrichment_fields(existing, map_customer(_bvi_customer()))
        assert update["bvi_customer_id"] == "cuid_cust_001"
        # A second BVI row deduping onto the SAME (already linked) IMS customer
        # keeps the original link.
        already = {**existing, "bvi_customer_id": "cuid_cust_000",
                   "email": "a@b.c", "shopify_customer_id": "s",
                   "billing_address": {"line1": "x"}}
        assert customer_enrichment_fields(already, map_customer(_bvi_customer())) == {}

    def test_fully_enriched_match_is_a_noop(self):
        mapped = map_customer(_bvi_customer())
        existing = dict(mapped)  # everything already present
        assert customer_enrichment_fields(existing, mapped) == {}

    def test_name_filled_only_when_existing_blank(self):
        existing = {"customer_id": "u-1", "name": "", "mobile": "9876543210"}
        update = customer_enrichment_fields(existing, map_customer(_bvi_customer()))
        assert update["name"] == "Asha Rao"


# ===========================================================================
# MAPPER 7: map_order (+ map_order_item, normalize_shopify_order_id)
# ===========================================================================

def _bvi_order(overrides: dict | None = None) -> dict:
    base = {
        "id": "cuid_order_001",
        "shopifyOrderId": "5551112223334",
        "orderNumber": "1042",
        "name": "#1042",
        "email": "asha.rao@example.com",
        "phone": "+91 98765 43210",
        "totalPrice": 6500.0,
        "subtotalPrice": 6500.0,
        "totalTax": 309.52,
        "totalDiscount": 0.0,
        "currency": "INR",
        "financialStatus": "paid",
        "fulfillmentStatus": "fulfilled",
        "orderStatus": "CLOSED",
        "customerId": "cuid_cust_001",
        "shippingAddress": '{"city": "Ranchi", "province": "Jharkhand"}',
        "billingAddress": None,
        "note": None,
        "tags": "online, prepaid",
        "source": "web",
        "cancelReason": None,
        "cancelledAt": None,
        "closedAt": datetime(2024, 6, 5, 12, 0, 0),
        "processedAt": datetime(2024, 6, 1, 9, 30, 0),
        "createdAt": datetime(2024, 6, 1, 9, 31, 0),
        "updatedAt": datetime(2024, 6, 5, 12, 0, 0),
    }
    if overrides:
        base.update(overrides)
    return base


def _bvi_line_items() -> list:
    return [
        {
            "id": "cuid_line_001",
            "orderId": "cuid_order_001",
            "productId": "cuid_product_001",
            "variantId": "gid://shopify/ProductVariant/9876",
            "shopifyLineItemId": "111",
            "title": "BOSS 1234 Rectangle Acetate Eyeglasses",
            "variantTitle": "086 / 55",
            "sku": "SP-BOSS-1234-086-55",
            "quantity": 1,
            "price": 6500.0,
            "totalDiscount": 0.0,
        },
    ]


class TestNormalizeShopifyOrderId:
    def test_bare_numeric_passthrough(self):
        assert normalize_shopify_order_id("5551112223334") == "5551112223334"

    def test_gid_form_stripped_to_bare(self):
        """The webhook ingest stores str(payload['id']) -- the BARE numeric --
        so its Layer-1 dedupe guard only matches if we store the same form."""
        assert (
            normalize_shopify_order_id("gid://shopify/Order/5551112223334")
            == "5551112223334"
        )

    def test_blank_and_none(self):
        assert normalize_shopify_order_id(None) == ""
        assert normalize_shopify_order_id("") == ""


class TestMapOrderItem:
    def test_display_shape_and_math(self):
        item = map_order_item(_bvi_line_items()[0])
        assert item["item_id"] == "bvi-cuid_line_001"
        assert item["product_name"] == "BOSS 1234 Rectangle Acetate Eyeglasses"
        assert item["sku"] == "SP-BOSS-1234-086-55"
        assert item["quantity"] == 1
        assert item["unit_price"] == 6500.0
        assert item["item_total"] == 6500.0
        # product_id is the BVI CUID == imported catalog_products.id
        assert item["product_id"] == "cuid_product_001"

    def test_no_tax_fields_fabricated(self):
        """IMS never invoiced these lines: minting hsn/gst per line would
        fabricate a GST liability that was settled outside IMS books."""
        item = map_order_item(_bvi_line_items()[0])
        for forbidden in ("hsn_code", "gst_rate", "taxable_value", "tax_amount"):
            assert forbidden not in item, forbidden

    def test_line_discount_reduces_total_never_negative(self):
        row = {**_bvi_line_items()[0], "quantity": 2, "price": 100.0,
               "totalDiscount": 250.0}
        item = map_order_item(row)
        assert item["item_total"] == 0.0  # clamped, never negative


class TestMapOrder:
    def test_status_historical_never_counted(self):
        """THE core invariant: status='HISTORICAL' is (a) listed by the
        customer-360 orders endpoint (which applies NO status filter) and
        (b) excluded from every revenue aggregation: it is outside the
        inventory/crm _SOLD_STATUSES $in sets by construction, and inside
        every $nin excluded-status list (finance/ticker/reports/payout/
        collection_insights/oracle/order_repository)."""
        doc = map_order(_bvi_order(), _bvi_line_items())
        assert doc["status"] == "HISTORICAL"
        assert doc["historical"] is True
        assert doc["source"] == "bvi_import"
        assert doc["channel"] == "ONLINE"

    def test_historical_status_is_pinned_in_finance_exclusions(self):
        """Cross-check the other half of the mechanism: the finance central
        excluded-status list actually carries HISTORICAL (the ticker mirror is
        pinned in test_finance_aggregation_correctness)."""
        from api.routers.finance import _EXCLUDED_ORDER_STATUSES

        assert "HISTORICAL" in _EXCLUDED_ORDER_STATUSES

    def test_bvi_cancelled_order_stays_cancelled(self):
        doc = map_order(
            _bvi_order({"cancelledAt": datetime(2024, 7, 1), "orderStatus": "CANCELLED"}),
            _bvi_line_items(),
        )
        assert doc["status"] == "CANCELLED"

    def test_no_invoice_no_payments_no_balance(self):
        """Settled OUTSIDE IMS books: IMS minted no invoice, holds no
        receivable, and Tally export / AR / cash-flow can never pick it up."""
        doc = map_order(_bvi_order(), _bvi_line_items())
        assert doc["invoice_number"] is None
        assert doc["payments"] == []
        assert doc["balance_due"] == 0.0

    def test_idempotency_keys_present(self):
        """The runner upserts on bvi_order_id; order_id/order_number/item ids
        are all derived-stable so a re-run rewrites identical values."""
        doc = map_order(_bvi_order(), _bvi_line_items())
        assert doc["bvi_order_id"] == "cuid_order_001"
        assert doc["order_id"] == "bvi-cuid_order_001"
        assert doc["order_number"] == "BVI-1042"
        # Mapping twice yields the same identity fields (no fresh uuids).
        doc2 = map_order(_bvi_order(), _bvi_line_items())
        assert doc2["order_id"] == doc["order_id"]
        assert doc2["items"][0]["item_id"] == doc["items"][0]["item_id"]

    def test_shopify_order_id_bare_form_for_webhook_idempotency(self):
        """Stored in the SAME bare-numeric form shopify_ingest writes, so its
        Layer-1 guard (find_one on orders.shopify_order_id) treats a future
        webhook replay of this order as a duplicate -- no second order, no
        invoice, no stock decrement."""
        doc = map_order(
            _bvi_order({"shopifyOrderId": "gid://shopify/Order/5551112223334"}),
            _bvi_line_items(),
        )
        assert doc["shopify_order_id"] == "5551112223334"

    def test_blank_shopify_order_id_omitted(self):
        """uniq_shopify_order_id is UNIQUE+partial on $type string: '' would
        dup-collide across docs, so blanks are omitted entirely."""
        doc = map_order(_bvi_order({"shopifyOrderId": None}), _bvi_line_items())
        assert "shopify_order_id" not in doc

    def test_payment_and_fulfillment_status_mapping(self):
        doc = map_order(_bvi_order(), _bvi_line_items())
        assert doc["payment_status"] == "PAID"
        assert doc["fulfillment_status"] == "FULFILLED"
        assert doc["amount_paid"] == 6500.0
        # Raw BVI statuses preserved for audit.
        assert doc["bvi_financial_status"] == "paid"
        assert doc["bvi_order_status"] == "CLOSED"

    def test_blank_financial_status_defaults_paid(self):
        """Pre-IMS history is settled by definition (owner decision)."""
        doc = map_order(_bvi_order({"financialStatus": None}), _bvi_line_items())
        assert doc["payment_status"] == "PAID"

    def test_customer_linkage_and_phone_snapshot(self):
        """customer_id = the deduped IMS id the runner resolves; the phone
        snapshot is NORMALIZED so customer-360 (which ORs customer_id and
        customer_phone) matches either way."""
        cust = {"id": "cuid_cust_001", "firstName": "Asha", "lastName": "Rao",
                "email": "asha.rao@example.com", "phone": "+91 98765 43210"}
        doc = map_order(_bvi_order(), _bvi_line_items(),
                        ims_customer_id="bvi-cuid_cust_001", customer_row=cust)
        assert doc["customer_id"] == "bvi-cuid_cust_001"
        assert doc["customer_name"] == "Asha Rao"
        assert doc["customer_phone"] == "9876543210"

    def test_guest_order_keeps_null_customer_link(self):
        doc = map_order(_bvi_order(), _bvi_line_items())
        assert doc["customer_id"] is None

    def test_money_totals_from_bvi(self):
        doc = map_order(_bvi_order(), _bvi_line_items())
        assert doc["grand_total"] == 6500.0
        assert doc["subtotal"] == 6500.0
        assert doc["tax_amount"] == 309.52
        assert doc["currency"] == "INR"

    def test_original_order_time_preserved(self):
        """created_at = processedAt (true Shopify order time), which the
        runner moves into $setOnInsert so re-runs never shift it."""
        doc = map_order(_bvi_order(), _bvi_line_items())
        assert doc["created_at"] == datetime(2024, 6, 1, 9, 30, 0)

    def test_addresses_parsed_from_json_strings(self):
        doc = map_order(_bvi_order(), _bvi_line_items())
        assert doc["shipping_address"] == {"city": "Ranchi", "province": "Jharkhand"}
        assert doc["billing_address"] is None

    def test_no_stock_or_task_side_channel_fields(self):
        """Pure historical record: nothing for the stock decrement / task
        engine to key on."""
        doc = map_order(_bvi_order(), _bvi_line_items())
        for forbidden in ("fulfillment_breakdown", "fulfillment_stores",
                          "rx_pending", "fulfillment_hold"):
            assert forbidden not in doc, forbidden

    def test_null_id_maps_to_blank_key(self):
        doc = map_order({}, [])
        assert doc["bvi_order_id"] == ""


# ===========================================================================
# Registration + webhook status-sync guard
# ===========================================================================

class TestEntityRegistration:
    def test_customers_and_orders_registered(self):
        assert "customers" in _ALL_ENTITIES
        assert "orders" in _ALL_ENTITIES

    def test_customers_run_before_orders(self):
        """Orders link to the customer ids the customers leg deduped."""
        assert _ALL_ENTITIES.index("customers") < _ALL_ENTITIES.index("orders")


class _GuardColl:
    """Minimal orders collection: find_one returns the canned doc; update_one
    records that a write happened (the guard must prevent it)."""

    def __init__(self, doc):
        self._doc = doc
        self.updated = []

    def find_one(self, *_a, **_k):
        return self._doc

    def update_one(self, flt, update, **_k):
        self.updated.append((flt, update))


class _GuardDB:
    def __init__(self, coll):
        self._coll = coll

    def get_collection(self, _name):
        return self._coll


class TestHistoricalStatusSyncGuard:
    """A late Shopify orders/updated webhook for an imported HISTORICAL order
    must NOT flip its status (that would silently start counting it as IMS
    revenue). online_order_mapper._sync_existing_order_status skips them."""

    def test_bvi_import_order_is_never_status_synced(self):
        from api.services.online_order_mapper import _sync_existing_order_status

        coll = _GuardColl(
            {"order_id": "bvi-x", "shopify_order_id": "555",
             "status": "HISTORICAL", "historical": True, "source": "bvi_import"}
        )
        synced = _sync_existing_order_status(
            _GuardDB(coll), "555",
            {"financial_status": "paid", "fulfillment_status": "fulfilled"},
        )
        assert synced is False
        assert coll.updated == []

    def test_live_shopify_order_still_syncs(self):
        from api.services.online_order_mapper import _sync_existing_order_status

        coll = _GuardColl(
            {"order_id": "o-1", "shopify_order_id": "777",
             "status": "CONFIRMED", "source": "shopify", "grand_total": 100.0}
        )
        synced = _sync_existing_order_status(
            _GuardDB(coll), "777",
            {"financial_status": "paid", "fulfillment_status": "fulfilled"},
        )
        assert synced is True
        assert len(coll.updated) == 1
        assert coll.updated[0][1]["$set"]["status"] == "DELIVERED"
