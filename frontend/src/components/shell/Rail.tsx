// 64px left rail — dark chrome, vertical nav, hover tooltips, avatar at bottom.
// Replaces the old module-aware sidebar with a flat top-level nav.
// Ported from design_handoff_ims_2_0/shell/shell.jsx → Rail

import { NavLink, useLocation } from 'react-router-dom';
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { useAuth } from '../../context/AuthContext';
import { useAppearance } from '../../context/AppearanceContext';
import { moduleForPath } from '../../context/ModuleContext';
import { Icon, type IconName } from './Icon';
import type { UserRole } from '../../types';
import { ecommerceSsoApi } from '../../services/api/ecommerceSso';
import { getBrandAssets } from '../../utils/brandAssets';

const COLLAPSED_GROUPS_KEY = 'ims_rail_collapsed_groups';

/** Load saved collapsed-group state from localStorage. Returns null when
 *  no key is present so the caller can apply the closed-by-default rule
 *  (all titled groups collapsed) on first visit. An empty array stored
 *  in localStorage is treated as a real user choice — all expanded. */
function loadCollapsedGroups(): Set<string> | null {
  try {
    const raw = localStorage.getItem(COLLAPSED_GROUPS_KEY);
    if (raw === null) return null;
    const parsed = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.filter((x) => typeof x === 'string') : []);
  } catch {
    return null;
  }
}

function saveCollapsedGroups(set: Set<string>): void {
  try {
    localStorage.setItem(COLLAPSED_GROUPS_KEY, JSON.stringify(Array.from(set)));
  } catch {
    /* localStorage full or disabled — collapse state stays in-memory only */
  }
}

interface NavItem {
  id: string;
  label: string;
  to: string;
  icon: IconName;
  requireRoles?: UserRole[]; // if set, only visible to users holding one of these roles
  external?: boolean; // render as <a target=_blank> instead of an in-app route
  sso?: boolean; // external app reached via an SSO handoff (mint token, then open)
}

interface NavGroup {
  /** Section title rendered only in expanded mode. Omit for the first
   *  group so the menu starts flush with the brand wordmark. */
  title?: string;
  items: NavItem[];
}

