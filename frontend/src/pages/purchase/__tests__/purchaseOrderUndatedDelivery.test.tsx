// ============================================================================
// IMS 2.0 - A purchase order with no expected delivery date (audit APP-02)
// ============================================================================
// The expected date is optional (vendors/models.py: expected_date
// Optional[str]); the mapper turns a missing one into ''. The order list card,
// the order detail and the downloaded copy all ran
// new Date(po.expectedDelivery).toLocaleDateString() on it and printed
// "Invalid Date" for a perfectly valid order. One rule now (purchaseMappers
// poDay): no date reads "Not set", a stored value that is not a date reads
// "Unreadable date", a real one reads exactly as it did before (the browser's
// own short date) -- and a date-only value is the same day whatever timezone
// the browser is in, in any browser (Safari reads only standard date strings).

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u1', roles: ['STORE_MANAGER'], activeStoreId: 'BV-DHN-01', storeIds: ['BV-DHN-01'] },
    hasRole: () => true,
  }),
}));
vi.mock('../../../components/print/storeIdentity', () => ({
  resolveStoreIdentity: vi.fn().mockResolvedValue(null),
}));

import { PurchaseTable } from '../PurchaseTable';
import { PurchaseOrderDetail } from '../PurchaseOrderDetail';
import { mapPOtoPurchaseOrder, poDay } from '../purchaseMappers';
import { withStrictDate } from '../../../test/strictDate';

/** A calendar day as this browser prints it -- what these screens always
 *  showed for a real date (25/10/2026 on an Indian PC). */
const shownDay = (y: number, m: number, d: number) => new Date(y, m - 1, d).toLocaleDateString();
const OCT_20 = shownDay(2026, 10, 20);
const OCT_25 = shownDay(2026, 10, 25);

/** What GET /vendors/purchase-orders sends for one order. */
const apiPO = (expected_date: unknown) => ({
  po_id: 'po-1', po_number: 'PO-0001', vendor_id: 'V-ESS', vendor_name: 'Essilor', status: 'SENT',
  delivery_store_id: 'BV-DHN-01', created_at: '2026-10-20T10:00:00', total_amount: 60, items: [],
  ...(expected_date === undefined ? {} : { expected_date }),
});

/** The text under a field's caption ("Expected Delivery", "Order Date"). */
const field = (root: HTMLElement, caption: string) =>
  within(root).getByText(caption).parentElement!.querySelector('p:last-child')!.textContent;

const CASES: Array<[string, unknown, string]> = [
  ['missing', undefined, 'Not set'],
  ['null', null, 'Not set'],
  ['blank', '', 'Not set'],
  ['malformed', 'next week', 'Unreadable date'],
  ['a date', '2026-10-25', OCT_25],
  ['a date and time', '2026-10-25T00:00:00', OCT_25],
];

afterEach(cleanup);

describe('APP-02: an undated purchase order never reads "Invalid Date"', () => {
  it.each(CASES)('the order list card: %s', (_name, stored, shown) => {
    const { container } = render(
      <MemoryRouter>
        <PurchaseTable purchaseOrders={[mapPOtoPurchaseOrder(apiPO(stored))]} onViewPO={() => {}} />
      </MemoryRouter>,
    );
    expect(field(container, 'Expected Delivery')).toBe(shown);
    expect(field(container, 'Order Date')).toBe(OCT_20);
    expect(container.textContent).not.toMatch(/Invalid Date/);
  });

  it.each(CASES)('the order detail: %s', (_name, stored, shown) => {
    const { container } = render(
      <PurchaseOrderDetail po={mapPOtoPurchaseOrder(apiPO(stored))} onClose={() => {}} onAction={() => {}} />,
    );
    expect(field(container, 'Expected Delivery')).toBe(shown);
    expect(container.textContent).not.toMatch(/Invalid Date/);
  });

  it('the downloaded copy of an undated order says "Not set"', async () => {
    let saved: Blob | null = null;
    URL.createObjectURL = vi.fn((b: Blob) => {
      saved = b;
      return 'blob:po';
    }) as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(
      <MemoryRouter>
        <PurchaseTable purchaseOrders={[mapPOtoPurchaseOrder(apiPO(null))]} onViewPO={() => {}} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTitle('Download PO'));
    expect(click).toHaveBeenCalled();
    const text = await new Promise<string>((done) => {
      const reader = new FileReader();
      reader.onload = () => done(String(reader.result));
      reader.readAsText(saved as unknown as Blob);
    });
    expect(text).toMatch(/Delivery\s+: Not set/);
    expect(text).toContain(`Date      : ${OCT_20}`);
    expect(text).not.toMatch(/Invalid Date/);
    click.mockRestore();
  });

  it('a date-only value is the same day in every browser timezone', () => {
    const was = process.env.TZ;
    try {
      for (const tz of ['Asia/Kolkata', 'America/Los_Angeles', 'Pacific/Kiritimati']) {
        process.env.TZ = tz;
        expect(poDay('2026-10-25')).toBe(shownDay(2026, 10, 25));
        expect(poDay('2026-10-25')).toMatch(/\b25\b/);
      }
    } finally {
      process.env.TZ = was;
    }
  });

  it('a real date keeps the style these screens always had', () => {
    // The fix is for the missing date only: how a real date is written
    // (also on the copy a vendor may be sent) is not this change's to restyle.
    expect(poDay('2026-10-25')).toBe(new Date('2026-10-25T00:00:00').toLocaleDateString());
    expect(poDay('2026-10-25')).not.toMatch(/Oct/);
  });

  it('reads in a browser that parses only standard date strings (Safari)', () => {
    withStrictDate(() => {
      expect(poDay('2026-10-25')).toBe(shownDay(2026, 10, 25));
      expect(poDay('2026-10-25T10:00:00')).toBe(shownDay(2026, 10, 25));
      expect(poDay('next week')).toBe('Unreadable date');
      expect(poDay('')).toBe('Not set');
    });
  });
});
