// ============================================================================
// IMS 2.0 - useSimilarProducts (dup-detect Phase 2 trigger logic)
// ============================================================================
// The council-exact trigger rule for the Add-Product "similar products" strip,
// extracted into a small testable hook:
//
//   * SEND the category and the form's attributes as typed (blanks dropped).
//     The server keys them with the create door's own identity_parts, so this
//     hook keeps NO list of what the duplicate key reads (a hand-kept copy
//     drifted: colour name, sub-brand, coating, index, contact-lens fit).
//   * ARM once a category and a brand are set; the server answers "nothing"
//     until the identity is complete.
//   * FIRE 400ms after the operator stops typing; every keystroke RESTARTS
//     the timer and ABORTS the in-flight request (AbortController).
//   * Any change clears the last answer at once: it was about what was typed
//     before, and a chip of the old model would put the form in that model's
//     variant mode. Never a loading state: nothing renders until fresh.

import { useEffect, useRef, useState } from 'react';
import { productApi, type SimilarProductsResponse } from '../../services/api/products';

/** Council ruling: fire 400ms after the operator stops typing. */
export const SIMILAR_DEBOUNCE_MS = 400;

export interface SimilarQueryInput {
  /** CATEGORIES picker code (SG/FR/...) — the backend resolves aliases. */
  category: string;
  /** The form's attributes, as typed. */
  attributes: Record<string, string>;
}

/** ARM rule: a category (the registry the key is read in) and a brand. */
export function isSimilarQueryArmed(q: SimilarQueryInput): boolean {
  return Boolean(String(q.category || '').trim()) && Boolean(String(q.attributes?.brand_name ?? '').trim());
}

export interface UseSimilarProductsResult {
  /** The last completed response; null while idle, errored or un-armed —
   *  null always means "render nothing". */
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
  // A string, so a fresh attributes object with the same values never refires.
  const attrsKey = JSON.stringify(
    Object.entries(q.attributes || {})
      .map(([k, v]) => [k, String(v ?? '').trim()])
      .filter(([, v]) => v)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
  );

  useEffect(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setData(null);
    if (!armed) return undefined;

    const timer = window.setTimeout(() => {
      const controller = new AbortController();
      abortRef.current = controller;
      productApi
        .getSimilarProducts(
          { category: q.category, ...Object.fromEntries(JSON.parse(attrsKey) as [string, string][]) },
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
  }, [q.category, attrsKey, armed]);

  return { data, armed };
}
