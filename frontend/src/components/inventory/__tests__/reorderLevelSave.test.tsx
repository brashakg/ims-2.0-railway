// ============================================================================
// Reorder dashboard save - review round 2 (F73, owner D12)
// ============================================================================
// 1. A catalogue manager may not set a SHOP's level (the server 403s it) but
//    may still save the product-wide fields: those saves are independent.
// 2. A blank level in the modal means "not set": it is sent as null, never 0.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

const role = vi.hoisted(() => ({ current: 'CATALOG_MANAGER' }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'U', activeStoreId: 'BV-DHN-02', storeIds: ['BV-DHN-02'], roles: [role.current] },
    hasRole: (roles: string[]) => roles.includes(role.current),
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const LOW_STOCK = { items: [{ _id: 'P-FRAME', quantity: 1, reorder_point: 2, auto_reorder_disabled: false }] };
const STOCK_ROWS = {
  items: [{
    product_id: 'P-FRAME', sku: 'SKU-P-FRAME', name: 'Carrera CA8895', brand: 'Carrera',
    category: 'FR', quantity: 1, status: 'AVAILABLE', reorder_point: 2, reorder_quantity: 3,
  }],
};
const http = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(async () => ({ data: {} })),
  post: vi.fn(async () => ({ data: {} })),
  patch: vi.fn(async () => ({ data: {} })),
  delete: vi.fn(async () => ({ data: {} })),
}));
http.get.mockImplementation(async (url: string) => ({
  data: url.includes('low-stock') ? LOW_STOCK : url.includes('/inventory/stock') ? STOCK_ROWS : {},
}));
vi.mock('../../../services/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/client')>()),
  default: http,
}));

import { ReorderDashboard } from '../ReorderDashboard';
import { ReorderPointModal } from '../ReorderPointModal';

const putUrls = () => http.put.mock.calls.map((c) => String((c as unknown[])[0]));

beforeEach(() => {
  for (const fn of Object.values(http)) fn.mockClear();
});

describe('Reorder dashboard save, by role', () => {
  it('a catalogue manager saves the product-wide fields and never calls the shop-level write', async () => {
    role.current = 'CATALOG_MANAGER';
    render(<MemoryRouter><ReorderDashboard /></MemoryRouter>);
    fireEvent.click(await screen.findByTitle('Configure reorder point'));
    fireEvent.click(await screen.findByRole('button', { name: /save/i }));
    await waitFor(() => expect(putUrls()).toContain('/products/P-FRAME'));
    expect(putUrls().some((u) => u.includes('reorder-levels'))).toBe(false);
  });

  it('a store manager saves this shop\'s level; the product-wide fields are not theirs', async () => {
    role.current = 'STORE_MANAGER';
    render(<MemoryRouter><ReorderDashboard /></MemoryRouter>);
    fireEvent.click(await screen.findByTitle('Configure reorder point'));
    // The level is sent only when it changed (round 4): 2 -> 3.
    fireEvent.change(await screen.findByPlaceholderText('not set'), { target: { value: '3' } });
    fireEvent.click(await screen.findByRole('button', { name: /save/i }));
    await waitFor(() => expect(putUrls().some((u) => u.includes('reorder-levels/P-FRAME'))).toBe(true));
    expect(putUrls()).not.toContain('/products/P-FRAME');
  });
});

describe('Reorder modal level field', () => {
  it('blank means not set: saved as null, never 0', async () => {
    const onSave = vi.fn(async () => {});
    render(
      <ReorderPointModal
        isOpen
        onClose={() => {}}
        onSave={onSave}
        product={{
          id: 'P-FRAME', sku: 'S', name: 'N', brand: 'B', currentStock: 1,
          reorderPoint: 2, reorderQuantity: 3, maxStock: 50, leadTimeDays: 7,
        }}
      />,
    );
    const field = screen.getByPlaceholderText('not set');
    fireEvent.change(field, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect((onSave.mock.calls[0] as unknown[])[0]).toMatchObject({ reorderPoint: null });
  });

  it('0 is a real level', async () => {
    const onSave = vi.fn(async () => {});
    render(
      <ReorderPointModal
        isOpen
        onClose={() => {}}
        onSave={onSave}
        product={{
          id: 'P-FRAME', sku: 'S', name: 'N', brand: 'B', currentStock: 1,
          reorderPoint: null, reorderQuantity: 3, maxStock: 50, leadTimeDays: 7,
        }}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText('not set'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect((onSave.mock.calls[0] as unknown[])[0]).toMatchObject({ reorderPoint: 0 });
  });
});
