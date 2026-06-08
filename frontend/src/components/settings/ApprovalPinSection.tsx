// ============================================================================
// IMS 2.0 - Settings: Set / change your approval PIN
// ============================================================================
// Self-service approval-PIN management for any approver. A PIN is required to
// approve or reject requests in the E4 inbox. It is 4–6 digits, hashed with
// bcrypt server-side, and never returned. Self-rotation requires the current
// PIN; an ADMIN can force-set another user's PIN from User Management.

import { useCallback, useEffect, useState } from 'react';
import { KeyRound, Loader2, ShieldCheck, ShieldAlert } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { approvalsApi } from '../../services/api/approvals';
import { formatDateTimeIST } from '../../utils/datetime';

export function ApprovalPinSection() {
  const { user } = useAuth();
  const toast = useToast();
  const userId = user?.id || '';

  const [loading, setLoading] = useState(true);
  const [hasPin, setHasPin] = useState(false);
  const [pinSetAt, setPinSetAt] = useState<string | null>(null);
  const [currentPin, setCurrentPin] = useState('');
  const [newPin, setNewPin] = useState('');
  const [confirmPin, setConfirmPin] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const loadStatus = useCallback(async () => {
    if (!userId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const res = await approvalsApi.getPinStatus(userId);
      setHasPin(!!res.has_pin);
      setPinSetAt(res.pin_set_at ?? null);
    } catch {
      // Leave defaults (treated as "no PIN") on failure.
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const reset = () => {
    setCurrentPin('');
    setNewPin('');
    setConfirmPin('');
  };

  const handleSubmit = async () => {
    if (!userId) return;
    if (!/^\d{4,6}$/.test(newPin)) {
      toast.error('PIN must be 4–6 digits');
      return;
    }
    if (newPin !== confirmPin) {
      toast.error('PINs do not match');
      return;
    }
    if (hasPin && !currentPin) {
      toast.error('Enter your current PIN to change it');
      return;
    }
    setSubmitting(true);
    try {
      await approvalsApi.setPin(userId, newPin, hasPin ? currentPin : undefined);
      toast.success(hasPin ? 'Approval PIN updated' : 'Approval PIN set');
      reset();
      await loadStatus();
    } catch (e) {
      const msg =
        (e instanceof Error && e.message) || 'Could not update your PIN';
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const newPinValid = /^\d{4,6}$/.test(newPin);
  const canSubmit =
    !submitting &&
    newPinValid &&
    newPin === confirmPin &&
    (!hasPin || currentPin.length >= 4);

  return (
    <div className="border border-gray-200 rounded-lg bg-white p-4">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 p-2 rounded-lg bg-gray-100 text-gray-600 shrink-0">
          <KeyRound className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-gray-900">
            Your Approval PIN
          </h3>
          <p className="mt-0.5 text-xs text-gray-500">
            Required to approve or reject requests. 4–6 digits, stored encrypted.
          </p>

          {loading ? (
            <div className="flex items-center gap-2 mt-3 text-gray-500 text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              Checking PIN status…
            </div>
          ) : (
            <>
              <div className="mt-2">
                {hasPin ? (
                  <span className="inline-flex items-center gap-1 text-xs text-green-700">
                    <ShieldCheck className="w-3.5 h-3.5" />
                    PIN is set
                    {pinSetAt && (
                      <span className="text-gray-400">
                        {' '}
                        · {formatDateTimeIST(pinSetAt)}
                      </span>
                    )}
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-xs text-amber-700">
                    <ShieldAlert className="w-3.5 h-3.5" />
                    No PIN set — you cannot approve requests yet
                  </span>
                )}
              </div>

              <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-md">
                {hasPin && (
                  <div className="sm:col-span-2">
                    <label className="block text-xs font-medium text-gray-700 mb-1">
                      Current PIN
                    </label>
                    <input
                      type="password"
                      inputMode="numeric"
                      autoComplete="new-password"
                      maxLength={6}
                      value={currentPin}
                      onChange={(e) =>
                        setCurrentPin(e.target.value.replace(/\D/g, ''))
                      }
                      className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm font-mono tracking-widest focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                    />
                  </div>
                )}
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">
                    {hasPin ? 'New PIN' : 'PIN'}
                  </label>
                  <input
                    type="password"
                    inputMode="numeric"
                    autoComplete="new-password"
                    maxLength={6}
                    value={newPin}
                    onChange={(e) => setNewPin(e.target.value.replace(/\D/g, ''))}
                    placeholder="4–6 digits"
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm font-mono tracking-widest focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">
                    Confirm PIN
                  </label>
                  <input
                    type="password"
                    inputMode="numeric"
                    autoComplete="new-password"
                    maxLength={6}
                    value={confirmPin}
                    onChange={(e) =>
                      setConfirmPin(e.target.value.replace(/\D/g, ''))
                    }
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm font-mono tracking-widest focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                  />
                </div>
              </div>

              <div className="mt-3">
                <button
                  type="button"
                  onClick={handleSubmit}
                  disabled={!canSubmit}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
                  {hasPin ? 'Update PIN' : 'Set PIN'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default ApprovalPinSection;
