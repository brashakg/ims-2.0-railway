// ============================================================================
// IMS 2.0 - one-household client contract (owner ruling 2026-09-04)
// ============================================================================
// Every member-adding door (POST /customers with patients[], PUT /customers/{id}
// patients-append, POST /customers/{id}/patients) refuses a row whose number is
// already a family member on ANOTHER account with the same 409 body. That body
// must be recognisable through the REAL client error transform (buildApiError),
// and the three direction readers must never fire on each other's body.

import { describe, it, expect } from 'vitest';
import type { AxiosError } from 'axios';

import { buildApiError } from '../client';
import {
  familyMemberConflictFrom,
  householdConflictFrom,
  ownAccountConflictFrom,
  FAMILY_MEMBER_CONFLICT_CODE,
  HOUSEHOLD_CONFLICT_CODE,
  OWN_ACCOUNT_CONFLICT_CODE,
} from '../customers';

const axiosErr = (status: number, data: unknown) =>
  ({ message: `Request failed with status code ${status}`, response: { status, data } }) as unknown as AxiosError<{
    detail?: string;
  }>;

const HOUSEHOLD = {
  code: HOUSEHOLD_CONFLICT_CODE,
  message: "This number is already Riya Devi, a family member on Meena Devi's account",
  customer_id: 'cust-holder',
  account_holder_name: 'Meena Devi',
  patient_id: 'pat-daughter',
  patient_name: 'Riya Devi',
  relation: 'Daughter',
  patient_index: 1,
};

const OWN = {
  code: OWN_ACCOUNT_CONFLICT_CODE,
  message: "This number is already Arun Kumar's own account",
  customer_id: 'cust-own',
  customer_name: 'Arun Kumar',
  patient_index: 0,
  patient_name: 'Arun',
};

const FAMILY = {
  code: FAMILY_MEMBER_CONFLICT_CODE,
  message: "This number belongs to Riya Devi, a family member on Meena Devi's account",
  customer_id: 'cust-holder',
  account_holder_name: 'Meena Devi',
  patient_id: 'pat-daughter',
  patient_name: 'Riya Devi',
  relation: 'Daughter',
};

describe('householdConflictFrom', () => {
  it('reads the one-household 409 off the error the client actually delivers', () => {
    expect(householdConflictFrom(buildApiError(axiosErr(409, { detail: HOUSEHOLD })))).toEqual(HOUSEHOLD);
  });

  it('also reads a raw axios rejection (a caller that bypassed the interceptor)', () => {
    expect(householdConflictFrom(axiosErr(409, { detail: HOUSEHOLD }))).toEqual(HOUSEHOLD);
  });

  it('three bodies, three readers, no cross-fire', () => {
    const household = buildApiError(axiosErr(409, { detail: HOUSEHOLD }));
    const own = buildApiError(axiosErr(409, { detail: OWN }));
    const family = buildApiError(axiosErr(409, { detail: FAMILY }));
    expect(householdConflictFrom(own)).toBeNull();
    expect(householdConflictFrom(family)).toBeNull();
    expect(ownAccountConflictFrom(household)).toBeNull();
    expect(familyMemberConflictFrom(household)).toBeNull();
    expect(householdConflictFrom(household)).toEqual(HOUSEHOLD);
  });

  it('is null for the plain duplicate-mobile 409 (a string detail) and a non-API failure', () => {
    expect(
      householdConflictFrom(buildApiError(axiosErr(409, { detail: 'Customer with this mobile already exists' }))),
    ).toBeNull();
    expect(householdConflictFrom(new Error('network'))).toBeNull();
    expect(householdConflictFrom(undefined)).toBeNull();
  });

  it('is null when the body carries the code but not the fields the popup needs', () => {
    expect(
      householdConflictFrom(buildApiError(axiosErr(409, { detail: { code: HOUSEHOLD_CONFLICT_CODE, message: 'm' } }))),
    ).toBeNull();
  });
});
