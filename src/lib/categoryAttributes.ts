// Category -> attributes mapping, derived from the user's
// attribute-mapping.xlsx (2026-04-21). Source of truth for:
//  1. Which attribute inputs to show on the product form per category.
//  2. Which values to emit as Shopify tags when saving a product.
//  3. Which AttributeType rows to show on the /dashboard/attributes page
//     when a category tab is active.
//
// Attribute keys in this file use the Product/ProductVariant column name
// when one exists (e.g. `brand`, `frameMaterial`). When no column exists
// yet, the key is still camelCase so it can bridge seamlessly to a column
// or to Product.tags if the value is stored there instead.

export interface AttributeMeta {
  /** Stable enum key. camelCase for easy identifier use. */
  key: string;
  /** Human label shown in forms. */
  label: string;
  /** Level: "product" = on Product row; "variant" = on ProductVariant. */
  level: "product" | "variant";
  /** When true, value is auto-generated — don't render an input. */
  autoPopulate?: boolean;
  /** When true, include this attr's value as a `<prefix>_<value>` Shopify tag. */
  tag?: boolean;
  /** Tag prefix override. Defaults to key.toLowerCase(). Used so
   * historically-emitted prefixes like "origin_" / "framesize_" stay
   * parseable by the pull route's parseTagsToFields(). */
  tagPrefix?: string;
  /** Attribute name in the /dashboard/attributes page's AttributeType.name
   * column. Defaults to key.toLowerCase() but some historic names diverge
   * (e.g. "countryoforigin" not "countryOfOrigin"). */
  attributeTypeName?: string;
  /** When false or absent, the value stores on Product.tags only (no DB column). */
  hasColumn?: boolean;
}

