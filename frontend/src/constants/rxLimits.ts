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
//   PD   :     40 ..     80 mm (binocular / IPD), 0.5 step (a measurement, no diopter grid)
//   PD_MONO :  20 ..     45 mm (PER-EYE monocular PD, ~half the binocular)
//   K    :     30 ..     60 D  (keratometry / corneal curvature, never signed)
//   CL Base Curve : 8.0 .. 9.5 mm, 0.1 step
//   CL Diameter   : 13.0 .. 15.0 mm, 0.1 step
//
// PD COMES IN TWO SHAPES and they are NOT interchangeable. A per-eye PD box is
// MONOCULAR (about half the binocular value: 32.5mm is a perfectly ordinary
// reading). Validating a per-eye box against the 40-80 binocular range refused
// every correct monocular entry. The backend has always distinguished the two
// (rx_validation._RX_LIMITS "pd" vs "pd_mono", and EyeData.validate_pd uses
// pd_mono); the backend is the source of truth and this table now agrees.
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
  | 'pd_mono'
  | 'k'
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
  pd_mono: { min: 20, max: 45, step: 0.5, label: 'PD' },
  // KERATOMETRY has NO step grid. The server says so in as many words
  // (rx_validation _RX_LIMITS: "NOT on the 0.25 grid (devices report
  // 0.05/0.125 steps)") and range-checks it alone. A 0.01 grid here refused
  // 43.125 -- a reading an auto-refractometer genuinely produces and the
  // server genuinely stores -- so a record written by a device or the API
  // could not be re-saved from the Edit screen. `isOnStep` treats a step of
  // 0 as "no grid".
  k: { min: 30, max: 60, step: 0, label: 'K reading' },
  base_curve: { min: 8.0, max: 9.5, step: 0.1, label: 'Base Curve' },
  diameter: { min: 13.0, max: 15.0, step: 0.1, label: 'Diameter' },
};

/**
 * THE allowed visual-acuity values: the Snellen fractions at 6 metres PLUS the
 * four standard low-vision notations. Empty string = not recorded.
 *
 * Counting Fingers / Hand Movement / Perception of Light / No Perception of
 * Light are everyday findings in a dense cataract, an advanced glaucoma or a
 * post-op eye. A gate that offers only Snellen does not make the finding go
 * away -- it makes the optometrist pick the nearest fraction, and a CF eye
 * lands in the record as 6/60. So this set is the SERVER's set, exactly:
 * backend/api/services/rx_validation.py `_VA_SET`, pinned by
 * backend/tests/test_va_set_parity.py so the two can never drift again.
 *
 * Every VA dropdown in the app reads VA_OPTIONS below. Do not start a sixth
 * copy of this list.
 */
export const VA_SET = [
  '6/6', '6/9', '6/12', '6/18', '6/24', '6/36', '6/60',
  'CF', 'HM', 'PL', 'NPL',
] as const;
export type VAValue = (typeof VA_SET)[number];

/** VA_SET with a leading blank, for a `<select>` where "not recorded" is a
 *  legitimate choice. THE list every VA dropdown renders. */
export const VA_OPTIONS = ['', ...VA_SET] as const;

/** True if `v` is an allowed VA string (blank passes as "not recorded").
 *  Case-insensitive on the letter notations, matching the server, so a typed
 *  `cf` is not refused here and accepted there. */
export function isValidVA(v: string | null | undefined): boolean {
  if (v === null || v === undefined) return true;
  const s = String(v).trim().toUpperCase();
  if (s === '') return true;
  return (VA_SET as readonly string[]).some((allowed) => allowed.toUpperCase() === s);
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
  /** BINOCULAR / total PD (IPD), 40-80mm. */
  pd?: string | number | null;
  /** PER-EYE monocular PD, 20-45mm. Use THIS for an "OD PD" / "OS PD" box. */
  pd_mono?: string | number | null;
  va?: string | null;
  base_curve?: string | number | null;
  diameter?: string | number | null;
}

