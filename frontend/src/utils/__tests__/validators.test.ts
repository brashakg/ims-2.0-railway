import { describe, it, expect } from 'vitest';
import {
  validateGstin,
  validatePan,
  validateIfsc,
  validatePincode,
  validatePhone,
  validateGeoRadius,
  firstError,
} from '../validators';

describe('validateGstin', () => {
  it('accepts a well-formed 15-char GSTIN (returns null)', () => {
    expect(validateGstin('22AAAAA0000A1Z5')).toBeNull();
  });
  it('upper-cases + trims before validating', () => {
    expect(validateGstin('  22aaaaa0000a1z5  ')).toBeNull();
  });
  it('rejects a malformed GSTIN', () => {
    expect(validateGstin('22AAAAA0000A1X5')).toMatch(/15 characters/);
    expect(validateGstin('ABCDE')).toMatch(/15 characters/);
  });
  it('treats empty as valid (required-ness enforced by caller)', () => {
    expect(validateGstin('')).toBeNull();
    expect(validateGstin(null)).toBeNull();
    expect(validateGstin(undefined)).toBeNull();
  });
});

describe('validatePan', () => {
  it('accepts a valid PAN', () => {
    expect(validatePan('AAAAA0000A')).toBeNull();
    expect(validatePan('abcde1234f')).toBeNull(); // upper-cased internally
  });
  it('rejects a malformed PAN', () => {
    expect(validatePan('AAAA0000A')).toMatch(/10 characters/);
    expect(validatePan('12345AAAAA')).toMatch(/10 characters/);
  });
});

describe('validateIfsc', () => {
  it('accepts a valid IFSC', () => {
    expect(validateIfsc('HDFC0001234')).toBeNull();
  });
  it('rejects when the 5th char is not 0', () => {
    expect(validateIfsc('HDFC1001234')).toMatch(/11 characters/);
  });
});

describe('validatePincode', () => {
  it('accepts a 6-digit pincode not starting with 0', () => {
    expect(validatePincode('834001')).toBeNull();
  });
  it('rejects a leading-zero or wrong-length pincode', () => {
    expect(validatePincode('034001')).toMatch(/6 digits/);
    expect(validatePincode('1234')).toMatch(/6 digits/);
  });
});

describe('validatePhone', () => {
  it('accepts a 10-digit Indian mobile starting 6-9', () => {
    expect(validatePhone('9810000001')).toBeNull();
  });
  it('tolerates +91 prefix and spacing', () => {
    expect(validatePhone('+91 98100 00001')).toBeNull();
    expect(validatePhone('919810000001')).toBeNull();
  });
  it('rejects numbers that do not start 6-9 or are wrong length', () => {
    expect(validatePhone('1234567890')).toMatch(/10-digit/);
    expect(validatePhone('98100')).toMatch(/10-digit/);
  });
});

describe('validateGeoRadius', () => {
  it('accepts values within 100-2000 metres', () => {
    expect(validateGeoRadius(500)).toBeNull();
    expect(validateGeoRadius('100')).toBeNull();
    expect(validateGeoRadius(2000)).toBeNull();
  });
  it('rejects out-of-range or non-numeric values', () => {
    expect(validateGeoRadius(50)).toMatch(/between 100 and 2000/);
    expect(validateGeoRadius(3000)).toMatch(/between 100 and 2000/);
    expect(validateGeoRadius('abc')).toMatch(/between 100 and 2000/);
  });
  it('treats blank as valid', () => {
    expect(validateGeoRadius('')).toBeNull();
    expect(validateGeoRadius(null)).toBeNull();
    expect(validateGeoRadius(undefined)).toBeNull();
  });
});

describe('firstError', () => {
  it('returns the first non-null error in order', () => {
    expect(firstError(null, 'second bad', 'third bad')).toBe('second bad');
  });
  it('returns null when everything is valid', () => {
    expect(firstError(null, null, null)).toBeNull();
    expect(firstError()).toBeNull();
  });
});
