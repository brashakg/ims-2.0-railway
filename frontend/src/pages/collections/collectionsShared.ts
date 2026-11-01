// ============================================================================
// IMS 2.0 - Collections Phase 1: shared display helpers
// ============================================================================
// Tiny formatting helpers shared by the Collections list / builder / detail
// pages so the three screens can never drift on how money, percentages and
// the stock-value basis are rendered.

import type { InsightRule } from '../../services/api/collectionsInsights';

/** Indian-locale rupees, no paise. null/undefined/NaN -> em dash. */
export function rupee(n?: number | null): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return `₹${Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;
}

/** Plain integer count, Indian grouping. null/undefined -> em dash. */
export function fmtInt(n?: number | null): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return Number(n).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

/** Note beside a stock value (always at cost: pieces x average cost, S4)
 *  when some pieces have no cost yet, so the figure is too low. */
export function basisLabel(uncostedUnits?: number | null): string | null {
  const n = Number(uncostedUnits || 0);
  return n > 0 ? `${fmtInt(n)} pc${n === 1 ? '' : 's'} no cost` : null;
}

/** Percentage display tolerant of both a 0..1 fraction and a 0..100 number
 *  (the insights contract doesn't pin the scale; sell-through is typically a
 *  fraction). null -> em dash. */
export function pct(n?: number | null): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  const scaled = Math.abs(v) <= 1 ? v * 100 : v;
  return `${scaled.toLocaleString('en-IN', { maximumFractionDigits: 1 })}%`;
}

/** Days-of-cover with the 180+ display cap. null -> em dash. */
export function daysOfCover(n?: number | null): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  if (v > 180) return '180+';
  return v.toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

/** The chip builder's rules (Track 2 contract): each chip group is ONE
 *  EQUALS rule; groups AND together; price bounds are GREATER_THAN /
 *  LESS_THAN on 'price'. Several values on the SAME field cannot be one rule
 *  the website reads (Shopify has no "any of"; IMS counts such a collection
 *  as 0 products) -- those fields are returned in ``multi`` and the page
 *  refuses to create until one value per field is picked. Pure. */
export function compileBuilderRules(input: {
  category: string;
  brands: string[];
  attrs: { name: string; label: string; values: string[] }[];
  tags: string[];
  priceMin: string;
  priceMax: string;
}): { rules: InsightRule[]; multi: string[] } {
  const rules: InsightRule[] = [];
  const multi: string[] = [];
  const one = (field: string, label: string, values: string[]) => {
    if (values.length === 1) rules.push({ field, relation: 'EQUALS', value: values[0] });
    else if (values.length > 1) multi.push(label);
  };
  if (input.category) rules.push({ field: 'category', relation: 'EQUALS', value: input.category });
  one('brand', 'Brand', input.brands);
  for (const a of input.attrs) one(a.name, a.label, a.values);
  one('tag', 'Tags', input.tags);
  const min = Number(input.priceMin);
  const max = Number(input.priceMax);
  if (input.priceMin.trim() !== '' && Number.isFinite(min)) rules.push({ field: 'price', relation: 'GREATER_THAN', value: min });
  if (input.priceMax.trim() !== '' && Number.isFinite(max)) rules.push({ field: 'price', relation: 'LESS_THAN', value: max });
  return { rules, multi };
}
