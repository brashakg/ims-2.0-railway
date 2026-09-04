// ============================================================================
// IMS 2.0 - Subjective refraction (exam step 4)
// ============================================================================

import { Copy } from 'lucide-react';
import { EyePowerRow, PowerGrid } from './EyeTestInput';
import type { AutoRefData, LensometerData, PowerReading, SubjectiveRxData } from './eyeTestTypes';
import { rxDriftWarning, type PreviousRx } from './rxDrift';

interface SubjectiveRxTabProps {
  data: SubjectiveRxData;
  onChange: (data: SubjectiveRxData) => void;
  /** The earlier steps this one can carry in. */
  copyFrom?: { autoRef?: AutoRefData; lensometer?: LensometerData };
  /** The guardrail's inputs: the patient's previous Rx and their age. */
  drift?: { previousRx?: PreviousRx | null; age?: number };
}

const POWER_KEYS: (keyof PowerReading)[] = ['sphere', 'cylinder', 'axis', 'add', 'pd', 'va'];

/** Only the RECORDED values of a reading -- a blank never overwrites a typed one. */
function recorded(src: PowerReading | undefined): Partial<PowerReading> {
  const out: Partial<PowerReading> = {};
  for (const k of POWER_KEYS) {
    const v = src?.[k];
    if (v && v.trim() !== '') out[k] = v;
  }
  return out;
}

function hasAny(src: { rightEye?: PowerReading; leftEye?: PowerReading } | undefined): boolean {
  return (
    Object.keys(recorded(src?.rightEye)).length > 0 ||
    Object.keys(recorded(src?.leftEye)).length > 0
  );
}

export function SubjectiveRxTab({ data, onChange, copyFrom, drift }: SubjectiveRxTabProps) {
  const handleEyeChange = (eye: 'rightEye' | 'leftEye', field: keyof PowerReading, value: string) => {
    onChange({
      ...data,
      [eye]: { ...data[eye], [field]: value },
    });
  };

  const copy = (src: { rightEye: PowerReading; leftEye: PowerReading }) =>
    onChange({
      ...data,
      rightEye: { ...data.rightEye, ...recorded(src.rightEye) },
      leftEye: { ...data.leftEye, ...recorded(src.leftEye) },
    });

  const warn = (eye: 'Right' | 'Left', field: 'SPH' | 'CYL', next: string) =>
    rxDriftWarning({ eye, field, previous: drift?.previousRx, next, age: drift?.age });

  return (
    <div className="space-y-4">
      {(copyFrom?.autoRef || copyFrom?.lensometer) && (
        <div className="flex items-center gap-2 flex-wrap">
          {copyFrom.autoRef && (
            <button
              type="button"
              className="btn lg"
              disabled={!hasAny(copyFrom.autoRef)}
              onClick={() => copy(copyFrom.autoRef as AutoRefData)}
            >
              <Copy className="w-4 h-4" /> Copy from auto-ref
            </button>
          )}
          {copyFrom.lensometer && (
            <button
              type="button"
              className="btn lg"
              disabled={!hasAny(copyFrom.lensometer)}
              onClick={() => copy(copyFrom.lensometer as LensometerData)}
            >
              <Copy className="w-4 h-4" /> Copy old Rx
            </button>
          )}
        </div>
      )}

      <PowerGrid showVA>
        <EyePowerRow
          eye="R"
          data={data.rightEye}
          onChange={(field, value) => handleEyeChange('rightEye', field, value)}
          showVA
          warnings={{
            sphere: warn('Right', 'SPH', data.rightEye.sphere),
            cylinder: warn('Right', 'CYL', data.rightEye.cylinder),
          }}
        />
        <EyePowerRow
          eye="L"
          data={data.leftEye}
          onChange={(field, value) => handleEyeChange('leftEye', field, value)}
          showVA
          warnings={{
            sphere: warn('Left', 'SPH', data.leftEye.sphere),
            cylinder: warn('Left', 'CYL', data.leftEye.cylinder),
          }}
        />
      </PowerGrid>

      <div>
        <label htmlFor="subjective-remarks" className="text-sm text-ink-3 mb-1 block">Remarks</label>
        <textarea
          id="subjective-remarks"
          value={data.remarks}
          onChange={(e) => onChange({ ...data, remarks: e.target.value })}
          placeholder="Subjective refraction notes..."
          className="input-field w-full h-20 resize-none"
        />
      </div>
    </div>
  );
}
