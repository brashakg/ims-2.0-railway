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
_PRODUCT_CREATE = """
mutation imsProductCreate($input: ProductInput!) {
  productCreate(input: $input) {
    product {
      id
      handle
      variants(first: 100) {
        nodes { id title selectedOptions { name value } inventoryItem { id } }
      }
      media(first: 1) { nodes { id } }
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
      variants(first: 100) {
        nodes { id title selectedOptions { name value } inventoryItem { id } }
      }
      media(first: 1) { nodes { id } }
    }
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

