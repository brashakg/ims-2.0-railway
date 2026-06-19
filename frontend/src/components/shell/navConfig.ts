// Shared navigation model for the app shell.
// ---------------------------------------------------------------------------
// Single source of truth for the nav GROUPS + per-item role gating, consumed by
// BOTH shells:
//   - TopNav.tsx  — the tablet+desktop top horizontal menu (groups -> dropdowns)
//   - Rail.tsx    — the phone bottom tab bar + hamburger drawer (legacy rail)
// Keeping the data here means the two never drift: the same items, the same
// requireRoles, the same module gating in both places.

import { moduleForPath } from '../../context/ModuleContext';
import type { IconName } from './Icon';
import type { UserRole } from '../../types';

export interface NavItem {
  id: string;
  label: string;
  to: string;
  icon: IconName;
  requireRoles?: UserRole[]; // if set, only visible to users holding one of these roles
  external?: boolean; // render as <a target=_blank> instead of an in-app route
  sso?: boolean; // external app reached via an SSO handoff (mint token, then open)
}

export interface NavGroup {
  /** Section title rendered as the top-level menu item / rail group header.
   *  Omit for the first group (Hub / Notifications) so those render as direct
   *  top-level links rather than a dropdown. */
  title?: string;
  items: NavItem[];
}

