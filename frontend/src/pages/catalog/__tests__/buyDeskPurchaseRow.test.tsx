// ============================================================================
// IMS 2.0 - Buy Desk row "Purchase" opens a draft PO for THAT product (F61)
// ============================================================================
// The row button was a plain link to /purchase that carried no product. It now
// opens the draft form with that row already on it -- for a role that may
// raise a PO; owner 2026-10-08: the catalogue manager raises none.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));
const auth = vi.hoisted(() => ({ roles: ['STORE_MANAGER'] as string[] }));
vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { activeStoreId: 'BV-DHN-02', roles: auth.roles },
    hasRole: (r: string | string[]) => auth.roles.some((x) => [r].flat().includes(x)),
  }),
}));
vi.mock('../../../services/api', () => ({
  vendorsApi: {
    getVendors: vi.fn().mockResolvedValue({ vendors: [] }),
    createPurchaseOrder: vi.fn(),
  },
}));
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: { getLastCost: vi.fn().mockResolvedValue({ costs: {} }) },
}));
vi.mock('../../../services/api/buyDesk', () => ({
  buyDeskApi: {
    getRows: vi.fn().mockResolvedValue({
      rows: [
        {
          product_id: 'p1',
          sku: 'CA8895-807',
          name: 'Carrera CA8895',
          brand: 'Carrera',
          category: 'FRAME',
          catalog_status: 'ACTIVE',
          readiness: { complete: true, missing: [], blockers: [], purchasable: true },
          ecom_state: 'NOT_LISTED',
          on_hand: 0,
          on_order: 3,
          in_draft: 4,
          buy_signal: 2,
          purchasable: true,
          hsn_code: '900311',
          gst_rate: 5,
        },
      ],
    }),
  },
}));

import BuyDeskPage from '../BuyDeskPage';

beforeEach(() => {
  auth.roles = ['STORE_MANAGER'];
});

describe('Buy Desk row Purchase (F61)', () => {
  it('opens the draft PO form with that product, not a link to /purchase', async () => {
    const { container } = render(
      <MemoryRouter>
        <BuyDeskPage />
      </MemoryRouter>,
    );
    await screen.findByText('Carrera CA8895');
    expect(container.querySelector('a[href="/purchase"]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^purchase$/i }));
    expect(await screen.findByText(/create draft po · 1 product/i)).toBeInTheDocument();
  });

  it('gives the catalogue manager no Purchase that opens a draft (owner 2026-10-08)', async () => {
    auth.roles = ['CATALOG_MANAGER'];
    render(
      <MemoryRouter>
        <BuyDeskPage />
      </MemoryRouter>,
    );
    await screen.findByText('Carrera CA8895');
    expect(screen.queryByRole('button', { name: /^purchase$/i })).toBeNull();
    expect(screen.queryByText(/create draft po/i)).toBeNull();
  });
});

describe('Buy Desk on order vs in draft (owner D11, 2026-09-29)', () => {
  it('shows only what was sent as on order, and drafts apart as "in draft"', async () => {
    render(
      <MemoryRouter>
        <BuyDeskPage />
      </MemoryRouter>,
    );
    await screen.findByText('Carrera CA8895');
    const cell = screen.getByText('4 in draft').closest('td');
    expect(cell?.textContent).toBe('34 in draft');
  });
});