/**
 * Machine-readable reason an eye failed validation.
 *
 * Callers that need to treat ONE failure differently must branch on this code,
 * never on the message text and never by deleting a field and re-validating.
 * Deleting a field to ask "was that its only problem?" silently removes that
 * field's OWN range and step checks too (the loop below skips `undefined`), so
 * an un-grindable CYL of -8.00 or an off-grid -1.30 looked like a clean eye
 * with a missing axis. Codes make the question answerable without lying.
 */
export type RxEyeErrorCode =
  /** A single field is out of range / off the step grid / not a whole number. */
  | 'FIELD_INVALID'
  /** VA is not one of the allowed Snellen values. */
  | 'VA_INVALID'
  /** Non-zero CYL recorded with no AXIS. THE toric-axis case. */
  | 'AXIS_REQUIRED_FOR_CYL'
  /** AXIS recorded with no CYL -- the mirror-image pairing failure. */
  | 'CYL_REQUIRED_FOR_AXIS';

export interface RxEyeError {
  code: RxEyeErrorCode;
  /** Which field failed, for FIELD_INVALID. */
  field?: RxLimitField;
  /** The same plain-English message `validateEyePair` returns. */
  message: string;
}

/**
 * Validate one eye's full set and return the FIRST failure as a coded error,
 * or null when the whole eye is valid. `validateEyePair` is this function's
 * message, so the two can never disagree about whether an eye is valid.
 *
 * ORDER IS LOAD-BEARING. Every single-field check (including VA) runs BEFORE
 * the CYL<->AXIS pairing rule, so `AXIS_REQUIRED_FOR_CYL` is returned ONLY when
 * the eye has no other fault. That is what makes "is the missing axis this
 * eye's only problem?" answerable as `code === 'AXIS_REQUIRED_FOR_CYL'`.
 */
export function validateEyeDetailed(eye: RxEyeValues, label = ''): RxEyeError | null {
  const prefix = label ? `${label} ` : '';

  for (const f of [
    'sph', 'cyl', 'axis', 'add', 'pd', 'pd_mono', 'base_curve', 'diameter',
  ] as const) {
    if (eye[f] === undefined) continue;
    const err = validateRxField(f, eye[f], prefix);
    if (err) return { code: 'FIELD_INVALID', field: f, message: err };
  }

  // VA restricted to the Snellen set. Checked here, with the other per-field
  // rules, rather than after the pairing rule: a bad VA is a fault of its own
  // and must not be masked by the axis prompt taking the eye first.
  if (eye.va !== undefined && !isValidVA(eye.va)) {
    return {
      code: 'VA_INVALID',
      message: `${prefix}VA must be one of ${VA_SET.join(', ')}`,
    };
  }

  // Cross-field: CYL <-> AXIS pairing.
  const cyl = parseRxNumber(eye.cyl);
  const axis = parseRxNumber(eye.axis);
  const cylSet = cyl !== null && Number.isFinite(cyl) && Math.abs(cyl) > 1e-9;
  const axisSet = axis !== null && Number.isFinite(axis);
  if (cylSet && !axisSet) {
    return {
      code: 'AXIS_REQUIRED_FOR_CYL',
      message: `${prefix}AXIS is required when CYL is set`,
    };
  }
  if (axisSet && !cylSet) {
    return {
      code: 'CYL_REQUIRED_FOR_AXIS',
      message: `${prefix}CYL is required when AXIS is set`,
    };
  }

  return null;
}

/**
 * Validate one eye's full set, including the cross-field rules:
 *   - CYL set (non-zero) requires AXIS, and AXIS set requires a CYL.
 *   - ADD plus-only + range; PD/CL ranges; VA in the allowed set.
 * Returns the FIRST error message or null when the whole eye is valid.
 *
 * Thin wrapper over `validateEyeDetailed` so every existing caller keeps the
 * same `string | null` contract and the same wording.
 */
export function validateEyePair(eye: RxEyeValues, label = ''): string | null {
  return validateEyeDetailed(eye, label)?.message ?? null;
}
