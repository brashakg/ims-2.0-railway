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

// Decision D34 (Codex review of PR #1414, limit 2): the Order Date was the
// first ten letters of the stored instant -- its UTC day. An order raised at
// 01:30 on 2 November in India is stored as 2026-11-01T20:00:00 (UTC) and
// read "1 November". The mapper now asks the app's one India-day rule
// (utils/datetime istDayString); the list card, the detail, the downloaded
// copy and the print all read that mapped day.
describe('D34: the Order Date is the India calendar day, not the UTC one', () => {
  const TZS = ['Asia/Kolkata', 'America/Los_Angeles', 'Pacific/Kiritimati'];
  const inEveryTimezone = (check: () => void) => {
    const was = process.env.TZ;
    try {
      for (const tz of TZS) {
        process.env.TZ = tz;
        check();
        cleanup();
      }
    } finally {
      process.env.TZ = was;
    }
  };
  const orderDay = (created_at: unknown) => mapPOtoPurchaseOrder({ ...apiPO(null), created_at }).date;

  it('an order at 20:00 UTC on 1 November is dated 2 November, in every browser timezone', () => {
    inEveryTimezone(() => {
      expect(orderDay('2026-11-01T20:00:00Z')).toBe('2026-11-02');
      // What the server really sends: its UTC wall clock, no zone, microseconds.
      expect(orderDay('2026-11-01T20:00:00')).toBe('2026-11-02');
      expect(orderDay('2026-11-01T20:00:00.836000')).toBe('2026-11-02');
      expect(orderDay('2026-11-02T01:30:00+05:30')).toBe('2026-11-02');
      // The India midnight itself: 18:30 UTC.
      expect(orderDay('2026-11-01T18:29:59Z')).toBe('2026-11-01');
      expect(orderDay('2026-11-01T18:30:00Z')).toBe('2026-11-02');
      // A morning order was never wrong and still is not.
      expect(orderDay('2026-11-01T04:00:00Z')).toBe('2026-11-01');
      expect(poDay(orderDay('2026-11-01T20:00:00Z'))).toBe(shownDay(2026, 11, 2));
    });
  });

  it('the list card, the detail and the downloaded copy all show 2 November', async () => {
    const po = () => mapPOtoPurchaseOrder({ ...apiPO('2026-11-10'), created_at: '2026-11-01T20:00:00Z' });
    inEveryTimezone(() => {
      const NOV_2 = shownDay(2026, 11, 2);
      const list = render(
        <MemoryRouter>
          <PurchaseTable purchaseOrders={[po()]} onViewPO={() => {}} />
        </MemoryRouter>,
      );
      expect(field(list.container, 'Order Date')).toBe(NOV_2);
      list.unmount();
      const detail = render(<PurchaseOrderDetail po={po()} onClose={() => {}} onAction={() => {}} />);
      expect(field(detail.container, 'Order Date')).toBe(NOV_2);
    });

    let saved: Blob | null = null;
    URL.createObjectURL = vi.fn((b: Blob) => {
      saved = b;
      return 'blob:po';
    }) as unknown as typeof URL.createObjectURL;
    URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(
      <MemoryRouter>
        <PurchaseTable purchaseOrders={[po()]} onViewPO={() => {}} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTitle('Download PO'));
    const text = await new Promise<string>((done) => {
      const reader = new FileReader();
      reader.onload = () => done(String(reader.result));
      reader.readAsText(saved as unknown as Blob);
    });
    expect(text).toContain(`Date      : ${shownDay(2026, 11, 2)}`);
    click.mockRestore();
  });

  it('a day with no time stays that day; no date reads "Not set"; a stored non-date reads "Unreadable date"', () => {
    inEveryTimezone(() => {
      expect(orderDay('2026-11-01')).toBe('2026-11-01');
      expect(poDay(orderDay('2026-11-01'))).toBe(shownDay(2026, 11, 1));
      for (const none of [undefined, null, '']) {
        expect(orderDay(none)).toBe('');
        expect(poDay(orderDay(none))).toBe('Not set');
      }
      // As before this change: never "Invalid Date", never a made-up day.
      expect(poDay(orderDay('last tuesday'))).toBe('Unreadable date');
    });
  });

  it('reads in a browser that parses only standard date strings (Safari)', () => {
    withStrictDate(() => {
      expect(orderDay('2026-11-01T20:00:00Z')).toBe('2026-11-02');
      expect(orderDay('2026-11-01T20:00:00')).toBe('2026-11-02');
      expect(orderDay('2026-11-01')).toBe('2026-11-01');
    });
  });
});
