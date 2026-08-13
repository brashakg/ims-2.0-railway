// ============================================================================
// PATIENT SAFETY: the printed Rx card must not confuse "plano" with "unknown"
// ============================================================================
// PrescriptionCard is the card handed to the patient by the clinical queue's
// "Print Rx Card" button. Its power fields used to be typed as plain `number`
// and rendered with a bare `.toFixed(2)`, which forced its one caller to spell
// `readEyePower(...) || 0` -- so an eye test that recorded NO sphere printed
// "0.00", a positive claim that no correction is needed. The type is now
// nullable and the renderer distinguishes the two states.
//
// Both directions, both eyes, sphere / cylinder / add. The card renders twice
// (a print block and an on-screen block), so each assertion is made against
// EVERY table that carries the powers -- a fix applied to one block only would
// still hand the patient a wrong card.

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PrescriptionCard } from '../PrescriptionCard';

type Eye = { sphere: number | null; cylinder: number | null; axis: number | null; add: number | null };

const EMPTY_EYE: Eye = { sphere: null, cylinder: null, axis: null, add: null };

function renderCard(over: Record<string, unknown>) {
  const { container } = render(
    <PrescriptionCard
      prescription={{
        id: 'RX-1',
        patientName: 'Asha Kumari',
        patientAge: 44,
        date: '2026-08-01T00:00:00.000Z',
        validUntil: '2027-08-01T00:00:00.000Z',
        optometristName: 'Dr Rao',
        rightEye: { ...EMPTY_EYE },
        leftEye: { ...EMPTY_EYE },
        pd: null,
        visualAcuity: '',
        notes: '',
        storeName: 'Better Vision Bokaro',
        ...over,
      } as never}
    />,
  );
  return container;
}

/** Every power row on the card: [OD cells, OS cells] per rendered table.
 *  Columns: Eye SPH CYL AXIS ADD. */
function powerRows(container: HTMLElement): string[][] {
  const rows: string[][] = [];
  for (const table of Array.from(container.querySelectorAll('table'))) {
    const head = (table.querySelector('thead')?.textContent || '').toUpperCase();
    if (!(head.includes('SPH') && head.includes('CYL') && head.includes('ADD'))) continue;
    for (const tr of Array.from(table.querySelectorAll('tbody tr'))) {
      rows.push(Array.from(tr.querySelectorAll('td')).map((td) => (td.textContent || '').trim()));
    }
  }
  if (rows.length < 2) throw new Error('no power tables rendered');
  return rows;
}

const SPH = 1, CYL = 2, AXIS = 3, ADD = 4;

describe('a power recorded as ZERO prints as a power', () => {
  it('prints +0.00 for a plano SPH / zero CYL / zero ADD on BOTH eyes, in every block', () => {
    // THE REQUIREMENT: a plano IS a prescription and must be legible as one.
    const plano: Eye = { sphere: 0, cylinder: 0, axis: null, add: 0 };
    const rows = powerRows(renderCard({ rightEye: { ...plano }, leftEye: { ...plano } }));

    for (const row of rows) {
      expect(row[SPH]).toBe('+0.00');
      expect(row[CYL]).toBe('+0.00');
      expect(row[ADD]).toBe('+0.00');
    }
    // Every rendered block, both eyes: 2 tables x 2 rows.
    expect(rows.length).toBe(4);
  });
});

