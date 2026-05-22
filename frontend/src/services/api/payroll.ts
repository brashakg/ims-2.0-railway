// ============================================================================
// IMS 2.0 - Payroll Config API (Structured CTC salary master + PT slabs)
// ============================================================================

import api from './client';

export interface OtherAllowance {
  name: string;
  amount: number;
}

export interface SalaryConfig {
  config_id?: string;
  employee_id: string;
  entity_id?: string | null;
  store_id?: string | null;
  designation?: string;
  department?: string;
  date_of_joining?: string;
  // Earnings (monthly)
  basic: number;
  hra?: number;
  conveyance?: number;
  medical?: number;
  special_allowance?: number;
  other_allowances?: OtherAllowance[];
  // Statutory
  pf_applicable?: boolean;
  pf_wage_ceiling_cap?: boolean;
  esi_applicable?: boolean | null;
  pt_applicable?: boolean;
  tds_monthly?: number;
  // IDs
  uan?: string;
  esi_ip_number?: string;
  pan?: string;
  // Bank
  bank_account_no?: string;
  bank_ifsc?: string;
  bank_name?: string;
  is_active?: boolean;
}

export interface PtSlab {
  state_code: string;
  state_name?: string;
  basis: string;
  gender_aware?: boolean;
  slabs: Array<Record<string, unknown>>;
  notes?: string;
}

/** Sum of all monthly earning components on a config. */
export function grossOf(c: Partial<SalaryConfig>): number {
  const others = (c.other_allowances || []).reduce((s, a) => s + (a.amount || 0), 0);
  return (
    (c.basic || 0) +
    (c.hra || 0) +
    (c.conveyance || 0) +
    (c.medical || 0) +
    (c.special_allowance || 0) +
    others
  );
}

export const payrollApi = {
  listConfigs: async (params?: {
    store_id?: string;
    entity_id?: string;
    include_inactive?: boolean;
  }) => {
    const res = await api.get('/payroll/config', { params });
    return res.data as { configs: SalaryConfig[]; total: number };
  },

  getConfig: async (employeeId: string) => {
    const res = await api.get(`/payroll/config/${employeeId}`);
    return res.data as { config: SalaryConfig | Record<string, never> };
  },

  createConfig: async (payload: SalaryConfig) => {
    const res = await api.post('/payroll/config', payload);
    return res.data as { status: string; config_id: string };
  },

  updateConfig: async (employeeId: string, payload: Partial<SalaryConfig>) => {
    const res = await api.put(`/payroll/config/${employeeId}`, payload);
    return res.data as { status: string; config?: SalaryConfig };
  },

  bulkConfigs: async (configs: SalaryConfig[]) => {
    const res = await api.post('/payroll/config/bulk', { configs });
    return res.data as { created: number; updated: number; total: number };
  },

  listPtSlabs: async () => {
    const res = await api.get('/payroll/pt-slabs');
    return res.data as { pt_slabs: PtSlab[]; total: number; source: string };
  },

  seedPtSlabs: async () => {
    const res = await api.post('/payroll/pt-slabs/seed');
    return res.data as { status: string; seeded: number; states: string[] };
  },
};
