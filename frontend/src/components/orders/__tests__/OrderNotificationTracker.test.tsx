// ============================================================================
// IMS 2.0 - Order Notification Tracker shows a SHIPPED order's real progress
// ============================================================================
// An online order Shopify fulfilled is SHIPPED until the courier delivers it
// (owner ruling 2026-09-28). The tracker had no SHIPPED step: every step read
// "pending" and none "Current step", as if nothing had happened yet.

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { OrderNotificationTracker } from '../OrderNotificationTracker';
import type { OrderStatus } from '../../../types';

function tracker(status: OrderStatus) {
  render(
    <OrderNotificationTracker
      orderId="o1"
      orderNumber="BV-1"
      customerName="Asha"
      customerPhone="9800000000"
      status={status}
      createdAt="2026-09-28T10:00:00"
    />,
  );
}

describe('OrderNotificationTracker', () => {
  it('shows a shipped order on its Shipped step', () => {
    tracker('SHIPPED');
    expect(screen.getByText('Shipped')).toBeTruthy();
    expect(screen.getAllByText('Current step')).toHaveLength(1);
  });

  it('never shows a Shipped step on an order that is not shipped', () => {
    tracker('DELIVERED');
    expect(screen.queryByText('Shipped')).toBeNull();
  });
});
