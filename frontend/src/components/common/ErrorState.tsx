// ============================================================================
// IMS 2.0 - Error State Component
// ============================================================================

import { AlertTriangle, RefreshCw } from 'lucide-react';
import { formatApiError } from '../../utils/errorHandler';

interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  fullHeight?: boolean;
  title?: string;
  message?: string;
}

export function ErrorState({ error, onRetry, fullHeight = true, title, message }: ErrorStateProps) {
  const formatted = title && message ? { title, message } : formatApiError(error);

  return (
    <div className={`flex items-center justify-center ${fullHeight ? 'min-h-96' : 'py-8'}`}>
      <div className="text-center max-w-md">
        <div className="mb-4">
          <AlertTriangle className="w-12 h-12 text-red-500 mx-auto" />
        </div>
        <h3 className="text-lg font-semibold text-gray-900 mb-2">{formatted.title}</h3>
        <p className="text-gray-600 mb-6">{formatted.message}</p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="inline-flex items-center gap-2 px-4 py-2 bg-red-50 hover:bg-red-100 text-red-600 rounded-lg transition-colors font-medium"
          >
            <RefreshCw className="w-4 h-4" />
            Try Again
          </button>
        )}
      </div>
    </div>
  );
}

export default ErrorState;
