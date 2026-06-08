// ============================================================================
// F35 - Cost & margin masking (#35), frontend presentational guards.
// Cost / margin cells render their value ONLY for cost-visible roles
// (SUPERADMIN / ADMIN / ACCOUNTANT). Every other role sees a restrained "-"
// (no badge, no lock icon -- do not draw attention to the masking).
// The backend already strips the field for unauthorised roles; these cells are
// the matching client-side render of null/absent values + a defensive guard.
// ============================================================================

import { useAuth } from '../../context/AuthContext';
import type { UserRole } from '../../types';

const COST_VISIBLE: UserRole[] = ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'];

function Dash() {
  return <span className="text-gray-400 select-none" aria-label="not authorised">-</span>;
}

/** Renders a rupee cost value, masked to "-" for non-cost-visible roles. */
export function CostCell({ value }: { value: number | null | undefined }) {
  const { hasRole } = useAuth();
  if (!hasRole(COST_VISIBLE)) return <Dash />;
  if (value === null || value === undefined) return <Dash />;
  return <span className="font-mono">₹{value.toLocaleString('en-IN')}</span>;
}

/** Renders a margin percentage, masked to "-" for non-cost-visible roles. */
export function MarginCell({ value }: { value: number | null | undefined }) {
  const { hasRole } = useAuth();
  if (!hasRole(COST_VISIBLE)) return <Dash />;
  if (value === null || value === undefined) return <Dash />;
  return <span className="font-mono">{value.toFixed(1)}%</span>;
}

export default CostCell;
