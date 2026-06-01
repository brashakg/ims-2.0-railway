// ============================================================================
// IMS 2.0 - Marketing Automation API
// ============================================================================

import api from './client';

export const marketingApi = {
  // Feature 1: Notifications
  sendNotification: async (data: { customer_id: string; customer_phone: string; customer_name: string; template_id: string; channel?: string; variables?: Record<string, string>; category?: string }) => {
    const response = await api.post('/marketing/notifications/send', data);
    return response.data;
  },
  getNotificationLogs: async (params?: { store_id?: string; template_id?: string; status?: string; limit?: number }) => {
    const response = await api.get('/marketing/notifications/logs', { params });
    return response.data;
  },

  // Feature 2: Google Review
  sendReviewRequest: async (orderId: string) => {
    const response = await api.post(`/marketing/review-request/${orderId}`);
    return response.data;
  },

  // Feature 3: Rx Expiry Recall
  getRxExpiryAlerts: async (storeId?: string) => {
    const response = await api.get('/marketing/rx-expiry-alerts', { params: { store_id: storeId } });
    return response.data;
  },
  sendRxReminder: async (customerId: string) => {
    const response = await api.post(`/marketing/rx-reminder/${customerId}`);
    return response.data;
  },
  snoozeRxAlert: async (customerId: string, days: number) => {
    const response = await api.post(`/marketing/rx-snooze/${customerId}`, { days });
    return response.data;
  },

  // Feature 4: Referral Program
  sendReferralInvite: async (customerId: string) => {
    const response = await api.post(`/marketing/referral-invite/${customerId}`);
    return response.data;
  },
  getReferrals: async (storeId?: string, status?: string) => {
    const response = await api.get('/marketing/referrals', { params: { store_id: storeId, status } });
    return response.data;
  },
  redeemReferral: async (referralId: string) => {
    const response = await api.post(`/marketing/referrals/${referralId}/redeem`);
    return response.data;
  },

  // Feature 5: NPS Survey
  sendNpsSurvey: async (orderId: string) => {
    const response = await api.post(`/marketing/nps-survey/${orderId}`);
    return response.data;
  },
  submitNpsResponse: async (data: { nps_id: string; score: number; feedback?: string }) => {
    const response = await api.post('/marketing/nps-response', data);
    return response.data;
  },
  getNpsDashboard: async (storeId?: string) => {
    const response = await api.get('/marketing/nps-dashboard', { params: { store_id: storeId } });
    return response.data;
  },

  // Feature 6: Walk-in Capture
  createWalkin: async (data: { phone: string; name?: string; interest: string; notes?: string; store_id?: string }) => {
    const response = await api.post('/marketing/walkin', data, { params: { store_id: data.store_id } });
    return response.data;
  },
  getWalkins: async (storeId?: string) => {
    const response = await api.get('/marketing/walkins', { params: { store_id: storeId } });
    return response.data;
  },

  // Feature 7: Walkout Recovery
  recordWalkout: async (customerId: string, data: { frames_tried: string[]; reason?: string; notes?: string; store_id?: string }) => {
    const response = await api.post(`/marketing/walkout/${customerId}`, data, { params: { store_id: data.store_id } });
    return response.data;
  },
  getWalkoutRecoveries: async (storeId?: string) => {
    const response = await api.get('/marketing/walkout-recoveries', { params: { store_id: storeId } });
    return response.data;
  },
};

// ============================================================================
// Campaign layer (real backend: routers/campaigns.py under /api/v1/marketing)
// ============================================================================

export type CampaignType = 'rx_renewal' | 'birthday' | 'winback' | 'custom';
export type CampaignStatus = 'DRAFT' | 'SCHEDULED' | 'ACTIVE' | 'COMPLETED' | 'PAUSED';
export type ScheduleKind = 'ONE_TIME' | 'RECURRING' | 'TRIGGERED';

export interface CampaignSchedule {
  kind: ScheduleKind;
  send_at?: string | null;
  frequency?: string | null;
  time_of_day?: string | null;
  trigger_event?: string | null;
}

