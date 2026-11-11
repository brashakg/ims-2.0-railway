// Visible failure for a stored-settings read: never show defaults as if they
// were the saved values, and never leave Save enabled over an unknown
// configuration (audit L1-F01/F05, R43). Pair with useLoadedSettings.

import { AlertCircle } from 'lucide-react';

export function SettingsLoadError({ message, onRetry }: { message: string | null; onRetry?: () => void }) {
  if (!message) return null;
  return (
    <div role="alert" className="p-3 rounded-lg border border-red-200 bg-red-50 text-sm text-red-700 flex flex-wrap items-center gap-3">
      <AlertCircle className="w-4 h-4 shrink-0" />
      <span className="flex-1 min-w-0">{message}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-outline text-sm">
          Retry
        </button>
      )}
    </div>
  );
}

export default SettingsLoadError;
