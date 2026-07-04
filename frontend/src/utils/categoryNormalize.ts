// ============================================================================
// IMS 2.0 - Product category normalizer (frontend mirror of the backend
// product_master._CATEGORY_ALIASES registry)
// ============================================================================
// Products on the spine store CANONICAL long-form categories (FRAME, SUNGLASS,
// OPTICAL_LENS, ...). Historic UI code compares against short picker codes
// ('SG'/'FR'), legacy plurals ('SUNGLASSES'/'FRAMES'/'RX_LENSES'), or mixed
// variants -- which silently never match, so category filters showed nothing
// (owner bug 2026-07-04: "products shown on All, not when Sunglasses selected").
//
// Rule: ALWAYS compare categories through canonicalCategory()/sameCategory().
// Unknown values pass through upper-snaked (fail-open) so free-form legacy data
// still equals itself.

const ALIASES: Record<string, string> = {
  // short picker codes (frontend CATEGORIES arrays)
  FR: 'FRAME',
  SG: 'SUNGLASS',
  LS: 'OPTICAL_LENS',
  LENS: 'OPTICAL_LENS',
  RG: 'READING_GLASSES',
  CL: 'CONTACT_LENS',
  WT: 'WATCH',
  CK: 'WALL_CLOCK',
  CLOCK: 'WALL_CLOCK',
  WRIST_WATCH: 'WATCH',
  HA: 'HEARING_AID',
  SMTSG: 'SMARTGLASSES',
  SMTFR: 'SMARTGLASSES',
  SMART_FRAME: 'SMARTGLASSES',
  SMART_SUNGLASS: 'SMARTGLASSES',
  SMTWT: 'SMARTWATCH',
  SMART_WATCH: 'SMARTWATCH',
  ACC: 'ACCESSORIES',
  SVC: 'SERVICES',
  SERVICE: 'SERVICES',
  // legacy plurals / variants
  FRAMES: 'FRAME',
  SPECTACLE_FRAME: 'FRAME',
  EYEGLASS_FRAME: 'FRAME',
  SUNGLASSES: 'SUNGLASS',
  OPTICAL_LENSES: 'OPTICAL_LENS',
  RX_LENSES: 'OPTICAL_LENS',
  RX_LENS: 'OPTICAL_LENS',
  LENSES: 'OPTICAL_LENS',
  SPECTACLE_LENS: 'OPTICAL_LENS',
  CONTACT_LENSES: 'CONTACT_LENS',
  COLOUR_CONTACTS: 'COLORED_CONTACT_LENS',
  COLOR_CONTACTS: 'COLORED_CONTACT_LENS',
  COLORED_CONTACT_LENSES: 'COLORED_CONTACT_LENS',
  WATCHES: 'WATCH',
  WRIST_WATCHES: 'WATCH',
  SMARTWATCHES: 'SMARTWATCH',
  SMART_GLASSES: 'SMARTGLASSES',
  WALL_CLOCKS: 'WALL_CLOCK',
  READING_GLASS: 'READING_GLASSES',
  HEARING_AIDS: 'HEARING_AID',
  ACCESSORY: 'ACCESSORIES',
};

/** Normalise any category spelling (canonical, short code, plural, mixed case)
 *  to the canonical long-form key the product spine stores. Unknown values
 *  pass through upper-snaked so legacy free-form data still self-compares. */
export function canonicalCategory(value: string | null | undefined): string {
  if (!value) return '';
  const raw = String(value).trim().toUpperCase().replace(/[-\s]+/g, '_');
  return ALIASES[raw] ?? raw;
}

/** True when two category spellings refer to the same canonical category. */
export function sameCategory(
  a: string | null | undefined,
  b: string | null | undefined,
): boolean {
  const ca = canonicalCategory(a);
  return ca !== '' && ca === canonicalCategory(b);
}
