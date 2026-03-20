// ============================================================================
// IMS 2.0 - Eye Test Types & Constants
// ============================================================================

import { Eye, Glasses, Camera, FileText, Upload } from 'lucide-react';

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
  uploads: UploadedFile[];
}

export type TabId = 'lensometer' | 'slitlamp' | 'autoref' | 'subjective' | 'final' | 'uploads';

export const TABS: { id: TabId; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'lensometer', label: 'Lensometer', icon: Glasses },
  { id: 'slitlamp', label: 'Slit Lamp', icon: Eye },
  { id: 'autoref', label: 'Auto-Ref', icon: Camera },
  { id: 'subjective', label: 'Subjective Rx', icon: Eye },
  { id: 'final', label: 'Final Rx', icon: FileText },
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
