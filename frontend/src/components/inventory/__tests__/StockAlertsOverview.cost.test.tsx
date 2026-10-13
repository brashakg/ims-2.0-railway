// Stock alerts: the dead-stock / restock value and each alert's financial
// impact are worked out from cost, so they show to the managers only (owner
// ruling D7). Workshop staff open this screen too; for them the server leaves
// the keys out and the tiles are not drawn (never "NaN", never a rupee figure).

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { UserRole } from '../../../types';

const apiMock = vi.hoisted(() => ({ getStockAlerts: vi.fn() }));
const auth = vi.hoisted(() => ({ role: 'WORKSHOP_STAFF' as string }));

vi.mock('../../../services/api/inventory', () => ({ inventoryApi: apiMock }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { activeStoreId: 'BV-TEST-01', roles: [auth.role] },
    hasRole: (r: UserRole | UserRole[]) => (Array.isArray(r) ? r : [r]).includes(auth.role as UserRole),
  }),
}));

import { StockAlertsOverview } from '../StockAlertsOverview';

const alert = {
  id: 'A1',
  sku: 'SKU-1',
  productName: 'Aviator',
  brand: 'Ray-Ban',
  category: 'FRAME',
  currentStock: 4,
  reorderPoint: null,
  safetyStock: 0,
  projectedDaysToStockout: 0,
  alertType: 'DEAD_STOCK',
  severity: 'LOW',
  actionRequired: 'No sales in 120 days - consider clearance',
};
const counts = { totalAlerts: 1, critical: 0, high: 0, medium: 0, low: 1 };

describe('StockAlertsOverview cost figures', () => {
  beforeEach(() => vi.clearAllMocks());

  // Owner ruling 2026-10-12: the managers see no value at cost either.
  it.each(['WORKSHOP_STAFF', 'STORE_MANAGER', 'AREA_MANAGER', 'CATALOG_MANAGER'])(
    '%s see the alert and its units, no value at cost', async (role) => {
    auth.role = role;
    apiMock.getStockAlerts.mockResolvedValue({ alerts: [alert], stats: counts });
    render(<StockAlertsOverview />);
    expect(await screen.findByText('Aviator')).toBeTruthy();
    expect(screen.getByText('4 units')).toBeTruthy();
    expect(screen.queryByText('Dead Stock Value')).toBeNull();
    expect(screen.queryByText('Restock Value')).toBeNull();
    expect(screen.queryByText(/Financial Impact/)).toBeNull();
    expect(document.body.textContent).not.toContain('NaN');
    expect(document.body.textContent).not.toContain('₹');
  });

  it('accounts still see the value at cost', async () => {
    auth.role = 'ACCOUNTANT';
    apiMock.getStockAlerts.mockResolvedValue({
      alerts: [{ ...alert, costImpact: 17287 }],
      stats: { ...counts, deadStockValue: 17287, recommendedRestockValue: 0 },
    });
    render(<StockAlertsOverview />);
    expect(await screen.findByText('Aviator')).toBeTruthy();
    expect(screen.getByText('Dead Stock Value')).toBeTruthy();
    expect(screen.getByText('₹17K')).toBeTruthy();
    expect(screen.getByText(/Financial Impact/)).toBeTruthy();
  });
});
