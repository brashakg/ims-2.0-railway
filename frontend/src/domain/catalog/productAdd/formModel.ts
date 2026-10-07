// ============================================================================
// IMS 2.0 - Product Add: form values, validation, create payload, HSN/GST
// ============================================================================

import type { CreateProductPayload } from '../../../services/api/products';
import { resolveGstRate, resolveHsn } from '../../../constants/gstRuntime';
import { getCategoryFields, registryRequiredFields } from './categoryFields';

// Number coercion shared by the CL/LS field mapping. Returns undefined for
// blank / non-numeric so the backend treats the field as absent.
const num = (v: unknown): number | undefined => {
  const n = parseFloat(String(v ?? '').trim());
  return Number.isFinite(n) ? n : undefined;
};

// All the form values either mode collects. Quick Add keeps these in local
// state; the wizard keeps them in its own useState hooks and assembles this
// object at submit time. Either way the SAME mapping below runs.
export interface ProductFormValues {
  category: string;
  attributes: Record<string, string>;
  description?: string;
  hsnCode?: string;
  gstRate: string;
  weight?: string;
  mrp: string;
  offerPrice?: string;
  costPrice?: string;
  discountCategory: string;
  // Uploaded product-image URLs (self-hosted, from productApi.uploadProductImage).
  // Optional so existing callers that don't collect images still type-check;
  // buildProductPayload defaults it to [].
  images?: string[];
  // ---- Review-mode (imported catalog_products doc) extras. ADDITIVE: the
  // create/edit doors ignore them entirely (buildProductPayload untouched).
  /** Display name — the ?review editor maps it to PUT `name` (name + title). */
  name?: string;
  /** Governed product tags — the ?review editor maps them to PUT `tags`. */
  tags?: string[];
}

// Validate the common, mode-agnostic rules: category present, all required
// category fields filled, MRP present + > 0, and (client mirror of the server
// rule) MRP >= offer price. Returns a field->message map; empty means valid.
export function validateProductForm(values: ProductFormValues): Record<string, string> {
  const errors: Record<string, string> = {};

  if (!values.category) {
    errors.category = 'Please select a category';
  }

  if (values.category) {
    // Required-ness comes from the canonical registry when loaded (getCategoryFields
    // overrides each field's `required` flag from the server), else the local
    // CATEGORY_FIELDS fallback flags — so this mirrors the server create gate.
    const fields = getCategoryFields(values.category);
    fields.forEach((field) => {
      if (field.required && !values.attributes[field.name]) {
        errors[field.name] = `${field.label} is required`;
      }
      // Catalog Dictionary mirror of the server gate: a filled select value
      // must be one of the allowed options (case-insensitive — the server
      // canonicalises the casing on save).
      const val = values.attributes[field.name];
      if (
        !errors[field.name] &&
        val &&
        field.type === 'select' &&
        Array.isArray(field.options) &&
        field.options.length > 0 &&
        !field.options.some((o) => o.toLowerCase() === String(val).trim().toLowerCase())
      ) {
        errors[field.name] =
          `"${val}" is not in the allowed list for ${field.label} — pick one from the dropdown (manage values in Settings)`;
      }
    });
    // Belt-and-braces: enforce every registry-required key even if it had no UI
    // metadata to render (getCategoryFields appends those, but a defensive check
    // here guarantees a 422-causing gap is surfaced inline rather than at POST).
    const reqSet = registryRequiredFields(values.category);
    if (reqSet) {
      reqSet.forEach((name) => {
        if (!values.attributes[name] && !errors[name]) {
          const label = fields.find((f) => f.name === name)?.label || name;
          errors[name] = `${label} is required`;
        }
      });
    }
    // HSN: the form has marked it required since the Guided wizard, but the
    // rule lived nowhere -- the Advanced row had no error slot and a blanked
    // code sailed through to the server's category default. It sits in THIS
    // validator so the still-missing row, the section counts and the inline
    // slot all read one list.
    //
    // Gated on resolveHsn(): that returns '' until GET /products/gst-rates has
    // answered, and in exactly that window the page never auto-filled the code
    // and the SERVER fills in its own (the right one). Requiring it there would
    // be an unsatisfiable block on a cold session, not a useful rule.
    if (!String(values.hsnCode || '').trim() && resolveHsn(values.category)) {
      errors.hsn_code = 'Pick an HSN code';
    }
  }

  const mrpNum = parseFloat(values.mrp);
  if (!values.mrp || !Number.isFinite(mrpNum) || mrpNum <= 0) {
    errors.mrp = 'MRP is required and must be greater than 0';
  }

  // MRP >= offer price (the backend blocks MRP < offer at the DB; mirror it
  // here so the user gets an inline error instead of a 4xx).
  if (values.offerPrice) {
    const offerNum = parseFloat(values.offerPrice);
    if (Number.isFinite(offerNum) && Number.isFinite(mrpNum) && offerNum > mrpNum) {
      errors.offer_price = 'Offer price cannot exceed MRP';
    }
  }

  // Discount tier is no longer picked per product (owner rule: it is set
  // brand-wise in the Brand Master + forced category-wise). The backend
  // derives it (category force > brand tier); when it cannot, the product
  // saves as DRAFT with the gap named -- visible, never silently MASS.

  return errors;
}

