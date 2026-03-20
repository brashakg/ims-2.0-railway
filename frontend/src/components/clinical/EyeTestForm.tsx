// ============================================================================
// IMS 2.0 - Eye Test Form Component
// ============================================================================
// Comprehensive eye examination form with multiple tabs:
// Lensometer, Slit Lamp, Auto-Refractometer, Subjective Rx, Final Rx, Uploads

import { useState } from 'react';
import {
  X,
  Eye,
  Save,
  Printer,
  History,
  User,
  Calendar,
  Monitor,
  AlertCircle,
} from 'lucide-react';
import clsx from 'clsx';

import type {
  EyeTestFormProps,
  EyeTestData,
  LensometerData,
  SlitLampData,
  AutoRefData,
  SubjectiveRxData,
  FinalRxData,
  UploadedFile,
  TabId,
} from './eyeTestTypes';
import {
  TABS,
  VDU_OPTIONS,
  createEmptyPowerReading,
  createEmptySlitLampEye,
} from './eyeTestTypes';

import { LensometerTab } from './LensometerTab';
import { SlitLampTab } from './SlitLampTab';
import { AutoRefTab } from './AutoRefTab';
import { SubjectiveRxTab } from './SubjectiveRxTab';
import { FinalRxTab } from './FinalRxTab';
import { UploadsTab } from './UploadsTab';

// Re-export for backward compatibility
export type { EyeTestData } from './eyeTestTypes';

