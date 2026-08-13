// ============================================================================
// IMS 2.0 - Rx POWER values: "blank" is not "0" (PATIENT SAFETY)
// ============================================================================
// A BLANK power means the clinician did not record one. A 0 means they DID
// record one and it is zero -- a plano lens, an eye with no astigmatism, no
// reading addition. Those are different medical facts, and this repo has
// swapped each for the other, in opposite directions, in two places at once:
//
//   * POSLayout built the create payload as `String(rxData.sph_od || 0)`, so an
//     empty SPH / CYL / ADD box left the counter as the STRING "0" -- a positive
//     clinical claim that this patient needs no correction, has no astigmatism
//     and needs no reading add. Nobody made that claim. (`pd` on the very same
//     line already did the right thing: `String(rxData.pd_od || '')`.)
//   * EyeTestForm built the printed Rx card as
//     `parseFloat(finalRxData.rightEye.sphere) || null`, and `0 || null` is
//     null, so a clinician who recorded a plano sphere, a zero cylinder or a
//     zero add got a DASH on the patient's prescription card instead of 0.00.
//
// The rule, entire -- there is no third state:
//   * blank / whitespace / unparseable  -> send NOTHING. `null`, never a
//     fabricated number. The backend already accepts this: EyeData in
//     routers/prescriptions.py declares sph/cyl/add as `Optional[str] = None`.
//   * a recorded 0                      -> preserve it EXACTLY. "0.00" stays
//     "0.00" where the field is a formatted string; numeric 0 stays 0 where the
//     field is a number. Never coerced to null, never re-badged as blank.
//
// These helpers exist so that a CALL SITE CANNOT EXPRESS THE BUG. `|| 0` and
// `|| null` are both a truthiness test on a value whose zero is meaningful;
// there is no way to spell that mistake through this module. Prefer importing
// from here over writing "the obvious one-liner" at a new site -- the one-liner
// is exactly what went wrong four times.
//
// ---------------------------------------------------------------------------
// What this module does NOT govern
// ---------------------------------------------------------------------------
// AXIS and PD. For those a 0 is not a real recorded value:
//   * an AXIS is a meridian notated 1-180 (0 and 180 name the same one, and the
//     backend enforces `ge=1`) -- see utils/rxAxisEntry, which owns it; and
//   * a PD of 0 mm is anatomically impossible.
// So a truthiness test on those two is harmless where it appears. It is still
// commented AT each such line rather than left looking identical to a power.
//
// The emptiness predicate is IMPORTED from rxAxisEntry rather than restated:
// one definition of "was this recorded?" for the whole prescription surface.

import { isBlankValue } from './rxAxisEntry';

export { isBlankValue };

/**
 * A power for the WIRE, where the backend field is a string
 * (`EyeData.sph/cyl/add: Optional[str] = None`).
 *
 * Returns the recorded text VERBATIM (trimmed) when a power was recorded --
 * including "0", "0.00" and "+0.00", which are preserved character for
 * character rather than reformatted -- and `null` when nothing was recorded or
 * what was recorded is not a number at all.
 *
 * `null` (not `undefined`) is deliberate: JSON.stringify DROPS an undefined
 * property, and the backend reads a dropped key as "not sent" rather than
 * "cleared". Same contract as `axisOrNull` and `services/api/sales._rxStr`.
 */
export function powerOrNull(v: unknown): string | null {
  if (isBlankValue(v)) return null;
  const text = typeof v === 'string' ? v.trim() : String(v);
  // Junk ("None", "abc", a stray unit) is ABSENCE, not a power. Coercing with
  // Number -- not parseFloat -- on purpose: parseFloat("1.25 D") silently
  // yields 1.25 and would let a malformed entry print as a real prescription.
  // Number() is what PrescriptionPrint.formatPower and rxAxisEntry already use.
  return Number.isFinite(Number(text)) ? text : null;
}

/**
 * A power as a NUMBER, for print data and in-memory prescription objects.
 *
 * A recorded 0 comes back as numeric 0 -- NOT null. That single fact is the
 * whole reason this function exists instead of `parseFloat(x) || null`.
 */
export function powerNumberOrNull(v: unknown): number | null {
  if (isBlankValue(v)) return null;
  const n = Number(typeof v === 'string' ? v.trim() : v);
  return Number.isFinite(n) ? n : null;
}

/**
 * The first power that was actually RECORDED, across the key aliases a stored
 * eye may use (`sph` / `sphere`, snake_case vs camelCase), else null.
 *
 * This is the read-back twin of `powerNumberOrNull`, and it exists because the
 * obvious `a || b || '0'` chain is wrong TWICE over: it skips past a recorded 0
 * in the first alias to whatever the second one holds, and it invents a 0 when
 * nothing was recorded at all. Falling through junk (a stored "None") to the
 * next alias IS wanted; falling through a real 0 is not.
 */
export function firstRecordedPower(...values: unknown[]): number | null {
  for (const value of values) {
    const n = powerNumberOrNull(value);
    if (n !== null) return n;
  }
  return null;
}

/**
 * A power as the `value` of a CONTROLLED numeric input.
 *
 * `value={eye.sphere || ''}` renders a recorded 0 as an EMPTY BOX: the optician
 * reading it back sees "not recorded" where a plano is on file, and retyping
 * over the apparent blank is how a real value gets lost. A recorded 0 must show
 * as "0"; only a genuine absence shows as empty.
 */
export function powerInputValue(v: unknown): string {
  const n = powerNumberOrNull(v);
  return n === null ? '' : String(n);
}

/**
 * True when a power was recorded at all -- use this to decide whether to RENDER
 * a power, never `{eye.add && ...}`. A recorded ADD of 0 (no reading addition)
 * is a finding the optician needs to see, and truthiness hides it completely.
 */
export function hasRecordedPower(v: unknown): boolean {
  return powerNumberOrNull(v) !== null;
}

/**
 * A power for DISPLAY: "+0.00" / "-1.25" when one was recorded, "-" when none
 * was. Same contract as PrescriptionPrint.formatPower, which is the wording
 * patients already see on the printed card.
 *
 * The dash is the whole point. A display formatter that answers "0.00" for an
 * unrecorded power tells the staff member reading it that this patient is
 * plano; POSLayout's own `if (!n || isNaN(n)) return '0.00'` did exactly that,
 * AND swallowed a genuinely recorded 0 into the same answer, so the two facts
 * were indistinguishable on screen. Absence and plano must never print alike.
 */
export function formatPowerOrDash(v: unknown): string {
  const n = powerNumberOrNull(v);
  if (n === null) return '-';
  return n >= 0 ? `+${n.toFixed(2)}` : n.toFixed(2);
}
