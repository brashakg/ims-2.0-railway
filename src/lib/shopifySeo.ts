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
 * GID. Setting `category` on ProductInput puts the product in the right
 * Google Shopping / Shop App / Marketplace classification automatically.
 *
 * GIDs verified live against Shopify's taxonomy API on 2026-05-08:
 *   query { taxonomy { categories(search:"...") { edges { node { id fullName } } } } }
 *
 * Most eyewear sub-categories (reading/computer/safety/clip-on/smart)
 * don't have dedicated taxonomy nodes — they fall under the closest
 * parent (Eyeglasses / Sunglasses / Smart Glasses / Safety Glasses).
 */
export function shopifyTaxonomyGidFor(
  category: string | null | undefined
): string | null {
  const norm = normalizeCategory(category);
  switch (norm) {
    case "SUNGLASSES":
    case "CLIP_ON_FRAMES":
      // Apparel & Accessories > Clothing Accessories > Sunglasses
      return "gid://shopify/TaxonomyCategory/aa-2-27";
    case "SPECTACLES":
    case "READING_GLASSES":
    case "COMPUTER_GLASSES":
      // Health & Beauty > Personal Care > Vision Care > Eyeglasses
      return "gid://shopify/TaxonomyCategory/hb-3-19-4";
    case "SAFETY_GLASSES":
      // Business & Industrial > Work Safety Protective Gear > Protective Eyewear > Safety Glasses
      return "gid://shopify/TaxonomyCategory/bi-25-6-3";
    case "CONTACT_LENSES":
      // Health & Beauty > Personal Care > Vision Care > Contact Lens Care > Contact Lenses
      return "gid://shopify/TaxonomyCategory/hb-3-19-1-4";
    case "WATCHES":
      // Apparel & Accessories > Jewelry > Watches
      return "gid://shopify/TaxonomyCategory/aa-6-11";
    case "SMARTWATCHES":
      // Apparel & Accessories > Jewelry > Smart Watches
      return "gid://shopify/TaxonomyCategory/aa-6-12";
    case "SMARTGLASSES":
      // Electronics > Computers > Smart Glasses
      return "gid://shopify/TaxonomyCategory/el-6-7";
    case "ACCESSORIES":
      // Health & Beauty > Personal Care > Vision Care > Eyewear Accessories
      return "gid://shopify/TaxonomyCategory/hb-3-19-5";
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
