// ============================================================================
// IMS 2.0 - Purchase Invoices section (/purchase/invoices)
// ============================================================================
// Wave 1 split: thin wrapper that loads the supplier list (the old tab
// container fetched it once for every tab) and renders the existing
// PurchaseInvoicesTab. The ?grn_id= deep-link auto-open lives inside
// PurchaseInvoicesTab and works unchanged at this URL.

import { useState, useEffect } from 'react';
import { vendorsApi } from '../../services/api';
import { PurchaseInvoicesTab } from './PurchaseInvoicesTab';
import { mapVendorToSupplier } from './purchaseMappers';
import type { Supplier } from './purchaseTypes';

export function PurchaseInvoicesSection() {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const vendorsResp = await vendorsApi.getVendors({ is_active: true });
        const rawVendors: unknown[] = vendorsResp?.vendors ?? [];
        if (alive) setSuppliers(rawVendors.map(mapVendorToSupplier));
      } catch {
        // The invoices tab renders without the supplier list (its own loads
        // surface their errors); an empty list only narrows the filter options.
      }
    })();
    return () => { alive = false; };
  }, []);

  return <PurchaseInvoicesTab suppliers={suppliers} />;
}
