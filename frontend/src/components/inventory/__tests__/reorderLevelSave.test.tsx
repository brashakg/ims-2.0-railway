// ============================================================================
// Reorder settings modal - review round 2 (F73, owner D12)
// ============================================================================
// A blank level in the modal means "not set": it is sent as null, never 0.
// (The save-by-role half moved with the screen to the one Reorder list, S5:
// pages/inventory/__tests__/ReorderListPage.test.tsx.)

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { ReorderPointModal } from '../ReorderPointModal';

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

  it('has no formula of its own: no auto-calculate, the server sales shown as they are (S5 review)', () => {
    render(
      <ReorderPointModal
        isOpen
        onClose={() => {}}
        onSave={vi.fn(async () => {})}
        product={{ id: 'P', sku: 'S', name: 'N', brand: 'B', currentStock: 1, reorderPoint: null, soldLast30Days: 5 }}
      />,
    );
    expect(screen.queryByText(/auto-calculate/i)).toBeNull();
    expect(screen.queryAllByRole('checkbox').map((c) => c.closest('label')?.textContent)).toEqual([
      'Auto-reorder disabled',
    ]);
    expect(screen.getByText('Sold in the last 30 days').nextSibling?.textContent).toBe('5');
    // never a made-up "2 a day", nor a default max stock / lead time
    expect(screen.getByLabelText('Maximum stock')).toHaveValue(null);
    expect(screen.getByLabelText('Supplier lead time (days)')).toHaveValue(null);
    expect(screen.getByLabelText('Order in packs of')).toHaveValue(null);
  });

  it('an untouched save writes no level, pack, max stock or lead time the person did not type', async () => {
    const onSave = vi.fn(async () => {});
    render(
      <ReorderPointModal
        isOpen
        onClose={() => {}}
        onSave={onSave}
        product={{ id: 'P', sku: 'S', name: 'N', brand: 'B', currentStock: 1, reorderPoint: 2, soldLast30Days: 0 }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect((onSave.mock.calls[0] as unknown[])[0]).toEqual({ productId: 'P', reorderPoint: 2, product: {} });
  });

  it('only the product-wide fields the person changed are sent', async () => {
    const onSave = vi.fn(async () => {});
    render(
      <ReorderPointModal
        isOpen
        onClose={() => {}}
        onSave={onSave}
        product={{ id: 'P', sku: 'S', name: 'N', brand: 'B', currentStock: 1, reorderPoint: 2,
          reorderQuantity: 3, maxStock: 50, leadTimeDays: 7 }}
      />,
    );
    fireEvent.change(screen.getByLabelText('Maximum stock'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    // cleared = 0 = no cap (the server drops nulls)
    expect((onSave.mock.calls[0] as unknown[])[0]).toMatchObject({ product: { max_stock: 0 } });
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
