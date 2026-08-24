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
  FinalRxData,
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

// ---------------------------------------------------------------------------
// The FINAL Rx -- the block that becomes a billable, dispensable prescription.
// ---------------------------------------------------------------------------
// THE PLUS SIGN IS LOST HERE, and this is why the owner sees "4.00" where he
// typed "+4.00". The form holds "+4.00"; this mapping used to run every power
// through parseFloat, so the number 4 went on the wire, the server stored
// str(4.0) = "4.0", and every surface that echoes the stored string raw --
// the lab job-card the lens grinder reads, the customer's Rx portal, the
// Customer-360 block, the clinical handover card -- printed a power with no
// sign. `String(4)` cannot put the plus back, because by then nothing knows
// there was one.
//
// The backend has always been ready for the signed string: EyeData.sph is
// Optional[str], float("+4.00") == 4.0, and _validate_rx_value passes a signed
// value through byte-for-byte. Only the frontend was throwing it away.

export interface FinalRxEyeWire {
  sphere: string | null;
  cylinder: string | null;
  axis: string | null;
  add: string | null;
  pd: string | null;
  prism: string | null;
  base: string | null;
  va: string | null;
}

export interface FinalRxWire {
  rightEye: FinalRxEyeWire;
  leftEye: FinalRxEyeWire;
  /** Binocular PD as a NUMBER -- the backend field is Optional[float]. */
  pd?: number;
  ipd?: string;
  lensRecommendation?: string;
  nextCheckup?: string;
}

/** null (not undefined) for "not recorded": JSON.stringify drops undefined,
 *  and the backend reads a dropped key as "not sent" rather than "blank". */
function wire(v: string | undefined | null): string | null {
  return txt(v) ?? null;
}

function finalEyeWire(
  eye: FinalRxData['rightEye'] | undefined,
  flatAdd: string | undefined,
): FinalRxEyeWire {
  return {
    sphere: wire(eye?.sphere),
    cylinder: wire(eye?.cylinder),
    axis: wire(eye?.axis),
    // The near ADD lives on the FLAT rightAdd/leftAdd field; the per-eye `add`
    // is the fallback (it is what "Copy from Subjective" fills in).
    add: wire(flatAdd) ?? wire(eye?.add),
    pd: wire(eye?.pd),
    prism: wire(eye?.prism),
    base: wire(eye?.base),
    va: wire(eye?.va),
  };
}

/** The Final Rx, ready to spread into a complete/update request body. */
export function finalRxPayload(fr: FinalRxData | undefined): FinalRxWire {
  const right = finalEyeWire(fr?.rightEye, fr?.rightAdd);
  const left = finalEyeWire(fr?.leftEye, fr?.leftAdd);
  const binocular = txt(fr?.rightEye?.pd) ?? txt(fr?.leftEye?.pd);
  const binocularNum = binocular === undefined ? undefined : Number(binocular);
  return {
    rightEye: right,
    leftEye: left,
    pd:
      binocularNum !== undefined && Number.isFinite(binocularNum)
        ? binocularNum
        : undefined,
    ipd: txt(fr?.ipd),
    lensRecommendation: txt(fr?.lensType),
    nextCheckup: txt(fr?.nextCheckup),
  };
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
