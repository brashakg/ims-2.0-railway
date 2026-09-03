// ============================================================================
// IMS 2.0 - Module keys + per-user module gating
// ============================================================================
// This file used to ALSO hold MODULE_CONFIGS: an eleven-module sidebar registry
// (~90 rows, including a fifteen-item CRM sidebar) that nothing ever rendered.
// The app's one nav registry is components/shell/navConfig.ts (TopNav + Rail);
// MODULE_CONFIGS was a second registry that could only ever drift away from it,
// and had: it still linked /reports?tab=churn, /customers?tab=recalls and other
// addresses the split-out pages replaced. Deleted 2026-09-02 rather than
// "reconciled" — syncing two copies of one rule is how they drift again.
// Anything genuinely missing from the menu belongs in navConfig.ts.
//
// What survives here is the part with live callers: the canonical module keys
// and the path -> module lookup used by ProtectedRoute, navConfig, the command
// palette and the admin Module Access grid.
// ============================================================================

import type { ReactNode } from 'react';

// ============================================================================
// Canonical module keys -- the SINGLE source of truth shared by SettingsAuth
// (the admin checkboxes), the Rail nav, and ProtectedRoute. A per-user
// `module_access` map (deny-only override on top of the role) is keyed on
// EXACTLY these strings.
//
// `settings` is deliberately NOT gateable: an admin must never be able to lock
// a user (or themselves) out of User Management, which is the only place to
// undo a bad module grant. Dashboard / print / jarvis / org are likewise
// ungated (return null from moduleForPath).
// ============================================================================

export const MODULE_KEYS = [
  'pos',
  'clinic',
  'inventory',
  'customers',
  'vendors',
  'workshop',
  'hr',
  'reports',
  'finance',
  // OS-053: the Online Store module (/online-store/*) — deniable per-user like
  // every other operational module. Key mirrors VALID_MODULE_KEYS in
  // backend/api/services/user_roles.py.
  'ecommerce',
] as const;

export type ModuleKey = (typeof MODULE_KEYS)[number];

/** Admin-facing label for each gateable module key, in the order shown in the
 *  SettingsAuth "Module Access" grid. Keep keys === MODULE_KEYS so the
 *  checkboxes, the Rail, and ProtectedRoute can never drift apart. */
export const MODULE_ACCESS_OPTIONS: { key: ModuleKey; label: string }[] = [
  { key: 'pos', label: 'POS' },
  { key: 'clinic', label: 'Clinical' },
  { key: 'inventory', label: 'Inventory' },
  { key: 'customers', label: 'Customers (CRM)' },
  { key: 'vendors', label: 'Supply Chain' },
  { key: 'workshop', label: 'Workshop' },
  { key: 'hr', label: 'HR & Tasks' },
  { key: 'reports', label: 'Reports' },
  { key: 'finance', label: 'Finance' },
  { key: 'ecommerce', label: 'Online Store' },
];

// Route-prefix -> canonical module key. Ordered longest-prefix-first so a more
// specific path wins (none currently overlap ambiguously, but the lookup is
// written to be prefix-safe). Anything not matched here is ungated (null).
//
// Notes on shared paths:
//  - /orders is surfaced by POS, Workshop, and Reports but is owned by POS, so
//    denying `pos` also removes order views (acceptable: no POS => no orders).
//  - /customers/campaigns (Marketing) sits under the `customers` module, so
//    denying `customers` also hides Marketing -- correct, it's a CRM feature.
//  - /catalog* is part of the Inventory module (Add Product / pricing).
const PATH_MODULE_PREFIXES: { prefix: string; key: ModuleKey }[] = [
  { prefix: '/pos', key: 'pos' },
  { prefix: '/orders', key: 'pos' },
  { prefix: '/estimates', key: 'pos' },
  { prefix: '/returns', key: 'pos' },
  { prefix: '/walkouts', key: 'pos' },
  { prefix: '/clinical', key: 'clinic' },
  { prefix: '/prescriptions', key: 'clinic' },
  { prefix: '/inventory', key: 'inventory' },
  { prefix: '/catalog', key: 'inventory' },
  { prefix: '/customers', key: 'customers' },
  { prefix: '/purchase', key: 'vendors' },
  { prefix: '/workshop', key: 'workshop' },
  { prefix: '/hr', key: 'hr' },
  { prefix: '/tasks', key: 'hr' },
  { prefix: '/incentive', key: 'hr' },
  { prefix: '/reports', key: 'reports' },
  { prefix: '/finance', key: 'finance' },
  // OS-053: every /online-store screen belongs to the ecommerce module, so the
  // per-user deny hides the nav item (navConfig.filterVisibleGroups) AND blocks
  // direct URLs (ProtectedRoute derives the module from the path).
  { prefix: '/online-store', key: 'ecommerce' },
];

/** Resolve the canonical module key that owns `path`, or null if ungated.
 *  Strips the query string and matches by the longest route prefix so e.g.
 *  `/inventory/audit?tab=x` -> `inventory`. */
export function moduleForPath(path: string): ModuleKey | null {
  if (!path) return null;
  const clean = path.split('?')[0].split('#')[0];
  let best: { prefix: string; key: ModuleKey } | null = null;
  for (const entry of PATH_MODULE_PREFIXES) {
    if (clean === entry.prefix || clean.startsWith(entry.prefix + '/')) {
      if (!best || entry.prefix.length > best.prefix.length) best = entry;
    }
  }
  return best ? best.key : null;
}

/** Kept only because App.tsx mounts it. The provider carried the deleted
 *  sidebar registry and had no remaining consumers (nothing called useModule),
 *  so it is now a pass-through; drop the wrapper from App.tsx when that file is
 *  next in scope. */
export function ModuleProvider({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
