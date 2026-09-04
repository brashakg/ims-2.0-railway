// ============================================================================
// IMS 2.0 - a stored eye test, back INTO the seven-tab exam form
// ============================================================================
// The exact inverse of eyeTestPayload.ts. That file maps the form onto the
// wire; this one maps a stored test document (GET /clinical/tests/{id}, which
// returns the document camelCased) back onto the form's tab state so the Edit
// pencil can reopen the SAME screen the readings were typed into.
//
// WHY THIS FILE HAS TO BE EXACT
// The clinic's Edit pencil used to open an Rx-only form: ~100 captured values
// with 19 of them editable, and the lensometer / slit-lamp / auto-ref /
// subjective readings simply absent. Reopening the full form is only safe if
// hydration is FAITHFUL -- a field this mapper forgets is a field that comes
// back blank, and saving would then erase a reading the optometrist took. That
// is worse than the bug it replaces, so every tab is round-tripped by test
// (ClinicPrescriptionHistoryEdit.test.tsx / EyeTestEditRoundTrip.test.tsx).
//
// POWERS ARE KEPT AS TEXT, verbatim. A stored "+4.00" hydrates as "+4.00" and
// goes back out as "+4.00". Running it through parseFloat would destroy the
// plus on the way IN, and `String(4)` cannot put it back -- the same defect the
// write path had. A stored bare "4" is normalised to "+4.00" by RxPowerInput on
// display, so the sign is re-derived rather than invented.
//
// A BLANK STAYS BLANK. Nothing here turns an unrecorded power into "0".

import {
  createEmptyClinicalFindings,
  createEmptyPowerReading,
  createEmptySlitLampEye,
  createEmptySoapNote,
  type AutoRefData,
  type AutoRefEye,
  type ClinicalFindingsData,
  type FinalRxData,
  type FinalRxEye,
  type LensometerData,
  type PowerReading,
  type SlitLampData,
  type SlitLampEye,
  type SoapNoteData,
  type SubjectiveRxData,
} from './eyeTestTypes';

/** A stored scalar as form TEXT. Absent -> '' (an empty box), never '0'. */
function s(v: unknown): string {
  if (v === undefined || v === null) return '';
  const t = String(v).trim();
  // The junk tokens that reach Mongo from JS and CSV import paths. Treated as
  // absence, exactly as the print cards treat them.
  if (t === '' || t === 'None' || t === 'null' || t === 'undefined' || t === 'NaN') return '';
  return t;
}

type Rec = Record<string, unknown>;

const obj = (v: unknown): Rec => (v && typeof v === 'object' ? (v as Rec) : {});

/**
 * One eye of a refraction block.
 *
 * Alias-tolerant on purpose: the exam blocks store `sphere`/`cylinder`, while
 * the final-Rx block stores `sph`/`cyl`, and imported records carry a mix. The
 * backend's own validator resolves these pairs the same way (first NON-blank
 * key, `_eye_value`), so hydration agrees with what was validated.
 */
function powerReading(raw: unknown): PowerReading {
  const e = obj(raw);
  const pick = (...keys: string[]): string => {
    for (const k of keys) {
      const v = s(e[k]);
      if (v !== '') return v;
    }
    return '';
  };
  return {
    sphere: pick('sphere', 'sph'),
    cylinder: pick('cylinder', 'cyl'),
    axis: pick('axis'),
    add: pick('add', 'addition'),
    pd: pick('pd'),
    va: pick('va', 'acuity'),
  };
}

function autoRefEye(raw: unknown): AutoRefEye {
  const e = obj(raw);
  return {
    ...powerReading(raw),
    k1: s(e.k1),
    k1Axis: s(e.k1Axis ?? e.k1_axis),
    k2: s(e.k2),
    k2Axis: s(e.k2Axis ?? e.k2_axis),
  };
}

function slitLampEye(raw: unknown): SlitLampEye {
  const e = obj(raw);
  return {
    lids: s(e.lids),
    conjunctiva: s(e.conjunctiva),
    cornea: s(e.cornea),
    ac: s(e.ac),
    iris: s(e.iris),
    pupil: s(e.pupil),
    lens: s(e.lens),
    fundus: s(e.fundus),
    iop: s(e.iop),
  };
}

