// ============================================================================
// IMS 2.0 - Legal Entities API
// ============================================================================
// A legal entity (PAN) groups stores; one entity can hold multiple GSTINs.

import api from './client';

export interface GstinEntry {
  gstin: string;
  state_code: string;
  state_name?: string;
  registration_type?: string;
  is_primary?: boolean;
}

export interface PtRegistration {
  state_code: string;
  registration_number: string;
}

export interface BankAccount {
  label?: string;
  account_no: string;
  ifsc: string;
  bank_name?: string;
  branch?: string;
  account_type?: string;
  gstin?: string;
  upi_vpa?: string;
  is_default?: boolean;
}

export interface InvoiceIdentity {
  legal_display_name?: string;
  logo_url?: string;
  header_lines?: string[];
  footer_text?: string;
  terms?: string;
  signatory_name?: string;
  signatory_designation?: string;
}

export interface EntityDocument {
  doc_type: string;
  name: string;
  url: string;
  uploaded_at?: string;
}

export interface Entity {
  entity_id: string;
  name: string;
  legal_name?: string;
  entity_type?: string;
  pan?: string;
  tan?: string;
  cin?: string;
  llpin?: string;
  udyam?: string;
  incorporation_date?: string;
  website?: string;
  registered_address?: string;
  registered_email?: string;
  registered_phone?: string;
  gstins?: GstinEntry[];
  pf?: { registered: boolean; establishment_code?: string };
  esi?: { registered: boolean; code?: string };
  pt_registrations?: PtRegistration[];
  bank_accounts?: BankAccount[];
  invoice?: InvoiceIdentity;
  documents?: EntityDocument[];
  bank_account_no?: string;
  bank_ifsc?: string;
  bank_name?: string;
  is_active?: boolean;
}

export interface OrgMeta {
  state_codes: Array<{ code: string; name: string }>;
  entity_types: string[];
}

export const entitiesApi = {
  list: async (includeInactive = false) => {
    const res = await api.get('/entities', {
      params: { include_inactive: includeInactive },
    });
    return res.data as { entities: Entity[]; total: number };
  },

  meta: async () => {
    const res = await api.get('/entities/meta/options');
    return res.data as OrgMeta;
  },

  get: async (entityId: string) => {
    const res = await api.get(`/entities/${entityId}`);
    return res.data as { entity: Entity };
  },

  create: async (payload: Partial<Entity>) => {
    const res = await api.post('/entities', payload);
    return res.data as { status: string; entity: Entity };
  },

  update: async (entityId: string, payload: Partial<Entity>) => {
    const res = await api.put(`/entities/${entityId}`, payload);
    return res.data as { status: string; entity: Entity };
  },

  listStores: async (entityId: string) => {
    const res = await api.get(`/entities/${entityId}/stores`);
    return res.data as { stores: Array<Record<string, unknown>>; total: number };
  },

  assignStore: async (entityId: string, storeId: string) => {
    const res = await api.post(`/entities/${entityId}/stores/${storeId}`);
    return res.data;
  },

  unassignStore: async (entityId: string, storeId: string) => {
    const res = await api.delete(`/entities/${entityId}/stores/${storeId}`);
    return res.data;
  },
};
