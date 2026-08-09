// ============================================================================
// IMS 2.0 - POS counter axis entry (PATIENT SAFETY)
// ============================================================================
// The AXIS is the orientation, in whole degrees, at which the lab grinds an
// astigmatic (CYLINDER) correction. POS used to send `rxData.axis_od || 180`:
// when the prescription carried no axis, POS INVENTED one of 180 and sent it
// into the prescription record and on to the lens spec. The lab then ground the
// lens to a guessed orientation -- blur, headaches, and a remake -- and the
// invented value also silently defeated the clinical toric-axis gate (PR #969),
// which exists precisely to reject an axis-less toric Rx. `|| 180` additionally
// REWROTE a legitimate axis of 0, because 0 is falsy.
//
// The rules this module encodes:
//   1. There is NO numeric fallback. A missing axis stays missing.
//   2. Emptiness is checked EXPLICITLY, never by truthiness -- see the "0" note
//      below for exactly what that does and does not mean for the AXIS.
//   3. A non-zero CYLINDER with a missing axis is un-grindable, so the counter
//      is PROMPTED for the axis before the prescription can be saved. A zero or
//      absent cylinder needs no axis and never triggers the prompt.
//
// ---------------------------------------------------------------------------
// "0 is a real clinical value" -- TRUE for the powers, FALSE for the AXIS
// ---------------------------------------------------------------------------
// READ THIS BEFORE CHANGING THE RANGE. The repo-wide rule that a 0 must never
// be treated as absent is about the POWERS: a 0 SPH / CYL / ADD / PRISM is
// meaningful (plano, no astigmatism, no near add, no prism) and blanking one is
// data loss. The AXIS is the exception. An axis is a MERIDIAN, notated 1-180:
// 0 and 180 describe the SAME meridian and optometry writes 180, which is why
// the canonical limits (constants/rxLimits RX_LIMITS.axis) and the backend
// (api/services/rx_validation._validate_axis, EyeData.axis `ge=1, le=180`) both
// start at 1. Do NOT "fix" that ge=1 back to 0 to match the powers rule: a 0
// this module accepted would be 422'd by the server and would strand the sale
// at the counter behind a raw Pydantic error -- the exact outcome the prompt
// exists to avoid.
//
// So the two halves of this module treat 0 DIFFERENTLY, on purpose:
//   * READING existing data (isAxisMissing / axisOrNull / eyesNeedingCounterAxis)
//     treats a stored 0 as PRESENT. We never rewrite what a clinician recorded
//     and never re-badge it as blank -- that was the `|| 180` bug.
//   * ACCEPTING a NEW entry (validateCounterAxis) REJECTS 0, because 1-180 is
//     what the server will store. The staff member means 180; let them say so.
//
// The RANGE is never re-derived here -- it comes from RX_LIMITS.axis.

import { RX_LIMITS, validateRxField } from '../constants/rxLimits';

/** The two eyes, keyed the way the POS prescription form emits them. */
export type EyeKey = 'od' | 'os';

/** Plain-English eye labels, matching the clinical validators' wording. */
export const EYE_LABEL: Record<EyeKey, string> = {
  od: 'Right eye (OD)',
  os: 'Left eye (OS)',
};

/**
 * True when a value was never entered. EXPLICIT emptiness only: `null`,
 * `undefined`, an empty/whitespace string, or a non-finite number (NaN).
 * `0`, `0.0` and `"0"` are PRESENT and MUST return false.
 */
export function isBlankValue(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  if (typeof v === 'string') return v.trim() === '';
  if (typeof v === 'number') return !Number.isFinite(v);
  return false;
}

/** True when the axis was not recorded for this eye. */
export function isAxisMissing(v: unknown): boolean {
  return isBlankValue(v);
}

/**
 * True when the cylinder is a real astigmatic power (present and non-zero).
 * A plano cylinder (0 / "0" / blank) needs no axis.
 */
export function isToricCyl(v: unknown): boolean {
  if (isBlankValue(v)) return false;
  const n = Number(typeof v === 'string' ? v.trim() : v);
  if (!Number.isFinite(n)) return false;
  return Math.abs(n) > 1e-9;
}

/** One eye's flat form values, as the POS PrescriptionForm emits them. */
export interface FlatRxEyes {
  cyl_od?: unknown;
  axis_od?: unknown;
  cyl_os?: unknown;
  axis_os?: unknown;
}

/**
 * Which eyes carry a cylinder but no axis, and therefore cannot be dispensed
 * until someone supplies the axis. Returns `[]` when nothing is missing --
 * including for an eye whose recorded axis is 0.
 */
