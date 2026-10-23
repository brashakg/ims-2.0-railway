// ============================================================================
// IMS 2.0 - Quick Add (the single product-add door)
// ============================================================================
// The SOLE product-add screen at /catalog/add. The older "Guided" (6-step
// wizard) and "Bulk" (Rapid Grid) modes + the Single|Guided|Bulk toggle were
// removed; this one-screen form absorbed EVERY field, section, button and
// validation Guided had, so nothing was lost:
//   - Accordion sections: Identity / Pricing / Inventory / Online
//   - Smart defaults (category -> HSN/GST auto) collapsed under "Advanced"
//     (incl. HSN-required marker + GST-compliance note carried over from Guided)
//   - Cost price + margin are role-gated (F35), as in Guided
//   - Product Images placeholder section (parity with Guided)
//   - Live Review summary rail (lists every filled attribute, like Guided's review)
//   - Ctrl+Enter = Save ; Ctrl+Shift+Enter = Save + New (keeps category + brand)
// Shares CATEGORY_FIELDS + the create payload mapping via
// domain/catalog/productAdd so the create contract + per-category
// required-field enforcement are unchanged.
//
// WAVE 3 FILE DIET: this file was ~3,200 lines - the largest in the app. What
// is left is the COMPOSITION. Every block MOVED, unchanged, into ./quickadd/:
// the state / validation / submit glue into useQuickAddForm, then one file per
// block (header, identity, pricing, inventory, online, review card, save bar).
// Nothing was rewritten - no copy, class, field order or payload changed.

import { AlertTriangle, CheckCircle2, CopyPlus, Pencil, X } from 'lucide-react';
import { DuplicateProductModal } from './DuplicateProductModal';
import { IdentitySection } from './quickadd/IdentitySection';
import { InventorySection } from './quickadd/InventorySection';
import { OnlineStrip } from './quickadd/OnlineStrip';
import { PricingSection } from './quickadd/PricingSection';
import { ProductFlagsPanel } from './quickadd/ProductFlagsPanel';
import { QuickAddHeader } from './quickadd/QuickAddHeader';
import { ReviewCard } from './quickadd/ReviewCard';
import { SaveBar } from './quickadd/SaveBar';
import { useQuickAddForm } from './quickadd/useQuickAddForm';

// Where "open the existing product" lands. Re-exported from its old home so
// every existing importer (and its test) is untouched by the split.
export { productListPath } from './quickadd/shared';

