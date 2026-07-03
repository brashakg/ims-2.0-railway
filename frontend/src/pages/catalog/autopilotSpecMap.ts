// ============================================================================
// IMS 2.0 - Catalog Autopilot v2: scraped-spec -> category-field mapper
// ============================================================================
// THE core of "Autopilot fills every field, the operator only verifies".
// Scraped product pages return free-form spec labels ("Lens Width", "BRIDGE
// (DBL)", "Replacement schedule"); the Add Product form renders only the
// category's DECLARED attribute fields (lens_size, bridge_width, modality,
// ...). This pure, unit-tested module bridges the two:
//
//   mapSpecsToCategoryFields(category, specs, query, title, description)
//     -> { mapped: {attributeName: value}, extras: {rawSpecKey: value} }
//
// - Synonym tables per attribute, matched case/punctuation-insensitively on
//   the spec KEYS (exact first, then a guarded loose pass).
// - EYEWEAR SIZE-STRING parsing: "52-18-140" / "52[]18-140" / "52/18/140"
//   (from a spec value, the user's Size input, the title, or the description)
//   -> lens_size=52, bridge_width=18, temple_length=140.
// - Value cleanup: units stripped for number fields ("50 mm" -> "50"), enum
//   coercion for select fields (modality "Monthly disposable" -> MONTHLY).
// - Fallbacks: colour from the user's Colour input, model/brand from the query.
// - Anything unmapped is returned under `extras` (surfaced to the operator AND
//   kept as passthrough attributes — nothing scraped is ever silently lost).
//
// Pure + side-effect-free: no network, no DOM, no state. getCategoryFields is
// only called at run time (its registry cache is optional — the local
// CATEGORY_FIELDS metadata is the deterministic fallback used in tests).

import { getCategoryFields, type CategoryField } from './productAddShared';

export interface AutopilotQuery {
  brand?: string | null;
  model?: string | null;
  color?: string | null;
  size?: string | null;
}

