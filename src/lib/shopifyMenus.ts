// Shopify Menu (storefront navigation) helpers. The Menu / MenuItem
// pair lives outside the Product / Collection helpers in shopify.ts so
// pulling these in doesn't drag the full ~1100-line file into menu
// routes. Uses the same makeGraphQLRequest helper for token caching,
// throttle handling, and API version selection.

import { makeGraphQLRequest } from "@/lib/shopify";

// ─── Types ─────────────────────────────────────────────

export interface ShopifyMenuItem {
  id: string;
  title: string;
  type: string; // COLLECTION, PRODUCT, PAGE, HTTP, FRONTPAGE, etc.
  url: string | null;
  resourceId: string | null;
  tags: string[];
  items: ShopifyMenuItem[];
}

export interface ShopifyMenu {
  id: string;
  handle: string;
  title: string;
  isDefault: boolean;
  items: ShopifyMenuItem[];
}

// ─── FETCH ALL MENUS ───────────────────────────────────

/**
 * Fetch every storefront Menu (with full item tree) from Shopify.
 *
 * Shopify's Admin API returns nested menu items up to 5 levels deep
 * via the `items { items { items { ... } } }` recursive shape. Most
 * eyewear stores use 2-3 levels (top → category → brand) so 5 is
 * generous; if you need more, add another nested `items` block.
 */
export async function fetchAllMenus(): Promise<{
  success: boolean;
  menus?: ShopifyMenu[];
  error?: string;
}> {
  const query = `
    query FetchMenus($cursor: String) {
      menus(first: 50, after: $cursor) {
        edges {
          cursor
          node {
            id
            handle
            title
            isDefault
            items {
              id
              title
              type
              url
              resourceId
              tags
              items {
                id
                title
                type
                url
                resourceId
                tags
                items {
                  id
                  title
                  type
                  url
                  resourceId
                  tags
                  items {
                    id
                    title
                    type
                    url
                    resourceId
                    tags
                    items {
                      id
                      title
                      type
                      url
                      resourceId
                      tags
                    }
                  }
                }
              }
            }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  `;

  interface MenusResponse {
    menus: {
      edges: Array<{ cursor: string; node: ShopifyMenu }>;
      pageInfo: { hasNextPage: boolean; endCursor: string | null };
    };
  }

  const allMenus: ShopifyMenu[] = [];
  let cursor: string | null = null;
  let hasNextPage = true;

  while (hasNextPage) {
    const result: { success: boolean; data?: MenusResponse; error?: string } =
      await makeGraphQLRequest<MenusResponse>(query, { cursor });

    if (!result.success || !result.data) {
      return { success: false, error: result.error || "Failed to fetch menus" };
    }

    for (const edge of result.data.menus.edges) {
      allMenus.push(edge.node);
    }

    hasNextPage = result.data.menus.pageInfo.hasNextPage;
    cursor = result.data.menus.pageInfo.endCursor;
  }

  return { success: true, menus: allMenus };
}

// ─── PUSH MENU TO SHOPIFY ──────────────────────────────

export interface MenuItemTreeInput {
  id?: string | null; // existing shopifyItemId, if any
  title: string;
  type: string; // COLLECTION, COLLECTIONS, PRODUCT, PAGE, BLOG, ARTICLE, FRONTPAGE, CATALOG, SEARCH, HTTP, SHOP_POLICY, METAOBJECT
  url?: string | null;
  resourceId?: string | null;
  tags?: string[] | null;
  items?: MenuItemTreeInput[];
}

export interface PushMenuInput {
  shopifyMenuId: string; // gid://shopify/Menu/...
  title: string;
  handle: string;
  items: MenuItemTreeInput[];
}

/**
 * Push a complete menu tree to Shopify via `menuUpdate`.
 *
 * Shopify's Admin API treats menuUpdate as a wholesale replacement of
 * the menu's items array — every push must contain the full tree we
 * want the storefront to show. We translate our local MenuItem[] tree
 * into Shopify's MenuItemUpdateInput shape (no IDs for new items, the
 * existing shopifyItemId for ones we previously pushed).
 *
 * The mutation returns the canonical Shopify item IDs in tree order;
 * the caller is responsible for walking the response and writing the
 * IDs back to local MenuItem.shopifyItemId so the next push reuses
 * them instead of recreating items.
 */
