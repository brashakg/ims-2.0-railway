// ============================================================================
// IMS 2.0 - Eye Test Types & Constants
// ============================================================================

import { Eye, Glasses, Camera, FileText, Upload, Stethoscope } from 'lucide-react';

// Types
export interface PatientInfo {
  id: string;
  name: string;
  phone: string;
  age?: number;
  customerId: string;
}

export interface EyeTestFormProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (data: EyeTestData) => void;
  patient: PatientInfo | null;
  optometristName?: string;
}

// Form Data Types
export interface PowerReading {
  sphere: string;
  cylinder: string;
  axis: string;
  add: string;
  pd: string;
  va: string;
}

export interface LensometerData {
  rightEye: PowerReading;
  leftEye: PowerReading;
  remarks: string;
}

export interface SlitLampEye {
  lids: string;
  conjunctiva: string;
  cornea: string;
  ac: string;
  iris: string;
  pupil: string;
  lens: string;
  fundus: string;
  iop: string;
}

export interface SlitLampData {
  rightEye: SlitLampEye;
  leftEye: SlitLampEye;
  remarks: string;
}

export interface AutoRefEye extends PowerReading {
  k1: string;
  k1Axis: string;
  k2: string;
  k2Axis: string;
}

export interface AutoRefData {
  rightEye: AutoRefEye;
  leftEye: AutoRefEye;
  remarks: string;
}

export interface SubjectiveRxData {
  rightEye: PowerReading;
  leftEye: PowerReading;
  remarks: string;
}

export interface FinalRxEye extends PowerReading {
  prism: string;
  base: string;
}

export interface FinalRxData {
  rightEye: FinalRxEye;
  leftEye: FinalRxEye;
  rightAdd: string;
  leftAdd: string;
  ipd: string;
  lensType: string;
  nextCheckup: string;
  remarks: string;
}

// C6-B: full optometric-exam findings beyond refraction. INTERNAL ONLY — these
// are stored on the test record (clinical_findings) but deliberately NOT printed
// on the customer's Rx card. All optional; a refraction-only test leaves them
// blank. Field names match the backend ClinicalFindings camelCase aliases.
export interface ClinicalFindingsData {
  iopRight: string;        // intra-ocular pressure (eye pressure), mmHg
  iopLeft: string;
  diagnosis: string;
  colourVision: string;    // e.g. "Normal", "Ishihara 14/14"
  coverTest: string;       // squint / phoria check
  dominantEye: '' | 'RIGHT' | 'LEFT';
}

export interface UploadedFile {
  id: string;
  name: string;
  type: string;
  size: number;
  url?: string;
}

export interface EyeTestData {
  patientId: string;
  examDate: string;
  optometristName: string;
  chiefComplaint: string;
  vduUsage: string;
  lensometer: LensometerData;
  slitLamp: SlitLampData;
  autoRef: AutoRefData;
  subjectiveRx: SubjectiveRxData;
  finalRx: FinalRxData;
  // C6-B internal-only exam findings (IOP / diagnosis / colour vision / cover
  // test / dominant eye). Optional — blank for a refraction-only test.
  clinicalFindings: ClinicalFindingsData;
  // CLI-11: structured SOAP exam note. Optional — blank for a refraction-only
  // test. Submitted as soapNote to the backend; can also be saved/updated via
  // the standalone POST /tests/{id}/soap-note endpoint.
  soapNote: SoapNoteData;
  uploads: UploadedFile[];
}

// CLI-11: Structured SOAP exam note types.
// Each section is a flat set of optional strings/booleans; Dx codes are a
// separate list. All fields mirror the backend SoapNote Pydantic model
// (camelCase aliases).

export interface SoapDxCodeData {
  code: string;
  description: string;
  system: string;  // "ICD-10" by default
}

export interface SoapNoteData {
  // Subjective
  chiefComplaint: string;
  historyPresentIllness: string;
  ocularHistory: string;
  systemicHistory: string;
  familyHistory: string;
  medications: string;
  allergies: string;
  vduUsage: string;
  // Objective
  vaRightUnaided: string;
  vaLeftUnaided: string;
  vaRightAided: string;
  vaLeftAided: string;
  vaBinocular: string;
  iopRight: string;
  iopLeft: string;
  colourVision: string;
  coverTest: string;
  dominantEye: '' | 'RIGHT' | 'LEFT';
  pupils: string;
  ocularMotility: string;
  slitLampSummary: string;
  fundusSummary: string;
  // Assessment
  assessment: string;
  dxCodes: SoapDxCodeData[];
  // Plan
  plan: string;
  planReferral: boolean;
  planReferralTo: string;
  planFollowUp: boolean;
  planFollowUpWeeks: number | undefined;
  patientInstructions: string;
}

export const createEmptySoapNote = (): SoapNoteData => ({
  chiefComplaint: '',
  historyPresentIllness: '',
  ocularHistory: '',
  systemicHistory: '',
  familyHistory: '',
  medications: '',
  allergies: '',
  vduUsage: '',
  vaRightUnaided: '',
  vaLeftUnaided: '',
  vaRightAided: '',
  vaLeftAided: '',
  vaBinocular: '',
  iopRight: '',
  iopLeft: '',
  colourVision: '',
  coverTest: '',
  dominantEye: '',
  pupils: '',
  ocularMotility: '',
  slitLampSummary: '',
  fundusSummary: '',
  assessment: '',
  dxCodes: [],
  plan: '',
  planReferral: false,
  planReferralTo: '',
  planFollowUp: false,
  planFollowUpWeeks: undefined,
  patientInstructions: '',
});

export type TabId = 'lensometer' | 'slitlamp' | 'autoref' | 'subjective' | 'final' | 'soap' | 'uploads';

export const TABS: { id: TabId; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'lensometer', label: 'Lensometer', icon: Glasses },
  { id: 'slitlamp', label: 'Slit Lamp', icon: Eye },
  { id: 'autoref', label: 'Auto-Ref', icon: Camera },
  { id: 'subjective', label: 'Subjective Rx', icon: Eye },
  { id: 'final', label: 'Final Rx', icon: FileText },
  // CLI-11: SOAP exam note tab — structured EHR charting.
  { id: 'soap', label: 'SOAP Note', icon: Stethoscope },
  { id: 'uploads', label: 'Uploads', icon: Upload },
];

export const VDU_OPTIONS = ['None', '< 2 hours', '2-4 hours', '4-6 hours', '6-8 hours', '> 8 hours'];

export const LENS_TYPES = [
  'Single Vision',
  'Bifocal',
  'Progressive',
  'Office Lens',
  'Anti-Fatigue',
];

export const createEmptyPowerReading = (): PowerReading => ({
  sphere: '',
  cylinder: '',
  axis: '',
  add: '',
  pd: '',
  va: '',
});

export const createEmptySlitLampEye = (): SlitLampEye => ({
  lids: 'Normal',
  conjunctiva: 'Normal',
  cornea: 'Clear',
  ac: 'Normal',
  iris: 'Normal',
  pupil: 'Normal',
  lens: 'Clear',
  fundus: 'Normal',
  iop: '',
});

export const createEmptyClinicalFindings = (): ClinicalFindingsData => ({
  iopRight: '',
  iopLeft: '',
  diagnosis: '',
  colourVision: '',
  coverTest: '',
  dominantEye: '',
});

// Common colour-vision results for the pick-list (free-text still allowed via "Other").
export const COLOUR_VISION_OPTIONS = ['Normal', 'Deficient', 'Ishihara 14/14', 'Not tested'];
