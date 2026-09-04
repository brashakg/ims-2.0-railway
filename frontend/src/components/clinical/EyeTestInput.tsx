// ============================================================================
// IMS 2.0 - Eye Test Input Components (PowerInput, PowerGrid, EyePowerRow)
// ============================================================================
// These wrap the shared, sign-aware RxPowerInput so the clinical exam steps
// (Lensometer / Auto-Ref / Subjective Rx) get the same +/- handling and
// optical-format-on-blur as the POS + intake forms. CLINICAL-CRITICAL: the sign
// of a power is medically load-bearing; RxPowerInput preserves it end-to-end.
//
// The exam page renders each eye as a ROW of 44px monospace power boxes under
// one header row (SPH CYL AXIS ADD PD VA), and a guardrail -- the Rx drift
// warning -- sits UNDER the field it is about, never in a toast.

import clsx from 'clsx';
import { RxPowerInput, type RxPowerKind } from './RxPowerInput';
import type { PowerReading } from './eyeTestTypes';

interface PowerInputProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** Which Rx kind this field is (drives sign/format rules). VA is free text. */
  kind?: RxPowerKind;
  /** Accessible name, when it must differ from the short visible label. */
  ariaLabel?: string;
  /** The step's own guardrail, rendered in place under the box. */
  warning?: string | null;
}

export function PowerInput({
  label,
  value,
  onChange,
  placeholder = '',
  kind = 'SPH',
  ariaLabel,
  warning,
}: PowerInputProps) {
  return (
    <div className="flex flex-col items-center">
      <RxPowerInput
        kind={kind}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className={clsx('pw', value.trim() !== '' && 'filled')}
        aria-label={ariaLabel ?? label}
      />
      {warning && (
        <div className="exam-drift" role="note">
          {warning}
        </div>
      )}
    </div>
  );
}

/** The header row + table the eye rows sit in. */
export function PowerGrid({ showVA = true, children }: { showVA?: boolean; children: React.ReactNode }) {
  return (
    <div className="pw-wrap">
      <table className="pw-grid">
        <thead>
          <tr>
            <th aria-label="Eye" />
            <th>SPH</th>
            <th>CYL</th>
            <th>AXIS</th>
            <th>ADD</th>
            <th>PD</th>
            {showVA && <th>VA</th>}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/** Drift warnings keyed by the field they belong under. */
export type PowerWarnings = Partial<Record<'sphere' | 'cylinder', string | null>>;

interface EyePowerRowProps {
  eye: 'R' | 'L';
  data: PowerReading;
  onChange: (field: keyof PowerReading, value: string) => void;
  showVA?: boolean;
  warnings?: PowerWarnings;
}

export function EyePowerRow({
  eye,
  data,
  onChange,
  showVA = true,
  warnings,
}: EyePowerRowProps) {
  // Both eyes render in the SAME step, so a bare "SPH" label appears twice and a
  // screen reader (or a test) cannot tell which eye it is on. Prefix the eye.
  const side = eye === 'R' ? 'Right' : 'Left';
  const L = (field: string) => `${side} ${field}`;
  return (
    <tr>
      <td className="eye">{eye}</td>
      <td>
        <PowerInput kind="SPH" label="SPH" ariaLabel={L('SPH')} value={data.sphere} onChange={(v) => onChange('sphere', v)} placeholder="+0.00" warning={warnings?.sphere} />
      </td>
      <td>
        <PowerInput kind="CYL" label="CYL" ariaLabel={L('CYL')} value={data.cylinder} onChange={(v) => onChange('cylinder', v)} placeholder="-0.00" warning={warnings?.cylinder} />
      </td>
      <td>
        <PowerInput kind="AXIS" label="AXIS" ariaLabel={L('AXIS')} value={data.axis} onChange={(v) => onChange('axis', v)} placeholder="1-180" />
      </td>
      <td>
        <PowerInput kind="ADD" label="ADD" ariaLabel={L('ADD')} value={data.add} onChange={(v) => onChange('add', v)} placeholder="+0.00" />
      </td>
      <td>
        <PowerInput kind="PD" label="PD" ariaLabel={L('PD')} value={data.pd} onChange={(v) => onChange('pd', v)} placeholder="mm" />
      </td>
      {showVA && (
        <td>
          <PowerInput kind="VA" label="VA" ariaLabel={L('VA')} value={data.va} onChange={(v) => onChange('va', v)} placeholder="6/6" />
        </td>
      )}
    </tr>
  );
}
