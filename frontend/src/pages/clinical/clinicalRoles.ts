// ============================================================================
// IMS 2.0 - Clinical: WHO SEES WHAT. One list, one file.
// ============================================================================
// Wave 2 split of ClinicalPage. Before this file the answer was written down
// SIX times (the route gate in clinicalRoutes plus canStartTest /
// canAddPatient / canViewAbuseAlerts / canViewPrescriptions /
// canViewConversion inside ClinicalPage.tsx) and the copies disagreed with the
// route gate that actually decided who got in:
//
//   - canViewPrescriptions and canViewConversion listed AREA_MANAGER, but the
//     /clinical route gate did not, so an AREA_MANAGER never reached those
//     tabs. Dead grant.
//   - canAddPatient listed SALES_STAFF - dead for the same reason.
//
// This refactor is ZERO behaviour change, so the lists below are the EFFECTIVE
// access of today: each in-page list INTERSECTED with the route gate. The two
// dead grants are documented here rather than silently revived or silently
// dropped - widening access for AREA_MANAGER or SALES_STAFF is an owner
// decision, and when made it is one line, in this file only.
//
// ponytail: exported as arrays, not a policy engine. A role list is a list.

import type { UserRole } from '../../types';

/**
 * Who may open the Clinical module at all: queue intake, start/continue an eye
 * test, completed-today, prescriptions, conversion, test history, family Rx
 * and contact-lens fitting. Identical to the pre-split /clinical route gate.
 */
export const CLINICAL_MODULE_ROLES: UserRole[] = [
  'SUPERADMIN',
  'ADMIN',
  'STORE_MANAGER',
  'OPTOMETRIST',
];

/**
 * Abuse alerts: management only. An OPTOMETRIST must not browse the abuse
 * screen - they can be a subject of it (redo/discount abuse patterns).
 * Matches the pre-split canViewAbuseAlerts list exactly.
 */
export const CLINICAL_MANAGER_ROLES: UserRole[] = [
  'SUPERADMIN',
  'ADMIN',
  'STORE_MANAGER',
];

/** The runtime half of the same ruling, for section-nav visibility. */
export function canSeeClinicalAbuseAlerts(
  roles: readonly string[] | undefined | null,
): boolean {
  if (!roles) return false;
  return roles.some((r) => (CLINICAL_MANAGER_ROLES as readonly string[]).includes(r));
}
