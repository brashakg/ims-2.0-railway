// Audit F54 (verifier round 4): stock whose arrival date is unknown (legacy
// units with no created_at) is OLD by the server's one rule -- class C, the
// 180+ bucket, daysInStock null. The screen used to coerce null to 0, so the
// same row read "Slow Mover" beside "0d", and the Old Stock / Avg Age tiles
// counted it as brand new.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const getAgingReport = vi.fn();

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', roles: ['STORE_MANAGER'], activeStoreId: 'S1' } }),
}));
vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ error: () => {}, success: () => {}, warning: () => {}, info: () => {} }),
}));
vi.mock('../../../services/api', () => ({
  inventoryApi: { getAgingReport: (...a: unknown[]) => getAgingReport(...a) },
}));

import { StockAgingReport } from '../StockAgingReport';

const row = (id: string, daysInStock: number | null, classification: string, ageCategory: string) => ({
  id,
  sku: id,
  name: `Frame ${id}`,
  brand: 'RB',
  category: 'FRAME',
  quantity: 1,
  value: 1000,
  daysInStock,
  salesLast30Days: 0,
  salesLast90Days: 0,
  turnoverRate: 0,
  classification,
  ageCategory,
});

describe('StockAgingReport - unknown stock age', () => {
  it('shows legacy stock as old, never as 0 days', async () => {
    getAgingReport.mockResolvedValue({
      products: [row('LEGACY', null, 'C', '180+'), row('FRESH', 10, 'NEW', '0-30')],
    });
    render(<StockAgingReport />);

    expect(await screen.findByText('Unknown')).toBeInTheDocument();
    expect(screen.queryByText('0d')).not.toBeInTheDocument();
    // Old Stock (90d+) counts the legacy row; Avg Age averages known ages only.
    const tile = (label: string) => screen.getByText(label).nextElementSibling?.textContent;
    expect(tile('Old Stock (90d+)')).toBe('1');
    expect(tile('Avg Age')).toBe('10d');
  });
});
