// ============================================================================
// ONE GATE for the stock-count screen - checked from the nav side
// ============================================================================
// Before the split the stock-count screen had TWO contradicting gates: the
// /inventory/audit route excluded WORKSHOP_STAFF, but /inventory?tab=stock-count
// rendered the SAME StockAudit component to WORKSHOP_STAFF inline. Same story
// for the lens power grid (route: manage+OPTOMETRIST; tab: everyone). The tab
// copies are deleted; this asserts the layout nav honours the ONE list in
// inventoryRoles.ts - a WORKSHOP_STAFF login must not be offered either
// screen, and every manage-ladder role must be.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

// jsdom has no requestIdleCallback, so the layout's chunk-warming falls back
// to a 1.5s setTimeout that can fire AFTER teardown and import chunks into a
// dead environment (flaky unhandled errors). Warming is not under test.
vi.stubGlobal('requestIdleCallback', () => 0);
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

let currentRoles: string[] = [];

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', activeStoreId: 'BV-RAN-01', storeIds: ['BV-RAN-01'], roles: currentRoles },
    hasRole: (roles: string[]) => roles.some((r) => currentRoles.includes(r)),
  }),
}));
vi.mock('../../../components/inventory/StockTransferModal', () => ({
  StockTransferModal: () => null,
}));
// Stub only the data hooks; the nav gating under test stays real.
vi.mock('../inventoryQueries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../inventoryQueries')>();
  const idle = { data: undefined, isFetching: false, isError: false, isPending: false };
  return {
    ...actual,
    useStock: () => idle,
    useLowStock: () => idle,
    useOnlineStatus: () => idle,
    useFixturesMap: () => idle,
    useQuarantineUnlabeled: () => idle,
    useInventoryStores: () => ({ data: [] }),
    useOnlineSummary: () => idle,
  };
});

import { InventoryLayout } from '../InventoryLayout';
import { INVENTORY_MANAGE_ROLES, INVENTORY_MODULE_ROLES } from '../inventoryRoles';

function renderAs(role: string, path: string) {
  currentRoles = [role];
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <InventoryLayout />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the one manage-ladder list gates the two tighter sections', () => {
  it('WORKSHOP_STAFF is a module member but NOT on the manage ladder', () => {
    expect(INVENTORY_MODULE_ROLES).toContain('WORKSHOP_STAFF');
    expect(INVENTORY_MANAGE_ROLES).not.toContain('WORKSHOP_STAFF');
  });

  it('does not offer WORKSHOP_STAFF the stock count (the old tab leaked it)', () => {
    renderAs('WORKSHOP_STAFF', '/inventory/reorders');
    // The Operations sub-nav is open; its other entries prove it rendered.
    expect(screen.getByRole('button', { name: /Transfers/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Stock count/ })).not.toBeInTheDocument();
  });

  it('does not offer WORKSHOP_STAFF the lens power grid', () => {
    renderAs('WORKSHOP_STAFF', '/inventory/serial-numbers');
    expect(screen.getByRole('button', { name: /Contact lens/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Lens power grid/ })).not.toBeInTheDocument();
  });

  it.each(INVENTORY_MANAGE_ROLES)('offers %s both gated sections', (role) => {
    renderAs(role, '/inventory/reorders');
    expect(screen.getByRole('button', { name: /Stock count/ })).toBeInTheDocument();
    renderAs(role, '/inventory/serial-numbers');
    expect(screen.getByRole('button', { name: /Lens power grid/ })).toBeInTheDocument();
  });
});
