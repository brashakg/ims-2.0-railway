import { describe, it, expect } from 'vitest';
import {
  customerToPatientFields,
  hasCopyableCustomerFields,
  applyCustomerToPatient,
  copyWouldOverwrite,
} from '../patientFromCustomer';

describe('customerToPatientFields', () => {
  it('extracts overlapping fields from a camelCase form-shape customer', () => {
    const fields = customerToPatientFields({
      fullName: 'Asha Verma',
      mobileNumber: '9810000001',
      email: 'asha@example.com',
      dateOfBirth: '1990-04-01',
    });
    expect(fields).toEqual({
      name: 'Asha Verma',
      mobile: '9810000001',
      email: 'asha@example.com',
      dateOfBirth: '1990-04-01',
    });
  });

  it('reads snake_case / api-shape keys (name, phone, dob)', () => {
    const fields = customerToPatientFields({
      name: 'Ravi Kumar',
      phone: '9899999999',
      dob: '1985-12-20',
    });
    expect(fields).toEqual({ name: 'Ravi Kumar', mobile: '9899999999', dateOfBirth: '1985-12-20' });
  });

  it('omits empty/whitespace fields and copies gender when present', () => {
    const fields = customerToPatientFields({ name: '  ', mobile: '9810000002', gender: 'FEMALE' });
    expect(fields).toEqual({ mobile: '9810000002', gender: 'FEMALE' });
    expect(fields.name).toBeUndefined();
  });

  it('returns {} for null/undefined', () => {
    expect(customerToPatientFields(null)).toEqual({});
    expect(customerToPatientFields(undefined)).toEqual({});
  });
});

describe('hasCopyableCustomerFields', () => {
  it('is true when at least one field is copyable', () => {
    expect(hasCopyableCustomerFields({ name: 'X' })).toBe(true);
  });
  it('is false for an empty / null customer', () => {
    expect(hasCopyableCustomerFields({})).toBe(false);
    expect(hasCopyableCustomerFields(null)).toBe(false);
  });
});

describe('applyCustomerToPatient (non-destructive fill)', () => {
  const customer = { name: 'Asha Verma', mobile: '9810000001', dateOfBirth: '1990-04-01' };

  it('fills only blank fields by default, preserving typed values', () => {
    const current = { name: 'Typed Name', mobile: '', dateOfBirth: '', relation: 'Self' };
    const next = applyCustomerToPatient(current, customer);
    expect(next).toEqual({
      name: 'Typed Name', // preserved
      mobile: '9810000001', // filled
      dateOfBirth: '1990-04-01', // filled
      relation: 'Self', // untouched extra key
    });
  });

  it('does not mutate the input object', () => {
    const current = { name: '', mobile: '', dateOfBirth: '', relation: 'Self' };
    const next = applyCustomerToPatient(current, customer);
    expect(current.name).toBe('');
    expect(next).not.toBe(current);
  });

  it('overwrites every field when onlyFillBlanks=false', () => {
    const current = { name: 'Typed Name', mobile: '0000000000', dateOfBirth: '2000-01-01', relation: 'Self' };
    const next = applyCustomerToPatient(current, customer, false);
    expect(next.name).toBe('Asha Verma');
    expect(next.mobile).toBe('9810000001');
    expect(next.dateOfBirth).toBe('1990-04-01');
  });
});

describe('copyWouldOverwrite', () => {
  const customer = { name: 'Asha Verma', mobile: '9810000001' };

  it('is false when patient fields are blank (pure fill)', () => {
    expect(copyWouldOverwrite({ name: '', mobile: '' }, customer)).toBe(false);
  });

  it('is true when a typed field differs from the customer value', () => {
    expect(copyWouldOverwrite({ name: 'Someone Else', mobile: '' }, customer)).toBe(true);
  });

  it('is false when a typed field already equals the customer value', () => {
    expect(copyWouldOverwrite({ name: 'Asha Verma', mobile: '9810000001' }, customer)).toBe(false);
  });
});
