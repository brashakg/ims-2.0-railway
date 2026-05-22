// ============================================================================
// IMS 2.0 - Legal Entities API
// ============================================================================
// A legal entity (PAN) groups stores; one entity can hold multiple GSTINs.

import api from './client';

export interface GstinEntry {
  gstin: string;
  state_code: string;
  state_name?: string;
}

export interface PtRegistration {
  state_code: string;
  registration_number: string;
}

export interface Entity {
  entity_id: string;
  name: string;
  legal_name?: string;
  pan?: string;
  tan?: string;
  registered_address?: string;
  gstins?: GstinEntry[];
  pf?: { registered: boolean; establishment_code?: string };
  esi?: { registered: boolean; code?: string };
  pt_registrations?: PtRegistration[];
  bank_account_no?: string;
  bank_ifsc?: string;
  bank_name?: string;
  is_active?: boolean;
}

export const entitiesApi = {
  list: async (includeInactive = false) => {
    const res = await api.get('/entities', {
      params: { include_inactive: includeInactive },
    });
    return res.data as { entities: Entity[]; total: number };
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