// All known attributes, keyed by identifier. Flat registry so we can look
// up metadata anywhere.
export const ATTRIBUTES: Record<string, AttributeMeta> = {
  // ── Identity (columns exist on Product) ──
  brand:            { key: "brand",            label: "Brand",              level: "product", tag: true,  hasColumn: true },
  subBrand:         { key: "subBrand",         label: "Sub-brand",          level: "product", tag: true,  hasColumn: true },
  modelNo:          { key: "modelNo",          label: "Model No",           level: "product", tag: true,  hasColumn: true },
  productName:      { key: "productName",      label: "Product Name",       level: "product", tag: false, hasColumn: true },
  label:            { key: "label",            label: "Label",              level: "product", tag: false, hasColumn: true },
  sku:              { key: "sku",              label: "SKU",                level: "product", tag: false, hasColumn: true },
  gtin:             { key: "gtin",             label: "GTIN",               level: "product", tag: false, hasColumn: true },
  upc:              { key: "upc",              label: "UPC",                level: "product", tag: false, hasColumn: true },
  barcode:          { key: "barcode",          label: "Barcode",            level: "variant", tag: false, hasColumn: true },
  manufacturer:     { key: "manufacturer",     label: "Manufacturer",       level: "product", tag: false, hasColumn: false },

  // ── Demographics / origin ──
  gender:           { key: "gender",           label: "Gender",             level: "product", tag: true,  hasColumn: true },
  countryOfOrigin:  { key: "countryOfOrigin",  label: "Country of Origin",  level: "product", tag: true,  tagPrefix: "origin", attributeTypeName: "countryoforigin", hasColumn: true },
  warranty:         { key: "warranty",         label: "Warranty",           level: "product", tag: true,  hasColumn: true },

  // ── Pricing ──
  mrp:              { key: "mrp",              label: "MRP",                level: "product", tag: false, hasColumn: true },
  srp:              { key: "srp",              label: "SRP (Selling Price)", level: "product", tag: false, hasColumn: false },

  // ── Frame / body attrs ──
  shape:            { key: "shape",            label: "Shape",              level: "product", tag: true,  hasColumn: true },
  frameMaterial:    { key: "frameMaterial",    label: "Frame Material",     level: "product", tag: true,  attributeTypeName: "framematerial", hasColumn: true },
  templeMaterial:   { key: "templeMaterial",   label: "Temple Material",    level: "product", tag: true,  attributeTypeName: "templematerial", hasColumn: true },
  frameType:        { key: "frameType",        label: "Frame Type",         level: "product", tag: true,  attributeTypeName: "frametype", hasColumn: true },
  nosepadMaterial:  { key: "nosepadMaterial",  label: "Nosepad Material",   level: "product", tag: true,  hasColumn: false },

  // ── Lens attrs (product-level for eyewear + contact lenses) ──
  lensMaterial:     { key: "lensMaterial",     label: "Lens Material",      level: "product", tag: true,  attributeTypeName: "lensmaterial", hasColumn: true },
  lensColour:       { key: "lensColour",       label: "Lens Colour",        level: "product", tag: true,  attributeTypeName: "lenscolour", hasColumn: true },
  tint:             { key: "tint",             label: "Tint",               level: "product", tag: true,  attributeTypeName: "tint", hasColumn: true },
  photochromatic:   { key: "photochromatic",   label: "Photochromatic",     level: "product", tag: true,  hasColumn: false },
  polarization:     { key: "polarization",     label: "Polarization",       level: "product", tag: true,  attributeTypeName: "polarization", hasColumn: true },
  uvProtection:     { key: "uvProtection",     label: "UV Protection",      level: "product", tag: true,  attributeTypeName: "uvprotection", hasColumn: true },
  lensUSP:          { key: "lensUSP",          label: "Lens USP",           level: "product", tag: false, attributeTypeName: "lensUSP", hasColumn: true },

  // ── Variant-level sizing (eyewear/smartglasses) ──
  colorCode:        { key: "colorCode",        label: "Color Code",         level: "variant", tag: true,  hasColumn: true },
  colorName:        { key: "colorName",        label: "Color Name",         level: "variant", tag: true,  hasColumn: true },
  frameColor:       { key: "frameColor",       label: "Frame Color",        level: "variant", tag: true,  hasColumn: true },
  templeColor:      { key: "templeColor",      label: "Temple Color",       level: "variant", tag: true,  hasColumn: true },
  lensSize:         { key: "lensSize",         label: "Lens Size (mm)",     level: "variant", tag: false, hasColumn: false }, // stored in ProductVariant.frameSize today
  bridgeSize:       { key: "bridgeSize",       label: "Bridge Size (mm)",   level: "variant", tag: false, hasColumn: false }, // stored in ProductVariant.bridge
  templeLength:     { key: "templeLength",     label: "Temple Length (mm)", level: "variant", tag: false, hasColumn: true },
  weightGrams:      { key: "weightGrams",      label: "Weight (grams)",     level: "variant", tag: false, hasColumn: false }, // stored in ProductVariant.weight

  // ── Contact lens specifics ──
  clearColor:       { key: "clearColor",       label: "Clear / Color",      level: "product", tag: true,  hasColumn: false },
  packSize:         { key: "packSize",         label: "Pack Size",          level: "variant", tag: false, hasColumn: false },
  lensType:         { key: "lensType",         label: "Lens Type",          level: "product", tag: true,  hasColumn: false },
  wearSchedule:     { key: "wearSchedule",     label: "Wear Schedule",      level: "product", tag: true,  hasColumn: false },
  wearingHours:     { key: "wearingHours",     label: "Wearing Hours",      level: "product", tag: false, hasColumn: false },
  waterContent:     { key: "waterContent",     label: "Water Content",      level: "product", tag: false, hasColumn: false },
  centerThickness:  { key: "centerThickness",  label: "Center Thickness",   level: "variant", tag: false, hasColumn: false },
  oxygenPermeability: { key: "oxygenPermeability", label: "Oxygen Permeability", level: "product", tag: false, hasColumn: false },
  baseCurve:        { key: "baseCurve",        label: "Base Curve",         level: "variant", tag: false, hasColumn: false },
  diameter:         { key: "diameter",         label: "Diameter",           level: "variant", tag: false, hasColumn: false },
  expiry:           { key: "expiry",           label: "Expiry Date",        level: "variant", tag: false, hasColumn: false },

  // ── Product USP ──
  productUSP1:      { key: "productUSP1",      label: "Product USP 1",      level: "product", tag: false, hasColumn: false },
  productUSP2:      { key: "productUSP2",      label: "Product USP 2",      level: "product", tag: false, hasColumn: false },

  // ── Stock ──
  quantity:         { key: "quantity",         label: "Quantity",           level: "variant", tag: false, hasColumn: true }, // per-location via VariantLocation

  // ── Smartglass specifics ──
  displayType:      { key: "displayType",      label: "Display Type",       level: "product", tag: true,  hasColumn: false },
  audio:            { key: "audio",            label: "Audio",              level: "product", tag: true,  hasColumn: false },
  camera:           { key: "camera",           label: "Camera",             level: "product", tag: true,  hasColumn: false },
  cameraResolution: { key: "cameraResolution", label: "Camera Resolution",  level: "product", tag: false, hasColumn: false },
  microphone:       { key: "microphone",       label: "Microphone",         level: "product", tag: true,  hasColumn: false },
  bluetooth:        { key: "bluetooth",        label: "Bluetooth",          level: "product", tag: true,  hasColumn: false },
  batteryLife:      { key: "batteryLife",      label: "Battery Life",       level: "product", tag: false, hasColumn: false },
  prescriptionSupport: { key: "prescriptionSupport", label: "Prescription Support", level: "product", tag: true, hasColumn: false },
  aiFeatures:       { key: "aiFeatures",       label: "AI Features",        level: "product", tag: true,  hasColumn: false },
  healthSensors:    { key: "healthSensors",    label: "Health Sensors",     level: "product", tag: true,  hasColumn: false },
  gps:              { key: "gps",              label: "GPS",                level: "product", tag: true,  hasColumn: false },
  connectivity:     { key: "connectivity",     label: "Connectivity",       level: "product", tag: true,  hasColumn: false },
  waterResistance:  { key: "waterResistance",  label: "Water Resistance",   level: "product", tag: true,  hasColumn: false },

  // ── Auto-generated (no input rendered) ──
  description:      { key: "description",      label: "Description",        level: "product", autoPopulate: true, tag: false, hasColumn: true },
  tags:             { key: "tags",             label: "Tags",               level: "product", autoPopulate: true, tag: false, hasColumn: true },
};

