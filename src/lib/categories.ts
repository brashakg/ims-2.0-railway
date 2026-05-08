// Single source of truth for product categories. Used by product forms,
// filters, the Attributes section, and the AI SEO generator.
//
// When adding a new category:
//  1. Add it to CATEGORIES (stable enum key + display label + SEO noun).
//  2. Decide which attribute types apply via ATTRIBUTES_BY_CATEGORY below.
//  3. Re-seed AttributeTypes if needed (GET /api/seed handles this).
//  4. Optionally add a DiscountRule row in /dashboard/admin/discount-rules.

export interface CategoryDef {
  /** Stable enum stored in Product.category (UPPERCASE, no spaces). */
  key: string;
  /** Human label used in UIs. */
  label: string;
  /** Singular noun the AI SEO generator should use in titles ("Watch", "Eyeglasses"). */
  seoNoun: string;
}

// SEO nouns set per the user's Add Product mapping decisions
// (round 1 + round 2). Singular forms preferred for SEO except where the
// user explicitly asked for the plural ("Reading Glasses", "Contact Lenses").
//
// Note: SUNGLASSES → "Sunglass" (per round 1 1.1)
//       SPECTACLES → "Optical Frame" (per round 1 SPECTACLES.1.1)
//       COMPUTER_GLASSES → "Blue-Light Glass" (per round 2)
//       SAFETY_GLASSES → "Safety Goggle" (per round 2)
//       and so on — see also COLOR_CONTACT_LENSES added in round 2.
export const CATEGORIES: CategoryDef[] = [
  { key: "SPECTACLES",          label: "Spectacles",          seoNoun: "Optical Frame" },
  { key: "CLIP_ON_FRAMES",      label: "Clip-On Frames",      seoNoun: "Clip-On Frame" },
  { key: "SUNGLASSES",          label: "Sunglasses",          seoNoun: "Sunglass" },
  { key: "READING_GLASSES",     label: "Reading Glasses",     seoNoun: "Reading Glasses" },
  { key: "COMPUTER_GLASSES",    label: "Computer Glasses",    seoNoun: "Blue-Light Glass" },
  { key: "SAFETY_GLASSES",      label: "Safety Glasses",      seoNoun: "Safety Goggle" },
  { key: "CONTACT_LENSES",      label: "Contact Lenses",      seoNoun: "Contact Lenses" },
  { key: "COLOR_CONTACT_LENSES", label: "Color Contact Lenses", seoNoun: "Color Contact Lens" },
  { key: "SMARTGLASSES",        label: "Smartglasses",        seoNoun: "Smartglass" },
  { key: "WATCHES",             label: "Watches",             seoNoun: "Watch" },
  { key: "SMARTWATCHES",        label: "Smartwatches",        seoNoun: "Smartwatch" },
  { key: "ACCESSORIES",         label: "Accessories",         seoNoun: "Accessory" },
];

export const CATEGORY_KEYS = CATEGORIES.map((c) => c.key);
export type CategoryKey = (typeof CATEGORY_KEYS)[number];

/** All category keys (plus legacy values the DB might still have) that should
 * be treated as equivalent to a canonical key — helpful for the pull route
 * when Shopify returns non-canonical productType values. */
export const CATEGORY_ALIASES: Record<string, string> = {
  // Legacy / Shopify productType spellings — all uppercased before lookup.
  WATCH: "WATCHES",
  SUNGLASS: "SUNGLASSES",
  "CONTACT LENS": "CONTACT_LENSES",
  "CONTACT LENSES": "CONTACT_LENSES",
  "CLIP-ON": "CLIP_ON_FRAMES",
  "CLIP-ON FRAMES": "CLIP_ON_FRAMES",
  "SMART GLASSES": "SMARTGLASSES",
  SMARTGLASS: "SMARTGLASSES",
  "SMART WATCH": "SMARTWATCHES",
  "SMART WATCHES": "SMARTWATCHES",
  SMARTWATCH: "SMARTWATCHES",
  "READING GLASSES": "READING_GLASSES",
  "COMPUTER GLASSES": "COMPUTER_GLASSES",
  "SAFETY GLASS": "SAFETY_GLASSES",
  "SAFETY GLASSES": "SAFETY_GLASSES",
  SOLUTIONS: "CONTACT_LENSES", // legacy: "SOLUTIONS" meant contact-lens care
  "COLOR CONTACT LENS": "COLOR_CONTACT_LENSES",
  "COLOR CONTACT LENSES": "COLOR_CONTACT_LENSES",
  "COLOUR CONTACT LENS": "COLOR_CONTACT_LENSES",
  "COLOUR CONTACT LENSES": "COLOR_CONTACT_LENSES",
  "COSMETIC CONTACT LENS": "COLOR_CONTACT_LENSES",
};

/** Normalize any freeform category value (from Shopify or legacy DB) to a
 * canonical CategoryKey. Returns the uppercased input if no alias matches. */
export function normalizeCategory(raw: string | null | undefined): string {
  if (!raw) return "";
  const upper = raw.trim().toUpperCase();
  return CATEGORY_ALIASES[upper] || upper;
}

export function categoryLabel(key: string | null | undefined): string {
  if (!key) return "";
  const norm = normalizeCategory(key);
  return CATEGORIES.find((c) => c.key === norm)?.label || key;
}
