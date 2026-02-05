// ============================================================================
// IMS 2.0 - Eye Test Form Component
// ============================================================================
// Comprehensive eye examination form with multiple tabs:
// Lensometer, Slit Lamp, Auto-Refractometer, Subjective Rx, Final Rx, Uploads

import { useState } from 'react';
import {
  X,
  Eye,
  Glasses,
  Camera,
  FileText,
  Upload,
  Save,
  Printer,
  History,
  User,
  Calendar,
  Monitor,
  AlertCircle,
  Trash2,
} from 'lucide-react';
import clsx from 'clsx';

// Types
interface PatientInfo {
  id: string;
  name: string;
  phone: string;
  age?: number;
  customerId: string;
}

interface EyeTestFormProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (data: EyeTestData) => void;
  patient: PatientInfo | null;
  optometristName?: string;
}

// Form Data Types
interface PowerReading {
  sphere: string;
  cylinder: string;
  axis: string;
  add: string;
  pd: string;
  va: string;
}

interface LensometerData {
  rightEye: PowerReading;
  leftEye: PowerReading;
  remarks: string;
}

interface SlitLampData {
  rightEye: {
    lids: string;
    conjunctiva: string;
    cornea: string;
    ac: string;
    iris: string;
    pupil: string;
    lens: string;
    fundus: string;
    iop: string;
  };
  leftEye: {
    lids: string;
    conjunctiva: string;
    cornea: string;
    ac: string;
    iris: string;
    pupil: string;
    lens: string;
    fundus: string;
    iop: string;
  };
  remarks: string;
}

interface AutoRefData {
  rightEye: PowerReading & {
    k1: string;
    k1Axis: string;
    k2: string;
    k2Axis: string;
  };
  leftEye: PowerReading & {
    k1: string;
    k1Axis: string;
    k2: string;
    k2Axis: string;
  };
  remarks: string;
}

interface SubjectiveRxData {
  rightEye: PowerReading;
  leftEye: PowerReading;
  remarks: string;
}

interface FinalRxData {
  rightEye: PowerReading & { prism: string; base: string };
  leftEye: PowerReading & { prism: string; base: string };
  rightAdd: string;
  leftAdd: string;
  ipd: string;
  lensType: string;
  nextCheckup: string;
  remarks: string;
}

interface UploadedFile {
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

type TabId = 'lensometer' | 'slitlamp' | 'autoref' | 'subjective' | 'final' | 'uploads';

const TABS: { id: TabId; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: 'lensometer', label: 'Lensometer', icon: Glasses },
  { id: 'slitlamp', label: 'Slit Lamp', icon: Eye },
  { id: 'autoref', label: 'Auto-Ref', icon: Camera },
  { id: 'subjective', label: 'Subjective Rx', icon: Eye },
  { id: 'final', label: 'Final Rx', icon: FileText },
  { id: 'uploads', label: 'Uploads', icon: Upload },
];

const VDU_OPTIONS = ['None', '< 2 hours', '2-4 hours', '4-6 hours', '6-8 hours', '> 8 hours'];

const LENS_TYPES = [
  'Single Vision',
  'Bifocal',
  'Progressive',
  'Office Lens',
  'Anti-Fatigue',
];


const createEmptyPowerReading = (): PowerReading => ({
  sphere: '',
  cylinder: '',
  axis: '',
  add: '',
  pd: '',
  va: '',
});

