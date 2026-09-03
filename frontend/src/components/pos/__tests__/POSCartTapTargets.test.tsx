// ============================================================================
// Cart line controls are fingertip-sized
// ============================================================================
// The quantity +/- were 22x22 on every surface -- half a fingertip on the shop
// iPads the tills run on. 44px is the floor for every tap target on the POS.

import { render, screen } from '@testing-library/react';
import { it, expect, beforeEach } from 'vitest';
import { CartSidebar } from '../POSCart';
import { usePOSStore } from '../../../stores/posStore';

beforeEach(() => {
  usePOSStore.getState().resetTransaction();
  usePOSStore.getState().addToCart({
    id: 'p-1',
    product_id: 'p-1',
    name: 'Titan Neo',
    quantity: 1,
    unit_price: 4500,
    price: 4500,
    mrp: 5000,
    tax_rate: 18,
  } as never);
});

it('gives the quantity +/- and remove controls a 44px floor', () => {
  render(<CartSidebar />);
  for (const name of ['Decrease quantity', 'Increase quantity', 'Remove Titan Neo']) {
    const el = screen.getByLabelText(name) as HTMLElement;
    expect(parseInt(el.style.minHeight, 10), `${name} height`).toBeGreaterThanOrEqual(44);
    expect(parseInt(el.style.minWidth, 10), `${name} width`).toBeGreaterThanOrEqual(44);
  }
});
