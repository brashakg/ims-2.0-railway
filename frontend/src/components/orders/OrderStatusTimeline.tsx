// ============================================================================
// Order Status Timeline Component
// ============================================================================

import type { StatusHistory } from '../../types';

interface OrderStatusTimelineProps {
  statusHistory?: StatusHistory[];
  createdAt: string;
  createdBy?: string;
}

const STATUS_COLORS: Record<string, { icon: string; color: string; bgColor: string }> = {
  DRAFT: { icon: '📝', color: 'text-gray-600', bgColor: 'bg-gray-100' },
  CONFIRMED: { icon: '✓', color: 'text-blue-600', bgColor: 'bg-blue-100' },
  PROCESSING: { icon: '⏳', color: 'text-yellow-600', bgColor: 'bg-yellow-100' },
  READY: { icon: '📦', color: 'text-green-600', bgColor: 'bg-green-100' },
  DELIVERED: { icon: '🚚', color: 'text-emerald-600', bgColor: 'bg-emerald-100' },
  CANCELLED: { icon: '✕', color: 'text-red-600', bgColor: 'bg-red-100' },
};

export function OrderStatusTimeline({ statusHistory, createdAt, createdBy }: OrderStatusTimelineProps) {
  const formatDateTime = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-IN') + ' ' + date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
  };

  // Build timeline from creation + status history
  const timeline = [
    {
      status: 'DRAFT' as const,
      timestamp: createdAt,
      changedBy: createdBy || 'System',
    },
    ...(statusHistory || []),
  ];

  return (
    <div className="mt-6 pt-6 border-t border-gray-200">
      <h3 className="text-sm font-semibold text-gray-900 mb-4">Status Timeline</h3>
      <div className="space-y-4">
        {timeline.map((entry, index) => {
          const config = STATUS_COLORS[entry.status] || STATUS_COLORS.DRAFT;
          const isLast = index === timeline.length - 1;

          return (
            <div key={`${entry.status}-${index}`} className="flex gap-3">
              {/* Timeline dot */}
              <div className="flex flex-col items-center">
                <div className={`w-8 h-8 rounded-full ${config.bgColor} flex items-center justify-center text-sm`}>
                  {config.icon}
                </div>
                {!isLast && <div className="w-0.5 h-12 bg-gray-200 my-2"></div>}
              </div>

              {/* Timeline content */}
              <div className="flex-1 pb-2">
                <div className="flex items-baseline gap-2">
                  <p className={`font-medium ${config.color}`}>{entry.status}</p>
                  <p className="text-xs text-gray-500">{formatDateTime(entry.timestamp)}</p>
                </div>
                <p className="text-xs text-gray-600 mt-1">Changed by: {entry.changedBy}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
