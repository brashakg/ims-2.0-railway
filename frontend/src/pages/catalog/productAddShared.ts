// ============================================================================
// IMS 2.0 - Product Add: shared field config + payload mapping
// ============================================================================
// Single source of truth for the product-add CATEGORIES list, the
// category-specific field config, and the payload-building logic. Imported by
// BOTH the fast one-screen "Quick Add" (QuickAddPage) and the step-by-step
// "Guided Add" wizard (AddProductPage) so the two modes stay byte-identical in
// what fields they collect and what payload they POST. Do NOT redefine these
// fields elsewhere — extend them here.

import type { CreateProductPayload } from '../../services/api/products';
import type { AutopilotCandidate } from '../../services/api/catalogAutopilot';
import { getHSNByCategory, getGSTRateByCategory } from '../../constants/gst';

// Product categories with display names + emoji (used in the category picker).
export const CATEGORIES = [
  { code: 'SG', name: 'Sunglass', icon: '🕶️' },
  { code: 'FR', name: 'Frame', icon: '👓' },
  { code: 'CL', name: 'Contact Lens', icon: '👁️' },
  { code: 'LS', name: 'Optical Lens', icon: '🔍' },
  { code: 'RG', name: 'Reading Glasses', icon: '📖' },
  { code: 'WT', name: 'Wrist Watch', icon: '⌚' },
  { code: 'CK', name: 'Clock', icon: '🕐' },
  { code: 'HA', name: 'Hearing Aid', icon: '🦻' },
  { code: 'ACC', name: 'Accessories', icon: '🎒' },
  { code: 'SMTSG', name: 'Smart Sunglass', icon: '🥽' },
  { code: 'SMTFR', name: 'Smart Glasses', icon: '🤓' },
  { code: 'SMTWT', name: 'Smart Watch', icon: '⌚' },
] as const;

export interface CategoryField {
  name: string;
  label: string;
  type: 'text' | 'number' | 'select' | 'date';
  required: boolean;
  options?: string[];
  placeholder?: string;
}

