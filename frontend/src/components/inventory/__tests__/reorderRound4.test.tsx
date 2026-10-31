// ============================================================================
// Reorder screens - review round 4 (F73, owner D12)
// ============================================================================
// 3.  The Reorder modal shows the SERVER's band for the saved level, no bands
//     of its own.
// 11. Each screen refuses an invalid typed level: toast, no PUT.
// 12. ShopReorderLevel controls keep the 36px min height.
// R5. ShopReorderLevel: blank on an unset shop sends nothing; the draft never
//     moves to another shop.
// (The dashboard's items moved with the screen to the one Reorder list, S5:
// pages/inventory/__tests__/ReorderListPage.test.tsx.)
// 15. Malformed number text (validity.badInput) is invalid, never a blank that
//     clears the level.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const auth = vi.hoisted(() => ({ store: 'BV-DHN-02' as string | undefined, role: 'ADMIN' }));
const toast = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn(),
}));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'U', activeStoreId: auth.store, storeIds: ['BV-DHN-02'], roles: [auth.role] },
    hasRole: (roles: string[]) => roles.includes(auth.role),
  }),
}));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const feed = vi.hoisted(() => ({
  rows: [] as Array<Record<string, unknown>>,
  ledger: [] as Array<Record<string, unknown>>,
}));
const http = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(async () => ({ data: {} })),
  post: vi.fn(async () => ({ data: {} })),
  patch: vi.fn(async () => ({ data: {} })),
  delete: vi.fn(async () => ({ data: {} })),
}));
http.get.mockImplementation(async (url: string) => ({
  data: url.includes('low-stock')
    ? { items: feed.rows }
    : url.includes('/inventory/stock')
      ? { items: feed.ledger }
      : {},
}));
vi.mock('../../../services/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../services/api/client')>()),
  default: http,
}));

import { ReorderPointModal } from '../ReorderPointModal';
import { ShopReorderLevel } from '../../../pages/inventory/ShopReorderLevel';

const putUrls = () => http.put.mock.calls.map((c) => String((c as unknown[])[0]));
const levelPuts = () => putUrls().filter((u) => u.includes('reorder-levels'));

const modalProduct = (over: Record<string, unknown> = {}) => ({
  id: 'P-FRAME', sku: 'S', name: 'N', brand: 'B', currentStock: 1,
  reorderPoint: 2, reorderQuantity: 3, maxStock: 50, leadTimeDays: 7, ...over,
});
const renderModal = (product = modalProduct(), onSave = vi.fn(async () => {})) => {
  render(<ReorderPointModal isOpen onClose={() => {}} onSave={onSave} product={product} />);
  return onSave;
};
/** A browser reports value '' and validity.badInput for text like "1e". */
const typeBadText = (input: HTMLElement) => {
  Object.defineProperty(input, 'validity', { value: { badInput: true }, configurable: true });
  fireEvent.change(input, { target: { value: '' } });
};

beforeEach(() => {
  for (const fn of [...Object.values(http), ...Object.values(toast)]) fn.mockClear();
  auth.store = 'BV-DHN-02';
  auth.role = 'ADMIN';
  feed.rows = [{ _id: 'P-FRAME', quantity: 1, reorder_point: 2, stock_status: 'low' }];
  feed.ledger = [{
    product_id: 'P-FRAME', sku: 'S', name: 'Carrera CA8895', brand: 'Carrera', category: 'FR',
    quantity: 1, status: 'AVAILABLE', reorder_quantity: 3,
  }];
});

describe('3. modal band is the server band', () => {
  it('level 2, 1 on hand, server says low: the badge says Low (a client band would say Critical)', () => {
    renderModal(modalProduct({ stockStatus: 'low' }));
    expect(screen.getByText('Low', { selector: 'span' })).toBeTruthy();
    expect(screen.queryByText('Critical', { selector: 'span' })).toBeNull();
  });

  it('a level being typed has no verdict until it is saved', () => {
    renderModal(modalProduct({ stockStatus: 'critical' }));
    expect(screen.getByText('Critical', { selector: 'span' })).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText('not set'), { target: { value: '9' } });
    expect(screen.queryByText('Critical', { selector: 'span' })).toBeNull();
    expect(screen.queryByText('Low', { selector: 'span' })).toBeNull();
  });
});

describe('11. an invalid typed level is refused: toast, no PUT', () => {
  it('modal', async () => {
    const onSave = renderModal();
    fireEvent.change(screen.getByPlaceholderText('not set'), { target: { value: '1e2' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(onSave).not.toHaveBeenCalled();
  });

  it('shop level editor', async () => {
    render(<ShopReorderLevel productId="P-FRAME" storeId="BV-DHN-02" level={2} canEdit onSaved={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /change it/i }));
    fireEvent.change(screen.getByLabelText('Reorder level'), { target: { value: '-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(levelPuts()).toEqual([]);
  });
});

describe('12. ShopReorderLevel keeps the 36px control height', () => {
  it('the edit link and the input carry min-h-[36px]', () => {
    const view = render(
      <ShopReorderLevel productId="P-FRAME" storeId="BV-DHN-02" level={2} canEdit onSaved={() => {}} />,
    );
    expect(screen.getByRole('button', { name: /change it/i }).className).toContain('min-h-[36px]');
    fireEvent.click(screen.getByRole('button', { name: /change it/i }));
    expect(screen.getByLabelText('Reorder level').className).toContain('min-h-[36px]');
    view.unmount();
  });
});

describe('15. malformed number text is invalid, never a blank that clears the level', () => {
  it('modal', async () => {
    const onSave = renderModal();
    typeBadText(screen.getByPlaceholderText('not set'));
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(onSave).not.toHaveBeenCalled();
  });

  it('shop level editor', async () => {
    render(<ShopReorderLevel productId="P-FRAME" storeId="BV-DHN-02" level={2} canEdit onSaved={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /change it/i }));
    typeBadText(screen.getByLabelText('Reorder level'));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(levelPuts()).toEqual([]);
  });
});

describe('round 5', () => {
  it('3. saving blank on a shop whose level is not set sends no PUT and closes the editor', async () => {
    render(<ShopReorderLevel productId="P-FRAME" storeId="BV-DHN-02" level={null} canEdit onSaved={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /change it/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByLabelText('Reorder level')).toBeNull());
    expect(levelPuts()).toEqual([]);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('4. a typed value never moves to another shop when storeId changes', async () => {
    const view = render(
      <ShopReorderLevel productId="P-FRAME" storeId="S1" level={null} canEdit onSaved={() => {}} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /change it/i }));
    fireEvent.change(screen.getByLabelText('Reorder level'), { target: { value: '9' } });
    view.rerender(<ShopReorderLevel productId="P-FRAME" storeId="S2" level={7} canEdit onSaved={() => {}} />);
    expect(screen.queryByLabelText('Reorder level')).toBeNull();
    expect(levelPuts()).toEqual([]);
    expect(screen.getByRole('button', { name: /change it/i }).textContent).toContain('7');
  });
});
