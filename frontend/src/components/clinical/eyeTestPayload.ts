// ============================================================================
// IMS 2.0 - the exam tabs, on the wire
// ============================================================================
// Maps the seven-tab eye-exam form's state onto the request body of
// POST /clinical/tests/{id}/complete (and PUT /clinical/tests/{id}).
//
// Until 2026-08-24 there was nothing to map: the API client had NO field for
// lensometer / auto-ref / subjective-refraction / slit-lamp, so every one of
// those readings was discarded when the dialog closed. Not stored, not
// transmitted, not validated -- which is also why the Edit screen could not
// show them and had to fall back to an Rx-only form.
//
// POWERS TRAVEL AS SIGNED STRINGS, verbatim. The form already holds "+4.00"
// (RxPowerInput normalises to that on blur); anything that runs the value
// through parseFloat/Number destroys the explicit plus, and `String(4)` cannot
// put it back. A blank stays blank -- it is never turned into a 0.

import type {
  AutoRefEyeReadingPayload,
  AutoRefPayload,
  ExamEyeReadingPayload,
  ExamRefractionPayload,
  SlitLampPayload,
} from '../../services/api/clinical';
import type {
  AutoRefData,
  AutoRefEye,
  LensometerData,
  PowerReading,
  SlitLampData,
  SlitLampEye,
  SubjectiveRxData,
} from './eyeTestTypes';

/** A recorded value, verbatim, or undefined when nothing was recorded. */
function txt(v: string | undefined | null): string | undefined {
  if (v === undefined || v === null) return undefined;
  const s = String(v).trim();
  return s === '' ? undefined : s;
}

function eyePayload(r: PowerReading | undefined): ExamEyeReadingPayload {
  return {
    sphere: txt(r?.sphere),
    cylinder: txt(r?.cylinder),
    axis: txt(r?.axis),
    add: txt(r?.add),
    pd: txt(r?.pd),
    va: txt(r?.va),
  };
}

function autoRefEyePayload(r: AutoRefEye | undefined): AutoRefEyeReadingPayload {
  return {
    ...eyePayload(r),
    k1: txt(r?.k1),
    k1Axis: txt(r?.k1Axis),
    k2: txt(r?.k2),
    k2Axis: txt(r?.k2Axis),
  };
}

function slitLampEyePayload(e: SlitLampEye | undefined) {
  const iopRaw = txt(e?.iop);
  const iop = iopRaw === undefined ? undefined : Number(iopRaw);
  return {
    lids: txt(e?.lids),
    conjunctiva: txt(e?.conjunctiva),
    cornea: txt(e?.cornea),
    ac: txt(e?.ac),
    iris: txt(e?.iris),
    pupil: txt(e?.pupil),
    lens: txt(e?.lens),
    fundus: txt(e?.fundus),
    iop: iop !== undefined && Number.isFinite(iop) ? iop : undefined,
  };
}

/** True when any leaf of an object carries a value. */
function hasAnyValue(obj: Record<string, unknown>): boolean {
  return Object.values(obj).some((v) =>
    v !== undefined && v !== null && v !== '' &&
    (typeof v !== 'object' || hasAnyValue(v as Record<string, unknown>)),
  );
}

function refractionPayload(
  data: LensometerData | SubjectiveRxData | undefined,
): ExamRefractionPayload | undefined {
  if (!data) return undefined;
  const block: ExamRefractionPayload = {
    rightEye: eyePayload(data.rightEye),
    leftEye: eyePayload(data.leftEye),
    remarks: txt(data.remarks),
  };
  // A tab the optometrist never touched sends nothing at all, so a quick
  // refraction-only test writes exactly the document it always did.
  return hasAnyValue(block as unknown as Record<string, unknown>) ? block : undefined;
}

function autoRefPayload(data: AutoRefData | undefined): AutoRefPayload | undefined {
  if (!data) return undefined;
  const block: AutoRefPayload = {
    rightEye: autoRefEyePayload(data.rightEye),
    leftEye: autoRefEyePayload(data.leftEye),
    remarks: txt(data.remarks),
  };
  return hasAnyValue(block as unknown as Record<string, unknown>) ? block : undefined;
}

function slitLampPayload(data: SlitLampData | undefined): SlitLampPayload | undefined {
  if (!data) return undefined;
  const block: SlitLampPayload = {
    rightEye: slitLampEyePayload(data.rightEye),
    leftEye: slitLampEyePayload(data.leftEye),
    remarks: txt(data.remarks),
  };
  return hasAnyValue(block as unknown as Record<string, unknown>) ? block : undefined;
}

export interface ExamTabsSource {
  lensometer?: LensometerData;
  slitLamp?: SlitLampData;
  autoRef?: AutoRefData;
  subjectiveRx?: SubjectiveRxData;
}

export interface ExamTabsPayload {
  lensometer?: ExamRefractionPayload;
  autoRef?: AutoRefPayload;
  subjectiveRx?: ExamRefractionPayload;
  slitLamp?: SlitLampPayload;
}

/** The exam tabs, ready to spread into a complete/update request body. */
export function examTabsPayload(data: ExamTabsSource): ExamTabsPayload {
  return {
    lensometer: refractionPayload(data.lensometer),
    autoRef: autoRefPayload(data.autoRef),
    subjectiveRx: refractionPayload(data.subjectiveRx),
    slitLamp: slitLampPayload(data.slitLamp),
  };
}
