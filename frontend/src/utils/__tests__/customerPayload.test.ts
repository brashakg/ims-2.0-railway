import { describe, it, expect } from 'vitest';
import {
  buildCustomerCreatePayload,
  emptyCustomerFormData,
  type CustomerFormData,
} from '../customerPayload';

// A fully-filled B2B form with patients + consent, so the test locks in the
// canonical mapping the POS door and the Clinical door BOTH depend on. If either
// door tries to diverge, this contract test is the tripwire.
function fullB2BForm(): CustomerFormData {
  return {
    ...emptyCustomerFormData(),
    customerType: 'B2B',
    fullName: '  Asha Verma  ',
    // Deliberately messy: +91, spaces, dashes — must normalise to bare 10 digits.
    mobileNumber: '+91 98100-00001',
    email: 'asha@example.com',
    dateOfBirth: '1990-04-01',
    anniversary: '2015-11-20',
    address: '12 MG Road',
    city: 'Mumbai',
    state: 'Maharashtra',
    pincode: '400001',
    gstNumber: '27AABCU9603R1ZM',
    businessName: 'Verma Opticals',
    panNumber: 'AABCU9603R',
    marketingConsent: false,
    dataConsent: true,
    dataConsentTextVersion: 'v3',
    patients: [
      {
        id: 'p1',
        name: 'Asha Verma',
        mobile: '+91 98100-00001',
        email: 'asha@example.com', // must NOT leak into the payload (PatientCreate has no email)
        dateOfBirth: '1990-04-01',
        relation: 'Self',
      },
      {
        id: 'p2',
        name: 'Rohit Verma',
        mobile: '',
        email: '',
        dateOfBirth: '2012-06-15',
        relation: 'Son',
      },
    ],
  };
}

describe('buildCustomerCreatePayload', () => {
  it('maps a full B2B form to the canonical CustomerCreate keys', () => {
    const payload = buildCustomerCreatePayload(fullB2BForm());

    expect(payload).toEqual({
      name: 'Asha Verma',
      mobile: '9810000001',
      email: 'asha@example.com',
      customer_type: 'B2B',
      // camelCase dateOfBirth -> canonical `dob`, never `dateOfBirth`.
      dob: '1990-04-01',
      anniversary: '2015-11-20',
      gstin: '27AABCU9603R1ZM',
      billing_address: {
        address: '12 MG Road',
        city: 'Mumbai',
        state: 'Maharashtra',
        pincode: '400001',
      },
      patients: [
        { name: 'Asha Verma', mobile: '9810000001', dob: '1990-04-01', relation: 'Self' },
        { name: 'Rohit Verma', mobile: undefined, dob: '2012-06-15', relation: 'Son' },
      ],
      marketing_consent: false,
      data_consent: true,
      data_consent_text_version: 'v3',
    });
  });

  it('never emits a dateOfBirth key on the customer or on any patient', () => {
    const payload = buildCustomerCreatePayload(fullB2BForm());
    expect(payload).not.toHaveProperty('dateOfBirth');
    for (const p of payload.patients) {
      expect(p).not.toHaveProperty('dateOfBirth');
      expect(p).not.toHaveProperty('email'); // PatientCreate has no email
    }
  });

  it('includes gstin ONLY for a B2B customer', () => {
    const b2c = { ...fullB2BForm(), customerType: 'B2C' as const };
    const payload = buildCustomerCreatePayload(b2c);
    expect(payload.customer_type).toBe('B2C');
    expect(payload.gstin).toBeUndefined();
  });

  it('omits billing_address when no address field is set', () => {
    const form: CustomerFormData = {
      ...emptyCustomerFormData(),
      fullName: 'Walk In',
      mobileNumber: '9820000002',
    };
    const payload = buildCustomerCreatePayload(form);
    expect(payload.billing_address).toBeUndefined();
  });

  it('emits a billing_address object when any single address field is set', () => {
    const form: CustomerFormData = {
      ...emptyCustomerFormData(),
      fullName: 'City Only',
      mobileNumber: '9820000003',
      city: 'Ranchi',
    };
    const payload = buildCustomerCreatePayload(form);
    expect(payload.billing_address).toEqual({
      address: '',
      city: 'Ranchi',
      state: '',
      pincode: '',
    });
  });

  it('defaults both consents on for a fresh form', () => {
    const payload = buildCustomerCreatePayload({
      ...emptyCustomerFormData(),
      fullName: 'Fresh Person',
      mobileNumber: '9820000004',
    });
    expect(payload.marketing_consent).toBe(true);
    expect(payload.data_consent).toBe(true);
  });

  it('normalises the mobile to the bare last-10 digits', () => {
    const payload = buildCustomerCreatePayload({
      ...emptyCustomerFormData(),
      fullName: 'Padded',
      mobileNumber: '0091-99999-00000',
    });
    expect(payload.mobile).toBe('9999900000');
  });
});
