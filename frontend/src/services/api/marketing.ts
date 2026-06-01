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
// Campaign Manager — the campaign LAYER on top of the marketing send infra.
// Backed by /api/v1/marketing/campaigns/* (routers/campaigns.py). Import DIRECT
// from this module: `import { campaignsApi } from '../../services/api/marketing'`.
// ============================================================================

export type CampaignType = 'rx_renewal' | 'birthday' | 'winback' | 'custom';
export type CampaignStatus = 'DRAFT' | 'SCHEDULED' | 'ACTIVE' | 'PAUSED' | 'COMPLETED';
export type CampaignChannel = 'WHATSAPP' | 'SMS' | 'EMAIL';
export type ScheduleKind = 'one_time' | 'recurring' | 'triggered';

export interface CampaignSchedule {
  kind: ScheduleKind;
  send_at?: string | null;
  frequency?: 'daily' | 'weekly' | 'monthly' | null;
  trigger_event?: string | null;
}

export interface CampaignStats {
  sent: number;
  delivered: number;
  failed: number;
  converted: number;
}

export interface Campaign {
  campaign_id: string;
  name: string;
  campaign_type: CampaignType;
  segment_id: string;
  channels: CampaignChannel[];
  template_id: string;
  schedule?: CampaignSchedule;
  status: CampaignStatus;
  store_id?: string | null;
  notes?: string | null;
  stats: CampaignStats;
  last_run_at?: string | null;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
}

export interface CampaignSummary {
  active: number;
  total: number;
  total_sent: number;
  total_delivered: number;
  delivery_rate: number;
  open_rate: number;
  conversion_rate: number;
}

export interface CampaignListResponse {
  campaigns: Campaign[];
  summary: CampaignSummary;
  dispatch_mode?: string;
}

export interface CampaignSegment {
  id: string;
  label: string;
  description: string;
  default_template: string;
  campaign_type: CampaignType;
  audience_count: number | null;
}

export interface SegmentListResponse {
  segments: CampaignSegment[];
  store_scope?: string | null;
  dispatch_mode?: string;
}

export interface SegmentPreviewResponse {
  segment_id: string;
  audience_count: number;
  sample: { name: string; masked_phone: string }[];
  store_scope?: string | null;
}

export interface CampaignSendResponse {
  message: string;
  campaign_id: string;
  campaign_run_id: string;
  audience: number;
  queued: number;
  skipped: number;
  failed: number;
  dispatch_mode: string;
  status: CampaignStatus;
}

export interface CampaignAnalytics {
  campaign_id: string;
  name?: string;
  status?: CampaignStatus;
  segment_id?: string;
  channels?: CampaignChannel[];
  totals: {
    audience_messages: number;
    queued: number;
    sent: number;
    delivered: number;
    failed: number;
    converted: number;
  };
  rates: {
    delivery_rate: number;
    failure_rate: number;
    conversion_rate: number;
  };
  per_channel: Record<string, { queued: number; sent: number; delivered: number; failed: number }>;
  dispatch_mode?: string;
  last_run_at?: string | null;
}

export interface CampaignCreatePayload {
  name: string;
  campaign_type: CampaignType;
  segment_id: string;
  channels: CampaignChannel[];
  template_id: string;
  schedule?: CampaignSchedule;
  store_id?: string;
  notes?: string;
}

export type CampaignUpdatePayload = Partial<Omit<CampaignCreatePayload, 'store_id'>>;

export const campaignsApi = {
  list: async (params?: { store_id?: string; status?: string; limit?: number }) => {
    const response = await api.get('/marketing/campaigns', { params });
    return response.data as CampaignListResponse;
  },
  get: async (campaignId: string) => {
    const response = await api.get(`/marketing/campaigns/${campaignId}`);
    return response.data as { campaign: Campaign };
  },
  create: async (payload: CampaignCreatePayload) => {
    const response = await api.post('/marketing/campaigns', payload);
    return response.data as { message: string; campaign: Campaign };
  },
  update: async (campaignId: string, payload: CampaignUpdatePayload) => {
    const response = await api.put(`/marketing/campaigns/${campaignId}`, payload);
    return response.data as { message: string; campaign: Campaign };
  },
  remove: async (campaignId: string) => {
    const response = await api.delete(`/marketing/campaigns/${campaignId}`);
    return response.data as { message: string; campaign_id: string };
  },
  duplicate: async (campaignId: string) => {
    const response = await api.post(`/marketing/campaigns/${campaignId}/duplicate`);
    return response.data as { message: string; campaign: Campaign };
  },

  // Segments
  listSegments: async (params?: { store_id?: string; with_counts?: boolean }) => {
    const response = await api.get('/marketing/campaigns/segments', { params });
    return response.data as SegmentListResponse;
  },
  previewSegment: async (payload: { segment_id: string; store_id?: string; sample_size?: number }) => {
    const response = await api.post('/marketing/campaigns/segments/preview', payload);
    return response.data as SegmentPreviewResponse;
  },

  // Lifecycle
  schedule: async (campaignId: string, schedule: CampaignSchedule) => {
    const response = await api.post(`/marketing/campaigns/${campaignId}/schedule`, { schedule });
    return response.data as { message: string; campaign_id: string; status: CampaignStatus };
  },
  pause: async (campaignId: string) => {
    const response = await api.post(`/marketing/campaigns/${campaignId}/pause`);
    return response.data as { message: string; campaign_id: string; status: CampaignStatus };
  },
  resume: async (campaignId: string) => {
    const response = await api.post(`/marketing/campaigns/${campaignId}/resume`);
    return response.data as { message: string; campaign_id: string; status: CampaignStatus };
  },
  send: async (campaignId: string) => {
    const response = await api.post(`/marketing/campaigns/${campaignId}/send`);
    return response.data as CampaignSendResponse;
  },
  analytics: async (campaignId: string) => {
    const response = await api.get(`/marketing/campaigns/${campaignId}/analytics`);
    return response.data as CampaignAnalytics;
  },
};