export function EyeTestForm({ isOpen, onClose, onSave, patient, optometristName = '' }: EyeTestFormProps) {
  const [activeTab, setActiveTab] = useState<TabId>('lensometer');
  const [isSaving, setIsSaving] = useState(false);

  // Header data
  const [examDate, setExamDate] = useState(new Date().toISOString().split('T')[0]);
  const [optometrist, setOptometrist] = useState(optometristName);
  const [chiefComplaint, setChiefComplaint] = useState('');
  const [vduUsage, setVduUsage] = useState('None');

  // Tab data
  const [lensometerData, setLensometerData] = useState<LensometerData>({
    rightEye: createEmptyPowerReading(),
    leftEye: createEmptyPowerReading(),
    remarks: '',
  });

  const [slitLampData, setSlitLampData] = useState<SlitLampData>({
    rightEye: createEmptySlitLampEye(),
    leftEye: createEmptySlitLampEye(),
    remarks: '',
  });

  const [autoRefData, setAutoRefData] = useState<AutoRefData>({
    rightEye: { ...createEmptyPowerReading(), k1: '', k1Axis: '', k2: '', k2Axis: '' },
    leftEye: { ...createEmptyPowerReading(), k1: '', k1Axis: '', k2: '', k2Axis: '' },
    remarks: '',
  });

  const [subjectiveRxData, setSubjectiveRxData] = useState<SubjectiveRxData>({
    rightEye: createEmptyPowerReading(),
    leftEye: createEmptyPowerReading(),
    remarks: '',
  });

  const [finalRxData, setFinalRxData] = useState<FinalRxData>({
    rightEye: { ...createEmptyPowerReading(), prism: '', base: '' },
    leftEye: { ...createEmptyPowerReading(), prism: '', base: '' },
    rightAdd: '',
    leftAdd: '',
    ipd: '',
    lensType: '',
    nextCheckup: '',
    remarks: '',
  });

  const [uploads, setUploads] = useState<UploadedFile[]>([]);

  if (!isOpen || !patient) return null;

  const handleSave = async () => {
    setIsSaving(true);
    try {
      const data: EyeTestData = {
        patientId: patient.id,
        examDate,
        optometristName: optometrist,
        chiefComplaint,
        vduUsage,
        lensometer: lensometerData,
        slitLamp: slitLampData,
        autoRef: autoRefData,
        subjectiveRx: subjectiveRxData,
        finalRx: finalRxData,
        uploads,
      };
      await onSave(data);
    } finally {
      setIsSaving(false);
    }
  };

  const handleFileUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files) return;

    const newFiles: UploadedFile[] = Array.from(files).map(file => ({
      id: crypto.randomUUID(),
      name: file.name,
      type: file.type,
      size: file.size,
    }));

    setUploads([...uploads, ...newFiles]);
    event.target.value = '';
  };

  const removeUpload = (id: string) => {
    setUploads(uploads.filter(f => f.id !== id));
  };

  return (
    <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-6xl max-h-[95vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div className="flex items-center gap-4">
            <div className="w-12 h-12 bg-teal-100 rounded-full flex items-center justify-center">
              <Eye className="w-6 h-6 text-teal-600" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900">Eye Examination</h2>
              <p className="text-sm text-gray-500">
                {patient.name} • {patient.phone} {patient.age ? `• Age: ${patient.age}` : ''}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              disabled
              title="Coming soon"
              onClick={() => {/* TODO: View history */}}
              className="btn-outline flex items-center gap-2 text-sm"
            >
              <History className="w-4 h-4" />
              History
            </button>
            <button
              disabled
              title="Coming soon"
              onClick={() => {/* TODO: Print */}}
              className="btn-outline flex items-center gap-2 text-sm"
            >
              <Printer className="w-4 h-4" />
              Print
            </button>
            <button
              onClick={onClose}
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            >
              <X className="w-5 h-5 text-gray-500" />
            </button>
          </div>
        </div>

        {/* Patient Info Bar */}
        <div className="px-4 py-3 bg-gray-50 border-b border-gray-200">
          <div className="grid grid-cols-4 gap-4">
            <div>
              <label className="text-xs text-gray-500 flex items-center gap-1 mb-1">
                <Calendar className="w-3 h-3" /> Exam Date
              </label>
              <input
                type="date"
                value={examDate}
                onChange={(e) => setExamDate(e.target.value)}
                className="input-field text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-gray-500 flex items-center gap-1 mb-1">
                <User className="w-3 h-3" /> Optometrist
              </label>
              <input
                type="text"
                value={optometrist}
                onChange={(e) => setOptometrist(e.target.value)}
                placeholder="Enter optometrist name"
                className="input-field text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-gray-500 flex items-center gap-1 mb-1">
                <AlertCircle className="w-3 h-3" /> Chief Complaint
              </label>
              <input
                type="text"
                value={chiefComplaint}
                onChange={(e) => setChiefComplaint(e.target.value)}
                placeholder="e.g., Blurred vision, headache"
                className="input-field text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-gray-500 flex items-center gap-1 mb-1">
                <Monitor className="w-3 h-3" /> VDU Usage
              </label>
              <select
                value={vduUsage}
                onChange={(e) => setVduUsage(e.target.value)}
                className="input-field text-sm"
              >
                {VDU_OPTIONS.map(opt => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-200 px-4 overflow-x-auto">
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={clsx(
                'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap',
                activeTab === tab.id
                  ? 'border-teal-600 text-teal-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              )}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {activeTab === 'lensometer' && (
            <LensometerTab data={lensometerData} onChange={setLensometerData} />
          )}
          {activeTab === 'slitlamp' && (
            <SlitLampTab data={slitLampData} onChange={setSlitLampData} />
          )}
          {activeTab === 'autoref' && (
            <AutoRefTab data={autoRefData} onChange={setAutoRefData} />
          )}
          {activeTab === 'subjective' && (
            <SubjectiveRxTab data={subjectiveRxData} onChange={setSubjectiveRxData} />
          )}
          {activeTab === 'final' && (
            <FinalRxTab data={finalRxData} onChange={setFinalRxData} subjectiveRxData={subjectiveRxData} />
          )}
          {activeTab === 'uploads' && (
            <UploadsTab uploads={uploads} onUpload={handleFileUpload} onRemove={removeUpload} />
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-4 border-t border-gray-200 bg-gray-50">
          <button
            disabled
            title="Coming soon"
            onClick={() => {/* TODO: View history */}}
            className="btn-outline flex items-center gap-2"
          >
            <History className="w-4 h-4" />
            View History
          </button>
          <button
            disabled
            title="Coming soon"
            onClick={() => {/* TODO: Print */}}
            className="btn-outline flex items-center gap-2"
          >
            <Printer className="w-4 h-4" />
            Print Prescription
          </button>
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="btn-primary flex items-center gap-2"
          >
            {isSaving ? (
              <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            Save Prescription
          </button>
        </div>
      </div>
    </div>
  );
}

export default EyeTestForm;
