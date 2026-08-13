// ============================================================================
// IMS 2.0 - Payroll access notice
// ============================================================================
// ONE honest empty-state for every payroll screen that a non-admin can open but
// no longer read (owner ruling 2026-08-09: salary data is ADMIN/SUPERADMIN
// only, plus your own).
//
// WHY THIS EXISTS. Each of these screens used to fall back to an empty list on
// error: an empty salary sheet, an empty payroll-run grid, "No payslip found
// for selected month". After the ruling those screens 403 for ACCOUNTANT /
// AREA_MANAGER / STORE_MANAGER, and the empty table would read as "nobody was
// paid this month" or "this employee has no payslip" -- a data-integrity lie
// dressed as a permissions change. This says the true thing instead, in the
// words a shop manager uses. Same pattern as PR #960 (honest states replacing
// false "coming online" screens).

import { Lock } from 'lucide-react';

interface PayrollAccessNoticeProps {
  /** The server's own 403 detail when there is one. */
  message?: string;
  /** What the user was trying to open, e.g. "the salary sheet". */
  what?: string;
}

export function PayrollAccessNotice({ message, what }: PayrollAccessNoticeProps) {
  return (
    <div
      role="status"
      className="flex flex-col items-center justify-center gap-3 rounded-lg border border-gray-200 bg-gray-50 px-6 py-12 text-center"
    >
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gray-200">
        <Lock className="h-5 w-5 text-gray-600" aria-hidden="true" />
      </div>
      <p className="text-base font-semibold text-gray-900">
        You do not have access to payroll data
      </p>
      <p className="max-w-md text-sm text-gray-600">
        {message ||
          `Salary and payroll information${
            what ? ` (${what})` : ''
          } is restricted to administrators. Please ask an administrator.`}
      </p>
      <p className="max-w-md text-xs text-gray-500">
        This is a permission limit, not an error &mdash; the data exists and is unchanged.
        You can always see your own pay under My Work.
      </p>
    </div>
  );
}

export default PayrollAccessNotice;
