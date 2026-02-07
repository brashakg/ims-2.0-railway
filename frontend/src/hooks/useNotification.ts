// ============================================================================
// IMS 2.0 - Notification Hook
// ============================================================================

import { useCallback } from 'react';
import { useToast } from '../context/ToastContext';

export interface NotificationOptions {
  duration?: number;
  action?: {
    label: string;
    onClick: () => void;
  };
}

export function useNotification() {
  const toast = useToast();

  const success = useCallback(
    (message: string) => {
      toast.success(message);
    },
    [toast]
  );

  const error = useCallback(
    (message: string) => {
      toast.error(message);
    },
    [toast]
  );

  const warning = useCallback(
    (message: string) => {
      toast.warning?.(message);
    },
    [toast]
  );

  const info = useCallback(
    (message: string) => {
      toast.info?.(message);
    },
    [toast]
  );

  const loading = useCallback(
    (message: string) => {
      // Some toast libraries support loading states
      toast.info?.(message);
    },
    [toast]
  );

  return { success, error, warning, info, loading };
}

export default useNotification;
