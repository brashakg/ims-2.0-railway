// ============================================================================
// IMS 2.0 - Session Expiry Warning Component
// ============================================================================

import { AlertTriangle, Clock } from 'lucide-react';
import { useSessionExpiry } from '../../hooks/useSessionExpiry';

export function SessionExpiryWarning() {
  const { showWarning, timeRemaining, extendSession } = useSessionExpiry();

  if (!showWarning || !timeRemaining) {
    return null;
  }

  const minutes = Math.floor(timeRemaining / 60000);
  const seconds = Math.floor((timeRemaining % 60000) / 1000);

  return (
    <div className="fixed top-4 right-4 z-[60] max-w-md">
      <div className="bg-yellow-50 border border-yellow-200 rounded-lg shadow-lg p-4">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-yellow-600 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <h3 className="font-semibold text-yellow-900 mb-1">Session Expiring Soon</h3>
            <div className="flex items-center gap-1 text-sm text-yellow-800 mb-3">
              <Clock className="w-4 h-4" />
              <span>
                {minutes > 0
                  ? `${minutes} minute${minutes !== 1 ? 's' : ''} ${seconds} second${seconds !== 1 ? 's' : ''} remaining`
                  : `${seconds} second${seconds !== 1 ? 's' : ''} remaining`}
              </span>
            </div>
            <button
              onClick={extendSession}
              className="w-full bg-yellow-600 hover:bg-yellow-700 text-white font-medium py-2 px-3 rounded-lg transition-colors text-sm"
            >
              Extend Session
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default SessionExpiryWarning;
