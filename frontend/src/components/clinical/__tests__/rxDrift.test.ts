// ============================================================================
// The exam page's guardrail: a >0.50 D move in under a year, on an adult
// ============================================================================
// Pure-function tests. The page-level tests (EyeExamPage.test.tsx) prove the
// warning lands UNDER the field; these pin the rule itself, edge by edge, so a
// threshold or window that drifts is caught by name.

import { describe, it, expect } from 'vitest';
import { powerDelta, previousRxFromFamily, rxDriftWarning, type PreviousRx } from '../rxDrift';

const NOW = new Date('2026-09-04T10:00:00Z');

const PREV: PreviousRx = {
  prescriptionId: 'rx-1',
  date: '2026-03-12',
  rightEye: { sph: '-1.50', cyl: '-0.50', axis: 180, add: null },
  leftEye: { sph: '-1.75', cyl: null, axis: null, add: null },
};

const warn = (next: string, over: Partial<Parameters<typeof rxDriftWarning>[0]> = {}) =>
  rxDriftWarning({ eye: 'Right', field: 'SPH', previous: PREV, next, age: 34, now: NOW, ...over });

describe('powerDelta', () => {
  it('is next minus previous, to 2 dp, and null when either side is blank', () => {
    expect(powerDelta('-1.50', '-2.25')).toBe(-0.75);
    expect(powerDelta(-1.5, '+0.25')).toBe(1.75);
    expect(powerDelta('', '-2.25')).toBeNull();
    expect(powerDelta('-1.50', '')).toBeNull();
    expect(powerDelta(null, '-1')).toBeNull();
  });
});

describe('rxDriftWarning', () => {
  it('warns when an adult eye moved MORE than 0.50 D against an Rx under a year old', () => {
    const msg = warn('-2.25');
    expect(msg).toMatch(/^Right SPH has moved -0\.75 since Mar 2026/);
    expect(msg).toMatch(/second check/);
  });

  it('warns on a move in the plus direction too', () => {
    expect(warn('-0.75')).toMatch(/moved \+0\.75/);
  });

  it('is silent at EXACTLY 0.50 D (the rule is strictly more than)', () => {
    expect(warn('-2.00')).toBeNull();
    expect(warn('-1.00')).toBeNull();
  });

  it('is silent under 0.50 D', () => {
    expect(warn('-1.75')).toBeNull();
  });

  it('is silent for a child -- a growing eye is expected to move', () => {
    expect(warn('-2.25', { age: 10 })).toBeNull();
    expect(warn('-2.25', { age: 17 })).toBeNull();
    // 18 is an adult.
    expect(warn('-2.25', { age: 18 })).not.toBeNull();
  });

  it('treats an UNKNOWN age as adult -- unknown is not "child"', () => {
    expect(warn('-2.25', { age: undefined })).not.toBeNull();
  });

  it('is silent when the previous Rx is more than a year old', () => {
    const old = { ...PREV, date: '2025-06-01' };
    expect(warn('-2.25', { previous: old })).toBeNull();
    // 364 days is inside the window; 366 is out.
    const inside = { ...PREV, date: new Date(NOW.getTime() - 364 * 86_400_000).toISOString() };
    const outside = { ...PREV, date: new Date(NOW.getTime() - 366 * 86_400_000).toISOString() };
    expect(warn('-2.25', { previous: inside })).not.toBeNull();
    expect(warn('-2.25', { previous: outside })).toBeNull();
  });

  it('is silent with no previous Rx, a blank box, or junk', () => {
    expect(warn('-2.25', { previous: null })).toBeNull();
    expect(warn('')).toBeNull();
    expect(warn('abc')).toBeNull();
  });

  it('checks the CYL against the CYL and names the field', () => {
    expect(warn('-1.25', { field: 'CYL' })).toMatch(/^Right CYL has moved -0\.75/);
    expect(warn('-0.75', { field: 'CYL' })).toBeNull();
  });

  it('compares the LEFT eye with the left eye, not the right', () => {
    // Left prev -1.75: -2.00 is a 0.25 move (silent); against the right eye's
    // -1.50 it would be 0.50 -- also silent -- so use -2.50: left move 0.75.
    expect(warn('-2.50', { eye: 'Left' })).toMatch(/^Left SPH has moved -0\.75/);
    expect(warn('-2.00', { eye: 'Left' })).toBeNull();
  });
});

describe('previousRxFromFamily', () => {
  const rx = (id: string, date: string, extra: Record<string, unknown> = {}) => ({
    prescription_id: id,
    test_date: date,
    right_eye: { sph: '-1.50', cyl: '-0.50', axis: 180 },
    left_eye: { sph: '-1.75' },
    ...extra,
  });

  it('picks the LATEST spectacle Rx of the target patient, and the earliest as "wearing since"', () => {
    const members = [
      { patient_id: 'p1', prescriptions: [rx('a', '2024-01-10'), rx('c', '2026-03-12'), rx('b', '2025-02-01')] },
      { patient_id: 'p2', prescriptions: [rx('z', '2026-08-30')] },
    ];
    const { previous, earliest } = previousRxFromFamily(members, { target: 'p1' });
    expect(previous?.prescriptionId).toBe('c');
    expect(previous?.rightEye.sph).toBe('-1.50');
    expect(earliest).toBe('2024-01-10');
  });

  it('skips a contact-lens Rx and the Rx minted by the exam being amended', () => {
    const members = [
      {
        patient_id: 'p1',
        prescriptions: [
          rx('cl', '2026-08-01', { rx_kind: 'CONTACT_LENS' }),
          rx('mine', '2026-07-01', { eye_test_id: 'test-9' }),
          rx('older', '2026-03-12'),
        ],
      },
    ];
    const { previous } = previousRxFromFamily(members, { target: 'p1', excludeTestId: 'test-9' });
    expect(previous?.prescriptionId).toBe('older');
  });

  it('falls back to the only member when the target does not match (holder without a patient id)', () => {
    const members = [{ patient_id: 'c1', prescriptions: [rx('a', '2026-03-12')] }];
    expect(previousRxFromFamily(members, { target: null })?.previous?.prescriptionId).toBe('a');
  });

  it('returns nothing for no members, no prescriptions, or junk dates', () => {
    expect(previousRxFromFamily([], { target: 'p1' })).toEqual({ previous: null, earliest: null });
    expect(previousRxFromFamily(undefined, { target: 'p1' }).previous).toBeNull();
    const junk = [{ patient_id: 'p1', prescriptions: [rx('a', 'not-a-date')] }];
    expect(previousRxFromFamily(junk, { target: 'p1' }).previous).toBeNull();
  });
});
