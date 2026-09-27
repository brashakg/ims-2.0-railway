import { CATEGORY_FIELDS, getCategoryFields } from './categoryFields';
import type { ProductFormValues } from './formModel';
import type { ProductDoc } from './cloneMapping';
import { productToFormValues, str } from './cloneMapping';
import { inferCategoryCode } from './inferCategory';

// ============================================================================
// Variant assist (Phase 1): duplicate-rescue popup / "+ Variant" -> variant mode
// ----------------------------------------------------------------------------
// Turn an EXISTING product into the seed for a SIBLING variant (a new colour /
// size of the same model). Unlike clone (which copies everything), variant
// mode applies an explicit per-category copy rulebook that FAILS CLOSED:
//
//   COPY (silent)  - model-level attributes shared by every variant of the
//                    model (brand/subbrand/label/model, shape, frame type,
//                    materials, gender, warranty, origin, USPs, blue-cut, ...).
//   FLAG (amber)   - copied but needs explicit confirmation before save:
//                    sizes (lens/bridge/temple/size/dial/belt) + the form-level
//                    pricing (MRP/offer/cost) and HSN/GST/discount band
//                    (owner ruling 2026-07-04: variant price copies with an
//                    amber confirm flag).
//   NEVER (clear)  - variant-DEFINING or per-unit values: every colour-ish
//                    field (colour_code, frame/temple/lens colour, tint, dial/
//                    belt/body colour...), polarization (differs per sunglass
//                    variant), manufacturer identity codes (UPC/GTIN/full
//                    model no/serial no), batch expiry, description, images.
//                    SKU/barcode/description keep fresh-create behaviour
//                    (auto-mint / blank + AI button); photos are offered via a
//                    one-click "Copy photos from <sibling>" instead.
//
//   UNKNOWN keys   - DEFAULT-DENY. Any attribute not classified above is NOT
//                    carried over (fail closed) — a new field added to a
//                    category can never silently leak across variants.

export type VariantFieldRule = 'copy' | 'flag' | 'never';

// Model-level attributes that carry over to a sibling variant unchanged.
const VARIANT_COPY_SILENT = new Set<string>([
  'brand_name', 'subbrand', 'label', 'model_no', 'model_name',
  'shape', 'frame_type', 'gender', 'warranty', 'country_of_origin',
  'usp_1', 'usp_2', 'blue_cut_lens', 'uv_protection',
  'lens_material', 'frame_material', 'temple_material',
  // optical lens (LS) model identity
  'index', 'coating', 'lens_category', 'add_on_1', 'add_on_2', 'add_on_3',
  // contact lens model identity
  'cl_series', 'modality',
  // watches / clocks / hearing aids / accessories / smart devices
  'watch_category', 'clock_category', 'battery_size',
  'machine_capacity', 'machine_type', 'accessory_type', 'year_of_launch',
]);

// Sizes carry over but stay amber-FLAGGED until the operator confirms them —
// a new colour of the same model usually shares them, but not always.
const VARIANT_COPY_FLAGGED = new Set<string>([
  'lens_size', 'bridge_width', 'temple_length', 'size',
  'dial_size', 'belt_size',
]);

// Identity/manufacturer codes are per-variant (or per-unit) — never copied.
const VARIANT_NEVER_KEYS = new Set<string>([
  'upc', 'gtin', 'full_model_no', 'serial_no', 'sku', 'barcode',
  // batch-specific (CL medical shelf life) — a new variant has its own batch.
  'expiry_date',
  // provenance of the SIBLING's data (legacy Autopilot-era attr on old docs).
  'autopilot_reference',
]);

// Anything colour-ish is variant-DEFINING. Matched by pattern (not just an
// explicit list) so a colour field added to ANY category defaults to
// "cleared", never silently copied: colour_code, colour_name, frame_color,
// temple_color, lens_colour, tint, dial_colour, belt_colour, body_colour, ...
const VARIANT_COLOUR_RE = /colou?r|tint/i;

/** Classify one attribute key for variant seeding. FAILS CLOSED: an unknown
 *  key returns 'never'. `category` is accepted for future category-specific
 *  carve-outs; today the rulebook is uniform across categories (polarization
 *  only exists on SUNGLASS, where it is variant-defining per the owner). */
export function variantFieldRule(category: string, name: string): VariantFieldRule {
  void category;
  if (!name) return 'never';
  if (name === 'polarization') return 'never';
  if (VARIANT_COLOUR_RE.test(name)) return 'never';
  if (VARIANT_NEVER_KEYS.has(name)) return 'never';
  if (VARIANT_COPY_FLAGGED.has(name)) return 'flag';
  if (VARIANT_COPY_SILENT.has(name)) return 'copy';
  return 'never'; // FAIL CLOSED: unknown keys are never silently copied.
}

export interface VariantCopyRules {
  copy: string[]; // copied silently
  flag: string[]; // copied but amber-flagged until confirmed
  cleared: string[]; // variant-defining / identity codes — cleared for entry
}

// PER-CATEGORY rulebook, derived from each category's declared field registry
// (CATEGORY_FIELDS keys) through variantFieldRule. "+ Variant" applies to ALL
// categories — every picker code gets an entry; any field not classified by
// the explicit lists lands in `cleared` (fail closed).
export const VARIANT_COPY_FIELDS: Record<string, VariantCopyRules> =
  Object.fromEntries(
    Object.keys(CATEGORY_FIELDS).map((code) => {
      const rules: VariantCopyRules = { copy: [], flag: [], cleared: [] };
      (CATEGORY_FIELDS[code] || []).forEach((f) => {
        const r = variantFieldRule(code, f.name);
        if (r === 'copy') rules.copy.push(f.name);
        else if (r === 'flag') rules.flag.push(f.name);
        else rules.cleared.push(f.name);
      });
      return [code, rules];
    })
  );

