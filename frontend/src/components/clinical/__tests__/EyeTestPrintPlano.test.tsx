// ============================================================================
// PATIENT SAFETY: a recorded PLANO must print as a power, not as a dash
// ============================================================================
// SITE 2 of the blank-vs-zero conflation, and the opposite direction to POS.
// EyeTestForm.buildPrintData computed
//
//     sphere: parseFloat(finalRxData.rightEye.sphere) || null
//
// and `parseFloat("0") || null` is NULL. So a clinician who measured a plano
// sphere, a zero cylinder or a zero reading add handed the patient a card with
// a DASH in that column -- the card denied a finding that had actually been
// made. The patient, the lab and any optician reading the card back all see
// "never measured" where "measured, and it is zero" is the truth.
//
// This drives the REAL form: type into the real Final Rx inputs, click the real
// Print button, and read the cells of the rendered card. It deliberately does
// not call buildPrintData directly -- that function is private, and a test that
// reached past the component could not tell whether the component still uses it.
//
// BOTH EYES, and SPH / CYL / ADD each. The axis fix last round shipped with the
// left eye unpinned; per-eye asymmetry is the specific failure watched for here.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', name: 'Dr Rao', activeStoreId: 'BV-BOK-01' } }),
}));

// The print header resolves the issuing store over the network. Irrelevant to
// the powers, and left unresolved on purpose so the card renders from the
// prescription alone.
vi.mock('../../print/storeIdentity', () => ({
  resolveStoreIdentity: () => Promise.resolve(null),
}));

import { MemoryRouter } from 'react-router-dom';
import { EyeTestForm } from '../EyeTestForm';

// Distance Vision columns: Eye SPH CYL AXIS ADD PD V/A
const SPH = 1, CYL = 2, ADD = 4;

const PATIENT = { id: 'p1', name: 'Asha Kumari', phone: '9000000001', age: 44 };

function renderEyeTest() {
  return render(
    <MemoryRouter>
      <EyeTestForm
        isOpen
        onClose={() => {}}
        onSave={async () => {}}
        patient={PATIENT as any}
        optometristName="Dr Rao"
      />
    </MemoryRouter>,
  );
}

/** RxPowerInput normalises on blur, so type then blur like a real clinician. */
function typePower(label: string, value: string) {
  const el = screen.getByLabelText(label);
  fireEvent.change(el, { target: { value } });
  fireEvent.blur(el);
}

/** Read the printed Distance Vision rows as arrays of cell text. Selected by
 *  its header columns -- the statutory header block renders tables of its own. */
function distanceRows(container: HTMLElement): string[][] {
  const table = Array.from(container.querySelectorAll('table')).find((t) => {
    const head = (t.querySelector('thead')?.textContent || '').trim();
    return head.includes('SPH') && head.includes('PD') && head.includes('V/A');
  });
  if (!table) throw new Error('Distance Vision table not found in the printed card');
  return Array.from(table.querySelectorAll('tbody tr')).map((tr) =>
    Array.from(tr.querySelectorAll('td')).map((td) => (td.textContent || '').trim()),
  );
}

/** Fill the Final Rx tab, then open the print preview and return its rows. */
async function printAfter(fill: () => void): Promise<{ od: string[]; os: string[] }> {
  const { container } = renderEyeTest();
  fireEvent.click(screen.getByRole('button', { name: /Final Rx/ }));
  fill();
  fireEvent.click(screen.getByRole('button', { name: /^Print$/ }));
  await waitFor(() => expect(distanceRows(container).length).toBeGreaterThan(1));
  const rows = distanceRows(container);
  return { od: rows[0], os: rows[1] };
}

describe('a recorded ZERO prints as a power on the patient card', () => {
  it('prints a measured plano SPH as +0.00, not a dash - BOTH eyes', async () => {
    // THE REQUIREMENT. `parseFloat("0.00") || null` was null, so this column
    // read "-" and the card denied a measurement the clinician had made.
    const { od, os } = await printAfter(() => {
      typePower('Right (OD) sphere', '0');
      typePower('Left (OS) sphere', '0');
    });

    expect(od[SPH]).toBe('+0.00');
    expect(os[SPH]).toBe('+0.00');
    expect(od[SPH]).not.toBe('-');
    expect(os[SPH]).not.toBe('-');
  });

  it('prints a measured zero CYL as +0.00, not a dash - BOTH eyes', async () => {
    const { od, os } = await printAfter(() => {
      typePower('Right (OD) cylinder', '0');
      typePower('Left (OS) cylinder', '0');
    });

    expect(od[CYL]).toBe('+0.00');
    expect(os[CYL]).toBe('+0.00');
    expect(od[CYL]).not.toBe('-');
    expect(os[CYL]).not.toBe('-');
  });

  it('prints a measured zero ADD as +0.00, not a dash - BOTH eyes', async () => {
    // The near-vision ADD lives on the FLAT rightAdd/leftAdd fields, not on the
    // eye objects, so it is a separate read in buildPrintData and a separate
    // chance to lose the zero. "No reading addition" is a finding.
    const { od, os } = await printAfter(() => {
      typePower('Right eye add', '0');
      typePower('Left eye add', '0');
    });

    expect(od[ADD]).toBe('+0.00');
    expect(os[ADD]).toBe('+0.00');
    expect(od[ADD]).not.toBe('-');
    expect(os[ADD]).not.toBe('-');
  });

  it('keeps ordinary powers printing correctly - a fix that hard-coded +0.00 would fail here', async () => {
    const { od, os } = await printAfter(() => {
      typePower('Right (OD) sphere', '-2.25');
      typePower('Left (OS) sphere', '1.5');
    });

    expect(od[SPH]).toBe('-2.25');
    expect(os[SPH]).toBe('+1.50');
  });
});

describe('a power that was NEVER recorded still prints as a dash', () => {
  it('leaves an untouched SPH / CYL / ADD as "-" on BOTH eyes', async () => {
    // The other half of the rule, and the control for the block above: the fix
    // must not "solve" the missing plano by printing 0.00 for everything. This
    // direction was already correct at this site (parseFloat("") is NaN, and
    // `NaN || null` is null) and must stay correct.
    const { od, os } = await printAfter(() => {
      // Nothing typed at all.
    });

    for (const row of [od, os]) {
      expect(row[SPH]).toBe('-');
      expect(row[CYL]).toBe('-');
      expect(row[ADD]).toBe('-');
    }
  });

  it('distinguishes a measured plano from an unmeasured power IN THE SAME CARD', async () => {
    // The conflation itself, asserted directly: the right eye was measured and
    // is plano; the left eye was never measured. The card must not show them
    // alike, in either direction.
    const { od, os } = await printAfter(() => {
      typePower('Right (OD) sphere', '0');
    });

    expect(od[SPH]).toBe('+0.00');
    expect(os[SPH]).toBe('-');
    expect(od[SPH]).not.toBe(os[SPH]);
  });
});
