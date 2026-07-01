// ============================================================================
// IMS 2.0 - Customer API
// ============================================================================

import api from './client';

export const customerApi = {
  getCustomers: async (params?: { search?: string; page?: number; pageSize?: number; storeId?: string; limit?: number; skip?: number; channel?: string; customer_type?: string }) => {
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
