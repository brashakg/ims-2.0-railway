// ============================================================================
// Reorder screens - review round 4 (F73, owner D12)
// ============================================================================
// 3.  The Reorder modal shows the SERVER's band for the saved level, no bands
//     of its own.
// 5.  The Reorder dashboard shows the server's on-hand quantity, not a re-sum.
// 11. Each screen refuses an invalid typed level: toast, no PUT.
// 12. ShopReorderLevel controls keep the 36px min height.
// 14. The dashboard sends the level only when it changed and a shop is active.
// 15. Malformed number text (validity.badInput) is invalid, never a blank that
//     clears the level.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

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

import { ReorderDashboard } from '../ReorderDashboard';
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
  feed.rows = [{ _id: 'P-FRAME', quantity: 1, reorder_point: 2, stock_status: 'low', top_up_qty: 2 }];
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

describe('5. dashboard shows the server on-hand', () => {
  it('feed says 3 on hand, ledger units sum to 5: the screen shows 3', async () => {
    feed.rows = [{ _id: 'P-FRAME', quantity: 3, reorder_point: 6, stock_status: 'low', top_up_qty: 4 }];
    feed.ledger = Array.from({ length: 5 }, () => ({
      product_id: 'P-FRAME', sku: 'SKU-P-FRAME', name: 'Carrera CA8895', brand: 'Carrera',
      category: 'FR', quantity: 1, status: 'AVAILABLE',
    }));
    render(<MemoryRouter><ReorderDashboard /></MemoryRouter>);
    await screen.findByText('Carrera CA8895');
    expect(screen.getAllByText('3', { selector: 'span.font-medium' }).length).toBeGreaterThan(0);
    expect(screen.queryByText('5', { selector: 'span.font-medium' })).toBeNull();
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

describe('14. the dashboard sends the level only when it changed and a shop is active', () => {
  const openModalWithNoShop = async () => {
    const view = render(<MemoryRouter><ReorderDashboard /></MemoryRouter>);
    fireEvent.click(await screen.findByTitle('Configure reorder point'));
    auth.store = undefined; // the admin switches to "no shop"
    view.rerender(<MemoryRouter><ReorderDashboard /></MemoryRouter>);
    await screen.findByRole('alert');
  };

  it('no shop, level untouched: the product-wide save goes through with no Pick-a-shop error', async () => {
    await openModalWithNoShop();
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(putUrls()).toContain('/products/P-FRAME'));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(levelPuts()).toEqual([]);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('an untouched level is not re-sent even with a shop', async () => {
    render(<MemoryRouter><ReorderDashboard /></MemoryRouter>);
    fireEvent.click(await screen.findByTitle('Configure reorder point'));
    fireEvent.click(await screen.findByRole('button', { name: /save/i }));
    await waitFor(() => expect(putUrls()).toContain('/products/P-FRAME'));
    expect(levelPuts()).toEqual([]);
  });

  it('a changed level with a shop is sent', async () => {
    render(<MemoryRouter><ReorderDashboard /></MemoryRouter>);
    fireEvent.click(await screen.findByTitle('Configure reorder point'));
    fireEvent.change(await screen.findByPlaceholderText('not set'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(levelPuts().length).toBe(1));
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
