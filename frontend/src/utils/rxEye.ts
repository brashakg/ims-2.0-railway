// ============================================================================
// IMS 2.0 — Defensive prescription eye-power reader
// ============================================================================
// The Rx eye block is, transitionally, stored under several shapes:
//   - test docs nest the captured Rx at `prescription.rightEye.sphere`
//   - prescription docs are flat: `rightEye.sph` / `right_eye.sph`
//   - field names alias: sphere|sph, cylinder|cyl, add|addition|add_power
// Read-sites that hard-coded one shape (e.g. `test.rightEye.sphere`) rendered
// "−" because the data lived elsewhere. This reader bridges all of them in one
// place. Once the canonical-shape normalization (initiative C3) lands at the
// write boundary, the aliases collapse to `sph/cyl/axis/add` and this reader
// keeps working unchanged.
// ============================================================================

type AnyRec = Record<string, any> | null | undefined;
type Side = 'right' | 'left';
type Field = 'sphere' | 'cylinder' | 'axis' | 'add';

const FIELD_ALIASES: Record<Field, string[]> = {
  sphere: ['sphere', 'sph'],
  cylinder: ['cylinder', 'cyl'],
  axis: ['axis'],
  add: ['add', 'addition', 'add_power'],
};

/** Resolve the eye object regardless of nesting / casing. */
export function eyeObject(doc: AnyRec, side: Side): AnyRec {
  if (!doc || typeof doc !== 'object') return undefined;
  const camel = side === 'right' ? 'rightEye' : 'leftEye';
  const snake = side === 'right' ? 'right_eye' : 'left_eye';
  const p = (doc as AnyRec)?.prescription; // test docs nest under `prescription`
  return p?.[camel] ?? p?.[snake] ?? (doc as any)[camel] ?? (doc as any)[snake] ?? undefined;
}

/** Read one eye power, trying every field-name alias. Returns undefined if
 *  absent. Typed `any` so it drops into the existing `formatPower(...)` call
 *  sites (which accept the raw heterogeneous value) without a cast. */
export function readEyePower(doc: AnyRec, side: Side, field: Field): any {
  const eye = eyeObject(doc, side) as AnyRec;
  if (!eye) return undefined;
  for (const k of FIELD_ALIASES[field]) {
    const v = (eye as any)[k];
    if (v !== undefined && v !== null && v !== '') return v;
  }
  return undefined;
}
