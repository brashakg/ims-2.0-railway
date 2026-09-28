// ============================================================================
// IMS 2.0 - Finance dashboard: Outstanding & Collections
// ============================================================================
// One section of the old FinanceDashboard tab container, moved verbatim. The
// data and handlers come from FinanceLayout (useFinanceContext).

import { useFinanceContext } from './FinanceLayout';
import OutstandingPanel from './OutstandingPanel';

export function FinanceOutstandingPage() {
  const { outstanding, vendorPayments } = useFinanceContext();
  return (
    <OutstandingPanel outstanding={outstanding} vendorPayments={vendorPayments} />
  );
}
