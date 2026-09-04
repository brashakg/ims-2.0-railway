// ============================================================================
// IMS 2.0 - Tasks: WHO SEES WHAT. One list, one file.
// ============================================================================
// OWNER RULING 2026-09-03: team tasks are for managers and above only -
// STORE_MANAGER, AREA_MANAGER, ADMIN, SUPERADMIN. Everyone else sees ONLY the
// tasks assigned to them.
//
// Before this file the answer was written down THREE times, and the three
// disagreed, which is why nobody could say who was supposed to see team tasks:
//
//   1. routes/taskRoutes.tsx  /tasks gate      -> the 4 managers + ACCOUNTANT
//   2. TasksDashboard.tsx     "Team Tasks" tab -> STORE_MANAGER + AREA_MANAGER
//   3. TaskManagementPage.tsx myTasks filter   -> ADMIN/SUPERADMIN saw EVERY
//                                                 task as if it were "Mine"
//
// All three copies are DELETED (both pages are gone; the route gate now
// imports from here). This is the only place the answer lives - a fourth
// opinion cannot appear without deleting this comment first.
//
// ponytail: exported as arrays, not a class or a policy engine. A role list
// is a list.

import type { UserRole } from '../../types';

/** Team tasks, the SOP library and store-wide performance. Managers and above. */
export const TEAM_TASK_ROLES: UserRole[] = [
  'SUPERADMIN',
  'ADMIN',
  'AREA_MANAGER',
  'STORE_MANAGER',
];

/**
 * Who may open the Tasks module at all - i.e. who gets "Mine" and the daily
 * SOP checklist. Every operational role, floor staff included: sales staff,
 * cashiers, optometrists and workshop staff do most of the tasks and had no
 * Tasks link at all before this split.
 *
 * Copied character-for-character off the existing `/my-work` and `/attendance`
 * gates in routes/taskRoutes.tsx - the established "every operational role"
 * list in this codebase - so a bisect can tell a permission change from a
 * file move.
 */
export const TASK_MODULE_ROLES: UserRole[] = [
  'SUPERADMIN',
  'ADMIN',
  'AREA_MANAGER',
  'STORE_MANAGER',
  'ACCOUNTANT',
  'OPTOMETRIST',
  'CASHIER',
  'SALES_STAFF',
  'WORKSHOP_STAFF',
];

/** The runtime half of the same ruling, for nav/tab visibility. */
export function canSeeTeamTasks(roles: readonly string[] | undefined | null): boolean {
  if (!roles) return false;
  return roles.some((r) => (TEAM_TASK_ROLES as readonly string[]).includes(r));
}
