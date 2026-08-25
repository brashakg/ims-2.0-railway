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
import { eyeTestWriteBody } from '../eyeTestPayload';
import type { EyeTestData } from '../eyeTestTypes';

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
    // Blurs for real: the box used to CLAMP 0 up to 1 on blur, so this test
    // had to skip the blur to see the bug at all. It no longer does.
    typeInto('Right AXIS', '0');
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

// ---------------------------------------------------------------------------
// AN IMPOSSIBLE AXIS IS REFUSED, NEVER REWRITTEN.
// ---------------------------------------------------------------------------
// AXIS is a MERIDIAN, not a magnitude. 181 is not "nearly 180" -- it is most
// likely a slip for 18, which is 162 degrees away: the wrong cylinder meridian,
// a patient with headaches, and a remake. The true value is UNKNOWABLE, so the
// only safe answer is to refuse the entry, exactly as the backend's own
// docstring promises ("a FRACTIONAL axis (90.5) is REJECTED, not rounded").
//
// The box used to clamp on blur (181 -> 180, 0 -> 1, 9999 -> 180) and strip the
// decimal point while typing (90.5 -> 905 -> clamped to 180). Because the value
// was rewritten into something IN RANGE, neither the client gate nor the server
// gate ever saw the mistake. These tests drive the real form: change, blur,
// Save, and assert BOTH that the typed text survived and that saving was
// refused with a banner the optometrist can actually read.
// ---------------------------------------------------------------------------
describe('an impossible AXIS is refused, not silently corrected', () => {
  const IMPOSSIBLE = ['181', '0', '90.5', '9999'];

  for (const typed of IMPOSSIBLE) {
    it(`keeps ${typed} exactly as typed and refuses to save it`, async () => {
      const onSave = renderForm();
      openTab(/Lensometer/);
      // A cylinder alongside it, so the refusal cannot be the CYL<->AXIS
      // pairing rule standing in for the range check we are actually testing.
      typeInto('Right CYL', '-1.00');
      typeInto('Right AXIS', typed);

      // (a) NOT REWRITTEN -- the box still shows what the clinician typed.
      expect((screen.getByLabelText('Right AXIS') as HTMLInputElement).value).toBe(typed);

      // (b) REFUSED, visibly.
      save();
      await waitFor(() => expect(alertText()).toBeTruthy());
      expect(alertText()).toMatch(/AXIS/i);
      expect(onSave).not.toHaveBeenCalled();
    });
  }

  it('says a fractional axis is not a whole number, not "out of range"', async () => {
    renderForm();
    openTab(/Lensometer/);
    typeInto('Right CYL', '-1.00');
    typeInto('Right AXIS', '90.5');
    save();

    await waitFor(() => expect(alertText()).toBeTruthy());
    expect(alertText()).toMatch(/whole number/i);
  });

  it('refuses an impossible axis on the FINAL Rx too (the billable one)', async () => {
    const onSave = renderForm();
    openTab(/Final Rx/);
    typeInto('Right (OD) cylinder', '-1.00');
    typeInto('Right (OD) axis', '181');

    expect((screen.getByLabelText('Right (OD) axis') as HTMLInputElement).value).toBe('181');

    save();
    await waitFor(() => expect(alertText()).toBeTruthy());
    expect(onSave).not.toHaveBeenCalled();
  });

  // POSITIVE CONTROLS: refusing everything would pass every assertion above.
  it('accepts the boundary meridians 1, 90 and 180 and normalises them', async () => {
    const onSave = renderForm();
    openTab(/Lensometer/);
    typeInto('Right CYL', '-1.00');
    typeInto('Right AXIS', '001');
    expect((screen.getByLabelText('Right AXIS') as HTMLInputElement).value).toBe('1');
    typeInto('Right AXIS', '90.0');
    expect((screen.getByLabelText('Right AXIS') as HTMLInputElement).value).toBe('90');
    typeInto('Left CYL', '-0.50');
    typeInto('Left AXIS', '180');
    expect((screen.getByLabelText('Left AXIS') as HTMLInputElement).value).toBe('180');

    save();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(alertText()).toBeNull();
  });

  it('leaves the SIGNED kinds alone -- a plus power still formats on blur', async () => {
    renderForm();
    openTab(/Lensometer/);
    typeInto('Right SPH', '4');
    expect((screen.getByLabelText('Right SPH') as HTMLInputElement).value).toBe('+4.00');
    typeInto('Right ADD', '2');
    expect((screen.getByLabelText('Right ADD') as HTMLInputElement).value).toBe('+2.00');
    typeInto('Right PD', '32');
    expect((screen.getByLabelText('Right PD') as HTMLInputElement).value).toBe('32.0');
  });
});

