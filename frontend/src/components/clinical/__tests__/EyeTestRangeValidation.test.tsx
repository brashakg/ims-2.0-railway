// ============================================================================
// PATIENT SAFETY: the eye-test tabs must RANGE-CHECK every power they capture
// ============================================================================
// Owner report (2026-08-24): "prescription power validation is not working in
// lensometer values ... right now it is even taking -9999 for prescription
// values in lensometer".
//
// The canonical clinical limits (constants/rxLimits.ts RX_LIMITS, mirrored by
// backend/api/services/rx_validation.py) were consumed by exactly two screens
// -- PatientIntakeModal and the POS/clinic PrescriptionForm. The seven-tab eye
// examination form, which is where an optometrist actually types a power,
// consumed none of them. Lensometer / Auto-Ref / Subjective Rx accepted -9999,
// +9999, a cylinder with no axis, and a monocular PD of any size.
//
// These tests drive the REAL EyeTestForm with the REAL tabs and the REAL
// RxPowerInput, click the REAL Save button, and assert on the RENDERED alert
// plus whether onSave was reached. Nothing about the validator is stubbed --
// the subject IS the validation, so a mock of it would prove nothing.

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../../context/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', name: 'Dr Rao', activeStoreId: 'BV-BOK-01' } }),
}));

vi.mock('../../print/storeIdentity', () => ({
  resolveStoreIdentity: () => Promise.resolve(null),
}));

import { MemoryRouter } from 'react-router-dom';
import { EyeTestForm } from '../EyeTestForm';

const PATIENT = { id: 'p1', name: 'Asha Kumari', phone: '9000000001', age: 44 };

function renderForm() {
  const onSave = vi.fn(async () => {});
  render(
    <MemoryRouter>
      <EyeTestForm
        isOpen
        onClose={() => {}}
        onSave={onSave}
        patient={PATIENT as any}
        optometristName="Dr Rao"
      />
    </MemoryRouter>,
  );
  return onSave;
}

function openTab(name: RegExp) {
  fireEvent.click(screen.getByRole('button', { name }));
}

/** Type into a labelled Rx box the way a clinician does: change, then blur. */
function typeInto(label: string, value: string, { blur = true } = {}) {
  const el = screen.getByLabelText(label);
  fireEvent.change(el, { target: { value } });
  if (blur) fireEvent.blur(el);
}

function save() {
  fireEvent.click(screen.getByRole('button', { name: /Save Prescription/i }));
}

/** The rendered validation banner, or null when the form reported no problem. */
function alertText(): string | null {
  const el = screen.queryByRole('alert');
  return el ? (el.textContent || '').trim() : null;
}

// ---------------------------------------------------------------------------
// THE REPORTED BUG, on the tab it was reported on.
// ---------------------------------------------------------------------------
describe('Lensometer refuses an impossible power', () => {
  it('REJECTS -9999 SPH and never calls onSave', async () => {
    const onSave = renderForm();
    openTab(/Lensometer/);
    typeInto('Right SPH', '-9999');
    save();

    await waitFor(() => expect(alertText()).toBeTruthy());
    expect(alertText()).toMatch(/SPH/i);
    expect(alertText()).toMatch(/-25/);
    expect(onSave).not.toHaveBeenCalled();
  });

  it('REJECTS +9999 SPH and never calls onSave', async () => {
    const onSave = renderForm();
    openTab(/Lensometer/);
    typeInto('Left SPH', '+9999');
    save();

    await waitFor(() => expect(alertText()).toBeTruthy());
    expect(alertText()).toMatch(/SPH/i);
    expect(onSave).not.toHaveBeenCalled();
  });

  it('REJECTS a cylinder recorded with a 0 axis', async () => {
    // 0 is not a meridian (the axis notation runs 1..180) and a toric lens with
    // no usable axis is un-grindable.
    const onSave = renderForm();
    openTab(/Lensometer/);
    typeInto('Right CYL', '-1.00');
    typeInto('Right AXIS', '0', { blur: false });
    save();

    await waitFor(() => expect(alertText()).toBeTruthy());
    expect(alertText()).toMatch(/AXIS/i);
    expect(onSave).not.toHaveBeenCalled();
  });

  it('REJECTS an off-grid power no lens is ground to', async () => {
    const onSave = renderForm();
    openTab(/Lensometer/);
    typeInto('Right SPH', '-1.30');
    save();

    await waitFor(() => expect(alertText()).toBeTruthy());
    expect(alertText()).toMatch(/0\.25/);
    expect(onSave).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// THE SET of clinical capture surfaces -- not a count. Every tab that takes a
// power must range-check it; the owner asked for "all modules in clinic".
// ---------------------------------------------------------------------------
describe('every eye-test tab that captures a power range-checks it', () => {
  const SURFACES: { tab: RegExp; label: string }[] = [
    { tab: /Lensometer/, label: 'Right SPH' },
    { tab: /Auto-Ref/, label: 'Right SPH' },
    { tab: /Subjective Rx/, label: 'Right SPH' },
    { tab: /Final Rx/, label: 'Right (OD) sphere' },
  ];

  for (const { tab, label } of SURFACES) {
    it(`${String(tab)} rejects -9999`, async () => {
      const onSave = renderForm();
      openTab(tab);
      typeInto(label, '-9999');
      save();

      await waitFor(() => expect(alertText()).toBeTruthy());
      expect(onSave).not.toHaveBeenCalled();
    });
  }

  it('Auto-Ref rejects an impossible keratometry reading', async () => {
    const onSave = renderForm();
    openTab(/Auto-Ref/);
    const k1 = screen.getByLabelText('Right Eye K1');
    fireEvent.change(k1, { target: { value: '-9999' } });
    fireEvent.blur(k1);
    save();

    await waitFor(() => expect(alertText()).toBeTruthy());
    expect(onSave).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// POSITIVE CONTROLS. A fix that simply refuses everything would pass every
// assertion above and be far worse than the bug.
// ---------------------------------------------------------------------------
describe('ordinary clinical values still save', () => {
  it('accepts a normal lensometer + subjective + final reading', async () => {
    const onSave = renderForm();

    openTab(/Lensometer/);
    typeInto('Right SPH', '-2.50');
    typeInto('Right CYL', '-0.75');
    typeInto('Right AXIS', '90');
    typeInto('Left SPH', '+4.00');

    openTab(/Subjective Rx/);
    typeInto('Right SPH', '-2.25');

    openTab(/Final Rx/);
    typeInto('Right (OD) sphere', '-2.25');
    typeInto('Right eye add', '+2.00');

    save();

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(alertText()).toBeNull();
  });

  it('accepts a MONOCULAR per-eye PD of 32.5mm', async () => {
    // The per-eye PD box is monocular (~half the binocular PD). The frontend
    // limit table checked it against the BINOCULAR 40-80 range, so a correct
    // 32.5 was refused while the backend (EyeData.validate_pd -> "pd_mono",
    // 20-45) accepted it. The backend is the source of truth.
    const onSave = renderForm();
    openTab(/Lensometer/);
    typeInto('Right PD', '32.5');
    save();

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(alertText()).toBeNull();
  });

  it('accepts an entirely blank exam (nothing recorded is not an error)', async () => {
    const onSave = renderForm();
    save();

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(alertText()).toBeNull();
  });
});
