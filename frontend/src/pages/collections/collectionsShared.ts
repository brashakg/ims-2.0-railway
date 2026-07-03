// ============================================================================
// IMS 2.0 - Collections Phase 1: shared display helpers
// ============================================================================
// Tiny formatting helpers shared by the Collections list / builder / detail
// pages so the three screens can never drift on how money, percentages and
// the stock-value basis are rendered.

import type { ValueBasis } from '../../services/api/collectionsInsights';

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

/** Qualifier shown next to a stock value when it is NOT at cost basis. */
export function basisLabel(basis?: ValueBasis | string | null): string | null {
  if (basis === 'offer') return 'at selling price';
  if (basis === 'mixed') return 'mixed basis';
  return null; // 'cost' (or unknown) -> no qualifier
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
