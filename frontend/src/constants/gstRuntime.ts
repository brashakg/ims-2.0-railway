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
// map, the category -> master-row hint and the canonical category -> rate table
// used to be hand-copied here from backend api/services/gst_rates.py, and the
// copies had drifted (smartglasses were pointed at the sunglasses HSN 900410
// instead of 852580; an eye test, billed at 0% as an exempt health service, had
// no row at all). They are now read off the server, which is the only place
// that can be right.

import api from '../services/api/client';
import { getGSTRateByCategory, HSN_CODES } from './gst';
import { canonicalCategory } from '../utils/categoryNormalize';

let _byHsn: Record<string, number> = {};
let _byCat: Record<string, number> = {};
// Server-fed: category spelling -> the category_hint on a master row, the
// canonical category -> HSN code, and the canonical category -> rate the
// backend's own last step uses. Empty until the endpoint answers.
let _hint: Record<string, string> = {};
let _hsnByCat: Record<string, string> = {};
let _rateByCat: Record<string, number> = {};
let _loaded = false;

const LS_KEY = 'ims_hsn_gst_rates';

// ONE category normaliser for the whole app: utils/categoryNormalize. This file
// used to inline its own upper-snake regex, which is the same rule MINUS the
// trim -- so a legacy row written '  FRAME  ' normalised to '__FRAME__', matched
// nothing, and printed a blank HSN on the tax invoice. The server trims
// (gst_rates._normalize_category begins with .strip()).
function _normalizeCat(category?: string | null): string {
  const canon = canonicalCategory(category);
  return _hint[canon] || canon;
}

/** Last session's tables, so a page load is never colder than the one before
 *  it. Silent on a parse error / private mode: the static fallback stands. */
function _applyCache(): void {
  try {
    const p = JSON.parse(localStorage.getItem(LS_KEY) || 'null');
    if (!p) return;
    _byHsn = p.byHsn || {};
    _byCat = p.byCat || {};
    _hint = p.hint || {};
    _hsnByCat = p.hsnByCat || {};
    _rateByCat = p.rateByCat || {};
    _loaded = true;
  } catch {
    /* keep the static fallback */
  }
}

/** Fetch the HSN->GST master into the in-memory cache. Safe to call repeatedly;
 *  never throws. Warm-starts from the localStorage snapshot, and keeps any
 *  table the response does not carry.
 *
 *  THE DEPLOY ORDER IS WHY. The frontend ships on Vercel and the backend on
 *  Railway, in that order, so for the minutes between them every browser gets a
 *  200 from the OLD endpoint -- which has no hsn_by_category / rate_by_category
 *  key at all. Overwriting with `|| {}` there would blank the maps this file
 *  depends on, on a live POS, and then persist that blank over the good
 *  snapshot. `?? _x` leaves an absent table alone; a present one still wins. */
export async function loadHsnRates(): Promise<void> {
  if (!_loaded) _applyCache();
  try {
    const res = await api.get('/products/gst-rates');
    const data = res.data || {};
    _byHsn = data.by_hsn ?? _byHsn;
    _byCat = data.by_cat ?? _byCat;
    _hint = data.category_hint ?? _hint;
    _hsnByCat = data.hsn_by_category ?? _hsnByCat;
    _rateByCat = data.rate_by_category ?? _rateByCat;
    _loaded = true;
    try {
      localStorage.setItem(
        LS_KEY,
        JSON.stringify({
          byHsn: _byHsn, byCat: _byCat, hint: _hint,
          hsnByCat: _hsnByCat, rateByCat: _rateByCat,
        }),
      );
    } catch {
      /* ignore quota / private-mode errors */
    }
  } catch {
    if (!_loaded) _applyCache();
  }
}

/** The canonical HSN code for a product category, straight off the server's
 *  GST_CATEGORY_TABLE. '' until the endpoint has answered (callers then omit
 *  the HSN, and the server fills in its own -- which is the right one). */
export function resolveHsn(category?: string | null): string {
  return _hsnByCat[canonicalCategory(category)] || '';
}

