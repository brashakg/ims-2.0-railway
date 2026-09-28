// ============================================================================
// IMS 2.0 - Finance dashboard: Cash Flow
// ============================================================================
// One section of the old FinanceDashboard tab container, moved verbatim. The
// data and handlers come from FinanceLayout (useFinanceContext).

import { useFinanceContext } from './FinanceLayout';
import CashFlowPanel from './CashFlowPanel';
import RestrictedTotalsNotice from './RestrictedTotalsNotice';

export function FinanceCashFlowTabPage() {
  const { cashFlowRestricted, cashFlow } = useFinanceContext();
  return (
    <>
      {/* "Total outflows" below is short by whatever was withheld from
          this role. Say so above the number, not after it. */}
      <RestrictedTotalsNotice
        show={cashFlowRestricted}
        scope="cash outflow figures"
        className="mb-4"
      />
      <CashFlowPanel cashFlow={cashFlow} />
    </>
  );
}