// Nav groups — each titled group becomes a top-level dropdown in the top menu
// (and a collapsible section in the phone drawer). The first (untitled) group's
// items render as direct top-level links.
export const NAV_GROUPS: NavGroup[] = [
  {
    items: [
      { id: 'hub', label: 'Hub', to: '/dashboard', icon: 'home' },
      { id: 'notifications', label: 'Notifications', to: '/notifications', icon: 'bell' },
    ],
  },
  {
    title: 'Sales floor',
    // requireRoles on each item MIRRORS the route's ProtectedRoute allowedRoles
    // in App.tsx, so a role never sees a nav link that lands it on /unauthorized.
    items: [
      { id: 'pos', label: 'POS', to: '/pos', icon: 'cart', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF'] },
      { id: 'customers', label: 'Customers', to: '/customers', icon: 'users', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF'] },
      // F39: NBA daily call list — ranked customers to phone today (in-app only).
      { id: 'daily-calls', label: 'Daily Calls', to: '/customers/nba', icon: 'phone', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_STAFF'] },
      { id: 'walkouts', label: 'Walkouts', to: '/walkouts', icon: 'user', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'CASHIER'] },
      { id: 'orders', label: 'Orders', to: '/orders', icon: 'receipt', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CASHIER', 'SALES_STAFF', 'OPTOMETRIST', 'WORKSHOP_STAFF'] },
      { id: 'estimates', label: 'Estimates', to: '/estimates', icon: 'file', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_STAFF'] },
      { id: 'returns', label: 'Returns', to: '/returns', icon: 'refresh', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_STAFF'] },
      // F27: refund-approval queue (the refund-only slice of the E4 inbox).
      // requireRoles mirrors the /returns/approvals ProtectedRoute gate.
      { id: 'refund-approvals', label: 'Refund Approvals', to: '/returns/approvals', icon: 'shield', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
    ],
  },
  {
    title: 'Clinical',
    items: [
      { id: 'clinical', label: 'Clinical', to: '/clinical', icon: 'eye', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST'] },
    ],
  },
  {
    title: 'Stock & supply',
    items: [
      { id: 'inventory', label: 'Inventory', to: '/inventory', icon: 'box', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF'] },
      { id: 'power-grid', label: 'Power Grid', to: '/inventory/power-grid', icon: 'box', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER', 'OPTOMETRIST'] },
      { id: 'online-stock', label: 'Online Stock', to: '/inventory/online-sync', icon: 'box', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER'] },
      { id: 'purchase', label: 'Purchase', to: '/purchase', icon: 'truck', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'grn-cockpit', label: 'Receive Goods', to: '/purchase/receive', icon: 'truck', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'] },
      { id: 'vendor-returns', label: 'Vendor Returns', to: '/purchase/vendor-returns', icon: 'refresh', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'WORKSHOP_STAFF'] },
      { id: 'workshop', label: 'Workshop', to: '/workshop', icon: 'wrench', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'WORKSHOP_STAFF'] },
      { id: 'catalog', label: 'Catalog', to: '/catalog/add', icon: 'tag', requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
      { id: 'catalog-autopilot', label: 'Catalog Autopilot', to: '/catalog/autopilot', icon: 'cpu', requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
      { id: 'pricing', label: 'Pricing & Offers', to: '/catalog/pricing', icon: 'coins', requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER'] },
    ],
  },
  {
    title: 'Operations',
    items: [
      { id: 'tasks', label: 'Tasks & SOPs', to: '/tasks', icon: 'check', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      // E4: PIN-gated approval inbox. requireRoles mirrors the /approvals
      // ProtectedRoute gate (the approver set; ACCOUNTANT is inbox read-only).
      { id: 'approvals', label: 'Approvals', to: '/approvals', icon: 'shield', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'expenses', label: 'Expenses', to: '/finance/expenses', icon: 'wallet' },
      // Attendance is its OWN top-level item (was buried in HR tabs). Managers
      // see the full monthly grid + admin edit; staff (roles 5-7) get their
      // self check-in card. requireRoles mirrors the /attendance route gate.
      { id: 'attendance', label: 'Attendance', to: '/attendance', icon: 'calendarCheck', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'OPTOMETRIST', 'CASHIER', 'SALES_STAFF', 'WORKSHOP_STAFF'] },
      { id: 'hr', label: 'HR', to: '/hr', icon: 'user', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'salary-setup', label: 'Salary Setup', to: '/hr/salary-setup', icon: 'payslip', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'payroll-run', label: 'Payroll Run', to: '/hr/payroll-run', icon: 'calculator', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
      { id: 'incentive', label: 'Incentive', to: '/incentive', icon: 'zap', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'CASHIER'] },
    ],
  },
  {
    // Till / Day-close — cashier-facing till tools. These were mis-filed under
    // "Analysis" with the accountant finance reports; the nav home follows the
    // operator's mental model (a cashier's daily open/close), not the code
    // package (URLs stay /finance/*). requireRoles are unchanged from before.
    title: 'Till / Day-close',
    items: [
      { id: 'cash-register', label: 'Cash Register', to: '/finance/cash-register', icon: 'cashRegister', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'blind-eod', label: 'Blind EOD Tally', to: '/finance/blind-eod', icon: 'lock', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'CASHIER', 'SALES_STAFF'] },
      // #7 Manager-facing reconciliation console across BOTH day-close flows.
      { id: 'cash-reconciliation', label: 'Cash Reconciliation', to: '/finance/cash-reconciliation', icon: 'calculator', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
    ],
  },
  {
    title: 'Analysis',
    items: [
      { id: 'reports', label: 'Reports', to: '/reports', icon: 'chart', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'finance', label: 'Finance', to: '/finance/dashboard', icon: 'banknote', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      // cash-register + blind-eod re-homed to the Operations "Till / Day-close"
      // group below (cashier tools, not accountant analysis). URLs + roles unchanged.
      { id: 'cashflow', label: 'Cash Flow', to: '/finance/cash-flow', icon: 'trendingUp', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
      { id: 'itc', label: 'GST Credit (ITC)', to: '/finance/itc', icon: 'percent', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
      // B2B invoices -> Tally: e-invoice + e-way bill issued in Tally (owner
      // decision). Export console + reminder worklist; finance-admin only.
      { id: 'b2b-tally-export', label: 'B2B → Tally Export', to: '/finance/b2b-tally-export', icon: 'receipt', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
      { id: 'b2b-tally-worklist', label: 'B2B Tally Worklist', to: '/finance/b2b-tally-worklist', icon: 'clipboard', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
      // Purchase S6: Accountant reconciliation console (4 tick flags + 4 worklists)
      { id: 'recon-console', label: 'Recon Console', to: '/purchase/recon-console', icon: 'check', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
    ],
  },
  {
    title: 'Growth',
    items: [
      { id: 'marketing', label: 'Marketing', to: '/customers/campaigns', icon: 'megaphone', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
      // F11/F12: Promotions rules admin + Offer Tally report. The live POS apply
      // is dark behind PROMO_ENGINE_ENABLED; rules are authored/previewed here.
      { id: 'promotions', label: 'Promotions', to: '/promotions', icon: 'tag', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CATALOG_MANAGER'] },
      { id: 'promotions-report', label: 'Offer Tally', to: '/reports/promotions', icon: 'chart', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      // F40: VIP churn watchlist -- overdue high-LTV customers (personalised
      // buying rhythm). Read-only retention oversight; SUPERADMIN / ADMIN only.
      { id: 'vip-churn-watchlist', label: 'VIP Watch List', to: '/customers/vip-churn-watchlist', icon: 'users', requireRoles: ['SUPERADMIN', 'ADMIN'] },
      // CRM-14: WhatsApp Inbox -- inbound customer messages via Meta Business API.
      { id: 'whatsapp-inbox', label: 'WA Inbox', to: '/customers/whatsapp-inbox', icon: 'chat', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER'] },
      // In-app Online Store module (BVI merge). Replaces the old external SSO
      // link to uniparallel.com; the storefront admin remains reachable from a
      // button inside the module page during the strangler-fig transition.
      { id: 'online-store', label: 'Online Store', to: '/online-store', icon: 'store', requireRoles: ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER'] },
      // CRM-16: Ad Performance (Google + Meta agency oversight). Finance-sensitive
      // spend data -- restricted to SUPERADMIN / ADMIN only.
      { id: 'ad-performance', label: 'Ad Performance', to: '/marketing/ad-performance', icon: 'chart', requireRoles: ['SUPERADMIN', 'ADMIN'] },
    ],
  },
  {
    // AI features (JARVIS + the 8 superhero agents live behind /jarvis).
    // SUPERADMIN-only, so the whole group only renders for superadmins.
    title: 'AI',
    items: [
      { id: 'jarvis', label: 'Jarvis', to: '/jarvis', icon: 'cpu', requireRoles: ['SUPERADMIN'] },
    ],
  },
  {
    // Audit trail / oversight — its OWN group, separate from AI. SUPERADMIN-only,
    // so the whole group only renders for superadmins.
    title: 'Audit',
    items: [
      { id: 'activity-log', label: 'Activity Log', to: '/admin/activity-log', icon: 'shield', requireRoles: ['SUPERADMIN'] },
    ],
  },
  {
    title: 'System',
    items: [
      { id: 'print', label: 'Print', to: '/print', icon: 'printer', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'CASHIER', 'SALES_STAFF'] },
      // Three items used to share the `settings` cog glyph (indistinguishable).
      // Now: Settings keeps the cog; Staff Onboarding gets a user-plus mark and
      // sits next to the people-admin tools; Organization gets a building mark.
      // The /setup wizard is relabeled "Staff Onboarding" (it onboards staff via
      // create_user) — route `to:` is unchanged.
      { id: 'onboarding', label: 'Staff Onboarding', to: '/setup', icon: 'userPlus', requireRoles: ['SUPERADMIN', 'ADMIN'] },
      { id: 'organization', label: 'Organization', to: '/organization', icon: 'building', requireRoles: ['SUPERADMIN', 'ADMIN'] },
      { id: 'setup', label: 'Settings', to: '/settings', icon: 'settings', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'AREA_MANAGER', 'CATALOG_MANAGER', 'ACCOUNTANT'] },
    ],
  },
];

export function hasAnyRole(userRoles: readonly UserRole[] | undefined, required: UserRole[]): boolean {
  if (!userRoles || userRoles.length === 0) return false;
  return required.some((r) => userRoles.includes(r));
}

/**
 * Filter the nav model down to what THIS user may see. Two gates, identical to
 * what ProtectedRoute enforces at the route level:
 *   1. role ceiling — `requireRoles` checked against the stored roles[] AND the
 *      active role (covers role-switching);
 *   2. per-user deny-only module override — an item whose route maps to a denied
 *      module is hidden (external/SSO + ungated paths map to null -> never hidden).
 * Empty groups are dropped so an all-hidden section never renders a stray header.
 */
export function filterVisibleGroups(
  userRoles: readonly UserRole[] | undefined,
  activeRole: UserRole | null | undefined,
  hasModuleAccess: (moduleKey: string) => boolean,
): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => {
      const roleOk =
        !item.requireRoles ||
        hasAnyRole(userRoles, item.requireRoles) ||
        (activeRole != null && item.requireRoles.includes(activeRole));
      if (!roleOk) return false;
      const mod = item.external ? null : moduleForPath(item.to);
      return !mod || hasModuleAccess(mod);
    }),
  })).filter((group) => group.items.length > 0);
}
