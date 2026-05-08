const rawStoreUrl = process.env.SHOPIFY_STORE_URL || "";
const SHOPIFY_STORE_URL = rawStoreUrl.startsWith("http") ? rawStoreUrl : `https://${rawStoreUrl}`;
const SHOPIFY_CLIENT_ID = process.env.SHOPIFY_CLIENT_ID || "";
const SHOPIFY_CLIENT_SECRET = process.env.SHOPIFY_CLIENT_SECRET || "";
const SHOPIFY_LEGACY_TOKEN = process.env.SHOPIFY_ACCESS_TOKEN || process.env.SHOPIFY_ADMIN_TOKEN || "";

let cachedToken: { token: string; expiresAt: number } | null = null;

// Force-clear the in-memory OAuth token cache. Call after changing app
// scopes in Shopify so the next request mints a fresh token without a
// server restart.
export function clearCachedShopifyToken(): void {
  cachedToken = null;
}

async function getAccessToken(): Promise<string> {
  if (SHOPIFY_CLIENT_ID && SHOPIFY_CLIENT_SECRET) {
    if (cachedToken && Date.now() < cachedToken.expiresAt - 300000) {
      return cachedToken.token;
    }
    const response = await fetch(
      `${SHOPIFY_STORE_URL}/admin/oauth/access_token`,
      {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: SHOPIFY_CLIENT_ID,
          client_secret: SHOPIFY_CLIENT_SECRET,
          grant_type: "client_credentials",
        }).toString(),
      }
    );
    if (!response.ok) {
      const errorBody = await response.text();
      console.error(`OAuth token request failed: HTTP ${response.status}`, errorBody);
      throw new Error(`OAuth token request failed: HTTP ${response.status} - ${errorBody}`);
    }
    const data = await response.json();
    cachedToken = {
      token: data.access_token,
      expiresAt: Date.now() + (data.expires_in || 86399) * 1000,
    };
    return cachedToken.token;
  }
  if (SHOPIFY_LEGACY_TOKEN) {
    return SHOPIFY_LEGACY_TOKEN;
  }
  throw new Error("No Shopify credentials configured.");
}

// ─── GraphQL helpers ───────────────────────────────────

interface GraphQLError {
  message: string;
  locations?: Array<{ line: number; column: number }>;
  path?: string[];
}
interface GraphQLThrottleStatus {
  maximumAvailable: number;
  currentlyAvailable: number;
  restoreRate: number;
}
interface GraphQLResponse<T> {
  data?: T;
  errors?: Array<GraphQLError & { extensions?: { code?: string } }>;
  extensions?: {
    cost?: {
      requestedQueryCost?: number;
      actualQueryCost?: number | null;
      throttleStatus?: GraphQLThrottleStatus;
    };
  };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Sleep long enough for Shopify's cost bucket to refill to `needed` points.
// `restoreRate` is points per second, so we sleep (needed - currentlyAvailable) / restoreRate.
function msUntilAvailable(
  throttle: GraphQLThrottleStatus,
  needed: number
): number {
  if (throttle.currentlyAvailable >= needed) return 0;
  const deficit = needed - throttle.currentlyAvailable;
  const seconds = deficit / Math.max(throttle.restoreRate, 1);
  // Add a 500ms buffer to be safe, cap at 30s.
  return Math.min(Math.ceil(seconds * 1000) + 500, 30_000);
}

export async function makeGraphQLRequest<T>(
  query: string,
  variables?: Record<string, unknown>
): Promise<{ success: boolean; data?: T; error?: string }> {
  const maxAttempts = 4;
  let lastError = "Unknown error";

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const accessToken = await getAccessToken();
      const response = await fetch(
        `${SHOPIFY_STORE_URL}/admin/api/${process.env.SHOPIFY_API_VERSION || "2026-04"}/graphql.json`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": accessToken,
          },
          body: JSON.stringify({ query, variables: variables || {} }),
        }
      );

      // 429 Too Many Requests: honor Retry-After header or back off.
      if (response.status === 429) {
        const retryAfter = Number(response.headers.get("Retry-After")) || 2;
        await sleep(retryAfter * 1000);
        lastError = `HTTP 429 (retry-after ${retryAfter}s)`;
        continue;
      }
      if (!response.ok) {
        return {
          success: false,
          error: `HTTP ${response.status}: ${response.statusText}`,
        };
      }

      const result: GraphQLResponse<T> = await response.json();
      const throttle = result.extensions?.cost?.throttleStatus;
      const throttled = (result.errors || []).some(
        (e) => e.extensions?.code === "THROTTLED" || /throttl/i.test(e.message)
      );

      if (throttled && throttle && attempt < maxAttempts) {
        const requested = result.extensions?.cost?.requestedQueryCost || 100;
        await sleep(msUntilAvailable(throttle, requested));
        lastError = "throttled, retrying after bucket refill";
        continue;
      }

      if (result.errors && result.errors.length > 0) {
        return {
          success: false,
          error: result.errors.map((e) => e.message).join("; "),
        };
      }

      // Proactively pause after successful calls when the bucket is low —
      // the next call in a tight loop (e.g. pagination) would otherwise
      // throttle. Only waits if less than 20% of max remains.
      if (
        throttle &&
        throttle.currentlyAvailable < throttle.maximumAvailable * 0.2
      ) {
        await sleep(
          msUntilAvailable(throttle, throttle.maximumAvailable * 0.5)
        );
      }

      return { success: true, data: result.data };
    } catch (error) {
      lastError = error instanceof Error ? error.message : "Unknown error";
      if (attempt < maxAttempts) {
        await sleep(1000 * attempt);
        continue;
      }
    }
  }

  return { success: false, error: lastError };
}

// ─── Types ─────────────────────────────────────────────

export interface ShopifyVariantInput {
  optionValues: Array<{ optionName: string; name: string }>;
  price: string;
  compareAtPrice?: string;
  sku?: string;
  barcode?: string;
}