// ---------------------------------------------------------------------------
// A LOW-VISION ACUITY MUST BE RECORDABLE.
// ---------------------------------------------------------------------------
// Counting Fingers, Hand Movement and Perception of Light are what a dense
// cataract, an advanced glaucoma or a fresh post-op eye actually reads -- daily
// findings in six optical stores. The exam form's VA gate arrived on this
// branch with the SEVEN Snellen fractions while the server was widened to
// eleven, so the form refused four values its own API accepts. The optometrist
// cannot make the finding go away; the realistic outcome is a CF eye written
// down as 6/60, which is a false clinical record produced by the gate itself.
//
// These drive the real form and assert on the REAL WIRE BODY -- the exact
// object ClinicalPage hands to clinicalApi -- not on component state.
// ---------------------------------------------------------------------------
describe('a low-vision acuity is recorded, not rounded up to a Snellen fraction', () => {
  const LOW_VISION = ['CF', 'HM', 'PL', 'NPL'];

  for (const notation of LOW_VISION) {
    it(`saves ${notation} on the Subjective Rx, verbatim on the wire`, async () => {
      const onSave = renderForm();
      openTab(/Subjective Rx/);
      typeInto('Right VA', notation);
      save();

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(alertText()).toBeNull();

      const body = eyeTestWriteBody(onSave.mock.calls[0][0] as EyeTestData);
      expect(body.subjectiveRx?.rightEye?.va).toBe(notation);
    });
  }

  it('accepts a lower-case cf, exactly as the server does', async () => {
    const onSave = renderForm();
    openTab(/Subjective Rx/);
    typeInto('Right VA', 'cf');
    save();

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(alertText()).toBeNull();
  });

  it('records the low-vision finding on the LEFT eye too', async () => {
    const onSave = renderForm();
    openTab(/Subjective Rx/);
    typeInto('Left VA', 'HM');
    save();

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const body = eyeTestWriteBody(onSave.mock.calls[0][0] as EyeTestData);
    expect(body.subjectiveRx?.leftEye?.va).toBe('HM');
  });

  // NEGATIVE CONTROLS. Widening the set must not turn the gate off: junk and a
  // non-existent Snellen line are still refused, on the client as on the server
  // (backend/tests/test_clinical_exam_rx_validation.py BAD_VA).
  for (const junk of ['banana', '6/7', '20/20']) {
    it(`still REFUSES ${junk}`, async () => {
      const onSave = renderForm();
      openTab(/Subjective Rx/);
      typeInto('Right VA', junk);
      save();

      await waitFor(() => expect(alertText()).toBeTruthy());
      expect(alertText()).toMatch(/VA must be one of/i);
      // The banner names the set the optometrist may choose from. Listing only
      // the seven fractions is how the form told them CF was not allowed.
      expect(alertText()).toMatch(/CF, HM, PL, NPL/);
      expect(onSave).not.toHaveBeenCalled();
    });
  }

  it('still accepts the ordinary Snellen values', async () => {
    const onSave = renderForm();
    openTab(/Subjective Rx/);
    typeInto('Right VA', '6/6');
    typeInto('Left VA', '6/60');
    save();

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(alertText()).toBeNull();
  });
});
