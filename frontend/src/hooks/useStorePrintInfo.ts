// ============================================================================
// IMS 2.0 - useStorePrintInfo
// ============================================================================
// Shared hook that resolves a print-ready StoreInfo for the ISSUING store of a
// printed document. The owner runs multiple optical stores across 2 brands and
// multiple legal entities/GSTINs, so every printout (PO, GRN, Rx card, token,
// job card) must carry the identity of the store that issued it -- name,
// address, GSTIN, phone -- NOT a hardcoded brand block.
//
// Pass an explicit `storeId` (the document's store) when one is available;
// otherwise it falls back to the logged-in user's active store, which is the
// store actually issuing the document in the desk-bound flows (purchase /
// goods-receipt / clinical token / Rx).
//
// Fail-soft: on a fetch error it returns a neutral StoreInfo carrying just the
// store id/code -- never a fixed brand name that would mislabel another store.

import { useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { storeApi } from '../services/api';

export interface StorePrintInfo {
  storeName: string;
  address: string;
  city: string;
  state: string;
  pincode: string;
  phone?: string;
  gstin?: string;
  stateCode?: string;
  logo?: string;
}

/**
 * Resolve the issuing store's print identity.
 *
 * @param storeId Optional explicit store id (the document's store). When
 *   omitted, the logged-in user's active store is used.
 * @returns The resolved StoreInfo, or null until the fetch resolves.
 */
export function useStorePrintInfo(storeId?: string | null): StorePrintInfo | null {
  const { user } = useAuth();
  const effectiveId = storeId || user?.activeStoreId || '';
  const [info, setInfo] = useState<StorePrintInfo | null>(null);

  useEffect(() => {
    if (!effectiveId) {
      setInfo(null);
      return;
    }
    let active = true;
    storeApi
      .getStore(effectiveId)
      .then((s: any) => {
        if (!active) return;
        if (!s) {
          setInfo({ storeName: effectiveId, address: '', city: '', state: '', pincode: '' });
          return;
        }
        // The API client adds camelCase aliases over the snake_case backend
        // doc, so both shapes resolve; read defensively either way.
        setInfo({
          storeName: s.storeName || s.store_name || s.name || effectiveId,
          address: s.address || s.street || s.address_line_1 || '',
          city: s.city || '',
          state: s.state || s.state_name || '',
          pincode: s.pincode || '',
          phone: s.phone || undefined,
          gstin: s.gstin || undefined,
          stateCode: s.stateCode || s.state_code || undefined,
          logo: s.logo_url || s.logoUrl || undefined,
        });
      })
      .catch(() => {
        // Fail-soft neutral fallback -- carry the id, never a brand name.
        if (active) {
          setInfo({ storeName: effectiveId, address: '', city: '', state: '', pincode: '' });
        }
      });
    return () => {
      active = false;
    };
  }, [effectiveId]);

  return info;
}

export default useStorePrintInfo;
