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
// Shares CATEGORY_FIELDS + the create payload mapping via productAddShared.ts so
// the create contract + per-category required-field enforcement are unchanged.
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
    editMode, isReviewMode, reviewSnapshot, handleBackToQueue,
    variantCtx, exitVariantMode,
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
          <p className="text-sm text-blue-900 flex-1 min-w-[200px]">
            You're editing <span className="font-semibold">{editMode.sku || 'this product'}</span>.
            SKU, barcode and category are locked — saving updates the product, it will NOT create
            a new SKU. Wrong category? Use <span className="font-medium">Clone as new SKU</span>{' '}
            from the Catalog drawer instead.
          </p>
          <button
            type="button"
            onClick={() => navigate(`/catalog?focus=${encodeURIComponent(editMode.id)}`)}
            className="btn-secondary !py-1.5 !px-3 text-xs"
          >
            Cancel
          </button>
        </div>
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
          {/* VARIANT MODE banner: which model this sibling belongs to, the
              copied-field legend, the dictionary-drop note, and the exit. */}
          {variantCtx && (
            <div className="rounded-xl border border-bv-50 bg-bv-soft px-4 py-3 text-sm">
              <div className="flex items-start gap-2.5">
                <CopyPlus className="w-4 h-4 text-bv mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="text-gray-800">
                    <span className="font-semibold">Variant mode</span> — adding a new
                    colour/size of <span className="font-semibold">{variantCtx.sourceLabel}</span>
                    {variantCtx.sourceSku ? (
                      <span className="text-gray-500"> (SKU {variantCtx.sourceSku})</span>
                    ) : null}
                    . Brand &amp; model are locked; <span className="text-amber-700 font-medium">amber</span> fields
                    were copied — confirm or edit them. Each save stays in variant mode
                    for the next colour/size.
                  </p>
                  {variantCtx.dictionaryNote && (
                    <p className="text-xs text-amber-700">{variantCtx.dictionaryNote}</p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={exitVariantMode}
                  className="shrink-0 btn-secondary !py-1 !px-2.5 text-xs flex items-center gap-1.5"
                  title="Exit variant mode and start a fresh blank form (Esc)"
                >
                  <X className="w-3.5 h-3.5" />
                  New model <kbd className="qa-kbd">Esc</kbd>
                </button>
              </div>
            </div>
          )}

          <IdentitySection form={form} />

          <PricingSection form={form} />

          <InventorySection form={form} />

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
