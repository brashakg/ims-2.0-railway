import { describe, it, expect } from 'vitest';
import { eyeObject, readEyePower } from '../rxEye';

describe('eyeObject', () => {
  it('resolves camelCase top-level eye objects', () => {
    const doc = { rightEye: { sphere: -1.5 }, leftEye: { sphere: -2.0 } };
    expect(eyeObject(doc, 'right')).toEqual({ sphere: -1.5 });
    expect(eyeObject(doc, 'left')).toEqual({ sphere: -2.0 });
  });

  it('resolves snake_case top-level eye objects', () => {
    const doc = { right_eye: { sph: -1.0 }, left_eye: { sph: -1.25 } };
    expect(eyeObject(doc, 'right')).toEqual({ sph: -1.0 });
    expect(eyeObject(doc, 'left')).toEqual({ sph: -1.25 });
  });

  it('prefers the nested `prescription.*` shape used by test docs', () => {
    const testDoc = {
      prescription: { rightEye: { sphere: -3.0 } },
      // a stray flat field that should be shadowed by the nested one
      rightEye: { sphere: 0 },
    };
    expect(eyeObject(testDoc, 'right')).toEqual({ sphere: -3.0 });
  });

  it('returns undefined for non-objects / missing eye', () => {
    expect(eyeObject(null, 'right')).toBeUndefined();
    expect(eyeObject(undefined, 'left')).toBeUndefined();
    expect(eyeObject({}, 'right')).toBeUndefined();
    expect(eyeObject('str' as any, 'right')).toBeUndefined();
  });
});

describe('readEyePower', () => {
  it('reads via the primary field name', () => {
    const doc = { rightEye: { sphere: -1.5, cylinder: -0.75, axis: 90, add: 1.5 } };
    expect(readEyePower(doc, 'right', 'sphere')).toBe(-1.5);
    expect(readEyePower(doc, 'right', 'cylinder')).toBe(-0.75);
    expect(readEyePower(doc, 'right', 'axis')).toBe(90);
    expect(readEyePower(doc, 'right', 'add')).toBe(1.5);
  });

  it('falls back across field-name aliases (sph/cyl/add_power)', () => {
    const doc = { left_eye: { sph: -2.25, cyl: -1.0, add_power: 2.0 } };
    expect(readEyePower(doc, 'left', 'sphere')).toBe(-2.25);
    expect(readEyePower(doc, 'left', 'cylinder')).toBe(-1.0);
    expect(readEyePower(doc, 'left', 'add')).toBe(2.0);
  });

  it('treats empty string / null / undefined values as absent and keeps trying aliases', () => {
    // `add` is '' but `addition` carries the real value
    const doc = { rightEye: { add: '', addition: 1.25 } };
    expect(readEyePower(doc, 'right', 'add')).toBe(1.25);
  });

  it('preserves a real 0 value (does not treat 0 as missing)', () => {
    const doc = { rightEye: { sphere: 0 } };
    expect(readEyePower(doc, 'right', 'sphere')).toBe(0);
  });

  it('returns undefined when neither the eye nor the field exists', () => {
    expect(readEyePower({}, 'right', 'sphere')).toBeUndefined();
    expect(readEyePower({ rightEye: {} }, 'right', 'axis')).toBeUndefined();
  });
});
