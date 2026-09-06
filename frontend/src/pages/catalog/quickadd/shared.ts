// ============================================================================
// Quick Add - shared vocabulary (accordion ids, the error->section rule and
// the "open the existing product" destination). MOVED verbatim out of
// QuickAddPage.tsx by the Wave 3 file diet; QuickAddPage re-exports
// productListPath from its old path so existing importers are untouched.
// ============================================================================

export type SectionId = 'identity' | 'pricing' | 'inventory';

// Which accordion a validator key lives in. The discount tier sits in Pricing,
// so a discount_category error must open Pricing (not Identity) or the inline
// error would stay hidden. Everything else the validator names is Identity.
const PRICING_ERROR_KEYS: ReadonlySet<string> = new Set(['mrp', 'offer_price', 'discount_category']);
export const sectionOfError = (key: string): SectionId =>
  PRICING_ERROR_KEYS.has(key) ? 'pricing' : 'identity';

/** Where "open the existing product" lands. There is no per-product detail
 *  route yet; the Inventory stock ledger is the canonical product list and it
 *  seeds its search box from `?search=`. This used to link the mega-page tab
 *  `/inventory?tab=catalog`, which the Wave 2 split turned into a redirect —
 *  the real address is linked directly now (the shim stays for bookmarks). */
export const productListPath = (sku?: string | null): string =>
  `/inventory/stock${sku ? `?search=${encodeURIComponent(sku)}` : ''}`;

// The page's edit target, discriminated by which collection it edits:
//   kind='spine'   — /catalog/add?edit=<id>: EDIT-IN-PLACE of a billing
//                    `products` row (one validated PUT /products/{id}).
//   kind='catalog' — /catalog/add?review=<id>: FULL-PAGE REVIEW of an
//                    imported `catalog_products` doc (diff-only PUT
//                    /catalog/products/{id} + promote to approve).
//   null           — plain create (also clone / variant seeding).
export type EditMode = { kind: 'spine' | 'catalog'; id: string; sku: string } | null;
