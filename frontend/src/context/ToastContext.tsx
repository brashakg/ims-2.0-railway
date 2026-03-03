// ============================================================================
// IMS 2.0 - Toast Context
// ============================================================================
// Provides toast notifications throughout the application

import { createContext, useContext, useState, useCallback } from 'react';
import type { ReactNode } from 'react';

// ============================================================================
// Types
// ============================================================================

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface Toast {
  id: string;
  type: ToastType;
  message: string;
  duration?: number;
}

interface ToastContextType {
  toasts: Toast[];
  success: (message: unknown, duration?: number) => void;
  error: (message: unknown, duration?: number) => void;
  warning: (message: unknown, duration?: number) => void;
  info: (message: unknown, duration?: number) => void;
  dismiss: (id: string) => void;
  dismissAll: () => void;
}

// ============================================================================
// Context
// ============================================================================

const ToastContext = createContext<ToastContextType | undefined>(undefined);

// ============================================================================
// Provider Component
// ============================================================================

interface ToastProviderProps {
  children: ReactNode;
}

export function ToastProvider({ children }: ToastProviderProps) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  // Safety: convert any input to readable string (prevents [object Object])
  const safeMessage = (msg: unknown): string => {
    if (typeof msg === 'string') return msg;
    if (msg instanceof Error) return msg.message;
    if (msg && typeof msg === 'object') {
      const obj = msg as Record<string, unknown>;
      if (typeof obj.detail === 'string') return obj.detail;
      if (Array.isArray(obj.detail))
        return obj.detail.map((d: Record<string, unknown>) => (d.msg as string) || String(d)).join('. ');
      if (typeof obj.message === 'string') return obj.message;
    }
    return String(msg || 'An error occurred');
  };

  const addToast = useCallback((type: ToastType, message: unknown, duration = 5000) => {
    const safeMsg = safeMessage(message);
    const id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    const toast: Toast = { id, type, message: safeMsg, duration };

    setToasts((prev) => [...prev, toast]);

    // Auto-dismiss after duration
    if (duration > 0) {
      setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, duration);
    }

    return id;
  }, []);

  const success = useCallback((message: unknown, duration?: number) => {
    addToast('success', message, duration);
  }, [addToast]);

  const error = useCallback((message: unknown, duration?: number) => {
    addToast('error', message, duration);
  }, [addToast]);

  const warning = useCallback((message: unknown, duration?: number) => {
    addToast('warning', message, duration);
  }, [addToast]);

  const info = useCallback((message: unknown, duration?: number) => {
    addToast('info', message, duration);
  }, [addToast]);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const dismissAll = useCallback(() => {
    setToasts([]);
  }, []);

  const value: ToastContextType = {
    toasts,
    success,
    error,
    warning,
    info,
    dismiss,
    dismissAll,
  };

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Toast Container */}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`px-4 py-3 rounded-lg shadow-lg flex items-start gap-3 animate-slide-up ${
              toast.type === 'success'
                ? 'bg-green-600 text-white'
                : toast.type === 'error'
                ? 'bg-red-600 text-white'
                : toast.type === 'warning'
                ? 'bg-amber-500 text-white'
                : 'bg-blue-600 text-white'
            }`}
          >
            <span className="flex-1 text-sm">{toast.message}</span>
            <button
              onClick={() => dismiss(toast.id)}
              className="text-white/80 hover:text-white"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// ============================================================================
// Hook
// ============================================================================

export function useToast(): ToastContextType {
  const context = useContext(ToastContext);
  if (context === undefined) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}

export default ToastContext;
