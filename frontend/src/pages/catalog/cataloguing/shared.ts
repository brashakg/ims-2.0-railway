// Shared by the two cataloguing pages (scorecard + QC review).

import type { UserRole } from '../../../types';

/** Who may open /catalog/scorecard and /catalog/qc: the manager ladder.
 *  One list for both route gates and both sidebar items; mirrors the backend
 *  rbac rows for /products/cataloguing-scorecard + /products/qc-samples*. */
export const CATALOGUING_MANAGER_ROLES: UserRole[] = [
  'SUPERADMIN',
  'ADMIN',
  'AREA_MANAGER',
  'STORE_MANAGER',
  'CATALOG_MANAGER',
];

/** "COLORED_CONTACT_LENS" -> "Colored contact lens" */
export function prettyCategory(cat: string): string {
  const s = String(cat || '').replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}
