// ============================================================================
// IMS 2.0 - Workshop: QC checklist modal
// ============================================================================
// Moved verbatim out of WorkshopPage.tsx (Wave 3 file diet). Same three items,
// same submit paths (/qc-checklist and /qc), same enable rules.

import { useState } from 'react';
import { ClipboardCheck, X } from 'lucide-react';
import type { Job } from './shared';

// ============================================================================
// QC checklist modal — power verification / fitting / cosmetic check + notes.
// The backend /qc endpoint only stores `passed` + a free-text `notes`, so the
// checklist outcome is folded into the notes string. "Pass" requires every
// item ticked; "Fail" needs a reason.
// ============================================================================

const QC_CHECKLIST_ITEMS: Array<{ key: string; label: string; hint: string }> = [
  { key: 'power', label: 'Power verification', hint: 'Lensmeter reading matches the prescription' },
  { key: 'fitting', label: 'Fitting check', hint: 'Lenses seated, frame aligned, screws tight' },
  { key: 'cosmetic', label: 'Cosmetic check', hint: 'No scratches, chips, coating defects or marks' },
];

// Phase 6.9: per-item structured check state: each item has a pass/fail + optional note.
type CheckState = Record<string, { passed: boolean; note: string }>;

export function QcChecklistModal({
  job,
  busy,
  onCancel,
  onSubmit,
}: {
  job: Job;
  busy: boolean;
  onCancel: () => void;
  // checklistItems carries the structured per-item results for /qc-checklist.
  onSubmit: (
    passed: boolean,
    notes: string,
    checklistItems: Array<{ key: string; label: string; passed: boolean; note?: string }>,
  ) => void;
}) {
  const [checks, setChecks] = useState<CheckState>({});
  const [overallNotes, setOverallNotes] = useState('');

  const allChecked = QC_CHECKLIST_ITEMS.every((item) => checks[item.key]?.passed === true);
  const anyFailed = QC_CHECKLIST_ITEMS.some((item) => checks[item.key]?.passed === false);
  // A submit is enabled once every item has been explicitly set (pass or fail)
  const allAnswered = QC_CHECKLIST_ITEMS.every((item) => checks[item.key] !== undefined);

  const buildChecklistItems = () =>
    QC_CHECKLIST_ITEMS.map((item) => ({
      key: item.key,
      label: item.label,
      passed: checks[item.key]?.passed ?? false,
      note: checks[item.key]?.note || undefined,
    }));

  const handlePassItem = (key: string, passed: boolean) => {
    setChecks((prev) => ({
      ...prev,
      [key]: { passed, note: prev[key]?.note || '' },
    }));
  };

  const handleItemNote = (key: string, note: string) => {
    setChecks((prev) => ({
      ...prev,
      [key]: { passed: prev[key]?.passed ?? false, note },
    }));
  };

  const handlePass = () => {
    if (!allChecked || busy) return;
    onSubmit(true, overallNotes, buildChecklistItems());
  };

  const handleFail = () => {
    if (busy) return;
    if (!overallNotes.trim()) return; // a failure must say why
    onSubmit(false, overallNotes, buildChecklistItems());
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md max-h-[90dvh] overflow-y-auto">
        <div className="p-5 border-b border-gray-200 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ClipboardCheck className="w-5 h-5 text-bv-red-600" />
            <div>
              <h3 className="font-semibold text-gray-900">Quality check</h3>
              <p className="text-xs text-gray-500">Job {job.jobNumber || job.id}</p>
            </div>
          </div>
          <button
            onClick={onCancel}
            className="p-1 hover:bg-gray-100 rounded text-gray-500 hover:text-gray-700"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* Per-item checklist — pass/fail toggle + optional note per item */}
          <div className="space-y-3">
            {QC_CHECKLIST_ITEMS.map((item) => {
              const state = checks[item.key];
              const isPassed = state?.passed === true;
              const isFailed = state?.passed === false;
              return (
                <div
                  key={item.key}
                  className={`rounded-lg border p-3 ${
                    isPassed
                      ? 'border-green-200 bg-green-50/40'
                      : isFailed
                      ? 'border-red-200 bg-red-50/40'
                      : 'border-gray-200'
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1">
                      <p className="text-sm font-medium text-gray-900">{item.label}</p>
                      <p className="text-xs text-gray-500">{item.hint}</p>
                    </div>
                    <div className="flex gap-1 shrink-0">
                      <button
                        type="button"
                        onClick={() => handlePassItem(item.key, true)}
                        className={`px-2.5 py-1 text-xs font-semibold rounded border transition-colors ${
                          isPassed
                            ? 'bg-green-600 text-white border-green-600'
                            : 'bg-white text-green-700 border-green-300 hover:bg-green-50'
                        }`}
                      >
                        Pass
                      </button>
                      <button
                        type="button"
                        onClick={() => handlePassItem(item.key, false)}
                        className={`px-2.5 py-1 text-xs font-semibold rounded border transition-colors ${
                          isFailed
                            ? 'bg-red-600 text-white border-red-600'
                            : 'bg-white text-red-700 border-red-300 hover:bg-red-50'
                        }`}
                      >
                        Fail
                      </button>
                    </div>
                  </div>
                  {isFailed && (
                    <input
                      type="text"
                      value={state?.note || ''}
                      onChange={(e) => handleItemNote(item.key, e.target.value)}
                      placeholder="Describe the defect..."
                      className="mt-2 w-full px-2 py-1.5 text-xs border border-red-200 rounded bg-white text-gray-900 placeholder-gray-400"
                    />
                  )}
                </div>
              );
            })}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Overall notes
              {anyFailed && <span className="text-red-500 ml-1">(required to fail QC)</span>}
            </label>
            <textarea
              value={overallNotes}
              onChange={(e) => setOverallNotes(e.target.value)}
              rows={3}
              placeholder="Rework instructions, defect summary, or any additional context..."
              className="input-field text-sm w-full"
            />
          </div>

          {!allAnswered && (
            <p className="text-xs text-gray-400">
              Mark every item pass or fail to submit.
            </p>
          )}
          {allAnswered && !allChecked && (
            <p className="text-xs text-amber-600">
              One or more items failed. Add overall notes describing what needs rework, then click &quot;Fail QC&quot;.
            </p>
          )}
        </div>

        <div className="p-5 border-t border-gray-200 flex gap-2">
          <button
            onClick={handlePass}
            disabled={!allChecked || busy}
            className="btn-success text-sm flex-1 disabled:opacity-50"
          >
            {busy ? 'Saving…' : 'Pass — mark ready'}
          </button>
          <button
            onClick={handleFail}
            disabled={busy || !anyFailed || !overallNotes.trim()}
            className="btn-outline text-sm flex-1 text-red-600 border-red-600 disabled:opacity-50"
          >
            Fail QC
          </button>
        </div>
      </div>
    </div>
  );
}
