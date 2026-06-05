"""
Unit tests for scripts/migrate_bvi_pim.py -- the four PURE mapper functions.

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
