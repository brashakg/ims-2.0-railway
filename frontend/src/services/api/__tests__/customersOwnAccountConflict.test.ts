// ============================================================================
// IMS 2.0 - reverse-split client contract (owner ruling 2026-09-04)
// ============================================================================
// Every member-adding door (POST /customers with patients[], PUT /customers/{id}
// patients-append, POST /customers/{id}/patients) refuses a row whose number is
// already someone's OWN account with the same 409 body. That body must be
// recognisable through the REAL client error transform (buildApiError), and the
// two direction readers must never both fire on one body.

import { describe, it, expect } from 'vitest';
import type { AxiosError } from 'axios';

import { buildApiError } from '../client';
import {
  familyMemberConflictFrom,
  ownAccountConflictFrom,
  FAMILY_MEMBER_CONFLICT_CODE,
  OWN_ACCOUNT_CONFLICT_CODE,
} from '../customers';

const axiosErr = (status: number, data: unknown) =>
  ({ message: `Request failed with status code ${status}`, response: { status, data } }) as unknown as AxiosError<{
    detail?: string;
  }>;

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

describe('ownAccountConflictFrom', () => {
  it('reads the reverse-split 409 off the error the client actually delivers', () => {
    const err = buildApiError(axiosErr(409, { detail: OWN }));
    expect(ownAccountConflictFrom(err)).toEqual(OWN);
  });

  it('also reads a raw axios rejection (a caller that bypassed the interceptor)', () => {
    expect(ownAccountConflictFrom(axiosErr(409, { detail: OWN }))).toEqual(OWN);
  });

  it('is null for the forward family-member 409, and vice versa (one body, one reader)', () => {
    const own = buildApiError(axiosErr(409, { detail: OWN }));
    const family = buildApiError(axiosErr(409, { detail: FAMILY }));
    expect(ownAccountConflictFrom(family)).toBeNull();
    expect(familyMemberConflictFrom(own)).toBeNull();
    expect(familyMemberConflictFrom(family)).toEqual(FAMILY);
  });

  it('is null for the plain duplicate-mobile 409 (a string detail) and a non-API failure', () => {
    expect(
      ownAccountConflictFrom(buildApiError(axiosErr(409, { detail: 'Customer with this mobile already exists' }))),
    ).toBeNull();
    expect(ownAccountConflictFrom(new Error('network'))).toBeNull();
    expect(ownAccountConflictFrom(undefined)).toBeNull();
  });

  it('is null when the body carries the code but not the fields the popup needs', () => {
    const err = buildApiError(axiosErr(409, { detail: { code: OWN_ACCOUNT_CONFLICT_CODE, message: 'm' } }));
    expect(ownAccountConflictFrom(err)).toBeNull();
  });
});
