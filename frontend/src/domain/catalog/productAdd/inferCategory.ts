import { CATEGORIES } from './categoryFields';

// ============================================================================
// Category inference (SHARED: variant flow, catalog-doc mapping, smartglasses
// tile highlighting). Formerly co-located with the removed Catalog Autopilot
// section; these helpers are feature-independent and stay.
// ============================================================================
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
  // ONE tile: the retired "Smartglasses (Sunglass)" spellings resolve to SMTFR
  // like every other one. Resolving them to SMTSG left the picker with nothing
  // to highlight and categoryName('SMTSG') === '', so the review row showed a
  // dash. SMTSG survives only as a FIELD-LIST alias (CATEGORY_FIELDS.SMTSG).
  SMARTSUNGLASS: 'SMTFR', SMARTSUNGLASSES: 'SMTFR', SMTSG: 'SMTFR',
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
  ['smart sunglass', 'SMTFR'],
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
