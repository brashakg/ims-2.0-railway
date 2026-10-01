import type { ProductFormValues } from './formModel';
import { resolveHsnGst } from './formModel';

// ============================================================================
// Clone support (Phase C): map a persisted product doc back into the Quick Add
// form-values shape so the user can tweak it and save it as a NEW SKU.
// ----------------------------------------------------------------------------
// The create payload flattens attributes (see buildProductPayload); cloning is
// the inverse. We rebuild `attributes` from the product's stored `attributes`
// dict (the canonical home of the category-specific fields), falling back to a
// few top-level identity fields so a clone still prefills brand/model even for
// older docs that didn't persist them under attributes.

// String coercion that turns null/undefined/NaN into '' so blank inputs stay
// blank (and number 0 / 0.0 survives as "0").
export const str = (v: unknown): string => {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return s === 'NaN' ? '' : s;
};

/** The catalog twin's tag list -- ecom.seo.tags, the ONE spelling every door
 *  writes and the Shopify push reads (sync audit gap #4); the BVI import's
 *  top-level `tags` only when that is absent. Mirrors product_master.twin_tags. */
export function docTags(doc: Record<string, unknown>): string[] {
  const ecom = doc.ecom as Record<string, unknown> | null | undefined;
  const seo = ecom?.seo as Record<string, unknown> | null | undefined;
  const raw = Array.isArray(seo?.tags) ? seo.tags : doc.tags;
  return Array.isArray(raw) ? (raw as unknown[]).map((t) => str(t)).filter(Boolean) : [];
}

// A product doc as returned by GET /products/{id} (only the fields we read).
export interface ProductDoc {
  category?: string;
  brand?: string;
  model?: string;
  attributes?: Record<string, unknown> | null;
  description?: string;
  hsn_code?: string;
  gst_rate?: number | string;
  weight?: number | string;
  mrp?: number | string;
  offer_price?: number | string;
  cost_price?: number | string;
  discount_category?: string;
  // CL / lens identity (top-level on the doc; mirrored back into attributes).
  cl_series?: unknown;
  modality?: unknown;
  base_curve?: unknown;
  diameter?: unknown;
  cl_power?: unknown;
  cl_cyl?: unknown;
  cl_axis?: unknown;
  cl_add?: unknown;
  pack_size?: unknown;
  sph?: unknown;
  cyl?: unknown;
  axis?: unknown;
  add?: unknown;
  [k: string]: unknown;
}

export function productToFormValues(product: ProductDoc): ProductFormValues {
  const category = str(product.category);

  // Start from the stored attributes (canonical), then ensure the top-level
  // identity + power fields are represented so the form prefills fully.
  const attributes: Record<string, string> = {};
  const srcAttrs = product.attributes || {};
  Object.keys(srcAttrs).forEach((k) => {
    attributes[k] = str(srcAttrs[k]);
  });

  // Backfill brand/model from the top-level fields when the attributes dict
  // didn't carry them (older docs). The form maps brand_name/model_no back to
  // top-level brand/model on save, so this keeps the round-trip lossless.
  if (!attributes.brand_name && product.brand) attributes.brand_name = str(product.brand);
  if (!attributes.model_no && !attributes.model_name && product.model) {
    attributes.model_no = str(product.model);
  }

  // Mirror the top-level CL / lens identity fields back onto the attribute
  // names the form uses (buildProductPayload reads these names).
  const mirror: Array<[keyof ProductDoc, string]> = [
    ['cl_series', 'cl_series'],
    ['modality', 'modality'],
    ['base_curve', 'base_curve'],
    ['diameter', 'diameter'],
    ['cl_power', 'power'],
    ['cl_cyl', 'cl_cyl'],
    ['cl_axis', 'cl_axis'],
    ['cl_add', 'cl_add'],
    ['pack_size', 'pack'],
    ['sph', 'sph'],
    ['cyl', 'cyl'],
    ['axis', 'axis'],
    ['add', 'add'],
  ];
  mirror.forEach(([docKey, attrKey]) => {
    const v = product[docKey];
    if (v !== null && v !== undefined && !attributes[attrKey]) {
      attributes[attrKey] = str(v);
    }
  });

  return {
    category,
    attributes,
    description: str(product.description),
    hsnCode: str(product.hsn_code),
    // A doc old enough to have no gst_rate is rated from its CATEGORY, not
    // from a hand-written 18. The clone form's rate is saved as the new SKU's
    // gst_rate (routers/products: an explicit rate wins over the server's own),
    // and the category autofill that would have corrected it is deliberately
    // SKIPPED when the clone brings an hsn_code with it (QuickAddPage), so an
    // 18 here rides all the way onto a 5% frame.
    gstRate: str(product.gst_rate) || resolveHsnGst(category).gstRate,
    weight: str(product.weight),
    mrp: str(product.mrp),
    offerPrice: str(product.offer_price),
    costPrice: str(product.cost_price),
    // Clone inherits the source tier when it has one; a tier-less (legacy)
    // source maps to '' so the operator must consciously pick a tier (matches
    // the require-explicit-tier rule on the create forms).
    discountCategory: str(product.discount_category) || '',
    // Online flags are NOT cloned: a new SKU shouldn't inherit Shopify sync.
    syncToShopify: false,
    shopifyTags: [],
    // Preserve the source product's images so the clone starts with them (the
    // operator can remove them before saving the new SKU).
    images: Array.isArray(product.images)
      ? (product.images as unknown[]).map((u) => str(u)).filter(Boolean)
      : [],
  };
}

/** A maker's barcode names ONE item, so a new SKU never inherits it: the server
 *  refuses a GTIN another product holds (product_master.assert_gtin_free), and a
 *  copied UPC would reach Shopify/Google as the source's. The variant rulebook
 *  (variantRules VARIANT_NEVER_KEYS) reads this same list. */
export const MANUFACTURER_BARCODE_KEYS = ['upc', 'gtin'];

/** `values` minus the manufacturer barcodes: THE strip for every prefill that
 *  starts a NEW SKU from another's data (Clone, a saved template). */
export function withoutManufacturerBarcodes(values: ProductFormValues): ProductFormValues {
  const attributes = { ...(values.attributes || {}) };
  MANUFACTURER_BARCODE_KEYS.forEach((k) => delete attributes[k]);
  return { ...values, attributes };
}

/** The Clone prefill: every stored field except the manufacturer barcodes. */
export function productToCloneValues(product: ProductDoc): ProductFormValues {
  return withoutManufacturerBarcodes(productToFormValues(product));
}
