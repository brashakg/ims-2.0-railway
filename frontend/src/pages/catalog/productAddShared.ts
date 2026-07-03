// ============================================================================
// IMS 2.0 - Product Add: shared field config + payload mapping
// ============================================================================
// Single source of truth for the product-add CATEGORIES list, the
// category-specific field config, and the payload-building logic. Imported by
// BOTH the fast one-screen "Quick Add" (QuickAddPage) and the step-by-step
// "Guided Add" wizard (AddProductPage) so the two modes stay byte-identical in
// what fields they collect and what payload they POST. Do NOT redefine these
// fields elsewhere — extend them here.

import type {
  CreateProductPayload,
  CategoryRegistryEntry,
  CategoryRegistryField,
} from '../../services/api/products';
import { productApi } from '../../services/api/products';
import type { AutopilotCandidate } from '../../services/api/catalogAutopilot';
import { mapSpecsToCategoryFields } from './autopilotSpecMap';
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
  { code: 'SMTSG', name: 'Smartglasses (Sunglass)', icon: '🥽' },
  { code: 'SMTFR', name: 'Smartglasses', icon: '🤓' },
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

// Category-specific fields configuration. This is now UI METADATA ONLY (labels,
// input types, select options, placeholders). The authoritative REQUIRED/optional
// flag for each field comes from the backend canonical registry
// (GET /products/categories -> product_master CATEGORY_SPECS) at runtime via
// getCategoryFields(); the `required` booleans hard-coded below are the offline
// fallback used only until the registry has loaded (or if the fetch fails). This
// keeps the three entry doors in lockstep with the server's create-time
// enforcement and removes the drift that previously let the FE and backend
// disagree on which fields a category requires. Do NOT redefine these elsewhere.
export const CATEGORY_FIELDS: Record<string, CategoryField[]> = {
  SG: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Oakley', 'Vogue', 'Prada', 'Gucci', 'Titan', 'Fastrack', 'Lenskart', 'Vincent Chase'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'label', label: 'Label', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'full_model_no', label: 'Full Model No', type: 'text', required: false },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'shape', label: 'Shape', type: 'select', required: false, options: ['Rectangle', 'Square', 'Round', 'Oval', 'Cat-Eye', 'Aviator', 'Wayfarer', 'Clubmaster', 'Oversized', 'Geometric', 'Wrap'] },
    { name: 'frame_color', label: 'Frame Colour', type: 'text', required: false },
    { name: 'temple_color', label: 'Temple Colour', type: 'text', required: false },
    { name: 'frame_material', label: 'Frame Material', type: 'text', required: false },
    { name: 'temple_material', label: 'Temple Material', type: 'text', required: false },
    { name: 'frame_type', label: 'Frame Type', type: 'select', required: false, options: ['Full Rim', 'Half Rim', 'Rimless'] },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
    { name: 'lens_colour', label: 'Lens Colour', type: 'text', required: false },
    { name: 'lens_material', label: 'Lens Material', type: 'select', required: false, options: ['CR-39', 'Polycarbonate', 'Glass', 'Trivex', 'Nylon'] },
    { name: 'polarization', label: 'Polarization', type: 'select', required: false, options: ['Yes', 'No'] },
    { name: 'uv_protection', label: 'UV Protection', type: 'select', required: false, options: ['UV400', 'UV380', 'Polarized', 'None'] },
    { name: 'tint', label: 'Tint', type: 'text', required: false },
    { name: 'lens_usp', label: 'Lens USP', type: 'text', required: false },
    { name: 'product_usp', label: 'Product USP', type: 'text', required: false },
    { name: 'usp_1', label: 'Product USP 1', type: 'text', required: false },
    { name: 'usp_2', label: 'Product USP 2', type: 'text', required: false },
    { name: 'usp', label: 'USP', type: 'text', required: false },
    { name: 'gender', label: 'Gender', type: 'select', required: false, options: ['Men', 'Women', 'Unisex', 'Kids'] },
    { name: 'gender_label', label: 'Gender Label', type: 'text', required: false },
    { name: 'country_of_origin', label: 'Country of Origin', type: 'text', required: false },
    { name: 'warranty', label: 'Warranty', type: 'text', required: false },
    { name: 'upc', label: 'UPC (mfr)', type: 'text', required: false },
    { name: 'gtin', label: 'GTIN (mfr)', type: 'text', required: false },
  ],
  FR: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Oakley', 'Vogue', 'Prada', 'Titan', 'Fastrack', 'Lenskart', 'Vincent Chase', 'John Jacobs'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'label', label: 'Label', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'full_model_no', label: 'Full Model No', type: 'text', required: false },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'shape', label: 'Shape', type: 'select', required: false, options: ['Rectangle', 'Square', 'Round', 'Oval', 'Cat-Eye', 'Aviator', 'Wayfarer', 'Clubmaster', 'Oversized', 'Geometric', 'Wrap'] },
    { name: 'frame_color', label: 'Frame Colour', type: 'text', required: false },
    { name: 'temple_color', label: 'Temple Colour', type: 'text', required: false },
    { name: 'frame_material', label: 'Frame Material', type: 'text', required: false },
    { name: 'temple_material', label: 'Temple Material', type: 'text', required: false },
    { name: 'frame_type', label: 'Frame Type', type: 'select', required: false, options: ['Full Rim', 'Half Rim', 'Rimless'] },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Width (mm)', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
    { name: 'blue_cut_lens', label: 'Blue-Cut Lens', type: 'select', required: false, options: ['Yes', 'No'] },
    { name: 'lens_usp', label: 'Lens USP', type: 'text', required: false },
    { name: 'product_usp', label: 'Product USP', type: 'text', required: false },
    { name: 'usp_1', label: 'Product USP 1', type: 'text', required: false },
    { name: 'usp_2', label: 'Product USP 2', type: 'text', required: false },
    { name: 'usp', label: 'USP', type: 'text', required: false },
    { name: 'gender', label: 'Gender', type: 'select', required: false, options: ['Men', 'Women', 'Unisex', 'Kids'] },
    { name: 'gender_label', label: 'Gender Label', type: 'text', required: false },
    { name: 'country_of_origin', label: 'Country of Origin', type: 'text', required: false },
    { name: 'warranty', label: 'Warranty', type: 'text', required: false },
    { name: 'upc', label: 'UPC (mfr)', type: 'text', required: false },
    { name: 'gtin', label: 'GTIN (mfr)', type: 'text', required: false },
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

