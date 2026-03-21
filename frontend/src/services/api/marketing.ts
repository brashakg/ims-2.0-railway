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
