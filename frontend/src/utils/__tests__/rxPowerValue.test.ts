// ============================================================================
// PATIENT SAFETY: "blank" and "0" are DIFFERENT clinical facts
// ============================================================================
// A blank power means the clinician did not record one. A 0 means they DID
// record one and it is zero -- a plano lens, an eye with no astigmatism, no
// reading addition. The repo swapped each for the other, in OPPOSITE
// directions, at the same time:
//
//   * POS persisted `String(rxData.sph_od || 0)`, so an empty box became the
//     string "0" -- a positive claim that no correction is needed.
//   * The clinical print path computed `parseFloat(x) || null`, and
//     `parseFloat("0") || null` is null, so a recorded plano printed as a dash.
//
// Every call site now goes through utils/rxPowerValue. This file is the
// contract those call sites rely on, and it is written so that BOTH failure
// directions are pinned for EVERY function -- a helper that only refuses to
// invent a zero, while still eating a recorded one, would be half a fix.
//
// Each test asserts the REQUIREMENT first, so no incidental check can shadow
// the one that matters.

import { describe, it, expect } from 'vitest';
import {
  firstRecordedPower,
  formatPowerOrDash,
  hasRecordedPower,
  powerInputValue,
  powerNumberOrNull,
  powerOrNull,
} from '../rxPowerValue';

// Everything a clinician can leave behind that means "I did not record this".
const BLANKS: unknown[] = ['', '   ', '\t', null, undefined, NaN];

// Every spelling of a RECORDED zero we have seen on the wire or in the form.
// RxPowerInput normalises SPH/CYL to "0.00" and ADD to "+0.00" on blur; older
// rows and imports carry the bare forms.
const ZEROS: unknown[] = ['0', '0.00', '+0.00', '-0.00', ' 0.00 ', 0];

describe('powerOrNull - the WIRE value (backend EyeData.sph/cyl/add: Optional[str])', () => {
  it.each(BLANKS)('sends NOTHING for the blank %p - never a fabricated "0"', (blank) => {
    // THE REQUIREMENT: an unrecorded power must not become a claim of plano.
    expect(powerOrNull(blank)).toBeNull();
    expect(powerOrNull(blank)).not.toBe('0');
  });

  it.each(ZEROS)('preserves the recorded zero %p exactly', (zero) => {
    // THE REQUIREMENT: a recorded 0 survives, and survives VERBATIM -- the
    // clinician's own notation is what the lab and the patient's card show.
    const out = powerOrNull(zero);
    expect(out).not.toBeNull();
    expect(out).toBe(typeof zero === 'string' ? zero.trim() : String(zero));
  });

  it('preserves ordinary recorded powers verbatim, sign and all', () => {
    expect(powerOrNull('-1.25')).toBe('-1.25');
    expect(powerOrNull('+2.50')).toBe('+2.50');
    expect(powerOrNull(-0.75)).toBe('-0.75');
  });

  it('treats junk as ABSENCE, not as a power', () => {
    // A stray unit or a stringified None is not a prescription. parseFloat
    // would have yielded 1.25 from "1.25 D" and shipped a malformed entry as a
    // real power; Number() refuses it.
    expect(powerOrNull('1.25 D')).toBeNull();
    expect(powerOrNull('None')).toBeNull();
    expect(powerOrNull('abc')).toBeNull();
  });

  it('returns null, never undefined - JSON.stringify DROPS an undefined key', () => {
    // A dropped key reads as "not sent" on the backend, which is a different
    // statement from "not recorded". The distinction is why this is asserted.
    expect(powerOrNull('')).not.toBeUndefined();
    expect(JSON.parse(JSON.stringify({ sph: powerOrNull('') }))).toHaveProperty('sph');
  });
});

describe('powerNumberOrNull - the NUMERIC value (print data, in-memory Rx)', () => {
  it.each(BLANKS)('returns null for the blank %p', (blank) => {
    expect(powerNumberOrNull(blank)).toBeNull();
  });

  it.each(ZEROS)('returns numeric 0 - NOT null - for the recorded zero %p', (zero) => {
    // THE REQUIREMENT, and the entire reason this exists instead of
    // `parseFloat(x) || null`: that expression answers null here.
    //
    // `=== 0` rather than `toBe(0)`: Number("-0.00") is NEGATIVE zero, and
    // toBe uses Object.is, which separates -0 from +0. Clinically "-0.00" and
    // "0.00" are the same plano, so the requirement is numeric zero, not a
    // particular sign of zero.
    const n = powerNumberOrNull(zero);
    expect(n === 0).toBe(true);
    expect(n).not.toBeNull();
  });

  it('parses ordinary recorded powers', () => {
    expect(powerNumberOrNull('-1.25')).toBe(-1.25);
    expect(powerNumberOrNull('+2.50')).toBe(2.5);
  });

  it('treats junk as absence', () => {
    expect(powerNumberOrNull('None')).toBeNull();
    expect(powerNumberOrNull('1.25 D')).toBeNull();
  });
});

