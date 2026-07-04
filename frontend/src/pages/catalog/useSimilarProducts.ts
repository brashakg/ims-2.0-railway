// ============================================================================
// IMS 2.0 - useSimilarProducts (dup-detect Phase 2 trigger logic)
// ============================================================================
// The council-exact trigger rule for the Add-Product "similar products" strip,
// extracted into a small testable hook:
//
//   * WATCH brand, model (model_no / model_name), colour_code and the size
//     field. ARM only when brand AND model (>= 2 chars) are both set.
//   * FIRE 400ms after the operator stops typing in ANY of the watched
//     fields; every keystroke RESTARTS the timer and ABORTS the in-flight
//     request (AbortController).
//   * While waiting / fetching there is NO loading state to render — the
//     strip simply shows nothing (stale data is cleared immediately on any
//     input change so it can never mislead).
//
// The backend (GET /products/similar) matches through THE same normaliser
// that builds the spine's duplicate identity_key, so the hook never folds or
// normalises values itself — it sends what the operator typed.

import { useEffect, useRef, useState } from 'react';
import { productApi, type SimilarProductsResponse } from '../../services/api/products';

/** Council ruling: fire 400ms after the operator stops typing. */
export const SIMILAR_DEBOUNCE_MS = 400;

export interface SimilarQueryInput {
  /** CATEGORIES picker code (SG/FR/...) — the backend resolves aliases. */
  category: string;
  brand: string;
  model: string;
  colour: string;
  size: string;
}

/** ARM rule (council-exact): brand AND model (>= 2 chars) both set — plus a
 *  category, without which the endpoint has no field registry to match in. */
export function isSimilarQueryArmed(q: {
  category: string;
  brand: string;
  model: string;
}): boolean {
  return (
    Boolean(String(q.category || '').trim()) &&
    String(q.brand || '').trim().length > 0 &&
    String(q.model || '').trim().length >= 2
  );
}

export interface UseSimilarProductsResult {
  /** The last completed response for the CURRENT inputs; null while idle,
   *  debouncing, fetching, errored, or un-armed — null always means "render
   *  nothing". */
  data: SimilarProductsResponse | null;
  /** Whether the trigger rule is currently armed (for tests/diagnostics). */
  armed: boolean;
}

export function useSimilarProducts(
  q: SimilarQueryInput,
  enabled: boolean = true
): UseSimilarProductsResult {
  const [data, setData] = useState<SimilarProductsResponse | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const armed = enabled && isSimilarQueryArmed(q);

  useEffect(() => {
    // Any watched-field change: abort the in-flight request and clear stale
    // data (never a spinner over the form — nothing renders until fresh).
    abortRef.current?.abort();
    abortRef.current = null;
    setData(null);
    if (!armed) return undefined;

    const timer = window.setTimeout(() => {
      const controller = new AbortController();
      abortRef.current = controller;
      productApi
        .getSimilarProducts(
          {
            category: q.category,
            brand: q.brand.trim(),
            model_no: q.model.trim(),
            colour_code: q.colour.trim() || undefined,
            size: q.size.trim() || undefined,
          },
          controller.signal
        )
        .then((res) => {
          if (!controller.signal.aborted) setData(res ?? null);
        })
        .catch(() => {
          // Error (or abort race) -> render nothing. The strip is a hint;
          // the server-side 409 hard-block still guards the actual create.
          if (!controller.signal.aborted) setData(null);
        });
    }, SIMILAR_DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timer);
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [q.category, q.brand, q.model, q.colour, q.size, armed]);

  return { data, armed };
}