describe('a power that was NEVER recorded prints as a dash', () => {
  it('prints "-" - never "0.00" - for an unrecorded SPH / CYL / ADD on BOTH eyes', () => {
    // THE REQUIREMENT in the other direction, and the one the old card got
    // wrong: `(null as any).toFixed(2)` was avoided by the caller passing 0,
    // so the patient read a confident plano for a power nobody measured.
    const rows = powerRows(renderCard({}));

    for (const row of rows) {
      expect(row[SPH]).toBe('-');
      expect(row[CYL]).toBe('-');
      expect(row[ADD]).toBe('-');
      expect(row[SPH]).not.toBe('0.00');
      expect(row[ADD]).not.toBe('0.00');
    }
  });

  it('distinguishes the two states within ONE card, per eye', () => {
    // The conflation itself: the right eye was measured and is plano, the left
    // eye was not measured. Asymmetry between eyes is the failure this watches.
    const rows = powerRows(
      renderCard({
        rightEye: { sphere: 0, cylinder: 0, axis: null, add: 0 },
        leftEye: { ...EMPTY_EYE },
      }),
    );

    for (let i = 0; i < rows.length; i += 2) {
      const od = rows[i];
      const os = rows[i + 1];
      expect(od[SPH]).toBe('+0.00');
      expect(os[SPH]).toBe('-');
      expect(od[SPH]).not.toBe(os[SPH]);
    }
  });

  it('keeps ordinary signed powers intact', () => {
    // A "fix" that printed +0.00 or "-" for everything would pass the two
    // blocks above; this is the control that stops it.
    const rows = powerRows(
      renderCard({
        rightEye: { sphere: -2.25, cylinder: -0.75, axis: 90, add: 2 },
        leftEye: { sphere: 1.5, cylinder: null, axis: null, add: null },
      }),
    );

    for (let i = 0; i < rows.length; i += 2) {
      expect(rows[i][SPH]).toBe('-2.25');
      expect(rows[i][CYL]).toBe('-0.75');
      expect(rows[i][AXIS]).toBe('90');
      expect(rows[i][ADD]).toBe('+2.00');
      expect(rows[i + 1][SPH]).toBe('+1.50');
      expect(rows[i + 1][AXIS]).toBe('-');
    }
  });
});

describe('the AXIS and the PD are NOT powers and are formatted as themselves', () => {
  it('prints an unrecorded axis as a dash, not as 0 and not as +0.00', () => {
    // An axis is a meridian notated 1-180, so it carries no meaningful zero and
    // wants neither a sign nor two decimals -- but "not recorded" must still
    // read as not recorded.
    const rows = powerRows(renderCard({}));
    for (const row of rows) expect(row[AXIS]).toBe('-');
  });

  it('never prints a bare unit for an unrecorded PD', () => {
    // The caller now passes `pd: null`. Rendering it as "-mm" would read like a
    // measurement with a missing number rather than a measurement not taken.
    const container = renderCard({ pd: null });
    expect(container.textContent).not.toContain('-mm');
    expect(container.textContent).toContain('PD');
  });

  it('still prints a real PD with its unit', () => {
    const container = renderCard({ pd: 62 });
    expect(container.textContent).toContain('62mm');
  });
});

describe('the card still renders its identifying information', () => {
  it('names the patient and the issuing store', () => {
    renderCard({});
    expect(screen.getAllByText(/Asha Kumari/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Better Vision Bokaro/).length).toBeGreaterThan(0);
  });
});

describe('the card survives a prescription with no validity date', () => {
  it('renders "-" instead of crashing when validUntil is absent', () => {
    // Intl.DateTimeFormat.format() THROWS on an invalid Date, so the unguarded
    // version took the whole card down. The clinical queue's "Print Rx Card"
    // button has never supplied a validUntil, so this was the real behaviour.
    const container = renderCard({ validUntil: undefined });

    expect(container.textContent).toContain('Valid Until: -');
    expect(container.textContent).not.toContain('Invalid Date');
  });

  it('does not stamp a card EXPIRED just because no expiry is on file', () => {
    // "Unknown" is not "expired" -- the same mistake as printing 0.00 for an
    // unmeasured power, in a different column.
    const container = renderCard({ validUntil: undefined });

    expect(container.textContent).not.toContain('has expired');
  });

  it('still marks a genuinely expired prescription', () => {
    const container = renderCard({ validUntil: '2020-01-01T00:00:00.000Z' });

    expect(container.textContent).toContain('has expired');
  });
});
