// ============================================================================
// D34.2 / Codex PR-1414 round 2: a printed date is the India calendar day.
// ============================================================================
// The PO's Order Date is a date-only "2026-11-02" (its India day). The shared
// print formatter read it as UTC midnight and printed the browser's local day,
// so a browser west of UTC printed 01-Nov-2026 under a list that said 2 Nov.
// Run in New York (UTC-5) and in India; the printed day must not move.

import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { formatDate } from '../legalPrimitives';
import { POPrint } from '../POPrint';

const PO = {
  po_id: 'PO1', po_number: 'AUDIT-1414-IST', po_date: '2026-11-02', expected_delivery: '2026-11-10',
  vendor_id: 'V1', vendor_name: 'Safilo India', vendor_address: '', vendor_gstin: '',
  items: [{ product_id: 'P1', product_name: 'Carrera 1234', quantity: 1, unit_price: 100, total: 100 }],
  subtotal: 100, tax_amount: 0, grand_total: 100,
};
const STORE = { storeName: 'Dhanbad', address: 'Bank More', city: 'Dhanbad', state: 'Jharkhand', pincode: '826001' };

const saved = process.env.TZ;
afterAll(() => { process.env.TZ = saved; });

for (const tz of ['America/New_York', 'Asia/Kolkata']) {
  describe(`printed dates in ${tz}`, () => {
    beforeAll(() => { process.env.TZ = tz; });

    it('the browser is really in that timezone', () => {
      expect(new Date('2026-11-02T00:00:00Z').getTimezoneOffset()).toBe(tz === 'Asia/Kolkata' ? -330 : 300);
    });

    it('a date-only value is that calendar day', () => {
      expect(formatDate('2026-11-02')).toBe('02-Nov-2026');
      expect(formatDate('2026-01-01')).toBe('01-Jan-2026');
    });

    it('a timestamp is the day it was in India', () => {
      // 20:00 UTC on 1 Nov is 01:30 on 2 Nov in India.
      expect(formatDate('2026-11-01T20:00:00Z')).toBe('02-Nov-2026');
      expect(formatDate('2026-11-01T20:00:00')).toBe('02-Nov-2026'); // naive backend time is UTC
      expect(formatDate(new Date('2026-11-01T20:00:00Z'))).toBe('02-Nov-2026');
    });

    it('nothing or garbage prints blank, never "Invalid Date"', () => {
      expect(formatDate('')).toBe('');
      expect(formatDate(null)).toBe('');
      expect(formatDate(undefined)).toBe('');
      expect(formatDate('not a date')).toBe('');
    });

    it('the Print PO document carries the India Order Date and delivery date', () => {
      render(<POPrint po={PO} store={STORE} onClose={() => undefined} />);
      expect(screen.getAllByText('02-Nov-2026').length).toBeGreaterThan(0);
      expect(screen.queryByText('01-Nov-2026')).toBeNull();
      expect(screen.getAllByText('10-Nov-2026').length).toBeGreaterThan(0);
    });
  });
}
