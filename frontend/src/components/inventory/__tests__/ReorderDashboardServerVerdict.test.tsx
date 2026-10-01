// Audit F48 (verifier round 6): the Reorder dashboard kept its own copy of the
// reorder rule. A settings save re-decided "auto-reorder off" from the saved
// quantity alone, so a discontinued product (the server says off whatever the
// quantity) turned orderable by following the screen's own advice ("Enable it
// via the settings icon") and Generate PO raised a real PO for it. The server
// flag is the only judge; a discontinued product says so instead.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getLowStock = vi.fn();
const getStock = vi.fn();
const createPurchaseOrder = vi.fn();
const updateReorderSettings = vi.fn();
const toastError = vi.fn();

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', roles: ['ADMIN'], activeStoreId: 'S1' },
    hasRole: () => true,
  }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: toastError, success: () => {}, warning: () => {}, info: () => {} }),
}));
vi.mock('../../../services/api/inventory', () => ({
  inventoryApi: { getLowStock: (...a: unknown[]) => getLowStock(...a), getStock: (...a: unknown[]) => getStock(...a) },
  vendorsApi: { createPurchaseOrder: (...a: unknown[]) => createPurchaseOrder(...a) },
  reorderApi: { updateReorderSettings: (...a: unknown[]) => updateReorderSettings(...a) },
}));
// The modal's own form is not under test: one button saves quantity 5.
vi.mock('../ReorderPointModal', () => ({
  ReorderPointModal: ({ product, onSave }: { product: { id: string }; onSave: (d: unknown) => Promise<void> }) => (
    <button
      onClick={() =>
        onSave({ productId: product.id, reorderPoint: 10, reorderQuantity: 5, maxStock: 50, leadTimeDays: 7 })
      }
    >
      save qty 5
    </button>
  ),
}));

import { ReorderDashboard } from '../ReorderDashboard';

const ledger = (extra: Record<string, unknown>) => ({
  items: [
    { product_id: 'P1', name: 'Frame P1', quantity: 3, status: 'AVAILABLE', reorder_point: 10, vendor_id: 'V1', ...extra },
  ],
});
const verdict = (off: boolean) => ({ items: [{ _id: 'P1', quantity: 3, auto_reorder_disabled: off }] });

async function saveQty5ThenGeneratePO() {
  fireEvent.click(await screen.findByTitle('Configure reorder point'));
  fireEvent.click(screen.getByText('save qty 5'));
  await waitFor(() => expect(getLowStock).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getAllByRole('checkbox')[1]);
  fireEvent.click(screen.getByText(/Generate PO/));
}

describe('ReorderDashboard - the server decides auto-reorder', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateReorderSettings.mockResolvedValue({});
    createPurchaseOrder.mockResolvedValue({});
  });

  it('never orders a discontinued product, even after a quantity is saved', async () => {
    getStock.mockResolvedValue(ledger({ is_active: false, reorder_quantity: 5 }));
    getLowStock.mockResolvedValue(verdict(true));
    render(<MemoryRouter><ReorderDashboard /></MemoryRouter>);

    expect(await screen.findByText('Discontinued - not reordered')).toBeInTheDocument();
    await saveQty5ThenGeneratePO();

    expect(screen.getByText('Discontinued - not reordered')).toBeInTheDocument();
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(createPurchaseOrder).not.toHaveBeenCalled();
    const said = toastError.mock.calls.map((c) => String(c[0])).join(' | ');
    expect(said).toContain('discontinued, not reordered');
    expect(said).not.toContain('settings');
  });

  it('orders a product the server turns on once its quantity is saved', async () => {
    getStock.mockResolvedValue(ledger({ is_active: true, reorder_quantity: -1 }));
    getLowStock.mockResolvedValueOnce(verdict(true)).mockResolvedValue(verdict(false));
    render(<MemoryRouter><ReorderDashboard /></MemoryRouter>);

    expect(await screen.findByText('Auto-reorder off')).toBeInTheDocument();
    await saveQty5ThenGeneratePO();

    await waitFor(() => expect(createPurchaseOrder).toHaveBeenCalledTimes(1));
    expect(createPurchaseOrder.mock.calls[0][0].items[0]).toMatchObject({ product_id: 'P1', quantity: 5 });
  });
});
