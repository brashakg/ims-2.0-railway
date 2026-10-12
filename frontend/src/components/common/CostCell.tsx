// ============================================================================
// F35 - Cost masking (#35), frontend presentational guard.
// A per-unit product cost renders ONLY for PRODUCT_COST_ROLES; every other role
// sees a restrained "-" (no badge, no lock icon -- do not draw attention to the
// masking). The backend already strips the field for unauthorised roles; this
// cell is the matching client-side render of null/absent values + a defensive
// guard.
// ============================================================================

import { useAuth } from '../../context/AuthContext';
import type { UserRole } from '../../types';

/** Who sees per-unit product cost (owner ruling 2026-09-28): the managers
 *  (store, area, catalogue) and the owner / accounts roles; counter staff never.
 *  It IS the backend's cost_mask "product" context -- a backend test
 *  (test_counter_roles_no_purchase_reads) holds the two sets equal. */
export const PRODUCT_COST_ROLES: UserRole[] = [
  'SUPERADMIN',
  'ADMIN',
  'ACCOUNTANT',
  'AREA_MANAGER',
  'STORE_MANAGER',
  'CATALOG_MANAGER',
];

/** Who sees and books supplier payments -- bills, payments, balances, per
 *  vendor AND in total (owner ruling 2026-09-29): the accounts roles. It IS the
 *  backend's cost_mask "payables" context (AP_ROLES + SUPERADMIN); a backend
 *  test (test_counter_roles_no_purchase_reads) holds the two sets equal. */
export const PAYABLES_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'];

function Dash() {
  return <span className="text-gray-400 select-none" aria-label="not authorised">-</span>;
}

/** Renders a rupee cost value, masked to "-" outside PRODUCT_COST_ROLES. */
export function CostCell({ value }: { value: number | null | undefined }) {
  const { hasRole } = useAuth();
  if (!hasRole(PRODUCT_COST_ROLES)) return <Dash />;
  if (value === null || value === undefined) return <Dash />;
  return <span className="font-mono">₹{value.toLocaleString('en-IN')}</span>;
}

export default CostCell;
