// ============================================================================
// Patient-facing Rx card must never print junk placeholder strings.
// ============================================================================
// A blank per-eye PD used to be persisted as the LITERAL string "None" by the
// pre-#969 backend expression `str(data.right_eye.get("pd", ""))`. #969 fixed
// NEW writes, but existing rows still carry it -- and the same shape can arrive
// at any time from a CSV/Excel import, an integration, or a device feed.
//
// The prop TYPES say `number | null`, which is exactly why the old
// `re.pd ?? prescription.pd` failed: the junk is a non-null STRING, so `??`
// kept it AND suppressed the binocular-PD fallback that would have filled in
// the right number. The card then read "PD: None".
//
// These tests drive the REAL component render, not the private helpers.
//
// THE CRITICAL DISTINCTION: a genuine 0 (or "0") is CLINICALLY REAL. An axis of
// 0, a cylinder of 0, a prism of 0 all mean something. The emptiness check must
// never be truthiness-based, or it erases real clinical data from the card.

import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { PrescriptionPrint } from '../PrescriptionPrint';
import type { PrescriptionPrintData, StoreInfo } from '../PrescriptionPrint';

const STORE: StoreInfo = {
  storeName: 'Better Vision Bokaro',
  address: 'City Centre',
  city: 'Bokaro',
  state: 'Jharkhand',
  pincode: '827004',
  stateCode: '20',
  gstin: '20AABCB1234M1Z5',
};

// Column indices of the Distance Vision table: Eye SPH CYL AXIS ADD PD V/A
const EYE = 0, SPH = 1, CYL = 2, AXIS = 3, ADD = 4, PD = 5, VA = 6;

/** Build print data. `rx` is loosely typed on purpose: the whole point is that
 *  the wire delivers values the TS types say are impossible. */
function makeRx(rx: Record<string, unknown>): PrescriptionPrintData {
  return {
    id: 'abc123def456',
    patientName: 'Test Patient',
    customerPhone: '9999900000',
    prescribedAt: '2026-08-10T00:00:00.000Z',
    rightEye: { sphere: -1.25, cylinder: -0.5, axis: 90, add: null },
    leftEye: { sphere: -1.0, cylinder: -0.75, axis: 85, add: null },
    ...rx,
  } as unknown as PrescriptionPrintData;
}

/** Read the Distance Vision table rows as arrays of cell text. Selected by its
 *  header columns (the statutory header block renders tables of its own). */
function distanceRows(container: HTMLElement): string[][] {
  const table = Array.from(container.querySelectorAll('table')).find((t) => {
    const head = (t.querySelector('thead')?.textContent || '').trim();
    return head.includes('SPH') && head.includes('PD') && head.includes('V/A');
  });
  if (!table) throw new Error('Distance Vision table not found in rendered card');
  return Array.from(table.querySelectorAll('tbody tr')).map((tr) =>
    Array.from(tr.querySelectorAll('td')).map((td) => (td.textContent || '').trim()),
  );
}

/** Read the Near Vision table rows (SPH CYL AXIS ADD -- no PD column). */
function nearRows(container: HTMLElement): string[][] {
  const table = Array.from(container.querySelectorAll('table')).find((t) => {
    const head = (t.querySelector('thead')?.textContent || '').trim();
    return head.includes('SPH') && head.includes('ADD') && !head.includes('PD');
  });
  if (!table) throw new Error('Near Vision table not found in rendered card');
  return Array.from(table.querySelectorAll('tbody tr')).map((tr) =>
    Array.from(tr.querySelectorAll('td')).map((td) => (td.textContent || '').trim()),
  );
}

function renderCard(rx: Record<string, unknown>) {
  const { container } = render(
    <PrescriptionPrint prescription={makeRx(rx)} store={STORE} onClose={() => {}} />,
  );
  return container;
}

