// ============================================================================
// IMS 2.0 - The blocked page says who CAN do this (procurement audit F5)
// ============================================================================
// The person who opens the parcel (workshop staff) landed on a bare "403 - You
// don't have permission" when he tried to receive goods. Receiving stays with
// the managers (owner ruling 2026-09-28) -- but the page now says who can
// receive, so he knows whom to hand the box to.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    isLoading: false,
    hasRole: () => false,
    hasPermission: () => true,
    hasModuleAccess: () => true,
  }),
}));

import { ProtectedRoute } from '../ProtectedRoute';
import { UnauthorizedPage } from '../UnauthorizedPage';
import { PURCHASE_MANAGER_ROLES } from '../../../pages/purchase/purchaseTypes';

function renderBlocked(path: string, element: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={path} element={element} />
        <Route path="/unauthorized" element={<UnauthorizedPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('403 page names who can receive goods (F5)', () => {
  it('a blocked receive route says only managers receive, and names them', () => {
    renderBlocked(
      '/purchase/receive',
      <ProtectedRoute allowedRoles={[...PURCHASE_MANAGER_ROLES]} deniedHint="Only managers receive goods into stock.">
        <div>receive screen</div>
      </ProtectedRoute>,
    );
    expect(screen.queryByText('receive screen')).not.toBeInTheDocument();
    expect(screen.getByText(/only managers receive goods/i)).toBeInTheDocument();
    expect(screen.getByText(/store manager/i)).toBeInTheDocument();
    expect(screen.getByText(/area manager/i)).toBeInTheDocument();
    // Receiving is NOT opened to workshop staff.
    expect(screen.queryByText(/workshop/i)).not.toBeInTheDocument();
  });

  it('any other blocked page still says who can open it', () => {
    renderBlocked(
      '/purchase/recon-console',
      <ProtectedRoute allowedRoles={['SUPERADMIN', 'ADMIN', 'ACCOUNTANT']}>
        <div>recon</div>
      </ProtectedRoute>,
    );
    expect(screen.getByText(/accountant/i)).toBeInTheDocument();
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
