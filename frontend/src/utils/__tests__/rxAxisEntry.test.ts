// ============================================================================
// PATIENT SAFETY regression: POS must never fabricate an AXIS
// ============================================================================
// POS used to send `rxData.axis_od || 180`. When a prescription carried no
// axis, POS invented one of 180 and sent it into the prescription record and on
// to the lens spec, so the lab ground an astigmatic correction to a guessed
// orientation (blur, headaches, a remake) -- and the invented value silently
// defeated the clinical toric-axis gate added by PR #969. Because `||` is a
// truthiness test it ALSO rewrote a legitimate recorded axis of 0.
//
// The contract pinned here:
//   * a non-zero CYL with no axis => that eye is flagged for the counter prompt;
//   * EACH EYE is judged INDEPENDENTLY (a per-eye asymmetry would be invisible
//     in a way uniform fabrication is not -- this is the case that catches it);
//   * an axis of 0 is PRESENT: no prompt, no rewrite;
//   * a zero / absent cylinder never needs an axis;
//   * a blank axis normalises to null, NEVER to a number.

import { describe, it, expect } from 'vitest';

import {
  AXIS_SOURCE_COUNTER,
  axisOrNull,
  axisPromptReason,
  axisSourceFor,
  eyesNeedingCounterAxis,
  isAxisMissing,
  isToricCyl,
  validateCounterAxis,
} from '../rxAxisEntry';

describe('emptiness is explicit, never truthiness', () => {
  it('treats only null / undefined / blank / NaN as missing', () => {
    expect(isAxisMissing(null)).toBe(true);
    expect(isAxisMissing(undefined)).toBe(true);
    expect(isAxisMissing('')).toBe(true);
    expect(isAxisMissing('   ')).toBe(true);
    expect(isAxisMissing(Number.NaN)).toBe(true);
  });

  it('treats 0, 0.0 and "0" as PRESENT values (a real axis of 0)', () => {
    expect(isAxisMissing(0)).toBe(false);
    expect(isAxisMissing(0.0)).toBe(false);
    expect(isAxisMissing('0')).toBe(false);
  });

  it('treats a zero cylinder as non-toric and a non-zero one as toric', () => {
    expect(isToricCyl(0)).toBe(false);
    expect(isToricCyl('0')).toBe(false);
    expect(isToricCyl('')).toBe(false);
    expect(isToricCyl(null)).toBe(false);
    expect(isToricCyl(-1.25)).toBe(true);
    expect(isToricCyl('-0.25')).toBe(true);
    expect(isToricCyl(0.5)).toBe(true);
  });
});

describe('the prompt trigger: cylinder present, axis missing', () => {
  it('flags a toric Rx with no axis', () => {
    expect(
      eyesNeedingCounterAxis({ cyl_od: -1.25, axis_od: undefined, cyl_os: -0.75, axis_os: undefined }),
    ).toEqual(['od', 'os']);
  });

  // THE ASYMMETRY GUARD. A half-applied fix -- one eye protected, the other
  // still silently guessed -- is worse than the original uniform bug, because
  // nobody would think to look for it. Each eye is asserted on its own.
  it('flags the RIGHT eye alone when only the right eye is missing its axis', () => {
    expect(
      eyesNeedingCounterAxis({ cyl_od: -1.25, axis_od: undefined, cyl_os: -0.75, axis_os: 90 }),
    ).toEqual(['od']);
  });

  it('flags the LEFT eye alone when only the left eye is missing its axis', () => {
    expect(
      eyesNeedingCounterAxis({ cyl_od: -1.25, axis_od: 10, cyl_os: -0.75, axis_os: undefined }),
    ).toEqual(['os']);
  });

  it('does NOT flag an axis of 0 -- it is a real clinical reading, on either eye', () => {
    expect(
      eyesNeedingCounterAxis({ cyl_od: -1.25, axis_od: 0, cyl_os: -0.75, axis_os: 0 }),
    ).toEqual([]);
    expect(
      eyesNeedingCounterAxis({ cyl_od: -1.25, axis_od: '0', cyl_os: -0.75, axis_os: 0.0 }),
    ).toEqual([]);
  });

  it('does NOT flag a non-toric Rx with no axis -- no cylinder needs no axis', () => {
    expect(
      eyesNeedingCounterAxis({ cyl_od: 0, axis_od: undefined, cyl_os: undefined, axis_os: undefined }),
    ).toEqual([]);
    expect(
      eyesNeedingCounterAxis({ cyl_od: '0', axis_od: '', cyl_os: '', axis_os: null }),
    ).toEqual([]);
  });

  it('is safe on empty / missing input', () => {
    expect(eyesNeedingCounterAxis(null)).toEqual([]);
    expect(eyesNeedingCounterAxis(undefined)).toEqual([]);
    expect(eyesNeedingCounterAxis({})).toEqual([]);
  });
});

