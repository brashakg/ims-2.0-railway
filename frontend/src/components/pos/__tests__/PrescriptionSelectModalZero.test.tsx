// ============================================================================
// PATIENT SAFETY: the Rx PICKER must show a power recorded as zero
// ============================================================================
// This is the modal the optician reads when choosing WHICH stored prescription
// to attach to a live sale, so what it shows is what gets ground. It carried
// the same truthiness gate the POS Rx panel did --
//
//     {rightEye.add && (<div>ADD ...</div>)}
//
// -- and it is worse here than a plain omission, because JSX renders the falsy
// operand: `0 && (...)` evaluates to `0`, so React prints a BARE "0" as a text
// node immediately after the AXIS value. An Rx of axis 90 / add 0 therefore
// reads "AXIS 900" at the counter. Wrong axis read, wrong lens ground, remake.
//
// Both eyes: a fix applied to one eye only is this repo's known failure mode.
//
// The third test pins the OTHER defect this file used to carry: its own local
// `formatPower` called `.toFixed` directly, so any row whose powers arrive as
// STRINGS threw `value.toFixed is not a function` and blanked the whole modal.
// The backend stores these as strings (clinical._power_for_storage writes
// str(); sales._rxStr re-stringifies), so that shape is reachable.

import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

const H = vi.hoisted(() => ({ getFamilyRx: vi.fn() }));

vi.mock('../../../services/api', () => ({
  prescriptionApi: { getFamilyRx: H.getFamilyRx },
}));

vi.mock('../../../services/api/handoffs', () => ({
  handoffsApi: { listClinicalInbox: () => Promise.resolve({ handoffs: [] }) },
}));

import { PrescriptionSelectModal } from '../PrescriptionSelectModal';

/** One family member, one prescription, the same eye values on both eyes. */
function familyWith(eye: Record<string, unknown>) {
  const rx = {
    prescription_id: 'RX-1',
    customer_id: 'c1',
    patient_id: 'p1',
    store_id: 'BV-BOK-01',
    test_date: '2026-08-01T00:00:00.000Z',
    expiry_date: '2027-08-01T00:00:00.000Z',
    is_valid: true,
    right_eye: { sphere: -2, cylinder: -1.25, axis: 90, add: null, pd: 31, ...eye },
    left_eye: { sphere: -2, cylinder: -1.25, axis: 90, add: null, pd: 31, ...eye },
  };
  return {
    members: [
      {
        patient_id: 'p1',
        name: 'Asha Kumari',
        relation: 'SELF',
        prescription_count: 1,
        valid_count: 1,
        prescriptions: [rx],
      },
    ],
  };
}

async function renderModal(eye: Record<string, unknown>) {
  H.getFamilyRx.mockResolvedValue(familyWith(eye));
  render(
    <PrescriptionSelectModal
      onClose={() => {}}
      onSelect={() => {}}
      onCreateNew={() => {}}
      patient={null}
      customerId="c1"
    />,
  );
  // A single member auto-selects, so the Rx list renders without a click.
  await waitFor(() => expect(screen.getAllByText('AXIS').length).toBe(2));
}

/** The eye row that holds a given AXIS label -- label text plus its values. */
function eyeRowTextFor(index: number): string {
  const axisLabel = screen.getAllByText('AXIS')[index];
  const row = axisLabel.closest('div.flex') as HTMLElement;
  return row.textContent ?? '';
}

beforeEach(() => {
  H.getFamilyRx.mockReset();
});

describe('a recorded ADD of 0 is shown, and never corrupts the AXIS beside it', () => {
  it('renders an ADD cell of +0.00 on BOTH eyes for a measured zero add', async () => {
    // THE REQUIREMENT: "no reading addition" is a finding the optician needs.
    await renderModal({ add: 0 });

    expect(screen.getAllByText('ADD').length).toBe(2);
    expect(screen.getAllByText('+0.00').length).toBe(2);
  });

  it('does not print a stray 0 that reads as part of the AXIS - BOTH eyes', async () => {
    // THE SHARPER HALF, and the one a "did the ADD cell appear" assertion would
    // miss entirely. With `{eye.add && (...)}` the expression evaluates to the
    // NUMBER 0 and React renders it, so the row reads "AXIS 900" for axis 90.
    await renderModal({ add: 0 });

    for (const eyeIndex of [0, 1]) {
      const row = eyeRowTextFor(eyeIndex);
      expect(row).not.toContain('AXIS900');
      expect(row).toContain('AXIS90');
    }
  });

  it('still hides the ADD cell when no add was recorded at all', async () => {
    // The control. Without it, a component that rendered "+0.00" for every
    // prescription would pass the two tests above.
    await renderModal({ add: null });

    expect(screen.queryByText('ADD')).not.toBeInTheDocument();
    for (const eyeIndex of [0, 1]) {
      expect(eyeRowTextFor(eyeIndex)).toContain('AXIS90');
    }
  });

  it('renders an ordinary add unchanged', async () => {
    await renderModal({ add: 2 });

    expect(screen.getAllByText('ADD').length).toBe(2);
    expect(screen.getAllByText('+2.00').length).toBe(2);
  });
});

describe('powers stored as STRINGS still render, instead of blanking the modal', () => {
  it('shows string powers rather than throwing on .toFixed', async () => {
    // The old local formatPower took `number | null | undefined` and called
    // `.toFixed` straight away. A string power threw, React unmounted the tree,
    // and the optician saw an EMPTY picker -- no prescriptions at all, on a
    // customer who has them.
    await renderModal({ sphere: '-2.00', cylinder: '-1.25', axis: 90, add: '0' });

    expect(screen.getAllByText('-2.00').length).toBe(2);
    expect(screen.getAllByText('-1.25').length).toBe(2);
    // ...and the string zero add is a recorded zero, not an absence.
    expect(screen.getAllByText('ADD').length).toBe(2);
    expect(screen.getAllByText('+0.00').length).toBe(2);
  });
});
