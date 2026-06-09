// ============================================================================
// IMS 2.0 - Configurable Reminders API (F46 / Engine E6)
// ============================================================================
// CRUD + toggle + preview(dry_run) + run-now + history over the config-driven
// reminder rail. This is CONFIG ONLY -- nothing here performs a live send. The
// rail rides send_notification (PENDING, DISPATCH_MODE-gated; the Railway
// default is `off`), so building/saving/activating a rule never reaches a
// provider. Seeded rules ship active=False.

import api from './client';

export type ReminderTriggerKind = 'CRON' | 'EVENT';
export type ReminderChannel = 'WHATSAPP' | 'SMS' | 'EMAIL';
export type ReminderScope = 'GLOBAL' | 'ENTITY' | 'STORE';
export type ReminderRuleType =
  | 'rx_expiry'
  | 'birthday'
  | 'winback'
  | 'cl_reorder'
  | 'churn_risk'
  | 'lookbook'
  | 'feedback'
  | 'fu_due_today'
  | 'custom';

export interface ReminderTrigger {
  kind: ReminderTriggerKind;
  cron?: string | null;
  event_key?: string | null;
}

export interface ReminderVoucherTemplate {
  type: 'GIFT_CARD' | 'DISCOUNT';
  amount: number;
  validity_days: number;
}

export interface ReminderRule {
  rule_id: string;
  scope: ReminderScope;
  entity_id?: string | null;
  store_id?: string | null;
  name: string;
  rule_type: ReminderRuleType;
  segment_key: string;
  segment_params: Record<string, unknown>;
  channel: ReminderChannel;
  template_id: string;
  trigger: ReminderTrigger;
  is_transactional: boolean;
  freq_cap_exempt: boolean;
  voucher_template?: ReminderVoucherTemplate | null;
  active: boolean;
  last_run_at?: string | null;
  last_resolved?: number | null;
  sent_count: number;
  skipped_count: number;
  failed_count: number;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
  audience_estimate?: number;
}

export interface ReminderRuleCreate {
  name: string;
  rule_type: ReminderRuleType;
  segment_key: string;
  segment_params?: Record<string, unknown>;
  channel: ReminderChannel;
  template_id: string;
  trigger?: ReminderTrigger;
  scope?: ReminderScope;
  entity_id?: string | null;
  store_id?: string | null;
  is_transactional?: boolean;
  freq_cap_exempt?: boolean;
  voucher_template?: ReminderVoucherTemplate | null;
  active?: boolean;
}

export type ReminderRuleUpdate = Partial<ReminderRuleCreate>;

export interface ReminderPreviewResult {
  rule_id: string;
  dry_run: boolean;
  resolved: number;
  queued: number;
  tasks_created: number;
  voucher_minted: number;
  skipped_consent: number;
  skipped_freqcap: number;
  skipped_quiet: number;
  skipped_no_phone: number;
  errors: number;
}

export interface ReminderHistoryRow {
  notification_id?: string;
  customer_id?: string;
  customer_phone_masked?: string;
  template_id?: string;
  channel?: string;
  status?: string;
  created_at?: string;
}

export const remindersApi = {
  list: async (params?: {
    store_id?: string;
    entity_id?: string;
    active?: boolean;
    rule_type?: string;
  }): Promise<{ rules: ReminderRule[]; total: number }> => {
    const res = await api.get('/reminders/rules', { params });
    return res.data;
  },

  create: async (
    payload: ReminderRuleCreate,
  ): Promise<{ message: string; rule: ReminderRule }> => {
    const res = await api.post('/reminders/rules', payload);
    return res.data;
  },

  get: async (ruleId: string): Promise<ReminderRule> => {
    const res = await api.get(`/reminders/rules/${ruleId}`);
    return res.data;
  },

  update: async (ruleId: string, payload: ReminderRuleUpdate): Promise<ReminderRule> => {
    const res = await api.put(`/reminders/rules/${ruleId}`, payload);
    return res.data;
  },

  remove: async (ruleId: string): Promise<{ message: string; rule_id: string }> => {
    const res = await api.delete(`/reminders/rules/${ruleId}`);
    return res.data;
  },

  toggle: async (ruleId: string): Promise<{ rule_id: string; active: boolean }> => {
    const res = await api.post(`/reminders/rules/${ruleId}/toggle`);
    return res.data;
  },

  preview: async (ruleId: string): Promise<ReminderPreviewResult> => {
    const res = await api.post(`/reminders/rules/${ruleId}/preview`);
    return res.data;
  },

  runNow: async (ruleId: string): Promise<ReminderPreviewResult> => {
    const res = await api.post(`/reminders/rules/${ruleId}/run-now`);
    return res.data;
  },

  history: async (
    ruleId: string,
    limit = 100,
  ): Promise<{ rule_id: string; history: ReminderHistoryRow[]; total: number }> => {
    const res = await api.get(`/reminders/rules/${ruleId}/history`, { params: { limit } });
    return res.data;
  },
};
