// ============================================================================
// IMS 2.0 - PO Status Badge Helper
// ============================================================================

import {
  FileText,
  CheckCircle,
  Clock,
  X as XIcon,
  Truck,
} from 'lucide-react';
import type { POStatus } from './purchaseTypes';

export function getStatusBadge(status: POStatus) {
  const config = {
    DRAFT: { label: 'Draft', color: 'bg-gray-100 text-gray-800', icon: FileText },
    PENDING: { label: 'Pending Approval', color: 'bg-yellow-100 text-yellow-800', icon: Clock },
    APPROVED: { label: 'Approved', color: 'bg-blue-100 text-blue-800', icon: CheckCircle },
    ORDERED: { label: 'Ordered', color: 'bg-purple-100 text-purple-800', icon: Truck },
    RECEIVED: { label: 'Received', color: 'bg-green-100 text-green-800', icon: CheckCircle },
    CANCELLED: { label: 'Cancelled', color: 'bg-red-100 text-red-800', icon: XIcon },
  };

  const { label, color, icon: Icon } = config[status];

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium ${color}`}>
      <Icon className="w-3 h-3" />
      {label}
    </span>
  );
}