export interface Campaign {
  campaign_id: string;
  name: string;
  type: CampaignType;
  segment_key: string;
  segment_params?: Record<string, unknown>;
  channels: string[];
  template_id: string;
  store_id?: string | null;
  schedule?: CampaignSchedule | null;
  description?: string | null;
  status: CampaignStatus;
  audience_count: number;
  sent_count: number;
  failed_count: number;
  skipped_count: number;
  opened_count: number;
  converted_count: number;
  last_sent_at?: string | null;
  created_at: string;
  updated_at: string;
  audience_estimate?: number;
}

export interface CampaignSummary {
  active: number;
  total_campaigns: number;
  total_sent: number;
  open_rate: number;
  conversion: number;
}

export interface Segment {
  key: string;
  label: string;
  description: string;
  default_channel: string;
  default_template_id: string;
  campaign_type: CampaignType;
  store_scoped: boolean;
  count: number;
}

export interface SegmentPreview {
  key: string;
  label: string;
  count: number;
  sample: { customer_id: string; name: string; phone_masked: string }[];
}

export interface CampaignAnalytics {
  campaign_id: string;
  name?: string;
  status?: string;
  total: number;
  sent: number;
  delivered: number;
  failed: number;
  pending: number;
  opened: number;
  converted: number;
  open_rate: number;
  conversion_rate: number;
  delivery_rate: number;
  by_channel: Record<string, { total: number; sent: number; delivered: number; failed: number }>;
}

export interface CampaignCreatePayload {
  name: string;
  type: CampaignType;
  segment_key: string;
  channels: string[];
  template_id: string;
  store_id?: string;
  segment_params?: Record<string, unknown>;
  schedule?: CampaignSchedule;
  description?: string;
}

export const campaignsApi = {
  listSegments: async (storeId?: string): Promise<{ segments: Segment[]; total: number }> => {
    const response = await api.get('/marketing/segments', { params: { store_id: storeId } });
    return response.data;
  },
  previewSegment: async (
    key: string,
    params?: { store_id?: string; customer_type?: string; window_days?: number },
  ): Promise<SegmentPreview> => {
    const response = await api.get(`/marketing/segments/${key}/preview`, { params });
    return response.data;
  },

  list: async (params?: { store_id?: string; status?: string; limit?: number }): Promise<{ campaigns: Campaign[]; total: number; summary: CampaignSummary }> => {
    const response = await api.get('/marketing/campaigns', { params });
    return response.data;
  },
  get: async (id: string): Promise<Campaign> => {
    const response = await api.get(`/marketing/campaigns/${id}`);
    return response.data;
  },
  create: async (data: CampaignCreatePayload): Promise<{ message: string; campaign: Campaign }> => {
    const response = await api.post('/marketing/campaigns', data);
    return response.data;
  },
  update: async (id: string, data: Partial<CampaignCreatePayload>): Promise<{ message: string; campaign: Campaign }> => {
    const response = await api.put(`/marketing/campaigns/${id}`, data);
    return response.data;
  },
  remove: async (id: string): Promise<{ message: string; campaign_id: string }> => {
    const response = await api.delete(`/marketing/campaigns/${id}`);
    return response.data;
  },
  duplicate: async (id: string): Promise<{ message: string; campaign: Campaign }> => {
    const response = await api.post(`/marketing/campaigns/${id}/duplicate`);
    return response.data;
  },
  schedule: async (id: string, schedule: CampaignSchedule): Promise<{ message: string; campaign: Campaign }> => {
    const response = await api.post(`/marketing/campaigns/${id}/schedule`, schedule);
    return response.data;
  },
  pause: async (id: string): Promise<{ message: string; campaign: Campaign }> => {
    const response = await api.post(`/marketing/campaigns/${id}/pause`);
    return response.data;
  },
  resume: async (id: string): Promise<{ message: string; campaign: Campaign }> => {
    const response = await api.post(`/marketing/campaigns/${id}/resume`);
    return response.data;
  },
  send: async (id: string): Promise<{ message: string; campaign_id: string; status: CampaignStatus; audience_count: number; queued: number; skipped: number; failed: number }> => {
    const response = await api.post(`/marketing/campaigns/${id}/send`);
    return response.data;
  },
  analytics: async (id: string): Promise<CampaignAnalytics> => {
    const response = await api.get(`/marketing/campaigns/${id}/analytics`);
    return response.data;
  },
};
