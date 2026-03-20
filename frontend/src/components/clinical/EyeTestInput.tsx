// ============================================================================
// IMS 2.0 - Eye Test Input Components (PowerInput & EyePowerRow)
// ============================================================================

import clsx from 'clsx';
import type { PowerReading } from './eyeTestTypes';

interface PowerInputProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  width?: string;
}

export function PowerInput({
  label,
  value,
  onChange,
  placeholder = '',
  width = 'w-20',
}: PowerInputProps) {
  return (
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
}

interface EyePowerRowProps {
  eye: 'R' | 'L';
  data: PowerReading;
  onChange: (field: keyof PowerReading, value: string) => void;
  showVA?: boolean;
}

export function EyePowerRow({
  eye,
  data,
  onChange,
  showVA = true,
}: EyePowerRowProps) {
  return (
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
}
