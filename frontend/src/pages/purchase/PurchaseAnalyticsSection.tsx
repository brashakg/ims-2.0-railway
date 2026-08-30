// ============================================================================
// IMS 2.0 - Purchase Analytics section (/purchase/analytics)
// ============================================================================
// Wave 1 split: thin wrapper that loads POs + suppliers (the old tab
// container fetched them once for every tab) and renders the existing
// PurchaseAnalytics panel.

import { useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { vendorsApi } from '../../services/api';
import { PurchaseAnalytics } from './PurchaseAnalytics';
import { mapVendorToSupplier, mapPOtoPurchaseOrder } from './purchaseMappers';
import type { Supplier, PurchaseOrder } from './purchaseTypes';

export function PurchaseAnalyticsSection() {
  const { user } = useAuth();
  const [isLoading, setIsLoading] = useState(true);
  const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrder[]>([]);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      setIsLoading(true);
      try {
        const storeId = user?.activeStoreId;
        const [vendorsResp, posResp] = await Promise.all([
          vendorsApi.getVendors({ is_active: true }),
          vendorsApi.getPurchaseOrders(storeId ? { store_id: storeId } : {}),
        ]);
        if (!alive) return;
        setSuppliers((vendorsResp?.vendors ?? []).map(mapVendorToSupplier));
        setPurchaseOrders((posResp?.purchase_orders ?? []).map(mapPOtoPurchaseOrder));
      } catch {
        // Read-only analytics: an empty dataset renders as zeroed panels.
      } finally {
        if (alive) setIsLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [user?.activeStoreId]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
      </div>
    );
  }
  return <PurchaseAnalytics purchaseOrders={purchaseOrders} suppliers={suppliers} />;
}
