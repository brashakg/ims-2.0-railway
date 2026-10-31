// ============================================================================
// IMS 2.0 - Website 1: an online order shows WHERE it ships, and says so
// loudly when the IMS total is not what Shopify charged.
// ============================================================================

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { OnlineOrderFacts } from '../OnlineOrderFacts';

describe('OnlineOrderFacts', () => {
  it('shows the checkout delivery address', () => {
    render(
      <OnlineOrderFacts
        shippingAddress={{
          name: 'Ravi Kumar',
          address1: '12 MG Road',
          address2: 'Flat 3',
          city: 'Pune',
          province: 'Maharashtra',
          zip: '411001',
          phone: '+919800000000',
        }}
      />,
    );
    expect(screen.getByText('Ship to')).toBeTruthy();
    expect(screen.getByText('Ravi Kumar')).toBeTruthy();
    expect(screen.getByText('12 MG Road, Flat 3')).toBeTruthy();
    expect(screen.getByText('Pune, Maharashtra 411001')).toBeTruthy();
    expect(screen.getByText('+919800000000')).toBeTruthy();
  });

  it('flags a total that differs from what Shopify charged', () => {
    render(
      <OnlineOrderFacts
        totalsCheck={{ status: 'MISMATCH', shopifyTotal: 950, imsTotal: 999, difference: -49 }}
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toMatch(/Shopify charged/);
    expect(alert.textContent).toMatch(/950\.00/);
    expect(alert.textContent).toMatch(/999\.00/);
  });

  it('renders nothing for a matching in-store order', () => {
    const { container } = render(<OnlineOrderFacts totalsCheck={{ status: 'MATCH' }} />);
    expect(container.textContent).toBe('');
  });
});
