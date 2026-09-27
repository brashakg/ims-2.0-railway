// ============================================================================
// IMS 2.0 - Product Add: category picker, per-category field lists, registry
// ============================================================================
// RULE - a category field is declared in TWO places and must be added to BOTH:
//   backend/api/services/product_master.py   _CATEGORY_SPECS + _FIELD_LABELS
//   this file                                CATEGORY_FIELDS, read through
//                                            getCategoryFields()
// The server registry (GET /products/categories) decides required-ness and
// Catalog Dictionary options at runtime; this file holds the UI metadata
// (labels, input types, select options, placeholders) and the offline
// fallback flags. backend/tests/test_smartglass_listing.py parses THIS file
// (the SMTFR list and the CATEGORIES picker) to pin the form to the registry.

import type {
  CategoryRegistryEntry,
  CategoryRegistryField,
} from '../../../services/api/products';
import { productApi } from '../../../services/api/products';
import type { TaxedCategory } from '../../../constants/gst';

// Product categories with display names + emoji (used in the category picker).
export const CATEGORIES = [
  { code: 'SG', name: 'Sunglass', icon: '🕶️' },
  { code: 'FR', name: 'Frame', icon: '👓' },
  { code: 'CL', name: 'Contact Lens', icon: '👁️' },
  // Owner 2026-07-05 split: colour/cosmetic lenses are their own category
  // (canonical COLORED_CONTACT_LENS, SKU prefix CCL).
  { code: 'CCL', name: 'Colour Contact Lens', icon: '👁️' },
  { code: 'LS', name: 'Optical Lens', icon: '🔍' },
  { code: 'RG', name: 'Reading Glasses', icon: '📖' },
  { code: 'WT', name: 'Wrist Watch', icon: '⌚' },
  { code: 'CK', name: 'Clock', icon: '🕐' },
  { code: 'HA', name: 'Hearing Aid', icon: '🦻' },
  { code: 'ACC', name: 'Accessories', icon: '🎒' },
  // ONE smartglasses tile (2026-08-25). There used to be two -- "Smartglasses
  // (Sunglass)" (SMTSG) and "Smartglasses" (SMTFR) -- but BOTH resolve to the
  // same canonical registry entry (SMARTGLASSES, see FE_CODE_TO_CANONICAL) and
  // anything catalogued under either re-opens as SMTFR (CANONICAL_TO_PICKER).
  // So the second tile only ever offered a shorter version of the same form.
  // SMTSG survives as a FIELD-LIST alias below for legacy/stored codes.
  { code: 'SMTFR', name: 'Smartglasses', icon: '🥽' },
  { code: 'SMTWT', name: 'Smart Watch', icon: '⌚' },
  // A picker code MUST be one constants/gst.ts prices (TaxedCategory), or this
  // list fails `tsc`. Without that, a new category falls through that table's
  // unknown-category defaults -- HSN 900490 at 18% -- while the server stores
  // the rate the picked HSN actually carries, so the form asserts one number
  // under helper text promising another. CCL did exactly that: 18% on screen,
  // 5% stored, on a category these shops bill every day.
] as const satisfies ReadonlyArray<{ code: TaxedCategory; name: string; icon: string }>;

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
// Fields the SERVER re-cases on save (backend/api/services/product_master.py,
// _CASE_CODE and _CASE_NAME). The Add-Product inputs show CAPS while you type
// so nobody reaches for the shift key; the server decides the stored case.
//
// The uppercase styling is driven by THIS set and not by `type === 'text'`,
// deliberately. An unclassified text field left uppercase in the UI but
// untouched by the server would store SHOUTING permanently - the field list is
// rewritten at runtime from the server registry, so unknown keys must degrade
// to today's behaviour, never to capitals. Keep both sides in step: a field
// added here without a matching entry in _CASE_CODE / _CASE_NAME is a bug.
export const CAPS_ENTRY_FIELDS: ReadonlySet<string> = new Set([
  // codes - stored upper-cased
  'model_no', 'full_model_no', 'colour_code', 'color_code',
  'serial_no', 'battery_size', 'upc', 'gtin', 'hsn_code',
  // words - stored title-cased
  'model_name', 'label', 'subbrand', 'brand_name',
  'colour_name', 'frame_color', 'temple_color', 'lens_colour', 'dial_colour',
  'belt_colour', 'body_colour', 'tint',
  'frame_material', 'temple_material', 'lens_material',
  'country_of_origin', 'generation', 'cl_series',
  'audio_type', 'camera_type', 'connectivity', 'voice_assistant',
  'add_on_1', 'add_on_2', 'add_on_3',
]);