/**
 * Which attributes apply to each category (from the xlsx).
 * Categories here must match the keys in src/lib/categories.ts.
 */
// Watch-specific attribute meta (added when user restored WATCHES +
// SMARTWATCHES + ACCESSORIES + READING/COMPUTER/SAFETY glasses as their
// own categories on 2026-04-21).
export const WATCH_ATTRS: AttributeMeta[] = [
  { key: "movementType",     label: "Movement Type",      level: "product", tag: true,  hasColumn: false },
  { key: "features",         label: "Watch Features",     level: "product", tag: true,  hasColumn: false },
  { key: "caseColor",        label: "Case Color",         level: "variant", tag: true,  hasColumn: false },
  { key: "caseMaterial",     label: "Case Material",      level: "variant", tag: true,  hasColumn: false },
  { key: "caseSize",         label: "Case Size (mm)",     level: "variant", tag: false, hasColumn: false },
  { key: "strapColor",       label: "Strap Color",        level: "variant", tag: true,  hasColumn: false },
  { key: "strapMaterial",    label: "Strap Material",     level: "variant", tag: true,  hasColumn: false },
  { key: "dialColor",        label: "Dial Color",         level: "variant", tag: true,  hasColumn: false },
  { key: "os",               label: "Operating System",   level: "product", tag: true,  hasColumn: false },
];
for (const w of WATCH_ATTRS) ATTRIBUTES[w.key] = w;

