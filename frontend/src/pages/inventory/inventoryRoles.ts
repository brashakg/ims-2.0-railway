// ============================================================================
// IMS 2.0 - Inventory: WHO SEES WHAT. One list, one file.
// ============================================================================
// Wave 2 split. Before this file the same manager-ladder list was hand-typed
// FOUR times in routes/inventoryRoutes.tsx (replenishment / audit /
// opening-stock / online-sync), and the stock-count screen had TWO
// contradicting gates: the /inventory/audit route excluded WORKSHOP_STAFF,
// while the /inventory?tab=stock-count tab rendered the SAME StockAudit
// component to WORKSHOP_STAFF inline. One screen, two answers. The tab copy
// is deleted; the route gate (the deliberate, tighter one) is the answer,
// and it lives here.
//
// ponytail: exported as arrays, not a policy engine. A role list is a list.

import type { UserRole } from '../../types';

/**
 * Who may open the Inventory module at all (the layout + every section that
 * was a tab of the old mega-page). Verbatim the old /inventory route gate -
 * WORKSHOP_STAFF included, they look up stock for jobs.
 */
export const INVENTORY_MODULE_ROLES: UserRole[] = [
  'SUPERADMIN',
  'ADMIN',
  'AREA_MANAGER',
  'STORE_MANAGER',
  'CATALOG_MANAGER',
  'WORKSHOP_STAFF',
];

/**
 * The manager ladder for stock-changing surfaces: replenishment, the stock
 * count (blind day-end audit), the opening-stock importer and online sync.
 * Verbatim the list that was typed four times in the old route file.
 * NO WORKSHOP_STAFF - counting and importing stock is not a workshop job.
 */
export const INVENTORY_MANAGE_ROLES: UserRole[] = [
  'SUPERADMIN',
  'ADMIN',
  'AREA_MANAGER',
  'STORE_MANAGER',
  'CATALOG_MANAGER',
];

/**
 * The lens power grid: the manage ladder plus OPTOMETRIST, who reads it to
 * see which powers are stocked before promising a delivery date.
 * Verbatim the old /inventory/power-grid route gate.
 */
export const POWER_GRID_ROLES: UserRole[] = [
  ...INVENTORY_MANAGE_ROLES,
  'OPTOMETRIST',
];

/** Nav visibility for the two sections that live behind tighter gates. */
export function canManageInventory(roles: readonly string[] | undefined | null): boolean {
  if (!roles) return false;
  return roles.some((r) => (INVENTORY_MANAGE_ROLES as readonly string[]).includes(r));
}
