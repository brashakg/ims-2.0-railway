// ============================================================================
// IMS 2.0 - Shared, sign-aware Rx power input
// ============================================================================
// CLINICAL-CRITICAL: the SIGN of an optical power (+ vs -) is medically
// load-bearing. A `-0.75` cylinder ground as `+0.75` is the WRONG lens. This
// one component is the single door for typing an Rx power so signs are never
// lost, flipped, or made hard to enter.
//
// Why not <input type="number">? Three real problems it causes on Rx fields:
//   1. type=number rejects a leading `+` and refuses to hold partial states
//      like `-` or `0.` while typing, so `+0.25` / `-0.75` are hard/impossible.
//   2. its placeholder (e.g. "+1.00") renders in the browser's dark grey and
//      reads like a real value.
//   3. a positive power shows WITHOUT its `+` (5.00, not the required +5.00).
//
// This renders <input type="text" inputMode="decimal"> (numeric for AXIS) and:
//   * WHILE TYPING: lightly sanitizes but PRESERVES partial states (a lone `-`,
//     a trailing `.`, a leading `0`) so the user can build the value freely.
//   * ON BLUR + on mount / prop-change: normalizes to canonical optical format
//     via formatRxPower() (explicit `+` on positive SPH/CYL/ADD, 2 decimals,
//     integer AXIS 1-180, 1-decimal PD). An invalid/non-numeric string is left
//     UNCHANGED so the form's own range validation still flags it.
//
// The value is always a STRING (e.g. "+5.00", "-0.75", "0.00"). Number("+5.00")
// === 5 and Python float("+5.00") === 5.0, so the signed string round-trips
// through the API client and the backend `float(...)` parsers unchanged.
// ============================================================================

import { useEffect, useRef, useState } from 'react';
import clsx from 'clsx';

export type RxPowerKind = 'SPH' | 'CYL' | 'ADD' | 'AXIS' | 'PD' | 'VA' | 'BC' | 'DIA';

// Kinds that carry a sign the user may type. ADD is plus-only (normalized to
// +abs on blur); SPH/CYL are signed both ways. AXIS/PD/VA/BC/DIA carry no sign.
const SIGNED_KINDS: RxPowerKind[] = ['SPH', 'CYL', 'ADD'];

// Millimetre measurements formatted to one decimal, unsigned (like PD): contact-
// lens Base Curve (BC) and Diameter (DIA).
const MM_1DP_KINDS: RxPowerKind[] = ['PD', 'BC', 'DIA'];

/**
 * Normalize an Rx power string to its canonical optical representation.
 * Pure + exported so it is unit-testable and reusable outside the component.
 *
 * Rules (only applied when the string parses to a finite number):
 *   SPH / CYL : >0 -> "+X.XX", <0 -> "-X.XX", ==0 -> "0.00"   (2 dp, explicit +)
 *   ADD       : always positive -> "+X.XX"  (plus-only; a negative is |abs|'d)
 *   AXIS      : whole number, clamped to 1..180, no sign, no decimals
 *   PD        : one decimal (e.g. "32.5"), no sign
 *   VA        : returned as-is (free text like "6/6")
 *   ""        : stays ""
 *   invalid   : returned UNCHANGED (so range validation downstream still bites)
 */
export function formatRxPower(value: string, kind: RxPowerKind): string {
  if (value == null) return '';
  const raw = String(value).trim();
  if (raw === '') return '';

  // VA is free text (e.g. "6/6", "6/9", "CF", "HM") -- never reformat.
  if (kind === 'VA') return raw;

  const num = Number(raw);
  // Number("") === 0, but raw is non-empty here. Number("+") / Number("-.") etc.
  // are NaN -> leave the partial/invalid string untouched for the form to flag.
  if (!Number.isFinite(num)) return raw;

  if (kind === 'AXIS') {
    // Whole degree. Keep it a plain integer; clamp into 1..180 so a stray 200
    // or 0 is corrected rather than silently accepted.
    let deg = Math.round(num);
    if (deg < 1) deg = 1;
    if (deg > 180) deg = 180;
    return String(deg);
  }

  if (MM_1DP_KINDS.includes(kind)) {
    // PD / BC / DIA are millimetre measurements: one decimal, unsigned.
    return Math.abs(num).toFixed(1);
  }

  if (kind === 'ADD') {
    // ADD is plus-only. If someone typed a negative, take its magnitude.
    const mag = Math.abs(num);
    return `+${mag.toFixed(2)}`;
  }

  // SPH / CYL (and any other signed dioptric kind): explicit + on positives,
  // explicit - on negatives, plain 0.00 for plano.
  if (num > 0) return `+${num.toFixed(2)}`;
  if (num < 0) return `-${Math.abs(num).toFixed(2)}`;
  return '0.00';
}

