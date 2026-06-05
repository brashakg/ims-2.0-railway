import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';

// Mock the dashboard API module so the card renders without a network call.
vi.mock('../../../services/api/dashboard', () => ({
  dashboardApi: { getOwnerDigest: vi.fn() },
}));

import OwnerDigestCard from '../OwnerDigestCard';
import { dashboardApi } from '../../../services/api/dashboard';

const DIGEST = {
  date: '2026-06-06',
  store_id: null,
  today: {
    sales: 12345,
    collections: 10000,
    expenses: 2000,
    cash_net: 8000,
    orders: 7,
    new_customers: 3,
    pending_tasks: 2,
    low_stock: 4,
    out_of_stock: 1,
  },
  mtd: { sales: 500000, expenses: 50000, orders: 200 },
  expanded: {
    by_store: [{ store_id: 'BV-BOK-02', sales: 12345, orders: 7 }],
    payment_modes: { CASH: 6000, UPI: 4000 },
    low_stock_items: [{ name: 'Ray-Ban Aviator', sku: 'RB1', qty: 0, reorder_point: 5 }],
    pending_task_list: [{ title: 'Restock frames', priority: 'P2' }],
    staff: { present_today: 5, total_staff: 8 },
  },
};

const mockGet = dashboardApi.getOwnerDigest as unknown as ReturnType<typeof vi.fn>;

describe('OwnerDigestCard', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the today KPI strip (brief view) once loaded', async () => {
    mockGet.mockResolvedValue(DIGEST);
    render(<OwnerDigestCard />);
    expect(screen.getByText('Day at a glance')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/12,345/)).toBeInTheDocument());
    expect(screen.getByText('Sales')).toBeInTheDocument();
    expect(screen.getByText('Pending tasks')).toBeInTheDocument();
    expect(screen.getByText('Collected')).toBeInTheDocument();
  });

  it('reveals MTD + per-store + payment-mode detail when expanded', async () => {
    mockGet.mockResolvedValue(DIGEST);
    render(<OwnerDigestCard />);
    await waitFor(() => screen.getByText(/12,345/));
    fireEvent.click(screen.getByRole('button', { name: /Expand/i }));
    expect(screen.getByText('Month to date')).toBeInTheDocument();
    expect(screen.getByText('BV-BOK-02')).toBeInTheDocument();
    expect(screen.getByText('CASH')).toBeInTheDocument();
    expect(screen.getByText('Ray-Ban Aviator')).toBeInTheDocument();
  });

  it('renders nothing if the fetch fails (fail-soft, never breaks the Hub)', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    const { container } = render(<OwnerDigestCard />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('passes the active store id through to the API', async () => {
    mockGet.mockResolvedValue({ ...DIGEST, store_id: 'BV-BOK-02' });
    render(<OwnerDigestCard storeId="BV-BOK-02" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith('BV-BOK-02'));
  });
});