// Category-specific fields configuration. Identical to the original wizard's
// CATEGORY_FIELDS — reused verbatim so no field is lost in either mode.
export const CATEGORY_FIELDS: Record<string, CategoryField[]> = {
  SG: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Oakley', 'Vogue', 'Prada', 'Gucci', 'Titan', 'Fastrack', 'Lenskart', 'Vincent Chase'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
  ],
  FR: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Oakley', 'Vogue', 'Prada', 'Titan', 'Fastrack', 'Lenskart', 'Vincent Chase', 'John Jacobs'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
  ],
  CL: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Bausch & Lomb', 'Johnson & Johnson', 'Alcon', 'CooperVision', 'Acuvue'] },
    { name: 'cl_series', label: 'Series', type: 'text', required: false, placeholder: 'e.g. Acuvue Oasys' },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true },
    { name: 'modality', label: 'Modality', type: 'select', required: false, options: ['DAILY', 'FORTNIGHTLY', 'MONTHLY', 'QUARTERLY', 'YEARLY', 'COLOR'] },
    { name: 'colour_name', label: 'Colour Name', type: 'text', required: false },
    { name: 'power', label: 'Power (SPH)', type: 'text', required: true, placeholder: '-6.00 to +6.00' },
    { name: 'base_curve', label: 'Base Curve (BC)', type: 'number', required: false, placeholder: '8.6' },
    { name: 'diameter', label: 'Diameter (DIA)', type: 'number', required: false, placeholder: '14.2' },
    { name: 'cl_cyl', label: 'Cylinder (toric)', type: 'number', required: false },
    { name: 'cl_axis', label: 'Axis (toric, 0-180)', type: 'number', required: false },
    { name: 'cl_add', label: 'Add (multifocal)', type: 'number', required: false },
    { name: 'pack', label: 'Pack Size', type: 'select', required: false, options: ['1', '3', '6', '30', '90'] },
    // Contact lenses are medical devices with a shelf life -- the canonical
    // product-create registry (step-9) hard-requires expiry_date at every door,
    // so the wizard must block submit inline rather than 422 after POST.
    { name: 'expiry_date', label: 'Expiry Date', type: 'date', required: true },
  ],
  LS: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Essilor', 'Zeiss', 'Hoya', 'Crizal', 'Kodak', 'Nikon', 'Rodenstock'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'index', label: 'Index', type: 'select', required: true, options: ['1.50', '1.56', '1.59', '1.60', '1.67', '1.74'] },
    { name: 'coating', label: 'Coating', type: 'select', required: true, options: ['UC', 'HC', 'ARC', 'Blue Cut', 'Photochromic', 'Transitions', 'Polarized'] },
    { name: 'lens_category', label: 'Lens Category', type: 'select', required: false, options: ['Single Vision', 'Bifocal', 'Progressive', 'Office', 'Driving'] },
    // Stock-power identity -> drives the SPH x CYL Power Grid. Leave blank for
    // made-to-order lenses; fill for ready-made stock trays.
    { name: 'sph', label: 'SPH (stock power)', type: 'number', required: false, placeholder: 'e.g. -2.00' },
    { name: 'cyl', label: 'CYL (stock power)', type: 'number', required: false, placeholder: 'e.g. -0.50' },
    { name: 'axis', label: 'Axis (0-180)', type: 'number', required: false },
    { name: 'add', label: 'Add (bifocal/progressive)', type: 'number', required: false },
    { name: 'add_on_1', label: 'Add-On 1', type: 'text', required: false },
    { name: 'add_on_2', label: 'Add-On 2', type: 'text', required: false },
    { name: 'add_on_3', label: 'Add-On 3', type: 'text', required: false },
  ],
  RG: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Titan', 'Fastrack', 'Lenskart', 'Vincent Chase'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'power', label: 'Power', type: 'select', required: false, options: ['+1.00', '+1.25', '+1.50', '+1.75', '+2.00', '+2.25', '+2.50', '+2.75', '+3.00', '+3.50'] },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
  ],
  WT: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Titan', 'Fastrack', 'Casio', 'Fossil', 'Timex', 'Sonata', 'HMT'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'dial_colour', label: 'Dial Colour', type: 'text', required: false },
    { name: 'belt_colour', label: 'Belt Colour', type: 'text', required: false },
    { name: 'dial_size', label: 'Dial Size (mm)', type: 'number', required: false },
    { name: 'belt_size', label: 'Belt Size (mm)', type: 'number', required: false },
    { name: 'watch_category', label: 'Watch Category', type: 'select', required: false, options: ['Analog', 'Digital', 'Analog-Digital', 'Chronograph', 'Automatic', 'Quartz'] },
  ],
  CK: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Titan', 'Casio', 'Seiko', 'Ajanta', 'Generic'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'dial_colour', label: 'Dial Colour', type: 'text', required: false },
    { name: 'body_colour', label: 'Body Colour', type: 'text', required: false },
    { name: 'dial_size', label: 'Dial Size (inches)', type: 'number', required: false },
    { name: 'battery_size', label: 'Battery Size', type: 'text', required: false },
    { name: 'clock_category', label: 'Clock Category', type: 'select', required: false, options: ['Wall Clock', 'Table Clock', 'Alarm Clock', 'Desk Clock', 'Decorative'] },
  ],
  HA: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Phonak', 'Signia', 'Widex', 'Oticon', 'ReSound', 'Starkey'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'serial_no', label: 'Serial No', type: 'text', required: false },
    { name: 'machine_capacity', label: 'Machine Capacity', type: 'select', required: false, options: ['Mild', 'Moderate', 'Severe', 'Profound'] },
    { name: 'machine_type', label: 'Machine Type', type: 'select', required: false, options: ['BTE', 'ITE', 'ITC', 'CIC', 'RIC', 'Body Worn'] },
  ],
  ACC: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Generic', 'Ray-Ban', 'Oakley', 'Titan'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true },
    { name: 'accessory_type', label: 'Accessory Type', type: 'select', required: false, options: ['Case', 'Cloth', 'Chain', 'Nose Pad', 'Temple Tip', 'Screw Kit', 'Spray', 'Other'] },
    { name: 'size', label: 'Size', type: 'text', required: false },
    { name: 'pack', label: 'Pack Size', type: 'number', required: false },
    { name: 'expiry_date', label: 'Expiry Date', type: 'date', required: false },
  ],
  SMTSG: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Bose', 'Amazon', 'Meta'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
    { name: 'year_of_launch', label: 'Year of Launch', type: 'number', required: false },
  ],
  SMTFR: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Meta', 'Amazon', 'Google'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
    { name: 'year_of_launch', label: 'Year of Launch', type: 'number', required: false },
  ],
  SMTWT: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Apple', 'Samsung', 'Fitbit', 'Garmin', 'Amazfit', 'Noise', 'boAt'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'body_colour', label: 'Body Colour', type: 'text', required: false },
    { name: 'belt_colour', label: 'Belt Colour', type: 'text', required: false },
    { name: 'dial_size', label: 'Dial Size (mm)', type: 'number', required: false },
    { name: 'belt_size', label: 'Belt Size (mm)', type: 'number', required: false },
    { name: 'year_of_launch', label: 'Year of Launch', type: 'number', required: false },
  ],
};

