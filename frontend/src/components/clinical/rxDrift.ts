// ============================================================================
// IMS 2.0 - Rx DRIFT: the exam page's guardrail, on the field
// ============================================================================
// When an eye moves more than 0.50 D against the patient's previous
// prescription within a year, on an adult, the exam page shows a warning
// UNDER THAT FIELD -- not a toast that disappears while the optometrist looks
// away. A move that size in under a year is worth a second check before it
// becomes a remake.
//
// Pure functions, no React: the page feeds them the previous Rx (picked from
// the family-Rx payload here too) and the value being typed.

import { formatRxPower } from './RxPowerInput';

/** Dioptres. Strictly MORE than this is a drift. */
export const DRIFT_THRESHOLD_D = 0.5;
/** The previous Rx must be at most this old for the comparison to mean much. */
export const DRIFT_WINDOW_DAYS = 365;
/** A child's eye is expected to move; the guardrail is for adults. */
export const ADULT_AGE_YEARS = 18;

export interface PreviousRxEye {
  sph: string | number | null;
  cyl: string | number | null;
  axis: string | number | null;
  add: string | number | null;
}

export interface PreviousRx {
  prescriptionId: string | null;
  /** ISO date the Rx was issued. */
  date: string;
  rightEye: PreviousRxEye;
  leftEye: PreviousRxEye;
}

function num(v: unknown): number | null {
  if (v === undefined || v === null || v === '') return null;
  const n = Number(String(v).trim());
  return Number.isFinite(n) ? n : null;
}

/** next - prev, to 2 dp; null when either side is not a recorded number. */
export function powerDelta(prev: unknown, next: unknown): number | null {
  const a = num(prev);
  const b = num(next);
  if (a === null || b === null) return null;
  return Math.round((b - a) * 100) / 100;
}

/** "Mar 2026" for a date; '' for junk. */
export function monthYear(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
}

export function isWithinDays(iso: string, days: number, now: Date = new Date()): boolean {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  const age = (now.getTime() - d.getTime()) / 86_400_000;
  return age >= 0 && age <= days;
}

export interface DriftArgs {
  eye: 'Right' | 'Left';
  field: 'SPH' | 'CYL';
  previous: PreviousRx | null | undefined;
  /** The value in the box right now (signed string, as RxPowerInput holds it). */
  next: string;
  age?: number;
  now?: Date;
}

/**
 * The warning to show under a power box, or null.
 *
 * Fires only when ALL hold: there is a previous Rx, it is under a year old,
 * the patient is an adult (or the age is unknown -- unknown is not "child"),
 * and the move is strictly more than 0.50 D.
 */
export function rxDriftWarning({ eye, field, previous, next, age, now }: DriftArgs): string | null {
  if (!previous) return null;
  if (age !== undefined && age < ADULT_AGE_YEARS) return null;
  if (!isWithinDays(previous.date, DRIFT_WINDOW_DAYS, now)) return null;
  const prevEye = eye === 'Right' ? previous.rightEye : previous.leftEye;
  const delta = powerDelta(field === 'SPH' ? prevEye.sph : prevEye.cyl, next);
  if (delta === null || Math.abs(delta) <= DRIFT_THRESHOLD_D) return null;
  return (
    `${eye} ${field} has moved ${formatRxPower(String(delta), 'SPH')} since ${monthYear(previous.date)}. ` +
    `Over ${DRIFT_THRESHOLD_D.toFixed(2)} D in under a year on an adult is worth a second check ` +
    'before it becomes a remake.'
  );
}

// ---------------------------------------------------------------------------
// Picking the previous Rx out of GET /prescriptions/family/{customer_id}
// ---------------------------------------------------------------------------

type Rec = Record<string, unknown>;
const obj = (v: unknown): Rec => (v && typeof v === 'object' ? (v as Rec) : {});

function rxDate(rx: Rec): string {
  const raw = rx.prescription_date ?? rx.prescriptionDate ?? rx.test_date ?? rx.testDate ?? rx.created_at;
  return raw === undefined || raw === null ? '' : String(raw);
}

function eyeOf(raw: unknown): PreviousRxEye {
  const e = obj(raw);
  const pick = (...keys: string[]): string | number | null => {
    for (const k of keys) {
      const v = e[k];
      if (v !== undefined && v !== null && v !== '') return v as string | number;
    }
    return null;
  };
  return {
    sph: pick('sph', 'sphere'),
    cyl: pick('cyl', 'cylinder'),
    axis: pick('axis'),
    add: pick('add', 'addition', 'add_power'),
  };
}

export interface FamilyMemberLike {
  patient_id?: string | null;
  prescriptions?: unknown[];
}

/**
 * The patient's most recent EARLIER spectacle Rx, and the date of their
 * earliest one ("wearing since").
 *
 * `target` is the patient being examined (the family member's id, falling
 * back to the account holder's customer id -- that is what complete_test
 * stamps on a holder's own Rx). Contact-lens Rx are skipped (a CL power is
 * not comparable to a spectacle power), and so is the Rx minted by the very
 * exam being amended.
 */
export function previousRxFromFamily(
  members: ReadonlyArray<FamilyMemberLike> | null | undefined,
  opts: { target?: string | null; excludeTestId?: string | null },
): { previous: PreviousRx | null; earliest: string | null } {
  const list = members ?? [];
  let mine = list.filter((m) => opts.target && m.patient_id === opts.target);
  if (mine.length === 0 && list.length === 1) mine = [list[0]];
  const rows = mine
    .flatMap((m) => (Array.isArray(m.prescriptions) ? m.prescriptions : []))
    .map(obj)
    .filter((rx) => (rx.rx_kind ?? rx.rxKind) !== 'CONTACT_LENS')
    .filter((rx) => !opts.excludeTestId || (rx.eye_test_id ?? rx.eyeTestId) !== opts.excludeTestId)
    .filter((rx) => !Number.isNaN(new Date(rxDate(rx)).getTime()) && rxDate(rx) !== '')
    .sort((a, b) => new Date(rxDate(b)).getTime() - new Date(rxDate(a)).getTime());
  if (rows.length === 0) return { previous: null, earliest: null };
  const latest = rows[0];
  return {
    previous: {
      prescriptionId: (latest.prescription_id ?? latest.id ?? null) as string | null,
      date: rxDate(latest),
      rightEye: eyeOf(latest.right_eye ?? latest.rightEye),
      leftEye: eyeOf(latest.left_eye ?? latest.leftEye),
    },
    earliest: rxDate(rows[rows.length - 1]),
  };
}
