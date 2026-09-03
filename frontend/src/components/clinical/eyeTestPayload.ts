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
  EyeTestWriteBody,
  AutoRefPayload,
  ExamEyeReadingPayload,
  ExamRefractionPayload,
  SlitLampPayload,
} from '../../services/api/clinical';
import type {
  AutoRefData,
  AutoRefEye,
  EyeTestData,
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

// ---------------------------------------------------------------------------
// THE eye-test write body
// ---------------------------------------------------------------------------
// Built here, once, and used by BOTH writes: the first save
// (POST /clinical/tests/{id}/complete) and every later correction from the
// clinic's Edit screen (PUT /clinical/tests/{id}/exam).
//
// It lived inline in ClinicalPage's save handler. The moment the Edit screen
// needed the same body, leaving it there would have meant a second copy -- and
// a second place for an exam tab to be quietly forgotten, which is exactly how
// the lensometer / slit-lamp / auto-ref / subjective readings came to be
// dropped in the first place.

export function eyeTestWriteBody(data: EyeTestData): EyeTestWriteBody {
  const re = data.finalRx?.rightEye;
  const le = data.finalRx?.leftEye;
  return {
      // The four exam tabs. They used to stop here: this handler read only
      // finalRx / chiefComplaint / clinicalFindings / soapNote, and the API
      // client had no field for the rest, so a lensometer or slit-lamp
      // reading never left the browser.
      ...examTabsPayload(data),
      examDate: data.examDate || undefined,
      optometristName: data.optometristName || undefined,
      chiefComplaint: data.chiefComplaint || undefined,
      vduUsage: data.vduUsage || undefined,
      // Final Rx: signed strings, sign intact end-to-end.
      ...finalRxPayload(data.finalRx),
      // `notes` carries the optometrist's FINAL RX REMARKS, because that is
      // what the backend stores it as: complete_test writes
      // `"remarks": data.notes` onto the prescription, and the Rx card prints
      // that. It used to be set to the chief complaint, so three things went
      // wrong at once - the remarks the optometrist typed were never sent, the
      // Remarks box refilled from `notes` on reopening and showed the chief
      // complaint back (which reads as saved), and the patient's PRINTED
      // prescription carried "blurred distance vision" where the clinical
      // advice belonged.
      //
      // The chief complaint is not lost: it is sent on its own `chiefComplaint`
      // field above and stored as `chief_complaint` on the test record.
      notes: data.finalRx?.remarks || '',
      // C6-B: also persist the structured exam findings the form already
      // collects (chief complaint + per-eye aided VA from the Final Rx) into
      // the test record's `clinical_findings` block, so they're queryable
      // (VA trend across visits, search by complaint) rather than only buried
      // in `notes`/per-eye. Only sent when non-empty -> a refraction-only test
      // is unchanged. Net-new inputs (IOP/diagnosis/...) land here once the
      // EyeTestForm grows those fields (follow-up).
      clinicalFindings: (() => {
        const cf: Record<string, string | number> = {};
        if (data.chiefComplaint) cf.chiefComplaint = data.chiefComplaint;
        if (re?.va) cf.vaRightAided = re.va;
        if (le?.va) cf.vaLeftAided = le.va;
        // The new internal Clinical Findings card (IOP / diagnosis / colour
        // vision / cover test / dominant eye). Only forward filled fields;
        // IOP -> number so the backend's 0-80 mmHg bound applies.
        const f = data.clinicalFindings;
        if (f) {
          if (f.iopRight) cf.iopRight = parseFloat(f.iopRight);
          if (f.iopLeft) cf.iopLeft = parseFloat(f.iopLeft);
          if (f.diagnosis) cf.diagnosis = f.diagnosis;
          if (f.colourVision) cf.colourVision = f.colourVision;
          if (f.coverTest) cf.coverTest = f.coverTest;
          if (f.dominantEye) cf.dominantEye = f.dominantEye;
        }
        return Object.keys(cf).length > 0 ? cf : undefined;
      })(),
      // CLI-11: forward the structured SOAP note when the optometrist filled
      // at least one field. An entirely empty SOAP note is omitted so the
      // backend keeps the test as a refraction-only record (unchanged).
      soapNote: (() => {
        const sn = data.soapNote;
        if (!sn) return undefined;
        const hasText =
          sn.chiefComplaint || sn.historyPresentIllness || sn.ocularHistory ||
          sn.systemicHistory || sn.familyHistory || sn.medications || sn.allergies ||
          sn.vduUsage || sn.vaRightUnaided || sn.vaLeftUnaided || sn.vaRightAided ||
          sn.vaLeftAided || sn.vaBinocular || sn.iopRight || sn.iopLeft ||
          sn.colourVision || sn.coverTest || sn.dominantEye || sn.pupils ||
          sn.ocularMotility || sn.slitLampSummary || sn.fundusSummary ||
          sn.assessment || (sn.dxCodes && sn.dxCodes.length > 0) ||
          sn.plan || sn.planReferral || sn.planFollowUp || sn.patientInstructions;
        if (!hasText) return undefined;
        // Shape matches the backend SoapNote camelCase aliases.
        return {
          chiefComplaint: sn.chiefComplaint || undefined,
          historyPresentIllness: sn.historyPresentIllness || undefined,
          ocularHistory: sn.ocularHistory || undefined,
          systemicHistory: sn.systemicHistory || undefined,
          familyHistory: sn.familyHistory || undefined,
          medications: sn.medications || undefined,
          allergies: sn.allergies || undefined,
          vduUsage: sn.vduUsage || undefined,
          vaRightUnaided: sn.vaRightUnaided || undefined,
          vaLeftUnaided: sn.vaLeftUnaided || undefined,
          vaRightAided: sn.vaRightAided || undefined,
          vaLeftAided: sn.vaLeftAided || undefined,
          vaBinocular: sn.vaBinocular || undefined,
          iopRight: sn.iopRight ? parseFloat(sn.iopRight) : undefined,
          iopLeft: sn.iopLeft ? parseFloat(sn.iopLeft) : undefined,
          colourVision: sn.colourVision || undefined,
          coverTest: sn.coverTest || undefined,
          dominantEye: sn.dominantEye || undefined,
          pupils: sn.pupils || undefined,
          ocularMotility: sn.ocularMotility || undefined,
          slitLampSummary: sn.slitLampSummary || undefined,
          fundusSummary: sn.fundusSummary || undefined,
          assessment: sn.assessment || undefined,
          dxCodes:
            sn.dxCodes && sn.dxCodes.length > 0
              ? sn.dxCodes.map(d => ({ code: d.code, description: d.description, system: d.system }))
              : undefined,
          plan: sn.plan || undefined,
          planReferral: sn.planReferral || undefined,
          planReferralTo: sn.planReferralTo || undefined,
          planFollowUp: sn.planFollowUp || undefined,
          planFollowUpWeeks: sn.planFollowUpWeeks || undefined,
          patientInstructions: sn.patientInstructions || undefined,
        };
      })(),
  };
}