export const categoryName = (code: string | null | undefined): string =>
  CATEGORIES.find((c) => c.code === code)?.name ?? '';

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
  syncToShopify: boolean;
  shopifyTags: string[];
  publishPOS: boolean;
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
    const fields = CATEGORY_FIELDS[values.category] || [];
    fields.forEach((field) => {
      if (field.required && !values.attributes[field.name]) {
        errors[field.name] = `${field.label} is required`;
      }
    });
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

  return errors;
}

// Build the exact CreateProductPayload the wizard's handleSubmit produced.
// Centralised so Quick Add and Guided Add POST byte-identical payloads. The
// API contract (productApi.createProduct) is unchanged.
export function buildProductPayload(values: ProductFormValues): CreateProductPayload {
  const { category, attributes } = values;

  // ProductCreate requires top-level sku/brand/model. The dynamic form collects
  // these under category-specific attribute names (brand_name, model_no /
  // model_name); map them here. SKU is not a form field, so generate a stable
  // unique one when absent.
  const brand = String(attributes.brand_name || attributes.brand || '').trim();
  const model = String(
    attributes.model_no || attributes.model_name || attributes.subbrand || 'STD'
  ).trim();
  const sku =
    String(attributes.sku || attributes.barcode || '').trim() ||
    `${category}-${(brand || 'GEN').replace(/\s+/g, '').slice(0, 6)}-${Date.now().toString(36)}`.toUpperCase();

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
    sku,
    brand,
    model,
    attributes,
    description: values.description || undefined,
    // India: contact lenses default to HSN 9001 (90013000) at 5% GST under
    // GST 2.0 when an HSN is not explicitly chosen.
    hsn_code: values.hsnCode || (isCL ? '90013000' : undefined),
    // Flat fields. Offer price falls back to MRP when left blank. Stock qty is
    // intentionally omitted: inventory is created via GRN, not at create time.
    mrp,
    offer_price: values.offerPrice ? parseFloat(values.offerPrice) : mrp,
    gst_rate: parseFloat(values.gstRate),
    ...clFields,
    ...lsFields,
    weight: values.weight ? parseFloat(values.weight) : undefined,
    cost_price: values.costPrice ? parseFloat(values.costPrice) : undefined,
    discount_category: values.discountCategory,
    images: [],
    shopify: {
      // Kept for future vendor sync (NEXUS pushes POS stock -> Shopify). We
      // don't render our own storefront.
      sync_to_shopify: values.syncToShopify,
      shopify_tags: values.shopifyTags,
      publish_to_pos: values.publishPOS,
    },
  };
}

// Resolve the auto HSN/GST for a category. Mirrors the wizard's useEffect:
// HSN code comes from getHSNByCategory, the RATE from getGSTRateByCategory (so
// categories that share a 4-digit HSN heading at different rates still get the
// correct per-category rate).
export function resolveHsnGst(category: string, use6Digit: boolean): { hsnCode: string; gstRate: string } {
  const hsnData = getHSNByCategory(category, use6Digit);
  return {
    hsnCode: hsnData ? hsnData.code : '',
    gstRate: getGSTRateByCategory(category).toString(),
  };
}

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
const str = (v: unknown): string => {
  if (v === null || v === undefined) return '';
  const s = String(v);
  return s === 'NaN' ? '' : s;
};

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
    gstRate: str(product.gst_rate) || '18',
    weight: str(product.weight),
    mrp: str(product.mrp),
    offerPrice: str(product.offer_price),
    costPrice: str(product.cost_price),
    discountCategory: str(product.discount_category) || 'MASS',
    // Online flags are NOT cloned: a new SKU shouldn't inherit Shopify sync.
    syncToShopify: false,
    shopifyTags: [],
    publishPOS: true,
  };
}

