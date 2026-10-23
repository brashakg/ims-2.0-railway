// Quick Add - fill helpers (PR 3 of the catalogue UX list, owner-approved
// 2026-10-23). Pure functions only: "Copy specs from last product" and the
// soft warnings. NOTHING here blocks a save -- the validator alone does that.

import { variantFieldRule } from '../../../domain/catalog/productAdd/variantRules';

const alnum = (s: string) => s.toUpperCase().replace(/[^A-Z0-9]/g, '');

// The model itself is typed fresh for a new model; never copied.
const NOT_COPIED = new Set(['brand_name', 'model_no', 'model_name']);

/** The repeating spec fields of the last product saved this session that
 *  this (same-category) form still has blank. The variant rulebook decides
 *  what repeats (variantFieldRule copy/flag): it never copies a SKU, a colour,
 *  a barcode/GTIN or an unknown key; photos and prices are not attributes. A
 *  value already typed is never overwritten. */
export function copyableSpecs(
  category: string,
  last: { category: string; attributes: Record<string, string> } | null,
  current: Record<string, string>,
): Record<string, string> {
  if (!last || !category || last.category !== category) return {};
  const out: Record<string, string> = {};
  // A sub-brand / label belongs to its brand: never offered across brands.
  const sameBrand =
    alnum(last.attributes.brand_name || '') === alnum(current.brand_name || '');
  Object.entries(last.attributes).forEach(([k, v]) => {
    const val = String(v ?? '').trim();
    if (!val || NOT_COPIED.has(k) || String(current[k] ?? '').trim()) return;
    if (!sameBrand && (k === 'subbrand' || k === 'label')) return;
    if (variantFieldRule(category, k) === 'never') return;
    out[k] = val;
  });
  return out;
}

const num = (s: string | undefined) => {
  const t = String(s ?? '').trim();
  return t && /^\d+(\.\d+)?$/.test(t) ? Number(t) : null;
};

/** Amber, never-blocking hints. Size rules (owner 2026-10-22): lens size 99
 *  is a UNILENS (single shield lens) model -- valid, never flagged; a bridge
 *  of 01 is not a real bridge. */
export function softWarnings(input: {
  attributes: Record<string, string>;
  mrp: string;
  /** The MRP of the model colour this one was started from (variant mode). */
  modelMrp?: string;
  imageCount: number;
  /** A size-only variant or a shop-first save can be fine with one photo. */
  showPhotoHint: boolean;
}): string[] {
  const a = input.attributes;
  const out: string[] = [];
  // The brand as whole word(s) of the model ('RAYBAN RB4165', 'Ray-Ban
  // RB4165'), never a substring of a code ('OAKLEYX' or 'PRADAMIX' stay quiet).
  const brand = alnum(a.brand_name || '');
  const words = (t: string) => t.toUpperCase().split(/[^A-Z0-9]+/).filter(Boolean);
  const bw = words(a.brand_name || '');
  const mw = words(a.model_no || a.model_name || '');
  const repeats =
    mw.includes(brand) ||
    mw.some((_, i) => bw.length > 1 && mw.slice(i, i + bw.length).join(' ') === bw.join(' '));
  if (brand.length >= 3 && repeats) {
    out.push('The model number repeats the brand name - type just the model (e.g. RB4165).');
  }
  const lens = num(a.lens_size);
  if (lens !== null && lens !== 99 && (lens < 30 || lens > 80)) {
    out.push(`Lens size ${a.lens_size} looks unusual (most are 40-70; 99 is a single-lens model).`);
  }
  const bridge = num(a.bridge_width);
  if (bridge !== null && (bridge < 10 || bridge > 30)) {
    out.push(`Bridge ${a.bridge_width} looks wrong (most are 14-24).`);
  }
  const temple = num(a.temple_length);
  if (temple !== null && (temple < 110 || temple > 160)) {
    out.push(`Temple ${a.temple_length} looks unusual (most are 135-150).`);
  }
  const mrp = num(input.mrp);
  const modelMrp = num(input.modelMrp);
  if (mrp !== null && modelMrp && Math.abs(mrp - modelMrp) / modelMrp > 0.5) {
    out.push(`MRP ${input.mrp} is far from this model's other colour (${input.modelMrp}).`);
  }
  if (input.showPhotoHint && input.imageCount < 2) {
    out.push(
      input.imageCount === 0
        ? 'No photos yet - the website needs at least one; two or more sell better.'
        : 'Only 1 photo - two or more sell better on the website.',
    );
  }
  return out;
}

/** What a description draft was written from (the specs it reads). */
export function specKey(attributes: Record<string, string>, weight: string): string {
  const filled = Object.entries(attributes)
    .filter(([, v]) => String(v ?? '').trim())
    .sort(([x], [y]) => x.localeCompare(y));
  return JSON.stringify([filled, String(weight ?? '').trim()]);
}
