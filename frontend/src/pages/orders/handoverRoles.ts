/**
 * Roles permitted to CLOSE A HANDOVER (mark an order ready / delivered).
 *
 * MUST mirror `HANDOVER_ROLES` in backend/api/routers/orders.py. The backend is
 * the authority; this list exists only so the counter is never shown a green
 * button that 403s in front of a customer.
 *
 * The backend set is derived from the scan-role tuples (labels.SCAN_ROLES /
 * workshop._LAB_SCAN_ROLES) on the rule "whoever may scan a job to DELIVERED at
 * pickup must be able to close the order". OPTOMETRIST is in NEITHER scan tuple,
 * so it is deliberately absent here too -- rather than widen a clinical role's
 * transactional reach without an owner decision, the button is hidden for it.
 */
export const HANDOVER_ROLES = [
  'SUPERADMIN',
  'ADMIN',
  'AREA_MANAGER',
  'STORE_MANAGER',
  'SALES_CASHIER',
  'SALES_STAFF',
  'CASHIER',
  'WORKSHOP_STAFF',
] as const;

/**
 * True when the signed-in user may close a handover. Reads the full role set
 * with a fallback to activeRole, so a dual-role user is not wrongly blocked.
 */
export const canCloseHandover = (user: {
  roles?: string[] | null;
  activeRole?: string | null;
} | null | undefined): boolean => {
  if (!user) return false;
  const held = new Set<string>([
    ...((user.roles as string[] | undefined) ?? []),
    ...(user.activeRole ? [user.activeRole] : []),
  ]);
  return HANDOVER_ROLES.some((r) => held.has(r));
};
