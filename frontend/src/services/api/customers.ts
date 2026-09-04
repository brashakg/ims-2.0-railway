// ============================================================================
// IMS 2.0 - Customer API
// ============================================================================

import api from './client';

// ── Family-member guard (owner ruling 2026-09-04: "block it outright") ──────
// Customer identity is mobile-primary. POST /customers REFUSES (409) a number
// that already belongs to a FAMILY MEMBER on someone else's account, so one
// person never ends up as two records. The 409 body is this shape; the
// AddCustomerModal turns it into the promote / open-existing popup.
export const FAMILY_MEMBER_CONFLICT_CODE = 'MOBILE_BELONGS_TO_FAMILY_MEMBER';

export interface FamilyMemberConflict {
  code: typeof FAMILY_MEMBER_CONFLICT_CODE;
  message: string;
  /** The account that holds the person as a family member. */
  customer_id: string;
  account_holder_name: string;
  patient_id: string;
  patient_name: string;
  relation?: string | null;
}

/** A customer a caller can put straight on a bill: the common subset of what
 *  the promote door returns and what GET /customers/{id} returns. */
export interface SelectableCustomer {
  customer_id: string;
  name: string;
  mobile?: string | null;
  phone?: string | null;
  customer_type?: string;
  patients: Array<{
    patient_id: string;
    name: string;
    mobile?: string | null;
    relation?: string | null;
    is_primary?: boolean;
  }>;
  primary_patient_id?: string | null;
}

/** What POST /customers/{id}/patients/{pid}/promote returns: the family member
 *  as their OWN top-level customer (same patient_id, Rx/eye tests carried). */
export interface PromotedCustomer extends SelectableCustomer {
  mobile: string;
  phone: string;
  promoted_from: { customer_id: string; patient_id: string; at: string };
  /** Records re-pointed at the new account, per collection. */
  carried: Record<string, number>;
}

// ── The REVERSE split (owner ruling 2026-09-04: "block it the same way") ────
// Adding a FAMILY MEMBER with a number that is already a top-level customer's
// own is refused (409) by every member-adding door: POST /customers with
// patients[], PUT /customers/{id} patients-append, POST /customers/{id}/patients.
// The body names the person's own account and the offending row; the popup
// offers to OPEN that account (one person, one record -- no copy is made).
export const OWN_ACCOUNT_CONFLICT_CODE = 'MOBILE_IS_OWN_ACCOUNT';

export interface OwnAccountConflict {
  code: typeof OWN_ACCOUNT_CONFLICT_CODE;
  message: string;
  /** The person's own account. */
  customer_id: string;
  customer_name: string;
  /** Position of the offending row in the submitted patients[] (0 for a single add). */
  patient_index: number;
  patient_name: string;
}

// ── ONE HOUSEHOLD (owner ruling 2026-09-04: "Block it, one household account") ─
// A number may be a family member on only ONE account. Every member-adding door
// refuses (409) a row whose number already sits in ANOTHER account's family;
// the body names that account and the member row. The popup offers to OPEN
// that household -- no promote, no link, no copy.
export const HOUSEHOLD_CONFLICT_CODE = 'MOBILE_ON_ANOTHER_HOUSEHOLD';

export interface HouseholdConflict {
  code: typeof HOUSEHOLD_CONFLICT_CODE;
  message: string;
  /** The account that already holds the person as a family member. */
  customer_id: string;
  account_holder_name: string;
  patient_id: string | null;
  patient_name: string;
  relation?: string | null;
  /** Position of the offending row in the submitted patients[] (0 for a single add). */
  patient_index: number;
}

/** Any refusal of the one-person-one-record rule, as a 409 body. */
export type CustomerConflict = FamilyMemberConflict | OwnAccountConflict | HouseholdConflict;

/** The structured `detail` of a rejected request, from the ApiError the client
 *  throws (`.detail`) or a raw axios error alike; undefined when not an object. */
function conflictDetailFrom(err: unknown): Record<string, unknown> | undefined {
  const e = err as { detail?: unknown; response?: { data?: { detail?: unknown } } } | null;
  const detail = e?.detail ?? e?.response?.data?.detail;
  return detail && typeof detail === 'object' ? (detail as Record<string, unknown>) : undefined;
}

/** Pull the family-member 409 out of a rejected create, or null. */
export function familyMemberConflictFrom(err: unknown): FamilyMemberConflict | null {
  const detail = conflictDetailFrom(err) as Partial<FamilyMemberConflict> | undefined;
  if (
    detail &&
    detail.code === FAMILY_MEMBER_CONFLICT_CODE &&
    typeof detail.customer_id === 'string' &&
    typeof detail.patient_id === 'string'
  ) {
    return detail as FamilyMemberConflict;
  }
  return null;
}

/** Pull the reverse-split 409 (a member row that is someone's own account) out
 *  of a rejected create / update / add-patient, or null. */