export const CATEGORY_ATTRIBUTES: Record<string, string[]> = {
  SPECTACLES: [
    "brand","subBrand","modelNo","colorCode","productName","label","quantity",
    "gender","sku","countryOfOrigin","warranty","description","tags","shape",
    "mrp","srp","weightGrams","lensSize","bridgeSize","templeLength",
    "frameColor","templeColor","frameMaterial","templeMaterial","frameType",
    "nosepadMaterial","photochromatic","productUSP1","productUSP2","lensUSP",
    "barcode","gtin","upc","manufacturer",
  ],
  CLIP_ON_FRAMES: [
    "brand","subBrand","modelNo","colorCode","productName","label","quantity",
    "gender","sku","countryOfOrigin","warranty","description","tags","shape",
    "mrp","srp","weightGrams","lensSize","bridgeSize","templeLength",
    "frameColor","templeColor","frameMaterial","templeMaterial","frameType",
    "nosepadMaterial","lensMaterial","lensColour","tint","photochromatic",
    "polarization","uvProtection","productUSP1","productUSP2","lensUSP",
    "barcode","gtin","upc","manufacturer","prescriptionSupport",
  ],
  SUNGLASSES: [
    "brand","subBrand","modelNo","colorCode","productName","label","quantity",
    "gender","sku","countryOfOrigin","warranty","description","tags","shape",
    "mrp","srp","weightGrams","lensSize","bridgeSize","templeLength",
    "frameColor","templeColor","frameMaterial","templeMaterial","frameType",
    "nosepadMaterial","lensMaterial","lensColour","tint","photochromatic",
    "polarization","uvProtection","productUSP1","productUSP2","lensUSP",
    "barcode","gtin","upc","manufacturer","prescriptionSupport",
  ],
  CONTACT_LENSES: [
    "brand","subBrand","modelNo","clearColor","colorCode","productName","label",
    "packSize","quantity","sku","countryOfOrigin","description","tags",
    "mrp","srp","lensMaterial","barcode","gtin","upc","manufacturer",
    "lensType","wearSchedule","wearingHours","waterContent","centerThickness",
    "oxygenPermeability","baseCurve","diameter","expiry",
  ],
  SMARTGLASSES: [
    "brand","subBrand","modelNo","colorCode","productName","label","quantity",
    "gender","sku","countryOfOrigin","warranty","description","tags","shape",
    "mrp","srp","weightGrams","lensSize","bridgeSize","templeLength",
    "frameColor","templeColor","frameMaterial","templeMaterial","frameType",
    "nosepadMaterial","lensMaterial","lensColour","tint","photochromatic",
    "polarization","uvProtection","productUSP1","productUSP2","lensUSP",
    "barcode","gtin","upc","manufacturer","prescriptionSupport",
    "displayType","audio","camera","cameraResolution","microphone","bluetooth",
    "batteryLife","aiFeatures","healthSensors","gps","connectivity","waterResistance",
  ],
  // Reading / Computer / Safety glasses share the Spectacles attribute set
  // (frame + lens attributes apply). If specific sub-attributes are needed
  // later — e.g. computer-glasses BLUE_LIGHT_FILTER strength — add them here.
  READING_GLASSES: [
    "brand","subBrand","modelNo","colorCode","productName","label","quantity",
    "gender","sku","countryOfOrigin","warranty","description","tags","shape",
    "mrp","srp","weightGrams","lensSize","bridgeSize","templeLength",
    "frameColor","templeColor","frameMaterial","templeMaterial","frameType",
    "nosepadMaterial","lensMaterial","photochromatic",
    "productUSP1","productUSP2","lensUSP","barcode","gtin","upc","manufacturer",
    "prescriptionSupport",
  ],
  COMPUTER_GLASSES: [
    "brand","subBrand","modelNo","colorCode","productName","label","quantity",
    "gender","sku","countryOfOrigin","warranty","description","tags","shape",
    "mrp","srp","weightGrams","lensSize","bridgeSize","templeLength",
    "frameColor","templeColor","frameMaterial","templeMaterial","frameType",
    "nosepadMaterial","lensMaterial","photochromatic","lensUSP",
    "productUSP1","productUSP2","barcode","gtin","upc","manufacturer",
    "prescriptionSupport",
  ],
  SAFETY_GLASSES: [
    "brand","subBrand","modelNo","colorCode","productName","label","quantity",
    "gender","sku","countryOfOrigin","warranty","description","tags","shape",
    "mrp","srp","weightGrams","lensSize","bridgeSize","templeLength",
    "frameColor","templeColor","frameMaterial","templeMaterial","frameType",
    "nosepadMaterial","lensMaterial","uvProtection","lensUSP",
    "productUSP1","productUSP2","barcode","gtin","upc","manufacturer",
  ],
  WATCHES: [
    "brand","subBrand","modelNo","colorCode","productName","label","quantity",
    "gender","sku","countryOfOrigin","warranty","description","tags",
    "mrp","srp","weightGrams",
    "movementType","features","caseColor","caseMaterial","caseSize",
    "strapColor","strapMaterial","dialColor","waterResistance",
    "productUSP1","productUSP2","barcode","gtin","upc","manufacturer",
  ],
  SMARTWATCHES: [
    "brand","subBrand","modelNo","colorCode","productName","label","quantity",
    "gender","sku","countryOfOrigin","warranty","description","tags",
    "mrp","srp","weightGrams",
    "caseColor","caseMaterial","caseSize","strapColor","strapMaterial",
    "displayType","os","bluetooth","batteryLife","aiFeatures",
    "healthSensors","gps","connectivity","waterResistance",
    "productUSP1","productUSP2","barcode","gtin","upc","manufacturer",
  ],
  ACCESSORIES: [
    "brand","subBrand","modelNo","colorCode","productName","label","quantity",
    "sku","countryOfOrigin","warranty","description","tags",
    "mrp","srp","productUSP1","productUSP2",
    "barcode","gtin","upc","manufacturer",
  ],
};

