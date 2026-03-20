// ============================================================================
// IMS 2.0 - Final Rx (Prescription) Tab Component
// ============================================================================

import { FileText } from 'lucide-react';
import type { FinalRxData, SubjectiveRxData } from './eyeTestTypes';
import { LENS_TYPES } from './eyeTestTypes';

interface FinalRxTabProps {
  data: FinalRxData;
  onChange: (data: FinalRxData) => void;
  subjectiveRxData: SubjectiveRxData;
}

const VA_OPTIONS = ['6/6', '6/9', '6/12', '6/18', '6/24', '6/36', '6/60'];
const BASE_OPTIONS = ['IN', 'OUT', 'UP', 'DOWN'];

function DistanceVisionRow({
  label,
  eye,
  data,
  onFieldChange,
}: {
  label: string;
  eye: 'rightEye' | 'leftEye';
  data: FinalRxData['rightEye'];
  onFieldChange: (eye: 'rightEye' | 'leftEye', field: string, value: string) => void;
}) {
  return (
    <tr className={eye === 'rightEye' ? 'border-b border-gray-100' : ''}>
      <td className="py-3 px-3 font-medium text-gray-900">{label}</td>
      <td className="py-2 px-2">
        <input type="text" value={data.sphere} onChange={(e) => onFieldChange(eye, 'sphere', e.target.value)}
          placeholder="SPH" className="input-field text-center text-sm w-full" />
      </td>
      <td className="py-2 px-2">
        <input type="text" value={data.cylinder} onChange={(e) => onFieldChange(eye, 'cylinder', e.target.value)}
          placeholder="CYL" className="input-field text-center text-sm w-full" />
      </td>
      <td className="py-2 px-2">
        <input type="text" value={data.axis} onChange={(e) => onFieldChange(eye, 'axis', e.target.value)}
          placeholder="1-180" className="input-field text-center text-sm w-full" />
      </td>
      <td className="py-2 px-2">
        <input type="text" value={data.prism} onChange={(e) => onFieldChange(eye, 'prism', e.target.value)}
          placeholder="Prism" className="input-field text-center text-sm w-full" />
      </td>
      <td className="py-2 px-2">
        <select value={data.base} onChange={(e) => onFieldChange(eye, 'base', e.target.value)}
          className="input-field text-center text-sm w-full">
          <option value="">-</option>
          {BASE_OPTIONS.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </td>
      <td className="py-2 px-2">
        <select value={data.va} onChange={(e) => onFieldChange(eye, 'va', e.target.value)}
          className="input-field text-center text-sm w-full">
          {VA_OPTIONS.map(opt => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
      </td>
    </tr>
  );
}

export function FinalRxTab({ data, onChange, subjectiveRxData }: FinalRxTabProps) {
  const handleFieldChange = (eye: 'rightEye' | 'leftEye', field: string, value: string) => {
    onChange({
      ...data,
      [eye]: { ...data[eye], [field]: value },
    });
  };

  const handleCopyFromSubjective = () => {
    onChange({
      ...data,
      rightEye: { ...data.rightEye, ...subjectiveRxData.rightEye },
      leftEye: { ...data.leftEye, ...subjectiveRxData.leftEye },
    });
  };

  const handleCopyRightToLeft = () => {
    onChange({
      ...data,
      leftEye: { ...data.rightEye },
      leftAdd: data.rightAdd,
    });
  };

  return (
    <div className="space-y-4">
      {/* Header with Copy buttons */}
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-gray-900">Final Prescription</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={handleCopyFromSubjective}
            className="btn-outline text-sm flex items-center gap-1"
          >
            <FileText className="w-4 h-4" />
            Copy from Subjective
          </button>
          <button
            onClick={handleCopyRightToLeft}
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
              <DistanceVisionRow label="Right (OD)" eye="rightEye" data={data.rightEye} onFieldChange={handleFieldChange} />
              <DistanceVisionRow label="Left (OS)" eye="leftEye" data={data.leftEye} onFieldChange={handleFieldChange} />
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
              value={data.rightAdd}
              onChange={(e) => onChange({ ...data, rightAdd: e.target.value })}
              placeholder="+0.00"
              className="input-field"
            />
          </div>
          <div>
            <label className="text-sm text-gray-600 mb-1 block">Left ADD</label>
            <input
              type="text"
              value={data.leftAdd}
              onChange={(e) => onChange({ ...data, leftAdd: e.target.value })}
              placeholder="+0.00"
              className="input-field"
            />
          </div>
          <div>
            <label className="text-sm text-gray-600 mb-1 block">IPD (mm)</label>
            <input
              type="text"
              value={data.ipd}
              onChange={(e) => onChange({ ...data, ipd: e.target.value })}
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
              value={data.lensType}
              onChange={(e) => onChange({ ...data, lensType: e.target.value })}
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
              value={data.nextCheckup}
              onChange={(e) => onChange({ ...data, nextCheckup: e.target.value })}
              className="input-field"
            />
          </div>
        </div>
      </div>

      {/* Remarks */}
      <div className="card">
        <label className="text-sm text-gray-600 mb-1 block">Remarks</label>
        <textarea
          value={data.remarks}
          onChange={(e) => onChange({ ...data, remarks: e.target.value })}
          placeholder="Clinical notes, recommendations..."
          className="input-field w-full h-24 resize-none"
        />
      </div>
    </div>
  );
}
