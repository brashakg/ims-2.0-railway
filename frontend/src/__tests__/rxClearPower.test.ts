import { describe, it, expect, vi } from 'vitest';

// The payload builder is pure; stub the axios client so this suite does not
// drag the whole HTTP layer (interceptors, idle-logout hook) into the run.
vi.mock('../services/api/client', () => ({ default: {} }));

import { toPrescriptionCreatePayload } from '../services/api/sales';

// ============================================================================
// PATIENT SAFETY regression: clearing an Rx power must reach the server
// ============================================================================
// PrescriptionForm.handleRxChange stores `undefined` for a blanked box, and
// JSON.stringify DROPS an undefined property. So when the payload builder
// returned `undefined` for a blank, a cleared CYL/AXIS vanished from the PUT
// body entirely -> the backend's exclude_unset read "not sent" -> its
// deep-merge restored the STORED cylinder/axis -> 200 "Prescription updated"
// while the clinical correction was silently discarded, and the patient kept
// being dispensed a toric lens they do not need.
//
// The contract: a blanked box travels as an explicit `null`, which the backend
// (prescriptions._merge_eye_subdoc) treats as "clear this field".

/** The flat form state after an optometrist blanks the CYL and AXIS boxes on
 *  an Rx prefilled by rxToFormInitial (ClinicPrescriptionHistory). */
const CLEARED_CYL_AND_AXIS = {
  sph_od: -2,
  cyl_od: undefined,
  axis_od: undefined,
  add_od: 2,
  pd_od: 32,
  va_od: undefined,
  prism_od: undefined,
  base_od: undefined,
  sph_os: -1.25,
  cyl_os: -0.25,
  axis_os: 85,
  add_os: 2,
  pd_os: 32,
  va_os: undefined,
  prism_os: undefined,
  base_os: undefined,
};

describe('toPrescriptionCreatePayload - clearing a power', () => {
  it('sends a blanked CYL/AXIS as an explicit null, not undefined', () => {
    const payload = toPrescriptionCreatePayload(CLEARED_CYL_AND_AXIS);
    expect(payload.right_eye.cyl).toBeNull();
    expect(payload.right_eye.axis).toBeNull();
    // The powers the clinician did NOT clear are untouched.
    expect(payload.right_eye.sph).toBe('-2');
    expect(payload.right_eye.pd).toBe('32');
  });

  it('SURVIVES JSON serialisation - the cleared keys are still on the wire', () => {
    // This is the assertion that would have caught the regression: an
    // `undefined` value makes JSON.stringify delete the key outright.
    const payload = toPrescriptionCreatePayload(CLEARED_CYL_AND_AXIS);
    const wire = JSON.parse(JSON.stringify(payload));
    expect('cyl' in wire.right_eye).toBe(true);
    expect('axis' in wire.right_eye).toBe(true);
    expect(wire.right_eye.cyl).toBeNull();
    expect(wire.right_eye.axis).toBeNull();
    // The backend deep-merge only clears what it is SENT, so every eye key the
    // form owns must be present on the wire.
    expect(Object.keys(wire.right_eye).sort()).toEqual(
      ['acuity', 'add', 'axis', 'base', 'cyl', 'pd', 'prism', 'sph'].sort()
    );
  });

  it('keeps the untouched eye intact', () => {
    const wire = JSON.parse(
      JSON.stringify(toPrescriptionCreatePayload(CLEARED_CYL_AND_AXIS))
    );
    expect(wire.left_eye.cyl).toBe('-0.25');
    expect(wire.left_eye.axis).toBe(85);
  });

  it('rounds a fractional axis client-side (documents current behaviour)', () => {
    // NOTE: this is why the backend's fractional-axis rejection cannot fire for
    // this client. Tracked separately; pinned here so a change is deliberate.
    const payload = toPrescriptionCreatePayload({ sph_od: -1, axis_od: 90.5 });
    expect(payload.right_eye.axis).toBe(91);
  });

  it('passes an already-nested payload straight through', () => {
    const nested = { right_eye: { sph: '-1.00' }, left_eye: { sph: '-1.00' } };
    expect(toPrescriptionCreatePayload(nested)).toBe(nested);
  });
});
