// ============================================================================
// IMS 2.0 - PurchaseOrderComposer: GST + expected-delivery-date (owner items
// 1, 2 and 4, 2026-08-26)
// ============================================================================
// Pins what the buyer actually SEES while raising a purchase order:
//   - a within-state purchase shows CGST + SGST; an inter-state one shows IGST
//   - an undecidable place of supply says so instead of showing a wrong split
//   - a line's GST comes from the picked PRODUCT, never a flat 18%
//   - a product with no GST rate is named on the screen, not silently taxed
//   - the delivery-date picker cannot offer a past date

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}));

vi.mock('../../../context/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../../services/api/inventory', () => ({
  vendorsApi: { getLastCost: vi.fn().mockResolvedValue({ costs: {} }) },
}));

import {
  PurchaseOrderComposer,
  applyPickedProduct,
  todayForDateInput,
} from '../PurchaseOrderComposer';
import type { ComposerLine, PurchaseOrderComposerProps } from '../PurchaseOrderComposer';

const LINE = (over: Partial<ComposerLine> = {}): ComposerLine => ({
  productId: 'prod-a',
  productName: 'Ray-Ban RB3025',
  sku: 'RB3025-GOLD',
  quantity: 2,
  unitCost: 1000,
  taxRate: 5,
  hsn: '900311',
  gstResolved: true,
  costTouched: true,
  lastPaid: null,
  ...over,
});

function renderComposer(over: Partial<PurchaseOrderComposerProps> = {}) {
  const props: PurchaseOrderComposerProps = {
    mode: 'modal',
    vendors: [{ id: 'v-1', name: 'Luxottica India', code: 'LUX' }],
    initialVendorId: 'v-1',
    initialLines: [LINE()],
    renderProductCell: ({ line }) => <div data-testid="cell">{line.productName}</div>,
    onSubmit: vi.fn().mockResolvedValue(undefined),
    ...over,
  };
  return render(<PurchaseOrderComposer {...props} />);
}

/** Read the rupee figure sitting beside a totals label. */
function amountFor(label: string): string {
  const el = screen.getByText(label);
  return el.parentElement?.textContent?.replace(label, '').trim() ?? '';
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('PurchaseOrderComposer — place of supply', () => {
  // 2 x Rs 1000 at 5% = Rs 100 of GST on Rs 2000 taxable.
  it('splits into CGST + SGST for a purchase inside the state', () => {
    renderComposer({ interstate: false });
    expect(amountFor('CGST')).toBe('₹50');
    expect(amountFor('SGST')).toBe('₹50');
    expect(screen.queryByText('IGST')).toBeNull();
    expect(screen.getByText(/Same state/i)).toBeTruthy();
  });

  it('raises a single IGST charge for a purchase from another state', () => {
    renderComposer({ interstate: true });
    expect(amountFor('IGST')).toBe('₹100');
    expect(screen.queryByText('CGST')).toBeNull();
    expect(screen.queryByText('SGST')).toBeNull();
    expect(screen.getByText(/Different states/i)).toBeTruthy();
  });

  it('charges the same money either way — only which tax it is changes', () => {
    const { unmount } = renderComposer({ interstate: false });
    const within = amountFor('Grand Total');
    unmount();
    renderComposer({ interstate: true });
    expect(amountFor('Grand Total')).toBe(within);
    expect(within).toBe('₹2,100');
  });

  it('says the split could not be told rather than showing a wrong one', () => {
    renderComposer({ interstate: null });
    expect(screen.getByText(/add the GST number to this vendor and to this shop/i)).toBeTruthy();
  });
});

describe('PurchaseOrderComposer — the rate comes from the product', () => {
  it('shows each line its own rate and HSN, not one house rate', () => {
    renderComposer({
      initialLines: [
        LINE(),
        LINE({ productId: 'prod-b', productName: 'Oakley', taxRate: 18, hsn: '900410' }),
      ],
    });
    expect(screen.getByText(/^5%$/)).toBeTruthy();
    expect(screen.getByText(/^18%$/)).toBeTruthy();
    expect(screen.getByText(/HSN 900311/)).toBeTruthy();
    expect(screen.getByText(/HSN 900410/)).toBeTruthy();
    // Rs 100 (5% of 2000) + Rs 360 (18% of 2000) = Rs 460, NOT a flat 18% of
    // Rs 4000 (= Rs 720).
    expect(amountFor('CGST')).toBe('₹230');
  });

  it('a blank line starts with NO rate rather than a flat 18%', () => {
    const blank = applyPickedProduct(LINE({ taxRate: 18 }), {
      productId: 'p',
      productName: 'Unknown',
      sku: 'U',
      gstRate: null,
      hsn: null,
    });
    expect(blank.taxRate).toBe(0);
    expect(blank.gstResolved).toBe(false);
  });

  it('takes the rate and HSN off the picked product', () => {
    const picked = applyPickedProduct(LINE({ taxRate: 18, hsn: null }), {
      productId: 'p',
      productName: 'Ray-Ban',
      sku: 'RB',
      gstRate: 5,
      hsn: '900311',
      detail: 'GOLD · 52 · MRP ₹6,000',
    });
    expect(picked.taxRate).toBe(5);
    expect(picked.hsn).toBe('900311');
    expect(picked.gstResolved).toBe(true);
    expect(picked.productDetail).toBe('GOLD · 52 · MRP ₹6,000');
  });

  it('names the products whose GST rate is unknown instead of taxing them', () => {
    renderComposer({
      initialLines: [LINE({ productName: 'Nameless Frame', taxRate: 0, gstResolved: false })],
    });
    expect(screen.getByText(/No GST rate for 1 product/i)).toBeTruthy();
    // Twice: once in the line itself, once named in the warning.
    expect(screen.getAllByText(/Nameless Frame/).length).toBe(2);
    expect(screen.getByText(/add the HSN number on the product/i)).toBeTruthy();
    expect(screen.getByText(/Rate not set/i)).toBeTruthy();
    // Nothing invented: no tax charged on a rate nobody knows.
    expect(amountFor('Grand Total')).toBe('₹2,000');
  });
});

describe('PurchaseOrderComposer — expected delivery date', () => {
  it('will not offer a date before today', () => {
    renderComposer();
    const input = screen.getByLabelText('Expected Delivery Date') as HTMLInputElement;
    expect(input.getAttribute('min')).toBe(todayForDateInput());
    expect(screen.getByText(/cannot be promised in the past/i)).toBeTruthy();
  });

  it('todayForDateInput reads the LOCAL day, not the UTC one', () => {
    // In IST, 19:30 UTC is already 01:00 the NEXT morning. A naive
    // toISOString() would hand the picker yesterday's floor and quietly allow
    // a backdated promise the server then rejects. Derived independently here
    // from the Date's own local getters, not by repeating the implementation.
    const instant = new Date('2026-08-26T19:30:00Z');
    const pad = (n: number) => String(n).padStart(2, '0');
    const localDay =
      `${instant.getFullYear()}-${pad(instant.getMonth() + 1)}-${pad(instant.getDate())}`;
    expect(todayForDateInput(instant)).toBe(localDay);
  });
});