/**
 * Lightly sanitize a value WHILE the user is typing. Must PRESERVE partial /
 * intermediate states (a lone "+"/"-", a trailing ".", a leading "0") so the
 * value can be built keystroke-by-keystroke. Does NOT reformat -- that only
 * happens on blur via formatRxPower. This is what fixes the "can't type -, +,
 * or a leading 0" problem that <input type=number> creates.
 */
function sanitizeWhileTyping(value: string, kind: RxPowerKind): string {
  if (kind === 'VA') return value; // free text

  if (kind === 'AXIS') {
    // Digits only (a whole-degree axis has no sign and no decimal point).
    return value.replace(/[^0-9]/g, '');
  }

  const allowSign = SIGNED_KINDS.includes(kind);
  // Strip anything that isn't a digit, a dot, or (for signed kinds) a sign.
  let out = value.replace(allowSign ? /[^0-9.+-]/g : /[^0-9.]/g, '');

  if (allowSign) {
    // At most ONE leading sign; drop any other sign characters.
    const negative = out.startsWith('-');
    const positive = out.startsWith('+');
    out = out.replace(/[+-]/g, '');
    if (negative) out = `-${out}`;
    else if (positive) out = `+${out}`;
  }

  // At most one decimal point: keep the first, drop the rest.
  const firstDot = out.indexOf('.');
  if (firstDot !== -1) {
    out = out.slice(0, firstDot + 1) + out.slice(firstDot + 1).replace(/\./g, '');
  }
  return out;
}

export interface RxPowerInputProps {
  kind: RxPowerKind;
  value: string;
  onChange: (value: string) => void;
  label?: string;
  placeholder?: string;
  className?: string;
  /** Convenience width class (e.g. "w-20"); merged into className. */
  width?: string;
  'aria-label'?: string;
  disabled?: boolean;
  id?: string;
}

/**
 * Sign-aware Rx power text input. Emits a STRING; normalizes on blur + on
 * incoming prop change so a stored "+5.00" displays with its sign and a bare
 * "5.00" is upgraded to "+5.00".
 */
export function RxPowerInput({
  kind,
  value,
  onChange,
  label,
  placeholder,
  className,
  width,
  disabled,
  id,
  'aria-label': ariaLabel,
}: RxPowerInputProps) {
  // Local text state so partial typing (a lone "-", "0.", ".") isn't clobbered
  // by a re-render before blur. We push sanitized text up on every keystroke so
  // the parent always has a usable string, but only normalize on blur.
  const [text, setText] = useState<string>(() => formatRxPower(value ?? '', kind));
  // Track the last value we emitted upward so an external value change (edit
  // load, "copy from subjective", reset) re-normalizes into the box, but our
  // own keystroke echo does not fight the local partial state.
  const lastEmitted = useRef<string>(value ?? '');

  useEffect(() => {
    const incoming = value ?? '';
    if (incoming !== lastEmitted.current) {
      // Value changed from OUTSIDE (not our own onChange echo) -> normalize it
      // into the visible box so stored/edited signs render canonically.
      const formatted = formatRxPower(incoming, kind);
      setText(formatted);
      lastEmitted.current = incoming;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, kind]);

  const inputMode: 'decimal' | 'numeric' = kind === 'AXIS' ? 'numeric' : 'decimal';

  const handleChange = (raw: string) => {
    const cleaned = sanitizeWhileTyping(raw, kind);
    setText(cleaned);
    lastEmitted.current = cleaned;
    onChange(cleaned);
  };

  const handleBlur = () => {
    const normalized = formatRxPower(text, kind);
    setText(normalized);
    if (normalized !== lastEmitted.current) {
      lastEmitted.current = normalized;
      onChange(normalized);
    }
  };

  return (
    <input
      id={id}
      type="text"
      inputMode={inputMode}
      value={text}
      onChange={(e) => handleChange(e.target.value)}
      onBlur={handleBlur}
      placeholder={placeholder}
      disabled={disabled}
      aria-label={ariaLabel ?? label}
      // `rx-power-input` scopes a LIGHTER placeholder colour (see the rule added
      // to index.css) so the example hint doesn't read as a real value. We do
      // NOT touch the global .input-field::placeholder.
      className={clsx('rx-power-input', className, width)}
    />
  );
}

export default RxPowerInput;
