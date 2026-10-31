// ============================================================================
// IMS 2.0 - Website 1: a SIMULATED booking is labelled as one
// ============================================================================
// A dry-run Shiprocket booking (DISPATCH_MODE off / no courier creds) carries a
// fake AWB and changes nothing - the card must not read like a real shipment.

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../../services/api/shipping', () => ({
  shippingApi: {
    book: vi.fn(),
    list: vi.fn().mockResolvedValue({
      shipments: [
        {
          shipment_id: 'SHP-1',
          order_id: 'ORD-W1',
          awb: 'SIMSR123',
          courier: 'SIMULATED',
          status: 'SIMULATED',
          tracking_status: 'SIMULATED',
          simulated: true,
        },
      ],
      total: 1,
    }),
    track: vi.fn(),
  },
}));

vi.mock('../../../context/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

import { OrderShippingCard } from '../OrderShippingCard';

describe('OrderShippingCard simulated booking', () => {
  it('labels a simulated booking as not shipped', async () => {
    render(<OrderShippingCard orderId="ORD-W1" orderNumber="INV-7001" />);
    expect(await screen.findByText('Simulated - not shipped')).toBeTruthy();
  });
});
