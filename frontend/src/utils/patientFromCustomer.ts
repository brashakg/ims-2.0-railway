// ============================================================================
// IMS 2.0 - Copy customer details into a patient ("Self" / account holder)
// ============================================================================
// When a customer's family member (patient) IS the account holder — relation
// "Self" — re-typing the customer's name / mobile / DOB / email is wasteful and
// error-prone. This tiny shared helper maps the parent CUSTOMER's overlapping
// fields onto a patient form so the operator can one-tap copy them.
//
// Used by:
//   - components/customers/AddCustomerModal.tsx (inline "Add Patient" form)
//   - pages/customers/CustomersPage.tsx        (Customer 360 "Add Patient" modal)
//
// SAFETY: the copy is non-destructive by default — `onlyFillBlanks` keeps any
// value the operator already typed and fills only the empty patient fields. The
// caller decides whether to confirm before an overwrite.

/** Loosely-typed view of the parent customer as it exists in the add/edit
 *  forms. Customer objects come from a few shapes (form state, API row,
 *  selected record) so we read both camelCase and snake_case keys. */
export interface CustomerLike {
  // name
  name?: string;
  full_name?: string;
  fullName?: string;
  // mobile / phone
  mobile?: string;
  phone?: string;
  mobileNumber?: string;
  // email
  email?: string;
  // date of birth
  dateOfBirth?: string;
  date_of_birth?: string;
  dob?: string;
  // gender (not always present on a customer, but copied when it is)
  gender?: string;
}

/** The overlapping patient fields we can copy from a customer. All optional —
 *  only the keys the customer actually has are returned. */
export interface PatientCopyFields {
  name?: string;
  mobile?: string;
  email?: string;
  dateOfBirth?: string;
  gender?: string;
}

const firstNonEmpty = (...vals: Array<string | undefined | null>): string => {
  for (const v of vals) {
    if (typeof v === 'string' && v.trim() !== '') return v.trim();
  }
  return '';
};

/** Extract the patient-copyable fields from a customer object, tolerant of
 *  camelCase / snake_case field naming across the different call sites. */
export function customerToPatientFields(customer: CustomerLike | null | undefined): PatientCopyFields {
  if (!customer) return {};
  const out: PatientCopyFields = {};
  const name = firstNonEmpty(customer.name, customer.full_name, customer.fullName);
  const mobile = firstNonEmpty(customer.mobile, customer.phone, customer.mobileNumber);
  const email = firstNonEmpty(customer.email);
  const dateOfBirth = firstNonEmpty(customer.dateOfBirth, customer.date_of_birth, customer.dob);
  const gender = firstNonEmpty(customer.gender);
  if (name) out.name = name;
  if (mobile) out.mobile = mobile;
  if (email) out.email = email;
  if (dateOfBirth) out.dateOfBirth = dateOfBirth;
  if (gender) out.gender = gender;
  return out;
}

/** True when the customer has at least one field worth copying. Lets the UI
 *  disable / hide the "Copy from customer" affordance when there's nothing to
 *  copy yet (e.g. a brand-new customer whose own name isn't typed). */
export function hasCopyableCustomerFields(customer: CustomerLike | null | undefined): boolean {
  return Object.keys(customerToPatientFields(customer)).length > 0;
}

/** Merge customer fields onto an existing patient form value.
 *
 *  @param current  the patient form's current values (any shape with the
 *                  overlapping keys; extra keys are preserved untouched).
 *  @param customer the parent customer to copy from.
 *  @param onlyFillBlanks  when true (default), keep any value already present
 *                  on `current` and only fill empty fields — non-destructive.
 *                  When false, customer values overwrite.
 *  @returns a NEW object — never mutates `current`.
 */
export function applyCustomerToPatient<T extends PatientCopyFields>(
  current: T,
  customer: CustomerLike | null | undefined,
  onlyFillBlanks: boolean = true,
): T {
  const src = customerToPatientFields(customer);
  const next: T = { ...current };
  (Object.keys(src) as Array<keyof PatientCopyFields>).forEach((key) => {
    const incoming = src[key];
    if (!incoming) return;
    const existing = (current as PatientCopyFields)[key];
    const existingIsEmpty = !existing || (typeof existing === 'string' && existing.trim() === '');
    if (!onlyFillBlanks || existingIsEmpty) {
      (next as PatientCopyFields)[key] = incoming;
    }
  });
  return next;
}

/** Would applying the copy actually CHANGE any field the operator has already
 *  filled? Used to decide whether to ask for confirmation before overwriting.
 *  Returns true only when a non-empty patient field differs from the customer's
 *  value for that same field. */
export function copyWouldOverwrite<T extends PatientCopyFields>(
  current: T,
  customer: CustomerLike | null | undefined,
): boolean {
  const src = customerToPatientFields(customer);
  return (Object.keys(src) as Array<keyof PatientCopyFields>).some((key) => {
    const incoming = src[key];
    const existing = (current as PatientCopyFields)[key];
    const existingIsEmpty = !existing || (typeof existing === 'string' && existing.trim() === '');
    return !!incoming && !existingIsEmpty && existing !== incoming;
  });
}