// ============================================================================
// Canonical category registry — single source of truth for required fields.
// ----------------------------------------------------------------------------
// The backend GET /products/categories endpoint returns, per canonical category,
// the required/optional attribute fields the create gate enforces. We fetch it
// ONCE (module-level promise cache) and let all three product-entry doors derive
// their required-ness from it, so the FE markers + block-submit always match the
// server. Field UI metadata (labels, input types, options) still comes from the
// local CATEGORY_FIELDS; only the `required` flag is overridden by the registry,
// and any registry-required field absent from the local metadata is appended as a
// text input (so a server-required field can never be invisible / unfilled).

// Maps a CATEGORIES picker code (SG/FR/CL/...) to the registry entry. The
// registry keys on `sku_prefix` (FR, SG, ...). A few FE codes need explicit
// aliasing: CL -> CONTACT_LENS, SMTSG (smart sunglass) -> SMARTGLASSES.
const FE_CODE_TO_CANONICAL: Record<string, string> = {
  SG: 'SUNGLASS',
  FR: 'FRAME',
  CL: 'CONTACT_LENS',
  LS: 'OPTICAL_LENS',
  RG: 'READING_GLASSES',
  WT: 'WATCH',
  CK: 'WALL_CLOCK',
  HA: 'HEARING_AID',
  ACC: 'ACCESSORIES',
  SMTSG: 'SMARTGLASSES',
  SMTFR: 'SMARTGLASSES',
  SMTWT: 'SMARTWATCH',
};