export async function pushMenuToShopify(input: PushMenuInput): Promise<{
  success: boolean;
  message: string;
  items?: ShopifyMenuItem[]; // updated tree from Shopify, with new IDs
}> {
  const mutation = `
    mutation MenuUpdate($id: ID!, $title: String!, $handle: String!, $items: [MenuItemUpdateInput!]!) {
      menuUpdate(id: $id, title: $title, handle: $handle, items: $items) {
        menu {
          id
          handle
          title
          items {
            id
            title
            type
            url
            resourceId
            tags
            items {
              id
              title
              type
              url
              resourceId
              tags
              items {
                id
                title
                type
                url
                resourceId
                tags
                items {
                  id
                  title
                  type
                  url
                  resourceId
                  tags
                  items {
                    id
                    title
                    type
                    url
                    resourceId
                    tags
                  }
                }
              }
            }
          }
        }
        userErrors { field message code }
      }
    }
  `;

  // Shopify expects MenuItemUpdateInput without `id` for new items.
  // For items that already exist (we previously pushed), passing `id`
  // tells Shopify to keep the same MenuItem rather than recreating it.
  const toShopifyInput = (
    items: MenuItemTreeInput[]
  ): Array<Record<string, unknown>> => {
    return items.map((item) => {
      const out: Record<string, unknown> = {
        title: item.title,
        type: item.type,
      };
      if (item.id) out.id = item.id;
      if (item.url) out.url = item.url;
      if (item.resourceId) out.resourceId = item.resourceId;
      if (item.tags && item.tags.length > 0) out.tags = item.tags;
      if (item.items && item.items.length > 0) {
        out.items = toShopifyInput(item.items);
      }
      return out;
    });
  };

  const variables = {
    id: input.shopifyMenuId,
    title: input.title,
    handle: input.handle,
    items: toShopifyInput(input.items),
  };

  const result = await makeGraphQLRequest<{
    menuUpdate: {
      menu: ShopifyMenu | null;
      userErrors: Array<{ field: string; message: string; code: string }>;
    };
  }>(mutation, variables);

  if (!result.success) {
    return { success: false, message: result.error || "Failed to push menu" };
  }

  const errors = result.data?.menuUpdate.userErrors || [];
  if (errors.length > 0) {
    return {
      success: false,
      message: errors
        .map((e) => `${e.field || "_"}: ${e.message}`)
        .join("; "),
    };
  }

  return {
    success: true,
    message: "Menu pushed to Shopify",
    items: result.data?.menuUpdate.menu?.items || [],
  };
}

// ─── BRAND ALPHABET BUCKET (Round 2 mapping M2) ────────

/**
 * Per round 2 mapping M2: when a new brand is auto-added to the Brands
 * mega menu, route it into the right alphabet bucket. Better Vision
 * uses three buckets to keep the mega menu wide (A-H, I-Q, R-Z); the
 * spec calls them "A-H" / "M-Q" / "R-Z" but in practice anything M-Q
 * also covers I-L since there are no I/J/K/L brands in the catalog
 * yet. We keep the M2-named buckets but map I/J/K/L → "M-Q" so the
 * code never returns an unknown bucket.
 *
 * Returns one of three constant strings so the menu editor can do a
 * straight-up handle/title lookup against the existing bucket items.
 */
export type BrandBucket = "A-H" | "M-Q" | "R-Z";

export function bucketBrandIntoAlphabet(brandTitle: string): BrandBucket {
  const trimmed = (brandTitle || "").trim();
  if (!trimmed) return "M-Q"; // null-safe default; matches mid-bucket

  const ch = trimmed[0].toUpperCase();
  if (ch >= "A" && ch <= "H") return "A-H";
  if (ch >= "R" && ch <= "Z") return "R-Z";
  // I, J, K, L, M, N, O, P, Q all fall here (and any non-letter chars).
  return "M-Q";
}

// ─── DELETE MENU ───────────────────────────────────────

/**
 * Delete a Shopify menu by GID. Used by the dashboard delete action
 * for non-default menus. Default menus (main-menu, footer) cannot be
 * deleted via the API; the local DB also blocks DELETE for isDefault
 * rows so this is mostly defensive.
 */
export async function deleteShopifyMenu(
  shopifyMenuId: string
): Promise<{ success: boolean; message: string }> {
  const mutation = `
    mutation MenuDelete($id: ID!) {
      menuDelete(id: $id) {
        deletedMenuId
        userErrors { field message code }
      }
    }
  `;

  const result = await makeGraphQLRequest<{
    menuDelete: {
      deletedMenuId: string | null;
      userErrors: Array<{ field: string; message: string; code: string }>;
    };
  }>(mutation, { id: shopifyMenuId });

  if (!result.success) {
    return { success: false, message: result.error || "Failed to delete menu" };
  }
  const errors = result.data?.menuDelete.userErrors || [];
  if (errors.length > 0) {
    return {
      success: false,
      message: errors.map((e) => `${e.field || "_"}: ${e.message}`).join("; "),
    };
  }
  return { success: true, message: "Menu deleted from Shopify" };
}

// ─── CREATE MENU ───────────────────────────────────────

/**
 * Create a new Shopify menu with no items. We let the caller push
 * items afterwards via pushMenuToShopify so creation and structure
 * editing share the same code path.
 */
export async function createShopifyMenu(input: {
  title: string;
  handle: string;
}): Promise<{ success: boolean; message: string; shopifyMenuId?: string }> {
  const mutation = `
    mutation MenuCreate($title: String!, $handle: String!, $items: [MenuItemCreateInput!]!) {
      menuCreate(title: $title, handle: $handle, items: $items) {
        menu { id handle title }
        userErrors { field message code }
      }
    }
  `;

  const result = await makeGraphQLRequest<{
    menuCreate: {
      menu: { id: string; handle: string; title: string } | null;
      userErrors: Array<{ field: string; message: string; code: string }>;
    };
  }>(mutation, { title: input.title, handle: input.handle, items: [] });

  if (!result.success) {
    return { success: false, message: result.error || "Failed to create menu" };
  }
  const errors = result.data?.menuCreate.userErrors || [];
  if (errors.length > 0) {
    return {
      success: false,
      message: errors.map((e) => `${e.field || "_"}: ${e.message}`).join("; "),
    };
  }
  const created = result.data?.menuCreate.menu;
  if (!created?.id) {
    return { success: false, message: "Menu created but no ID returned" };
  }
  return {
    success: true,
    message: "Menu created on Shopify",
    shopifyMenuId: created.id,
  };
}
