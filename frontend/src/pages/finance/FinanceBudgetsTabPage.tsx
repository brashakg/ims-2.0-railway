// ============================================================================
// IMS 2.0 - Finance dashboard: Budgets
// ============================================================================
// One section of the old FinanceDashboard tab container, moved verbatim. The
// data and handlers come from FinanceLayout (useFinanceContext).

import { useFinanceContext } from './FinanceLayout';
import BudgetPanel from './BudgetPanel';
import RestrictedTotalsNotice from './RestrictedTotalsNotice';

export function FinanceBudgetsTabPage() {
  const { budgetRestricted, budgets, selectedYear } = useFinanceContext();
  return (
    <>
      {/* Rows AND the totals underneath are short by the same amount. */}
      <RestrictedTotalsNotice
        show={budgetRestricted}
        scope="budget rows and totals"
        className="mb-4"
      />
      <BudgetPanel budgets={budgets} selectedYear={selectedYear} />
    </>
  );
}
