// ============================================================================
// IMS 2.0 — F50 Send-to-Floor Drawer (clinical -> retail handover)
// ============================================================================
// Opened from a COMPLETED eye-test row. The optometrist adds free-text product
// recommendations (max 5 rows) + an optional one-line note for sales, then
// sends. This mints a CLINICAL_RX handover (8h TTL) and rings the in-app bell
// for every sales associate + store manager at the store. NO outbound message.
// Restrained, light UI: neutral throughout, single accent (bv-red) on the
// primary Send action only.

import { useState } from 'react';
import { X, Plus } from 'lucide-react';
import { clinicalApi } from '../../services/api';
import { useToast } from '../../context/ToastContext';

const CATEGORY_OPTIONS = [
  'Progressive',
  'Single Vision',
  'Bifocal',
  'AR Coating',
  'Photochromic',
  'Contact Lens',
  'Sunglasses',
  'Frame',
] as const;

interface RecRow {
  category: string;
  brandPreference: string;
  notes: string;
}

const EMPTY_ROW: RecRow = { category: '', brandPreference: '', notes: '' };

export interface SendToFloorDrawerProps {
  testId: string;
  patientName: string;
  onClose: () => void;
  /** Fired after a successful send (or an already-sent response) so the parent
   *  can flip the row's button to "Sent". */
  onSent?: () => void;
}

export function SendToFloorDrawer({ testId, patientName, onClose, onSent }: SendToFloorDrawerProps) {
  const toast = useToast();
  const [rows, setRows] = useState<RecRow[]>([{ ...EMPTY_ROW }]);
  const [summary, setSummary] = useState('');
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);

  const addRow = () => {
    if (rows.length >= 5) return;
    setRows((r) => [...r, { ...EMPTY_ROW }]);
  };

  const removeRow = (idx: number) => {
    setRows((r) => (r.length <= 1 ? [{ ...EMPTY_ROW }] : r.filter((_, i) => i !== idx)));
  };

  const updateRow = (idx: number, patch: Partial<RecRow>) => {
    setRows((r) => r.map((row, i) => (i === idx ? { ...row, ...patch } : row)));
  };

  const handleSend = async () => {
    setSending(true);
    try {
      // Drop empty rows; keep only those with at least one field filled.
      const recs = rows
        .filter((r) => r.category || r.brandPreference.trim() || r.notes.trim())
        .map((r) => ({
          category: r.category || undefined,
          brandPreference: r.brandPreference.trim() || undefined,
          notes: r.notes.trim() || undefined,
        }));
      const res = await clinicalApi.sendToFloor(testId, {
        productRecommendations: recs,
        clinicalSummary: summary.trim() || undefined,
      });
      setSent(true);
      if (res.already_sent) {
        toast.info('Already sent — the handover is active on the floor.');
      } else {
        toast.success(`Handover sent to ${res.recipient_count} staff member${res.recipient_count === 1 ? '' : 's'}.`);
      }
      onSent?.();
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 403) {
        toast.error('Clinical handover is not enabled for this store.');
      } else if (status === 422) {
        toast.error('This test must be completed with a prescription before sending.');
      } else {
        toast.error('Could not send the handover. Please try again.');
      }
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Scrim */}
      <button
        type="button"
        aria-label="Close"
        className="absolute inset-0 bg-black/30"
        onClick={onClose}
      />
      {/* Drawer */}
      <div className="relative h-full w-full max-w-[420px] bg-white shadow-xl flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Send to Floor</h2>
            <p className="text-xs text-gray-500">{patientName}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 text-gray-400 hover:text-gray-700 transition-colors"
            aria-label="Close drawer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {/* Recommendations */}
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 mb-2">
              Doctor&apos;s recommendations
            </p>
            <div className="space-y-3">
              {rows.map((row, idx) => (
                <div key={idx} className="border border-gray-200 rounded-md p-3 space-y-2">
                  <select
                    value={row.category}
                    onChange={(e) => updateRow(idx, { category: e.target.value })}
                    className="input-field w-full text-sm"
                  >
                    <option value="">Category (optional)</option>
                    {CATEGORY_OPTIONS.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                  <input
                    type="text"
                    value={row.brandPreference}
                    onChange={(e) => updateRow(idx, { brandPreference: e.target.value })}
                    placeholder="Brand, e.g. Zeiss"
                    maxLength={60}
                    className="input-field w-full text-sm"
                  />
                  <input
                    type="text"
                    value={row.notes}
                    onChange={(e) => updateRow(idx, { notes: e.target.value })}
                    placeholder="Optional note"
                    maxLength={200}
                    className="input-field w-full text-sm"
                  />
                  {rows.length > 1 && (
                    <button
                      type="button"
                      onClick={() => removeRow(idx)}
                      className="text-xs text-gray-400 hover:text-gray-700"
                    >
                      Remove
                    </button>
                  )}
                </div>
              ))}
            </div>
            {rows.length < 5 && (
              <button
                type="button"
                onClick={addRow}
                className="mt-2 inline-flex items-center gap-1 text-xs text-gray-600 hover:text-gray-900"
              >
                <Plus className="w-3.5 h-3.5" /> Add recommendation
              </button>
            )}
          </div>

          {/* Note for sales */}
          <div>
            <label className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 mb-1.5 block">
              Note for sales
            </label>
            <input
              type="text"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder="e.g. patient prefers lightweight frames"
              maxLength={120}
              className="input-field w-full text-sm"
            />
          </div>
        </div>

        <div className="px-5 py-4 border-t border-gray-200 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="text-sm text-gray-500 hover:text-gray-800"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSend}
            disabled={sending || sent}
            className="px-4 py-2 rounded-md text-sm font-medium text-white bg-bv-red-600 hover:bg-bv-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {sent ? 'Sent' : sending ? 'Sending…' : 'Send to Floor'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default SendToFloorDrawer;