function finalRxEye(raw: unknown): FinalRxEye {
  const e = obj(raw);
  return { ...powerReading(raw), prism: s(e.prism), base: s(e.base) };
}

/** The shape GET /clinical/tests/{id} returns (camelCased document). */
export interface StoredEyeTest {
  testId?: string;
  id?: string;
  examDate?: unknown;
  optometristName?: unknown;
  chiefComplaint?: unknown;
  vduUsage?: unknown;
  lensometer?: unknown;
  slitLamp?: unknown;
  autoRef?: unknown;
  subjectiveRx?: unknown;
  prescription?: unknown;
  clinicalFindings?: unknown;
  soapNote?: unknown;
  [key: string]: unknown;
}

export interface HydratedEyeTest {
  examDate: string;
  optometristName: string;
  chiefComplaint: string;
  vduUsage: string;
  lensometer: LensometerData;
  slitLamp: SlitLampData;
  autoRef: AutoRefData;
  subjectiveRx: SubjectiveRxData;
  finalRx: FinalRxData;
  clinicalFindings: ClinicalFindingsData;
  soapNote: SoapNoteData;
  /** Staff-only note; '' when none was stored. */
  internalNote: string;
  /** The step "Save & pause" left the exam on; '' when never paused. */
  examStep: string;
}

export function hydrateLensometer(raw: unknown): LensometerData {
  const b = obj(raw);
  return {
    rightEye: powerReading(b.rightEye ?? b.right_eye),
    leftEye: powerReading(b.leftEye ?? b.left_eye),
    remarks: s(b.remarks),
  };
}

export function hydrateSlitLamp(raw: unknown): SlitLampData {
  const b = obj(raw);
  return {
    rightEye: slitLampEye(b.rightEye ?? b.right_eye),
    leftEye: slitLampEye(b.leftEye ?? b.left_eye),
    remarks: s(b.remarks),
  };
}

export function hydrateAutoRef(raw: unknown): AutoRefData {
  const b = obj(raw);
  return {
    rightEye: autoRefEye(b.rightEye ?? b.right_eye),
    leftEye: autoRefEye(b.leftEye ?? b.left_eye),
    remarks: s(b.remarks),
  };
}

export function hydrateSubjectiveRx(raw: unknown): SubjectiveRxData {
  const b = obj(raw);
  return {
    rightEye: powerReading(b.rightEye ?? b.right_eye),
    leftEye: powerReading(b.leftEye ?? b.left_eye),
    remarks: s(b.remarks),
  };
}

/**
 * The Final Rx tab, from the test document's `prescription` block.
 *
 * `rightAdd`/`leftAdd` mirror each eye's own `add` -- the tab renders the near
 * addition in a separate box but it is the same stored value, so hydrating one
 * without the other would show a blank ADD beside a populated one.
 */
export function hydrateFinalRx(raw: unknown): FinalRxData {
  const p = obj(raw);
  const right = finalRxEye(p.rightEye ?? p.right_eye);
  const left = finalRxEye(p.leftEye ?? p.left_eye);
  return {
    rightEye: right,
    leftEye: left,
    rightAdd: right.add,
    leftAdd: left.add,
    // BINOCULAR IPD only. NEVER fall back to the stored `pd`: the exam's
    // top-level pd is filled from the RIGHT EYE's monocular PD (see
    // finalRxPayload), so falling back would show ~32.5 in the binocular box
    // and an amendment would then push that half-value at the lab-facing
    // prescription. Blank is the honest answer for an exam that predates
    // storing ipd; the backend leaves a field the form did not carry alone.
    ipd: s(p.ipd),
    lensType: s(p.lensRecommendation ?? p.lens_recommendation),
    nextCheckup: s(p.nextCheckup ?? p.next_checkup),
    // `remarks` FIRST. Reading `notes` first is what put the chief complaint
    // in the Remarks box on every reopened exam - and made the loss invisible,
    // because the box was never empty. `notes` stays as the fallback so exams
    // saved before the fix still show whatever they stored.
    remarks: s(p.remarks ?? p.notes),
  };
}

