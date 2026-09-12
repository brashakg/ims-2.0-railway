"""Shopify push -- queries

The GraphQL operation documents (the Phase-5 push set;
BVI_MERGE_PLAN.md A.3), the per-call caps, the Online Store publication
name and the per-process publication-id cache shared by the gate reader
and the publish resolver.
"""

from __future__ import annotations

from typing import Dict

# ===========================================================================
# GraphQL operations (the Phase-5 push set; BVI_MERGE_PLAN.md A.3)
# ===========================================================================
# Pinned, minimal mutations. We keep them small + explicit so a Shopify default
# bump can't silently change the contract. Each create returns the new gid which
# we write back for idempotency; each update is selected when a gid already exists.

# NOTE (API 2024-10 product model): ProductInput carries NO price and NO sku --
# both live on the VARIANT since 2024-04. productCreate therefore auto-creates a
# single variant (Shopify: "Only one product variant is created and linked with
# the first option value specified for each option name") at price 0.00 with no
# SKU. We select that variant back so the seeding step can price + SKU it; the
# extra selection is read-only and changes nothing about what is written.
#
# inventoryItem { id } rides on the same selection (oversell-guard publish
# precondition): the InventoryItem gid is what the stock write-back resolver
# needs (catalog_variants.shopify_inventory_item_id) to sync the listed
# quantity down after an in-store sale. Read-only; no extra network call.
# media(first: 250) -- EVERY media id (+ the CDN url of an image) rides on
# the create/update response so the photo pass (media.sync_product_media)
# can diff IMS's photo list against what is on Shopify with NO extra query.
# 250 is Shopify's per-product media ceiling, so the page is always complete.
_PRODUCT_CREATE = """
mutation imsProductCreate($input: ProductInput!) {
  productCreate(input: $input) {
    product {
      id
      handle
      tags
      variants(first: 100) {
        nodes { id title selectedOptions { name value } inventoryItem { id } }
      }
      media(first: 250) { nodes { id ... on MediaImage { image { url } } } }
    }
    userErrors { field message }
  }
}
"""

# The UPDATE mutation ALSO selects the variants back, but the seeding step is
# only reached on an update when SHOPIFY_PUSH_PRICE_ON_UPDATE is on (default
# OFF) -- so by default an update is byte-identical to before apart from this
# read-only selection.
_PRODUCT_UPDATE = """
mutation imsProductUpdate($input: ProductInput!) {
  productUpdate(input: $input) {
    product {
      id
      handle
      tags
      variants(first: 100) {
        nodes { id title selectedOptions { name value } inventoryItem { id } }
      }
      media(first: 250) { nodes { id ... on MediaImage { image { url } } } }
    }
    userErrors { field message }
  }
}
"""