let _registryPromise: Promise<CategoryRegistryEntry[]> | null = null;
let _registryByCanonical: Record<string, CategoryRegistryEntry> = {};

// Resolve a CATEGORIES picker code to its registry entry (once loaded).
function registryEntryForCode(code: string | null | undefined): CategoryRegistryEntry | undefined {
  if (!code) return undefined;
  const canonical = FE_CODE_TO_CANONICAL[code] || code;
  return _registryByCanonical[canonical] || _registryByCanonical[code];
}

// Load + cache the canonical category registry. Idempotent: concurrent callers
// share the same in-flight promise; a successful load is cached for the session.
// On failure the promise cache is cleared so a later call can retry, and the
// caller falls back to the local CATEGORY_FIELDS `required` flags.
export async function loadCategoryRegistry(): Promise<CategoryRegistryEntry[]> {
  if (_registryPromise) return _registryPromise;
  _registryPromise = productApi
    .getCategoryRegistry()
    .then((res) => {
      const cats = res?.categories ?? [];
      const byCanonical: Record<string, CategoryRegistryEntry> = {};
      cats.forEach((c) => {
        if (c?.code) byCanonical[c.code] = c;
      });
      _registryByCanonical = byCanonical;
      return cats;
    })
    .catch((err) => {
      // Clear so a later mount can retry; doors fall back to local required flags.
      _registryPromise = null;
      throw err;
    });
  return _registryPromise;
}

// True once the registry has loaded (entries cached). Doors can render either
// way — this just decides whether required-ness comes from the server or the
// local fallback flags.
export function isCategoryRegistryLoaded(): boolean {
  return Object.keys(_registryByCanonical).length > 0;
}

// The registry's required-field key SET for a picker code, or null when the
// registry hasn't loaded / doesn't know the code (caller then uses local flags).
export function registryRequiredFields(code: string | null | undefined): Set<string> | null {
  const entry = registryEntryForCode(code);
  if (!entry) return null;
  return new Set(entry.required_fields || []);
}

// The render-ready field list for a category, with `required` flags sourced from
// the canonical registry when loaded (else the local fallback flags). Any
// registry-required field with no local UI metadata is appended as a text input
// so a server-required field is always visible + collectible.
export function getCategoryFields(code: string | null | undefined): CategoryField[] {
  if (!code) return [];
  const local = CATEGORY_FIELDS[code] || [];
  const entry = registryEntryForCode(code);
  if (!entry) return local; // registry not loaded — use local metadata as-is.

  const requiredSet = new Set(entry.required_fields || []);
  const optionalSet = new Set(entry.optional_fields || []);
  const known = new Set(local.map((f) => f.name));

  // Catalog Dictionary: per-field allowed values the server attached to the
  // registry (Settings -> Catalog Dictionary; brand_name = Brand Master).
  // When the server sends options for a field they REPLACE any local
  // hardcoded list and force the field to render as a restricted select —
  // the owner's saved values are the only choosable ones. brand_name is
  // special: the server sends it even when EMPTY (empty Brand Master), so an
  // empty select + "add brands in Settings" hint shows instead of the stale
  // hardcoded brand list.
  const serverOptions = new Map<string, string[]>();
  (entry.fields || []).forEach((rf: CategoryRegistryField) => {
    if (Array.isArray(rf.options) && (rf.options.length > 0 || rf.name === 'brand_name')) {
      serverOptions.set(rf.name, rf.options);
    }
  });

  // 1) Override the required flag on every local field from the registry. A field
  // the registry lists (required OR optional) keeps its local UI metadata; a
  // local field the registry does not mention keeps its own `required` flag
  // (e.g. extra UI-only fields like lens_size that the spine doesn't gate on).
  const merged: CategoryField[] = local.map((f) => {
    let out = f;
    if (requiredSet.has(f.name)) out = { ...out, required: true };
    else if (optionalSet.has(f.name)) out = { ...out, required: false };
    const opts = serverOptions.get(f.name);
    if (opts) out = { ...out, type: 'select', options: opts };
    return out;
  });

  // 2) Append any registry field (required first) the local metadata lacks, so
  // it can never be hidden. Build a minimal text field using the registry label
  // (a select when the dictionary configured values for it).
  (entry.fields || []).forEach((rf: CategoryRegistryField) => {
    if (!known.has(rf.name)) {
      const opts = serverOptions.get(rf.name);
      merged.push({
        name: rf.name,
        label: rf.label || rf.name,
        type: opts ? 'select' : 'text',
        required: !!rf.required,
        ...(opts ? { options: opts } : {}),
      });
    }
  });

  return merged;
}

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
  // Uploaded product-image URLs (self-hosted, from productApi.uploadProductImage).
  // Optional so existing callers that don't collect images still type-check;
  // buildProductPayload defaults it to [].
  images?: string[];
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

  // Discount tier must be an explicit choice (no silent MASS default) so a
  // premium/luxury product never lands on the highest discount caps by
  // accident -- it drives the POS discount ceiling.
  if (!values.discountCategory) {
    errors.discount_category = 'Please choose a discount tier';
  }

  return errors;
}

