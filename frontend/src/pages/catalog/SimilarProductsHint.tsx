// ============================================================================
// IMS 2.0 - SimilarProductsHint (dup-detect Phase 2)
// ============================================================================
// The quiet as-you-type "similar products" strip rendered under the model
// field of the Add-Product form. Contract (council ruling):
//
//   * Renders NOTHING while: not armed (no category or brand yet), before
//     the first answer, errored, or no matches — NEVER a spinner or
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
//   * similar_models -> the same model written differently (owner 2026-11-01,
//     RB4340 vs 4340): a RED panel when a product is this colour and size
//     (Save asks too: 409 NEAR_DUPLICATE), an AMBER hint when only the model
//     matches. "Open it" opens a NEW TAB (the typed form and photos stay);
//     "Add a new colour or size of <stored model>" takes the stored spelling
//     (onPickSibling); "It's a different product" needs a reason, recorded on
//     Save, and hides the group.
//   * No button of the strip participates in the Tab order (tabIndex=-1) —
//     it must never interrupt heads-down keyboard entry.
//
// The trigger/debounce/abort logic lives in useSimilarProducts; the backend
// answers with the create door's own rule (identity_conflict), so what this
// warns about is exactly what Save answers.

import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, ImageOff } from 'lucide-react';
import { useSimilarProducts } from './useSimilarProducts';
import { DifferentProductReason, addedBy, openInNewTab } from './DifferentProductReason';
import { resolveApiAssetUrl } from '../../services/api/client';
import type {
  FamilyHint,
  SimilarModelGroup,
  SimilarProductSummary,
} from '../../services/api/products';

export interface SimilarProductsHintProps {
  /** CATEGORIES picker code (SG/FR/...). */
  category: string;
  /** The form's attributes, sent as typed: the server decides what the
   *  duplicate key reads (useSimilarProducts). */
  attributes: Record<string, string>;
  /** Variant mode: suppress the sibling strip for the locked brand/model but
   *  keep the exact-colour warning live. */
  variantMode?: boolean;
  /** Chip click -> the Phase 1 variant path (fetch + enterVariantMode). */
  onPickSibling: (productId: string) => void;
  /** "Open it" on the exact-match warning -> the same product-open path the
   *  Phase 1 duplicate-rescue popup uses (existingProductPath). */
  onOpenExisting: (existing: SimilarProductSummary) => void;
  /** The product the editor has open (?edit=): never "similar" to itself. An
   *  ordered draft being finished was told to "Finish it", and the link
   *  reloaded the editor over the unsaved edits. */
  selfId?: string | null;
  /** G1: every fresh answer's model (null: none / not armed) -> the form
   *  joins it (handleFamilyFound). */
  onFamily?: (family: FamilyHint | null) => void;
  /** Every fresh answer's brand_in_model (false: none / not armed /
   *  unmounted) -> the form's "brand in the model number" hint. */
  onBrandInModel?: (repeats: boolean) => void;
  /** "It's a different product" with its reason, for these products (sent
   *  as distinct_from on Save). */
  onMarkDifferent?: (productIds: string[], reason: string) => void;
  /** Products already answered "different": their group is hidden. */
  answeredIds?: ReadonlySet<string>;
}

function colourSize(s: SimilarProductSummary): string {
  return [s.colour_code, s.size].map((v) => String(v || '').trim()).filter(Boolean).join(' - ');
}

/** One stored spelling of the typed model: red when a product is this colour
 *  and size, amber when only the model matches. */