/** The SERVER's GST rate (%) for a line, in the same three steps the server
 *  takes: editable master by exact HSN -> editable master by category hint ->
 *  the server's canonical category table. Returns null until the endpoint has
 *  answered (or for a category the server does not name) -- a DISPLAY caller
 *  must then show nothing, never a hand-typed value. */
export function serverGstRate(category?: string | null, hsnCode?: string | null): number | null {
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
  if (norm && _rateByCat[norm] != null) return _rateByCat[norm];
  return null;
}

/** Resolve the GST rate (%) for a line: serverGstRate() above, then the
 *  offline table in constants/gst.ts. Synchronous + always returns a number.
 *
 *  The server tables used to be a hand-written copy over here. The copy
 *  disagreed with the server: an EYE_TEST / CONSULTATION line is an exempt
 *  health service billed at 0% (SAC 9993), and the copy had no row for it, so
 *  the screen and the printed invoice quoted the unknown-category default while
 *  the customer was charged nothing. Reading the server's own table removes the
 *  whole class -- nothing over here has to be told when a rate changes.
 *
 *  The offline step is reached only for a category the server does not name (a
 *  legacy or free-text spelling) or before the endpoint has answered. It is a
 *  rate on the POS billing path, so it stays hand-written and deliberately
 *  dull. Display-only callers use serverGstRate() and skip it. */
export function resolveGstRate(category?: string | null, hsnCode?: string | null): number {
  const server = serverGstRate(category, hsnCode);
  if (server != null) return server;
  return getGSTRateByCategory(_normalizeCat(category));
}

/** The category_hint vocabulary of the editable HSN->GST master, straight off
 *  the server's hint map (the distinct values of gst_rates._CATEGORY_HINT).
 *  resolve_gst_rate matches a master row's stored hint against THIS vocabulary,
 *  so offering any other spelling would make the row silently no-op. Empty
 *  until the endpoint has answered -- a select must then offer nothing rather
 *  than a hand-typed list. */
export function categoryHintOptions(): string[] {
  return [...new Set(Object.values(_hint))].sort();
}

/** The HSN codes the cataloguing screen may offer, server-first.
 *
 *  Every code the category resolver can produce is in here, because the list
 *  IS the server's canonical category -> HSN table (plus the locally described
 *  codes, which are extra choices a cataloguer may still pick by hand). The
 *  hand-written list alone could not represent 852580 (smartglasses -- 35 of
 *  the 68 live products) or 9993 (eye tests): the resolver set a value the
 *  dropdown had no option for, so a REQUIRED field rendered blank.
 *
 *  A code the local list describes keeps its description; one that only the
 *  server names is labelled by the code itself. The rate on each option is
 *  resolveGstRate()'s answer, so the option cannot quote a rate the server
 *  contradicts. */
export function hsnOptions(): Array<{ value: string; label: string; gstRate: number }> {
  const opts = new Map<string, { value: string; label: string; gstRate: number }>();
  // The codes the local list describes, at the rate it states -- unless the
  // owner-editable master has that code, in which case the master wins.
  for (const hsn of Object.values(HSN_CODES)) {
    // HSN_CODES entries carry NO rate of their own (deleted with the drifted
    // copies); offline the code's rate is its category's, off the one local
    // fallback table. The owner-editable master still wins once loaded.
    const gstRate = _byHsn[hsn.code] ?? getGSTRateByCategory(hsn.category);
    opts.set(hsn.code, {
      value: hsn.code,
      label: `${hsn.code} - ${hsn.description} (GST: ${gstRate}%)`,
      gstRate,
    });
  }
  // Then every code the server's canonical table can hand a category, priced
  // through that category so the option cannot quote a rate the server denies.
  for (const [category, code] of Object.entries(_hsnByCat)) {
    if (opts.has(code)) continue;
    const gstRate = resolveGstRate(category, code);
    opts.set(code, { value: code, label: `${code} (GST: ${gstRate}%)`, gstRate });
  }
  return [...opts.values()].sort((a, b) => a.value.localeCompare(b.value));
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