describe('firstRecordedPower - alias fall-through that stops at a recorded 0', () => {
  it('STOPS at a recorded 0 in the first alias instead of skipping past it', () => {
    // THE REQUIREMENT. `a || b` is falsy on 0 and would have returned -3.00
    // here -- reading a plano eye as a strong prescription.
    expect(firstRecordedPower('0.00', '-3.00')).toBe(0);
  });

  it('falls through a BLANK alias to the one that was recorded', () => {
    expect(firstRecordedPower('', '-3.00')).toBe(-3);
    expect(firstRecordedPower(null, undefined, '-1.25')).toBe(-1.25);
  });

  it('falls through JUNK to the alias that holds a real power', () => {
    expect(firstRecordedPower('None', '-1.25')).toBe(-1.25);
  });

  it('returns null - never 0 - when NO alias was recorded', () => {
    // THE REQUIREMENT in the other direction: nothing recorded must not become
    // a confident plano. The old `parseFloat(a || b || '0')` returned 0 here.
    expect(firstRecordedPower('', null, undefined)).toBeNull();
    expect(firstRecordedPower()).toBeNull();
  });
});

describe('powerInputValue - a controlled input must SHOW a recorded 0', () => {
  it('renders a recorded 0 as "0", not as an empty box', () => {
    // THE REQUIREMENT: `value={x || ''}` blanked the box, so the optician read
    // "not recorded" over a plano on file and retyping is how it got lost.
    expect(powerInputValue(0)).toBe('0');
    expect(powerInputValue('0.00')).toBe('0');
  });

  it('renders a genuine absence as an empty box', () => {
    expect(powerInputValue('')).toBe('');
    expect(powerInputValue(null)).toBe('');
    expect(powerInputValue(undefined)).toBe('');
  });

  it('round-trips an ordinary power', () => {
    expect(powerInputValue('-1.25')).toBe('-1.25');
  });
});

describe('hasRecordedPower - the render gate', () => {
  it('is TRUE for a recorded 0 - an ADD of 0 is a finding, not a blank', () => {
    // THE REQUIREMENT: `{eye.add && <row/>}` hid "no reading addition"
    // completely, which reads to staff as "the ADD was never measured".
    expect(hasRecordedPower(0)).toBe(true);
    expect(hasRecordedPower('0.00')).toBe(true);
    expect(hasRecordedPower('+0.00')).toBe(true);
  });

  it('is FALSE for every spelling of absence', () => {
    for (const blank of BLANKS) expect(hasRecordedPower(blank)).toBe(false);
    expect(hasRecordedPower('None')).toBe(false);
  });
});

describe('formatPowerOrDash - absence and plano must never print alike', () => {
  it('prints a recorded 0 as "+0.00"', () => {
    // THE REQUIREMENT: a plano IS a prescription and must be legible as one.
    expect(formatPowerOrDash(0)).toBe('+0.00');
    expect(formatPowerOrDash('0.00')).toBe('+0.00');
  });

  it('prints an unrecorded power as "-", never as "0.00"', () => {
    // THE REQUIREMENT in the other direction: POSLayout's old formatter
    // answered "0.00" here, telling staff the patient was plano.
    for (const blank of BLANKS) expect(formatPowerOrDash(blank)).toBe('-');
    expect(formatPowerOrDash('None')).toBe('-');
  });

  it('never renders absence and plano as the same string', () => {
    // The conflation itself, asserted directly.
    expect(formatPowerOrDash(null)).not.toBe(formatPowerOrDash(0));
  });

  it('keeps the sign on ordinary powers', () => {
    expect(formatPowerOrDash(-1.25)).toBe('-1.25');
    expect(formatPowerOrDash('+2.5')).toBe('+2.50');
  });
});
