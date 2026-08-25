// ============================================================================
// PATIENT SAFETY: the eye validator's machine-readable failure codes
// ============================================================================
// `validateEyeDetailed` exists so a caller can single out ONE failure -- "this
// eye's only problem is a cylinder with no axis" -- without lying about the
// rest of the eye.
//
// The technique it replaces: re-validating the eye with `cyl: undefined` and
// treating "clean" as proof. `validateEyeDetailed` SKIPS undefined fields, so
// removing the cylinder removed the cylinder's own range and step checks too.
// An un-grindable CYL of -8.00 and an off-grid -1.30 both came back clean and
// were handed to the POS counter-axis prompt, which asked staff to type an axis
// for a cylinder no lab can grind and then met a raw server rejection.
//
// These cases pin the property the POS gate depends on: AXIS_REQUIRED_FOR_CYL
// is returned ONLY when the eye has no other fault. If someone reorders the
// checks inside validateEyeDetailed so the pairing rule runs before the
// per-field ones, the CYL cases below flip to AXIS_REQUIRED_FOR_CYL and fail.

import { describe, it, expect } from 'vitest';
import { validateEyeDetailed, validateEyePair, validateRxField } from '../rxLimits';

const LABEL = 'Right eye (OD)';

describe('AXIS_REQUIRED_FOR_CYL means the missing axis is the eye ONLY problem', () => {
  it('is returned for a valid cylinder with no axis', () => {
    const err = validateEyeDetailed({ sph: -2, cyl: -1.25, axis: undefined }, LABEL);
    expect(err?.code).toBe('AXIS_REQUIRED_FOR_CYL');
  });

  it('is returned on the LEFT eye just the same', () => {
    const err = validateEyeDetailed({ sph: -1, cyl: -0.75, axis: undefined }, 'Left eye (OS)');
    expect(err?.code).toBe('AXIS_REQUIRED_FOR_CYL');
  });

  // The two probes that used to slip through. The CYL is itself invalid, so the
  // eye has a second fault and must NOT be reported as an axis-only problem.
  it('is NOT returned when the cylinder is out of range', () => {
    const err = validateEyeDetailed({ sph: -2, cyl: -8.0, axis: undefined }, LABEL);
    expect(err?.code).toBe('FIELD_INVALID');
    expect(err?.field).toBe('cyl');
  });

  it('is NOT returned when the cylinder is off the 0.25 step grid', () => {
    const err = validateEyeDetailed({ sph: -2, cyl: -1.3, axis: undefined }, LABEL);
    expect(err?.code).toBe('FIELD_INVALID');
    expect(err?.field).toBe('cyl');
  });

  it('is NOT returned when another field on the eye is out of range', () => {
    const err = validateEyeDetailed({ sph: -40, cyl: -1.25, axis: undefined }, LABEL);
    expect(err?.code).toBe('FIELD_INVALID');
    expect(err?.field).toBe('sph');
  });

  it('is NOT returned when the VA is not a Snellen value', () => {
    const err = validateEyeDetailed({ sph: -2, cyl: -1.25, axis: undefined, va: '20/20' }, LABEL);
    expect(err?.code).toBe('VA_INVALID');
  });

  it('is NOT returned for the mirror-image failure, an axis with no cylinder', () => {
    const err = validateEyeDetailed({ sph: -2, cyl: undefined, axis: 90 }, LABEL);
    expect(err?.code).toBe('CYL_REQUIRED_FOR_AXIS');
  });

  it('a plano cylinder needs no axis at all', () => {
    expect(validateEyeDetailed({ sph: -2, cyl: 0, axis: undefined }, LABEL)).toBeNull();
  });

  it('a complete toric eye is clean', () => {
    expect(validateEyeDetailed({ sph: -2, cyl: -1.25, axis: 85 }, LABEL)).toBeNull();
  });
});

// validateEyePair is the message of validateEyeDetailed. Its callers
// (components/clinical/PatientIntakeModal, components/pos/PrescriptionForm's CL
// branch) still take a `string | null`, so the wording must not have drifted.
describe('validateEyePair still returns exactly the detailed error message', () => {
  const CASES = [
    { sph: -2, cyl: -1.25, axis: undefined },
    { sph: -2, cyl: -8.0, axis: undefined },
    { sph: -40, cyl: -1.25, axis: undefined },
    { sph: -2, cyl: undefined, axis: 90 },
    { sph: -2, cyl: -1.25, axis: 85, va: '20/20' },
    { sph: -2, cyl: -1.25, axis: 85 },
  ];

  it.each(CASES)('agrees for %j', (eye) => {
    expect(validateEyePair(eye, LABEL)).toBe(validateEyeDetailed(eye, LABEL)?.message ?? null);
  });

  it('keeps the exact pairing wording the clinical screens show', () => {
    expect(validateEyePair({ cyl: -1.25 }, LABEL)).toBe(
      'Right eye (OD) AXIS is required when CYL is set',
    );
    expect(validateEyePair({ axis: 90 }, LABEL)).toBe(
      'Right eye (OD) CYL is required when AXIS is set',
    );
  });
});

// ---------------------------------------------------------------------------
// KERATOMETRY: a range, not a grid.
// ---------------------------------------------------------------------------
// The server range-checks K (30-60 D) and deliberately applies no step, its
// own comment naming the 0.05/0.125 steps real auto-refractometers report.
// This table carried a 0.01 grid, so a stored 43.125 was un-re-savable from
// the Edit screen: the client refused a value the server had accepted.
describe('a K reading is checked for RANGE only', () => {
  for (const reading of ['43.125', '44.375', '41.05', '43.13', '30', '60']) {
    it(`accepts ${reading} D`, () => {
      expect(validateRxField('k', reading, 'Auto-Ref - Right (OD) K1 ')).toBeNull();
    });
  }

  // POSITIVE CONTROL: dropping the grid must not drop the range.
  for (const impossible of ['29.9', '60.1', '0', '9999', '-43']) {
    it(`still REFUSES ${impossible} D`, () => {
      expect(validateRxField('k', impossible, 'Auto-Ref - Right (OD) K1 ')).toMatch(
        /K reading must be between 30.00 and 60.00/,
      );
    });
  }

  // ...and must not leak onto the fields that DO have a grid.
  it('leaves the dioptric 0.25 grid alone', () => {
    expect(validateRxField('sph', '-2.30', 'Right eye (OD) ')).toMatch(/0.25 steps/);
    expect(validateRxField('cyl', '-1.10', 'Right eye (OD) ')).toMatch(/0.25 steps/);
  });
});
