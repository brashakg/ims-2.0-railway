// ============================================================================
// Opening stock: quantities for every inventory role, a unit cost for accounts
// ============================================================================
// Owner 2026-10-18 (FU-23): what we paid is entered by the accounts roles only.
// The server refuses a unit_cost from anyone else, so the screen neither
// offers the column to them nor sends one; their units take the cost from
// the bill.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const auth = vi.hoisted(() => ({ role: 'STORE_MANAGER' }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { activeStoreId: 'BV-TEST-01', roles: [auth.role] } }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));
vi.mock('../../../context/ToastContext', () => ({ useToast: () => toast }));
const preview = vi.hoisted(() => vi.fn());
vi.mock('../../../services/api', () => ({
  inventoryApi: { previewOpeningStock: preview, commitOpeningStock: vi.fn() },
}));

import OpeningStockImport from '../OpeningStockImport';

const CSV = 'sku,quantity,unit_cost\nRB3025-001,12,3450';

async function previewAs(role: string) {
  auth.role = role;
  render(<OpeningStockImport />);
  fireEvent.change(screen.getByRole('textbox'), { target: { value: CSV } });
  fireEvent.click(screen.getByRole('button', { name: /preview/i }));
  await waitFor(() => expect(preview).toHaveBeenCalled());
  return preview.mock.calls[0][0];
}

describe('Opening stock cost column', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    preview.mockResolvedValue({
      rows: [{ index: 0, status: 'WILL_ADD', sku: 'RB3025-001', quantity: 12, message: 'Will add 12.' }],
      summary: { total_rows: 1, units_to_add: 12, rows_to_skip: 0, rows_with_errors: 0 },
    });
  });

  it.each(['STORE_MANAGER', 'AREA_MANAGER', 'CATALOG_MANAGER', 'WORKSHOP_STAFF'])(
    '%s enters quantities only: no cost column, no cost sent',
    async (role) => {
      const rows = await previewAs(role);
      expect(rows).toEqual([{ sku: 'RB3025-001', quantity: 12 }]);
      expect(toast.info).toHaveBeenCalledWith(expect.stringMatching(/unit_cost column was left out/));
      expect(screen.queryByText('unit_cost')).toBeNull();
      expect(screen.queryByRole('columnheader', { name: 'Cost' })).toBeNull();
    },
  );

  it.each(['ADMIN', 'SUPERADMIN', 'ACCOUNTANT'])('%s still enters the unit cost', async (role) => {
    const rows = await previewAs(role);
    expect(rows).toEqual([{ sku: 'RB3025-001', quantity: 12, unit_cost: 3450 }]);
    expect(screen.getByText('unit_cost')).toBeTruthy();
    expect(await screen.findByRole('columnheader', { name: 'Cost' })).toBeTruthy();
  });
});
