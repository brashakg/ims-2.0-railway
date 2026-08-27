// ============================================================================
// IMS 2.0 - GST state codes (2-digit -> state name)
// ============================================================================
// The first two digits of a GSTIN ARE the state, and that state decides
// whether a purchase is CGST+SGST or IGST. There is exactly ONE list of those
// codes in this system -- `api/services/org_validation.INDIAN_STATE_CODES`,
// served by GET /entities/meta/options -- and this hook is how the frontend
// reads it. Deliberately NOT a second hardcoded copy in the browser: a list
// that drifts from the one the backend taxes with is worse than no list.
//
// Fetched once per session and shared (the codes change roughly never), so
// putting it on a card in a list costs one request for the whole page.

import { useEffect, useState } from 'react';
import { entitiesApi } from '../services/api/entities';

export type GstStateNames = Record<string, string>;

let cached: GstStateNames | null = null;
let inFlight: Promise<GstStateNames> | null = null;

function load(): Promise<GstStateNames> {
  if (cached) return Promise.resolve(cached);
  if (!inFlight) {
    inFlight = entitiesApi
      .meta()
      .then((meta) => {
        cached = Object.fromEntries(
          (meta.state_codes ?? []).map((s) => [s.code, s.name]),
        );
        return cached;
      })
      .catch(() => {
        // Fail-soft: a card shows the raw code instead of a name. Never
        // blocks the page, and never invents a name.
        inFlight = null;
        return {} as GstStateNames;
      });
  }
  return inFlight;
}

/** 2-digit GST state code -> state name. `{}` until the first fetch lands. */
export function useGstStateCodes(): GstStateNames {
  const [names, setNames] = useState<GstStateNames>(cached ?? {});

  useEffect(() => {
    if (cached) return;
    let alive = true;
    load().then((m) => {
      if (alive) setNames(m);
    });
    return () => {
      alive = false;
    };
  }, []);

  return names;
}