describe('PrescriptionPrint - junk values never reach the patient', () => {
  it('falls back to the binocular PD when a per-eye PD is the string "None"', () => {
    // THE REPORTED BUG. `??` kept "None" and masked the binocular 62.
    const container = renderCard({
      rightEye: { sphere: -1.25, cylinder: -0.5, axis: 90, add: null, pd: 'None' },
      leftEye: { sphere: -1.0, cylinder: -0.75, axis: 85, add: null, pd: 'None' },
      pd: 62,
    });
    const rows = distanceRows(container);
    expect(rows[0][EYE]).toBe('OD (R)');
    expect(rows[0][PD]).toBe('62');
    expect(rows[1][PD]).toBe('62');
    expect(container.textContent).not.toContain('None');
  });

  it('renders "-" for a "None" PD when there is no binocular PD to fall back to', () => {
    const container = renderCard({
      rightEye: { sphere: -1.25, cylinder: -0.5, axis: 90, add: null, pd: 'None' },
      leftEye: { sphere: -1.0, cylinder: -0.75, axis: 85, add: null, pd: 'None' },
      pd: null,
    });
    const rows = distanceRows(container);
    expect(rows[0][PD]).toBe('-');
    expect(rows[1][PD]).toBe('-');
    expect(container.textContent).not.toContain('None');
  });

  it.each(['None', 'NONE', 'null', 'undefined', 'NaN', '   ', ''])(
    'treats the junk token %j as absent across SPH / CYL / AXIS / PD / V-A',
    (junk) => {
      const container = renderCard({
        rightEye: { sphere: junk, cylinder: junk, axis: junk, add: junk, pd: junk, va: junk },
        leftEye: { sphere: junk, cylinder: junk, axis: junk, add: junk, pd: junk, va: junk },
        pd: null,
      });
      const row = distanceRows(container)[0];
      expect(row[SPH]).toBe('-');
      expect(row[CYL]).toBe('-');
      expect(row[AXIS]).toBe('-');
      expect(row[PD]).toBe('-');
      expect(row[VA]).toBe('-');
    },
  );

  it('does not crash on a junk power (the old formatPower threw on .toFixed)', () => {
    expect(() =>
      renderCard({
        rightEye: { sphere: 'None', cylinder: 'None', axis: 'None', add: 'None' },
        leftEye: { sphere: 'None', cylinder: 'None', axis: 'None', add: 'None' },
      }),
    ).not.toThrow();
  });

  it('falls through a junk Near Vision value to the distance value', () => {
    // Same `??` trap one table down: a nearVision cell of "None" is non-null,
    // so `??` printed the junk instead of falling back to the distance Rx.
    const container = renderCard({
      rightEye: { sphere: -1.25, cylinder: -0.5, axis: 90, add: 2 },
      leftEye: { sphere: -1.0, cylinder: -0.75, axis: 85, add: 2 },
      nearVisionRight: { sphere: 'None', cylinder: 'None', axis: 'None', add: 'None' },
      nearVisionLeft: { sphere: 'None', cylinder: 'None', axis: 'None', add: 'None' },
    });
    const near = nearRows(container)[0];
    // Eye SPH CYL AXIS ADD
    expect(near[1]).toBe('-1.25');
    expect(near[2]).toBe('-0.50');
    expect(near[3]).toBe('90');
    expect(near[4]).toBe('+2.00');
    expect(container.textContent).not.toContain('None');
  });

  it('does not show a bogus Near Vision table when ADD is the junk string "None"', () => {
    const container = renderCard({
      rightEye: { sphere: -1.25, cylinder: -0.5, axis: 90, add: 'None' },
      leftEye: { sphere: -1.0, cylinder: -0.75, axis: 85, add: 'None' },
    });
    expect(container.textContent).not.toContain('Near Vision');
  });
});

describe('PrescriptionPrint - a genuine zero is REAL clinical data', () => {
  it('keeps a numeric 0 for CYL / AXIS / ADD / PD instead of blanking it', () => {
    const container = renderCard({
      rightEye: { sphere: 0, cylinder: 0, axis: 0, add: 0, pd: 0, va: '6/6' },
      leftEye: { sphere: 0, cylinder: 0, axis: 0, add: 0, pd: 0, va: '6/6' },
    });
    const row = distanceRows(container)[0];
    expect(row[SPH]).toBe('+0.00');
    expect(row[CYL]).toBe('+0.00');
    expect(row[AXIS]).toBe('0°');
    expect(row[ADD]).toBe('+0.00');
    expect(row[PD]).toBe('0');
    expect(row[VA]).toBe('6/6');
    // Nothing in a genuinely-zero Rx may render as absent.
    expect(row).not.toContain('-');
  });

  it('keeps a STRING "0" too -- legacy rows store powers as strings', () => {
    const container = renderCard({
      rightEye: { sphere: '0', cylinder: '0', axis: '0', add: '0', pd: '0' },
      leftEye: { sphere: '0', cylinder: '0', axis: '0', add: '0', pd: '0' },
    });
    const row = distanceRows(container)[0];
    expect(row[SPH]).toBe('+0.00');
    expect(row[CYL]).toBe('+0.00');
    expect(row[AXIS]).toBe('0°');
    expect(row[PD]).toBe('0');
  });

  it('does not let a 0 PD get replaced by the binocular PD', () => {
    // 0 is present, so the fallback must NOT engage.
    const container = renderCard({
      rightEye: { sphere: -1, cylinder: 0, axis: 90, add: null, pd: 0 },
      leftEye: { sphere: -1, cylinder: 0, axis: 90, add: null, pd: 0 },
      pd: 62,
    });
    expect(distanceRows(container)[0][PD]).toBe('0');
  });

  it('shows the Near Vision table for a real ADD of 0', () => {
    const container = renderCard({
      rightEye: { sphere: -1.25, cylinder: -0.5, axis: 90, add: 0 },
      leftEye: { sphere: -1.0, cylinder: -0.75, axis: 85, add: 0 },
    });
    expect(container.textContent).toContain('Near Vision');
  });
});
