// ============================================================================
// IMS 2.0 - The blocked page says who CAN do this (procurement audit F5)
// ============================================================================
// The person who opens the parcel (workshop staff) landed on a bare "403 - You
// don't have permission" when he tried to receive goods. Receiving stays with
// the managers (owner ruling 2026-09-28) -- but the page now says who can
// receive, so he knows whom to hand the box to.
//
// Mounts the REAL purchaseRoutes, so widening a receive route's roles or
// dropping its hint turns this red (a hand-built <ProtectedRoute> would not).

import { Suspense } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const ROLES: string[] = ['WORKSHOP_STAFF'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    isLoading: false,
    hasRole: (roles?: string[]) => !roles || roles.some((r) => ROLES.includes(r)),
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

// The section layout itself is not under test: an Outlet is enough to let a
// role through to the section's own gate.
vi.mock('../../../pages/purchase/PurchaseLayout', async () => {
  const { Outlet } = await import('react-router-dom');
  return { PurchaseLayout: () => <Outlet /> };
});

import { UnauthorizedPage } from '../UnauthorizedPage';
import { purchaseRoutes } from '../../../routes/purchaseRoutes';

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Suspense fallback={<div>loading</div>}>
        <Routes>
          {purchaseRoutes}
          <Route path="/unauthorized" element={<UnauthorizedPage />} />
        </Routes>
      </Suspense>
    </MemoryRouter>,
  );
}

describe('403 page names who can receive goods (F5)', () => {
  it.each(['/purchase/receive', '/purchase/grn'])(
    '%s: workshop staff get the blocked page, which names who receives',
    async (path) => {
      renderAt(path);
      expect(await screen.findByText('403')).toBeInTheDocument();
      expect(screen.getByText(/hand the delivery to one of them/i)).toBeInTheDocument();
      // The hint and the list agree: the list IS the rule -- managers only
      // (owner ruling 2026-09-28), the accountant not among them.
      expect(screen.getByText(/^Who can:/)).toHaveTextContent(
        'Who can: Admin, Area manager, Store manager.',
      );
    },
  );

  it.each(['/purchase/receive', '/purchase/grn'])(
    '%s: the accountant does not receive goods (managers only)',
    async (path) => {
      ROLES.splice(0, ROLES.length, 'ACCOUNTANT');
      try {
        renderAt(path);
        expect(await screen.findByText('403')).toBeInTheDocument();
      } finally {
        ROLES.splice(0, ROLES.length, 'WORKSHOP_STAFF');
      }
    },
  );

  it('any other blocked page still says who can open it', async () => {
    renderAt('/purchase/recon-console');
    expect(await screen.findByText(/^Who can:/)).toHaveTextContent('Who can: Admin, Accountant.');
  });

  // Review round 7: the /purchase LAYOUT gate (the union of every section's
  // roles) refused first and its list reached the page, naming workshop staff
  // for /purchase/orders and the accountant for /purchase/vendor-returns --
  // two roles those sections refuse.
  it.each([
    ['/purchase/orders', ['Workshop staff']],
    ['/purchase/vendor-returns', ['Accountant']],
  ])('%s: a role refused at the section layout is never told to ask %s', async (path, refused) => {
    ROLES.splice(0, ROLES.length, 'CATALOG_MANAGER');
    try {
      renderAt(path);
      expect(await screen.findByText('403')).toBeInTheDocument();
      for (const name of refused) {
        expect(screen.queryByText(new RegExp(name))).not.toBeInTheDocument();
      }
    } finally {
      ROLES.splice(0, ROLES.length, 'WORKSHOP_STAFF');
    }
  });

  it('a section gate still names its own roles', async () => {
    renderAt('/purchase/orders'); // workshop staff pass the layout, not the section
    expect(await screen.findByText(/^Who can:/)).toHaveTextContent(
      'Who can: Admin, Area manager, Store manager, Accountant.',
    );
  });

  it('opened directly (no blocked page behind it) it stays the plain message', () => {
    render(
      <MemoryRouter initialEntries={['/unauthorized']}>
        <UnauthorizedPage />
      </MemoryRouter>,
    );
    expect(screen.getByText(/you don't have permission/i)).toBeInTheDocument();
  });
});
