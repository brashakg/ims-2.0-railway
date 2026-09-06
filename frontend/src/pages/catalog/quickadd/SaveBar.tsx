// Quick Add - the sticky action bar: the single Save point, always reachable.
// Must stay the LAST child of the page container so sticky containment spans
// the whole page; -mx-7/-mb-6 cancel .inv-body's padding so the bar runs
// edge-to-edge, flush with the viewport bottom. Review mode swaps in the
// ReviewQueueBar (queue position + Prev/Next + gap chips + Save fixes /
// Save & Approve / Skip).
// MOVED verbatim out of QuickAddPage.tsx (Wave 3 file diet).

import { Keyboard, Loader2, RotateCcw, Save, X } from 'lucide-react';
import { fieldLabelFor } from '../productAddShared';
import { ReviewQueueBar } from './ReviewQueueBar';
import type { QuickAddForm } from './useQuickAddForm';

export function SaveBar({ form }: { form: QuickAddForm }) {
  const {
    isReviewMode, queuePos, reviewDirty, isSubmitting, reviewApproving,
    reviewDryLoading, reviewDry, reviewGapView,
    handleReviewPrev, handleReviewSkip, handleReviewSave, handleReviewApprove,
    handleSubmit, editMode, variantCtx, navigate, liveErrors, jumpToField,
    selectedCategory,
  } = form;

  return (
    <>
      {isReviewMode ? (
        <ReviewQueueBar
          positionLabel={queuePos ? `Item ${queuePos.n} of ${queuePos.m}` : 'Review item'}
          canPrev={Boolean(queuePos?.hasPrev)}
          dirty={reviewDirty}
          saving={isSubmitting}
          approving={reviewApproving}
          dryLoading={reviewDryLoading}
          dryOk={reviewDry ? reviewDry.ok : null}
          gapChips={reviewGapView.chips}
          otherIssues={reviewGapView.other}
          dupCount={reviewDry?.duplicate_warnings?.length ?? 0}
          onPrev={handleReviewPrev}
          onNext={handleReviewSkip}
          onSave={() => void handleReviewSave()}
          onApprove={() => void handleReviewApprove()}
        />
      ) : (
      <div className="sticky bottom-0 z-30 -mx-7 -mb-6 px-7 py-2.5 bg-white/95 backdrop-blur border-t border-gray-200 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => handleSubmit(false)}
          disabled={isSubmitting}
          className="btn-primary flex items-center gap-2"
        >
          {isSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {editMode?.kind === 'spine' ? 'Save changes' : variantCtx ? 'Save variant' : 'Save product'}
        </button>
        {editMode?.kind === 'spine' ? (
          <button
            type="button"
            onClick={() => navigate(`/catalog?focus=${encodeURIComponent(editMode.id)}`)}
            disabled={isSubmitting}
            className="btn-secondary flex items-center gap-2"
          >
            <X className="w-4 h-4" />
            Cancel
          </button>
        ) : (
          <button
            type="button"
            onClick={() => handleSubmit(true)}
            disabled={isSubmitting}
            className="btn-secondary flex items-center gap-2"
          >
            <RotateCcw className="w-4 h-4" />
            Save + New
          </button>
        )}
        {/* What the validator would still refuse -- its own list, live. Save
            stays enabled; each chip jumps to (and opens) its field. */}
        {Object.keys(liveErrors).length > 0 && (
          <span
            className="flex flex-wrap items-center gap-1.5 text-xs text-gray-600"
            data-testid="qa-still-missing"
          >
            Still missing:
            {Object.keys(liveErrors).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => jumpToField(k)}
                className="inline-flex min-h-8 items-center rounded-full bg-red-50 px-2.5 text-[11px] font-medium text-red-700 hover:bg-red-100"
              >
                {fieldLabelFor(selectedCategory, k)}
              </button>
            ))}
            <span className="text-gray-400">— tap one to jump to it</span>
          </span>
        )}
        <span className="ml-auto hidden tablet:flex items-center gap-4 text-xs text-gray-500">
          <Keyboard className="w-4 h-4" />
          <span><kbd className="qa-kbd">Ctrl</kbd>+<kbd className="qa-kbd">Enter</kbd> Save</span>
          {!editMode && (
            <span><kbd className="qa-kbd">Ctrl</kbd>+<kbd className="qa-kbd">Shift</kbd>+<kbd className="qa-kbd">Enter</kbd> Save + New</span>
          )}
        </span>
      </div>
      )}
    </>
  );
}
