// ============================================================================
// F35 - Cost masking (#35), frontend presentational guard.
// A per-unit product cost renders ONLY for COST_ROLES; every other role sees a
// restrained "-" (no badge, no lock icon -- do not draw attention to the
// masking). The backend already strips the field for unauthorised roles; this
// cell is the matching client-side render of null/absent values + a defensive
// guard.
// ============================================================================

import { useAuth } from '../../context/AuthContext';
import type { UserRole } from '../../types';

/** Who sees EVERYTHING WE PAY (owner ruling 2026-10-12): product cost, landed
 *  cost, margin, stock value at cost, purchase prices (PO lines and totals,
 *  receiving, last price paid, cost at acceptance, vendor returns) and supplier
 *  payments -- the owner and the accounts roles only. Area, store and catalogue
 *  managers see MRP and selling price, never cost; counter staff never.
 *  The ONE list every screen reads. It IS the backend's cost_mask
 *  COST_VISIBLE_ROLES ("product", "purchase" and "payables" contexts); a
 *  backend test (test_what_we_pay_accounts_only) holds the two sets equal. */
export const COST_ROLES: UserRole[] = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'];

function Dash() {
  return <span className="text-gray-400 select-none" aria-label="not authorised">-</span>;
}

/** Renders a rupee cost value, masked to "-" outside COST_ROLES. */
export function CostCell({ value }: { value: number | null | undefined }) {
  const { hasRole } = useAuth();
  if (!hasRole(COST_ROLES)) return <Dash />;
  if (value === null || value === undefined) return <Dash />;
  return <span className="font-mono">₹{value.toLocaleString('en-IN')}</span>;
}

export default CostCell;
