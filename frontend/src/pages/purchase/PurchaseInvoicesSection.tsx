// ============================================================================
// IMS 2.0 - Purchase Invoices section (/purchase/invoices)
// ============================================================================
// Wave 1 split: thin wrapper that provides the supplier list (shared React
// Query cache — instant on section switches) and renders the existing
// PurchaseInvoicesTab. The ?grn_id= deep-link auto-open lives inside
// PurchaseInvoicesTab and works unchanged at this URL.

import { PurchaseInvoicesTab } from './PurchaseInvoicesTab';
import { useSuppliers } from './purchaseQueries';

export function PurchaseInvoicesSection() {
  // The invoices tab renders fine with an empty list while this loads (its
  // own fetches surface their errors); the list only narrows filter options.
  const suppliersQ = useSuppliers();
  return <PurchaseInvoicesTab suppliers={suppliersQ.data ?? []} />;
}