/** Form-level (non-attribute) fields that copy to a variant but stay
 *  amber-flagged until touched: pricing (owner ruling) + HSN/GST/discount
 *  band + weight (a size-ish spec). Only values actually present flag. */
export function variantFlaggedFormFields(values: ProductFormValues): string[] {
  const flags: string[] = [];
  if (String(values.mrp ?? '').trim()) flags.push('mrp');
  if (String(values.offerPrice ?? '').trim()) flags.push('offer_price');
  if (String(values.costPrice ?? '').trim()) flags.push('cost_price');
  if (String(values.hsnCode ?? '').trim()) flags.push('hsn_code');
  if (String(values.gstRate ?? '').trim()) flags.push('gst_rate');
  if (String(values.discountCategory ?? '').trim()) flags.push('discount_category');
  if (String(values.weight ?? '').trim()) flags.push('weight');
  return flags;
}

/** Everything QuickAddPage needs to flip into variant mode. */
export interface VariantSeed {
  values: ProductFormValues;
  /** Resolved CATEGORIES picker code (SG/FR/...). '' if unresolvable. */
  category: string;
  /** Identity fields kept + LOCKED (chip/lock in the UI). */
  locked: string[];
  /** Attribute names + form-level keys copied but amber until touched. */
  flagged: string[];
  /** Variant-defining fields cleared for entry, in render order — the first
   *  one receives cursor focus. */
  cleared: string[];
  /** Attribute keys with values that were NOT carried over (fail closed). */
  dropped: string[];
  /** Copied values dropped because they are not in the current Catalog
   *  Dictionary options for their field (operator re-picks from dropdown). */
  dictionaryDropped: string[];
  sourceProductId: string;
  sourceSku: string;
  /** "Brand Model" display label of the sibling. */
  sourceLabel: string;
  /** The sibling's photos, offered via the one-click copy button (images are
   *  never copied automatically). */
  sourceImages: string[];
}

const VARIANT_LOCKED_IDENTITY = ['brand_name', 'model_no', 'model_name'];

/** Map an existing product into the variant-mode form seed. Pure (unit-
 *  testable): reads only its inputs + the category field metadata. */
export function productToVariantFormValues(
  product: ProductDoc,
  categoryCode?: string
): VariantSeed {
  const base = productToFormValues(product);
  // Resolve to a CATEGORIES picker code: the caller's explicit code wins, then
  // the product's stored category (canonical values like "SUNGLASS" resolve
  // through the synonym table), else whatever the base mapping carried.
  const category =
    (categoryCode ? inferCategoryCode(categoryCode) : '') ||
    inferCategoryCode(product.category) ||
    base.category;

  const fields = getCategoryFields(category);
  const optionsByField = new Map<string, string[]>();
  fields.forEach((f) => {
    if (f.type === 'select' && Array.isArray(f.options) && f.options.length > 0) {
      optionsByField.set(f.name, f.options);
    }
  });

  const attributes: Record<string, string> = {};
  const flagged: string[] = [];
  const dropped: string[] = [];
  const dictionaryDropped: string[] = [];

  Object.entries(base.attributes).forEach(([k, v]) => {
    const val = String(v ?? '').trim();
    if (!val) return;
    const isLockedIdentity = VARIANT_LOCKED_IDENTITY.includes(k);
    const rule = variantFieldRule(category, k);
    if (rule === 'never' && !isLockedIdentity) {
      dropped.push(k);
      return;
    }
    // Dictionary-governed fields: a copied value outside the CURRENT allowed
    // list is dropped (surfaced via dictionaryDropped) so the operator picks a
    // valid option instead of inheriting a now-invalid value. The locked
    // identity is exempt — the sibling already passed the Brand Master gate.
    if (!isLockedIdentity) {
      const allowed = optionsByField.get(k);
      if (
        allowed &&
        !allowed.some((o) => o.toLowerCase() === val.toLowerCase())
      ) {
        dictionaryDropped.push(k);
        return;
      }
    }
    attributes[k] = v;
    if (rule === 'flag') flagged.push(k);
  });

  // Cleared = the category's declared variant-defining fields, in render
  // order (the first is the focus target). Locked identity never clears.
  const cleared = fields
    .map((f) => f.name)
    .filter(
      (n) =>
        !VARIANT_LOCKED_IDENTITY.includes(n) &&
        variantFieldRule(category, n) === 'never'
    );

  const values: ProductFormValues = {
    category,
    attributes,
    // Fresh-create behaviour: SKU/barcode auto-mint server-side; description
    // starts blank (the AI button drafts a new one for THIS variant).
    description: '',
    hsnCode: base.hsnCode,
    gstRate: base.gstRate,
    weight: base.weight,
    mrp: base.mrp,
    offerPrice: base.offerPrice,
    costPrice: base.costPrice,
    discountCategory: base.discountCategory,
    // A new variant never inherits online flags (same rule as clone).
    syncToShopify: false,
    shopifyTags: [],
    publishPOS: true,
    // Images are NEVER auto-copied — sourceImages powers the one-click button.
    images: [],
  };

  const locked = VARIANT_LOCKED_IDENTITY.filter((n) => attributes[n]);
  const sourceLabel = [
    attributes.brand_name,
    attributes.model_no || attributes.model_name,
  ]
    .filter(Boolean)
    .join(' ');

  return {
    values,
    category,
    locked,
    flagged: [...flagged, ...variantFlaggedFormFields(values)],
    cleared,
    dropped,
    dictionaryDropped,
    sourceProductId: str(product.product_id ?? product.id),
    sourceSku: str(product.sku),
    sourceLabel,
    sourceImages: Array.isArray(base.images) ? base.images : [],
  };
}
