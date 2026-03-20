// ============================================================================
// IMS 2.0 - Subjective Rx Tab Component
// ============================================================================

import { Eye } from 'lucide-react';
import { EyePowerRow } from './EyeTestInput';
import type { SubjectiveRxData, PowerReading } from './eyeTestTypes';

interface SubjectiveRxTabProps {
  data: SubjectiveRxData;
  onChange: (data: SubjectiveRxData) => void;
}

export function SubjectiveRxTab({ data, onChange }: SubjectiveRxTabProps) {
  const handleEyeChange = (eye: 'rightEye' | 'leftEye', field: keyof PowerReading, value: string) => {
    onChange({
      ...data,
      [eye]: { ...data[eye], [field]: value },
    });
  };

  return (
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
            data={data.rightEye}
            onChange={(field, value) => handleEyeChange('rightEye', field, value)}
            showVA={true}
          />
          <EyePowerRow
            eye="L"
            data={data.leftEye}
            onChange={(field, value) => handleEyeChange('leftEye', field, value)}
            showVA={true}
          />
        </div>

        <div className="mt-4">
          <label className="text-sm text-gray-600 mb-1 block">Remarks</label>
          <textarea
            value={data.remarks}
            onChange={(e) => onChange({ ...data, remarks: e.target.value })}
            placeholder="Subjective refraction notes..."
            className="input-field w-full h-20 resize-none"
          />
        </div>
      </div>
    </div>
  );
}