export interface CreateProductInput {
  title: string;
  description?: string;
  images?: Array<{ src: string; alt?: string }>;
  variants?: ShopifyVariantInput[];
  productOptions?: Array<{ name: string; values: Array<{ name: string }> }>;
  seoTitle?: string;
  seoDescription?: string;
  tags?: string[];
  productType?: string;
  status?: "ACTIVE" | "DRAFT" | "ARCHIVED";
  /** Shopify "Vendor" field — should be the BRAND, not the store name.
   *  Audit 2026-05: every existing product had vendor="Better Vision",
   *  which broke Shopify's vendor filters and brand-collection rules. */
  vendor?: string;
  /** Shopify standard product taxonomy GID (e.g.
   *  "gid://shopify/TaxonomyCategory/aa-1-13-7" for Sunglasses).
   *  Used by Google Shopping, Shop App, and Marketplaces for SEO and
   *  filtering. Use shopifyTaxonomyGidFor() in shopifySeo.ts to look up
   *  the right GID for a category. */
  productCategory?: string;
  /** Theme template suffix — picks the storefront layout
   *  (round 2 mapping U6: pre-order, appointment, rx-required). */
  templateSuffix?: string | null;
}

export interface CreateProductResult {
  success: boolean;
  shopifyId?: string;
  variantIds?: Array<{ sku?: string; shopifyVariantId: string; title?: string; inventoryItemId?: string }>;
  message: string;
}

// ─── CREATE PRODUCT (with variants) ────────────────────

export async function createProduct(
  productData: CreateProductInput
): Promise<CreateProductResult> {
  const mutation = `
    mutation CreateProduct($input: ProductInput!, $media: [CreateMediaInput!]) {
      productCreate(input: $input, media: $media) {
        product {
          id
          handle
          title
          variants(first: 50) {
            edges {
              node {
                id
                sku
                title
                price
                inventoryItem {
                  id
                }
              }
            }
          }
        }
        userErrors {
          field
          message
        }
      }
    }
  `;

  const input: Record<string, unknown> = {
    title: productData.title,
    descriptionHtml: productData.description || "",
    // Drop empty tags before send. tagsForProductAttributes already
    // skips empty values, but every push goes through ", "-joined string
    // round-trips that can introduce stray "" entries.
    tags: (productData.tags || []).map((t) => t.trim()).filter(Boolean),
    status: productData.status || "ACTIVE",
    productType: productData.productType || "",
    // vendor MUST be the brand. If caller doesn't supply one, Shopify
    // defaults to the store name which is what produced the
    // "vendor=Better Vision on every product" mess in 4,400+ historical rows.
    ...(productData.vendor ? { vendor: productData.vendor } : {}),
    // Shopify standard product taxonomy. The ProductInput field is just
    // `category` (an ID), NOT `productCategory: { productTaxonomyNodeId }`.
    // Verified against the live schema 2026-05; the older nested shape
    // never worked — Shopify silently dropped it.
    ...(productData.productCategory
      ? { category: productData.productCategory }
      : {}),
    seo: {
      title: productData.seoTitle || productData.title,
      description: productData.seoDescription || "",
    },
    // Round 2 mapping U6 / 9.6 — theme template suffix maps to Shopify's
    // ProductInput.templateSuffix and selects which storefront template
    // renders this product (pre-order, price-on-appointment, rx-required).
    ...(productData.templateSuffix
      ? { templateSuffix: productData.templateSuffix }
      : {}),
  };

  // Add product options (Color, Size). productCreate accepts options
  // inline so the bulk-variants call below can reference their names.
  if (productData.productOptions && productData.productOptions.length > 0) {
    input.productOptions = productData.productOptions;
  }

  // NOTE: ProductInput does NOT accept `variants` anymore. The current
  // Shopify Admin API (2025-01+) requires variants be added via a
  // separate productVariantsBulkCreate mutation AFTER productCreate.
  // Sending input.variants returns "Variable $input of type ProductInput!
  // was provided invalid value for variants (Field is not defined on
  // ProductInput)". We do a two-step here.

  const media = (productData.images || []).map((img) => ({
    originalSource: img.src,
    alt: img.alt || "",
    mediaContentType: "IMAGE",
  }));

  const result = await makeGraphQLRequest<{
    productCreate: {
      product: {
        id: string;
        handle: string;
        title: string;
        // variants returned here is the auto-created "Default Title"
        // variant Shopify mints when no productOptions are supplied.
        // We discard it via strategy:REMOVE_STANDALONE_VARIANT during
        // the bulk-create call below.
        variants: {
          edges: Array<{
            node: {
              id: string;
              sku: string;
              title: string;
              price: string;
              inventoryItem: { id: string };
            };
          }>;
        };
      } | null;
      userErrors: Array<{ field: string; message: string }>;
    };
  }>(mutation, { input, media: media.length > 0 ? media : undefined });

  if (!result.success) {
    return { success: false, message: result.error || "Failed to create product" };
  }

  const userErrors = result.data?.productCreate.userErrors || [];
  if (userErrors.length > 0) {
    return {
      success: false,
      message: userErrors.map((e) => `${e.field}: ${e.message}`).join("; "),
    };
  }

  const product = result.data?.productCreate.product;
  if (!product?.id) {
    return { success: false, message: "Product created but no ID returned" };
  }

  // Step 2: bulk-create variants. If none were requested we keep the
  // auto-created Default Title variant — that's the right behaviour for
  // single-SKU products (accessories, contact lens boxes).
  let variantIds: CreateProductResult["variantIds"] = [];

  if (productData.variants && productData.variants.length > 0) {
    const bulkMutation = `
      mutation BulkCreateVariants(
        $productId: ID!,
        $variants: [ProductVariantsBulkInput!]!
      ) {
        productVariantsBulkCreate(
          productId: $productId,
          variants: $variants,
          strategy: REMOVE_STANDALONE_VARIANT
        ) {
          productVariants {
            id
            sku
            title
            price
            inventoryItem { id }
          }
          userErrors { field message }
        }
      }
    `;

    // Translate our internal ShopifyVariantInput shape into the bulk
    // input shape. SKU lives under inventoryItem.sku in the new API,
    // not at the top level of the variant.
    const bulkVariants = productData.variants.map((v) => ({
      optionValues: v.optionValues,
      price: v.price,
      ...(v.compareAtPrice ? { compareAtPrice: v.compareAtPrice } : {}),
      ...(v.barcode ? { barcode: v.barcode } : {}),
      ...(v.sku
        ? { inventoryItem: { sku: v.sku, tracked: true } }
        : { inventoryItem: { tracked: true } }),
    }));

    const bulkResult = await makeGraphQLRequest<{
      productVariantsBulkCreate: {
        productVariants: Array<{
          id: string;
          sku: string | null;
          title: string;
          price: string;
          inventoryItem: { id: string };
        }> | null;
        userErrors: Array<{ field: string; message: string }>;
      };
    }>(bulkMutation, { productId: product.id, variants: bulkVariants });

    if (!bulkResult.success) {
      return {
        success: false,
        shopifyId: product.id, // product itself succeeded — surface ID
        message: `Product created but variants failed: ${bulkResult.error}`,
      };
    }
    const bulkErrors =
      bulkResult.data?.productVariantsBulkCreate.userErrors || [];
    if (bulkErrors.length > 0) {
      return {
        success: false,
        shopifyId: product.id,
        message: `Variants rejected: ${bulkErrors.map((e) => `${e.field}: ${e.message}`).join("; ")}`,
      };
    }

    variantIds = (
      bulkResult.data?.productVariantsBulkCreate.productVariants || []
    ).map((pv) => ({
      sku: pv.sku || undefined,
      shopifyVariantId: pv.id,
      title: pv.title,
      inventoryItemId: pv.inventoryItem?.id,
    }));
  } else {
    // No variants requested — return whatever Default Title variant
    // Shopify auto-created so the caller can still update price /
    // inventory on it later.
    variantIds = (product.variants?.edges || []).map((edge) => ({
      sku: edge.node.sku || undefined,
      shopifyVariantId: edge.node.id,
      title: edge.node.title,
      inventoryItemId: edge.node.inventoryItem?.id,
    }));
  }

  return {
    success: true,
    shopifyId: product.id,
    variantIds,
    message: "Product created successfully",
  };
}

