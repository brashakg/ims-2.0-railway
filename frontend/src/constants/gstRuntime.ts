// ============================================================================
// IMS 2.0 - Runtime GST resolver (reads the editable HSN->GST master)
// ============================================================================
// Keeps the POS on-screen preview + invoice in sync with the SUPERADMIN-edited
// HSN->GST master that the backend bills from. The backend is always the
// source of truth on order-create (it recomputes tax); this only mirrors the
// rate for the live preview so the cashier sees the same number that is billed.
//
// Fail-soft: until the master loads (or if the fetch fails), resolveGstRate()
// falls back to the static GST 2.0 constants in constants/gst.ts. Loaded once
// per session (see AppLayout) + after an edit in Settings -> HSN & GST Rates.
//
// EVERY table below arrives from GET /products/gst-rates. The category -> HSN
// map and the category -> master-row hint used to be hand-copied here from
// backend api/services/gst_rates.py, and the copies had drifted (smartglasses
// were pointed at the sunglasses HSN 900410 instead of 852580). They are now
// read off the server, which is the only place that can be right.

import api from '../services/api/client';
import { getGSTRateByCategory } from './gst';

let _byHsn: Record<string, number> = {};
let _byCat: Record<string, number> = {};
// Server-fed: category spelling -> the category_hint on a master row, and
// category -> canonical HSN code. Empty until the endpoint answers.
let _hint: Record<string, string> = {};
let _hsnByCat: Record<string, string> = {};
let _loaded = false;

const LS_KEY = 'ims_hsn_gst_rates';

function _normalizeCat(category?: string | null): string {
  if (!category) return '';
  const raw = String(category).toUpperCase().replace(/[-\s]+/g, '_');
  return _hint[raw] || raw;
}

/** Fetch the HSN->GST master into the in-memory cache. Safe to call repeatedly;
 *  never throws. Falls back to a localStorage snapshot if the network fails. */
export async function loadHsnRates(): Promise<void> {
  try {
    const res = await api.get('/products/gst-rates');
    const data = res.data || {};
    _byHsn = data.by_hsn || {};
    _byCat = data.by_cat || {};
    _hint = data.category_hint || {};
    _hsnByCat = data.hsn_by_category || {};
    _loaded = true;
    try {
      localStorage.setItem(
        LS_KEY,
        JSON.stringify({ byHsn: _byHsn, byCat: _byCat, hint: _hint, hsnByCat: _hsnByCat }),
      );
    } catch {
      /* ignore quota / private-mode errors */
    }
  } catch {
    if (!_loaded) {
      try {
        const cached = localStorage.getItem(LS_KEY);
        if (cached) {
          const p = JSON.parse(cached);
          _byHsn = p.byHsn || {};
          _byCat = p.byCat || {};
          _hint = p.hint || {};
          _hsnByCat = p.hsnByCat || {};
          _loaded = true;
        }
      } catch {
        /* keep static fallback */
      }
    }
  }
}

/** The canonical HSN code for a product category, straight off the server's
 *  GST_CATEGORY_TABLE. '' until the endpoint has answered (callers then omit
 *  the HSN, and the server fills in its own -- which is the right one). */
export function resolveHsn(category?: string | null): string {
  if (!category) return '';
  return _hsnByCat[String(category).toUpperCase().replace(/[-\s]+/g, '_')] || '';
}

/** Resolve the GST rate (%) for a line: exact HSN -> category hint -> static
 *  GST 2.0 fallback (constants/gst.ts). Synchronous + always returns a number.
 *
 *  THE ORDER OF PRECEDENCE AND THE LAST STEP ARE DELIBERATELY UNCHANGED. This
 *  function is on the POS billing path, so the number it returns before the
 *  server has answered must stay exactly the number it returned yesterday --
 *  which is why getGSTRateByCategory() survives as the one local GST table,
 *  while the category -> HSN and category -> hint tables became server-fed. */
export function resolveGstRate(category?: string | null, hsnCode?: string | null): number {
  if (hsnCode) {
    const hc = String(hsnCode).trim();
    if (hc && _byHsn[hc] != null) return _byHsn[hc];
  }
  const norm = _normalizeCat(category);
  if (norm && _byCat[norm] != null) return _byCat[norm];
  // The NORMALISED spelling, exactly as the server's own last step does
  // (gst_rates.resolve_gst_rate ends in `gst_rate_for_category(norm)`). Passing
  // the raw spelling here used to be masked by the old hand-copied hint map,
  // which sent COLORED_CONTACT_LENSES to the CONTACT_LENS master row; the real
  // hint sends it to COLORED_CONTACT_LENS, which has no master row, so the raw
  // plural would have fallen through to the 18% default while the server bills 5%.
  return getGSTRateByCategory(norm);
}

// ============================================================================
// GST pricing mode (inclusive vs exclusive) — read at RUNTIME from /health.
// ============================================================================
// CRITICAL: Vite bakes import.meta.env at BUILD time, so a backend GST_PRICING_MODE
// flip would NOT reach a pre-built frontend via a build var. We therefore read
// the mode from the backend `/health` at startup so a flag flip lands without a
// FE redeploy. Default inclusive (matches the backend default) until /health answers.

let _pricingInclusive = true;

/** Fetch the active GST pricing mode from the backend `/health`. Safe to call
 *  repeatedly; never throws; keeps the prior value (default inclusive) on error. */
export async function loadPricingMode(): Promise<void> {
  try {
    const res = await api.get('/health');
    const mode = String(res.data?.pricing_mode || 'inclusive').toLowerCase();
    _pricingInclusive = mode !== 'exclusive';
  } catch {
    /* keep current value (default inclusive) */
  }
}

/** True when GST is INCLUSIVE (counter price is all-in; tax extracted from
 *  within). False = EXCLUSIVE (GST added on top). Synchronous; default true. */
export function isInclusivePricing(): boolean {
  return _pricingInclusive;
}