// Labels for the keys validateProductForm can name that are NOT category
// attributes (they live on the form itself, not in the registry).
const FORM_LEVEL_LABELS: Record<string, string> = {
  category: 'Category',
  mrp: 'MRP',
  offer_price: 'Offer Price',
  hsn_code: 'HSN Code',
};

// The human label for any key the validator / review card can name: form-level
// keys first, then the category registry (the same list the form renders
// from), then a spaced-out key for a legacy attribute the registry no longer
// carries (a cloned older doc). One helper, so a field is never named two
// ways on one screen.
export function fieldLabelFor(category: string | null | undefined, name: string): string {
  return (
    FORM_LEVEL_LABELS[name] ||
    getCategoryFields(category).find((f) => f.name === name)?.label ||
    name.replace(/_/g, ' ')
  );
}

// Build the exact CreateProductPayload the wizard's handleSubmit produced.
// Centralised so Quick Add and Guided Add POST byte-identical payloads. The
// API contract (productApi.createProduct) is unchanged.
export function buildProductPayload(values: ProductFormValues): CreateProductPayload {
  const { category, attributes } = values;

  // ProductCreate requires top-level brand/model. The dynamic form collects
  // these under category-specific attribute names (brand_name, model_no /
  // model_name); map them here, and nothing else: a category with no model
  // (Optical Lens) sends a blank one and the server's one SKU minter
  // (product_master.build_sku, which the Review preview calls too) decides
  // what stands in for it. SKU is NOT a form field: the backend mints the
  // clean semantic SKU (product_master.mint_unique_sku) whenever none is sent,
  // so we OMIT it unless the operator explicitly supplied one (e.g. a legacy /
  // imported SKU under attributes.sku). We no longer fabricate a Date.now() SKU
  // — that ugly client SKU used to override the backend's clean one.
  const brand = String(attributes.brand_name || attributes.brand || '').trim();
  const model = String(attributes.model_no || attributes.model_name || '').trim();
  const suppliedSku = String(attributes.sku || '').trim();

  // Contact lenses: map CL attribute fields onto the top-level CL identity
  // fields the backend models. Only sent for CL.
  const isCL = category === 'CL';
  const clFields = isCL
    ? {
        cl_series: String(attributes.cl_series || '').trim() || undefined,
        modality: String(attributes.modality || '').trim() || undefined,
        base_curve: num(attributes.base_curve),
        diameter: num(attributes.diameter),
        cl_power: num(attributes.power),
        cl_cyl: num(attributes.cl_cyl),
        cl_axis: num(attributes.cl_axis),
        cl_add: num(attributes.cl_add),
        pack_size: num(attributes.pack),
      }
    : {};

  // Spectacle lenses: map stock-power fields onto the top-level lens power
  // identity (drives the SPH x CYL Power Grid). Only sent for LS.
  const isLens = category === 'LS';
  const lsFields = isLens
    ? {
        sph: num(attributes.sph),
        cyl: num(attributes.cyl),
        axis: num(attributes.axis),
        add: num(attributes.add),
      }
    : {};

  const mrp = parseFloat(values.mrp);

  return {
    category,
    // Only send a SKU when the operator explicitly supplied one; otherwise omit
    // it entirely so the backend mints the canonical SKU.
    ...(suppliedSku ? { sku: suppliedSku } : {}),
    brand,
    model,
    attributes,
    description: values.description || undefined,
    // No HSN chosen -> send none, and the server fills in the canonical code
    // for the category (routers/products._resolve_hsn_or_400 ->
    // gst_rates.hsn_for_category). A contact lens used to get an 8-DIGIT
    // '90013000' written in here instead; the rest of the app spells that same
    // HSN '900130' (6-digit, owner 2026-07-05 -- the 4/8-digit variants were
    // dropped app-wide), and two spellings of one product's HSN split it across
    // two rows of the HSN-wise summary on the invoice and in GSTR-1. The branch
    // was unreachable while values.hsnCode was always pre-filled locally; it
    // became live the moment the HSN prefill started coming from the server.
    hsn_code: values.hsnCode || undefined,
    // Flat fields. Offer price falls back to MRP when left blank. Stock qty is
    // intentionally omitted: inventory is created via GRN, not at create time.
    mrp,
    offer_price: values.offerPrice ? parseFloat(values.offerPrice) : mrp,
    gst_rate: parseFloat(values.gstRate),
    ...clFields,
    ...lsFields,
    weight: values.weight ? parseFloat(values.weight) : undefined,
    cost_price: values.costPrice ? parseFloat(values.costPrice) : undefined,
    // Only sent when explicitly set (template/clone/legacy); otherwise OMITTED
    // so the backend derives the tier from Settings (category force > Brand
    // Master brand tier).
    ...(values.discountCategory ? { discount_category: values.discountCategory } : {}),
    // Uploaded image URLs (durably stored + served by the backend). Empty when
    // the operator didn't add any.
    images: Array.isArray(values.images) ? values.images : [],
  };
}