export function hydrateClinicalFindings(raw: unknown): ClinicalFindingsData {
  const f = obj(raw);
  const empty = createEmptyClinicalFindings();
  const dominant = s(f.dominantEye ?? f.dominant_eye).toUpperCase();
  return {
    ...empty,
    iopRight: s(f.iopRight ?? f.iop_right),
    iopLeft: s(f.iopLeft ?? f.iop_left),
    diagnosis: s(f.diagnosis),
    colourVision: s(f.colourVision ?? f.colour_vision),
    coverTest: s(f.coverTest ?? f.cover_test),
    dominantEye: dominant === 'RIGHT' || dominant === 'LEFT' ? dominant : '',
  };
}

/**
 * The SOAP note.
 *
 * Merged onto the empty shape KEY BY KEY rather than spread wholesale, so a
 * stored document written before a field existed still produces a complete
 * form object, and a stray stored key can never introduce an undefined into a
 * controlled input (React would flip it to uncontrolled and warn).
 */
export function hydrateSoapNote(raw: unknown): SoapNoteData {
  const src = obj(raw);
  const empty = createEmptySoapNote();
  const out = { ...empty } as unknown as Record<string, unknown>;
  for (const key of Object.keys(empty)) {
    if (!(key in src)) continue;
    const v = src[key];
    if (v === undefined || v === null) continue;
    const current = (empty as unknown as Record<string, unknown>)[key];
    if (Array.isArray(current)) {
      out[key] = Array.isArray(v) ? v : current;
    } else if (typeof current === 'boolean') {
      out[key] = Boolean(v);
    } else if (typeof current === 'number' || current === undefined) {
      out[key] = v;
    } else {
      out[key] = s(v);
    }
  }
  return out as unknown as SoapNoteData;
}

/** Every tab of a stored test, ready to seed the form's state. */
export function hydrateEyeTest(test: StoredEyeTest | null | undefined): HydratedEyeTest {
  const t = obj(test);
  const examDateRaw = s(t.examDate ?? t.exam_date ?? t.startedAt ?? t.started_at);
  return {
    // The date input needs a bare YYYY-MM-DD; stored values are often full ISO.
    examDate: examDateRaw.slice(0, 10),
    optometristName: s(t.optometristName ?? t.optometrist_name),
    chiefComplaint: s(t.chiefComplaint ?? t.chief_complaint),
    // '' would blank the VDU <select>; 'None' is its real default option.
    vduUsage: s(t.vduUsage ?? t.vdu_usage) || 'None',
    lensometer: hydrateLensometer(t.lensometer),
    slitLamp: hydrateSlitLamp(t.slitLamp ?? t.slit_lamp),
    autoRef: hydrateAutoRef(t.autoRef ?? t.auto_ref),
    subjectiveRx: hydrateSubjectiveRx(t.subjectiveRx ?? t.subjective_rx),
    finalRx: hydrateFinalRx(t.prescription),
    clinicalFindings: hydrateClinicalFindings(t.clinicalFindings ?? t.clinical_findings),
    soapNote: hydrateSoapNote(t.soapNote ?? t.soap_note),
    internalNote: s(t.internalNote ?? t.internal_note),
    examStep: s(t.examStep ?? t.exam_step),
  };
}

/**
 * Does a stored test carry ANY exam data? A test the queue has only just
 * started is a bare header (patient, store, optometrist, IN_PROGRESS) and must
 * seed the page with the BLANK defaults (slit lamp "Normal"/"Clear"), exactly
 * as a fresh exam always did -- hydrating an empty document would seed every
 * slit-lamp box with '' instead.
 */
export function storedExamHasData(test: StoredEyeTest | null | undefined): boolean {
  const t = obj(test);
  return [
    'lensometer', 'slitLamp', 'slit_lamp', 'autoRef', 'auto_ref',
    'subjectiveRx', 'subjective_rx', 'prescription', 'clinicalFindings',
    'clinical_findings', 'soapNote', 'soap_note', 'examStep', 'exam_step',
    'internalNote', 'internal_note',
  ].some((k) => t[k] !== undefined && t[k] !== null && t[k] !== '');
}

// Re-exported for the form, which seeds untouched tabs from these.
export { createEmptyPowerReading, createEmptySlitLampEye };
