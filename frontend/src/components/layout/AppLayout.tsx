// ============================================================================
// IMS 2.0 — Main Application Layout
// Uses the new design-handoff Shell (64px rail + 52px topbar).
// Pages render inside <Outlet /> within the Shell's page-body.
// ============================================================================

import { Outlet, useLocation } from 'react-router-dom';
import { useEffect, useMemo } from 'react';
import { Eye } from 'lucide-react';
import { Shell, type Crumb } from '../shell';
import { useAppearance } from '../../context/AppearanceContext';
import { useAuth } from '../../context/AuthContext';
import { loadHsnRates } from '../../constants/gstRuntime';
import { ForcePasswordChange } from '../../pages/auth/ForcePasswordChange';

// Keep labels consistent with the Rail labels so the crumb matches the active item.
const SEGMENT_LABELS: Record<string, string> = {
  dashboard: 'Hub',
  pos: 'POS',
  customers: 'Customers',
  360: 'Customer 360',
  segmentation: 'Segmentation',
  loyalty: 'Loyalty',
  campaigns: 'Campaigns',
  referrals: 'Referrals',
  feedback: 'Feedback',
  'follow-ups': 'Follow-ups',
  orders: 'Orders',
  returns: 'Returns',
  clinical: 'Clinical',
  test: 'New Eye Test',
  history: 'Test History',
  'contact-lens': 'Contact Lens Fitting',
  prescriptions: 'Prescriptions',
  inventory: 'Inventory',
  replenishment: 'Replenishment',
  audit: 'Stock Audit',
  purchase: 'Purchase',
  vendors: 'Vendors',
  grn: 'GRN',
  workshop: 'Workshop',
  catalog: 'Catalog',
  add: 'Add Product',
  tasks: 'Tasks & SOPs',
  checklists: 'Checklists',
  hr: 'HR',
  payroll: 'Payroll',
  incentives: 'Incentives',
  reports: 'Reports',
  'day-end': 'Day-End',
  outstanding: 'Outstanding',
  finance: 'Finance',
  expenses: 'Expenses',
  settings: 'Store Setup',
  setup: 'Store Setup',
  jarvis: 'Jarvis',
  executive: 'Executive',
  analytics: 'Analytics',
  footfall: 'Footfall',
  print: 'Print',
};

function pathToCrumbs(pathname: string): Crumb[] {
  const parts = pathname.split('/').filter(Boolean);
  if (parts.length === 0) return [{ label: 'Hub' }];
  const crumbs: Crumb[] = [];
  let acc = '';
  parts.forEach((p, i) => {
    acc += '/' + p;
    const label = SEGMENT_LABELS[p] ?? p.replaceAll('-', ' ');
    const capitalized = label.charAt(0).toUpperCase() + label.slice(1);
    crumbs.push({
      label: capitalized,
      to: i < parts.length - 1 ? acc : undefined,
    });
  });
  return crumbs;
}

export function AppLayout() {
  const location = useLocation();
  const { brand } = useAppearance();
  const { isReadOnly, user } = useAuth();

  const crumbs = useMemo(() => pathToCrumbs(location.pathname), [location.pathname]);

  // Load the editable HSN->GST master once per session so the POS preview +
  // invoice reflect the same (SUPERADMIN-edited) rates the backend bills from.
  // Fail-soft: resolveGstRate() falls back to static GST 2.0 constants.
  useEffect(() => { loadHsnRates(); }, []);

  // Force-change-on-first-login gate: an admin-created / password-reset user
  // signs in with a temporary password and MUST change it before reaching any
  // part of the app. This blocks the whole authenticated shell (every route
  // renders inside AppLayout) until the flag clears. The screen itself offers a
  // "Sign out" escape hatch. Server-side, the temp password still works only
  // for /auth/change-password's verify step — this is the UX enforcement.
  if (user?.mustChangePassword) {
    return <ForcePasswordChange />;
  }

  return (
    <Shell crumbs={crumbs} brand={brand}>
      {/* INVESTOR read-only banner — sticky top-of-page strip when the
          signed-in user is INVESTOR-only. Backend middleware also blocks
          writes server-side; this is the user-facing acknowledgement so
          the operator never wonders why nothing saves. */}
      {isReadOnly() && (
        <div
          style={{
            background: 'var(--warn-50, #fef3c7)',
            borderBottom: '1px solid var(--warn-200, #fcd34d)',
            padding: '8px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 13,
            color: 'var(--warn-800, #92400e)',
            position: 'sticky',
            top: 52,
            zIndex: 50,
          }}
        >
          <Eye style={{ width: 16, height: 16 }} />
          <strong>Read-only mode</strong>
          <span style={{ opacity: 0.85 }}>
            · You're signed in with the INVESTOR role. Numbers and reports are visible; create / edit / delete actions are disabled.
          </span>
        </div>
      )}
      <main style={{ padding: '0' }}>
        <Outlet />
      </main>
    </Shell>
  );
}

export default AppLayout;
