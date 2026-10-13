// ============================================================================
// The customer's tracking page shows a shipped online order on its Shipped step
// ============================================================================
// A fulfilled online order is SHIPPED until the courier delivers it (owner
// ruling 2026-09-28). The page's step list had no SHIPPED: the status read the
// raw word and every step was grey with no "current" marker, as if the order
// had not even been placed. Only the network client is stubbed.

import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

const H = vi.hoisted(() => ({ trackOrder: vi.fn() }));

vi.mock('../../../services/api/portal', () => ({
  portalApi: { trackOrder: H.trackOrder },
}));

import OrderTrackingPage from '../OrderTrackingPage';

function open(status: string) {
  H.trackOrder.mockResolvedValue({
    order_number: 'BV-1', status, status_history: [{ status, timestamp: '2026-09-30T10:00:00Z' }],
    expected_delivery: null, delivery_priority: null, placed_at: null, item_count: 1, items: [],
    customer_first_name: null, store_name: null, store_phone: null,
  });
  render(
    <MemoryRouter initialEntries={['/track/tok']}>
      <Routes><Route path="/track/:token" element={<OrderTrackingPage />} /></Routes>
    </MemoryRouter>,
  );
}

describe('OrderTrackingPage shipped step', () => {
  it('reads Shipped and marks it the current step', async () => {
    open('SHIPPED');
    const step = (await screen.findByText('current')).closest('p');
    expect(step?.textContent).toContain('Shipped');
    expect(screen.getAllByText('Shipped')).toHaveLength(2); // the status, and the step
    expect(screen.queryByText('SHIPPED')).toBeNull();
  });

  it('shows no Shipped step on an order that never ships', async () => {
    open('DELIVERED');
    expect((await screen.findByText('current')).closest('p')?.textContent).toContain('Delivered');
    expect(screen.queryByText('Shipped')).toBeNull();
  });
});