# TAG OWNERSHIP (sync audit gap #4, owner 2026-09-06): an existing product's
# tags are never sent through productUpdate (which REPLACES the whole list and
# wiped hand-added admin tags). The diff of what IMS wants against what IMS
# last sent goes through these two -- both idempotent, both `userErrors`.
# `tags` rides the create/update response selection above (read-only) so the
# pass can see what is on Shopify with no extra query.
_TAGS_ADD = """
mutation imsTagsAdd($id: ID!, $tags: [String!]!) {
  tagsAdd(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""

_TAGS_REMOVE = """
mutation imsTagsRemove($id: ID!, $tags: [String!]!) {
  tagsRemove(id: $id, tags: $tags) {
    node { id }
    userErrors { field message }
  }
}
"""

_COLLECTION_CREATE = """
mutation imsCollectionCreate($input: CollectionInput!) {
  collectionCreate(input: $input) {
    collection { id handle }
    userErrors { field message }
  }
}
"""

_COLLECTION_UPDATE = """
mutation imsCollectionUpdate($input: CollectionInput!) {
  collectionUpdate(input: $input) {
    collection { id handle }
    userErrors { field message }
  }
}
"""

# CUSTOM-collection MANUAL membership push (parity with BVI's
# ecommerce/src/lib/shopify.ts addProductsToCollection). CollectionInput does
# NOT carry a manual product list, so a CUSTOM collection's members are attached
# in a SEPARATE step after the collection upsert. Idempotent: re-adding an
# existing member is a no-op on Shopify. SMART collections never use this (their
# membership is derived by Shopify from the ruleSet).
_COLLECTION_ADD_PRODUCTS = """
mutation imsCollectionAddProducts($id: ID!, $productIds: [ID!]!) {
  collectionAddProducts(id: $id, productIds: $productIds) {
    collection { id }
    userErrors { field message }
  }
}
"""
# Shopify accepts many ids per call; chunk to stay well within limits.
_COLLECTION_PRODUCTS_PER_CALL = 250

_MENU_CREATE = """
mutation imsMenuCreate($title: String!, $handle: String!, $items: [MenuItemCreateInput!]!) {
  menuCreate(title: $title, handle: $handle, items: $items) {
    menu { id handle }
    userErrors { field message }
  }
}
"""

_MENU_UPDATE = """
mutation imsMenuUpdate($id: ID!, $title: String!, $handle: String!, $items: [MenuItemUpdateInput!]!) {
  menuUpdate(id: $id, title: $title, handle: $handle, items: $items) {
    menu { id handle }
    userErrors { field message }
  }
}
"""

_PRODUCT_CREATE_MEDIA = """
mutation imsProductCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
  productCreateMedia(productId: $productId, media: $media) {
    media { ... on MediaImage { id status mediaErrors { code details message } } }
    mediaUserErrors { field message }
  }
}
"""

# The photo pass (sync audit gap #3, owner 2026-09-06: "replacing or removing a
# photo updates Shopify"). Delete takes the media ids IMS attached and no
# longer wants; reorder moves the IMS-owned media to IMS's display order.
# Both use `mediaUserErrors` (like productCreateMedia), not `userErrors`.
_PRODUCT_DELETE_MEDIA = """
mutation imsProductDeleteMedia($productId: ID!, $mediaIds: [ID!]!) {
  productDeleteMedia(productId: $productId, mediaIds: $mediaIds) {
    deletedMediaIds
    mediaUserErrors { field message code }
  }
}
"""

_PRODUCT_REORDER_MEDIA = """
mutation imsProductReorderMedia($id: ID!, $moves: [MoveInput!]!) {
  productReorderMedia(id: $id, moves: $moves) {
    job { id }
    mediaUserErrors { field message code }
  }
}
"""
# READ-ONLY: the media a live product carries today, for the adoption
# runbook (scripts/adopt_shopify_media_map.py) that claims pre-media_map media
# as IMS-owned by a positive identity match (media.match_media_to_photos).
# originalSource is the url Shopify was handed at attach; image.url the CDN
# copy (its file name is the only trace of the source name). alt is NOT read:
# IMS attaches every photo with alt '', so an alt can never identify one.
_PRODUCT_MEDIA_QUERY = """
query imsProductMedia($id: ID!) {
  product(id: $id) {
    id
    media(first: 250) {
      nodes { id ... on MediaImage { image { url } originalSource { url } } }
    }
  }
}
"""
# Shopify refuses the 251st media on a product ("Limit of 250 media per product
# reached" -- a July re-press hit it). The pass refuses BEFORE the call, with a
# code the sweep can show, instead of piling up to the wall.
_MEDIA_LIMIT = 250

# Online STOCK (owner ruling 2026-09-07, sync-audit gap #1 "make website
# quantities real"; per-store locations, owner ruling 2026-09-06). Four
# documents:
#   * the locations list -- the Organization page's dropdown read (the ONLY
#     locations read; the mapping lives on the store record);
#   * the variant inventory update -- tracked=true + inventoryPolicy on every
#     variant the product owns (an UNTRACKED item sells without limit, which is
#     exactly what the six live products were doing);
#   * inventorySetQuantities -- the ABSOLUTE available quantity per
#     (inventory item, location) row: one row per mapped shop (idempotent on
#     retry; needs write_inventory). `code` rides userErrors so the writer can
#     key on ITEM_NOT_STOCKED_AT_LOCATION and activate;
#   * inventoryBulkToggleActivation -- stock an item at the locations a chunk
#     just failed at, then the chunk is retried ONCE (write_inventory).
# first: 50 -- every location the shop has (one per physical shop), so the
# read never truncates.
_LOCATIONS_LIST_QUERY = """
query imsLocationList {
  locations(first: 50) {
    nodes { id name isActive fulfillsOnlineOrders shipsInventory address { city province } }
  }
}
"""

_VARIANTS_INVENTORY_UPDATE = """
mutation imsVariantInventoryUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id inventoryPolicy inventoryItem { id tracked } }
    userErrors { field message }
  }
}
"""

_INVENTORY_SET_QUANTITIES = """
mutation imsInventorySetQuantities($input: InventorySetQuantitiesInput!) {
  inventorySetQuantities(input: $input) {
    inventoryAdjustmentGroup { createdAt reason }
    userErrors { field message code }
  }
}
"""
# Shopify caps one inventorySetQuantities call at 250 quantity entries.
_INVENTORY_SET_MAX = 250

_INVENTORY_ACTIVATE = """
mutation imsInventoryActivate($inventoryItemId: ID!, $inventoryItemUpdates: [InventoryBulkToggleActivationInput!]!) {
  inventoryBulkToggleActivation(inventoryItemId: $inventoryItemId, inventoryItemUpdates: $inventoryItemUpdates) {
    inventoryItem { id }
    userErrors { field message code }
  }
}
"""

# Variant price/barcode push (owner priority: "change MRP in IMS -> website
# updates"). Shopify retired productVariantUpdate; the current path is
# productVariantsBulkUpdate keyed on the PARENT product gid (mirrors BVI's
# ecommerce/src/lib/shopify.ts updateVariantPrice). `barcode` is a top-level
# ProductVariantsBulkInput field in our pinned API version.
_VARIANTS_BULK_UPDATE = """
mutation imsVariantPricesUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id price compareAtPrice barcode }
    userErrors { field message }
  }
}
"""

# Shopify caps productVariantsBulkUpdate at 250 variants per call (eyewear
# products carry a handful, but the cap keeps a pathological doc safe).
_VARIANTS_PER_CALL = 250

# CREATE-side companion: productCreate only ever materialises ONE variant, so
# any REMAINING IMS variant (a second colour / size) has to be created. Same
# ProductVariantsBulkInput shape, plus optionValues to place it on the option
# grid. Returns the new gids (and each variant's inventoryItem gid -- the
# oversell-guard stock target) so they can be written back for idempotency.
_VARIANTS_BULK_CREATE = """
mutation imsVariantsBulkCreate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkCreate(productId: $productId, variants: $variants) {
    productVariants { id title selectedOptions { name value } inventoryItem { id } }
    userErrors { field message }
  }
}
"""

# Sales-channel publish. A product that
# is ACTIVE but published to NO channel is invisible on the storefront; this is
# the step that puts it on the Online Store. Only `userErrors` is selected so
# the operation stays valid across Admin API versions.
_PUBLISHABLE_PUBLISH = """
mutation imsPublishablePublish($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    userErrors { field message }
  }
}
"""

# Publication lookup (only when SHOPIFY_ONLINE_STORE_PUBLICATION_ID is not set).
# Needs the read_publications scope; fail-soft when the app lacks it.
_PUBLICATIONS_QUERY = """
query imsPublications {
  publications(first: 25) { nodes { id name } }
}
"""

# The Shopify sales channel whose publication we target on create.
_ONLINE_STORE_PUBLICATION_NAME = "Online Store"
# Resolved once per process (a publication id is stable for the shop).
_publication_id_cache: Dict[str, str] = {}

