// ============================================================================
// IMS 2.0 - PO Status Badge Helper
// ============================================================================

import {
  FileText,
  CheckCircle,
  Clock,
  X as XIcon,
  Truck,
  Send,
  type LucideIcon,
} from 'lucide-react';
import type { POStatus } from './purchaseTypes';

type POStatusBadge = { label: string; color: string; icon: LucideIcon };

export function getStatusBadge(status: POStatus) {
  const config: Record<string, POStatusBadge> = {
    DRAFT: { label: 'Draft', color: 'bg-gray-100 text-gray-800', icon: FileText },
    PENDING: { label: 'Pending Approval', color: 'bg-yellow-100 text-yellow-800', icon: Clock },
    APPROVED: { label: 'Approved', color: 'bg-blue-100 text-blue-800', icon: CheckCircle },
    SENT: { label: 'Sent', color: 'bg-indigo-100 text-indigo-800', icon: Send },
    ACKNOWLEDGED: { label: 'Acknowledged', color: 'bg-teal-100 text-teal-800', icon: CheckCircle },
    ORDERED: { label: 'Ordered', color: 'bg-purple-100 text-purple-800', icon: Truck },
    PARTIAL: { label: 'Partially Received', color: 'bg-amber-100 text-amber-800', icon: Truck },
    PARTIALLY_RECEIVED: { label: 'Partially Received', color: 'bg-amber-100 text-amber-800', icon: Truck },
    RECEIVED: { label: 'Received', color: 'bg-green-100 text-green-800', icon: CheckCircle },
    CANCELLED: { label: 'Cancelled', color: 'bg-red-100 text-red-800', icon: XIcon },
  };

  // Fallback so any unmapped/legacy status renders instead of crashing the whole
  // Purchase tab: the backend emits SENT / ACKNOWLEDGED / PARTIALLY_RECEIVED which
  // were absent from this map, so destructuring config[status] threw and the
  // app-level ErrorBoundary unmounted everything once a PO was sent.
  const { label, color, icon: Icon } = config[status] ?? {
    label: String(status || 'Unknown'),
    color: 'bg-gray-100 text-gray-800',
    icon: FileText,
  };

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${color}`}>
      <Icon className="w-3 h-3" />
      {label}
    </span>
  );
}
