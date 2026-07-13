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
    // Single muted semantic palette: neutral (draft), amber (pending/partial),
    // blue (in-progress: approved/sent/acknowledged/ordered), green (received),
    // red (cancelled). Decorative indigo/teal/purple collapse into the info blue.
    DRAFT: { label: 'Draft', color: 'bg-gray-100 text-gray-700', icon: FileText },
    PENDING: { label: 'Pending Approval', color: 'bg-amber-50 text-amber-700', icon: Clock },
    APPROVED: { label: 'Approved', color: 'bg-blue-50 text-blue-700', icon: CheckCircle },
    SENT: { label: 'Sent', color: 'bg-blue-50 text-blue-700', icon: Send },
    ACKNOWLEDGED: { label: 'Acknowledged', color: 'bg-blue-50 text-blue-700', icon: CheckCircle },
    ORDERED: { label: 'Ordered', color: 'bg-blue-50 text-blue-700', icon: Truck },
    PARTIAL: { label: 'Partially Received', color: 'bg-amber-50 text-amber-700', icon: Truck },
    PARTIALLY_RECEIVED: { label: 'Partially Received', color: 'bg-amber-50 text-amber-700', icon: Truck },
    RECEIVED: { label: 'Received', color: 'bg-green-50 text-green-700', icon: CheckCircle },
    CANCELLED: { label: 'Cancelled', color: 'bg-red-50 text-red-700', icon: XIcon },
  };

  // Fallback so any unmapped/legacy status renders instead of crashing the whole
  // Purchase tab: the backend emits SENT / ACKNOWLEDGED / PARTIALLY_RECEIVED which
  // were absent from this map, so destructuring config[status] threw and the
  // app-level ErrorBoundary unmounted everything once a PO was sent.
  const { label, color, icon: Icon } = config[status] ?? {
    label: String(status || 'Unknown'),
    color: 'bg-gray-100 text-gray-700',
    icon: FileText,
  };

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${color}`}>
      <Icon className="w-3 h-3" />
      {label}
    </span>
  );
}
