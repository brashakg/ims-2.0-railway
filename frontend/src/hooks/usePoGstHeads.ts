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
  const [heads, setHeads] = useState<PoGstHeads>({});

  useEffect(() => {
    let alive = true;
    setHeads({});
    vendorsApi
      .getPoGstHeads(storeId || undefined)
      .then((r) => {
        if (alive) setHeads(r?.heads ?? {});
      })
      .catch(() => {
        if (alive) setHeads({});
      });
    return () => {
      alive = false;
    };
  }, [storeId]);

  return heads;
}

export default usePoGstHeads;
