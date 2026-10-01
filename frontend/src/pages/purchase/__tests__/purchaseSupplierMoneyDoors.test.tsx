// ============================================================================
// IMS 2.0 - Purchase: the supplier-bill tab is the accounts roles', and the
// accounts roles can reach the vendor returns they own
// ============================================================================
// Owner ruling 2026-10-01: "supplier balances should not be shown to anyone
// apart from admin superadmin and accountant". The Purchase Invoices tab IS
// the supplier bills -- what we were billed, what we owe, what is paid -- and
// every read behind it (/vendors/purchase-invoices/*) already answers the
// accounts roles only. It was still offered to store and area managers, who
// opened it onto a 403. Now neither the tab nor the address is theirs.
//
// The other direction: the supplier credit and GST debit notes on vendor
// returns are the accountant's to read, but /purchase/vendor-returns refused
// ACCOUNTANT ("not in this flow"). It is admitted now.
//
// The REAL route table and the REAL Purchase layout are mounted; only the
// section pages are stubbed so the test reads the gates and the tab row.

import { Suspense } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.stubGlobal('requestIdleCallback', () => 0);

let roles: string[] = ['STORE_MANAGER'];

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', name: 'Tester', roles, activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    isAuthenticated: true,
    isLoading: false,
    // Same contract as AuthContext.hasRole: ADMIN / SUPERADMIN pass any check.
    hasRole: (want: string | string[]) => {
      if (roles.includes('ADMIN') || roles.includes('SUPERADMIN')) return true;
      return (Array.isArray(want) ? want : [want]).some((r) => roles.includes(r));
    },
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));
vi.mock('../../../hooks/useIsOnlineStore', () => ({ useIsOnlineStore: () => false }));
// The admin's shop picker reads the store list; nothing else here needs it.
vi.mock('../../../hooks/usePOSQueries', () => ({ useStores: () => ({ data: [] }) }));
vi.mock('../PurchaseInvoicesSection', () => ({
  PurchaseInvoicesSection: () => <div>INVOICES SECTION</div>,
}));
vi.mock('../VendorReturns', () => ({
  VendorReturns: () => <div>VENDOR RETURNS SECTION</div>,
}));
vi.mock('../PurchaseVarianceTab', () => ({
  PurchaseVarianceTab: () => <div>VARIANCE SECTION</div>,
}));

import { purchaseRoutes } from '../../../routes/purchaseRoutes';

function openAt(path: string, role: string) {
  roles = [role];
  render(
    <MemoryRouter initialEntries={[path]}>
      <Suspense fallback={null}>
        <Routes>
          {purchaseRoutes}
          <Route path="/unauthorized" element={<div>UNAUTHORIZED</div>} />
        </Routes>
      </Suspense>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  roles = ['STORE_MANAGER'];
});

describe('Purchase Invoices - supplier bills are the accounts roles only', () => {
  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: no Purchase Invoices tab, and the address bounces',
    async (role) => {
      openAt('/purchase/variance', role);
      // Positive control: the layout and its tab row rendered.
      expect(await screen.findByText('VARIANCE SECTION')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: /Purchase Orders/ })).toBeInTheDocument();
      expect(screen.queryByRole('link', { name: /Purchase Invoices/ })).not.toBeInTheDocument();
    },
  );

  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])(
    '%s: /purchase/invoices lands on unauthorized, never the bills',
    async (role) => {
      openAt('/purchase/invoices', role);
      expect(await screen.findByText('UNAUTHORIZED')).toBeInTheDocument();
      expect(screen.queryByText('INVOICES SECTION')).not.toBeInTheDocument();
    },
  );

  it.each([['ACCOUNTANT'], ['ADMIN'], ['SUPERADMIN']])(
    '%s: the tab is offered and the bills open',
    async (role) => {
      openAt('/purchase/invoices', role);
      expect(await screen.findByText('INVOICES SECTION')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: /Purchase Invoices/ })).toBeInTheDocument();
    },
  );
});

describe('Vendor Returns - the accounts roles reach the debit notes they own', () => {
  it.each([['ACCOUNTANT'], ['STORE_MANAGER'], ['AREA_MANAGER'], ['WORKSHOP_STAFF'], ['ADMIN']])(
    '%s: /purchase/vendor-returns opens',
    async (role) => {
      openAt('/purchase/vendor-returns', role);
      expect(await screen.findByText('VENDOR RETURNS SECTION')).toBeInTheDocument();
    },
  );

  it.each([['SALES_STAFF'], ['CASHIER']])('%s: /purchase/vendor-returns still bounces', async (role) => {
    openAt('/purchase/vendor-returns', role);
    expect(await screen.findByText('UNAUTHORIZED')).toBeInTheDocument();
  });
});
