// ============================================================================
// S4: a product's page -- one average cost, its history, no per-piece cost
// ============================================================================
// Owner MCQ 2026-10-23: one average cost per product; the product page shows
// the COST HISTORY (each bill: date, pieces, price, new average), never a
// per-piece cost. What we pay is SUPERADMIN / ADMIN / ACCOUNTANT only.

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const CARD = {
  product_id: 'P1',
  pieces: 5,
  average_cost: 3395,
  stock_value: 16975,
  uncosted_pieces: 0,
  history: [
    { key: 'B2', kind: 'BILL', at: '2026-10-21T10:00:00+00:00', by: 'u-p', by_name: 'Priya', bill_number: 'SAF/24-25/8841',
      vendor_name: 'Safilo', qty: 3, price: 3450, method: 'AVERAGE', old_cost: 3340, new_cost: 3395 },
    { key: 'correction:x', kind: 'CORRECTION', at: '2026-08-02T10:00:00+00:00', by: 'u-p', by_name: 'Priya',
      old_cost: 3520, new_cost: 3340, reason: 'Safilo credit note for scheme discount' },
  ],
};

const box = vi.hoisted(() => ({ correct: vi.fn(), getCost: vi.fn() }));
vi.mock('../../../services/api', () => ({
  productApi: { getProduct: vi.fn(async () => ({ product_id: 'P1', name: 'Carrera 1234 Havana 55', sku: 'CAR-1234-086-55' })) },
  inventoryApi: {
    getUnits: vi.fn(async () => ({
      units: [
        { stock_id: 's1', barcode: 'BV24CA0101P4', status: 'AVAILABLE', in_shop: true, barcode_printed: true, location_code: 'C1', cost_price: 3395 },
        { stock_id: 's2', barcode: 'BV24CA0103R5', status: 'RESERVED', in_shop: true, barcode_printed: false, location_code: 'C1', cost_price: 3395 },
        { stock_id: 's3', barcode: 'BV24CA0100X1', status: 'SOLD', in_shop: false, barcode_printed: true, location_code: 'C1' },
      ],
      total: 3,
    })),
    getProductCost: (...a: unknown[]) => (box.getCost as any)(...a),
    correctProductCost: (...a: unknown[]) => (box.correct as any)(...a),
  },
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }),
}));
const auth = vi.hoisted(() => ({ role: 'ACCOUNTANT' }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u', activeStoreId: 'S1', roles: [auth.role] },
    hasRole: (roles: string[]) => roles.includes(auth.role),
  }),
}));

import { ProductStockPage } from '../ProductStockPage';

function renderAt() {
  return render(
    <MemoryRouter initialEntries={['/inventory/stock/P1']}>
      <Routes>
        <Route path="/inventory/stock/:productId" element={<ProductStockPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  auth.role = 'ACCOUNTANT';
  box.getCost.mockReset().mockResolvedValue(CARD);
  box.correct.mockReset().mockResolvedValue({ ...CARD, average_cost: 3400, stock_value: 17000 });
});

describe('a product page under one average cost', () => {
  it('shows the average, the stock value rule and the cost history -- never a per-piece cost', async () => {
    renderAt();
    expect(await screen.findByText('Carrera 1234 Havana 55')).toBeInTheDocument();
    expect(screen.getByText('₹3,395')).toBeInTheDocument();
    expect(screen.getByText('₹16,975')).toBeInTheDocument();
    expect(await screen.findByText(/Stock value = pieces in every shop × average cost \(5 × ₹3,395\)/)).toBeInTheDocument();
    expect(screen.getByText(/bill SAF\/24-25\/8841 \(Safilo\) accepted by Priya: 3 pieces at ₹3,450 → average ₹3,395/)).toBeInTheDocument();
    expect(screen.getByText(/Priya: average ₹3,520 → ₹3,340 · reason "Safilo credit note/)).toBeInTheDocument();
    // Pieces in this shop: the two standing ones, no cost column.
    expect(screen.getByText('BV24CA0101P4')).toBeInTheDocument();
    expect(screen.queryByText('BV24CA0100X1')).not.toBeInTheDocument();
    expect(screen.getByText('Reserved · customer order')).toBeInTheDocument();
  });

  it('labels the shop count and the every-shop count apart', async () => {
    renderAt();
    expect(await screen.findByText('Pieces in every shop')).toBeInTheDocument();
    const thisShop = screen.getAllByText('Pieces in this shop')[0].parentElement as HTMLElement;
    expect(thisShop.textContent).toContain('2');
  });

  it('a cost-card failure keeps the page and offers a retry', async () => {
    box.getCost.mockRejectedValueOnce(new Error('503'));
    renderAt();
    expect(await screen.findByText('BV24CA0101P4')).toBeInTheDocument();
    expect(screen.queryByText('Product not found')).not.toBeInTheDocument();
    expect(await screen.findByText(/The cost could not be loaded/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('₹16,975')).toBeInTheDocument();
  });

  it('words an average carried over from before S4', async () => {
    box.getCost.mockResolvedValue({ ...CARD, history: [
      { key: 'backfill:r', kind: 'CARRIED_OVER', at: '2026-10-30T10:00:00+00:00', by: 'script', old_cost: null, new_cost: 3395 },
    ] });
    renderAt();
    expect(await screen.findByText('Carried over')).toBeInTheDocument();
    expect(screen.getByText(/average ₹3,395 as it stood before bill-by-bill averaging began/)).toBeInTheDocument();
  });

  it('says the figure is too low while pieces have no cost', async () => {
    box.getCost.mockResolvedValue({ ...CARD, average_cost: null, stock_value: 0, uncosted_pieces: 5, history: [] });
    renderAt();
    expect(await screen.findByText(/5 pieces have no cost yet, so this figure is too low/)).toBeInTheDocument();
  });

  it('saves a correction only with a reason', async () => {
    renderAt();
    await screen.findByText('Correct the average cost');
    fireEvent.change(screen.getByLabelText('New average cost (excl. GST)'), { target: { value: '3400' } });
    fireEvent.click(screen.getByRole('button', { name: /save correction/i }));
    expect(box.correct).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Reason (required)'), { target: { value: 'scheme credit note' } });
    fireEvent.click(screen.getByRole('button', { name: /save correction/i }));
    await waitFor(() => expect(box.correct).toHaveBeenCalledWith('P1', 3400, 'scheme credit note'));
    expect(await screen.findByText('₹17,000')).toBeInTheDocument();
  });

  it.each(['STORE_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF'])('%s sees the pieces, no cost', async (role) => {
    auth.role = role;
    renderAt();
    expect(await screen.findByText('BV24CA0101P4')).toBeInTheDocument();
    expect(box.getCost).not.toHaveBeenCalled();
    expect(screen.queryByText('₹3,395')).not.toBeInTheDocument();
    expect(screen.queryByText('Cost history')).not.toBeInTheDocument();
    expect(screen.queryByText('Correct the average cost')).not.toBeInTheDocument();
  });
});
