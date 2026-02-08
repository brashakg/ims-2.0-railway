// ============================================================================
// IMS 2.0 - Bulk Actions Component
// ============================================================================
// Toolbar for performing bulk operations on selected items

import { useState } from 'react';
import { Trash2, Download, Edit2, Copy, CheckSquare, Square, X } from 'lucide-react';
import clsx from 'clsx';

export type BulkAction = 'delete' | 'export' | 'edit' | 'duplicate' | 'archive' | 'restore';

export interface BulkActionConfig {
  type: BulkAction;
  label: string;
  icon: React.ReactNode;
  color: 'red' | 'blue' | 'green' | 'amber' | 'gray';
  requiresConfirm?: boolean;
  confirmText?: string;
}

interface BulkActionsProps {
  selectedIds: string[];
  totalItems: number;
  onSelectAll?: () => void;
  onDeselectAll?: () => void;
  onAction: (action: BulkAction, ids: string[]) => Promise<void> | void;
  actions?: BulkActionConfig[];
  disabled?: boolean;
}

const defaultActions: BulkActionConfig[] = [
  {
    type: 'export',
    label: 'Export',
    icon: <Download className="w-4 h-4" />,
    color: 'blue',
  },
  {
    type: 'edit',
    label: 'Edit',
    icon: <Edit2 className="w-4 h-4" />,
    color: 'amber',
  },
  {
    type: 'duplicate',
    label: 'Duplicate',
    icon: <Copy className="w-4 h-4" />,
    color: 'green',
  },
  {
    type: 'delete',
    label: 'Delete',
    icon: <Trash2 className="w-4 h-4" />,
    color: 'red',
    requiresConfirm: true,
    confirmText: 'Delete selected items permanently?',
  },
];

const colorClasses = {
  red: 'text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20',
  blue: 'text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20',
  green: 'text-green-600 hover:bg-green-50 dark:hover:bg-green-900/20',
  amber: 'text-amber-600 hover:bg-amber-50 dark:hover:bg-amber-900/20',
  gray: 'text-gray-600 hover:bg-gray-50 dark:hover:bg-gray-900/20',
};

export function BulkActionsToolbar({
  selectedIds,
  totalItems,
  onSelectAll,
  onDeselectAll,
  onAction,
  actions = defaultActions,
  disabled = false,
}: BulkActionsProps) {
  const [showConfirm, setShowConfirm] = useState<BulkAction | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const isSelected = selectedIds.length > 0;
  const isAllSelected = selectedIds.length === totalItems && totalItems > 0;

  const handleAction = async (action: BulkAction) => {
    const actionConfig = actions.find(a => a.type === action);

    if (actionConfig?.requiresConfirm) {
      setShowConfirm(action);
      return;
    }

    await performAction(action);
  };

  const performAction = async (action: BulkAction) => {
    setActionLoading(true);
    setShowConfirm(null);

    try {
      await Promise.resolve(onAction(action, selectedIds));
      onDeselectAll?.();
    } catch (error) {
      console.error(`Error performing ${action}:`, error);
      alert(`Failed to perform ${action}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleToggleSelectAll = () => {
    if (isAllSelected) {
      onDeselectAll?.();
    } else {
      onSelectAll?.();
    }
  };

  if (!isSelected) {
    return null;
  }

  return (
    <>
      {/* Toolbar */}
      <div className="sticky bottom-0 left-0 right-0 bg-blue-50 dark:bg-blue-900/20 border-t-2 border-blue-200 dark:border-blue-800 p-4 shadow-lg z-40">
        <div className="flex items-center justify-between gap-4">
          {/* Selection Info */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleToggleSelectAll}
              className="p-2 hover:bg-blue-100 dark:hover:bg-blue-900/40 rounded-lg transition-colors"
              title={isAllSelected ? 'Deselect all' : 'Select all'}
              aria-label={isAllSelected ? 'Deselect all' : 'Select all'}
            >
              {isAllSelected ? (
                <CheckSquare className="w-5 h-5 text-blue-600" />
              ) : (
                <Square className="w-5 h-5 text-blue-600" />
              )}
            </button>
            <div>
              <p className="text-sm font-bold text-gray-900 dark:text-white">
                {selectedIds.length} selected
              </p>
              <p className="text-xs text-gray-600 dark:text-gray-400">
                {isAllSelected && totalItems > selectedIds.length
                  ? `All ${totalItems} items selected`
                  : `of ${totalItems} total`}
              </p>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2">
            {actions.map(action => (
              <button
                key={action.type}
                onClick={() => handleAction(action.type)}
                disabled={disabled || actionLoading}
                className={clsx(
                  'inline-flex items-center gap-2 px-3 py-2 rounded-lg font-medium transition-colors text-sm',
                  colorClasses[action.color],
                  (disabled || actionLoading) && 'opacity-50 cursor-not-allowed'
                )}
                title={action.label}
                aria-label={action.label}
              >
                {action.icon}
                <span className="hidden sm:inline">{action.label}</span>
              </button>
            ))}

            <button
              onClick={onDeselectAll}
              className="p-2 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors text-gray-600 dark:text-gray-400"
              title="Clear selection"
              aria-label="Clear selection"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>

      {/* Confirmation Modal */}
      {showConfirm && (
        <div
          className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
          onClick={() => setShowConfirm(null)}
        >
          <div
            className="bg-white dark:bg-gray-900 rounded-lg shadow-lg p-6 max-w-sm w-full"
            onClick={e => e.stopPropagation()}
            role="alertdialog"
          >
            <h2 className="text-lg font-bold text-gray-900 dark:text-white mb-2">
              Confirm Action
            </h2>
            <p className="text-gray-700 dark:text-gray-300 mb-6">
              {actions.find(a => a.type === showConfirm)?.confirmText}
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setShowConfirm(null)}
                className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors font-medium"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  performAction(showConfirm);
                  setShowConfirm(null);
                }}
                disabled={actionLoading}
                className="flex-1 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors font-medium disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {actionLoading ? 'Processing...' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/**
 * Checkbox for table row selection
 */
interface BulkSelectCheckboxProps {
  id: string;
  isSelected: boolean;
  onChange: (id: string, selected: boolean) => void;
  disabled?: boolean;
}

export function BulkSelectCheckbox({
  id,
  isSelected,
  onChange,
  disabled = false,
}: BulkSelectCheckboxProps) {
  return (
    <input
      type="checkbox"
      checked={isSelected}
      onChange={e => onChange(id, e.target.checked)}
      disabled={disabled}
      className="w-4 h-4 text-blue-600 rounded cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
      aria-label={`Select ${id}`}
    />
  );
}

/**
 * Column header checkbox for selecting all visible items
 */
interface BulkSelectHeaderProps {
  isAllSelected: boolean;
  isIndeterminate: boolean;
  onChange: (selected: boolean) => void;
  disabled?: boolean;
}

export function BulkSelectHeader({
  isAllSelected,
  isIndeterminate,
  onChange,
  disabled = false,
}: BulkSelectHeaderProps) {
  return (
    <input
      type="checkbox"
      checked={isAllSelected}
      ref={input => {
        if (input) {
          input.indeterminate = isIndeterminate;
        }
      }}
      onChange={e => onChange(e.target.checked)}
      disabled={disabled}
      className="w-4 h-4 text-blue-600 rounded cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
      aria-label="Select all items"
      title={isIndeterminate ? 'Some items selected' : isAllSelected ? 'All items selected' : 'No items selected'}
    />
  );
}
