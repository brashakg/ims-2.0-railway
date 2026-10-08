// ============================================================================
// IMS 2.0 - Vendor Returns without prices (owner ruling 2026-09-29)
// ============================================================================
// WORKSHOP_STAFF reads vendor returns and debit notes with the money keys
// stripped server-side (services/cost_mask). The screen must render the item,
// quantity and reason with "-" for money -- not crash on the missing
// total_value / unit_price, and not print a rupee figure.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn() }));
const dnMock = vi.hoisted(() => ({ list: vi.fn() }));

vi.mock('../../../services/api/client', () => ({ default: apiMock }));
vi.mock('../../../services/api/rtvDebitNotes', () => ({ rtvDebitNotesApi: dnMock }));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
let roles: string[] = ['WORKSHOP_STAFF'];
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-TEST-01', roles } }),
}));

import { VendorReturns } from '../VendorReturns';

const line = { product_id: 'P1', product_name: 'RB Frame', quantity: 2, reason: 'defective' };
const ret = {
  return_id: 'VR1',
  vendor_id: 'V1',
  vendor_name: 'Acme',
  store_id: 'BV-TEST-01',
  return_type: 'credit_note',
  status: 'approved',
  credit_note_number: null,
  created_at: '2026-09-29T10:00:00',
  created_by: 'u1',
  notes: '',
};

function load(returns: unknown[], notes: unknown[]) {
  apiMock.get.mockImplementation((url: string) =>
    Promise.resolve({ data: url.startsWith('/vendor-returns') ? { returns } : { vendors: [] } })
  );
  dnMock.list.mockResolvedValue({ debit_notes: notes, total: notes.length });
}

async function openReturn() {
  render(
    <MemoryRouter>
      <VendorReturns />
    </MemoryRouter>
  );
  fireEvent.click(await screen.findByText('Acme'));
}

describe('VendorReturns money display', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    roles = ['WORKSHOP_STAFF'];
  });

  it('renders a masked return (no prices) with dashes, never a rupee figure', async () => {
    load(
      [{ ...ret, items: [line] }],
      [{ debit_note_id: 'DN-1', debit_note_number: 'DN/26-27/0001', rtv_ref_id: 'VR1', vendor: { name: 'Acme' }, lines: [] }]
    );
    await openReturn();
    expect(screen.getByText('RB Frame')).toBeTruthy();
    expect(screen.getByText(/Qty: 2/)).toBeTruthy();
    expect(screen.getByText('DN/26-27/0001')).toBeTruthy();
    expect(document.body.textContent).not.toContain('₹');
  });

  // A line's price per piece is the supplier credit per piece (value = qty x
  // price): the accounts roles' alone (payables_mask, panel 2026-10-08).
  it('shows the prices to the accounts roles', async () => {
    roles = ['ACCOUNTANT'];
    load(
      [{ ...ret, items: [{ ...line, unit_price: 3173.37 }], total_value: 6346.74, credit_note_amount: null }],
      []
    );
    await openReturn();
    expect(document.body.textContent).toContain('₹3,173.37');
    expect(document.body.textContent).toContain('₹6,346.74');
  });

  it('keeps them off the screen for anyone else, whatever the server sends', async () => {
    load(
      [{ ...ret, items: [{ ...line, unit_price: 3173.37 }], total_value: 6346.74, credit_note_amount: null }],
      []
    );
    await openReturn();
    expect(screen.getByText(/Qty: 2/)).toBeTruthy();
    expect(document.body.textContent).not.toContain('₹');
  });
});
