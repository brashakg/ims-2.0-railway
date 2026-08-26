// ============================================================================
// IMS 2.0 - Range-checking the SEVEN-TAB eye examination form
// ============================================================================
// CLINICAL-CRITICAL. Owner report 2026-08-24: the Lensometer tab accepted a
// SPH of -9999.
//
// This file adds NO limits of its own. It is purely an adapter from the shapes
// the exam tabs hold (LensometerData / AutoRefData / SubjectiveRxData /
// FinalRxData / SlitLampData) onto the ONE canonical table and validator in
// constants/rxLimits.ts, which is itself the mirror of the backend's
// api/services/rx_validation.py. If a bound is wrong, it is wrong in ONE place.
//
// Two shapes of PD exist and they are different measurements: each eye's own
// box is MONOCULAR (~20-45mm), while the Final Rx "IPD" box is BINOCULAR
// (40-80mm). They are routed to `pd_mono` and `pd` respectively.
//
// The returned value is the FIRST plain-English problem, already prefixed with
// the tab and eye, or null when the whole exam is clean. A blank field is never
// an error -- an optometrist who did not run the lensometer leaves it empty.

import { validateEyePair, validateRxField, type RxEyeValues } from '../../constants/rxLimits';
import type {
  AutoRefData,
  ClinicalFindingsData,
  FinalRxData,
  LensometerData,
  PowerReading,
  SlitLampData,
  SubjectiveRxData,
} from './eyeTestTypes';

/** One eye's power row, mapped onto the canonical field names. */
function readingToEye(r: PowerReading | undefined, opts: { va?: boolean } = {}): RxEyeValues {
  const eye: RxEyeValues = {
    sph: r?.sphere ?? '',
    cyl: r?.cylinder ?? '',
    axis: r?.axis ?? '',
    add: r?.add ?? '',
    // PER-EYE box -> monocular limits (the backend's EyeData.validate_pd rule).
    pd_mono: r?.pd ?? '',
  };
  if (opts.va) eye.va = r?.va ?? '';
  return eye;
}

function checkReading(
  tab: string,
  side: 'Right (OD)' | 'Left (OS)',
  r: PowerReading | undefined,
  opts: { va?: boolean } = {},
): string | null {
  return validateEyePair(readingToEye(r, opts), `${tab} - ${side}`);
}

/** Intra-ocular pressure, mmHg. Mirrors the backend ClinicalFindings/SoapNote
 *  Field(ge=0, le=80) bound -- the ONE place that number is declared server-side. */
function checkIop(where: string, value: string | undefined): string | null {
  const raw = (value ?? '').trim();
  if (raw === '') return null;
  const n = Number(raw);
  if (!Number.isFinite(n)) return `${where} eye pressure must be a number`;
  if (n < 0 || n > 80) return `${where} eye pressure must be between 0 and 80 mmHg`;
  return null;
}

function checkKeratometry(side: string, k: {
  k1?: string; k1Axis?: string; k2?: string; k2Axis?: string;
} | undefined): string | null {
  const prefix = `Auto-Ref - ${side} `;
  return (
    validateRxField('k', k?.k1, `${prefix}K1 `) ||
    validateRxField('axis', k?.k1Axis, `${prefix}K1 `) ||
    validateRxField('k', k?.k2, `${prefix}K2 `) ||
    validateRxField('axis', k?.k2Axis, `${prefix}K2 `)
  );
}

/** The subset of EyeTestData this validator reads. Declared structurally so the
 *  function can also be pointed at an in-flight draft. */
export interface EyeTestValidatable {
  lensometer?: LensometerData;
  slitLamp?: SlitLampData;
  autoRef?: AutoRefData;
  subjectiveRx?: SubjectiveRxData;
  finalRx?: FinalRxData;
  clinicalFindings?: ClinicalFindingsData;
}

/**
 * Validate every power captured anywhere on the exam form.
 * Returns the first plain-English problem, or null when the exam is clean.
 */
export function validateEyeTest(data: EyeTestValidatable): string | null {
  // --- Lensometer (the patient's CURRENT glasses) -------------------------
  const lm = data.lensometer;
  const lensometer =
    checkReading('Lensometer', 'Right (OD)', lm?.rightEye) ||
    checkReading('Lensometer', 'Left (OS)', lm?.leftEye);
  if (lensometer) return lensometer;

  // --- Auto-refractometer + keratometry -----------------------------------
  const ar = data.autoRef;
  const autoRef =
    checkReading('Auto-Ref', 'Right (OD)', ar?.rightEye) ||
    checkReading('Auto-Ref', 'Left (OS)', ar?.leftEye) ||
    checkKeratometry('Right (OD)', ar?.rightEye) ||
    checkKeratometry('Left (OS)', ar?.leftEye);
  if (autoRef) return autoRef;

  // --- Subjective refraction ----------------------------------------------
  const sr = data.subjectiveRx;
  const subjective =
    checkReading('Subjective Rx', 'Right (OD)', sr?.rightEye, { va: true }) ||
    checkReading('Subjective Rx', 'Left (OS)', sr?.leftEye, { va: true });
  if (subjective) return subjective;

  // --- Slit lamp: only the IOP box carries a number ------------------------
  const sl = data.slitLamp;
  const slitLamp =
    checkIop('Slit Lamp - Right (OD)', sl?.rightEye?.iop) ||
    checkIop('Slit Lamp - Left (OS)', sl?.leftEye?.iop);
  if (slitLamp) return slitLamp;

  // --- Final Rx: the one that becomes a billable prescription --------------
  // The near ADD lives on the FLAT rightAdd/leftAdd fields here, not inside the
  // eye, and the IPD is BINOCULAR. Both are validated explicitly rather than
  // being left to the per-eye mapper, which would look at the wrong keys.
  const fr = data.finalRx;
  const finalRx =
    checkReading('Final Rx', 'Right (OD)', fr?.rightEye, { va: true }) ||
    checkReading('Final Rx', 'Left (OS)', fr?.leftEye, { va: true }) ||
    validateRxField('add', fr?.rightAdd, 'Final Rx - Right (OD) ') ||
    validateRxField('add', fr?.leftAdd, 'Final Rx - Left (OS) ') ||
    validateRxField('pd', fr?.ipd, 'Final Rx - ');
  if (finalRx) return finalRx;

  // --- Clinical findings card (internal) -----------------------------------
  const cf = data.clinicalFindings;
  return (
    checkIop('Right', cf?.iopRight) ||
    checkIop('Left', cf?.iopLeft)
  );
}