export const CATEGORY_FIELDS: Record<string, CategoryField[]> = {
  // SG/FR field ORDER + labels are owner-locked (2026-07-04 catalog form
  // rework): identity block first (brand -> colour), then sizes, then style,
  // then colours/materials, then USPs and provenance. Weight (a top-level
  // payload field, not an attribute) is rendered by QuickAddPage between
  // Warranty and UPC for these two categories. Removed from the FORM (values
  // on existing product docs are untouched): full_model_no, product_usp, usp,
  // lens_usp, gender_label.
  SG: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Oakley', 'Vogue', 'Prada', 'Gucci', 'Titan', 'Fastrack', 'Lenskart', 'Vincent Chase'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false },
    { name: 'label', label: 'Label', type: 'text', required: false },
    { name: 'model_no', label: 'Model No', type: 'text', required: true },
    { name: 'model_name', label: 'Model Name', type: 'text', required: false },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Size', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
    { name: 'gender', label: 'Gender', type: 'select', required: false, options: ['Men', 'Women', 'Unisex', 'Kids'] },
    { name: 'shape', label: 'Shape', type: 'select', required: false, options: ['Rectangle', 'Square', 'Round', 'Oval', 'Cat-Eye', 'Aviator', 'Wayfarer', 'Clubmaster', 'Oversized', 'Geometric', 'Wrap'] },
    { name: 'frame_type', label: 'Frame Type', type: 'select', required: false, options: ['Full Rim', 'Half Rim', 'Rimless'] },
    { name: 'lens_colour', label: 'Lens Colour', type: 'text', required: false },
    { name: 'tint', label: 'Tint', type: 'text', required: false },
    { name: 'polarization', label: 'Polarization', type: 'select', required: false, options: ['Yes', 'No'] },
    { name: 'uv_protection', label: 'UV Protection', type: 'select', required: false, options: ['UV400', 'UV380', 'Polarized', 'None'] },
    { name: 'frame_color', label: 'Frame Colour', type: 'text', required: false },
    { name: 'temple_color', label: 'Temple Colour', type: 'text', required: false },
    { name: 'lens_material', label: 'Lens Material', type: 'select', required: false, options: ['CR-39', 'Polycarbonate', 'Glass', 'Trivex', 'Nylon'] },
    { name: 'frame_material', label: 'Frame Front Material', type: 'text', required: false },
    { name: 'temple_material', label: 'Temple Material', type: 'text', required: false },
    { name: 'usp_1', label: 'Product USP 1', type: 'text', required: false },
    { name: 'usp_2', label: 'Product USP 2', type: 'text', required: false },
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
    { name: 'model_name', label: 'Model Name', type: 'text', required: false },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Size', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
    { name: 'gender', label: 'Gender', type: 'select', required: false, options: ['Men', 'Women', 'Unisex', 'Kids'] },
    { name: 'shape', label: 'Shape', type: 'select', required: false, options: ['Rectangle', 'Square', 'Round', 'Oval', 'Cat-Eye', 'Aviator', 'Wayfarer', 'Clubmaster', 'Oversized', 'Geometric', 'Wrap'] },
    { name: 'frame_type', label: 'Frame Type', type: 'select', required: false, options: ['Full Rim', 'Half Rim', 'Rimless'] },
    { name: 'blue_cut_lens', label: 'Blue-Cut Lens', type: 'select', required: false, options: ['Yes', 'No'] },
    { name: 'frame_color', label: 'Frame Colour', type: 'text', required: false },
    { name: 'temple_color', label: 'Temple Colour', type: 'text', required: false },
    { name: 'frame_material', label: 'Frame Front Material', type: 'text', required: false },
    { name: 'temple_material', label: 'Temple Material', type: 'text', required: false },
    { name: 'usp_1', label: 'Product USP 1', type: 'text', required: false },
    { name: 'usp_2', label: 'Product USP 2', type: 'text', required: false },
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
  // Colour Contact Lens (owner 2026-07-05 split, canonical COLORED_CONTACT_LENS,
  // SKU prefix CCL). Same clinical fields as CL; Lens Colour is REQUIRED (it is
  // the defining attribute — the backend registry enforces it too).
  CCL: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Bausch & Lomb', 'Johnson & Johnson', 'Alcon', 'CooperVision', 'Acuvue'] },
    { name: 'cl_series', label: 'Series', type: 'text', required: false, placeholder: 'e.g. FreshLook Colorblends' },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true },
    { name: 'colour_name', label: 'Lens Colour', type: 'text', required: true, placeholder: 'e.g. Hazel, Grey, Turquoise' },
    { name: 'modality', label: 'Modality', type: 'select', required: false, options: ['DAILY', 'FORTNIGHTLY', 'MONTHLY', 'QUARTERLY', 'YEARLY'] },
    { name: 'power', label: 'Power (SPH)', type: 'text', required: true, placeholder: '0.00 (plano) or -6.00 to +6.00' },
    { name: 'base_curve', label: 'Base Curve (BC)', type: 'number', required: false, placeholder: '8.6' },
    { name: 'diameter', label: 'Diameter (DIA)', type: 'number', required: false, placeholder: '14.2' },
    { name: 'cl_cyl', label: 'Cylinder (toric)', type: 'number', required: false },
    { name: 'cl_axis', label: 'Axis (toric, 0-180)', type: 'number', required: false },
    { name: 'pack', label: 'Pack Size', type: 'select', required: false, options: ['1', '2', '3', '6', '30', '90'] },
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
  // SMARTGLASSES (owner 2026-08-25). A smart glass IS a sunglass with
  // electronics, so this list = the SG eyewear questions (identity, sizes,
  // shape, colours, materials, lens, provenance) + the electronics half.
  // EVERY electronics field below is grounded in a spec bullet that appears on
  // the live Ray-Ban Meta listings on bettervision.in -- until now that detail
  // only existed as prose somebody typed, which is why a new model could not be
  // catalogued and listed normally. Filling them GENERATES the listing
  // (backend services/smartglass_listing.py); leaving one blank just omits its
  // bullet. Keep this field-for-field with the backend SMARTGLASSES registry
  // (product_master._EYEWEAR_SUN_TAIL + _SMARTGLASS_TECH) -- the pin test
  // backend/tests/test_smartglass_listing.py, which parses THIS file, fails if
  // the two drift (test_frontend_list_and_backend_registry_agree_field_for_field).
  SMTFR: [
    { name: 'brand_name', label: 'Brand Name', type: 'select', required: true, options: ['Ray-Ban', 'Meta', 'Amazon', 'Google', 'Bose'] },
    { name: 'subbrand', label: 'Sub Brand', type: 'text', required: false, placeholder: 'e.g. Meta' },
    { name: 'label', label: 'Label', type: 'text', required: false },
    { name: 'model_name', label: 'Model Name', type: 'text', required: true, placeholder: 'e.g. Wayfarer' },
    { name: 'model_no', label: 'Model No', type: 'text', required: false, placeholder: 'e.g. RW4006' },
    { name: 'colour_code', label: 'Colour Code', type: 'text', required: true, placeholder: 'e.g. 601/7150' },
    { name: 'lens_size', label: 'Lens Size (mm)', type: 'number', required: false },
    { name: 'bridge_width', label: 'Bridge Size', type: 'number', required: false },
    { name: 'temple_length', label: 'Temple Length (mm)', type: 'number', required: false },
    { name: 'gender', label: 'Gender', type: 'select', required: false, options: ['Men', 'Women', 'Unisex', 'Kids'] },
    { name: 'shape', label: 'Shape', type: 'select', required: false, options: ['Wayfarer', 'Skyler', 'Headliner', 'Round', 'Square', 'Rectangle', 'Cat-Eye', 'Aviator', 'Clubmaster', 'Oversized', 'Wrap'] },
    { name: 'frame_type', label: 'Frame Type', type: 'select', required: false, options: ['Full Rim', 'Half Rim', 'Rimless'] },
    { name: 'lens_colour', label: 'Lens Colour', type: 'text', required: false },
    { name: 'tint', label: 'Tint', type: 'text', required: false },
    { name: 'polarization', label: 'Polarization', type: 'select', required: false, options: ['Yes', 'No'] },
    { name: 'uv_protection', label: 'UV Protection', type: 'select', required: false, options: ['UV400', 'UV380', 'Polarized', 'None'] },
    { name: 'frame_color', label: 'Frame Colour', type: 'text', required: false },
    { name: 'temple_color', label: 'Temple Colour', type: 'text', required: false },
    { name: 'lens_material', label: 'Lens Material', type: 'select', required: false, options: ['CR-39', 'Polycarbonate', 'Glass', 'Trivex', 'Nylon'] },
    { name: 'frame_material', label: 'Frame Front Material', type: 'text', required: false, placeholder: 'e.g. Acetate' },
    { name: 'temple_material', label: 'Temple Material', type: 'text', required: false },
    { name: 'usp_1', label: 'Product USP 1', type: 'text', required: false },
    { name: 'usp_2', label: 'Product USP 2', type: 'text', required: false },
    { name: 'country_of_origin', label: 'Country of Origin', type: 'text', required: false },
    { name: 'warranty', label: 'Warranty', type: 'text', required: false },
    { name: 'upc', label: 'UPC (mfr)', type: 'text', required: false },
    { name: 'gtin', label: 'GTIN (mfr)', type: 'text', required: false },
    // ---- The electronics. Each one becomes a spec bullet on the website. ----
    { name: 'generation', label: 'Generation', type: 'text', required: false, placeholder: 'e.g. Gen 2' },
    { name: 'camera_mp', label: 'Camera (megapixels)', type: 'number', required: false, placeholder: '12' },
    { name: 'camera_type', label: 'Camera Type', type: 'text', required: false, placeholder: 'e.g. Ultra-wide' },
    { name: 'video_resolution', label: 'Video Recording', type: 'select', required: false, options: ['1080p', '1440p', '3K', '4K'] },
    { name: 'audio_type', label: 'Speakers', type: 'text', required: false, placeholder: 'e.g. Open-ear speakers' },
    { name: 'microphone_count', label: 'Number of Microphones', type: 'number', required: false, placeholder: '5' },
    { name: 'voice_assistant', label: 'Voice Assistant', type: 'text', required: false, placeholder: 'e.g. Meta AI' },
    { name: 'controls', label: 'Controls', type: 'text', required: false, placeholder: 'e.g. Capacitive touch controls on the temple' },
    { name: 'battery_life_hours', label: 'Battery Life (hours)', type: 'number', required: false, placeholder: '4' },
    { name: 'charging_case', label: 'Charging Case Included', type: 'select', required: false, options: ['Yes', 'No'] },
    { name: 'connectivity', label: 'Connectivity', type: 'text', required: false, placeholder: 'e.g. Wi-Fi 6 and Bluetooth 5.2' },
    { name: 'storage_gb', label: 'On-board Storage (GB)', type: 'number', required: false, placeholder: '32' },
    { name: 'prescription_ready', label: 'Prescription Lenses Possible', type: 'select', required: false, options: ['Yes', 'No'] },
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

// Legacy alias: SMTSG was the retired "Smartglasses (Sunglass)" picker tile.
// It is the SAME canonical category as SMTFR (FE_CODE_TO_CANONICAL maps both to
// SMARTGLASSES), so a stored/legacy SMTSG code now renders the ONE smartglasses
// field list instead of the stale short copy that used to live here.
CATEGORY_FIELDS.SMTSG = CATEGORY_FIELDS.SMTFR;

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
export const FE_CODE_TO_CANONICAL: Record<string, string> = {
  SG: 'SUNGLASS',
  FR: 'FRAME',
  CL: 'CONTACT_LENS',
  CCL: 'COLORED_CONTACT_LENS',
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

// Canonical long-form category -> the CATEGORIES picker code the form keys on.
// Hoisted from CatalogProductDrawer (which now imports it) so the drawer's
// mini-form and the full-page ?review editor share ONE mapping. Note this is
// NOT a strict inverse of FE_CODE_TO_CANONICAL: SMARTGLASSES resolves to the
// SMTFR picker (SMTSG shares the same canonical registry entry).
export const CANONICAL_TO_PICKER: Record<string, string> = {
  FRAME: 'FR',
  SUNGLASS: 'SG',
  CONTACT_LENS: 'CL',
  COLORED_CONTACT_LENS: 'CCL',
  OPTICAL_LENS: 'LS',
  READING_GLASSES: 'RG',
  WATCH: 'WT',
  WALL_CLOCK: 'CK',
  HEARING_AID: 'HA',
  ACCESSORIES: 'ACC',
  SMARTGLASSES: 'SMTFR',
  SMARTWATCH: 'SMTWT',
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