export interface SpecMapResult {
  /** attributeName -> cleaned value, restricted to the category's declared fields. */
  mapped: Record<string, string>;
  /** Raw scraped specs that no declared field consumed (operator-visible). */
  extras: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Normalisation helpers
// ---------------------------------------------------------------------------

/** Lower-case, collapse all non-alphanumerics to single spaces. */
export function normSpecKey(key: unknown): string {
  return String(key ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

const firstNumber = (value: string): string => {
  const m = value.match(/-?\d+(?:\.\d+)?/);
  return m ? m[0] : '';
};

// ---------------------------------------------------------------------------
// Eyewear size-string parsing ("52-18-140", "52[]18-140", "52/18/140", "52 18 140")
// ---------------------------------------------------------------------------
// The single biggest fill win for frames/sunglasses/readers: one size token
// carries lens/bridge/temple. Separators seen in the wild: hyphen, en/em dash,
// slash, x, the square glyph, or plain spaces. Values are sanity-ranged so a
// model number can't masquerade as a size.

const SIZE_TRIPLE_RE =
  /(\d{2})\s*[-–—□◻☐/xX×\\\s]{1,3}\s*(\d{2})(?:\s*[-–—□◻☐/xX×\\\s]{1,3}\s*(\d{3}))?/;

export interface EyewearSize {
  lens?: string;
  bridge?: string;
  temple?: string;
}

/** Parse an eyewear size string from free text. Returns {} when nothing sane. */
export function parseEyewearSize(text: unknown): EyewearSize {
  const s = String(text ?? '');
  if (!s.trim()) return {};
  const m = s.match(SIZE_TRIPLE_RE);
  if (m) {
    const lens = parseInt(m[1], 10);
    const bridge = parseInt(m[2], 10);
    const temple = m[3] ? parseInt(m[3], 10) : NaN;
    const lensOk = lens >= 35 && lens <= 80;
    const bridgeOk = bridge >= 8 && bridge <= 35;
    if (lensOk && bridgeOk) {
      const out: EyewearSize = { lens: String(lens), bridge: String(bridge) };
      if (Number.isFinite(temple) && temple >= 100 && temple <= 200) {
        out.temple = String(temple);
      }
      return out;
    }
  }
  // A bare 2-digit size ("52") -> lens size only.
  const bare = s.trim().match(/^(\d{2})(?:\s*mm)?$/i);
  if (bare) {
    const lens = parseInt(bare[1], 10);
    if (lens >= 35 && lens <= 80) return { lens: String(lens) };
  }
  return {};
}

// ---------------------------------------------------------------------------
// Synonym rules
// ---------------------------------------------------------------------------
// `exact`: normalised spec keys that map 1:1. `loose`: substrings that may
// claim a spec key in the second pass, guarded by `reject` (a key matching
// reject can never be claimed loosely — e.g. "dial colour" must not fall into
// colour_code). Rules are filtered to the category's DECLARED fields before
// matching, so cross-category collisions (CL "diameter" vs frame "lens
// diameter") never interact.

interface SynonymRule {
  attr: string;
  exact: string[];
  loose?: string[];
  reject?: RegExp;
}

const COLOUR_QUALIFIER = /dial|strap|band|belt|case|body|temple|lens|tint/;

const SYNONYM_RULES: SynonymRule[] = [
  // Identity
  { attr: 'brand_name', exact: ['brand', 'brand name', 'manufacturer'] },
  {
    attr: 'model_no',
    exact: ['model no', 'model number', 'model code', 'model', 'style code', 'product code', 'style'],
  },
  { attr: 'model_name', exact: ['model name', 'model', 'product name', 'style name'] },
  { attr: 'subbrand', exact: ['sub brand', 'subbrand', 'collection'] },
  // Eyewear dimensions
  {
    attr: 'lens_size',
    exact: ['lens size', 'lens width', 'eye size', 'lens diameter', 'lens size mm', 'lens width mm', 'lens diameter mm'],
    loose: ['lens width', 'lens size', 'eye size'],
  },
  {
    attr: 'bridge_width',
    exact: ['bridge', 'bridge size', 'bridge width', 'dbl', 'nose bridge', 'bridge mm', 'bridge width mm'],
    loose: ['bridge'],
  },
  {
    attr: 'temple_length',
    exact: ['temple', 'temple length', 'temples', 'arm length', 'temple size', 'temple length mm'],
    loose: ['temple length', 'arm length'],
  },
  // Colours
  {
    attr: 'colour_code',
    exact: ['colour code', 'color code', 'colourway', 'colorway', 'frame colour', 'frame color'],
    loose: ['colour', 'color'],
    reject: COLOUR_QUALIFIER,
  },
  {
    attr: 'colour_name',
    exact: ['colour name', 'color name'],
    loose: ['colour', 'color'],
    reject: COLOUR_QUALIFIER,
  },
  { attr: 'dial_colour', exact: ['dial colour', 'dial color', 'dial'] },
  {
    attr: 'belt_colour',
    exact: ['belt colour', 'belt color', 'strap colour', 'strap color', 'band colour', 'band color'],
  },
  { attr: 'body_colour', exact: ['body colour', 'body color', 'case colour', 'case color'] },
  { attr: 'dial_color', exact: ['dial colour', 'dial color', 'dial'] },
  { attr: 'strap_material', exact: ['strap material', 'band material', 'belt material'] },
  // Watch / clock dimensions
  {
    attr: 'dial_size',
    exact: ['dial size', 'case diameter', 'case size', 'dial diameter', 'case width', 'case diameter mm'],
  },
  {
    attr: 'belt_size',
    exact: ['belt size', 'strap size', 'band size', 'strap width', 'band width', 'lug width'],
  },
  { attr: 'battery_size', exact: ['battery size', 'battery', 'battery type'] },
  { attr: 'watch_category', exact: ['watch category', 'watch type', 'movement', 'display type'] },
  { attr: 'clock_category', exact: ['clock category', 'clock type'] },
  // Contact lenses
  { attr: 'cl_series', exact: ['series', 'cl series', 'product series'] },
  {
    attr: 'modality',
    exact: ['modality', 'replacement schedule', 'replacement frequency', 'wear duration', 'wear schedule', 'replacement', 'disposability', 'usage duration'],
  },
  { attr: 'base_curve', exact: ['base curve', 'bc', 'base curve bc', 'base curve mm'] },
  { attr: 'diameter', exact: ['diameter', 'dia', 'diameter dia', 'lens diameter', 'diameter mm'] },
  { attr: 'power', exact: ['power', 'sph', 'sphere', 'spherical power', 'power sph'] },
  { attr: 'cl_cyl', exact: ['cylinder', 'cyl', 'cylindrical power'] },
  { attr: 'cl_axis', exact: ['axis'] },
  { attr: 'cl_add', exact: ['add', 'addition', 'add power', 'near addition'] },
  {
    attr: 'pack',
    exact: ['pack size', 'pack', 'lenses per box', 'pack of', 'quantity per box', 'box size', 'count', 'pieces per pack'],
  },
  { attr: 'expiry_date', exact: ['expiry', 'expiry date', 'expiration date', 'use before'] },
  // Optical lenses
  { attr: 'index', exact: ['index', 'refractive index'] },
  { attr: 'coating', exact: ['coating', 'lens coating', 'coatings'] },
  { attr: 'lens_category', exact: ['lens category', 'lens type', 'vision type', 'design'] },
  { attr: 'sph', exact: ['sph', 'sphere', 'spherical power'] },
  { attr: 'cyl', exact: ['cyl', 'cylinder'] },
  { attr: 'axis', exact: ['axis'] },
  { attr: 'add', exact: ['add', 'addition', 'add power'] },
  // Materials / frame details
  { attr: 'frame_material', exact: ['frame material', 'front material'] },
  { attr: 'material', exact: ['material', 'lens material'] },
  { attr: 'frame_type', exact: ['frame type', 'rim type', 'rim'] },
  { attr: 'polarization', exact: ['polarization', 'polarisation', 'polarized', 'polarised'] },
  { attr: 'uv_protection', exact: ['uv protection', 'uv', 'uv400', 'uv rating'] },
  { attr: 'tint', exact: ['tint', 'lens tint'] },
  // Rich eyewear field set (FRAME + SUNGLASS). frame_color/temple_color are
  // guarded so a bare "colour" doesn't claim them (they need the qualifier).
  { attr: 'shape', exact: ['shape', 'frame shape', 'lens shape', 'style shape'] },
  { attr: 'frame_color', exact: ['frame colour', 'frame color', 'front colour', 'front color'], loose: ['frame colour', 'frame color'] },
  { attr: 'temple_color', exact: ['temple colour', 'temple color', 'arm colour', 'arm color'] },
  { attr: 'temple_material', exact: ['temple material', 'arm material', 'side material'] },
  { attr: 'lens_colour', exact: ['lens colour', 'lens color', 'tint colour', 'tint color'] },
  { attr: 'lens_material', exact: ['lens material', 'lens type material'] },
  { attr: 'blue_cut_lens', exact: ['blue cut', 'blue cut lens', 'blue light', 'blue light filter', 'blue block'] },
  { attr: 'gender', exact: ['gender', 'suitable for', 'target group'] },
  { attr: 'country_of_origin', exact: ['country of origin', 'origin', 'made in', 'manufactured in'] },
  { attr: 'warranty', exact: ['warranty', 'guarantee', 'warranty period'] },
  { attr: 'full_model_no', exact: ['full model no', 'full model number', 'full style code'] },
  { attr: 'upc', exact: ['upc', 'upc code'] },
  { attr: 'gtin', exact: ['gtin', 'ean', 'ean code', 'barcode'] },
  // Hearing aids
  { attr: 'machine_type', exact: ['machine type', 'hearing aid type', 'aid type', 'wearing style'] },
  {
    attr: 'machine_capacity',
    exact: ['machine capacity', 'capacity', 'severity', 'hearing loss level', 'fitting range'],
  },
  { attr: 'serial_no', exact: ['serial no', 'serial number'] },
  // Accessories / misc
  { attr: 'accessory_type', exact: ['accessory type', 'type of accessory'] },
  { attr: 'size', exact: ['size', 'frame size'] },
  { attr: 'year_of_launch', exact: ['year of launch', 'launch year', 'release year', 'year'] },
];

// ---------------------------------------------------------------------------
// Value coercion per attribute (enum/select + numeric cleanup)
// ---------------------------------------------------------------------------

/** Map a free-form modality value onto the CL modality enum ('' if unknown).
 *  Order matters: "3 month"/quarter before the generic "month" check. */
export function coerceModality(value: unknown): string {
  const v = String(value ?? '').toLowerCase();
  if (!v.trim()) return '';
  if (/(color|colour|cosmetic)/.test(v)) return 'COLOR';
  if (/(quarter|3[\s-]*month|90[\s-]*day)/.test(v)) return 'QUARTERLY';
  if (/(fortnight|bi[\s-]*week|2[\s-]*week|two[\s-]*week|14[\s-]*day)/.test(v)) return 'FORTNIGHTLY';
  if (/(year|annual|12[\s-]*month)/.test(v)) return 'YEARLY';
  if (/(month|30[\s-]*day)/.test(v)) return 'MONTHLY';
  if (/(daily|1[\s-]*day|one[\s-]*day)/.test(v)) return 'DAILY';
  return '';
}

/** Map a free-form coating value onto the LS coating options ('' if unknown). */
function coerceCoating(value: string): string {
  const v = value.toLowerCase();
  if (/(photochrom)/.test(v)) return 'Photochromic';
  if (/(transition)/.test(v)) return 'Transitions';
  if (/(blue)/.test(v)) return 'Blue Cut';
  if (/(polar)/.test(v)) return 'Polarized';
  if (/(anti[\s-]*reflect|\barc\b|anti[\s-]*glare)/.test(v)) return 'ARC';
  if (/(hard[\s-]*coat|\bhc\b)/.test(v)) return 'HC';
  if (/(uncoated|\buc\b)/.test(v)) return 'UC';
  return '';
}

/** Clean a raw scraped value for a target field. Returns '' when the value
 *  can't be sensibly coerced (the caller then leaves the field unfilled). */
function cleanValue(field: CategoryField, raw: string): string {
  const value = raw.trim();
  if (!value) return '';
  if (field.name === 'modality') return coerceModality(value);
  if (field.type === 'number') {
    return firstNumber(value);
  }
  if (field.type === 'select' && field.options && field.options.length > 0) {
    // 1) exact / case-insensitive option match.
    const direct = field.options.find((o) => o.toLowerCase() === value.toLowerCase());
    if (direct) return direct;
    // 2) known coercions.
    if (field.name === 'coating') {
      const c = coerceCoating(value);
      if (c) return c;
    }
    // 3) numeric options (pack sizes, lens index): match on the number.
    const num = firstNumber(value);
    if (num) {
      const numMatch = field.options.find(
        (o) => parseFloat(o) === parseFloat(num) && Number.isFinite(parseFloat(o))
      );
      if (numMatch) return numMatch;
    }
    // 4) option contained in the value ("Analog watch" -> "Analog").
    const contained = field.options.find((o) => value.toLowerCase().includes(o.toLowerCase()));
    if (contained) return contained;
    return '';
  }
  // Text/date fields: strip a trailing unit ("52 mm" -> "52" only for pure
  // dimension-ish strings; otherwise keep the text as-is, trimmed).
  return value.replace(/^(\d+(?:\.\d+)?)\s*mm$/i, '$1');
}

// ---------------------------------------------------------------------------
// The mapper
// ---------------------------------------------------------------------------

const EYE_SIZE_FIELDS = ['lens_size', 'bridge_width', 'temple_length'] as const;

export function mapSpecsToCategoryFields(
  category: string,
  specs: Record<string, unknown> | undefined | null,
  query: AutopilotQuery = {},
  title?: string | null,
  description?: string | null
): SpecMapResult {
  const mapped: Record<string, string> = {};
  const extras: Record<string, string> = {};

  const fields = category ? getCategoryFields(category) : [];
  const fieldByName = new Map(fields.map((f) => [f.name, f]));

  // Spec entries (string values only, 'category' label excluded — it is job
  // metadata, not a form field).
  const entries: Array<{ rawKey: string; key: string; value: string; consumed: boolean }> = [];
  Object.entries(specs || {}).forEach(([k, v]) => {
    if (v === null || v === undefined) return;
    const value = String(v).trim();
    if (!value) return;
    if (normSpecKey(k) === 'category') return;
    entries.push({ rawKey: k, key: normSpecKey(k), value, consumed: false });
  });

  const rulesForCategory = SYNONYM_RULES.filter((r) => fieldByName.has(r.attr));

  const claim = (attr: string, entry: { value: string; consumed: boolean }): void => {
    if (mapped[attr]) return;
    const field = fieldByName.get(attr);
    if (!field) return;
    const cleaned = cleanValue(field, entry.value);
    if (!cleaned) return;
    mapped[attr] = cleaned;
    entry.consumed = true;
  };

  // Pass 1: exact synonym matches (highest confidence).
  for (const rule of rulesForCategory) {
    if (mapped[rule.attr]) continue;
    for (const entry of entries) {
      if (entry.consumed) continue;
      if (rule.exact.includes(entry.key)) {
        claim(rule.attr, entry);
        if (mapped[rule.attr]) break;
      }
    }
  }

  // Pass 2: guarded loose matches (synonym contained in the spec key).
  for (const rule of rulesForCategory) {
    if (mapped[rule.attr] || !rule.loose || rule.loose.length === 0) continue;
    for (const entry of entries) {
      if (entry.consumed) continue;
      if (rule.reject && rule.reject.test(entry.key)) continue;
      if (rule.loose.some((syn) => entry.key.includes(syn))) {
        claim(rule.attr, entry);
        if (mapped[rule.attr]) break;
      }
    }
  }

  // Eyewear size string: spec values under size-ish keys, then the user's
  // Size input, then title, then description. First sane parse wins; it only
  // fills the size fields the category declares and that are still empty.
  if (EYE_SIZE_FIELDS.some((f) => fieldByName.has(f))) {
    const sizeTexts: unknown[] = [];
    entries.forEach((e) => {
      if (!e.consumed && /(size|dimension|measurement)/.test(e.key)) sizeTexts.push(e.value);
    });
    sizeTexts.push(query.size, title, description);
    for (const text of sizeTexts) {
      const parsed = parseEyewearSize(text);
      if (!parsed.lens && !parsed.bridge && !parsed.temple) continue;
      if (parsed.lens && fieldByName.has('lens_size') && !mapped.lens_size) {
        mapped.lens_size = parsed.lens;
      }
      if (parsed.bridge && fieldByName.has('bridge_width') && !mapped.bridge_width) {
        mapped.bridge_width = parsed.bridge;
      }
      if (parsed.temple && fieldByName.has('temple_length') && !mapped.temple_length) {
        mapped.temple_length = parsed.temple;
      }
      // Mark the source spec entry consumed if it supplied the parse.
      const src = entries.find((e) => !e.consumed && String(e.value) === String(text));
      if (src) src.consumed = true;
      break;
    }
  }

  // Query fallbacks: the operator's own inputs are trusted when the scrape
  // didn't supply a value for a declared field.
  const q = {
    brand: String(query.brand ?? '').trim(),
    model: String(query.model ?? '').trim(),
    color: String(query.color ?? '').trim(),
    size: String(query.size ?? '').trim(),
  };
  if (q.brand && fieldByName.has('brand_name') && !mapped.brand_name) mapped.brand_name = q.brand;
  if (q.model) {
    if (fieldByName.has('model_no') && !mapped.model_no) mapped.model_no = q.model;
    if (fieldByName.has('model_name') && !mapped.model_name) mapped.model_name = q.model;
  }
  if (q.color) {
    if (fieldByName.has('colour_code') && !mapped.colour_code) mapped.colour_code = q.color;
    if (fieldByName.has('colour_name') && !mapped.colour_name) mapped.colour_name = q.color;
  }

  // Everything unconsumed is an extra (operator-visible passthrough).
  entries.forEach((e) => {
    if (!e.consumed) extras[e.rawKey] = e.value;
  });

  return { mapped, extras };
}