const createEmptySlitLampEye = () => ({
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

  // Power input component
  const PowerInput = ({
    label,
    value,
    onChange,
    placeholder = '',
    width = 'w-20',
  }: {
    label: string;
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    width?: string;
  }) => (
    <div className="flex flex-col">
      <label className="text-xs text-gray-500 mb-1">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={clsx('input-field text-center text-sm', width)}
      />
    </div>
  );

  // Eye power row component
  const EyePowerRow = ({
    eye,
    data,
    onChange,
    showVA = true,
  }: {
    eye: 'R' | 'L';
    data: PowerReading;
    onChange: (field: keyof PowerReading, value: string) => void;
    showVA?: boolean;
  }) => (
    <div className="flex items-center gap-2 py-2">
      <div className={clsx(
        'w-8 h-8 rounded-full flex items-center justify-center font-bold text-sm',
        eye === 'R' ? 'bg-blue-100 text-blue-600' : 'bg-green-100 text-green-600'
      )}>
        {eye}
      </div>
      <PowerInput label="SPH" value={data.sphere} onChange={(v) => onChange('sphere', v)} placeholder="±0.00" />
      <PowerInput label="CYL" value={data.cylinder} onChange={(v) => onChange('cylinder', v)} placeholder="±0.00" />
      <PowerInput label="AXIS" value={data.axis} onChange={(v) => onChange('axis', v)} placeholder="0-180" width="w-16" />
      <PowerInput label="ADD" value={data.add} onChange={(v) => onChange('add', v)} placeholder="+0.00" width="w-16" />
      <PowerInput label="PD" value={data.pd} onChange={(v) => onChange('pd', v)} placeholder="mm" width="w-16" />
      {showVA && (
        <PowerInput label="VA" value={data.va} onChange={(v) => onChange('va', v)} placeholder="6/6" width="w-16" />
      )}
    </div>
  );

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
              onClick={() => {/* TODO: View history */}}
              className="btn-outline flex items-center gap-2 text-sm"
            >
              <History className="w-4 h-4" />
              History
            </button>
            <button
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
          {/* Lensometer Tab */}
          {activeTab === 'lensometer' && (
            <div className="space-y-4">
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 flex items-center gap-2">
                <Glasses className="w-5 h-5 text-blue-600" />
                <span className="text-sm text-blue-700">Enter the power of patient's existing glasses</span>
              </div>

              <div className="card">
                <h3 className="font-semibold text-gray-900 mb-4">Current Glasses Power</h3>
                <div className="space-y-2">
                  <EyePowerRow
                    eye="R"
                    data={lensometerData.rightEye}
                    onChange={(field, value) =>
                      setLensometerData({
                        ...lensometerData,
                        rightEye: { ...lensometerData.rightEye, [field]: value },
                      })
                    }
                    showVA={false}
                  />
                  <EyePowerRow
                    eye="L"
                    data={lensometerData.leftEye}
                    onChange={(field, value) =>
                      setLensometerData({
                        ...lensometerData,
                        leftEye: { ...lensometerData.leftEye, [field]: value },
                      })
                    }
                    showVA={false}
                  />
                </div>

                <div className="mt-4">
                  <label className="text-sm text-gray-600 mb-1 block">Remarks</label>
                  <textarea
                    value={lensometerData.remarks}
                    onChange={(e) => setLensometerData({ ...lensometerData, remarks: e.target.value })}
                    placeholder="Any observations about current glasses..."
                    className="input-field w-full h-20 resize-none"
                  />
                </div>
              </div>
            </div>
          )}

          {/* Slit Lamp Tab */}
          {activeTab === 'slitlamp' && (
            <div className="space-y-4">
              <div className="bg-purple-50 border border-purple-200 rounded-lg p-3 flex items-center gap-2">
                <Eye className="w-5 h-5 text-purple-600" />
                <span className="text-sm text-purple-700">Anterior segment examination findings</span>
              </div>

              <div className="grid grid-cols-2 gap-4">
                {/* Right Eye */}
                <div className="card">
                  <div className="flex items-center gap-2 mb-4">
                    <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center font-bold text-blue-600">R</div>
                    <h3 className="font-semibold text-gray-900">Right Eye</h3>
                  </div>
                  <div className="space-y-3">
                    {Object.entries(slitLampData.rightEye).map(([key, value]) => (
                      <div key={key} className="flex items-center gap-2">
                        <label className="text-sm text-gray-600 w-24 capitalize">{key === 'ac' ? 'A/C' : key === 'iop' ? 'IOP' : key}</label>
                        <input
                          type="text"
                          value={value}
                          onChange={(e) =>
                            setSlitLampData({
                              ...slitLampData,
                              rightEye: { ...slitLampData.rightEye, [key]: e.target.value },
                            })
                          }
                          className="input-field flex-1 text-sm"
                        />
                      </div>
                    ))}
                  </div>
                </div>

                {/* Left Eye */}
                <div className="card">
                  <div className="flex items-center gap-2 mb-4">
                    <div className="w-8 h-8 rounded-full bg-green-100 flex items-center justify-center font-bold text-green-600">L</div>
                    <h3 className="font-semibold text-gray-900">Left Eye</h3>
                  </div>
                  <div className="space-y-3">
                    {Object.entries(slitLampData.leftEye).map(([key, value]) => (
                      <div key={key} className="flex items-center gap-2">
                        <label className="text-sm text-gray-600 w-24 capitalize">{key === 'ac' ? 'A/C' : key === 'iop' ? 'IOP' : key}</label>
                        <input
                          type="text"
                          value={value}
                          onChange={(e) =>
                            setSlitLampData({
                              ...slitLampData,
                              leftEye: { ...slitLampData.leftEye, [key]: e.target.value },
                            })
                          }
                          className="input-field flex-1 text-sm"
                        />
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="card">
                <label className="text-sm text-gray-600 mb-1 block">Remarks</label>
                <textarea
                  value={slitLampData.remarks}
                  onChange={(e) => setSlitLampData({ ...slitLampData, remarks: e.target.value })}
                  placeholder="Additional clinical findings..."
                  className="input-field w-full h-20 resize-none"
                />
              </div>
            </div>
          )}

          {/* Auto-Refractometer Tab */}
          {activeTab === 'autoref' && (
            <div className="space-y-4">
              <div className="bg-orange-50 border border-orange-200 rounded-lg p-3 flex items-center gap-2">
                <Camera className="w-5 h-5 text-orange-600" />
                <span className="text-sm text-orange-700">Auto-refractometer and Keratometry readings</span>
              </div>

              <div className="card">
                <h3 className="font-semibold text-gray-900 mb-4">Auto-Refraction</h3>
                <div className="space-y-2">
                  <EyePowerRow
                    eye="R"
                    data={autoRefData.rightEye}
                    onChange={(field, value) =>
                      setAutoRefData({
                        ...autoRefData,
                        rightEye: { ...autoRefData.rightEye, [field]: value },
                      })
                    }
                    showVA={false}
                  />
                  <EyePowerRow
                    eye="L"
                    data={autoRefData.leftEye}
                    onChange={(field, value) =>
                      setAutoRefData({
                        ...autoRefData,
                        leftEye: { ...autoRefData.leftEye, [field]: value },
                      })
                    }
                    showVA={false}
                  />
                </div>
              </div>

              <div className="card">
                <h3 className="font-semibold text-gray-900 mb-4">Keratometry (K-Readings)</h3>
                <div className="grid grid-cols-2 gap-6">
                  {/* Right Eye K */}
                  <div>
                    <div className="flex items-center gap-2 mb-3">
                      <div className="w-6 h-6 rounded-full bg-blue-100 flex items-center justify-center font-bold text-xs text-blue-600">R</div>
                      <span className="text-sm font-medium">Right Eye</span>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs text-gray-500 mb-1 block">K1 (D)</label>
                        <input
                          type="text"
                          value={autoRefData.rightEye.k1}
                          onChange={(e) => setAutoRefData({
                            ...autoRefData,
                            rightEye: { ...autoRefData.rightEye, k1: e.target.value }
                          })}
                          placeholder="e.g., 42.50"
                          className="input-field text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-500 mb-1 block">K1 Axis</label>
                        <input
                          type="text"
                          value={autoRefData.rightEye.k1Axis}
                          onChange={(e) => setAutoRefData({
                            ...autoRefData,
                            rightEye: { ...autoRefData.rightEye, k1Axis: e.target.value }
                          })}
                          placeholder="0-180"
                          className="input-field text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-500 mb-1 block">K2 (D)</label>
                        <input
                          type="text"
                          value={autoRefData.rightEye.k2}
                          onChange={(e) => setAutoRefData({
                            ...autoRefData,
                            rightEye: { ...autoRefData.rightEye, k2: e.target.value }
                          })}
                          placeholder="e.g., 43.00"
                          className="input-field text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-500 mb-1 block">K2 Axis</label>
                        <input
                          type="text"
                          value={autoRefData.rightEye.k2Axis}
                          onChange={(e) => setAutoRefData({
                            ...autoRefData,
                            rightEye: { ...autoRefData.rightEye, k2Axis: e.target.value }
                          })}
                          placeholder="0-180"
                          className="input-field text-sm"
                        />
                      </div>
                    </div>
                  </div>

                  {/* Left Eye K */}
                  <div>
                    <div className="flex items-center gap-2 mb-3">
                      <div className="w-6 h-6 rounded-full bg-green-100 flex items-center justify-center font-bold text-xs text-green-600">L</div>
                      <span className="text-sm font-medium">Left Eye</span>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs text-gray-500 mb-1 block">K1 (D)</label>
                        <input
                          type="text"
                          value={autoRefData.leftEye.k1}
                          onChange={(e) => setAutoRefData({
                            ...autoRefData,
                            leftEye: { ...autoRefData.leftEye, k1: e.target.value }
                          })}
                          placeholder="e.g., 42.50"
                          className="input-field text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-500 mb-1 block">K1 Axis</label>
                        <input
                          type="text"
                          value={autoRefData.leftEye.k1Axis}
                          onChange={(e) => setAutoRefData({
                            ...autoRefData,
                            leftEye: { ...autoRefData.leftEye, k1Axis: e.target.value }
                          })}
                          placeholder="0-180"
                          className="input-field text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-500 mb-1 block">K2 (D)</label>
                        <input
                          type="text"
                          value={autoRefData.leftEye.k2}
                          onChange={(e) => setAutoRefData({
                            ...autoRefData,
                            leftEye: { ...autoRefData.leftEye, k2: e.target.value }
                          })}
                          placeholder="e.g., 43.00"
                          className="input-field text-sm"
                        />
                      </div>
                      <div>
                        <label className="text-xs text-gray-500 mb-1 block">K2 Axis</label>
                        <input
                          type="text"
                          value={autoRefData.leftEye.k2Axis}
                          onChange={(e) => setAutoRefData({
                            ...autoRefData,
                            leftEye: { ...autoRefData.leftEye, k2Axis: e.target.value }
                          })}
                          placeholder="0-180"
                          className="input-field text-sm"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div className="card">
                <label className="text-sm text-gray-600 mb-1 block">Remarks</label>
                <textarea
                  value={autoRefData.remarks}
                  onChange={(e) => setAutoRefData({ ...autoRefData, remarks: e.target.value })}
                  placeholder="Auto-refraction notes..."
                  className="input-field w-full h-20 resize-none"
                />
              </div>
            </div>
          )}

          {/* Subjective Rx Tab */}
          {activeTab === 'subjective' && (
            <div className="space-y-4">
              <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-3 flex items-center gap-2">
                <Eye className="w-5 h-5 text-indigo-600" />
                <span className="text-sm text-indigo-700">Subjective refraction with visual acuity</span>
              </div>

              <div className="card">
                <h3 className="font-semibold text-gray-900 mb-4">Subjective Refraction</h3>
                <div className="space-y-2">
                  <EyePowerRow
                    eye="R"
                    data={subjectiveRxData.rightEye}
                    onChange={(field, value) =>
                      setSubjectiveRxData({
                        ...subjectiveRxData,
                        rightEye: { ...subjectiveRxData.rightEye, [field]: value },
                      })
                    }
                    showVA={true}
                  />
                  <EyePowerRow
                    eye="L"
                    data={subjectiveRxData.leftEye}
                    onChange={(field, value) =>
                      setSubjectiveRxData({
                        ...subjectiveRxData,
                        leftEye: { ...subjectiveRxData.leftEye, [field]: value },
                      })
                    }
                    showVA={true}
                  />
                </div>

                <div className="mt-4">
                  <label className="text-sm text-gray-600 mb-1 block">Remarks</label>
                  <textarea
                    value={subjectiveRxData.remarks}
                    onChange={(e) => setSubjectiveRxData({ ...subjectiveRxData, remarks: e.target.value })}
                    placeholder="Subjective refraction notes..."
                    className="input-field w-full h-20 resize-none"
                  />
                </div>
              </div>
            </div>
          )}

          {/* Final Rx Tab */}
          {activeTab === 'final' && (
            <div className="space-y-4">
              {/* Header with Copy buttons */}
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-gray-900">Final Prescription</h3>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      setFinalRxData({
                        ...finalRxData,
                        rightEye: { ...finalRxData.rightEye, ...subjectiveRxData.rightEye },
                        leftEye: { ...finalRxData.leftEye, ...subjectiveRxData.leftEye },
                      });
                    }}
                    className="btn-outline text-sm flex items-center gap-1"
                  >
                    <FileText className="w-4 h-4" />
                    Copy from Subjective
                  </button>
                  <button
                    onClick={() => {
                      setFinalRxData({
                        ...finalRxData,
                        leftEye: { ...finalRxData.rightEye },
                        leftAdd: finalRxData.rightAdd,
                      });
                    }}
                    className="btn-outline text-sm flex items-center gap-1"
                  >
                    Copy R → L
                  </button>
                </div>
              </div>

              {/* Distance Vision Table */}
              <div className="card">
                <h4 className="font-medium text-gray-800 mb-4">Distance Vision</h4>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-gray-200">
                        <th className="text-left py-2 px-3 text-sm font-medium text-gray-600 w-28">Eye</th>
                        <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">SPH</th>
                        <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">CYL</th>
                        <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">AXIS</th>
                        <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">PRISM</th>
                        <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">BASE</th>
                        <th className="text-center py-2 px-3 text-sm font-medium text-gray-600">VA</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr className="border-b border-gray-100">
                        <td className="py-3 px-3 font-medium text-gray-900">Right (OD)</td>
                        <td className="py-2 px-2">
                          <input type="text" value={finalRxData.rightEye.sphere} onChange={(e) => setFinalRxData({
                            ...finalRxData, rightEye: { ...finalRxData.rightEye, sphere: e.target.value }
                          })} placeholder="SPH" className="input-field text-center text-sm w-full" />
                        </td>
                        <td className="py-2 px-2">
                          <input type="text" value={finalRxData.rightEye.cylinder} onChange={(e) => setFinalRxData({
                            ...finalRxData, rightEye: { ...finalRxData.rightEye, cylinder: e.target.value }
                          })} placeholder="CYL" className="input-field text-center text-sm w-full" />
                        </td>
                        <td className="py-2 px-2">
                          <input type="text" value={finalRxData.rightEye.axis} onChange={(e) => setFinalRxData({
                            ...finalRxData, rightEye: { ...finalRxData.rightEye, axis: e.target.value }
                          })} placeholder="1-180" className="input-field text-center text-sm w-full" />
                        </td>
                        <td className="py-2 px-2">
                          <input type="text" value={finalRxData.rightEye.prism} onChange={(e) => setFinalRxData({
                            ...finalRxData, rightEye: { ...finalRxData.rightEye, prism: e.target.value }
                          })} placeholder="Prism" className="input-field text-center text-sm w-full" />
                        </td>
                        <td className="py-2 px-2">
                          <select value={finalRxData.rightEye.base} onChange={(e) => setFinalRxData({
                            ...finalRxData, rightEye: { ...finalRxData.rightEye, base: e.target.value }
                          })} className="input-field text-center text-sm w-full">
                            <option value="">-</option>
                            <option value="IN">IN</option>
                            <option value="OUT">OUT</option>
                            <option value="UP">UP</option>
                            <option value="DOWN">DOWN</option>
                          </select>
                        </td>
                        <td className="py-2 px-2">
                          <select value={finalRxData.rightEye.va} onChange={(e) => setFinalRxData({
                            ...finalRxData, rightEye: { ...finalRxData.rightEye, va: e.target.value }
                          })} className="input-field text-center text-sm w-full">
                            <option value="6/6">6/6</option>
                            <option value="6/9">6/9</option>
                            <option value="6/12">6/12</option>
                            <option value="6/18">6/18</option>
                            <option value="6/24">6/24</option>
                            <option value="6/36">6/36</option>
                            <option value="6/60">6/60</option>
                          </select>
                        </td>
                      </tr>
                      <tr>
                        <td className="py-3 px-3 font-medium text-gray-900">Left (OS)</td>
                        <td className="py-2 px-2">
                          <input type="text" value={finalRxData.leftEye.sphere} onChange={(e) => setFinalRxData({
                            ...finalRxData, leftEye: { ...finalRxData.leftEye, sphere: e.target.value }
                          })} placeholder="SPH" className="input-field text-center text-sm w-full" />
                        </td>
                        <td className="py-2 px-2">
                          <input type="text" value={finalRxData.leftEye.cylinder} onChange={(e) => setFinalRxData({
                            ...finalRxData, leftEye: { ...finalRxData.leftEye, cylinder: e.target.value }
                          })} placeholder="CYL" className="input-field text-center text-sm w-full" />
                        </td>
                        <td className="py-2 px-2">
                          <input type="text" value={finalRxData.leftEye.axis} onChange={(e) => setFinalRxData({
                            ...finalRxData, leftEye: { ...finalRxData.leftEye, axis: e.target.value }
                          })} placeholder="1-180" className="input-field text-center text-sm w-full" />
                        </td>
                        <td className="py-2 px-2">
                          <input type="text" value={finalRxData.leftEye.prism} onChange={(e) => setFinalRxData({
                            ...finalRxData, leftEye: { ...finalRxData.leftEye, prism: e.target.value }
                          })} placeholder="Prism" className="input-field text-center text-sm w-full" />
                        </td>
                        <td className="py-2 px-2">
                          <select value={finalRxData.leftEye.base} onChange={(e) => setFinalRxData({
                            ...finalRxData, leftEye: { ...finalRxData.leftEye, base: e.target.value }
                          })} className="input-field text-center text-sm w-full">
                            <option value="">-</option>
                            <option value="IN">IN</option>
                            <option value="OUT">OUT</option>
                            <option value="UP">UP</option>
                            <option value="DOWN">DOWN</option>
                          </select>
                        </td>
                        <td className="py-2 px-2">
                          <select value={finalRxData.leftEye.va} onChange={(e) => setFinalRxData({
                            ...finalRxData, leftEye: { ...finalRxData.leftEye, va: e.target.value }
                          })} className="input-field text-center text-sm w-full">
                            <option value="6/6">6/6</option>
                            <option value="6/9">6/9</option>
                            <option value="6/12">6/12</option>
                            <option value="6/18">6/18</option>
                            <option value="6/24">6/24</option>
                            <option value="6/36">6/36</option>
                            <option value="6/60">6/60</option>
                          </select>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Near Vision (ADD) */}
              <div className="card">
                <h4 className="font-medium text-gray-800 mb-4">Near Vision (ADD)</h4>
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="text-sm text-gray-600 mb-1 block">Right ADD</label>
                    <input
                      type="text"
                      value={finalRxData.rightAdd}
                      onChange={(e) => setFinalRxData({ ...finalRxData, rightAdd: e.target.value })}
                      placeholder="+0.00"
                      className="input-field"
                    />
                  </div>
                  <div>
                    <label className="text-sm text-gray-600 mb-1 block">Left ADD</label>
                    <input
                      type="text"
                      value={finalRxData.leftAdd}
                      onChange={(e) => setFinalRxData({ ...finalRxData, leftAdd: e.target.value })}
                      placeholder="+0.00"
                      className="input-field"
                    />
                  </div>
                  <div>
                    <label className="text-sm text-gray-600 mb-1 block">IPD (mm)</label>
                    <input
                      type="text"
                      value={finalRxData.ipd}
                      onChange={(e) => setFinalRxData({ ...finalRxData, ipd: e.target.value })}
                      placeholder="e.g., 62"
                      className="input-field"
                    />
                  </div>
                </div>
              </div>

              {/* Recommendations */}
              <div className="card">
                <h4 className="font-medium text-gray-800 mb-4">Recommendations</h4>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-sm text-gray-600 mb-1 block">Lens Type</label>
                    <select
                      value={finalRxData.lensType}
                      onChange={(e) => setFinalRxData({ ...finalRxData, lensType: e.target.value })}
                      className="input-field"
                    >
                      <option value="">Select Lens Type</option>
                      {LENS_TYPES.map(type => (
                        <option key={type} value={type}>{type}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-sm text-gray-600 mb-1 block">Next Checkup</label>
                    <input
                      type="date"
                      value={finalRxData.nextCheckup}
                      onChange={(e) => setFinalRxData({ ...finalRxData, nextCheckup: e.target.value })}
                      className="input-field"
                    />
                  </div>
                </div>
              </div>

              {/* Remarks */}
              <div className="card">
                <label className="text-sm text-gray-600 mb-1 block">Remarks</label>
                <textarea
                  value={finalRxData.remarks}
                  onChange={(e) => setFinalRxData({ ...finalRxData, remarks: e.target.value })}
                  placeholder="Clinical notes, recommendations..."
                  className="input-field w-full h-24 resize-none"
                />
              </div>
            </div>
          )}

          {/* Uploads Tab */}
          {activeTab === 'uploads' && (
            <div className="space-y-4">
              <div className="card">
                <h4 className="font-medium text-gray-800 mb-4">Upload Previous Prescription / Documents</h4>
                <div className="border-2 border-dashed border-gray-300 rounded-lg p-12 text-center bg-gray-50">
                  <input
                    type="file"
                    id="file-upload"
                    className="hidden"
                    multiple
                    accept="image/*,.pdf"
                    onChange={handleFileUpload}
                  />
                  <label
                    htmlFor="file-upload"
                    className="cursor-pointer flex flex-col items-center gap-3"
                  >
                    <Camera className="w-12 h-12 text-gray-400" />
                    <span className="text-gray-600 font-medium">Click to upload files</span>
                    <span className="text-sm text-gray-400">PNG, JPG, PDF up to 10MB</span>
                  </label>
                </div>

                {uploads.length > 0 && (
                  <div className="mt-6 space-y-2">
                    <h4 className="text-sm font-medium text-gray-700">Uploaded Files</h4>
                    <div className="grid grid-cols-2 gap-3">
                      {uploads.map(file => (
                        <div
                          key={file.id}
                          className="flex items-center justify-between p-3 bg-gray-50 rounded-lg border border-gray-200"
                        >
                          <div className="flex items-center gap-3">
                            <FileText className="w-5 h-5 text-gray-400" />
                            <div>
                              <p className="text-sm font-medium text-gray-900 truncate max-w-[150px]">{file.name}</p>
                              <p className="text-xs text-gray-500">
                                {(file.size / 1024).toFixed(1)} KB
                              </p>
                            </div>
                          </div>
                          <button
                            onClick={() => removeUpload(file.id)}
                            className="p-1 text-gray-400 hover:text-red-600 transition-colors"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-4 border-t border-gray-200 bg-gray-50">
          <button
            onClick={() => {/* TODO: View history */}}
            className="btn-outline flex items-center gap-2"
          >
            <History className="w-4 h-4" />
            View History
          </button>
          <button
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
