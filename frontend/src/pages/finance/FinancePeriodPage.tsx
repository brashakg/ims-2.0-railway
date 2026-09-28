// ============================================================================
// IMS 2.0 - Finance dashboard: Period Management
// ============================================================================
// One section of the old FinanceDashboard tab container, moved verbatim. The
// data and handlers come from FinanceLayout (useFinanceContext).

import { useFinanceContext } from './FinanceLayout';
import PeriodManagement from './PeriodManagement';

export function FinancePeriodPage() {
  const { periodLocked, handleLockPeriod, handleUnlockPeriod, dateFrom, dateTo } = useFinanceContext();
  return (
    <PeriodManagement
      periodLocked={periodLocked}
      onLockPeriod={handleLockPeriod}
      onUnlockPeriod={handleUnlockPeriod}
      dateFrom={dateFrom}
      dateTo={dateTo}
    />
  );
}
