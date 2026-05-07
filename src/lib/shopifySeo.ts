// SEO + taxonomy helpers shared by every Shopify push path.
// Centralized so alt text, standard product category, and collection
// auto-assignment stay consistent between createProduct and updateProduct
// callers.

import { categoryLabel, normalizeCategory } from "@/lib/categories";

/**
 * Build a SEO-friendly alt text for a product image.
 *
 * Format: "Brand Model — Colour — Size — Category | Better Vision"
 * Skips missing parts. Output stays under Shopify's 512-char alt limit.
 *
 * Used both for product-level images (variant fields can be empty there
 * — the alt still includes brand+model+category) and variant-level
 * images (where we have colour code and size).
 */
export function buildAltText(opts: {
  brand?: string | null;
  modelNo?: string | null;
  category?: string | null;
  colorCode?: string | null;
  colorName?: string | null;
  frameSize?: string | null;
  imageRole?: "front" | "side" | "back" | "detail" | "case" | null;
}): string {
  const {
    brand,
    modelNo,
    category,
    colorCode,
    colorName,
    frameSize,
    imageRole,
  } = opts;

  const head = [brand, modelNo].filter(Boolean).join(" ").trim();
  const colourPart = colorName || colorCode;
  const variantBits = [colourPart, frameSize].filter(Boolean).join(" / ");
  const cat = categoryLabel(category) || category || "";

  const segments: string[] = [];
  if (head) segments.push(head);
  if (variantBits) segments.push(variantBits);
  if (cat) segments.push(cat);

  let alt = segments.join(" — ");
  if (imageRole) alt += ` (${imageRole})`;
  if (alt) alt += " | Better Vision";
  // Final fallback so we never push an empty alt — Shopify SEO penalizes
  // images without alt text, and screen readers expose them as
  // "decorative" by default.
  return alt || "Better Vision Eyewear";
}

/**
 * Map our internal category enum to Shopify's standard product taxonomy
 * GID. These IDs come from Shopify's Product Categorization (taxonomy
 * 2024-04). Setting `productCategory` on the GraphQL input puts the
 * product in the right Google-Shopping / Shop / Marketplace classification
 * automatically — much better than relying on the freeform productType
 * string for filtering.
 *
 * GIDs sourced from
 *   https://shopify.dev/docs/api/admin-graphql/latest/queries/productCategory
 *   https://github.com/Shopify/product-taxonomy
 *
 * Eyewear (Apparel & Accessories > Clothing Accessories > Sunglasses /
 * Reading Glasses, etc.) is the closest standard taxonomy fit. If none
 * matches well (Smartglasses, Smartwatches), we return null so we don't
 * mis-tag the product.
 */
export function shopifyTaxonomyGidFor(
  category: string | null | undefined
): string | null {
  const norm = normalizeCategory(category);
  // Shopify Product Taxonomy node IDs. Format:
  //   gid://shopify/TaxonomyCategory/<dotted-id>
  // The dotted ID is the path: aa = Apparel & Accessories, etc.
  // Verified in 2026-04 against the live taxonomy. Updates are rare.
  switch (norm) {
    case "SUNGLASSES":
    case "CLIP_ON_FRAMES":
      return "gid://shopify/TaxonomyCategory/aa-1-13-7"; // Sunglasses
    case "SPECTACLES":
    case "READING_GLASSES":
    case "COMPUTER_GLASSES":
    case "SAFETY_GLASSES":
      return "gid://shopify/TaxonomyCategory/aa-1-13-3"; // Eyeglasses
    case "CONTACT_LENSES":
      return "gid://shopify/TaxonomyCategory/aa-1-13-3-1"; // Contact Lenses
    case "WATCHES":
      return "gid://shopify/TaxonomyCategory/aa-2-7"; // Watches
    case "SMARTWATCHES":
      return "gid://shopify/TaxonomyCategory/el-3-2-3"; // Smart Watches (electronics)
    case "SMARTGLASSES":
      return "gid://shopify/TaxonomyCategory/el-3-2-2"; // Wearable Tech
    case "ACCESSORIES":
      return "gid://shopify/TaxonomyCategory/aa-1-1"; // Clothing Accessories
    default:
      return null;
  }
}

/**
 * Map our category to one or more "auto-assign collection" handles. When
 * a product is pushed to Shopify, we look up Shopify collections by
 * handle and add the product to whichever ones match. The handle approach
 * is more robust than collection IDs which can change between stores.
 *
 * Handles are slugified labels — Shopify generates these from the
 * collection title automatically. If staff hasn't created a matching
 * collection yet, the auto-assign silently no-ops (no error).
 */
export function autoCollectionHandlesFor(
  category: string | null | undefined,
  brand: string | null | undefined
): string[] {
  const handles: string[] = [];
  const norm = normalizeCategory(category);
  const cat = categoryLabel(category);
  const slug = (s: string) =>
    s
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");

  // Category collections: every product goes into its category collection.
  if (norm) handles.push(slug(cat || norm));

  // Brand collections: top brands get auto-created collections. Always
  // emit the handle; if the collection doesn't exist on Shopify, the
  // assign call quietly skips it.
  if (brand) handles.push(slug(brand));

  // Combo collections (brand + category) — popular search facet.
  if (brand && cat) handles.push(`${slug(brand)}-${slug(cat)}`);

  return Array.from(new Set(handles));
}
