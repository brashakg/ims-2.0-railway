// ============================================================================
// PATIENT SAFETY: the POS Rx panel must SHOW a power recorded as zero
// ============================================================================
// The panel is what the optician reads back at the counter before dispensing.
// Two truthiness tests hid a recorded 0 from them:
//
//   * COMPACT view: `{prescription.rightEye.add && <div>ADD ...</div>}` -- a
//     measured ADD of 0 ("no reading addition") is falsy, so the whole ADD
//     block vanished and the panel looked exactly like an Rx where no near add
//     was ever measured.
//   * FULL view, editing: `value={editedPrescription.rightEye.sphere || ''}` --
//     a recorded 0 rendered as an EMPTY BOX. Typing over an apparent blank is
//     precisely how a real recorded value gets destroyed.
//
// THE TWO VIEWS ARE DIFFERENT CODE and are tested through the props that
// actually reach them. The ADD gate lives ONLY in the compact branch: the full
// view renders the SPH/CYL/AXIS/ADD labels unconditionally, so a test that
// asserted on labels there would pass no matter what the gate did. Both eyes,
// because a fix applied to one eye only is this repo's known failure mode.

import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PrescriptionPanel } from '../PrescriptionPanel';

function makeRx(right: Record<string, unknown>, left: Record<string, unknown>) {
  return {
    id: 'RX-1',
    patientId: 'p1',
    customerId: 'c1',
    storeId: 'BV-BOK-01',
    testDate: '2026-08-01T00:00:00.000Z',
    rightEye: { sphere: null, cylinder: null, axis: null, add: null, pd: 31, ...right },
    leftEye: { sphere: null, cylinder: null, axis: null, add: null, pd: 31, ...left },
    status: 'COMPLETED',
    createdAt: '2026-08-01T00:00:00.000Z',
    updatedAt: '2026-08-01T00:00:00.000Z',
  } as never;
}

/** The COMPACT view -- the one whose ADD block is conditional. */
function renderCompact(eye: Record<string, unknown>) {
  return render(<PrescriptionPanel compact prescription={makeRx(eye, eye)} />);
}

/** The FULL view, switched into edit mode. */
function renderEditing(eye: Record<string, unknown>) {
  render(<PrescriptionPanel prescription={makeRx(eye, eye)} />);
  fireEvent.click(screen.getByRole('button', { name: /Edit/i }));
  // Per eye the editor renders SPH, CYL, AXIS, ADD, PD -- in that order.
  return screen.getAllByRole('spinbutton').map((el) => (el as HTMLInputElement).value);
}

const POWER_SLOTS = [0, 1, 3, 5, 6, 8]; // SPH/CYL/ADD for the right eye, then the left
const AXIS_SLOTS = [2, 7];

describe('COMPACT view - a recorded ADD of 0 is displayed, not hidden', () => {
  it('renders the ADD block for a measured zero add on BOTH eyes', () => {
    // THE REQUIREMENT: "no reading addition" is a finding the optician needs,
    // and `{eye.add && ...}` deleted it from the panel entirely.
    renderCompact({ sphere: -2, add: 0 });

    expect(screen.getAllByText('ADD').length).toBe(2);
    expect(screen.getAllByText('+0.00').length).toBe(2);
  });

  it('still hides the ADD block when no add was recorded at all', () => {
    // The control: the fix must not render an empty ADD on every prescription.
    renderCompact({ sphere: -2, add: null });

    expect(screen.queryByText('ADD')).not.toBeInTheDocument();
  });

  it('renders an ordinary add unchanged', () => {
    renderCompact({ sphere: -2, add: 2 });

    expect(screen.getAllByText('ADD').length).toBe(2);
    expect(screen.getAllByText('+2.00').length).toBe(2);
  });
});

describe('FULL view - a recorded 0 is visible in the EDIT boxes', () => {
  it('shows "0" in the SPH / CYL / ADD boxes on BOTH eyes', () => {
    // THE REQUIREMENT. `value={x || ''}` showed an empty box, so the optician
    // read "not recorded" over a plano on file.
    const boxes = renderEditing({ sphere: 0, cylinder: 0, add: 0 });

    for (const slot of POWER_SLOTS) expect(boxes[slot]).toBe('0');
    // The AXIS boxes are legitimately blank here (none was recorded), which is
    // what stops this from passing on a component that renders "0" everywhere.
    for (const slot of AXIS_SLOTS) expect(boxes[slot]).toBe('');
  });

  it('leaves an unrecorded power as an empty box', () => {
    // The other direction: an absence must still look like one, or every blank
    // field invites the optician to accept a fabricated zero.
    const boxes = renderEditing({ sphere: null, cylinder: null, add: null });

    for (const slot of POWER_SLOTS) expect(boxes[slot]).toBe('');
    expect(boxes.filter((v) => v === '0').length).toBe(0);
  });

  it('round-trips an ordinary power into the box', () => {
    const boxes = renderEditing({ sphere: -1.25 });

    expect(boxes[0]).toBe('-1.25');
    expect(boxes[5]).toBe('-1.25');
  });
});