// ─── UPDATE PRODUCT ────────────────────────────────────

export interface UpdateProductInput {
  title?: string;
  description?: string;
  seoTitle?: string;
  seoDescription?: string;
  tags?: string[];
  productType?: string;
  vendor?: string;
  /** Shopify standard taxonomy GID (e.g. gid://shopify/TaxonomyCategory/aa-2-27 for Sunglasses). */
  productCategory?: string;
  /** Theme template suffix (round 2 U6). */
  templateSuffix?: string | null;
}

export async function updateProduct(
  shopifyId: string,
  productData: UpdateProductInput & { status?: "ACTIVE" | "DRAFT" | "ARCHIVED" }
): Promise<{ success: boolean; message: string }> {
  const mutation = `
    mutation UpdateProduct($input: ProductInput!) {
      productUpdate(input: $input) {
        product { id title }
        userErrors { field message }
      }
    }
  `;

  const input: Record<string, unknown> = { id: shopifyId };
  if (productData.templateSuffix !== undefined) {
    input.templateSuffix = productData.templateSuffix || null;
  }
  if (productData.title) input.title = productData.title;
  if (productData.description) input.descriptionHtml = productData.description;
  if (productData.tags) {
    input.tags = productData.tags.map((t) => t.trim()).filter(Boolean);
  }
  if (productData.productType !== undefined) input.productType = productData.productType;
  if (productData.vendor) input.vendor = productData.vendor;
  if (productData.productCategory) input.category = productData.productCategory;
  if (productData.status) input.status = productData.status;

  const seo: Record<string, string> = {};
  if (productData.seoTitle) seo.title = productData.seoTitle;
  if (productData.seoDescription) seo.description = productData.seoDescription;
  if (Object.keys(seo).length > 0) input.seo = seo;

  const result = await makeGraphQLRequest<{
    productUpdate: {
      product: { id: string; title: string } | null;
      userErrors: Array<{ field: string; message: string }>;
    };
  }>(mutation, { input });

  if (!result.success) {
    return { success: false, message: result.error || "Failed to update product" };
  }
  const errors = result.data?.productUpdate.userErrors || [];
  if (errors.length > 0) {
    return { success: false, message: errors.map((e) => `${e.field}: ${e.message}`).join("; ") };
  }
  return { success: true, message: "Product updated successfully" };
}

// Attach images (media) to an existing Shopify product. Used by the
// three-role design workflow: cataloger creates the product as DRAFT
// without images; designer later uploads edited images and calls this.
export async function attachMediaToProduct(
  shopifyProductId: string,
  images: Array<{ src: string; alt?: string }>
): Promise<{ success: boolean; message: string; mediaIds?: string[] }> {
  if (images.length === 0) {
    return { success: true, message: "No images to attach" };
  }
  const mutation = `
    mutation ProductCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
      productCreateMedia(productId: $productId, media: $media) {
        media { id alt mediaContentType status }
        mediaUserErrors { field message code }
      }
    }
  `;
  const media = images.map((img) => ({
    originalSource: img.src,
    alt: img.alt || "",
    mediaContentType: "IMAGE",
  }));
  const result = await makeGraphQLRequest<{
    productCreateMedia: {
      media: Array<{ id: string; alt: string | null }>;
      mediaUserErrors: Array<{ field: string; message: string; code: string }>;
    };
  }>(mutation, { productId: shopifyProductId, media });
  if (!result.success) {
    return {
      success: false,
      message: result.error || "Failed to attach media",
    };
  }
  const errs = result.data?.productCreateMedia.mediaUserErrors || [];
  if (errs.length > 0) {
    return {
      success: false,
      message: errs.map((e) => `${e.field}: ${e.message}`).join("; "),
    };
  }
  return {
    success: true,
    message: `Attached ${result.data?.productCreateMedia.media.length || 0} image(s)`,
    mediaIds: result.data?.productCreateMedia.media.map((m) => m.id),
  };
}

// ─── UPDATE VARIANT PRICE ──────────────────────────────
// Note: Shopify removed `productVariantUpdate` and `ProductVariantInput`
// from the Admin API. The current path is `productVariantsBulkUpdate`
// which requires the parent product's GID alongside the variant data —
// see the schema audit notes in CLAUDE.md.
//
// `createVariant` (single-variant wrapper around productVariantsBulkCreate)
// was also removed: it's unused, and its old signature accepted SKU at
// the top level of the variant which the new bulk shape doesn't allow
// (SKU lives under inventoryItem.sku now). If you ever need to add a
// single variant, call productVariantsBulkCreate directly with one
// element in the array.

