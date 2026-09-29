// ============================================================================
// New transfer, step 2: "Search by name, SKU, or barcode" finds a unit's code
// ============================================================================
// The box promised barcode but matched only product name and SKU, so the code
// on a unit's label ('BV0000000042', or an old 'BV--91FA3858') gave an empty
// list. The stock ledger rows now carry the codes of the units on hand.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'U1', activeStoreId: 'BV-DHN-02', roles: ['STORE_MANAGER'] } }),
}));
// One toast object for every render, as the real provider gives: the modal's
// search callback depends on it, so a fresh object per render re-runs it forever.
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));

const ROWS = [
  {
    id: 'P1', product_id: 'P1', name: 'Carrera CA 8895', sku: 'FR-CARRERA-8895-807',
    brand: 'Carrera', stock: 2, reserved_quantity: 0, unit_barcodes: ['BV0000000042', 'BV0000000043'],
  },
  {
    id: 'P2', product_id: 'P2', name: 'Wayfarer', sku: 'FR-RAYB-2140-BLK',
    brand: 'Ray-Ban', stock: 1, reserved_quantity: 0, unit_barcodes: ['BV--91FA3858'],
  },
];

vi.mock('../../../services/api', () => ({
  inventoryApi: { getStock: vi.fn(async () => ({ items: ROWS, total: ROWS.length })) },
}));
vi.mock('../../../services/api/stores', () => ({
  storeApi: {
    getStores: vi.fn(async () => [
      { store_id: 'BV-DHN-02', store_name: 'Dhanbad 2' },
      { store_id: 'BV-BOK-01', store_name: 'Bokaro', store_code: 'BOK' },
    ]),
  },
}));

import { StockTransferModal } from '../StockTransferModal';

async function openItemsStep() {
  render(<StockTransferModal isOpen onClose={() => {}} onTransferCreated={() => {}} />);
  const dest = screen.getByLabelText('Destination Store');
  await waitFor(() => expect(dest.querySelectorAll('option').length).toBe(2));
  fireEvent.change(dest, { target: { value: 'BV-BOK-01' } });
  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
}

describe('New transfer: item search by a unit code', () => {
  it.each([
    ['bv0000000043', 'Carrera CA 8895', 'Wayfarer'],
    ['BV--91FA3858', 'Wayfarer', 'Carrera CA 8895'],
  ])('%s finds its product', async (code, shown, hidden) => {
    await openItemsStep();
    fireEvent.change(screen.getByPlaceholderText('Search by name, SKU, or barcode...'), {
      target: { value: code },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(await screen.findByText(shown)).toBeInTheDocument();
    expect(screen.queryByText(hidden)).not.toBeInTheDocument();
  });
});
