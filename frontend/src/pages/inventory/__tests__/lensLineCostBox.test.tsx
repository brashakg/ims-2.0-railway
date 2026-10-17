// ============================================================================
// The new lens line's cost box follows the one cost rule (owner ruling 2026-10-12)
// ============================================================================
// As on Add product, the cost price box is for COST_ROLES only: a catalogue
// manager catalogues the lens by MRP and leaves the cost to accounts.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const auth = vi.hoisted(() => ({ role: 'CATALOG_MANAGER' }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-TEST-01', roles: [auth.role] } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
vi.mock('../../../services/api/lensCatalog', () => ({ lensCatalogApi: { create: vi.fn() } }));

import NewLensLineModal from '../NewLensLineModal';
import { COST_ROLES } from '../../../components/common/CostCell';

function renderModal(role: string) {
  auth.role = role;
  render(<NewLensLineModal meta={null} onClose={() => {}} onCreated={() => {}} />);
}

describe('New lens line', () => {
  it.each(['CATALOG_MANAGER', 'STORE_MANAGER'])('asks %s for the MRP but not the cost', (role) => {
    renderModal(role);
    expect(screen.getByText(/^MRP/)).toBeTruthy();
    expect(screen.queryByText(/cost price/i)).toBeNull();
  });

  it.each(COST_ROLES)('keeps the cost box for %s', (role) => {
    renderModal(role);
    expect(screen.getByText(/cost price/i)).toBeTruthy();
  });
});
