// ============================================================================
// IMS 2.0 - SimilarProductsHint (dup-detect Phase 2)
// ============================================================================
// The quiet as-you-type "similar products" strip rendered under the model
// field of the Add-Product form. Contract (council ruling):
//
//   * Renders NOTHING while: not armed (brand + model >= 2 chars not both
//     set), loading/debouncing, errored, or no matches — NEVER a spinner or
//     placeholder over the form.
//   * siblings only -> one quiet line: "This model exists in N colours:"
//     followed by clickable chips. A chip click prefills the form through the
//     EXISTING Phase 1 variant path (productToVariantFormValues +
//     enterVariantMode) — the parent passes that path in via onPickSibling.
//   * exact_match -> a warning line: the exact colour already exists, with an
//     "Open it" link that follows the same product-open path the Phase 1
//     duplicate-rescue popup uses (onOpenExisting). A discarded draft gets no
//     link: Save brings it back, and the popup after Save opens it.
//   * eye_size_needed -> Save's EYE_SIZE_NEEDED answer, in the same words.
//   * In VARIANT MODE the sibling line is suppressed (it would state the
//     obvious about the locked model) but the exact-colour warning stays live.
//   * No element of the strip participates in the Tab order (tabIndex=-1) —
//     it must never interrupt heads-down keyboard entry.
//
// The trigger/debounce/abort logic lives in useSimilarProducts; the backend
// answers with the create door's own rule (identity_conflict), so what this
// warns about is exactly what Save answers.

import { AlertTriangle } from 'lucide-react';
import { useSimilarProducts } from './useSimilarProducts';
import type { SimilarProductSummary } from '../../services/api/products';

export interface SimilarProductsHintProps {
  /** CATEGORIES picker code (SG/FR/...). */
  category: string;
  brand: string;
  model: string;
  colour: string;
  size: string;
  /** Variant mode: suppress the sibling strip for the locked brand/model but
   *  keep the exact-colour warning live. */
  variantMode?: boolean;
  /** Chip click -> the Phase 1 variant path (fetch + enterVariantMode). */
  onPickSibling: (productId: string) => void;
  /** "Open it" on the exact-match warning -> the same product-open path the
   *  Phase 1 duplicate-rescue popup uses (existingProductPath). */
  onOpenExisting: (existing: SimilarProductSummary) => void;
}

function chipLabel(s: SimilarProductSummary): string {
  const colour = String(s.colour_code || '').trim();
  const size = String(s.size || '').trim();
  const base = colour || String(s.sku || '').trim() || 'variant';
  return size ? `${base} · ${size}` : base;
}

export function SimilarProductsHint({
  category,
  brand,
  model,
  colour,
  size,
  variantMode = false,
  onPickSibling,
  onOpenExisting,
}: SimilarProductsHintProps) {
  const { data, armed } = useSimilarProducts({ category, brand, model, colour, size });

  // Render NOTHING unless armed with a completed response (null covers idle,
  // debouncing, in-flight and error states — the hook's contract).
  if (!armed || !data) return null;

  const exact = data.exact_match;
  const eyeSizes = exact ? [] : data.eye_size_needed || [];
  const siblings = variantMode ? [] : data.siblings || [];
  if (!exact && eyeSizes.length === 0 && siblings.length === 0) return null;

  const colourCount = data.model_colour_count || siblings.length;

  return (
    <div
      className="col-span-full space-y-1"
      data-testid="similar-products-hint"
      aria-live="polite"
    >
      {exact && (
        <p className="flex items-start gap-1.5 text-xs text-red-600" role="alert">
          <AlertTriangle className="w-3.5 h-3.5 mt-px shrink-0" />
          {exact.provisional ? (
            // Audit C3 (R1-85): a manager already ordered this exact item before
            // it was catalogued -- it is finished, never added a second time.
            <span>
              This exact item was ordered before it was catalogued — SKU{' '}
              <span className="font-semibold">{exact.sku || 'unknown'}</span>.{' '}
              <button
                type="button"
                tabIndex={-1}
                onClick={() => onOpenExisting(exact)}
                className="underline font-medium hover:text-red-700"
              >
                Finish it
              </button>
              {' '}instead of adding it again.
            </span>
          ) : exact.discarded_draft ? (
            // Save brings a discarded draft back to be finished when it is typed
            // as the kind it was ordered as (the door's revive) -- the popup
            // after Save is the one way in, so no second destination here.
            <span>
              This exact item is a draft discarded earlier — SKU{' '}
              <span className="font-semibold">{exact.sku || 'unknown'}</span>. Saving it as{' '}
              {String(exact.category || 'its category').toLowerCase().replace(/_/g, ' ')} brings it
              back to be finished.
            </span>
          ) : (
            <span>
              This exact colour already exists — SKU{' '}
              <span className="font-semibold">{exact.sku || 'unknown'}</span>.{' '}
              <button
                type="button"
                tabIndex={-1}
                onClick={() => onOpenExisting(exact)}
                className="underline font-medium hover:text-red-700"
              >
                Open it
              </button>
              , or enter a different colour.
            </span>
          )}
        </p>
      )}
      {eyeSizes.length > 0 && (
        // Save answers 422 EYE_SIZE_NEEDED for this: the same words before it.
        <p className="flex items-start gap-1.5 text-xs text-red-600" role="alert">
          <AlertTriangle className="w-3.5 h-3.5 mt-px shrink-0" />
          <span>
            This item is in the catalogue by eye size ({eyeSizes.join(', ')}). Type the eye size,
            or pick the item from the catalogue.
          </span>
        </p>
      )}
      {siblings.length > 0 && (
        <p className="text-xs text-gray-500 leading-6">
          This model exists in {colourCount} colour{colourCount === 1 ? '' : 's'}:{' '}
          {siblings.map((s, i) => (
            <button
              key={s.product_id || s.sku || i}
              type="button"
              tabIndex={-1}
              disabled={!s.product_id}
              onClick={() => s.product_id && onPickSibling(s.product_id)}
              title={`${s.name || 'Product'}${s.sku ? ` — SKU ${s.sku}` : ''}${
                s.is_active === false ? ' (inactive)' : ''
              } — click to add another variant of this model`}
              className="inline-flex items-center mr-1 mb-0.5 px-1.5 py-px rounded border border-gray-200 bg-gray-50 text-[11px] text-gray-700 hover:border-bv hover:bg-bv-50 disabled:cursor-default disabled:hover:border-gray-200 disabled:hover:bg-gray-50 align-middle"
            >
              {chipLabel(s)}
            </button>
          ))}
        </p>
      )}
    </div>
  );
}