export async function updateVariantPrice(
  shopifyProductId: string,
  shopifyVariantId: string,
  price: string,
  compareAtPrice?: string
): Promise<{ success: boolean; message: string }> {
  const mutation = `
    mutation UpdateVariantPrice(
      $productId: ID!,
      $variants: [ProductVariantsBulkInput!]!
    ) {
      productVariantsBulkUpdate(productId: $productId, variants: $variants) {
        productVariants { id price compareAtPrice }
        userErrors { field message }
      }
    }
  `;

  const variant: Record<string, unknown> = {
    id: shopifyVariantId,
    price,
  };
  if (compareAtPrice) variant.compareAtPrice = compareAtPrice;

  const result = await makeGraphQLRequest<{
    productVariantsBulkUpdate: {
      productVariants: Array<{
        id: string;
        price: string;
        compareAtPrice: string | null;
      }> | null;
      userErrors: Array<{ field: string; message: string }>;
    };
  }>(mutation, { productId: shopifyProductId, variants: [variant] });

  if (!result.success) {
    return {
      success: false,
      message: result.error || "Failed to update variant price",
    };
  }
  const errors = result.data?.productVariantsBulkUpdate.userErrors || [];
  if (errors.length > 0) {
    return {
      success: false,
      message: errors.map((e) => `${e.field}: ${e.message}`).join("; "),
    };
  }
  return { success: true, message: "Variant price updated successfully" };
}

// ─── INVENTORY ─────────────────────────────────────────

export async function updateInventory(
  inventoryItemId: string,
  locationId: string,
  quantity: number
): Promise<{ success: boolean; message: string }> {
  const mutation = `
    mutation AdjustQuantities($input: InventoryAdjustQuantitiesInput!) {
      inventoryAdjustQuantities(input: $input) {
        inventoryAdjustmentGroup { createdAt reason }
        userErrors { field message }
      }
    }
  `;

  const result = await makeGraphQLRequest<{
    inventoryAdjustQuantities: {
      inventoryAdjustmentGroup: Record<string, unknown> | null;
      userErrors: Array<{ field: string; message: string }>;
    };
  }>(mutation, {
    input: {
      reason: "CORRECTION",
      changes: [{ inventoryItemId, locationId, quantityAdjustment: quantity }],
    },
  });

  if (!result.success) {
    return { success: false, message: result.error || "Failed to update inventory" };
  }
  const errors = result.data?.inventoryAdjustQuantities.userErrors || [];
  if (errors.length > 0) {
    return { success: false, message: errors.map((e) => `${e.field}: ${e.message}`).join("; ") };
  }
  return { success: true, message: "Inventory updated successfully" };
}

// ─── SET INVENTORY (absolute quantity) ─────────────────

export async function setInventory(
  inventoryItemId: string,
  locationId: string,
  quantity: number
): Promise<{ success: boolean; message: string }> {
  const mutation = `
    mutation SetQuantities($input: InventorySetQuantitiesInput!) {
      inventorySetQuantities(input: $input) {
        inventoryAdjustmentGroup { createdAt reason }
        userErrors { field message code }
      }
    }
  `;

  const result = await makeGraphQLRequest<{
    inventorySetQuantities: {
      inventoryAdjustmentGroup: Record<string, unknown> | null;
      userErrors: Array<{ field: string; message: string; code: string }>;
    };
  }>(mutation, {
    input: {
      reason: "CORRECTION",
      name: "available",
      quantities: [
        {
          inventoryItemId,
          locationId,
          quantity,
        },
      ],
    },
  });

  if (!result.success) {
    return { success: false, message: result.error || "Failed to set inventory" };
  }
  const errors = result.data?.inventorySetQuantities.userErrors || [];
  if (errors.length > 0) {
    return { success: false, message: errors.map((e) => `${e.field}: ${e.message}`).join("; ") };
  }
  return { success: true, message: "Inventory set successfully" };
}

// ─── FETCH SHOPIFY LOCATIONS ──────────────────────────

export interface ShopifyLocation {
  id: string;
  name: string;
  address: { formatted: string[] };
  isActive: boolean;
}

export async function fetchShopifyLocations(): Promise<{
  success: boolean;
  locations?: ShopifyLocation[];
  error?: string;
}> {
  const query = `
    query FetchLocations {
      locations(first: 50) {
        edges {
          node {
            id
            name
            address { formatted }
            isActive
          }
        }
      }
    }
  `;

  const result = await makeGraphQLRequest<{
    locations: {
      edges: Array<{ node: ShopifyLocation }>;
    };
  }>(query);

  if (!result.success || !result.data) {
    return { success: false, error: result.error || "Failed to fetch locations" };
  }

  return {
    success: true,
    locations: result.data.locations.edges.map((e) => e.node),
  };
}

// ─── DELETE PRODUCT ────────────────────────────────────

export async function deleteProduct(
  shopifyId: string
): Promise<{ success: boolean; message: string }> {
  const mutation = `
    mutation DeleteProduct($input: ProductDeleteInput!) {
      productDelete(input: $input) {
        deletedProductId
        userErrors { field message }
      }
    }
  `;

  const result = await makeGraphQLRequest<{
    productDelete: {
      deletedProductId: string | null;
      userErrors: Array<{ field: string; message: string }>;
    };
  }>(mutation, { input: { id: shopifyId } });

  if (!result.success) {
    return { success: false, message: result.error || "Failed to delete product" };
  }
  const errors = result.data?.productDelete.userErrors || [];
  if (errors.length > 0) {
    return { success: false, message: errors.map((e) => `${e.field}: ${e.message}`).join("; ") };
  }
  return { success: true, message: "Product deleted successfully" };
}

// ─── COLLECTIONS ──────────────────────────────────────

