// ============================================================================
// IMS 2.0 - the eye examination's STATE: one draft, one validator, one body
// ============================================================================
// This is the brain that used to live inside the EyeTestForm modal (the seven
// tabs' state, the seed from a stored exam, the range check against the ONE
// canonical limits table, and the assembly of the write body). The modal is
// gone -- the exam is a page now -- and the brain lives here so the page
// composes it and nothing else re-implements it.
//
// SEEDED AT MOUNT, deliberately not from an effect: an effect renders the
// exam empty for one commit first, and a clinical form that is briefly blank
// is a clinical form that can be saved blank. The page keys the workbench by
// test id, so a different exam arrives as a fresh mount and seeds again.

import { useCallback, useState } from 'react';
import type {
  AutoRefData,
  ClinicalFindingsData,
  EyeTestData,
  ExamStepId,
  FinalRxData,
  LensometerData,
  SlitLampData,
  SoapNoteData,
  SubjectiveRxData,
  UploadedFile,
} from './eyeTestTypes';
import {
  createEmptyClinicalFindings,
  createEmptyPowerReading,
  createEmptySlitLampEye,
  createEmptySoapNote,
  isExamStepId,
} from './eyeTestTypes';
import { hydrateEyeTest, storedExamHasData, type StoredEyeTest } from './eyeTestHydrate';
import { validateEyeTest } from './eyeTestValidation';

export interface ExamDraft {
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
  uploads: UploadedFile[];
  /** Staff-only. See eyeTestTypes.EyeTestData.internalNote. */
  internalNote: string;
  /** The step the optometrist is on. */
  step: ExamStepId;
}

export function emptyExamDraft(optometristName = ''): ExamDraft {
  return {
    examDate: new Date().toISOString().split('T')[0],
    optometristName,
    chiefComplaint: '',
    vduUsage: 'None',
    lensometer: { rightEye: createEmptyPowerReading(), leftEye: createEmptyPowerReading(), remarks: '' },
    slitLamp: { rightEye: createEmptySlitLampEye(), leftEye: createEmptySlitLampEye(), remarks: '' },
    autoRef: {
      rightEye: { ...createEmptyPowerReading(), k1: '', k1Axis: '', k2: '', k2Axis: '' },
      leftEye: { ...createEmptyPowerReading(), k1: '', k1Axis: '', k2: '', k2Axis: '' },
      remarks: '',
    },
    subjectiveRx: { rightEye: createEmptyPowerReading(), leftEye: createEmptyPowerReading(), remarks: '' },
    finalRx: {
      rightEye: { ...createEmptyPowerReading(), prism: '', base: '' },
      leftEye: { ...createEmptyPowerReading(), prism: '', base: '' },
      rightAdd: '',
      leftAdd: '',
      ipd: '',
      lensType: '',
      nextCheckup: '',
      remarks: '',
    },
    clinicalFindings: createEmptyClinicalFindings(),
    soapNote: createEmptySoapNote(),
    uploads: [],
    internalNote: '',
    step: 'lensometer',
  };
}

/**
 * The draft a stored test seeds. A test with no exam data yet (the queue has
 * only just started it) seeds the BLANK defaults, exactly as a fresh exam
 * always did; a paused or completed exam hydrates every step.
 */
export function seedExamDraft(
  initialTest: StoredEyeTest | null | undefined,
  optometristName: string,
): ExamDraft {
  const base = emptyExamDraft(optometristName);
  if (!initialTest || !storedExamHasData(initialTest)) return base;
  const h = hydrateEyeTest(initialTest);
  return {
    ...base,
    examDate: h.examDate || base.examDate,
    optometristName: h.optometristName || optometristName,
    chiefComplaint: h.chiefComplaint,
    vduUsage: h.vduUsage || 'None',
    lensometer: h.lensometer,
    slitLamp: h.slitLamp,
    autoRef: h.autoRef,
    subjectiveRx: h.subjectiveRx,
    finalRx: h.finalRx,
    clinicalFindings: h.clinicalFindings,
    soapNote: h.soapNote,
    internalNote: h.internalNote,
    step: isExamStepId(h.examStep) ? h.examStep : 'lensometer',
  };
}

export function useEyeExamDraft(
  initialTest: StoredEyeTest | null | undefined,
  optometristName: string,
) {
  const [draft, setDraft] = useState<ExamDraft>(() => seedExamDraft(initialTest, optometristName));
  const patch = useCallback(
    (p: Partial<ExamDraft>) => setDraft((d) => ({ ...d, ...p })),
    [],
  );

  /**
   * PATIENT SAFETY: range-check EVERY power the exam captured before it can
   * leave the page -- on "Complete test" AND on "Save & pause". The backend
   * re-checks the same bounds (the API is reachable without this UI); this is
   * the fast, specific message, not the gate.
   */
  const validate = useCallback((): string | null => validateEyeTest(draft), [draft]);

  const toEyeTestData = useCallback(
    (patientId: string): EyeTestData => ({
      patientId,
      examDate: draft.examDate,
      optometristName: draft.optometristName,
      chiefComplaint: draft.chiefComplaint,
      vduUsage: draft.vduUsage,
      lensometer: draft.lensometer,
      slitLamp: draft.slitLamp,
      autoRef: draft.autoRef,
      subjectiveRx: draft.subjectiveRx,
      finalRx: draft.finalRx,
      clinicalFindings: draft.clinicalFindings,
      // CLI-11: an empty note is still included -- the wire body omits it when
      // every field is blank.
      soapNote: draft.soapNote,
      uploads: draft.uploads,
      internalNote: draft.internalNote,
      examStep: draft.step,
    }),
    [draft],
  );

  return { draft, patch, validate, toEyeTestData };
}
