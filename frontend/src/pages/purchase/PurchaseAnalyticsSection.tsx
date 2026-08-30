// ============================================================================
// IMS 2.0 - Purchase Analytics section (/purchase/analytics)
// ============================================================================
// Wave 1 split: thin wrapper over the shared React Query cache (instant on
// section switches) rendering the existing PurchaseAnalytics panel.

import { Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { PurchaseAnalytics } from './PurchaseAnalytics';
import { useSuppliers, usePurchaseOrdersQuery } from './purchaseQueries';

export function PurchaseAnalyticsSection() {
  const { user } = useAuth();
  const suppliersQ = useSuppliers();
  const posQ = usePurchaseOrdersQuery(user?.activeStoreId);

  if (suppliersQ.isPending || posQ.isPending) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
      </div>
    );
  }
  // Read-only analytics: an empty dataset renders as zeroed panels.
  return <PurchaseAnalytics purchaseOrders={posQ.data ?? []} suppliers={suppliersQ.data ?? []} />;
}
