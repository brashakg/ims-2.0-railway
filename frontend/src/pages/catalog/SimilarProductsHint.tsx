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
//     duplicate-rescue popup uses (onOpenExisting).
//   * In VARIANT MODE the sibling line is suppressed (it would state the
//     obvious about the locked model) but the exact-colour warning stays live.
//   * No element of the strip participates in the Tab order (tabIndex=-1) —
//     it must never interrupt heads-down keyboard entry.
//
// The trigger/debounce/abort logic lives in useSimilarProducts; the backend
// matches through the SAME normaliser that builds the create door's duplicate
// identity, so what this warns about is exactly what would 409 on save.

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
   *  Phase 1 duplicate-rescue popup uses. */
  onOpenExisting: (sku: string | null | undefined) => void;
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
  const siblings = variantMode ? [] : data.siblings || [];
  if (!exact && siblings.length === 0) return null;

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
          <span>
            This exact colour already exists — SKU{' '}
            <span className="font-semibold">{exact.sku || 'unknown'}</span>.{' '}
            <button
              type="button"
              tabIndex={-1}
              onClick={() => onOpenExisting(exact.sku)}
              className="underline font-medium hover:text-red-700"
            >
              Open it
            </button>
            , or enter a different colour.
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