function SimilarModelPanel({
  group,
  typedModel,
  onPickSibling,
  onMarkDifferent,
}: {
  group: SimilarModelGroup;
  typedModel: string;
  onPickSibling: (productId: string) => void;
  onMarkDifferent?: (productIds: string[], reason: string) => void;
}) {
  const [asking, setAsking] = useState(false);
  const first: SimilarProductSummary = group.products[0] || {};
  const red = group.same_colour_size;
  const brand = String(group.brand || first.brand || '').trim();
  const stored = String(group.model || first.model || '').trim();
  const imageUrl = first.image_url ? resolveApiAssetUrl(first.image_url) : '';
  const who = addedBy(first);
  const tone = red ? 'border-red-200 bg-red-50 text-red-700' : 'border-amber-200 bg-amber-50 text-amber-800';
  const ids = group.products.map((p) => p.product_id).filter((id): id is string => Boolean(id));
  return (
    <div
      className={`rounded-lg border p-2.5 text-xs ${tone}`}
      data-testid={red ? 'same-model-red' : 'same-model-amber'}
      role={red ? 'alert' : undefined}
    >
      <div className="flex items-start gap-2.5">
        {imageUrl ? (
          <img
            src={imageUrl}
            alt={`${brand} ${stored}`}
            className="w-12 h-12 rounded object-cover border border-gray-200 bg-white shrink-0"
          />
        ) : (
          <span className="w-12 h-12 rounded border border-gray-200 bg-white flex items-center justify-center text-gray-300 shrink-0">
            <ImageOff className="w-5 h-5" />
          </span>
        )}
        <div className="min-w-0 flex-1 space-y-1">
          {red ? (
            <p>
              This looks like the same product: {brand} {stored}
              {first.colour_code ? `, colour ${first.colour_code}` : ''}
              {first.size ? `, size ${first.size}` : ''}
              {first.sku ? ` (SKU ${first.sku})` : ''}
              {who ? `, ${who}` : ''}.
            </p>
          ) : (
            <p>
              Is this already in the catalogue? {brand} has <span className="font-semibold">{stored}</span> -
              it may be the same model as the {typedModel} you typed.
            </p>
          )}
          <p className="flex flex-wrap gap-1 text-gray-600">
            {group.products.map((p, i) => (
              <span key={p.product_id || i} className="px-1.5 rounded border border-gray-200 bg-white">
                {colourSize(p) || p.sku}
              </span>
            ))}
            {!red && who && <span className="text-gray-500">{who}</span>}
          </p>
          {!asking && (
            <p className="flex flex-wrap gap-x-3 gap-y-1">
              <button type="button" tabIndex={-1} onClick={() => openInNewTab(first)} className="underline font-medium">
                Open it
              </button>
              <button
                type="button"
                tabIndex={-1}
                disabled={!first.product_id}
                onClick={() => first.product_id && onPickSibling(first.product_id)}
                className="underline font-medium"
              >
                Add a new colour or size of {stored}
              </button>
              {onMarkDifferent && ids.length > 0 && (
                <button type="button" tabIndex={-1} onClick={() => setAsking(true)} className="underline font-medium">
                  It&apos;s a different product
                </button>
              )}
            </p>
          )}
          {asking && onMarkDifferent && (
            <DifferentProductReason onCancel={() => setAsking(false)} onSave={(reason) => onMarkDifferent(ids, reason)} />
          )}
        </div>
      </div>
    </div>
  );
}

function chipLabel(s: SimilarProductSummary): string {
  const colour = String(s.colour_code || '').trim();
  const size = String(s.size || '').trim();
  const base = colour || String(s.sku || '').trim() || 'variant';
  return size ? `${base} · ${size}` : base;
}

export function SimilarProductsHint({
  category,
  attributes,
  variantMode = false,
  onPickSibling,
  onOpenExisting,
  selfId = null,
  onFamily,
  onBrandInModel,
  onMarkDifferent,
  answeredIds,
}: SimilarProductsHintProps) {
  const { data, armed } = useSimilarProducts({ category, attributes, selfId });
  const onFamilyRef = useRef(onFamily);
  onFamilyRef.current = onFamily;
  useEffect(() => {
    if (!armed) onFamilyRef.current?.(null);
    else if (data) onFamilyRef.current?.(data.family ?? null);
  }, [armed, data]);
  const onBrandRef = useRef(onBrandInModel);
  onBrandRef.current = onBrandInModel;
  useEffect(() => {
    if (!armed) onBrandRef.current?.(false);
    else if (data) onBrandRef.current?.(Boolean(data.brand_in_model));
  }, [armed, data]);
  useEffect(() => () => onBrandRef.current?.(false), []);

  // Render NOTHING unless armed with a completed response (null covers idle,
  // debouncing, in-flight and error states — the hook's contract).
  if (!armed || !data) return null;

  const exact = data.exact_match && data.exact_match.product_id !== selfId ? data.exact_match : null;
  const eyeSizes = data.exact_match ? [] : data.eye_size_needed || [];
  const siblings = variantMode ? [] : (data.siblings || []).filter((s) => s.product_id !== selfId);
  const sameModels = variantMode
    ? []
    : (data.similar_models || []).filter(
        (g) => g.products.length > 0 && !g.products.every((p) => p.product_id && answeredIds?.has(p.product_id)),
      );
  if (!exact && eyeSizes.length === 0 && siblings.length === 0 && sameModels.length === 0) return null;
  const typedModel = String(attributes.model_no || attributes.model_name || '').trim();

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
      {sameModels.map((g) => (
        <SimilarModelPanel
          key={`${g.brand}|${g.model}`}
          group={g}
          typedModel={typedModel}
          onPickSibling={onPickSibling}
          onMarkDifferent={onMarkDifferent}
        />
      ))}
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
              className="inline-flex items-center min-h-9 mr-1 mb-1 px-2.5 rounded border border-gray-200 bg-gray-50 text-xs text-gray-700 hover:border-bv hover:bg-bv-50 disabled:cursor-default disabled:hover:border-gray-200 disabled:hover:bg-gray-50 align-middle"
            >
              {chipLabel(s)}
            </button>
          ))}
        </p>
      )}
    </div>
  );
}