// Rail groups — section titles render only in expanded mode; thin
// dividers render between groups in collapsed mode for orientation.
const RAIL_GROUPS: NavGroup[] = [
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
      { id: 'pos', label: 'POS', to: '/pos', icon: 'cart', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF'] },
      { id: 'customers', label: 'Customers', to: '/customers', icon: 'users', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'OPTOMETRIST', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF'] },
      // F39: NBA daily call list — ranked customers to phone today (in-app only).
      { id: 'daily-calls', label: 'Daily Calls', to: '/customers/nba', icon: 'phone', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_STAFF', 'SALES_CASHIER'] },
      { id: 'walkouts', label: 'Walkouts', to: '/walkouts', icon: 'user', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'SALES_CASHIER', 'CASHIER'] },
      { id: 'orders', label: 'Orders', to: '/orders', icon: 'receipt', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF', 'OPTOMETRIST', 'WORKSHOP_STAFF'] },
      { id: 'estimates', label: 'Estimates', to: '/estimates', icon: 'file', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'SALES_CASHIER', 'SALES_STAFF'] },
      { id: 'returns', label: 'Returns', to: '/returns', icon: 'refresh', requireRoles: ['SUPERADMIN', 'ADMIN', 'STORE_MANAGER', 'CASHIER', 'SALES_CASHIER'] },
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
      { id: 'attendance', label: 'Attendance', to: '/attendance', icon: 'calendarCheck', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'OPTOMETRIST', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF', 'WORKSHOP_STAFF'] },
      { id: 'hr', label: 'HR', to: '/hr', icon: 'user', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'salary-setup', label: 'Salary Setup', to: '/hr/salary-setup', icon: 'payslip', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT'] },
      { id: 'payroll-run', label: 'Payroll Run', to: '/hr/payroll-run', icon: 'calculator', requireRoles: ['SUPERADMIN', 'ADMIN', 'ACCOUNTANT'] },
      { id: 'incentive', label: 'Incentive', to: '/incentive', icon: 'zap', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_STAFF', 'SALES_CASHIER', 'CASHIER'] },
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
      { id: 'blind-eod', label: 'Blind EOD Tally', to: '/finance/blind-eod', icon: 'lock', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'SALES_CASHIER', 'CASHIER', 'SALES_STAFF'] },
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
      { id: 'print', label: 'Print', to: '/print', icon: 'printer', requireRoles: ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER', 'ACCOUNTANT', 'CASHIER', 'SALES_CASHIER', 'SALES_STAFF'] },
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

function hasAnyRole(userRoles: readonly UserRole[] | undefined, required: UserRole[]): boolean {
  if (!userRoles || userRoles.length === 0) return false;
  return required.some((r) => userRoles.includes(r));
}

export function Rail({ brand = 'bv', mobileOpen = false }: { brand?: 'bv' | 'wizopt'; mobileOpen?: boolean }) {
  const { user, hasModuleAccess, logout } = useAuth();
  const { railExpanded, toggleRailExpanded } = useAppearance();
  // User menu (sign out) anchored on the bottom rail avatar.
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement | null>(null);
  const railRef = useRef<HTMLElement | null>(null);
  // Collapsed-mode tooltips are position:fixed (so they escape the .rail-nav
  // scroll-clip box). Fixed positioning can't read the hovered item's location
  // from CSS, so on hover we publish the item's rect to --tip-x/--tip-y on the
  // rail; the .rail-label reads them. Only meaningful in icon-only mode.
  const positionTooltip = useCallback((e: ReactMouseEvent<HTMLElement>) => {
    const rail = railRef.current;
    if (!rail || rail.classList.contains('expanded')) return;
    const r = e.currentTarget.getBoundingClientRect();
    rail.style.setProperty('--tip-x', `${Math.round(r.right + 10)}px`);
    rail.style.setProperty('--tip-y', `${Math.round(r.top + r.height / 2)}px`);
  }, []);
  useEffect(() => {
    if (!userMenuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [userMenuOpen]);
  const handleSignOut = async () => {
    setUserMenuOpen(false);
    try { await logout(); } finally { window.location.assign('/login'); }
  };
  // In the mobile drawer, always render the expanded layout (group headers +
  // labels) regardless of the desktop icon-only collapse state.
  const expanded = railExpanded || mobileOpen;
  const { pathname } = useLocation();
  const userRoles = user?.roles;
  const activeRole = user?.activeRole;

  // Filter hidden items based on role AND the per-user deny-only module
  // override. The role check is the ceiling; hasModuleAccess can only further
  // hide an item whose route belongs to a denied module (moduleForPath maps the
  // item's `to` to its canonical module key; external/SSO URLs and ungated
  // paths -> null -> never hidden by the module gate). ProtectedRoute enforces
  // the same gate at the route level so a direct URL is blocked too.
  const visibleGroups = useMemo(() => {
    return RAIL_GROUPS.map((group) => ({
      ...group,
      items: group.items.filter((item) => {
        const roleOk =
          !item.requireRoles ||
          // Check both stored roles[] and active role (covers role-switching)
          hasAnyRole(userRoles, item.requireRoles) ||
          (activeRole && item.requireRoles.includes(activeRole));
        if (!roleOk) return false;
        const mod = item.external ? null : moduleForPath(item.to);
        return !mod || hasModuleAccess(mod);
      }),
    })).filter((group) => group.items.length > 0);
  }, [userRoles, activeRole, hasModuleAccess]);

  // Which group titles are collapsed. Untitled (Hub) groups can't collapse.
  // Persisted to localStorage so a user's collapse choices survive refresh
  // within a session. The group containing the active route is force-expanded
  // on every navigation so the user is never one click away from an invisible
  // nav item.
  //
  // Default (no localStorage key) = ALL titled groups COLLAPSED, so the rail is
  // short immediately on login (the admin/superadmin rail lists ~50 items).
  // AuthContext clears the key on every login, so each login starts collapsed;
  // a user's manual expands are written to localStorage and survive refresh
  // until the next login. The active-route group is force-expanded below.
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => {
    const saved = loadCollapsedGroups();
    if (saved !== null) return saved;
    const allTitled = new Set<string>();
    for (const g of RAIL_GROUPS) {
      if (g.title) allTitled.add(g.title);
    }
    return allTitled;
  });

  const activeGroupTitle = useMemo(() => {
    for (const g of visibleGroups) {
      if (!g.title) continue;
      const match = g.items.some((i) => pathname === i.to || pathname.startsWith(i.to + '/'));
      if (match) return g.title;
    }
    return null;
  }, [pathname, visibleGroups]);

  useEffect(() => {
    if (!activeGroupTitle) return;
    setCollapsedGroups((prev) => {
      if (!prev.has(activeGroupTitle)) return prev;
      const next = new Set(prev);
      next.delete(activeGroupTitle);
      saveCollapsedGroups(next);
      return next;
    });
  }, [activeGroupTitle]);

  // Groups now start COLLAPSED by default (see the collapsedGroups initializer),
  // so the old 15s "auto-collapse after mount" timer was removed — the rail is
  // already compact on login and a timer would only risk clobbering a user's
  // manual expand.

  const toggleGroup = useCallback((title: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      saveCollapsedGroups(next);
      return next;
    });
  }, []);

  const brandAssets = getBrandAssets(brand);
  const wordmark = brandAssets.name;
  const userInitials = (user?.name ?? '')
    .split(/\s+/)
    .map((s) => s[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase() || '?';

  return (
    <aside
      ref={railRef}
      id="rail-drawer"
      className={'rail' + (expanded ? ' expanded' : '') + (mobileOpen ? ' rail-mobile-open' : '')}
      {...(mobileOpen ? { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Navigation menu' } : {})}
    >
      {/* Header row — brand glyph + wordmark (expanded only) + toggle.
          Toggle moved up here so it's discoverable above the fold; the
          old position at the bottom was easy to miss. */}
      <div className="rail-header">
        <div className="rail-brand-row">
          {/* Real brand mark — white knockout so it reads on the dark rail */}
          <div className="brand" title={wordmark}>
            <img
              src={brandAssets.markWhite}
              alt={wordmark}
              width={28}
              height={28}
              style={{ objectFit: 'contain', display: 'block' }}
            />
          </div>
          {expanded && (
            <span className="rail-wordmark" aria-hidden="true">{wordmark}</span>
          )}
        </div>
        <button
          type="button"
          className="rail-toggle"
          onClick={toggleRailExpanded}
          title={railExpanded ? 'Collapse — show icons only' : 'Expand — show icons + labels'}
          aria-label={railExpanded ? 'Collapse sidebar' : 'Expand sidebar'}
        >
          <ChevronIcon flipped={railExpanded} />
        </button>
      </div>

      {/* Scrollable nav list. The scroll lives HERE (not on .rail) so the rail
          itself keeps overflow: visible and the hover tooltips (.rail-label)
          aren't clipped in collapsed icon-only mode. The brand header above and
          the avatar/user-menu below stay pinned outside this scroll region. */}
      <div className="rail-nav">
      {visibleGroups.map((group, gi) => {
        const isCollapsible = expanded && !!group.title;
        const isCollapsed = isCollapsible && collapsedGroups.has(group.title!);
        const itemsHidden = isCollapsed;
        return (
          <div key={gi} className="rail-group">
            {isCollapsible && (
              <button
                type="button"
                className={'rail-group-title' + (isCollapsed ? ' collapsed' : '')}
                onClick={() => toggleGroup(group.title!)}
                aria-expanded={!isCollapsed}
                aria-controls={`rail-group-${gi}-items`}
              >
                <span className="rail-group-title-label">{group.title}</span>
                <GroupChevron expanded={!isCollapsed} />
              </button>
            )}
            <div id={`rail-group-${gi}-items`} hidden={itemsHidden}>
              {group.items.map((item) => {
                const IconCmp = Icon[item.icon];
                if (item.external) {
                  if (item.sso) {
                    // SSO handoff: mint a short-lived token, then open the
                    // external app already logged in. Falls back to a plain
                    // open if SSO isn't configured / the user isn't allowed.
                    return (
                      <button
                        key={item.id}
                        type="button"
                        className="rail-item"
                        title={`${item.label} (opens in new tab)`}
                        onMouseEnter={positionTooltip}
                        onClick={async () => {
                          try {
                            const r = await ecommerceSsoApi.getUrl();
                            window.open(r.url, '_blank', 'noopener,noreferrer');
                          } catch {
                            window.open(item.to, '_blank', 'noopener,noreferrer');
                          }
                        }}
                      >
                        <IconCmp />
                        <span className="rail-label">{item.label}</span>
                      </button>
                    );
                  }
                  // External app (e.g. the e-commerce admin) — open in a new tab.
                  return (
                    <a
                      key={item.id}
                      href={item.to}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rail-item"
                      title={`${item.label} (opens in new tab)`}
                      onMouseEnter={positionTooltip}
                    >
                      <IconCmp />
                      <span className="rail-label">{item.label}</span>
                    </a>
                  );
                }
                return (
                  <NavLink
                    key={item.id}
                    to={item.to}
                    className={({ isActive }) => 'rail-item' + (isActive ? ' active' : '')}
                    title={item.label}
                    onMouseEnter={positionTooltip}
                  >
                    <IconCmp />
                    <span className="rail-label">{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
            {gi < visibleGroups.length - 1 && !expanded && <div className="rail-sep" />}
          </div>
        );
      })}
      <div className="rail-spacer" />
      </div>
      <div className="rail-usermenu" ref={userMenuRef}>
        {userMenuOpen && (
          <div className="rail-usermenu-pop" role="menu">
            <div className="rail-usermenu-head">
              <span className="rail-usermenu-name">{user?.name || 'User'}</span>
              <span className="rail-usermenu-role">{(activeRole || '').toString().replaceAll('_', ' ')}</span>
            </div>
            <button
              type="button"
              className="rail-usermenu-signout"
              role="menuitem"
              onClick={handleSignOut}
            >
              <svg viewBox="0 0 24 24" width={16} height={16} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
              Sign out
            </button>
          </div>
        )}
        <button
          type="button"
          className="rail-avatar rail-avatar-btn"
          aria-haspopup="menu"
          aria-expanded={userMenuOpen}
          title={user?.name ? `${user.name} • ${activeRole} — click to sign out` : 'Account'}
          onClick={() => setUserMenuOpen((o) => !o)}
        >
          <span className="rail-avatar-initials">{userInitials}</span>
          {expanded && (
            <span className="rail-avatar-name">
              {user?.name?.split(' ')[0] || 'User'}
            </span>
          )}
          {expanded && (
            <svg className="rail-avatar-caret" viewBox="0 0 24 24" width={14} height={14} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="18 15 12 9 6 15" />
            </svg>
          )}
        </button>
      </div>
    </aside>
  );
}

function ChevronIcon({ flipped }: { flipped: boolean }) {
  // Right-pointing chevron when collapsed (=> expand). Left when expanded.
  return (
    <svg
      viewBox="0 0 24 24" width={20} height={20}
      fill="none" stroke="currentColor" strokeWidth={1.6}
      strokeLinecap="round" strokeLinejoin="round"
      style={{ transform: flipped ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }}
    >
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

function GroupChevron({ expanded }: { expanded: boolean }) {
  // Down chevron when expanded (group is open), right chevron when collapsed.
  return (
    <svg
      viewBox="0 0 24 24" width={12} height={12}
      fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round"
      style={{ transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform .15s', flexShrink: 0 }}
      aria-hidden="true"
    >
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}
