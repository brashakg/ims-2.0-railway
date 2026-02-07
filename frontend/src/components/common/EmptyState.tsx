// ============================================================================
// IMS 2.0 - Empty State Component
// ============================================================================

import { type LucideIcon, Plus } from 'lucide-react';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description: string;
  action?: {
    label: string;
    onClick: () => void;
  };
  fullHeight?: boolean;
}

export function EmptyState({ icon: Icon, title, description, action, fullHeight = true }: EmptyStateProps) {
  return (
    <div className={`flex items-center justify-center ${fullHeight ? 'min-h-96' : 'py-8'}`}>
      <div className="text-center max-w-sm">
        {Icon && (
          <div className="mb-4">
            <Icon className="w-12 h-12 text-gray-400 mx-auto" />
          </div>
        )}
        <h3 className="text-lg font-semibold text-gray-900 mb-2">{title}</h3>
        <p className="text-gray-600 mb-6">{description}</p>
        {action && (
          <button
            onClick={action.onClick}
            className="inline-flex items-center gap-2 px-4 py-2 bg-blue-50 hover:bg-blue-100 text-blue-600 rounded-lg transition-colors font-medium"
          >
            <Plus className="w-4 h-4" />
            {action.label}
          </button>
        )}
      </div>
    </div>
  );
}

export default EmptyState;