// ============================================================================
// Catalog Autopilot -> Add Product prefill (the "payoff").
// ----------------------------------------------------------------------------
// Turn an approved/scraped/AI-enriched Autopilot candidate into ProductFormValues
// so the operator can hit "Create product from this" on a candidate and land on
// the Quick Add screen with brand/model/category/description/HSN/GST already
// filled. Pure + side-effect-free (the sessionStorage handoff is separate, below)
// so it is unit-testable.

// Free-text/category-ish label -> the Quick Add CATEGORIES code (SG/FR/CL/...).
// The candidate's `category` (and, as a fallback, its specs.category) can be a
// human label ("Sunglasses"), an enum ("SUNGLASS"), or absent. We normalise to
// alphanumerics and match against a small synonym table; an unknown value
// returns '' so the user simply picks the category (nothing is mis-filed).
const CATEGORY_CODE_SYNONYMS: Record<string, string> = {
  // Sunglasses
  SUNGLASS: 'SG', SUNGLASSES: 'SG', SG: 'SG', SHADES: 'SG',
  // Frames
  FRAME: 'FR', FRAMES: 'FR', FR: 'FR', EYEGLASSFRAME: 'FR', SPECTACLEFRAME: 'FR',
  OPTICALFRAME: 'FR', EYEGLASSES: 'FR', SPECTACLES: 'FR',
  // Contact lenses
  CONTACTLENS: 'CL', CONTACTLENSES: 'CL', CL: 'CL', CONTACTS: 'CL',
  COLOREDCONTACTLENS: 'CL', COLOURCONTACTS: 'CL',
  // Optical (spectacle) lenses
  OPTICALLENS: 'LS', LENS: 'LS', LENSES: 'LS', LS: 'LS', RXLENS: 'LS',
  RXLENSES: 'LS', EYEGLASSLENS: 'LS', SPECTACLELENS: 'LS',
  // Reading glasses
  READINGGLASSES: 'RG', RG: 'RG', READERS: 'RG', READER: 'RG',
  // Watches / clocks
  WATCH: 'WT', WATCHES: 'WT', WRISTWATCH: 'WT', WRISTWATCHES: 'WT', WT: 'WT',
  CLOCK: 'CK', CLOCKS: 'CK', WALLCLOCK: 'CK', CK: 'CK',
  // Hearing aids
  HEARINGAID: 'HA', HEARINGAIDS: 'HA', HA: 'HA',
  // Accessories
  ACCESSORY: 'ACC', ACCESSORIES: 'ACC', ACC: 'ACC',
  // Smart eyewear / watches
  SMARTSUNGLASS: 'SMTSG', SMARTSUNGLASSES: 'SMTSG', SMTSG: 'SMTSG',
  SMARTGLASSES: 'SMTFR', SMARTGLASS: 'SMTFR', SMTFR: 'SMTFR',
  SMARTWATCH: 'SMTWT', SMARTWATCHES: 'SMTWT', SMTWT: 'SMTWT',
};

export function inferCategoryCode(raw: unknown): string {
  const key = String(raw ?? '').replace(/[^A-Za-z0-9]/g, '').toUpperCase();
  if (!key) return '';
  if (CATEGORY_CODE_SYNONYMS[key]) return CATEGORY_CODE_SYNONYMS[key];
  // Direct match against a real CATEGORIES code (e.g. already 'SG').
  if (CATEGORIES.some((c) => c.code === key)) return key;
  return '';
}