/** Returns attribute keys (optionally filtered by level) that apply to a category. */
export function attributesForCategory(
  category: string,
  level?: "product" | "variant"
): AttributeMeta[] {
  const list = CATEGORY_ATTRIBUTES[category] || [];
  return list
    .map((k) => ATTRIBUTES[k])
    .filter((meta): meta is AttributeMeta => Boolean(meta))
    .filter((meta) => (level ? meta.level === level : true));
}

/** Does this attribute apply to the given category? */
export function isAttrApplicable(attrKey: string, category: string): boolean {
  const list = CATEGORY_ATTRIBUTES[category] || [];
  return list.includes(attrKey);
}

/**
 * Slugify a value for tag emission.
 * "Ray-Ban" -> "ray-ban", "BOSS 1234" -> "boss-1234".
 */
export function slugifyTagValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  return String(v)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Build the `<attrKey>_<value>` tag list for a product given its category
 * and raw attribute values. Called from generateTags in autoGenerate.ts.
 */
export function tagsForProductAttributes(
  category: string,
  attrs: Record<string, unknown>
): string[] {
  const applicable = attributesForCategory(category);
  const out: string[] = [];
  const seen = new Set<string>();

  // Always emit a category tag so the pull route can route by category.
  if (category) {
    const catTag = `category_${slugifyTagValue(category)}`;
    if (!seen.has(catTag)) {
      out.push(catTag);
      seen.add(catTag);
    }
  }

  for (const meta of applicable) {
    if (!meta.tag) continue;
    const raw = attrs[meta.key];
    if (raw === null || raw === undefined || raw === "") continue;
    const prefix = meta.tagPrefix ?? meta.key.toLowerCase();
    let valuePart: string;
    if (typeof raw === "boolean") {
      valuePart = raw ? "yes" : "no";
    } else {
      valuePart = slugifyTagValue(raw);
    }
    if (!valuePart) continue;
    const t = `${prefix}_${valuePart}`;
    if (seen.has(t)) continue;
    out.push(t);
    seen.add(t);
  }
  return out;
}
