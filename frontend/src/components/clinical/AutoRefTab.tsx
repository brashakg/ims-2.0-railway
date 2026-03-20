// ============================================================================
// IMS 2.0 - Auto-Refractometer Tab Component
// ============================================================================

import { Camera } from 'lucide-react';
import { EyePowerRow } from './EyeTestInput';
import type { AutoRefData, PowerReading } from './eyeTestTypes';

interface AutoRefTabProps {
  data: AutoRefData;
  onChange: (data: AutoRefData) => void;
}

function KeratometryEye({
  eye,
  label,
  badge,
  k1,
  k1Axis,
  k2,
  k2Axis,
  onFieldChange,
}: {
  eye: 'rightEye' | 'leftEye';
  label: string;
  badge: string;
  k1: string;
  k1Axis: string;
  k2: string;
  k2Axis: string;
  onFieldChange: (eye: 'rightEye' | 'leftEye', field: string, value: string) => void;
}) {
  const badgeColor = badge === 'R'
    ? 'bg-blue-100 text-blue-600'
    : 'bg-green-100 text-green-600';

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <div className={`w-6 h-6 rounded-full flex items-center justify-center font-bold text-xs ${badgeColor}`}>{badge}</div>
        <span className="text-sm font-medium">{label}</span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-gray-500 mb-1 block">K1 (D)</label>
          <input
            type="text"
            value={k1}
            onChange={(e) => onFieldChange(eye, 'k1', e.target.value)}
            placeholder="e.g., 42.50"
            className="input-field text-sm"
          />
        </div>
        <div>
          <label className="text-xs text-gray-500 mb-1 block">K1 Axis</label>
          <input
            type="text"
            value={k1Axis}
            onChange={(e) => onFieldChange(eye, 'k1Axis', e.target.value)}
            placeholder="0-180"
            className="input-field text-sm"
          />
        </div>
        <div>
          <label className="text-xs text-gray-500 mb-1 block">K2 (D)</label>
          <input
            type="text"
            value={k2}
            onChange={(e) => onFieldChange(eye, 'k2', e.target.value)}
            placeholder="e.g., 43.00"
            className="input-field text-sm"
          />
        </div>
        <div>
          <label className="text-xs text-gray-500 mb-1 block">K2 Axis</label>
          <input
            type="text"
            value={k2Axis}
            onChange={(e) => onFieldChange(eye, 'k2Axis', e.target.value)}
            placeholder="0-180"
            className="input-field text-sm"
          />
        </div>
      </div>
    </div>
  );
}

export function AutoRefTab({ data, onChange }: AutoRefTabProps) {
  const handlePowerChange = (eye: 'rightEye' | 'leftEye', field: keyof PowerReading, value: string) => {
    onChange({
      ...data,
      [eye]: { ...data[eye], [field]: value },
    });
  };

  const handleKFieldChange = (eye: 'rightEye' | 'leftEye', field: string, value: string) => {
    onChange({
      ...data,
      [eye]: { ...data[eye], [field]: value },
    });
  };

  return (
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
            data={data.rightEye}
            onChange={(field, value) => handlePowerChange('rightEye', field, value)}
            showVA={false}
          />
          <EyePowerRow
            eye="L"
            data={data.leftEye}
            onChange={(field, value) => handlePowerChange('leftEye', field, value)}
            showVA={false}
          />
        </div>
      </div>

      <div className="card">
        <h3 className="font-semibold text-gray-900 mb-4">Keratometry (K-Readings)</h3>
        <div className="grid grid-cols-2 gap-6">
          <KeratometryEye
            eye="rightEye"
            label="Right Eye"
            badge="R"
            k1={data.rightEye.k1}
            k1Axis={data.rightEye.k1Axis}
            k2={data.rightEye.k2}
            k2Axis={data.rightEye.k2Axis}
            onFieldChange={handleKFieldChange}
          />
          <KeratometryEye
            eye="leftEye"
            label="Left Eye"
            badge="L"
            k1={data.leftEye.k1}
            k1Axis={data.leftEye.k1Axis}
            k2={data.leftEye.k2}
            k2Axis={data.leftEye.k2Axis}
            onFieldChange={handleKFieldChange}
          />
        </div>
      </div>

      <div className="card">
        <label className="text-sm text-gray-600 mb-1 block">Remarks</label>
        <textarea
          value={data.remarks}
          onChange={(e) => onChange({ ...data, remarks: e.target.value })}
          placeholder="Auto-refraction notes..."
          className="input-field w-full h-20 resize-none"
        />
      </div>
    </div>
  );
}