// The auto HSN/GST a category prefills at the cataloguing door.
//
// BOTH numbers now come from the server (GET /products/gst-rates): the HSN off
// the canonical category -> HSN table, and then the rate off THAT HSN -- the
// same order the save itself uses, where the HSN settles the rate
// (product_master.normalise_payload). Before this, the HSN was read from a
// hand-copied table on the frontend that had drifted: smartglasses prefilled
// 900410, the SUNGLASSES code, and since a client-supplied hsn_code wins at the
// create door, that wrong code is what got stored on the product and printed on
// its documents.
//
// Before the endpoint has answered, hsnCode is '' -- the form then sends no
// hsn_code and the server fills in its own, which is the correct one.
export function resolveHsnGst(category: string): { hsnCode: string; gstRate: string } {
  const hsnCode = resolveHsn(category);
  return { hsnCode, gstRate: resolveGstRate(category, hsnCode).toString() };
}

// May the Add-Product form quote `resolveHsnGst(category).gstRate` as the rate
// this product will carry?
//
// Only while the HSN on the form is still the one the category implies. The
// rate on screen comes from the CATEGORY; the rate that gets STORED comes from
// the HSN, server-side (product_master.normalise_payload ->
// gst_rates.resolve_gst_rate_strict). Those are the same number for the HSN a
// category auto-fills, and only then -- so the moment the cataloguer picks a
// different code the box must stop quoting a number and say the HSN settles it
// on save.
export function hsnImpliesCategoryRate(category: string, hsnCode: string): boolean {
  return !hsnCode || !category || hsnCode === resolveHsnGst(category).hsnCode;
}
