// ============================================================================
// IMS 2.0 - usePoGstHeads
// ============================================================================
// The tax head (IGST / CGST + SGST) of a purchase from each vendor at the
// receiving shop, as the SERVER decides it (GET /vendors/po-gst-heads:
// org_validation.shop_gstin + purchase_invoice_engine.classify_supply -- the
// rule the order and the bill book with). The browser never compares GSTIN
// prefixes itself: a raw store.gstin can be blank or stale, and a prefix that
// is not a state ("88...") has no head.
//
// heads[vendorId]: true = IGST, false = CGST + SGST, null/missing = cannot tell
// (also while loading, and when the endpoint is down).

import { useEffect, useState } from 'react';
import { vendorsApi } from '../services/api/inventory';

export type PoGstHeads = Record<string, boolean | null>;

export function usePoGstHeads(storeId?: string | null): PoGstHeads {
  const store = storeId || '';
  // The verdict is kept WITH the shop it was decided for, and only handed out
  // for that shop: on a shop switch the very next render has no verdict (never
  // the previous shop's IGST / CGST + SGST for a frame), and a late answer for
  // the old shop can never be shown for the new one.
  const [state, setState] = useState<{ store: string; heads: PoGstHeads }>({
    store,
    heads: {},
  });

  useEffect(() => {
    let alive = true;
    vendorsApi
      .getPoGstHeads(store || undefined)
      .then((r) => {
        if (alive) setState({ store, heads: r?.heads ?? {} });
      })
      .catch(() => {
        if (alive) setState({ store, heads: {} });
      });
    return () => {
      alive = false;
    };
  }, [store]);

  return state.store === store ? state.heads : EMPTY;
}

const EMPTY: PoGstHeads = {};

export default usePoGstHeads;
