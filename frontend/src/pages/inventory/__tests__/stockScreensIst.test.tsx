// ============================================================================
// Audit F53 (2026-09-29): stock screens must show shop time (IST).
// ============================================================================
// The backend stamps NAIVE timestamps that are UTC (Railway runs in UTC). A
// raw `new Date(s).toLocaleString()` reads them as the browser's local time,
// so the stock-count session said "started 10:44:43 am" when the shop clock
// read 16:14. Every stock screen formats through utils/datetime (the ONE IST
// helper), so 05:14 UTC must read 10:44 am whatever the viewer's timezone.

import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const NAIVE_UTC = '2026-09-17T05:14:43'; // = 10:44 am IST

vi.mock('../../../services/api', () => ({
  inventoryApi: {
    getMovements: vi.fn(async () => ({
      items: [
        {
          id: 'RECEIVED:G1:P1:0',
          at: NAIVE_UTC,
          type: 'RECEIVED',
          product_id: 'P1',
          product_name: 'Ray-Ban RB3025',
          sku: 'RB3025',
          qty: 17,
          ref: 'RCPT/BV-DHN-02/26-27/0003',
          ref_id: 'G1',
          store_id: 'S1',
          detail: 'GRN RCPT/BV-DHN-02/26-27/0003',
        },
      ],
      total: 1,
      has_more: false,
    })),
    getStockCounts: vi.fn(async () => ({
      counts: [
        {
          count_id: 'C1',
          audit_number: 'AUD-1',
          status: 'in_progress',
          created_at: NAIVE_UTC,
          created_by_name: 'Manager',
          items_counted: 0,
        },
      ],
    })),
  },
}));

vi.mock('../InventoryLayout', () => ({
  useInventoryContext: () => ({ storeId: 'S1' }),
}));

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'm1', activeStoreId: 'S1', roles: ['STORE_MANAGER'] } }),
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { InventoryMovementsPage } from '../InventoryMovementsPage';
import StockAuditPage from '../StockAudit';

describe('stock screens show IST (F53)', () => {
  it('Movements shows a receipt at its IST time', async () => {
    render(<InventoryMovementsPage />);
    await waitFor(() => expect(screen.getByText('Ray-Ban RB3025')).toBeTruthy());
    expect(document.body.textContent).toMatch(/10:44\s*am/i);
  });

  it('the stock-count session start is IST, not UTC', async () => {
    render(<StockAuditPage />);
    const line = await screen.findByText(/AUD-1 · started/);
    expect(line.textContent).toMatch(/10:44\s*am/i);
    expect(line.textContent).not.toMatch(/5:14/);
  });
});
