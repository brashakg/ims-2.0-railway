// ============================================================================
// IMS 2.0 - family-member guard client contract
// ============================================================================
// The 409 POST /customers returns when a number already belongs to a FAMILY
// MEMBER on another account must be recognisable through the REAL client error
// transform (buildApiError), and the promote door must be called at its exact
// path. These lock the FE half of the guard so the popup cannot silently stop
// rendering because a payload shape drifted.

import { vi, beforeEach, describe, it, expect } from 'vitest';
import type { AxiosError } from 'axios';

vi.mock('../client', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../client')>();
  return {
    ...orig,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

import api from '../client';
import { buildApiError } from '../client';
import {
  customerApi,
  familyMemberConflictFrom,
  FAMILY_MEMBER_CONFLICT_CODE,
} from '../customers';

const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

const axiosErr = (status: number, data: unknown) =>
  ({ message: `Request failed with status code ${status}`, response: { status, data } }) as unknown as AxiosError<{
    detail?: string;
  }>;

const CONFLICT = {
  code: FAMILY_MEMBER_CONFLICT_CODE,
  message: "This number belongs to Riya Devi, a family member on Meena Devi's account",
  customer_id: 'cust-holder',
  account_holder_name: 'Meena Devi',
  patient_id: 'pat-daughter',
  patient_name: 'Riya Devi',
  relation: 'Daughter',
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('familyMemberConflictFrom', () => {
  it('reads the family-member 409 off the error the client actually delivers', () => {
    const err = buildApiError(axiosErr(409, { detail: CONFLICT }));
    expect(familyMemberConflictFrom(err)).toEqual(CONFLICT);
  });

  it('is null for the plain duplicate-mobile 409 (a string detail)', () => {
    const err = buildApiError(axiosErr(409, { detail: 'Customer with this mobile already exists' }));
    expect(familyMemberConflictFrom(err)).toBeNull();
  });

  it('is null for a structured 409 with a different code', () => {
    const err = buildApiError(
      axiosErr(409, { detail: { code: 'MOBILE_ALREADY_OWN_ACCOUNT', customer_id: 'x', patient_id: 'y', message: 'm' } }),
    );
    expect(familyMemberConflictFrom(err)).toBeNull();
  });

  it('is null for a non-API failure', () => {
    expect(familyMemberConflictFrom(new Error('network'))).toBeNull();
    expect(familyMemberConflictFrom(undefined)).toBeNull();
  });
});

describe('customerApi.promotePatient', () => {
  it('POSTs to /customers/{id}/patients/{pid}/promote and returns the promoted customer', async () => {
    const promoted = {
      customer_id: 'cust-new',
      name: 'Riya Devi',
      mobile: '9876500002',
      phone: '9876500002',
      patients: [{ patient_id: 'pat-daughter', name: 'Riya Devi', is_primary: true }],
      primary_patient_id: 'pat-daughter',
      promoted_from: { customer_id: 'cust-holder', patient_id: 'pat-daughter', at: 't' },
      carried: { prescriptions: 2 },
    };
    mockPost.mockResolvedValue({ data: promoted });
    const res = await customerApi.promotePatient('cust-holder', 'pat-daughter');
    expect(mockPost).toHaveBeenCalledWith('/customers/cust-holder/patients/pat-daughter/promote');
    expect(res.customer_id).toBe('cust-new');
    expect(res.carried.prescriptions).toBe(2);
  });
});
