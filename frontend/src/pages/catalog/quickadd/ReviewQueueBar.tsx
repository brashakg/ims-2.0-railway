// ---------------------------------------------------------------------------
// ReviewQueueBar — the review-mode replacement for the sticky action bar.
// MODULE-LEVEL on purpose (same lesson as Section: a nested component identity
// would remount per keystroke). Queue position + Prev/Next (Alt+arrows), the
// LIVE promote dry-run verdict as compact chips (green "ready" / amber gaps /
// orange duplicate hint), and the three actions: Save fixes (Ctrl+Enter),
// Save & Approve (Ctrl+Shift+Enter, the primary), Skip. Muted palette per the
// house theme — green-600 marks the approve CTA (money-forward action).
// ---------------------------------------------------------------------------
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Loader2, Save } from 'lucide-react';
import clsx from 'clsx';

export function ReviewQueueBar({
  positionLabel,
  canPrev,
  dirty,
  saving,
  approving,
  dryLoading,
  dryOk,
  gapChips,
  otherIssues,
  dupCount,
  onPrev,
  onNext,
  onSave,
  onApprove,
}: {
  positionLabel: string;
  canPrev: boolean;
  dirty: boolean;
  saving: boolean;
  approving: boolean;
  dryLoading: boolean;
  /** null = dry-run not back yet. */
  dryOk: boolean | null;
  gapChips: Array<{ key: string; label: string; title: string }>;
  otherIssues: string[];
  dupCount: number;
  onPrev: () => void;
  onNext: () => void;
  onSave: () => void;
  onApprove: () => void;
}) {
  const busy = saving || approving;
  return (
    <div className="sticky bottom-0 z-30 -mx-7 -mb-6 px-7 py-2.5 bg-white/95 backdrop-blur border-t border-gray-200 flex flex-wrap items-center gap-3">
      {/* Queue position + navigation */}
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onPrev}
          disabled={!canPrev || busy}
          className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 disabled:opacity-30"
          aria-label="Previous review item"
          title="Previous (Alt+←)"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <span className="text-xs font-medium text-gray-600 whitespace-nowrap min-w-[86px] text-center">
          {positionLabel}
        </span>
        <button
          type="button"
          onClick={onNext}
          disabled={busy}
          className="p-1.5 rounded-lg text-gray-500 hover:bg-gray-100 disabled:opacity-30"
          aria-label="Next review item"
          title="Next (Alt+→)"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

      {/* Live dry-run verdict */}
      <div className="flex-1 min-w-[160px] flex flex-wrap items-center gap-1.5">
        {dryLoading ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-500">
            <Loader2 className="w-3 h-3 animate-spin" /> Checking readiness…
          </span>
        ) : dryOk ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2 py-0.5 text-[11px] font-medium text-green-700">
            <CheckCircle2 className="w-3 h-3" /> Ready to approve
          </span>
        ) : dryOk === false ? (
          <>
            {gapChips.map((c) => (
              <span
                key={c.key}
                title={c.title}
                className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700"
              >
                <AlertTriangle className="w-3 h-3" /> {c.label}
              </span>
            ))}
            {otherIssues.map((msg, i) => (
              <span
                key={`other-${i}`}
                title={msg}
                className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700 max-w-[260px]"
              >
                <AlertTriangle className="w-3 h-3 shrink-0" />
                <span className="truncate">{msg}</span>
              </span>
            ))}
          </>
        ) : null}
        {dupCount > 0 && (
          <span
            className="inline-flex items-center gap-1 rounded-full bg-orange-50 px-2 py-0.5 text-[11px] text-orange-700"
            title="A similar billing product already exists — approving creates a second sellable product."
          >
            <AlertTriangle className="w-3 h-3" /> possible duplicate
          </span>
        )}
        {dirty && (
          <span className="inline-flex items-center rounded-full bg-blue-50 px-2 py-0.5 text-[11px] text-blue-700">
            unsaved changes
          </span>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onNext}
          disabled={busy}
          className="rounded-lg px-3 py-2 text-sm text-gray-600 hover:bg-gray-100 disabled:opacity-50"
          title="Move to the next item without saving (Alt+→)"
        >
          Skip
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={busy}
          className="btn-secondary flex items-center gap-1.5"
          title="Save the fixes and stay on this item (Ctrl+Enter)"
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          Save fixes
        </button>
        <button
          type="button"
          onClick={onApprove}
          disabled={busy}
          className={clsx(
            'flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-white',
            busy ? 'bg-gray-300 cursor-not-allowed' : 'bg-green-600 hover:bg-green-700'
          )}
          title="Save, validate and make this product sellable, then move on (Ctrl+Shift+Enter)"
        >
          {approving ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <CheckCircle2 className="w-4 h-4" />
          )}
          Save &amp; Approve
        </button>
      </div>
    </div>
  );
}
