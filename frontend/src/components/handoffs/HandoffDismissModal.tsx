// ============================================================================
// IMS 2.0 — Handoff Dismiss Modal
// ============================================================================
// After the recipient picks a response (approved / denied / accepted /
// received) we ask whether to remove the card from the Hub. Three
// outcomes:
//   - Dismiss: gone (re-find via /handoffs/inbox if needed)
//   - Keep:    sticks around on the Hub permanently (until TTL)
//   - Snooze:  re-appears after 1h / 4h / 24h

import { useState } from 'react';
import { X, Loader2, Trash2, Bookmark, Clock } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { handoffsApi, type DismissAction } from '../../services/api/handoffs';

interface HandoffDismissModalProps {
  isOpen: boolean;
  onClose: () => void;
  handoffId: string | null;
  /** What the user picked just before this modal opened. Shown back to
   *  them as "You replied 'approved'." for context. */
  lastResponse: string | null;
  onResolved?: () => void;
}

const SNOOZE_OPTIONS: Array<{ label: string; minutes: number }> = [
  { label: '1 hour', minutes: 60 },
  { label: '4 hours', minutes: 240 },
  { label: '24 hours', minutes: 1440 },
];

export function HandoffDismissModal({
  isOpen,
  onClose,
  handoffId,
  lastResponse,
  onResolved,
}: HandoffDismissModalProps) {
  const toast = useToast();
  const [busyAction, setBusyAction] = useState<DismissAction | null>(null);
  const [showSnoozeMenu, setShowSnoozeMenu] = useState(false);

  if (!isOpen || !handoffId) return null;

  const submit = async (action: DismissAction, snoozeMinutes?: number) => {
    setBusyAction(action);
    try {
      await handoffsApi.dismiss(handoffId, action, snoozeMinutes);
      const successMsg =
        action === 'dismiss'
          ? 'Removed from your Hub.'
          : action === 'keep'
            ? 'Kept on your Hub.'
            : `Snoozed for ${snoozeMinutes && snoozeMinutes >= 60
                ? `${Math.round(snoozeMinutes / 60)}h`
                : `${snoozeMinutes}m`}.`;
      toast.success(successMsg);
      onResolved?.();
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not update card.');
    } finally {
      setBusyAction(null);
      setShowSnoozeMenu(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-sm overflow-hidden">
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <h2 className="text-base font-semibold text-gray-900">Card on your Hub</h2>
          <button
            onClick={onClose}
            className="p-1.5 text-gray-500 hover:text-gray-600 transition-colors"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 space-y-3">
          <p className="text-sm text-gray-600">
            {lastResponse ? (
              <>
                You replied <span className="font-semibold text-gray-900">"{lastResponse}"</span>.
                Keep this card on your Hub?
              </>
            ) : (
              <>What should we do with this card now?</>
            )}
          </p>

          {!showSnoozeMenu ? (
            <div className="grid grid-cols-1 gap-2">
              <button
                type="button"
                onClick={() => submit('dismiss')}
                disabled={busyAction !== null}
                className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg border border-gray-200 hover:bg-gray-50 disabled:opacity-50 text-left"
              >
                <span className="flex items-center gap-2.5">
                  <span className="w-7 h-7 rounded-md bg-gray-100 flex items-center justify-center text-gray-600">
                    <Trash2 className="w-3.5 h-3.5" />
                  </span>
                  <span>
                    <span className="block text-sm font-medium text-gray-900">Dismiss</span>
                    <span className="block text-xs text-gray-500">Remove from Hub now</span>
                  </span>
                </span>
                {busyAction === 'dismiss' && <Loader2 className="w-4 h-4 animate-spin text-gray-500" />}
              </button>

              <button
                type="button"
                onClick={() => submit('keep')}
                disabled={busyAction !== null}
                className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg border border-gray-200 hover:bg-gray-50 disabled:opacity-50 text-left"
              >
                <span className="flex items-center gap-2.5">
                  <span className="w-7 h-7 rounded-md bg-blue-50 flex items-center justify-center text-blue-700">
                    <Bookmark className="w-3.5 h-3.5" />
                  </span>
                  <span>
                    <span className="block text-sm font-medium text-gray-900">Keep</span>
                    <span className="block text-xs text-gray-500">Stays on Hub until expiry</span>
                  </span>
                </span>
                {busyAction === 'keep' && <Loader2 className="w-4 h-4 animate-spin text-gray-500" />}
              </button>

              <button
                type="button"
                onClick={() => setShowSnoozeMenu(true)}
                disabled={busyAction !== null}
                className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg border border-gray-200 hover:bg-gray-50 disabled:opacity-50 text-left"
              >
                <span className="flex items-center gap-2.5">
                  <span className="w-7 h-7 rounded-md bg-amber-50 flex items-center justify-center text-amber-700">
                    <Clock className="w-3.5 h-3.5" />
                  </span>
                  <span>
                    <span className="block text-sm font-medium text-gray-900">Snooze</span>
                    <span className="block text-xs text-gray-500">Hide and re-surface later</span>
                  </span>
                </span>
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-gray-500">Hide this card for…</p>
              <div className="grid grid-cols-3 gap-2">
                {SNOOZE_OPTIONS.map((opt) => (
                  <button
                    key={opt.minutes}
                    type="button"
                    onClick={() => submit('snooze', opt.minutes)}
                    disabled={busyAction !== null}
                    className="px-2 py-2 rounded-lg border border-gray-200 hover:bg-amber-50 hover:border-amber-300 text-sm font-medium text-gray-900 disabled:opacity-50"
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={() => setShowSnoozeMenu(false)}
                disabled={busyAction !== null}
                className="w-full text-xs text-gray-500 hover:text-gray-700 mt-1 disabled:opacity-50"
              >
                Back
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default HandoffDismissModal;
