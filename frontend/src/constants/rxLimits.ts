// ============================================================================
// IMS 2.0 - Canonical Rx (prescription) realistic limits - SINGLE SOURCE
// ============================================================================
// CLINICAL-CRITICAL. These are the owner-approved ("wider extremes") realistic
// bounds every client-side Rx validator MUST use. The BACKEND is the ultimate
// gate (backend/api/services/rx_validation.py) and MUST agree with this table;
// if you change a bound here, change it there (and its tests) too.
//
//   SPH  : -25.00 .. +25.00, 0.25 step
//   CYL  :  -6.00 ..  +6.00, 0.25 step
//   AXIS :      1 ..    180, whole degrees; MANDATORY when CYL is set (& v.v.)
//   ADD  :  +0.75 ..  +4.00, 0.25 step, PLUS-ONLY (a near add is never minus)
//   PD   :     40 ..     80 mm (binocular), 0.5 step (a measurement, no diopter grid)
//   CL Base Curve : 8.0 .. 9.5 mm, 0.1 step
//   CL Diameter   : 13.0 .. 15.0 mm, 0.1 step
//
// Cross-field rules: CYL<->AXIS are paired (one present requires the other);
// ADD is plus-only; VA is restricted to the Snellen set below.
//
// A leading "+" MUST be accepted everywhere: Number("+5") === 5, so parse with
// Number()/parseFloat() (both handle "+5.00") rather than a regex that rejects
// the sign.
// ============================================================================

export type RxLimitField =
  | 'sph'
  | 'cyl'
  | 'axis'
  | 'add'
  | 'pd'
  | 'base_curve'
  | 'diameter';

export interface RxLimit {
  min: number;
  max: number;
  step: number;
  /** ADD is plus-only (magnitude is what matters; a minus is a data error). */
  plusOnly?: boolean;
  /** AXIS is a whole number of degrees. */
  wholeNumber?: boolean;
  /** Human label for messages. */
  label: string;
}

export const RX_LIMITS: Record<RxLimitField, RxLimit> = {
  sph: { min: -25.0, max: 25.0, step: 0.25, label: 'SPH' },
  cyl: { min: -6.0, max: 6.0, step: 0.25, label: 'CYL' },
  axis: { min: 1, max: 180, step: 1, wholeNumber: true, label: 'AXIS' },
  add: { min: 0.75, max: 4.0, step: 0.25, plusOnly: true, label: 'ADD' },
  pd: { min: 40, max: 80, step: 0.5, label: 'PD' },
  base_curve: { min: 8.0, max: 9.5, step: 0.1, label: 'Base Curve' },
  diameter: { min: 13.0, max: 15.0, step: 0.1, label: 'Diameter' },
};

/** Allowed visual-acuity (Snellen, 6m) values. Empty string = not recorded. */
export const VA_SET = ['6/6', '6/9', '6/12', '6/18', '6/24', '6/36', '6/60'] as const;
export type VAValue = (typeof VA_SET)[number];

/** True if `v` is an allowed VA string (blank passes as "not recorded"). */
export function isValidVA(v: string | null | undefined): boolean {
  if (v === null || v === undefined) return true;
  const s = String(v).trim();
  if (s === '') return true;
  return (VA_SET as readonly string[]).includes(s);
}

/**
 * Parse an Rx numeric string, accepting a leading "+" (Number('+5') === 5).
 * Returns null for blank/undefined (= "not entered"), NaN for non-numeric.
 */
export function parseRxNumber(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  if (s === '') return null;
  // Number() natively accepts a leading "+"/"-" and a leading/trailing dot.
  return Number(s);
}

/** True when `n` sits on the field's step grid (float-drift tolerant). */
export function isOnStep(n: number, step: number): boolean {
  if (step <= 0) return true;
  return Math.abs(Math.round(n / step) - n / step) < 1e-6;
}

/**
 * Validate a single Rx value against RX_LIMITS. Returns an error MESSAGE string
 * or null when valid. A blank/absent value is valid here (a field may be left
 * empty); required-ness (e.g. AXIS when CYL is set) is a cross-field concern
 * handled by validateEyePair below.
 *
 * `prefix` is prepended to the message (e.g. "Right eye (OD) ").
 */
export function validateRxField(
  field: RxLimitField,
  value: string | number | null | undefined,
  prefix = '',
): string | null {
  const lim = RX_LIMITS[field];
  const num = parseRxNumber(value);
  if (num === null) return null; // blank -> not entered -> nothing to validate here
  if (!Number.isFinite(num)) {
    return `${prefix}${lim.label} must be a valid number`;
  }
  // ADD is plus-only: a stored negative add is a data-entry error.
  if (lim.plusOnly && num < 0) {
    return `${prefix}${lim.label} must be positive (plus-only)`;
  }
  const min = lim.plusOnly ? Math.abs(lim.min) : lim.min;
  const val = lim.plusOnly ? Math.abs(num) : num;
  if (val < min || val > lim.max) {
    const lo = lim.plusOnly ? `+${min.toFixed(2)}` : min.toFixed(2);
    const hi = lim.plusOnly ? `+${lim.max.toFixed(2)}` : lim.max.toFixed(2);
    return `${prefix}${lim.label} must be between ${lo} and ${hi}`;
  }
  if (lim.wholeNumber && !Number.isInteger(num)) {
    return `${prefix}${lim.label} must be a whole number`;
  }
  if (!lim.wholeNumber && !isOnStep(val, lim.step)) {
    return `${prefix}${lim.label} must be in ${lim.step} steps`;
  }
  return null;
}

/** One eye's raw string values (any subset may be blank). */
export interface RxEyeValues {
  sph?: string | number | null;
  cyl?: string | number | null;
  axis?: string | number | null;
  add?: string | number | null;
  pd?: string | number | null;
  va?: string | null;
  base_curve?: string | number | null;
  diameter?: string | number | null;
}

/**
 * Validate one eye's full set, including the cross-field rules:
 *   - CYL set (non-zero) requires AXIS, and AXIS set requires a CYL.
 *   - ADD plus-only + range; PD/CL ranges; VA in the allowed set.
 * Returns the FIRST error message or null when the whole eye is valid.
 */
export function validateEyePair(eye: RxEyeValues, label = ''): string | null {
  const prefix = label ? `${label} ` : '';

  for (const f of ['sph', 'cyl', 'axis', 'add', 'pd', 'base_curve', 'diameter'] as const) {
    if (eye[f] === undefined) continue;
    const err = validateRxField(f, eye[f], prefix);
    if (err) return err;
  }

  // Cross-field: CYL <-> AXIS pairing.
  const cyl = parseRxNumber(eye.cyl);
  const axis = parseRxNumber(eye.axis);
  const cylSet = cyl !== null && Number.isFinite(cyl) && Math.abs(cyl) > 1e-9;
  const axisSet = axis !== null && Number.isFinite(axis);
  if (cylSet && !axisSet) {
    return `${prefix}AXIS is required when CYL is set`;
  }
  if (axisSet && !cylSet) {
    return `${prefix}CYL is required when AXIS is set`;
  }

  // VA restricted to the Snellen set.
  if (eye.va !== undefined && !isValidVA(eye.va)) {
    return `${prefix}VA must be one of ${VA_SET.join(', ')}`;
  }

  return null;
}
