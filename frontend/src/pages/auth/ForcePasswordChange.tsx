// ============================================================================
// IMS 2.0 - Forced Password Change (first login)
// ============================================================================
// Shown full-screen, blocking the rest of the app, when the signed-in user
// still has an admin-set temporary password (user.mustChangePassword === true).
// On success the user record is refreshed (flag now false) and the gate lifts.
// ============================================================================

import { useState } from 'react';
import { Eye, EyeOff, Lock, AlertCircle, ShieldCheck } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { authApi } from '../../services/api/auth';

export function ForcePasswordChange() {
  const { user, logout, refreshUser } = useAuth();
  const toast = useToast();

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showCurrent, setShowCurrent] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!currentPassword || !newPassword || !confirmPassword) {
      setError('Please fill in all three fields.');
      return;
    }
    if (newPassword.length < 8) {
      setError('New password must be at least 8 characters.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('New password and confirmation do not match.');
      return;
    }
    if (newPassword === currentPassword) {
      setError('New password must be different from the temporary password.');
      return;
    }

    setSubmitting(true);
    try {
      const res = await authApi.changePassword(currentPassword, newPassword);
      // Backend returns { message } on success; ApiResponse.success may be
      // undefined for that shape, so treat a non-throwing call as success.
      if (res && res.success === false) {
        setError(res.message || 'Could not change password. Please try again.');
        return;
      }
      toast.success('Password changed. Welcome aboard!');
      // Re-fetch the user so mustChangePassword flips to false and the gate
      // lifts. If the refresh fails for any reason, fall back to logout so the
      // user re-authenticates and picks up the cleared flag.
      try {
        await refreshUser();
      } catch {
        await logout();
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not change password.';
      // Surface the backend's "Current password is incorrect" cleanly.
      setError(msg.includes('incorrect') ? 'Your temporary password is incorrect.' : msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-bv-600 rounded-2xl mb-4">
            <ShieldCheck className="w-8 h-8 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">Set a new password</h1>
          <p className="text-gray-500 mt-1">
            Signed in as <span className="font-medium">{user?.name || user?.email}</span>
          </p>
        </div>

        {/* Card */}
        <div className="card">
          <div className="mb-5 p-3 bg-bv-50 border border-bv-soft rounded-lg flex items-start gap-2">
            <Lock className="w-5 h-5 text-bv-600 flex-shrink-0 mt-0.5" aria-hidden="true" />
            <p className="text-sm text-gray-700">
              You're using a temporary password set by your administrator. Choose a new
              password to continue.
            </p>
          </div>

          {error && (
            <div
              role="alert"
              aria-live="assertive"
              className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg flex items-start gap-2"
            >
              <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" aria-hidden="true" />
              <span className="text-sm text-red-700">{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4" aria-label="Set new password form">
            {/* Current (temporary) password */}
            <div>
              <label htmlFor="current-password" className="block text-sm font-medium text-gray-700 mb-1">
                Temporary password <span className="text-red-600" aria-label="required">*</span>
              </label>
              <div className="relative">
                <input
                  type={showCurrent ? 'text' : 'password'}
                  id="current-password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  className="input-field pr-10"
                  placeholder="Enter the temporary password"
                  autoComplete="current-password"
                  disabled={submitting}
                  aria-required="true"
                />
                <button
                  type="button"
                  onClick={() => setShowCurrent(!showCurrent)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-600 focus:outline-none focus:ring-2 focus:ring-bv-600 rounded"
                  aria-label={showCurrent ? 'Hide password' : 'Show password'}
                  aria-pressed={showCurrent ? "true" : "false"}
                >
                  {showCurrent ? <EyeOff className="w-5 h-5" aria-hidden="true" /> : <Eye className="w-5 h-5" aria-hidden="true" />}
                </button>
              </div>
            </div>

            {/* New password */}
            <div>
              <label htmlFor="new-password" className="block text-sm font-medium text-gray-700 mb-1">
                New password <span className="text-red-600" aria-label="required">*</span>
              </label>
              <div className="relative">
                <input
                  type={showNew ? 'text' : 'password'}
                  id="new-password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="input-field pr-10"
                  placeholder="New password (min 8 characters)"
                  autoComplete="new-password"
                  disabled={submitting}
                  aria-required="true"
                />
                <button
                  type="button"
                  onClick={() => setShowNew(!showNew)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-600 focus:outline-none focus:ring-2 focus:ring-bv-600 rounded"
                  aria-label={showNew ? 'Hide password' : 'Show password'}
                  aria-pressed={showNew ? "true" : "false"}
                >
                  {showNew ? <EyeOff className="w-5 h-5" aria-hidden="true" /> : <Eye className="w-5 h-5" aria-hidden="true" />}
                </button>
              </div>
            </div>

            {/* Confirm new password */}
            <div>
              <label htmlFor="confirm-password" className="block text-sm font-medium text-gray-700 mb-1">
                Confirm new password <span className="text-red-600" aria-label="required">*</span>
              </label>
              <input
                type={showNew ? 'text' : 'password'}
                id="confirm-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="input-field"
                placeholder="Re-enter the new password"
                autoComplete="new-password"
                disabled={submitting}
                aria-required="true"
              />
            </div>

            <button
              type="submit"
              disabled={submitting}
              className="btn-primary w-full py-3 flex items-center justify-center gap-2"
              aria-busy={submitting ? "true" : "false"}
            >
              {submitting ? (
                <>
                  <div className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" aria-hidden="true" />
                  <span>Updating...</span>
                </>
              ) : (
                'Set new password'
              )}
            </button>
          </form>

          {/* Escape hatch: log out instead */}
          <div className="mt-4 text-center">
            <button
              type="button"
              onClick={() => { logout(); }}
              className="text-sm text-gray-600 hover:text-bv-600 transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-bv-600 rounded px-1"
            >
              Sign out
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ForcePasswordChange;
