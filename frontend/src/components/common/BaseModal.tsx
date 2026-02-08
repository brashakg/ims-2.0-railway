// ============================================================================
// IMS 2.0 - Base Modal Component
// ============================================================================
// Reusable modal wrapper to eliminate duplication across 8+ modals

import React from 'react';
import { X } from 'lucide-react';
import clsx from 'clsx';

export interface BaseModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  icon?: React.ComponentType<{ className?: string }>;
  children: React.ReactNode;
  onSubmit?: () => void | Promise<void>;
  submitLabel?: string;
  submitVariant?: 'primary' | 'success' | 'danger';
  isLoading?: boolean;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  showFooter?: boolean;
  cancelLabel?: string;
  hideCancel?: boolean;
  closeOnBackdropClick?: boolean;
  className?: string;
  headerClassName?: string;
  bodyClassName?: string;
  footerClassName?: string;
}

const MODAL_SIZES = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
};

const SUBMIT_COLORS = {
  primary: 'bg-blue-600 hover:bg-blue-700 text-white',
  success: 'bg-green-600 hover:bg-green-700 text-white',
  danger: 'bg-red-600 hover:bg-red-700 text-white',
};

/**
 * BaseModal - Reusable modal component to eliminate duplication
 *
 * Consolidates the common modal pattern found in 8+ components:
 * - Fixed inset backdrop (bg-black/50)
 * - Header with icon, title, and close button
 * - Scrollable content area
 * - Footer with Cancel and Action buttons
 * - Loading and disabled states
 *
 * Usage:
 * <BaseModal
 *   isOpen={showModal}
 *   onClose={() => setShowModal(false)}
 *   title="Create Customer"
 *   icon={UserPlus}
 *   onSubmit={handleCreate}
 *   submitLabel="Create"
 * >
 *   <FormInput label="Name" value={name} onChange={e => setName(e.target.value)} />
 * </BaseModal>
 */
export function BaseModal({
  isOpen,
  onClose,
  title,
  icon: Icon,
  children,
  onSubmit,
  submitLabel = 'Save',
  submitVariant = 'primary',
  isLoading = false,
  size = 'md',
  showFooter = true,
  cancelLabel = 'Cancel',
  hideCancel = false,
  closeOnBackdropClick = true,
  className,
  headerClassName,
  bodyClassName,
  footerClassName,
}: BaseModalProps) {
  if (!isOpen) return null;

  const handleBackdropClick = (e: React.MouseEvent) => {
    if (closeOnBackdropClick && e.target === e.currentTarget) {
      onClose();
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={handleBackdropClick}
    >
      <div
        className={clsx(
          'bg-white dark:bg-gray-900 rounded-lg shadow-xl',
          'w-full max-h-[90vh] overflow-hidden flex flex-col',
          MODAL_SIZES[size],
          className
        )}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className={clsx(
            'border-b border-gray-200 dark:border-gray-800',
            'px-6 py-4 flex items-center justify-between',
            headerClassName
          )}
        >
          <div className="flex items-center gap-3">
            {Icon && <Icon className="w-5 h-5 text-gray-600 dark:text-gray-400" />}
            <h2 className="text-lg font-bold text-gray-900 dark:text-white">
              {title}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
            aria-label="Close modal"
          >
            <X className="w-5 h-5 text-gray-500 dark:text-gray-400" />
          </button>
        </div>

        {/* Content */}
        <div
          className={clsx(
            'flex-1 overflow-y-auto px-6 py-4',
            bodyClassName
          )}
        >
          {children}
        </div>

        {/* Footer */}
        {showFooter && (
          <div
            className={clsx(
              'border-t border-gray-200 dark:border-gray-800',
              'px-6 py-4 flex items-center justify-end gap-2 bg-gray-50 dark:bg-gray-800/50',
              footerClassName
            )}
          >
            {!hideCancel && (
              <button
                onClick={onClose}
                disabled={isLoading}
                className={clsx(
                  'px-4 py-2 rounded-lg font-medium transition-colors',
                  'border border-gray-300 dark:border-gray-700',
                  'text-gray-700 dark:text-gray-300',
                  'hover:bg-gray-100 dark:hover:bg-gray-700',
                  'disabled:opacity-50 disabled:cursor-not-allowed'
                )}
              >
                {cancelLabel}
              </button>
            )}
            {onSubmit && (
              <button
                onClick={onSubmit}
                disabled={isLoading}
                className={clsx(
                  'px-4 py-2 rounded-lg font-medium transition-colors',
                  SUBMIT_COLORS[submitVariant],
                  'disabled:opacity-50 disabled:cursor-not-allowed',
                  'flex items-center gap-2'
                )}
              >
                {isLoading && (
                  <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-current" />
                )}
                {submitLabel}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default BaseModal;
