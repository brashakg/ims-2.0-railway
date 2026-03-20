// ============================================================================
// IMS 2.0 - Lensometer Tab Component
// ============================================================================

import { Glasses } from 'lucide-react';
import { EyePowerRow } from './EyeTestInput';
import type { LensometerData, PowerReading } from './eyeTestTypes';

interface LensometerTabProps {
  data: LensometerData;
  onChange: (data: LensometerData) => void;
}

export function LensometerTab({ data, onChange }: LensometerTabProps) {
  const handleEyeChange = (eye: 'rightEye' | 'leftEye', field: keyof PowerReading, value: string) => {
    onChange({
      ...data,
      [eye]: { ...data[eye], [field]: value },
    });
  };

  return (
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
            data={data.rightEye}
            onChange={(field, value) => handleEyeChange('rightEye', field, value)}
            showVA={false}
          />
          <EyePowerRow
            eye="L"
            data={data.leftEye}
            onChange={(field, value) => handleEyeChange('leftEye', field, value)}
            showVA={false}
          />
        </div>

        <div className="mt-4">
          <label className="text-sm text-gray-600 mb-1 block">Remarks</label>
          <textarea
            value={data.remarks}
            onChange={(e) => onChange({ ...data, remarks: e.target.value })}
            placeholder="Any observations about current glasses..."
            className="input-field w-full h-20 resize-none"
          />
        </div>
      </div>
    </div>
  );
}
