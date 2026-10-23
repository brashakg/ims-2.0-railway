// ============================================================================
// Stock counts: the missing stock's value at cost is for accounts only
// ============================================================================
// Owner ruling 2026-10-12: stock at cost is what we pay, COST_ROLES only. A
// store or area manager's "Stock missing" tile counts the units short; an
// accountant's reads the rupees at cost. Fed a value the server would never
// send a manager, so a widened role check shows here.

import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../../services/api', () => ({
  inventoryApi: {
    getStockCounts: vi.fn(async () => ({
      counts: [
        {
          count_id: 'C1',
          audit_number: 'AUD-1',
          status: 'completed',
          created_at: '2026-10-12T05:14:43',
          created_by_name: 'Manager',
          items_counted: 3,
          shrinkage_units: 2,
          shrinkage_value: 8643.54,
        },
      ],
    })),
  },
}));

vi.mock('../InventoryLayout', () => ({
  useInventoryContext: () => ({ storeId: 'S1' }),
}));

const auth = vi.hoisted(() => ({ role: 'STORE_MANAGER' }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'm1', activeStoreId: 'S1', roles: [auth.role] },
    hasRole: (roles: string[]) => roles.includes(auth.role),
  }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import StockAuditPage from '../StockAudit';

async function missingTile() {
  render(<MemoryRouter><StockAuditPage /></MemoryRouter>);
  const label = await screen.findByText('Stock missing');
  return label.parentElement!.querySelector('.v')!.textContent;
}

describe('Stock counts - stock at cost follows the accounts rule', () => {
  it.each([['STORE_MANAGER'], ['AREA_MANAGER']])('%s: units short, no rupees at cost', async (role) => {
    auth.role = role;
    expect(await missingTile()).toBe('2');
    expect(screen.queryByText(/8,644/)).toBeNull();
  });

  it.each([['ACCOUNTANT'], ['ADMIN']])('%s: the rupees at cost', async (role) => {
    auth.role = role;
    expect(await missingTile()).toBe('₹8,644');
  });
});
