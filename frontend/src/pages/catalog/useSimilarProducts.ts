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
//   * A change to a field the answer says decides it (identity_fields, read
//     off the server's rule) clears the answer at once: it was about another
//     product, and a chip of the old model would put the form in that model's
//     variant mode. A change to any other field (lens colour, a USP) keeps it,
//     so the strip never vanishes and reflows the grid while it is still true.
//     Never a loading state: nothing renders until fresh.

import { useEffect, useRef, useState } from 'react';
import { productApi, type SimilarProductsResponse } from '../../services/api/products';
import { IDENTITY_DEBOUNCE_MS, answerStillHolds, typedAttributes } from './identityAnswer';

/** Council ruling: fire 400ms after the operator stops typing. */
export const SIMILAR_DEBOUNCE_MS = IDENTITY_DEBOUNCE_MS;

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
  /** The last completed response while it still holds; null while idle,
   *  errored, un-armed or after a change to a field that decides it — null
   *  always means "render nothing". */
  data: SimilarProductsResponse | null;
  /** Whether the trigger rule is currently armed (for tests/diagnostics). */
  armed: boolean;
}

interface Answer {
  category: string;
  /** The typed attributes the answer was asked with. */
  typed: Record<string, string>;
  data: SimilarProductsResponse;
}

function stillTrue(a: Answer, category: string, typed: Record<string, string>): boolean {
  return answerStillHolds(a, a.data.identity_fields, category, typed);
}

export function useSimilarProducts(
  q: SimilarQueryInput,
  enabled: boolean = true
): UseSimilarProductsResult {
  const [answer, setAnswer] = useState<Answer | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const armed = enabled && isSimilarQueryArmed(q);
  // A string, so a fresh attributes object with the same values never refires.
  const attrsKey = JSON.stringify(typedAttributes(q.attributes));
  const typed = JSON.parse(attrsKey) as Record<string, string>;
  const data = armed && answer && stillTrue(answer, q.category, typed) ? answer.data : null;

  useEffect(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (!armed) return undefined;

    const timer = window.setTimeout(() => {
      const controller = new AbortController();
      abortRef.current = controller;
      const asked = { category: q.category, typed: JSON.parse(attrsKey) as Record<string, string> };
      productApi
        .getSimilarProducts({ category: asked.category, ...asked.typed }, controller.signal)
        .then((res) => {
          if (!controller.signal.aborted) setAnswer(res ? { ...asked, data: res } : null);
        })
        .catch(() => {
          // Error (or abort race) -> render nothing. The strip is a hint;
          // the server-side 409 hard-block still guards the actual create.
          if (!controller.signal.aborted) setAnswer(null);
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
