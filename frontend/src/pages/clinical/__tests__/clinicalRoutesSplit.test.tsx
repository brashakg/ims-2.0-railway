// ============================================================================
// Wave 2 clinical split: addresses + role gates
// ============================================================================
// Pins the three things the split must not lose:
//
//   1. Old addresses keep working: bare /clinical lands on the queue,
//      /clinical/test (the deleted placeholder page) forwards to the queue,
//      and /prescriptions (the DELETED rival Rx-library page) forwards to
//      /clinical/prescriptions — the ONE surviving prescriptions door.
//   2. Manager-only stays manager-only: an OPTOMETRIST is bounced off
//      /clinical/abuse-alerts while a STORE_MANAGER gets in.
//   3. The module gate holds: a SALES_STAFF cannot open the queue by URL.
//
// The section PAGES are mocked to sentinels — what is under test here is the
// real clinicalRoutes + the real ProtectedRoute + the real role lists in
// clinicalRoles.ts. The auth mock's hasRole is a faithful mini-implementation
// (roles-intersect), NOT a stub returning true — a stub would pass every gate
// and prove nothing.

import { Suspense } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const CURRENT = { roles: ['SUPERADMIN'] as string[] };

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    isLoading: false,
    user: { id: 'u1', name: 'Test User', roles: CURRENT.roles, activeStoreId: 'BV-BOK-01' },
    hasRole: (role: string | string[]) =>
      (Array.isArray(role) ? role : [role]).some((r) => CURRENT.roles.includes(r)),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

// Section pages -> sentinels. The routing + gating stays real.
vi.mock('../ClinicalLayout', async () => {
  const { Outlet } = await import('react-router-dom');
  return { ClinicalLayout: () => (<div>LAYOUT-SHELL<Outlet /></div>) };
});
vi.mock('../ClinicalQueuePage', () => ({ ClinicalQueuePage: () => <div>QUEUE-PAGE-SENTINEL</div> }));
vi.mock('../ClinicalCompletedPage', () => ({ ClinicalCompletedPage: () => <div>COMPLETED-PAGE-SENTINEL</div> }));
vi.mock('../ClinicalPrescriptionsPage', () => ({ ClinicalPrescriptionsPage: () => <div>RX-DOOR-SENTINEL</div> }));
vi.mock('../ClinicalAbusePage', () => ({ ClinicalAbusePage: () => <div>ABUSE-PAGE-SENTINEL</div> }));
vi.mock('../ConversionTab', () => ({ ConversionTab: () => <div>CONVERSION-PAGE-SENTINEL</div> }));
vi.mock('../EyeExamPage', () => ({ EyeExamPage: () => <div>EXAM-PAGE-SENTINEL</div> }));

import { clinicalRoutes } from '../../../routes/clinicalRoutes';

const SLOW = 20000;

function renderAt(path: string, roles: string[]) {
  CURRENT.roles = roles;
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Suspense fallback={<div>chunk-loading</div>}>
        <Routes>
          {clinicalRoutes}
          <Route path="/unauthorized" element={<div>DENIED-SENTINEL</div>} />
          <Route path="/login" element={<div>LOGIN-SENTINEL</div>} />
        </Routes>
      </Suspense>
    </MemoryRouter>,
  );
}

describe('old clinical addresses keep working', () => {
  it('bare /clinical lands on the queue section', async () => {
    renderAt('/clinical', ['OPTOMETRIST']);
    expect(await screen.findByText('QUEUE-PAGE-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW);

  it('/clinical/test (the deleted placeholder) forwards to the queue', async () => {
    renderAt('/clinical/test', ['OPTOMETRIST']);
    expect(await screen.findByText('QUEUE-PAGE-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW);

  it('/prescriptions (the deleted rival Rx page) forwards to the ONE surviving door', async () => {
    renderAt('/prescriptions', ['OPTOMETRIST']);
    expect(await screen.findByText('RX-DOOR-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
    // ...and it renders inside the clinical layout, not as a standalone page.
    expect(screen.getByText('LAYOUT-SHELL')).toBeInTheDocument();
  }, SLOW);
});

describe('role gates come from clinicalRoles, and hold by direct URL', () => {
  it('bounces an OPTOMETRIST off /clinical/abuse-alerts (managers only)', async () => {
    renderAt('/clinical/abuse-alerts', ['OPTOMETRIST']);
    expect(await screen.findByText('DENIED-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
    expect(screen.queryByText('ABUSE-PAGE-SENTINEL')).not.toBeInTheDocument();
  }, SLOW);

  it('lets a STORE_MANAGER open /clinical/abuse-alerts', async () => {
    renderAt('/clinical/abuse-alerts', ['STORE_MANAGER']);
    expect(await screen.findByText('ABUSE-PAGE-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW);

  // ---------------------------------------------------------------------
  // /clinical/conversion is NOT manager-only, and pinning that is the point.
  // ---------------------------------------------------------------------
  // The pre-split page gated this tab on
  //   canViewConversion = hasRole(['SUPERADMIN','ADMIN','AREA_MANAGER',
  //                                'STORE_MANAGER','OPTOMETRIST'])
  // and the backend still does the same: clinical.py `_CONVERSION_VIEW_ROLES`
  // includes OPTOMETRIST, and GET /conversion-dashboard serves them their OWN
  // row with the revenue column stripped server-side ("DECISIONS sec 3,
  // LOCKED"). CLINICAL_MODULE_ROLES is that list intersected with the module
  // gate, so the split neither widened nor narrowed it.
  //
  // Both directions are pinned here. WIDENING (adding a role the pre-split
  // page did not admit) fails on the CASHIER case; NARROWING it to
  // CLINICAL_MANAGER_ROLES -- which would blank a screen the API serves and
  // contradict a locked decision -- fails on the OPTOMETRIST case.
  it('keeps an OPTOMETRIST on /clinical/conversion (parity with the old tab)', async () => {
    renderAt('/clinical/conversion', ['OPTOMETRIST']);
    expect(await screen.findByText('CONVERSION-PAGE-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW);

  it('bounces a CASHIER off /clinical/conversion -- the gate must not widen', async () => {
    renderAt('/clinical/conversion', ['CASHIER']);
    expect(await screen.findByText('DENIED-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
    expect(screen.queryByText('CONVERSION-PAGE-SENTINEL')).not.toBeInTheDocument();
  }, SLOW);

  it('lets an OPTOMETRIST open the examination page, and bounces a SALES_STAFF', async () => {
    renderAt('/clinical/test/q-1', ['OPTOMETRIST']);
    expect(await screen.findByText('EXAM-PAGE-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW);

  it('bounces a SALES_STAFF off the examination page by direct URL', async () => {
    renderAt('/clinical/test/q-1', ['SALES_STAFF']);
    expect(await screen.findByText('DENIED-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
    expect(screen.queryByText('EXAM-PAGE-SENTINEL')).not.toBeInTheDocument();
  }, SLOW);

  it('/clinical/test/amend/:testId is the SAME page, not swallowed by :entryId', async () => {
    renderAt('/clinical/test/amend/test-9', ['OPTOMETRIST']);
    expect(await screen.findByText('EXAM-PAGE-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
  }, SLOW);

  it('bounces a SALES_STAFF off the module entirely, by direct URL', async () => {
    renderAt('/clinical/queue', ['SALES_STAFF']);
    expect(await screen.findByText('DENIED-SENTINEL', {}, { timeout: SLOW })).toBeInTheDocument();
    expect(screen.queryByText('QUEUE-PAGE-SENTINEL')).not.toBeInTheDocument();
  }, SLOW);
});