export interface ShopifyCollection {
  id: string;
  title: string;
  handle: string;
  description: string;
  descriptionHtml: string;
  sortOrder: string;
  templateSuffix: string | null;
  image: { url: string; altText: string | null } | null;
  seo: { title: string | null; description: string | null };
  productsCount: { count: number };
  ruleSet: { appliedDisjunctively: boolean; rules: Array<{ column: string; relation: string; condition: string }> } | null;
  updatedAt: string;
}

export async function fetchAllCollections(): Promise<{
  success: boolean;
  collections?: ShopifyCollection[];
  error?: string;
}> {
  const query = `
    query FetchCollections($cursor: String) {
      collections(first: 50, after: $cursor) {
        edges {
          cursor
          node {
            id
            title
            handle
            description
            descriptionHtml
            sortOrder
            templateSuffix
            image { url altText }
            seo { title description }
            productsCount { count }
            ruleSet {
              appliedDisjunctively
              rules { column relation condition }
            }
            updatedAt
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  `;

  const allCollections: ShopifyCollection[] = [];
  let cursor: string | null = null;
  let hasNextPage = true;

  interface CollectionsResponse {
    collections: {
      edges: Array<{ cursor: string; node: ShopifyCollection }>;
      pageInfo: { hasNextPage: boolean; endCursor: string | null };
    };
  }

  while (hasNextPage) {
    const result: { success: boolean; data?: CollectionsResponse; error?: string } =
      await makeGraphQLRequest<CollectionsResponse>(query, { cursor });

    if (!result.success || !result.data) {
      return { success: false, error: result.error || "Failed to fetch collections" };
    }

    for (const edge of result.data.collections.edges) {
      allCollections.push(edge.node);
    }

    hasNextPage = result.data.collections.pageInfo.hasNextPage;
    cursor = result.data.collections.pageInfo.endCursor || null;
  }

  return { success: true, collections: allCollections };
}

export async function fetchCollectionProducts(
  collectionId: string
): Promise<{ success: boolean; productIds?: string[]; error?: string }> {
  const query = `
    query CollectionProducts($id: ID!, $cursor: String) {
      collection(id: $id) {
        products(first: 100, after: $cursor) {
          edges {
            node { id }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
  `;

  const allProductIds: string[] = [];
  let cursor: string | null = null;
  let hasNextPage = true;

  interface CollectionProductsResponse {
    collection: {
      products: {
        edges: Array<{ node: { id: string } }>;
        pageInfo: { hasNextPage: boolean; endCursor: string | null };
      };
    };
  }

  while (hasNextPage) {
    const result: { success: boolean; data?: CollectionProductsResponse; error?: string } =
      await makeGraphQLRequest<CollectionProductsResponse>(query, { id: collectionId, cursor });

    if (!result.success || !result.data) {
      return { success: false, error: result.error || "Failed to fetch collection products" };
    }

    for (const edge of result.data.collection.products.edges) {
      allProductIds.push(edge.node.id);
    }

    hasNextPage = result.data.collection.products.pageInfo.hasNextPage;
    cursor = result.data.collection.products.pageInfo.endCursor || null;
  }

  return { success: true, productIds: allProductIds };
}

export async function updateCollection(
  shopifyCollectionId: string,
  data: {
    title?: string;
    description?: string;
    descriptionHtml?: string;
    seoTitle?: string;
    seoDescription?: string;
    sortOrder?: string;
    imageUrl?: string;
    imageAlt?: string;
  }
): Promise<{ success: boolean; message: string }> {
  const mutation = `
    mutation UpdateCollection($input: CollectionInput!) {
      collectionUpdate(input: $input) {
        collection { id title }
        userErrors { field message }
      }
    }
  `;

  const input: Record<string, unknown> = { id: shopifyCollectionId };
  if (data.title) input.title = data.title;
  if (data.descriptionHtml !== undefined) input.descriptionHtml = data.descriptionHtml;
  if (data.sortOrder) input.sortOrder = data.sortOrder;

  const seo: Record<string, string> = {};
  if (data.seoTitle) seo.title = data.seoTitle;
  if (data.seoDescription) seo.description = data.seoDescription;
  if (Object.keys(seo).length > 0) input.seo = seo;

  if (data.imageUrl) {
    input.image = { src: data.imageUrl, altText: data.imageAlt || "" };
  }

  const result = await makeGraphQLRequest<{
    collectionUpdate: {
      collection: { id: string; title: string } | null;
      userErrors: Array<{ field: string; message: string }>;
    };
  }>(mutation, { input });

  if (!result.success) {
    return { success: false, message: result.error || "Failed to update collection" };
  }
  const errors = result.data?.collectionUpdate.userErrors || [];
  if (errors.length > 0) {
    return { success: false, message: errors.map((e) => `${e.field}: ${e.message}`).join("; ") };
  }
  return { success: true, message: "Collection updated successfully" };
}

/**
 * Look up Shopify collections by handle (slug). Returns a map handle →
 * collection GID so callers can resolve a list of handles to IDs in one
 * GraphQL roundtrip and then call addProductsToCollection per matched
 * collection.
 *
 * Used by the auto-assign-on-push flow — given handles like
 * ["sunglasses", "ray-ban", "ray-ban-sunglasses"], we get back which
 * ones actually exist on Shopify and skip the rest silently.
 */
