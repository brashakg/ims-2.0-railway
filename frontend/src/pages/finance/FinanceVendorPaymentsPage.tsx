// ============================================================================
// IMS 2.0 - Finance dashboard: Vendor Payments
// ============================================================================
// One section of the old FinanceDashboard tab container, moved verbatim. The
// data and handlers come from FinanceLayout (useFinanceContext).

import { useFinanceContext } from './FinanceLayout';
import VendorPayments from './VendorPayments';

export function FinanceVendorPaymentsPage() {
  const { vendorPayments } = useFinanceContext();
  return (
    <VendorPayments vendorPayments={vendorPayments} />
  );
}
