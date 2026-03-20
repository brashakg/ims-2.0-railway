// ============================================================================
// IMS 2.0 - Slit Lamp Tab Component
// ============================================================================

import { Eye } from 'lucide-react';
import type { SlitLampData, SlitLampEye } from './eyeTestTypes';

interface SlitLampTabProps {
  data: SlitLampData;
  onChange: (data: SlitLampData) => void;
}

function formatSlitLampLabel(key: string): string {
  if (key === 'ac') return 'A/C';
  if (key === 'iop') return 'IOP';
  return key;
}

export function SlitLampTab({ data, onChange }: SlitLampTabProps) {
  const handleEyeChange = (eye: 'rightEye' | 'leftEye', key: string, value: string) => {
    onChange({
      ...data,
      [eye]: { ...data[eye], [key]: value },
    });
  };

  const renderEyeFields = (eye: 'rightEye' | 'leftEye', label: string, badge: 'R' | 'L') => {
    const badgeColor = badge === 'R' ? 'bg-blue-100 text-blue-600' : 'bg-green-100 text-green-600';
    return (
      <div className="card">
        <div className="flex items-center gap-2 mb-4">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center font-bold ${badgeColor}`}>{badge}</div>
          <h3 className="font-semibold text-gray-900">{label}</h3>
        </div>
        <div className="space-y-3">
          {Object.entries(data[eye]).map(([key, value]) => (
            <div key={key} className="flex items-center gap-2">
              <label className="text-sm text-gray-600 w-24 capitalize">{formatSlitLampLabel(key)}</label>
              <input
                type="text"
                value={value as string}
                onChange={(e) => handleEyeChange(eye, key as keyof SlitLampEye, e.target.value)}
                className="input-field flex-1 text-sm"
              />
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-4">
      <div className="bg-purple-50 border border-purple-200 rounded-lg p-3 flex items-center gap-2">
        <Eye className="w-5 h-5 text-purple-600" />
        <span className="text-sm text-purple-700">Anterior segment examination findings</span>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {renderEyeFields('rightEye', 'Right Eye', 'R')}
        {renderEyeFields('leftEye', 'Left Eye', 'L')}
      </div>

      <div className="card">
        <label className="text-sm text-gray-600 mb-1 block">Remarks</label>
        <textarea
          value={data.remarks}
          onChange={(e) => onChange({ ...data, remarks: e.target.value })}
          placeholder="Additional clinical findings..."
          className="input-field w-full h-20 resize-none"
        />
      </div>
    </div>
  );
}