export function QuickAddPage() {
  const form = useQuickAddForm();
  const {
    canAddProduct, queueClear, setQueueClear, setSearchParams, navigate,
    editMode, isReviewMode, reviewSnapshot, handleBackToQueue, editListPath,
    variantCtx, exitVariantMode, leaveFamily, familyHint, optedOutLeadId,
    dupInfo, setDupInfo, dupBusy, handleDupAddVariant, handleDupOpenExisting,
  } = form;

  if (!canAddProduct) {
    return (
      <div className="inv-body">
        <div className="card text-center py-12">
          <h2 className="text-xl font-semibold text-gray-700">Access Denied</h2>
          <p className="text-gray-500 mt-1">You don't have permission to add products.</p>
        </div>
      </div>
    );
  }

  // Review-queue terminal state: nothing left to review. A friendly panel,
  // never an error — the reviewer chose to keep going and the queue ran dry.
  if (queueClear) {
    return (
      <div className="inv-body">
        <div className="card max-w-lg mx-auto text-center py-14">
          <CheckCircle2 className="w-10 h-10 mx-auto text-green-600" />
          <h2 className="mt-3 text-xl font-semibold text-gray-900">Review queue clear</h2>
          <p className="mt-1 text-sm text-gray-500">
            Every imported product has been reviewed. Nice work.
          </p>
          <div className="mt-5 flex items-center justify-center gap-2">
            <button
              type="button"
              onClick={() => navigate('/catalog/review')}
              className="btn-primary"
            >
              Back to catalog
            </button>
            <button
              type="button"
              onClick={() => {
                setQueueClear(false);
                setSearchParams(new URLSearchParams(), { replace: true });
              }}
              className="btn-secondary"
            >
              Add a product
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    // Controls keep the app-wide .input-field height: the page-scoped rule
    // that shrank every field to 32px (h-8) is gone -- this is filled on the
    // shop iPad. Only the h1 keeps its page-scoped size (32 -> 28px).
    <div className="inv-body [&_.inv-head_h1]:!text-[28px]">
      <QuickAddHeader form={form} />

      {/* EDIT MODE banner: which product is being edited + the escape hatch. */}
      {editMode?.kind === 'spine' && (
        <div className="mb-3 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 flex flex-wrap items-center gap-3">
          <Pencil className="w-4 h-4 text-blue-600 shrink-0" />
          {editMode.orderedDraft ? (
            <p className="text-sm text-blue-900 flex-1 min-w-[200px]">
              You're finishing <span className="font-semibold">{editMode.sku || 'this product'}</span>,
              ordered before it was catalogued. If it was ordered under the wrong category, pick the
              right one — saving updates this product, it will NOT create a new SKU.
            </p>
          ) : (
            <p className="text-sm text-blue-900 flex-1 min-w-[200px]">
              You're editing <span className="font-semibold">{editMode.sku || 'this product'}</span>.
              SKU, barcode and category are locked — saving updates the product, it will NOT create
              a new SKU. Wrong category? Use <span className="font-medium">Clone as new SKU</span>{' '}
              from the Catalog drawer instead.
            </p>
          )}
          <button
            type="button"
            onClick={() => navigate(editListPath)}
            className="btn-secondary !py-1.5 !px-3 text-xs"
          >
            Cancel
          </button>
        </div>
      )}

      {/* Open catalogue flags on this product: fix, then Mark fixed. */}
      {editMode?.kind === 'spine' && (
        <ProductFlagsPanel key={editMode.id} productId={editMode.id} initialFlags={editMode.flags ?? []} />
      )}

      {/* REVIEW MODE banner: what's being reviewed, the auto-SKU note, the
          read-only online (Shopify) status, and the way back to the queue. */}
      {isReviewMode && (
        <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex flex-wrap items-center gap-3">
          <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0" />
          <div className="text-sm text-amber-900 flex-1 min-w-[220px]">
            <p>
              Reviewing an <span className="font-semibold">imported product</span> — every field
              below is editable. Fix the details, then{' '}
              <span className="font-semibold">Save &amp; Approve</span> to make it sellable.
            </p>
            <p className="mt-0.5 text-xs text-amber-800">
              SKU:{' '}
              <span className="font-medium">
                {editMode?.sku || 'auto — assigned on approval'}
              </span>
              {' · '}barcode is assigned at goods receipt
              {reviewSnapshot?.doc.ecom && (
                <>
                  {' · '}online:{' '}
                  <span className="font-medium">
                    {String(reviewSnapshot.doc.ecom.status || 'linked')}
                  </span>
                  {reviewSnapshot.doc.ecom.handle ? (
                    <span className="text-amber-700"> ({String(reviewSnapshot.doc.ecom.handle)})</span>
                  ) : null}
                </>
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={handleBackToQueue}
            className="btn-secondary !py-1.5 !px-3 text-xs"
          >
            Back to queue
          </button>
        </div>
      )}

      {/* Single full-width column: the review moved to the bottom of the page,
          freeing the old 320px right rail for 3-4 field columns. */}
      <div className="space-y-3">
        {/* ---- Form column ---- */}
        <div className="space-y-3">
          {/* G1: the brand + model typed match an existing model -- Save adds
              this as a new colour/size of it (decided on Save, from what is
              typed then; nothing locks while typing). */}
          {!variantCtx && !editMode && familyHint && familyHint.lead_id !== optedOutLeadId && (
            <div
              className="flex items-start gap-2.5 rounded-xl border border-bv-50 bg-bv-soft px-4 py-3 text-sm"
              data-testid="model-family-hint"
            >
              <CopyPlus className="w-4 h-4 text-bv mt-0.5 shrink-0" />
              <div className="min-w-0 flex-1 space-y-1">
                <p className="text-gray-800">
                  <span className="font-semibold">This model already exists:</span>{' '}
                  <span className="font-semibold">{familyHint.label}</span>
                  <span className="text-gray-500">
                    {' '}— {familyHint.count} variant{familyHint.count === 1 ? '' : 's'}
                  </span>
                  . This will be added as a new colour/size of it; its shape and gender come from the model.
                </p>
                {familyHint.chips.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {familyHint.chips.map((c, i) => (
                      <span
                        key={`${c}-${i}`}
                        className="rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] text-gray-700"
                      >
                        {c}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={leaveFamily}
                className="shrink-0 btn-secondary !py-1 !px-2.5 text-xs"
                title="Save it as a model of its own"
              >
                This is a different model
              </button>
            </div>
          )}

          {/* VARIANT MODE banner: which model this sibling belongs to, the
              copied-field legend, the dictionary-drop note, and the exit. */}
          {variantCtx && (
            <div className="rounded-xl border border-bv-50 bg-bv-soft px-4 py-3 text-sm">
              <div className="flex items-start gap-2.5">
                <CopyPlus className="w-4 h-4 text-bv mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1 space-y-1" data-testid="model-family-banner">
                  {/* G1 (owner 2026-10-18): the model this item joins on Save. */}
                  <p className="text-gray-800">
                    <span className="font-semibold">This model already exists:</span>{' '}
                    <span className="font-semibold">{variantCtx.family?.label || variantCtx.sourceLabel}</span>
                    {variantCtx.family ? (
                      <span className="text-gray-500">
                        {' '}— {variantCtx.family.count} variant{variantCtx.family.count === 1 ? '' : 's'}
                      </span>
                    ) : variantCtx.sourceSku ? (
                      <span className="text-gray-500"> (SKU {variantCtx.sourceSku})</span>
                    ) : null}
                    . This will be added as a new colour/size of it.
                  </p>
                  {variantCtx.family && variantCtx.family.chips.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {variantCtx.family.chips.map((c, i) => (
                        <span
                          key={`${c}-${i}`}
                          className="rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[11px] text-gray-700"
                        >
                          {c}
                        </span>
                      ))}
                    </div>
                  )}
                  <p className="text-xs text-gray-600">
                    Model fields are locked (shared by every colour/size) —{' '}
                    <button
                      type="button"
                      onClick={() => navigate(`/catalog/add?edit=${encodeURIComponent(variantCtx.leadId)}`)}
                      className="font-medium text-bv underline"
                    >
                      Edit on the model
                    </button>
                    . Enter this item&apos;s colour, size, price, barcode and photos;{' '}
                    <span className="text-amber-700 font-medium">amber</span> fields were copied — confirm
                    or edit them.
                  </p>
                  {variantCtx.dictionaryNote && (
                    <p className="text-xs text-amber-700">{variantCtx.dictionaryNote}</p>
                  )}
                </div>
                <div className="flex shrink-0 flex-col gap-1.5">
                  <button
                    type="button"
                    onClick={leaveFamily}
                    className="btn-secondary !py-1 !px-2.5 text-xs"
                    title="Keep what is typed and save it as its own model"
                  >
                    This is a different model
                  </button>
                  <button
                    type="button"
                    onClick={exitVariantMode}
                    className="btn-secondary !py-1 !px-2.5 text-xs flex items-center gap-1.5"
                    title="Start a fresh blank form (Esc)"
                  >
                    <X className="w-3.5 h-3.5" />
                    New model <kbd className="qa-kbd">Esc</kbd>
                  </button>
                </div>
              </div>
            </div>
          )}

          <IdentitySection form={form} />

          <PricingSection form={form} />

          <InventorySection form={form} />

          {/* Soft warnings (owner 2026-10-23): amber hints, never a block. */}
          {form.softWarnings.length > 0 && (
            <ul className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900 space-y-0.5" data-testid="qa-soft-warnings">
              {form.softWarnings.map((w) => (
                <li key={w} className="flex items-start gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 mt-px shrink-0" />
                  {w}
                </li>
              ))}
            </ul>
          )}

          <OnlineStrip form={form} />

        </div>

        <ReviewCard form={form} />

        <SaveBar form={form} />
      </div>

      {/* Duplicate-rescue popup (409 DUPLICATE_PRODUCT). The form behind it is
          left fully intact; "Go back" / Esc simply closes it. */}
      {dupInfo && (
        <DuplicateProductModal
          info={dupInfo}
          busy={dupBusy}
          onAddVariant={() => { void handleDupAddVariant(); }}
          onOpenExisting={handleDupOpenExisting}
          onClose={() => setDupInfo(null)}
        />
      )}
    </div>
  );
}
export default QuickAddPage;
