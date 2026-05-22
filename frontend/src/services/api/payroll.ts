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

  // --- Run flow (Phase 2 engine) -------------------------------------------
  runPayroll: async (req: PayrollRunRequest) => {
    const res = await api.post('/payroll/run', req);
    return res.data as PayrollRunResponse;
  },

  listRunRows: async (params: { month: number; year: number; store_id?: string; entity_id?: string }) => {
    const res = await api.get('/payroll/run/rows', { params });
    return res.data as { rows: PayrollRow[]; total: number; totals: PayrollTotals };
  },

  approveRun: async (req: PayrollBatchAction) => {
    const res = await api.post('/payroll/approve', req);
    return res.data as { status: string; approved: number };
  },

  lockRun: async (req: PayrollBatchAction) => {
    const res = await api.post('/payroll/lock', req);
    return res.data as { status: string; locked: number };
  },
};

// --- Run-flow types ---------------------------------------------------------

export interface PayrollRunRequest {
  month: number;
  year: number;
  store_id?: string;
  entity_id?: string;
  lwp_days?: Record<string, number>;
  incentives?: Record<string, number>;
  advances?: Record<string, number>;
  dry_run?: boolean;
}

export interface PayrollBatchAction {
  month: number;
  year: number;
  store_id?: string;
  entity_id?: string;
}

export interface PayrollBreakdown {
  earnings: {
    basic: number; hra: number; conveyance: number; medical: number;
    special_allowance: number; other_allowances: number;
    full_gross: number; earned_gross: number; incentive: number; total_earnings: number;
  };
  deductions: {
    pf_employee: number; esi_employee: number; professional_tax: number;
    tds: number; advance_recovery: number; total_deductions: number;
  };
  employer_contributions: {
    pf_employer_epf: number; pf_employer_eps: number; pf_edli: number;
    pf_admin: number; pf_employer_total: number; esi_employer: number; total: number;
  };
  net_pay: number;
  ctc_cost: number;
  lwp_days: number;
  paid_days: number;
  proration_factor: number;
  esi_applicable: boolean;
  pf_applicable: boolean;
}

export interface PayrollRow {
  payroll_id?: string;
  employee_id: string;
  employee_name?: string;
  store_id?: string | null;
  entity_id?: string | null;
  basic_salary?: number;
  allowances?: number;
  incentives?: number;
  deductions?: number;
  advance_deduction?: number;
  net_salary?: number;
  status?: string;
  breakdown?: PayrollBreakdown;
  skipped?: boolean;
  reason?: string;
}

export interface PayrollTotals {
  gross?: number;
  deductions?: number;
  net?: number;
  employer_cost?: number;
}

export interface PayrollRunResponse {
  status: string;
  month: number;
  year: number;
  count: number;
  dry_run: boolean;
  rows: PayrollRow[];
  totals: PayrollTotals;
}
