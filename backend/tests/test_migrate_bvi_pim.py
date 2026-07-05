"""
Unit tests for scripts/migrate_bvi_pim.py -- the five PURE mapper functions.

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
"""
from __future__ import annotations

import sys
import os

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
    fold_variant_prices,
    _build_item_tree,
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
