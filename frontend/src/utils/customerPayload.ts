// ============================================================================
// IMS 2.0 - Shared customer-create form model + payload builder
// ============================================================================
// ONE source of truth for the shape of the customer-identity form and for the
// backend CustomerCreate payload it maps to. Every door that creates a customer
// (POS "Add Customer", the Customers page, and the Clinical "New Patient
// Intake") imports the SAME `CustomerFormData` type and calls the SAME
// `buildCustomerCreatePayload` so the two doors can never drift into producing
// different customer records again.
//
// The canonical backend contract lives in
//   backend/api/routers/customers.py :: CustomerCreate
// and is:
//   customer_type (B2C/B2B), name, mobile, email, dob, anniversary, gstin,
//   billing_address{address,city,state,pincode}, marketing_consent (default
//   true), data_consent (default true), data_consent_text_version,
//   patients:[{name, mobile, dob, relation}]   (PatientCreate has NO email; it
//   uses `dob`, not `dateOfBirth`).
//
// Do NOT change the backend or the API client signatures — this file only maps
// the camelCase form state onto those canonical snake_case keys.

// ---------------------------------------------------------------------------
// Form model (camelCase — how the UI holds the in-progress customer)
// ---------------------------------------------------------------------------

export interface PatientFormData {
  id: string;
  name: string;
  mobile: string;
  email: string;
  dateOfBirth: string;
  relation: string;
}

export interface CustomerFormData {
  customerType: 'B2C' | 'B2B';
  // Basic Info
  fullName: string;
  mobileNumber: string;
  email: string;
  dateOfBirth: string;
  anniversary: string;
  // Address
  address: string;
  pincode: string;
  city: string;
  state: string;
  // B2B specific
  gstNumber: string;
  businessName: string;
  panNumber: string;
  // Patients
  patients: PatientFormData[];
  // Marketing consent — opt-in default, operator flips off only on
  // explicit decline. Drives birthday / Rx-expiry / WhatsApp campaigns.
  marketingConsent: boolean;
  // DPDP Act 2023 data-storage consent — separate from marketing. Default on;
  // the operator ticks it after telling the customer. Carries the version of
  // the wording shown so the agreement is provable.
  dataConsent: boolean;
  dataConsentTextVersion?: string;
}

/** A fresh, empty customer form. Both doors seed their state from this so the
 *  default field set (incl. consents defaulting on) is identical. */
export function emptyCustomerFormData(): CustomerFormData {
  return {
    customerType: 'B2C',
    fullName: '',
    mobileNumber: '',
    email: '',
    dateOfBirth: '',
    anniversary: '',
    address: '',
    pincode: '',
    city: '',
    state: '',
    gstNumber: '',
    businessName: '',
    panNumber: '',
    patients: [],
    marketingConsent: true,
    dataConsent: true,
  };
}

// ---------------------------------------------------------------------------
// Backend payload (snake_case — exactly the CustomerCreate contract)
// ---------------------------------------------------------------------------

export interface CustomerCreatePatient {
  name: string;
  mobile?: string;
  dob?: string;
  relation: string;
}

export interface CustomerCreatePayload {
  name: string;
  mobile: string;
  email?: string;
  customer_type: 'B2C' | 'B2B';
  dob?: string;
  anniversary?: string;
  gstin?: string;
  billing_address?: {
    address: string;
    city: string;
    state: string;
    pincode: string;
  };
  patients: CustomerCreatePatient[];
  marketing_consent: boolean;
  data_consent: boolean;
  data_consent_text_version?: string;
}

/** Bare 10-digit form. The backend requires exactly 10 digits (^[6-9]\d{9}$),
 *  so strip any +91 / 0 / spaces / dashes the operator pasted and keep the last
 *  10 digits. A blank stays blank (backend then rejects — mobile is required). */
function tenDigits(v: string | undefined | null): string {
  return (v || '').replace(/\D/g, '').slice(-10);
}

function blankToUndef(v: string | undefined | null): string | undefined {
  const t = (v || '').trim();
  return t === '' ? undefined : t;
}

/**
 * Map a filled customer form onto the canonical CustomerCreate payload.
 *
 * This is the SINGLE mapping used by POS, the Customers page, and the Clinical
 * intake door. Rules baked in here (so no caller can diverge):
 *  - `fullName` -> `name`, `mobileNumber` -> `mobile` (normalised to 10 digits)
 *  - customer `dateOfBirth` -> `dob`; `anniversary` passed through (both dropped
 *    when blank)
 *  - `gstin` is included ONLY for a B2B customer (never leaks a stray GST on a
 *    B2C record)
 *  - `billing_address` is included ONLY when at least one address field is set
 *  - each patient `dateOfBirth` -> `dob`; patient mobile normalised; PatientCreate
 *    carries NO email
 *  - marketing + DPDP data consents (+ the wording version) always sent
 */
export function buildCustomerCreatePayload(formData: CustomerFormData): CustomerCreatePayload {
  const hasAddress = !!(formData.address || formData.city || formData.pincode || formData.state);

  const payload: CustomerCreatePayload = {
    name: (formData.fullName || '').trim(),
    mobile: tenDigits(formData.mobileNumber),
    email: blankToUndef(formData.email),
    customer_type: formData.customerType,
    dob: blankToUndef(formData.dateOfBirth),
    anniversary: blankToUndef(formData.anniversary),
    gstin: formData.customerType === 'B2B' ? blankToUndef(formData.gstNumber) : undefined,
    billing_address: hasAddress
      ? {
          address: formData.address,
          city: formData.city,
          state: formData.state,
          pincode: formData.pincode,
        }
      : undefined,
    patients: (formData.patients || []).map((p) => ({
      name: p.name,
      // Patient phone is optional; normalise to 10 digits when present.
      mobile: p.mobile ? tenDigits(p.mobile) || undefined : undefined,
      dob: blankToUndef(p.dateOfBirth),
      relation: p.relation || 'Self',
    })),
    marketing_consent: formData.marketingConsent,
    data_consent: formData.dataConsent,
    data_consent_text_version: formData.dataConsentTextVersion,
  };

  return payload;
}