// Coerce the candidate's specs (Record<string, unknown>) into the string->string
// shape the form's review/extra display expects, dropping empty values.
function specsToStrings(specs: Record<string, unknown> | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!specs) return out;
  Object.entries(specs).forEach(([k, v]) => {
    if (v === null || v === undefined) return;
    const s = String(v).trim();
    if (s) out[k] = s;
  });
  return out;
}

// Map an Autopilot candidate -> ProductFormValues for prefill. Brand + model land
// in the attribute keys the form reads (brand_name; both model_no AND model_name
// so whichever the chosen category renders is populated). Category is inferred;
// HSN/GST prefer the candidate's suggestions, else resolve from the category.
// Pricing is intentionally left blank -- the operator sets MRP/cost (Autopilot
// is a catalog/spec source, not a price source).
export function autopilotCandidateToFormValues(c: AutopilotCandidate): ProductFormValues {
  const category = inferCategoryCode(c.category ?? (c.specs ? (c.specs as Record<string, unknown>).category : undefined));

  const brand = str(c.brand);
  const model = str(c.model);

  // Seed attributes from the candidate's specs (so spec key/values aren't lost),
  // then layer identity on top. Spec keys are free-form scraped labels; the form
  // only renders the category's declared fields, so extra keys are harmless
  // passthrough that buildProductPayload forwards under `attributes`.
  const attributes: Record<string, string> = specsToStrings(c.specs as Record<string, unknown> | undefined);
  // Drop a spec-level "category" label so it doesn't masquerade as a form field.
  delete attributes.category;
  if (brand) attributes.brand_name = brand;
  if (model) {
    attributes.model_no = model;
    attributes.model_name = model;
  }

  // Description: prefer the full description, fall back to the USP one-liner.
  const description = str(c.description) || str(c.usp);

  // HSN/GST: prefer the candidate's explicit suggestions; else, if we resolved a
  // category, fall back to that category's canonical 4-digit HSN + rate; else
  // leave blank so the Quick Add category-change autofill fills them in.
  let hsnCode = str(c.suggested_hsn);
  let gstRate =
    c.suggested_gst_rate !== null && c.suggested_gst_rate !== undefined && Number.isFinite(Number(c.suggested_gst_rate))
      ? String(c.suggested_gst_rate)
      : '';
  if ((!hsnCode || !gstRate) && category) {
    const resolved = resolveHsnGst(category, false);
    if (!hsnCode) hsnCode = resolved.hsnCode;
    if (!gstRate) gstRate = resolved.gstRate;
  }

  return {
    category,
    attributes,
    description,
    hsnCode,
    gstRate: gstRate || '18',
    weight: '',
    // No price signal from Autopilot -- operator fills these in.
    mrp: '',
    offerPrice: '',
    costPrice: '',
    discountCategory: 'MASS',
    // A new SKU shouldn't auto-sync online.
    syncToShopify: false,
    shopifyTags: [],
    publishPOS: true,
  };
}

// ----------------------------------------------------------------------------
// sessionStorage handoff for the Autopilot -> Quick Add prefill. We pass the
// candidate via sessionStorage (not router state, which is lost when the lazily
// loaded /catalog/add shell mounts, and not the URL, which can't carry specs/
// image arrays). CatalogAutopilotPage stashes the candidate then navigates to
// /catalog/add?prefill=autopilot; QuickAddPage reads + clears it on mount.
export const AUTOPILOT_PREFILL_KEY = 'ims.autopilot.prefill';
export const AUTOPILOT_PREFILL_PARAM = 'prefill';
export const AUTOPILOT_PREFILL_VALUE = 'autopilot';

export function stashAutopilotPrefill(c: AutopilotCandidate): boolean {
  try {
    window.sessionStorage.setItem(AUTOPILOT_PREFILL_KEY, JSON.stringify(c));
    return true;
  } catch {
    return false;
  }
}

// Read + remove the stashed candidate (one-shot). Returns null if absent/invalid.
export function takeAutopilotPrefill(): AutopilotCandidate | null {
  try {
    const raw = window.sessionStorage.getItem(AUTOPILOT_PREFILL_KEY);
    if (!raw) return null;
    window.sessionStorage.removeItem(AUTOPILOT_PREFILL_KEY);
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? (parsed as AutopilotCandidate) : null;
  } catch {
    return null;
  }
}
