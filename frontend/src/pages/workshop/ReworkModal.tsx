// ============================================================================
// IMS 2.0 - Workshop: F13 rework justification modal
// ============================================================================
// Moved verbatim out of WorkshopPage.tsx (Wave 3 file diet). The backend still
// rejects a remake without a reason code; nothing about that gate changed.
import type { WorkshopPageState } from './useWorkshopPage';

export function ReworkModal({ page }: { page: WorkshopPageState }) {
  const { reworkModalJobId, setReworkModalJobId, reworkCode, setReworkCode, reworkCodes, reworkNotes, setReworkNotes, handleRework, qcBusy } = page;
  return (
    <>
      {/* F13 — REWORK JUSTIFICATION MODAL: a remake needs a reason code
          (backend rejects without one); the spoiled lens cost is logged. */}
      {reworkModalJobId && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md">
            <div className="p-5 border-b border-gray-200 flex items-center justify-between">
              <h3 className="font-semibold text-gray-900">Send for rework</h3>
              <button
                type="button"
                aria-label="Close"
                onClick={() => setReworkModalJobId(null)}
                className="p-1 hover:bg-gray-100 rounded text-gray-500 hover:text-gray-700"
              >
                ×
              </button>
            </div>
            <div className="p-5 space-y-4">
              <p className="text-sm text-gray-600">
                A rework spoils the current lens. Pick the reason — it drives the
                spoilage cost report.
              </p>
              <div>
                <label htmlFor="rework-reason-code" className="block text-sm font-medium text-gray-700 mb-1">
                  Remake reason <span className="text-bv-red-600">*</span>
                </label>
                <select
                  id="rework-reason-code"
                  value={reworkCode}
                  onChange={(e) => setReworkCode(e.target.value)}
                  className="w-full px-3 py-2.5 border border-gray-300 bg-white text-gray-900 rounded-lg text-sm"
                >
                  <option value="">Select a reason…</option>
                  {reworkCodes.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.label} ({c.category.replace(/_/g, ' ').toLowerCase()})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="rework-notes" className="block text-sm font-medium text-gray-700 mb-1">
                  Notes (optional)
                </label>
                <textarea
                  id="rework-notes"
                  value={reworkNotes}
                  onChange={(e) => setReworkNotes(e.target.value)}
                  rows={2}
                  placeholder="What exactly went wrong?"
                  className="w-full px-3 py-2.5 border border-gray-300 bg-white text-gray-900 rounded-lg text-sm placeholder-gray-500"
                />
              </div>
              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setReworkModalJobId(null)}
                  className="btn-outline text-sm"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => handleRework(reworkModalJobId, reworkCode, reworkNotes || undefined)}
                  disabled={qcBusy || !reworkCode}
                  className="btn-primary text-sm disabled:opacity-50"
                >
                  Confirm rework
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

    </>
  );
}
