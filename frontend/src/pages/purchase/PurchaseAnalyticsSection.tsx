// ============================================================================
// IMS 2.0 - Purchase Analytics section (/purchase/analytics)
// ============================================================================
// Wave 1 split: thin wrapper over the shared React Query cache (instant on
// section switches) rendering the existing PurchaseAnalytics panel.

import { Loader2 } from 'lucide-react';
import { usePurchaseShop } from './purchaseShop';
import { PurchaseAnalytics } from './PurchaseAnalytics';
import { useSuppliers, usePurchaseOrdersQuery } from './purchaseQueries';

export function PurchaseAnalyticsSection() {
  const { storeId } = usePurchaseShop(); // audit F63: one Purchase scope
  const suppliersQ = useSuppliers();
  const posQ = usePurchaseOrdersQuery(storeId);

  if (suppliersQ.isPending || posQ.isPending) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
      </div>
    );
  }
  // Read-only analytics: an empty dataset renders as zeroed panels.
  return (
    <PurchaseAnalytics
      purchaseOrders={posQ.data?.orders ?? []}
      totalOrders={posQ.data?.total}
      suppliers={suppliersQ.data ?? []}
    />
  );
}