// Build the exact CreateProductPayload the wizard's handleSubmit produced.
// Centralised so Quick Add and Guided Add POST byte-identical payloads. The
// API contract (productApi.createProduct) is unchanged.
export function buildProductPayload(values: ProductFormValues): CreateProductPayload {
  const { category, attributes } = values;

  // ProductCreate requires top-level brand/model. The dynamic form collects
  // these under category-specific attribute names (brand_name, model_no /
  // model_name); map them here. SKU is NOT a form field: the backend mints the
  // clean semantic SKU (product_master.mint_unique_sku) whenever none is sent,
  // so we OMIT it unless the operator explicitly supplied one (e.g. a legacy /
  // imported SKU under attributes.sku). We no longer fabricate a Date.now() SKU
  // — that ugly client SKU used to override the backend's clean one.
  const brand = String(attributes.brand_name || attributes.brand || '').trim();
  const model = String(
    attributes.model_no || attributes.model_name || attributes.subbrand || 'STD'
  ).trim();
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
    // Uploaded image URLs (durably stored + served by the backend). Empty when
    // the operator didn't add any.
    images: Array.isArray(values.images) ? values.images : [],
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
    // Clone inherits the source tier when it has one; a tier-less (legacy)
    // source maps to '' so the operator must consciously pick a tier (matches
    // the require-explicit-tier rule on the create forms).
    discountCategory: str(product.discount_category) || '',
    // Online flags are NOT cloned: a new SKU shouldn't inherit Shopify sync.
    syncToShopify: false,
    shopifyTags: [],
    publishPOS: true,
    // Preserve the source product's images so the clone starts with them (the
    // operator can remove them before saving the new SKU).
    images: Array.isArray(product.images)
      ? (product.images as unknown[]).map((u) => str(u)).filter(Boolean)
      : [],
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

// Keyword table for inferring a category from FREE TEXT (a product title /
// description / brand+model string) when there is no explicit `category` field.
// Ordered most-specific first: "sunglass" must beat "glass"/"lens", "reading
// glasses" must beat "glasses"->frame, "smart watch" must beat "watch", etc.
// Each entry is [substring, CATEGORIES code]. Matching is case-insensitive on a
// space-normalised lower-case string.
const TITLE_KEYWORD_RULES: Array<[string, string]> = [
  // Smart eyewear / watches (before their non-smart bases).
  ['smart sunglass', 'SMTSG'],
  ['smart glass', 'SMTFR'], ['smartglass', 'SMTFR'],
  ['smart watch', 'SMTWT'], ['smartwatch', 'SMTWT'],
  // Reading glasses (before generic "glasses" -> frame).
  ['reading glass', 'RG'], ['readers', 'RG'],
  // Sunglasses (before "glass"/"lens").
  ['sunglass', 'SG'], ['shades', 'SG'],
  // Contact lenses (before "lens").
  ['contact lens', 'CL'], ['contact lense', 'CL'], ['contacts', 'CL'],
  // Optical / spectacle lenses.
  ['spectacle lens', 'LS'], ['eyeglass lens', 'LS'], ['optical lens', 'LS'],
  ['rx lens', 'LS'],
  // Frames.
  ['eyeglass', 'FR'], ['spectacle', 'FR'], ['eyeframe', 'FR'],
  ['optical frame', 'FR'], ['frame', 'FR'], ['glasses', 'FR'],
  // Hearing aids.
  ['hearing aid', 'HA'], ['hearing', 'HA'],
  // Clocks (before "watch"? no overlap, but keep before generic).
  ['wall clock', 'CK'], ['table clock', 'CK'], ['alarm clock', 'CK'], ['clock', 'CK'],
  // Watches.
  ['wrist watch', 'WT'], ['wristwatch', 'WT'], ['watch', 'WT'],
  // Bare "lens" last (ambiguous — after contact/optical lens rules above).
  ['lens', 'LS'],
];

// Infer a CATEGORIES code from free text (title / description / brand+model).
// Returns '' when nothing matches so the caller leaves the category unset.
export function inferCategoryFromText(...texts: Array<unknown>): string {
  const hay = texts
    .map((t) => String(t ?? ''))
    .join(' ')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
  if (!hay) return '';
  for (const [needle, code] of TITLE_KEYWORD_RULES) {
    if (hay.includes(needle)) return code;
  }
  return '';
}

// Normalise a scraped spec KEY onto one of our canonical attribute keys where
// the mapping is obvious. Lowercases + strips punctuation, then looks up a small
// synonym table. Unknown keys return '' so the caller keeps the ORIGINAL key as
// passthrough (nothing scraped is silently lost). This complements the richer,
// category-aware mapper in autopilotSpecMap.ts (which only fills a category's
// DECLARED fields); here we also canonicalise the seed attributes so the new
// eyewear fields (frame_material, shape, lens_colour, upc/gtin, ...) prefill.
const SCRAPED_KEY_SYNONYMS: Record<string, string> = {
  'frame material': 'frame_material',
  'material': 'frame_material',
  'frame shape': 'shape',
  'shape': 'shape',
  'frame colour': 'frame_color',
  'frame color': 'frame_color',
  'colour': 'frame_color',
  'color': 'frame_color',
  'lens colour': 'lens_colour',
  'lens color': 'lens_colour',
  'temple colour': 'temple_color',
  'temple color': 'temple_color',
  'temple material': 'temple_material',
  'polarised': 'polarization',
  'polarized': 'polarization',
  'polarisation': 'polarization',
  'polarization': 'polarization',
  'uv': 'uv_protection',
  'uv protection': 'uv_protection',
  'lens material': 'lens_material',
  'bridge width': 'bridge_width',
  'temple length': 'temple_length',
  'lens size': 'lens_size',
  'upc': 'upc',
  'gtin': 'gtin',
  'ean': 'gtin',
  'country of origin': 'country_of_origin',
  'warranty': 'warranty',
};

/** Map a scraped spec key onto our canonical attribute key, or '' if unknown. */
export function normaliseSpecKey(key: unknown): string {
  const norm = String(key ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
  if (!norm) return '';
  return SCRAPED_KEY_SYNONYMS[norm] || '';
}

// Coerce the candidate's specs (Record<string, unknown>) into the string->string
// shape the form's review/extra display expects, dropping empty values. Scraped
// spec KEYS are canonicalised onto our attribute keys where obvious (e.g. "Frame
// Material" -> frame_material); unknown keys are kept as-is (passthrough). A
// canonical key an EARLIER entry already filled is never clobbered.
function specsToStrings(specs: Record<string, unknown> | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!specs) return out;
  Object.entries(specs).forEach(([k, v]) => {
    if (v === null || v === undefined) return;
    const s = String(v).trim();
    if (!s) return;
    const canonical = normaliseSpecKey(k);
    const outKey = canonical || k;
    // Don't overwrite a value a prior spec already set under the same key.
    if (out[outKey] === undefined) out[outKey] = s;
  });
  return out;
}

// The rich result the panel UX consumes: the form values PLUS which fields
// Autopilot filled (for the "auto" chips + fill summary), the unmapped extra
// specs (surfaced so the operator sees everything), and the reference URLs
// the data came from.
export interface AutopilotFillResult {
  values: ProductFormValues;
  /** Attribute names Autopilot populated (mapper + AI + identity + fallbacks). */
  autoFilled: string[];
  /** Scraped specs no declared field consumed (still kept as attributes). */
  extras: Record<string, string>;
  /** The exact page URL(s) this candidate's data came from. */
  referenceUrls: string[];
}

/** {domain, url} reference chips for a candidate (dedup'd, invalid dropped). */
export function candidateReferences(c: AutopilotCandidate): Array<{ domain: string; url: string }> {
  const urls: string[] = [];
  (c.references || []).forEach((r) => {
    if (r && typeof r.url === 'string' && r.url) urls.push(r.url);
  });
  if (urls.length === 0 && c.source_url) urls.push(c.source_url);
  if (urls.length === 0 && c.url) urls.push(c.url);
  const out: Array<{ domain: string; url: string }> = [];
  const seen = new Set<string>();
  urls.forEach((u) => {
    if (seen.has(u)) return;
    seen.add(u);
    try {
      const domain = new URL(u).hostname.replace(/^www\./, '');
      if (domain) out.push({ domain, url: u });
    } catch {
      /* not a valid absolute URL — skip the chip */
    }
  });
  return out;
}

// Attribute key that persists the Autopilot reference URL(s) onto the created
// product (harmless attributes passthrough -> a permanent record of where the
// catalog data came from).
export const AUTOPILOT_REFERENCE_ATTR = 'autopilot_reference';

// Whether the current rights rules allow USING a candidate's images at all
// (mirrors the backend image_use_allowed guard): AUTHORIZED sources yes;
// UNVERIFIED only when rights were explicitly confirmed. Gates the re-host —
// we never copy an image into OUR storage that the rules don't let us use.
export function candidateImagesUsable(c: AutopilotCandidate): boolean {
  return c.source_class === 'AUTHORIZED' || c.rights_confirmed === true;
}

// Human summary of the image re-host outcome for the fill-summary strip,
// e.g. "2 images copied to your storage, 1 kept as external link".
export function imageRehostSummary(copied: number, kept: number): string {
  if (copied <= 0 && kept <= 0) return '';
  const parts: string[] = [];
  if (copied > 0) parts.push(`${copied} image${copied === 1 ? '' : 's'} copied to your storage`);
  if (kept > 0) parts.push(`${kept} kept as external link${kept === 1 ? '' : 's'}`);
  return parts.join(', ');
}

// Map an Autopilot candidate -> the rich fill result. Brand + model land in
// the attribute keys the form reads (brand_name; both model_no AND model_name
// so whichever the chosen category renders is populated). Category comes from
// the job stamp (candidates now KNOW their category), else is inferred from
// text; `categoryOverride` (the form's already-picked category) wins over
// both so a user choice is never overridden. The spec mapper then populates
// the category's DECLARED fields from the scraped specs/title/size string,
// and any backend AI-suggested attributes fill remaining gaps (deterministic
// first, AI second). HSN/GST prefer the candidate's suggestions, else resolve
// from the category. Pricing is intentionally left blank -- the operator sets
// MRP/cost (Autopilot is a catalog/spec source, not a price source).
export function mapAutopilotCandidate(
  c: AutopilotCandidate,
  categoryOverride?: string
): AutopilotFillResult {
  // Category priority: the form's own pick > the job/candidate stamp (or the
  // candidate's specs.category) > free-text inference. '' when nothing works,
  // so the user simply picks one (nothing is mis-filed).
  const category =
    (categoryOverride && inferCategoryCode(categoryOverride)) ||
    inferCategoryCode(c.category ?? (c.specs ? (c.specs as Record<string, unknown>).category : undefined)) ||
    inferCategoryFromText(c.title, c.description, c.usp, c.brand, c.model);

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
  const autoFilled = new Set<string>(
    ['brand_name', 'model_no', 'model_name'].filter((k) => attributes[k])
  );

  // THE core v2 step: map free-form scraped specs (+ the size string in the
  // query/title/description) onto the category's declared form fields.
  let extras: Record<string, string> = {};
  if (category) {
    const specMap = mapSpecsToCategoryFields(
      category,
      c.specs as Record<string, unknown> | undefined,
      { brand, model, color: c.color ?? '', size: c.size ?? '' },
      c.title,
      c.description
    );
    Object.entries(specMap.mapped).forEach(([k, v]) => {
      if (v) {
        attributes[k] = v;
        autoFilled.add(k);
      }
    });
    extras = specMap.extras;

    // AI gap-fill (backend ai_attributes): deterministic mapping wins; the AI
    // only fills declared fields that are still empty. Unknown keys ignored.
    const declared = new Set(getCategoryFields(category).map((f) => f.name));
    Object.entries(c.ai_attributes || {}).forEach(([k, v]) => {
      const val = str(v);
      if (val && declared.has(k) && !attributes[k]) {
        attributes[k] = val;
        autoFilled.add(k);
      }
    });
  } else {
    // No category yet: every spec is an "extra" until the operator picks one
    // (QuickAddPage re-runs the mapper on category pick).
    extras = specsToStrings(c.specs as Record<string, unknown> | undefined);
    delete extras.category;
  }

  // Reference audit: persist where this data came from onto the product doc
  // via a passthrough attribute (plus return the URLs for the summary UI).
  const references = candidateReferences(c);
  const referenceUrls = references.map((r) => r.url);
  if (referenceUrls.length > 0) {
    attributes[AUTOPILOT_REFERENCE_ATTR] = referenceUrls.join(' ');
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

  const values: ProductFormValues = {
    category,
    attributes,
    description,
    hsnCode,
    gstRate: gstRate || '18',
    weight: '',
    // No price signal from Autopilot -- operator fills these in. Likewise the
    // discount tier is left blank so the operator must consciously pick it
    // (Autopilot has no tier signal; a silent MASS could under-cap a luxury SKU).
    mrp: '',
    offerPrice: '',
    costPrice: '',
    discountCategory: '',
    // A new SKU shouldn't auto-sync online.
    syncToShopify: false,
    shopifyTags: [],
    publishPOS: true,
    // Carry the candidate's images into the form so Autopilot-found photos
    // prefill the images array (the operator can remove/keep them). These are
    // the scraped source URLs; they're stored as-is on the product.
    images: Array.isArray(c.image_urls)
      ? c.image_urls.map((u) => str(u)).filter(Boolean)
      : [],
  };

  return { values, autoFilled: Array.from(autoFilled), extras, referenceUrls };
}

// Back-compat thin wrapper (the original prefill mapper's contract).
export function autopilotCandidateToFormValues(c: AutopilotCandidate): ProductFormValues {
  return mapAutopilotCandidate(c).values;
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