export async function findCollectionsByHandle(
  handles: string[]
): Promise<{ success: boolean; idByHandle: Map<string, string>; error?: string }> {
  const idByHandle = new Map<string, string>();
  if (handles.length === 0) return { success: true, idByHandle };

  // Shopify search supports "handle:foo OR handle:bar" — chain handles.
  const escaped = handles
    .map((h) => h.replace(/['"]/g, ""))
    .map((h) => `handle:${h}`)
    .join(" OR ");
  const query = `
    query FindCollections($q: String!) {
      collections(first: 50, query: $q) {
        edges { node { id handle } }
      }
    }
  `;
  const result = await makeGraphQLRequest<{
    collections: {
      edges: Array<{ node: { id: string; handle: string } }>;
    };
  }>(query, { q: escaped });

  if (!result.success) {
    return { success: false, idByHandle, error: result.error };
  }
  for (const e of result.data?.collections.edges || []) {
    idByHandle.set(e.node.handle, e.node.id);
  }
  return { success: true, idByHandle };
}

export async function addProductsToCollection(
  collectionId: string,
  productIds: string[]
): Promise<{ success: boolean; message: string }> {
  const mutation = `
    mutation CollectionAddProducts($id: ID!, $productIds: [ID!]!) {
      collectionAddProducts(id: $id, productIds: $productIds) {
        collection { id productsCount { count } }
        userErrors { field message }
      }
    }
  `;

  const result = await makeGraphQLRequest<{
    collectionAddProducts: {
      collection: { id: string } | null;
      userErrors: Array<{ field: string; message: string }>;
    };
  }>(mutation, { id: collectionId, productIds });

  if (!result.success) {
    return { success: false, message: result.error || "Failed to add products" };
  }
  const errors = result.data?.collectionAddProducts.userErrors || [];
  if (errors.length > 0) {
    return { success: false, message: errors.map((e) => `${e.field}: ${e.message}`).join("; ") };
  }
  return { success: true, message: "Products added to collection" };
}

export async function removeProductsFromCollection(
  collectionId: string,
  productIds: string[]
): Promise<{ success: boolean; message: string }> {
  const mutation = `
    mutation CollectionRemoveProducts($id: ID!, $productIds: [ID!]!) {
      collectionRemoveProducts(id: $id, productIds: $productIds) {
        userErrors { field message }
      }
    }
  `;

  const result = await makeGraphQLRequest<{
    collectionRemoveProducts: {
      userErrors: Array<{ field: string; message: string }>;
    };
  }>(mutation, { id: collectionId, productIds });

  if (!result.success) {
    return { success: false, message: result.error || "Failed to remove products" };
  }
  const errors = result.data?.collectionRemoveProducts.userErrors || [];
  if (errors.length > 0) {
    return { success: false, message: errors.map((e) => `${e.field}: ${e.message}`).join("; ") };
  }
  return { success: true, message: "Products removed from collection" };
}

// ─── FETCH ALL PRODUCTS FROM SHOPIFY ──────────────────

export interface ShopifyProductNode {
  id: string;
  title: string;
  handle: string;
  descriptionHtml: string;
  status: string;
  vendor: string;
  productType: string;
  tags: string[];
  totalInventory: number;
  createdAt: string;
  updatedAt: string;
  seo: { title: string | null; description: string | null };
  featuredImage: { url: string; altText: string | null } | null;
  images: {
    edges: Array<{
      node: { id: string; url: string; altText: string | null };
    }>;
  };
  variants: {
    edges: Array<{
      node: {
        id: string;
        title: string;
        sku: string | null;
        price: string;
        compareAtPrice: string | null;
        barcode: string | null;
        inventoryQuantity: number;
        selectedOptions: Array<{ name: string; value: string }>;
        inventoryItem: {
          id: string;
          inventoryLevels?: {
            edges: Array<{
              node: {
                location: { id: string };
                quantities: Array<{ name: string; quantity: number }>;
              };
            }>;
          };
        };
      };
    }>;
  };
  metafields: {
    edges: Array<{
      node: { namespace: string; key: string; value: string; type: string };
    }>;
  };
}

export async function fetchAllProducts(): Promise<{
  success: boolean;
  products?: ShopifyProductNode[];
  error?: string;
}> {
  // Page sizes are tuned against Shopify's GraphQL cost cap (1000 per query
  // on standard plans). The previous combination (products:50 × variants:100
  // × inventoryLevels:10) blew past it once the inventoryLevels field was
  // added. New defaults keep us well under the cap; tune via env if needed.
  const PRODUCTS_PER_PAGE = Number(process.env.SHOPIFY_PULL_PAGE_SIZE) || 20;
  const VARIANTS_PER_PRODUCT =
    Number(process.env.SHOPIFY_PULL_VARIANTS_PER_PRODUCT) || 25;
  const LOCATIONS_PER_VARIANT =
    Number(process.env.SHOPIFY_PULL_LOCATIONS_PER_VARIANT) || 5;
  const IMAGES_PER_PRODUCT = 10;
  const METAFIELDS_PER_PRODUCT = 15;

  const query = `
    query FetchProducts($cursor: String) {
      products(first: ${PRODUCTS_PER_PAGE}, after: $cursor) {
        edges {
          cursor
          node {
            id
            title
            handle
            descriptionHtml
            status
            vendor
            productType
            tags
            totalInventory
            createdAt
            updatedAt
            seo { title description }
            featuredImage { url altText }
            images(first: ${IMAGES_PER_PRODUCT}) {
              edges {
                node { id url altText }
              }
            }
            variants(first: ${VARIANTS_PER_PRODUCT}) {
              edges {
                node {
                  id
                  title
                  sku
                  price
                  compareAtPrice
                  barcode
                  inventoryQuantity
                  selectedOptions { name value }
                  inventoryItem {
                    id
                    inventoryLevels(first: ${LOCATIONS_PER_VARIANT}) {
                      edges {
                        node {
                          location { id }
                          quantities(names: ["available"]) { name quantity }
                        }
                      }
                    }
                  }
                }
              }
            }
            metafields(first: ${METAFIELDS_PER_PRODUCT}) {
              edges {
                node { namespace key value type }
              }
            }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  `;

  const allProducts: ShopifyProductNode[] = [];
  let cursor: string | null = null;
  let hasNextPage = true;

  interface ProductsResponse {
    products: {
      edges: Array<{ cursor: string; node: ShopifyProductNode }>;
      pageInfo: { hasNextPage: boolean; endCursor: string | null };
    };
  }

  while (hasNextPage) {
    const result: { success: boolean; data?: ProductsResponse; error?: string } =
      await makeGraphQLRequest<ProductsResponse>(query, { cursor });
    if (!result.success || !result.data) {
      return { success: false, error: result.error || "Failed to fetch products" };
    }
    for (const edge of result.data.products.edges) {
      allProducts.push(edge.node);
    }
    hasNextPage = result.data.products.pageInfo.hasNextPage;
    cursor = result.data.products.pageInfo.endCursor || null;
  }

  return { success: true, products: allProducts };
}

// Fetch a single page of products (for resumable/chunked pulls).
// Returns the nodes plus pagination info so the caller can loop, persist
// the cursor, or stop early.
export async function fetchProductsPage(
  cursor: string | null
): Promise<{
  success: boolean;
  products?: ShopifyProductNode[];
  nextCursor?: string | null;
  hasNextPage?: boolean;
  error?: string;
}> {
  const PRODUCTS_PER_PAGE = Number(process.env.SHOPIFY_PULL_PAGE_SIZE) || 20;
  const VARIANTS_PER_PRODUCT =
    Number(process.env.SHOPIFY_PULL_VARIANTS_PER_PRODUCT) || 25;
  const LOCATIONS_PER_VARIANT =
    Number(process.env.SHOPIFY_PULL_LOCATIONS_PER_VARIANT) || 5;
  const IMAGES_PER_PRODUCT = 10;
  const METAFIELDS_PER_PRODUCT = 15;

  const query = `
    query FetchProductsPage($cursor: String) {
      products(first: ${PRODUCTS_PER_PAGE}, after: $cursor) {
        edges {
          cursor
          node {
            id
            title
            handle
            descriptionHtml
            status
            vendor
            productType
            tags
            totalInventory
            createdAt
            updatedAt
            seo { title description }
            featuredImage { url altText }
            images(first: ${IMAGES_PER_PRODUCT}) {
              edges { node { id url altText } }
            }
            variants(first: ${VARIANTS_PER_PRODUCT}) {
              edges {
                node {
                  id
                  title
                  sku
                  price
                  compareAtPrice
                  barcode
                  inventoryQuantity
                  selectedOptions { name value }
                  inventoryItem {
                    id
                    inventoryLevels(first: ${LOCATIONS_PER_VARIANT}) {
                      edges {
                        node {
                          location { id }
                          quantities(names: ["available"]) { name quantity }
                        }
                      }
                    }
                  }
                }
              }
            }
            metafields(first: ${METAFIELDS_PER_PRODUCT}) {
              edges { node { namespace key value type } }
            }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  `;

  interface ProductsResponse {
    products: {
      edges: Array<{ cursor: string; node: ShopifyProductNode }>;
      pageInfo: { hasNextPage: boolean; endCursor: string | null };
    };
  }

  const result = await makeGraphQLRequest<ProductsResponse>(query, { cursor });
  if (!result.success || !result.data) {
    return {
      success: false,
      error: result.error || "Failed to fetch products page",
    };
  }
  return {
    success: true,
    products: result.data.products.edges.map((e) => e.node),
    nextCursor: result.data.products.pageInfo.endCursor,
    hasNextPage: result.data.products.pageInfo.hasNextPage,
  };
}

export async function fetchProductByShopifyId(shopifyGid: string): Promise<{
  success: boolean;
  product?: ShopifyProductNode;
  error?: string;
}> {
  const query = `
    query FetchProduct($id: ID!) {
      product(id: $id) {
        id
        title
        handle
        descriptionHtml
        status
        vendor
        productType
        tags
        totalInventory
        createdAt
        updatedAt
        seo { title description }
        featuredImage { url altText }
        images(first: 20) {
          edges {
            node { id url altText }
          }
        }
        variants(first: 100) {
          edges {
            node {
              id
              title
              sku
              price
              compareAtPrice
              barcode
              inventoryQuantity
              selectedOptions { name value }
              inventoryItem {
                id
                inventoryLevels(first: 10) {
                  edges {
                    node {
                      location { id }
                      quantities(names: ["available"]) { name quantity }
                    }
                  }
                }
              }
            }
          }
        }
        metafields(first: 20) {
          edges {
            node { namespace key value type }
          }
        }
      }
    }
  `;

  const result = await makeGraphQLRequest<{ product: ShopifyProductNode }>(query, { id: shopifyGid });
  if (!result.success || !result.data?.product) {
    return { success: false, error: result.error || "Product not found" };
  }
  return { success: true, product: result.data.product };
}

// ─── WEBHOOK SUBSCRIPTIONS ────────────────────────────

export interface WebhookSubscriptionNode {
  id: string;
  topic: string;
  endpoint: {
    __typename: string;
    callbackUrl?: string;
  };
  format: string;
  createdAt: string;
  updatedAt: string;
}

export async function registerWebhook(
  topic: string,
  callbackUrl: string
): Promise<{ success: boolean; webhookId?: string; error?: string }> {
  const mutation = `
    mutation WebhookCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
      webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
        webhookSubscription {
          id
          topic
          endpoint {
            __typename
            ... on WebhookHttpEndpoint {
              callbackUrl
            }
          }
        }
        userErrors { field message }
      }
    }
  `;

  const result = await makeGraphQLRequest<{
    webhookSubscriptionCreate: {
      webhookSubscription: { id: string; topic: string } | null;
      userErrors: Array<{ field: string; message: string }>;
    };
  }>(mutation, {
    topic,
    webhookSubscription: {
      callbackUrl,
      format: "JSON",
    },
  });

  if (!result.success) {
    return { success: false, error: result.error || "Failed to register webhook" };
  }
  const errors = result.data?.webhookSubscriptionCreate.userErrors || [];
  if (errors.length > 0) {
    return { success: false, error: errors.map((e) => `${e.field}: ${e.message}`).join("; ") };
  }
  const webhook = result.data?.webhookSubscriptionCreate.webhookSubscription;
  return { success: true, webhookId: webhook?.id };
}

export async function listWebhooks(): Promise<{
  success: boolean;
  webhooks?: WebhookSubscriptionNode[];
  error?: string;
}> {
  const query = `
    query ListWebhooks {
      webhookSubscriptions(first: 50) {
        edges {
          node {
            id
            topic
            endpoint {
              __typename
              ... on WebhookHttpEndpoint {
                callbackUrl
              }
            }
            format
            createdAt
            updatedAt
          }
        }
      }
    }
  `;

  const result = await makeGraphQLRequest<{
    webhookSubscriptions: {
      edges: Array<{ node: WebhookSubscriptionNode }>;
    };
  }>(query);

  if (!result.success || !result.data) {
    return { success: false, error: result.error || "Failed to list webhooks" };
  }

  return {
    success: true,
    webhooks: result.data.webhookSubscriptions.edges.map((e) => e.node),
  };
}

export async function deleteWebhook(
  webhookId: string
): Promise<{ success: boolean; error?: string }> {
  const mutation = `
    mutation WebhookDelete($id: ID!) {
      webhookSubscriptionDelete(id: $id) {
        deletedWebhookSubscriptionId
        userErrors { field message }
      }
    }
  `;

  const result = await makeGraphQLRequest<{
    webhookSubscriptionDelete: {
      deletedWebhookSubscriptionId: string | null;
      userErrors: Array<{ field: string; message: string }>;
    };
  }>(mutation, { id: webhookId });

  if (!result.success) {
    return { success: false, error: result.error || "Failed to delete webhook" };
  }
  const errors = result.data?.webhookSubscriptionDelete.userErrors || [];
  if (errors.length > 0) {
    return { success: false, error: errors.map((e) => `${e.field}: ${e.message}`).join("; ") };
  }
  return { success: true };
}

// ─── METAFIELDS ────────────────────────────────────────
// One mutation handles BOTH product and variant metafields — Shopify
// just looks at ownerId. Helpers below select the right resource.

interface MetafieldInput {
  namespace: string;
  key: string;
  value: string;
  type: string;
}

async function setMetafieldsForOwner(
  ownerId: string,
  metafields: MetafieldInput[]
): Promise<{ success: boolean; message: string }> {
  const mutation = `
    mutation SetMetafields($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { id namespace key value }
        userErrors { field message }
      }
    }
  `;

  const input = metafields.map((mf) => ({ ...mf, ownerId }));

  const result = await makeGraphQLRequest<{
    metafieldsSet: {
      metafields: Array<{ id: string }>;
      userErrors: Array<{ field: string; message: string }>;
    };
  }>(mutation, { metafields: input });

  if (!result.success) {
    return { success: false, message: result.error || "Failed to set metafields" };
  }
  const errors = result.data?.metafieldsSet.userErrors || [];
  if (errors.length > 0) {
    return { success: false, message: errors.map((e) => `${e.field}: ${e.message}`).join("; ") };
  }
  return { success: true, message: "Metafields set successfully" };
}

export async function setProductMetafields(
  shopifyProductId: string,
  metafields: MetafieldInput[]
): Promise<{ success: boolean; message: string }> {
  return setMetafieldsForOwner(shopifyProductId, metafields);
}

export async function setVariantMetafields(
  shopifyVariantId: string,
  metafields: MetafieldInput[]
): Promise<{ success: boolean; message: string }> {
  return setMetafieldsForOwner(shopifyVariantId, metafields);
}

// ─── FILE UPLOAD via Shopify Staged Uploads ───────────────

interface StagedTarget {
  url: string;
  resourceUrl: string;
  parameters: Array<{ name: string; value: string }>;
}

/**
 * Upload a file to Shopify's CDN using the Staged Uploads API.
 * Returns the permanent CDN URL on success.
 */
export async function uploadFileToShopify(
  fileBuffer: Buffer,
  fileName: string,
  mimeType: string
): Promise<{ success: boolean; url?: string; error?: string }> {
  try {
    // Step 1: Create a staged upload target
    const stageMutation = `
      mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
        stagedUploadsCreate(input: $input) {
          stagedTargets {
            url
            resourceUrl
            parameters { name value }
          }
          userErrors { field message }
        }
      }
    `;

    const stageResult = await makeGraphQLRequest<{
      stagedUploadsCreate: {
        stagedTargets: StagedTarget[];
        userErrors: Array<{ field: string; message: string }>;
      };
    }>(stageMutation, {
      input: [
        {
          filename: fileName,
          mimeType,
          resource: "FILE",
          httpMethod: "POST",
          fileSize: String(fileBuffer.length),
        },
      ],
    });

    if (!stageResult.success || !stageResult.data) {
      return { success: false, error: stageResult.error || "Failed to create staged upload" };
    }

    const stageErrors = stageResult.data.stagedUploadsCreate.userErrors;
    if (stageErrors.length > 0) {
      return { success: false, error: stageErrors.map((e) => e.message).join("; ") };
    }

    const target = stageResult.data.stagedUploadsCreate.stagedTargets[0];
    if (!target) {
      return { success: false, error: "No staged target returned" };
    }

    // Step 2: Upload the file to the staged target URL
    const formData = new FormData();
    for (const param of target.parameters) {
      formData.append(param.name, param.value);
    }
    formData.append("file", new Blob([new Uint8Array(fileBuffer)], { type: mimeType }), fileName);

    const uploadResponse = await fetch(target.url, {
      method: "POST",
      body: formData,
    });

    if (!uploadResponse.ok) {
      const errorText = await uploadResponse.text();
      console.error("Staged upload POST failed:", uploadResponse.status, errorText);
      return { success: false, error: `Upload to staging failed: ${uploadResponse.status}` };
    }

    // Step 3: Create a file record in Shopify pointing to the staged resource
    const fileCreateMutation = `
      mutation fileCreate($files: [FileCreateInput!]!) {
        fileCreate(files: $files) {
          files {
            ... on MediaImage {
              id
              image { url }
            }
            ... on GenericFile {
              id
              url
            }
          }
          userErrors { field message }
        }
      }
    `;

    const fileResult = await makeGraphQLRequest<{
      fileCreate: {
        files: Array<{
          id: string;
          image?: { url: string };
          url?: string;
        }>;
        userErrors: Array<{ field: string; message: string }>;
      };
    }>(fileCreateMutation, {
      files: [
        {
          originalSource: target.resourceUrl,
          contentType: "IMAGE",
        },
      ],
    });

    if (!fileResult.success || !fileResult.data) {
      return { success: false, error: fileResult.error || "Failed to create file in Shopify" };
    }

    const fileErrors = fileResult.data.fileCreate.userErrors;
    if (fileErrors.length > 0) {
      return { success: false, error: fileErrors.map((e) => e.message).join("; ") };
    }

    const createdFile = fileResult.data.fileCreate.files[0];
    const cdnUrl = createdFile?.image?.url || createdFile?.url || target.resourceUrl;

    return { success: true, url: cdnUrl };
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Unknown error";
    console.error("uploadFileToShopify error:", msg);
    return { success: false, error: msg };
  }
}
