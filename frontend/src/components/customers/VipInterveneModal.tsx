// ============================================================================
// IMS 2.0 - VIP Intervene Modal (F40)
// ============================================================================
// Shared intervention dialog used by both the VIP Churn Watchlist page and
// the Customer 360 dashboard. Picks one of four intervention types and a free
// notes field, then POSTs to /crm/vip-churn/{id}/intervene. The server creates
// a deduped P1 task + an immutable audit row. When the customer was already
// actioned in the current 30-day window, the API returns already_intervened
// and we surface "Already actioned this period" instead of inventing a task.
//
// Restrained/executive UI: neutral chrome, single blue accent on the primary
// action only, no decorative colour. SUPERADMIN/ADMIN-only — gating lives in
// the caller (this component is never rendered for other roles).

import { useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { crmApi } from '../../services/api/crm';
import type { VipInterventionType } from '../../services/api/crm';
import { useToast } from '../../context/ToastContext';

interface InterventionOption {
  value: VipInterventionType;
  label: string;
  hint: string;
}

// Plain-language labels for each intervention type. WINBACK_WHATSAPP queues a
// message via MEGAPHONE's drain loop (gated by DISPATCH_MODE server-side).
const INTERVENTION_OPTIONS: InterventionOption[] = [
  { value: 'PERSONAL_CALL', label: 'Personal call', hint: 'Store manager calls the customer directly.' },
  { value: 'EXCLUSIVE_OFFER', label: 'Exclusive offer', hint: 'Send a personalised, VIP-only offer.' },
  { value: 'LOYALTY_BONUS', label: 'Loyalty bonus', hint: 'Award bonus loyalty points as a goodwill gesture.' },
  { value: 'WINBACK_WHATSAPP', label: 'Win-back WhatsApp', hint: 'Queue a win-back WhatsApp message.' },
];

interface VipInterveneModalProps {
  customerId: string;
  customerName?: string;
  isOpen: boolean;
  onClose: () => void;
  /** Fired after a successful intervention so the caller can refresh. */
  onSuccess?: () => void;
}

export function VipInterveneModal({
  customerId,
  customerName,
  isOpen,
  onClose,
  onSuccess,
}: VipInterveneModalProps) {
  const toast = useToast();
  const [interventionType, setInterventionType] = useState<VipInterventionType>('PERSONAL_CALL');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);

  if (!isOpen) return null;

  const reset = () => {
    setInterventionType('PERSONAL_CALL');
    setNotes('');
  };

  const handleClose = () => {
    if (submitting) return;
    reset();
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting || !customerId) return;
    setSubmitting(true);
    try {
      const res = await crmApi.interveneVipChurn(customerId, {
        intervention_type: interventionType,
        notes: notes.trim(),
      });
      if (res.already_intervened) {
        toast.info('Already actioned this period');
      } else {
        toast.success('P1 task created for the store manager');
      }
      onSuccess?.();
      reset();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not log the intervention');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Log VIP intervention"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div className="bg-white border border-gray-200 rounded-lg shadow-lg w-full max-w-md">
        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Intervene</h2>
            <p className="text-sm text-gray-500">
              {customerName ? `Action for ${customerName}` : 'Log a retention action'}
            </p>
          </div>
          <button
            type="button"
            onClick={handleClose}
            disabled={submitting}
            className="text-gray-400 hover:text-gray-600 disabled:opacity-50"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <form onSubmit={handleSubmit}>
          <div className="px-5 py-4 space-y-4">
            <fieldset>
              <legend className="text-sm font-medium text-gray-700 mb-2">Intervention type</legend>
              <div className="space-y-2">
                {INTERVENTION_OPTIONS.map((opt) => (
                  <label
                    key={opt.value}
                    className="flex items-start gap-3 px-3 py-2 border border-gray-200 rounded cursor-pointer hover:border-gray-300"
                  >
                    <input
                      type="radio"
                      name="intervention_type"
                      value={opt.value}
                      checked={interventionType === opt.value}
                      onChange={() => setInterventionType(opt.value)}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block text-sm font-medium text-gray-900">{opt.label}</span>
                      <span className="block text-xs text-gray-500">{opt.hint}</span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>

            <div>
              <label htmlFor="vip-intervene-notes" className="block text-sm font-medium text-gray-700 mb-1">
                Notes
              </label>
              <textarea
                id="vip-intervene-notes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={3}
                placeholder="Context for the store manager (what to say, what to offer)…"
                className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-gray-200">
            <button
              type="button"
              onClick={handleClose}
              disabled={submitting}
              className="px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded hover:bg-blue-700 disabled:opacity-50 inline-flex items-center gap-1.5"
            >
              {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
              {submitting ? 'Logging…' : 'Log intervention'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default VipInterveneModal;
