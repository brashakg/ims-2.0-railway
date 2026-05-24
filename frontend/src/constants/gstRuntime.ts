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

import api from '../services/api/client';
import { getGSTRateByCategory } from './gst';

// Mirrors backend api/services/gst_rates._CATEGORY_HINT so a product category
// resolves to the same master row the backend uses.
const CATEGORY_HINT: Record<string, string> = {
  FRAME: 'FRAME', FRAMES: 'FRAME', EYEGLASS_FRAME: 'FRAME', SPECTACLE_FRAME: 'FRAME', FR: 'FRAME',
  OPTICAL_LENS: 'LENS', LENS: 'LENS', LENSES: 'LENS', RX_LENSES: 'LENS', EYEGLASS_LENS: 'LENS',
  OPTICAL_LENSES: 'LENS', SPECTACLE_LENS: 'LENS', SPECTACLE_LENSES: 'LENS', LS: 'LENS',
  CONTACT_LENS: 'CONTACT_LENS', CONTACT_LENSES: 'CONTACT_LENS', COLORED_CONTACT_LENS: 'CONTACT_LENS',
  COLORED_CONTACT_LENSES: 'CONTACT_LENS', COLOUR_CONTACTS: 'CONTACT_LENS', CL: 'CONTACT_LENS',
  READING_GLASSES: 'SPECTACLE', SPECTACLE: 'SPECTACLE', COMPLETE_SPECTACLE: 'SPECTACLE', RG: 'SPECTACLE',
  SUNGLASS: 'SUNGLASSES', SUNGLASSES: 'SUNGLASSES', SG: 'SUNGLASSES',
  WRIST_WATCHES: 'WATCH', WATCH: 'WATCH', WATCHES: 'WATCH', WT: 'WATCH',
  SMARTWATCHES: 'SMARTWATCH', SMARTWATCH: 'SMARTWATCH', SMART_WATCH: 'SMARTWATCH', SMTWT: 'SMARTWATCH',
  ACCESSORIES: 'ACCESSORIES', ACCESSORY: 'ACCESSORIES', ACC: 'ACCESSORIES',
  SERVICE: 'SERVICE', SERVICES: 'SERVICE', SVC: 'SERVICE',
  HEARING_AID: 'HEARING_AID', HEARING_AIDS: 'HEARING_AID', HA: 'HEARING_AID',
};

let _byHsn: Record<string, number> = {};
let _byCat: Record<string, number> = {};
let _loaded = false;

const LS_KEY = 'ims_hsn_gst_rates';

function _normalizeCat(category?: string | null): string {
  if (!category) return '';
  const raw = String(category).toUpperCase().replace(/[-\s]+/g, '_');
  return CATEGORY_HINT[raw] || raw;
}

/** Fetch the HSN->GST master into the in-memory cache. Safe to call repeatedly;
 *  never throws. Falls back to a localStorage snapshot if the network fails. */
export async function loadHsnRates(): Promise<void> {
  try {
    const res = await api.get('/products/gst-rates');
    const data = res.data || {};
    _byHsn = data.by_hsn || {};
    _byCat = data.by_cat || {};
    _loaded = true;
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ byHsn: _byHsn, byCat: _byCat }));
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
          _loaded = true;
        }
      } catch {
        /* keep static fallback */
      }
    }
  }
}

/** Resolve the GST rate (%) for a line: exact HSN -> category hint -> static
 *  GST 2.0 fallback (constants/gst.ts). Synchronous + always returns a number. */
export function resolveGstRate(category?: string | null, hsnCode?: string | null): number {
  if (hsnCode) {
    const hc = String(hsnCode).trim();
    if (hc && _byHsn[hc] != null) return _byHsn[hc];
  }
  const norm = _normalizeCat(category);
  if (norm && _byCat[norm] != null) return _byCat[norm];
  return getGSTRateByCategory(category || '');
}