export function ownAccountConflictFrom(err: unknown): OwnAccountConflict | null {
  const detail = conflictDetailFrom(err) as Partial<OwnAccountConflict> | undefined;
  if (
    detail &&
    detail.code === OWN_ACCOUNT_CONFLICT_CODE &&
    typeof detail.customer_id === 'string' &&
    typeof detail.patient_name === 'string'
  ) {
    return detail as OwnAccountConflict;
  }
  return null;
}

/** Pull the one-household 409 (a member row already on another account's
 *  family) out of a rejected create / update / add-patient, or null. */
export function householdConflictFrom(err: unknown): HouseholdConflict | null {
  const detail = conflictDetailFrom(err) as Partial<HouseholdConflict> | undefined;
  if (
    detail &&
    detail.code === HOUSEHOLD_CONFLICT_CODE &&
    typeof detail.customer_id === 'string' &&
    typeof detail.patient_name === 'string'
  ) {
    return detail as HouseholdConflict;
  }
  return null;
}

export const customerApi = {
  getCustomers: async (params?: { search?: string; page?: number; pageSize?: number; storeId?: string; limit?: number; skip?: number; channel?: string; customer_type?: string; exclude_marketing?: boolean }) => {
    // Convert camelCase storeId → snake_case store_id for the FastAPI Query.
    // Pre-fix, this passed `storeId` through as-is and the backend silently
    // dropped it (FastAPI Query param name didn't match), so every "Pune"
    // store-switch on /customers still returned Bokaro's seed customers.
    // `channel` (ONLINE / STORE, unification step-4) passes through as-is to
    // segregate online-origin (Shopify) buyers from in-store customers.
    const { storeId, ...rest } = params ?? {};
    const apiParams = { ...rest, ...(storeId ? { store_id: storeId } : {}) };
    const response = await api.get('/customers', { params: apiParams });
    return response.data;
  },

  getCustomer: async (customerId: string) => {
    const response = await api.get(`/customers/${customerId}`);
    return response.data;
  },

  // Store-credit / credit-note ledger
  getStoreCreditLedger: async (customerId: string) => {
    const response = await api.get(`/customers/${customerId}/store-credit/ledger`);
    return response.data as {
      customer_id: string;
      balance: number;
      entries: Array<{
        entry_id: string; type: string; amount: number; delta: number;
        balance_after: number; reason?: string; ref?: string | null;
        created_by?: string | null; created_at?: string;
      }>;
    };
  },
  issueStoreCredit: async (customerId: string, amount: number, reason?: string, ref?: string) => {
    const response = await api.post(`/customers/${customerId}/store-credit/issue`, { amount, reason, ref });
    return response.data;
  },
  redeemStoreCredit: async (customerId: string, amount: number, reason?: string, ref?: string) => {
    const response = await api.post(`/customers/${customerId}/store-credit/redeem`, { amount, reason, ref });
    return response.data;
  },

  createCustomer: async (data: Partial<import('../../types').Customer>) => {
    const response = await api.post('/customers', data);
    return response.data;
  },

  updateCustomer: async (customerId: string, data: Partial<import('../../types').Customer>) => {
    const response = await api.put(`/customers/${customerId}`, data);
    return response.data;
  },

  searchByPhone: async (phone: string) => {
    const response = await api.get('/customers/search/phone', { params: { phone } });
    return response.data;
  },

  addPatient: async (customerId: string, patient: Partial<import('../../types').Patient>) => {
    const response = await api.post(`/customers/${customerId}/patients`, patient);
    return response.data;
  },

  // Promote a family member OUT of `customerId` into their own top-level
  // account (the counterpart of the family-member 409 on createCustomer).
  promotePatient: async (customerId: string, patientId: string): Promise<PromotedCustomer> => {
    const response = await api.post(`/customers/${customerId}/patients/${patientId}/promote`);
    return response.data as PromotedCustomer;
  },

  // DPDP data-consent wording (editable under Marketing). The add-customer form
  // fetches this to show the customer the exact text they're agreeing to, and
  // stamps the returned `version` onto their stored consent.
  getConsentText: async (): Promise<{ text: string; version: string; updated_at: string | null }> => {
    const response = await api.get('/marketing/consent-text');
    return response.data;
  },
  // ADMIN-only: edit the consent wording (bumps the version).
  updateConsentText: async (text: string) => {
    const response = await api.put('/marketing/consent-text', { text });
    return response.data;
  },

  // POS-4: khata / credit-limit summary
  getCreditSummary: async (customerId: string): Promise<{
    customer_id: string;
    credit_limit: number;
    ar_outstanding: number;
    ar_available: number | null;
    limit_exceeded: boolean;
  }> => {
    const response = await api.get(`/customers/${customerId}/credit-summary`);
    return response.data;
  },
};

// Named alias used by CreditBillingOption (and future callers) — matches the
// barrel export name pattern used by the rest of the services layer.
export const customersApi = customerApi;