describe('axisOrNull: no numeric fallback, ever', () => {
  it('returns null for a blank axis rather than a fabricated degree', () => {
    expect(axisOrNull(undefined)).toBeNull();
    expect(axisOrNull(null)).toBeNull();
    expect(axisOrNull('')).toBeNull();
    expect(axisOrNull('  ')).toBeNull();
    expect(axisOrNull('abc')).toBeNull();
  });

  it('passes a recorded axis through untouched, INCLUDING 0', () => {
    expect(axisOrNull(0)).toBe(0);
    expect(axisOrNull('0')).toBe(0);
    expect(axisOrNull(90)).toBe(90);
    expect(axisOrNull('180')).toBe(180);
    expect(axisOrNull(1)).toBe(1);
  });

  it('never returns 180 for an absent value (the old `|| 180` bug)', () => {
    for (const blank of [undefined, null, '', '   ']) {
      expect(axisOrNull(blank)).not.toBe(180);
    }
  });
});

describe('counter axis entry validation', () => {
  it('rejects a blank entry and says what is needed', () => {
    const r = validateCounterAxis('', 'od');
    expect(r.value).toBeNull();
    expect(r.error).toMatch(/Right eye \(OD\) still needs an axis/);
  });

  it('rejects an out-of-range entry', () => {
    expect(validateCounterAxis('181', 'od').value).toBeNull();
    expect(validateCounterAxis('181', 'od').error).toMatch(/AXIS/);
    expect(validateCounterAxis('-5', 'os').value).toBeNull();
    expect(validateCounterAxis('999', 'os').value).toBeNull();
  });

  it('rejects a fractional axis (the workshop spec has no fractional degrees)', () => {
    const r = validateCounterAxis('90.5', 'od');
    expect(r.value).toBeNull();
    expect(r.error).toMatch(/whole number/);
  });

  it('rejects junk', () => {
    expect(validateCounterAxis('abc', 'od').value).toBeNull();
    expect(validateCounterAxis('9O', 'od').value).toBeNull();
  });

  it('accepts a whole degree inside the canonical clinical range', () => {
    expect(validateCounterAxis('1', 'od')).toEqual({ value: 1, error: null });
    expect(validateCounterAxis('90', 'od')).toEqual({ value: 90, error: null });
    expect(validateCounterAxis('180', 'os')).toEqual({ value: 180, error: null });
    expect(validateCounterAxis(' 45 ', 'os')).toEqual({ value: 45, error: null });
  });

  // The canonical repo-wide domain is 1-180 whole degrees (constants/rxLimits
  // RX_LIMITS.axis AND the backend's rx_validation._validate_axis /
  // EyeData.axis ge=1). An axis is a MERIDIAN: 0 and 180 name the same one and
  // optometry writes 180. So a NEW entry of 0 is rejected -- the server would
  // 422 it and strand the sale. This is deliberate and settled; it is not the
  // repo's "0 is a real clinical value" rule being broken (that rule is about
  // the POWERS: sph/cyl/add/prism). See the rxAxisEntry header before changing.
  it('rejects a NEW entry of 0 -- an axis is a meridian, 1-180 (settled)', () => {
    expect(validateCounterAxis('0', 'od').value).toBeNull();
  });

  // ...but READING existing data still treats a stored 0 as present, so we
  // never rewrite or re-badge what a clinician already recorded. The two halves
  // differ on purpose.
  it('still reads a STORED axis of 0 as present -- reading and entry differ', () => {
    expect(isAxisMissing(0)).toBe(false);
    expect(axisOrNull(0)).toBe(0);
    expect(eyesNeedingCounterAxis({ cyl_od: -1.25, axis_od: 0 })).toEqual([]);
  });
});

describe('prompt copy names the eye and the cylinder', () => {
  it('mirrors the clinical rejection wording from PR #969', () => {
    expect(axisPromptReason('od', -1.25)).toBe(
      'Right eye (OD) has cylinder -1.25 but no axis. The lab cannot grind this lens without one.',
    );
    expect(axisPromptReason('os', '0.75')).toMatch(/^Left eye \(OS\) has cylinder \+0\.75 but no axis\./);
  });
});

describe('provenance: a counter-entered axis is not a clinician-recorded one', () => {
  // PINS THE LITERAL. Renaming this value silently orphans every historical row
  // and every saved query -- and the backend accepts it as a Literal, so a
  // rename becomes a 422 at the counter. It must move with its migration, and
  // failing this test is the reminder.
  it('has a stable wire value that must not be renamed casually', () => {
    expect(AXIS_SOURCE_COUNTER).toBe('COUNTER_ENTERED');
  });

  it('stamps only the eyes that were entered at the counter', () => {
    expect(axisSourceFor('od', ['od'])).toBe(AXIS_SOURCE_COUNTER);
    expect(axisSourceFor('os', ['od'])).toBeUndefined();
    expect(axisSourceFor('os', ['od', 'os'])).toBe(AXIS_SOURCE_COUNTER);
  });

  it('stamps nothing when no axis was entered at the counter', () => {
    expect(axisSourceFor('od', [])).toBeUndefined();
    expect(axisSourceFor('os', [])).toBeUndefined();
  });

  // The marker must be a machine value, not prose: it rides on the eye
  // sub-document, and anything sentence-like suggests it belongs in `remarks`
  // -- which is published to the patient portal and printed on the Rx card.
  it('is an opaque machine token, not patient-facing prose', () => {
    expect(AXIS_SOURCE_COUNTER).toMatch(/^[A-Z_]+$/);
    expect(AXIS_SOURCE_COUNTER).not.toMatch(/\s/);
  });
});
