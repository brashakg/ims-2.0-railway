// ============================================================================
// IMS 2.0 - Eye Test Input Components (PowerInput & EyePowerRow)
// ============================================================================
// These wrap the shared, sign-aware RxPowerInput so the clinical exam tabs
// (Lensometer / Auto-Ref / Subjective Rx) get the same +/- handling and
// optical-format-on-blur as the POS + intake forms. CLINICAL-CRITICAL: the sign
// of a power is medically load-bearing; RxPowerInput preserves it end-to-end.

import { RxPowerInput, type RxPowerKind } from './RxPowerInput';
import type { PowerReading } from './eyeTestTypes';

interface PowerInputProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  width?: string;
  /** Which Rx kind this field is (drives sign/format rules). VA is free text. */
  kind?: RxPowerKind;
}

export function PowerInput({
  label,
  value,
  onChange,
  placeholder = '',
  width = 'w-20',
  kind = 'SPH',
}: PowerInputProps) {
  return (
    <div className="flex flex-col">
      <label className="text-xs text-gray-500 mb-1">{label}</label>
      <RxPowerInput
        kind={kind}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className="input-field text-center text-sm"
        width={width}
        aria-label={label}
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
    <div className="overflow-x-auto">
      <div className="flex items-center gap-2 py-2 min-w-max">
        <div
          className={
            `w-8 h-8 rounded-full flex items-center justify-center font-bold text-sm ${
              eye === 'R' ? 'bg-blue-100 text-blue-600' : 'bg-green-100 text-green-600'
            }`
          }
        >
          {eye}
        </div>
        <PowerInput kind="SPH" label="SPH" value={data.sphere} onChange={(v) => onChange('sphere', v)} placeholder="+0.00" />
        <PowerInput kind="CYL" label="CYL" value={data.cylinder} onChange={(v) => onChange('cylinder', v)} placeholder="-0.00" />
        <PowerInput kind="AXIS" label="AXIS" value={data.axis} onChange={(v) => onChange('axis', v)} placeholder="1-180" width="w-16" />
        <PowerInput kind="ADD" label="ADD" value={data.add} onChange={(v) => onChange('add', v)} placeholder="+0.00" width="w-16" />
        <PowerInput kind="PD" label="PD" value={data.pd} onChange={(v) => onChange('pd', v)} placeholder="mm" width="w-16" />
        {showVA && (
          <PowerInput kind="VA" label="VA" value={data.va} onChange={(v) => onChange('va', v)} placeholder="6/6" width="w-16" />
        )}
      </div>
    </div>
  );
}
