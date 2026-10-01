// ============================================================================
// IMS 2.0 - the Inventory "Online" card never says "none synced online" when
// IMS could not read which listings are live
// ============================================================================
// Multi-location PR 4, round 17 review round 2: /catalog/online-status now
// answers `online` from THE one live-listing reader; a dead read answers
// online: null (unknown). The card shows a dash and says it could not read
// the website. Count null as "not online" again -> "0 / none synced online"
// -> fails.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.stubGlobal('requestIdleCallback', () => 0);
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

let onlineStatus: Record<string, { online: boolean | null; online_stock: null }> = {};

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'USR-1', activeStoreId: 'BV-RAN-01', storeIds: ['BV-RAN-01'], roles: ['ADMIN'] },
    hasRole: (roles: string[]) => roles.includes('ADMIN'),
  }),
}));
vi.mock('../../../components/inventory/StockTransferModal', () => ({
  StockTransferModal: () => null,
}));
vi.mock('../inventoryQueries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../inventoryQueries')>();
  const idle = { data: undefined, isFetching: false, isError: false, isPending: false };
  return {
    ...actual,
    useStock: () => ({ ...idle, data: [{ id: 'S1', sku: 'SKU-1', stock: 1, mrp: 100 }] }),
    useLowStock: () => idle,
    useOnlineStatus: () => ({ ...idle, data: onlineStatus }),
    useFixturesMap: () => idle,
    useQuarantineUnlabeled: () => idle,
    useInventoryStores: () => ({ data: [] }),
    useOnlineSummary: () => idle,
  };
});

import { InventoryLayout } from '../InventoryLayout';

function renderLayout() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/inventory/reorders']}>
        <InventoryLayout />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('the Inventory Online card', () => {
  it('says it could not read the website when the live read is unknown', () => {
    onlineStatus = { 'SKU-1': { online: null, online_stock: null } };
    renderLayout();
    const note = screen.getByText(/could not read the website/i);
    expect(note.previousElementSibling).toHaveTextContent('—');
    expect(screen.queryByText(/none synced online/i)).toBeNull();
  });

  it('says none synced online when the read worked and nothing is live', () => {
    onlineStatus = { 'SKU-1': { online: false, online_stock: null } };
    renderLayout();
    expect(screen.getByText(/none synced online/i)).toBeInTheDocument();
  });
});
