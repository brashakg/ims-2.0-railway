// ============================================================================
// IMS 2.0 — Main Application Layout
// Uses the new design-handoff Shell (64px rail + 52px topbar).
// Pages render inside <Outlet /> within the Shell's page-body.
// ============================================================================

import { Outlet, useLocation } from 'react-router-dom';
import { useMemo } from 'react';
import { Shell, type Crumb } from '../shell';
import { useAppearance } from '../../context/AppearanceContext';

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

  const crumbs = useMemo(() => pathToCrumbs(location.pathname), [location.pathname]);

  return (
    <Shell crumbs={crumbs} brand={brand}>
      <main style={{ padding: '0' }}>
        <Outlet />
      </main>
    </Shell>
  );
}

export default AppLayout;