export function eyesNeedingCounterAxis(rx: FlatRxEyes | null | undefined): EyeKey[] {
  if (!rx || typeof rx !== 'object') return [];
  const out: EyeKey[] = [];
  if (isToricCyl(rx.cyl_od) && isAxisMissing(rx.axis_od)) out.push('od');
  if (isToricCyl(rx.cyl_os) && isAxisMissing(rx.axis_os)) out.push('os');
  return out;
}

/**
 * Normalise an axis for the wire. Returns the number when one is present
 * (INCLUDING 0) and `null` when it is blank -- never a fabricated degree.
 *
 * `null` (not `undefined`) is deliberate: JSON.stringify DROPS an undefined
 * property, and the backend treats a dropped key as "not sent" rather than
 * "cleared". See services/api/sales._rxAxis for the same contract.
 */
export function axisOrNull(v: unknown): number | null {
  if (isBlankValue(v)) return null;
  const n = Number(typeof v === 'string' ? v.trim() : v);
  return Number.isFinite(n) ? n : null;
}

export interface CounterAxisResult {
  /** The accepted axis, or null when the entry was rejected. */
  value: number | null;
  /** A plain-English problem to show the staff member, or null when valid. */
  error: string | null;
}

/**
 * Validate an axis typed at the counter for one eye. Blank is rejected (the
 * whole point of the prompt is that a value must be supplied); anything else is
 * judged by the canonical Rx limits -- a whole number of degrees, 1 to 180.
 *
 * 0 IS REJECTED HERE ON PURPOSE, and that is NOT a violation of the repo's
 * "a 0 is a real clinical value" rule -- see the header. That rule governs the
 * POWERS (sph/cyl/add/prism). An axis is a meridian: 0 and 180 name the SAME
 * one, optometry writes 180, and the server stores 1-180 (EyeData.axis ge=1),
 * so a 0 accepted here would come back as a 422 and strand the sale. If you are
 * here to widen this to 0, change the backend first.
 */
export function validateCounterAxis(raw: unknown, eye: EyeKey): CounterAxisResult {
  const label = EYE_LABEL[eye];
  if (isBlankValue(raw)) {
    return {
      value: null,
      error: `${label} still needs an axis - enter a whole number from ${RX_LIMITS.axis.min} to ${RX_LIMITS.axis.max}`,
    };
  }
  const err = validateRxField('axis', raw as string | number, `${label} `);
  if (err) {
    return {
      value: null,
      error: `${err} - a whole number from ${RX_LIMITS.axis.min} to ${RX_LIMITS.axis.max} degrees`,
    };
  }
  return { value: Number(String(raw).trim()), error: null };
}

/**
 * The message shown when the prompt opens. Mirrors the wording of the clinical
 * rejection added by PR #969 ("Right eye has cylinder -1.25 but no axis - an
 * axis (1-180 whole degrees) is required") so staff meet one voice everywhere.
 */
export function axisPromptReason(eye: EyeKey, cyl: unknown): string {
  const n = Number(String(cyl).trim());
  const power = Number.isFinite(n)
    ? `${n > 0 ? '+' : '-'}${Math.abs(n).toFixed(2)}`
    : String(cyl);
  return `${EYE_LABEL[eye]} has cylinder ${power} but no axis. The lab cannot grind this lens without one.`;
}

// ---------------------------------------------------------------------------
// Provenance: a counter-entered axis is NOT a clinician-recorded axis
// ---------------------------------------------------------------------------
// A stable, greppable marker so a remake dispute can tell the two apart, and so
// "how often does this happen" is a query rather than a guess. It is appended to
// the prescription's `remarks`, which is the only free field the create door
// persists verbatim (routers/prescriptions.create_prescription copies a fixed
// set of keys into rx_data, so any NEW key would be silently dropped -- see the
// PR notes for the durable first-class field this should become).
export const AXIS_COUNTER_MARK = '[AXIS-AT-COUNTER]';

/**
 * Build the provenance note recorded against the prescription.
 * `when` is injectable so the text is testable.
 */
export function buildAxisProvenanceRemark(
  eyes: EyeKey[],
  staffName: string | undefined,
  when: Date = new Date(),
): string {
  const which = eyes.map((e) => EYE_LABEL[e]).join(' and ');
  const who = (staffName || '').trim() || 'counter staff';
  const date = when.toISOString().slice(0, 10);
  return (
    `${AXIS_COUNTER_MARK} ${which} axis entered at the counter by ${who} on ${date} - ` +
    `not recorded by the prescribing clinician`
  );
}

/**
 * Join the prescription remarks, dropping blanks. Keeps the existing doctor
 * note and adds the provenance note beside it rather than replacing it.
 */
export function joinRemarks(...parts: Array<string | null | undefined>): string | undefined {
  const kept = parts.map((p) => (p || '').trim()).filter((p) => p !== '');
  return kept.length > 0 ? kept.join(' | ') : undefined;
}
