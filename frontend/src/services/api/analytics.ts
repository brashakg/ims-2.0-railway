// ============================================================================
// IMS 2.0 - Analytics V2 API
// ============================================================================

import api from './client';

export const analyticsV2Api = {
  // 10: Discount Analysis
  getDiscountAnalysis: async (params?: { store_id?: string; date_from?: string; date_to?: string }) => {
    const response = await api.get('/analytics-v2/discount-analysis', { params });
    return response.data;
  },

  // 11: Demand Forecasting (SUPERADMIN)
  getDemandForecast: async (params?: { store_id?: string; category?: string }) => {
    const response = await api.get('/analytics-v2/demand-forecast', { params });
    return response.data;
  },

  // 12: Dead Stock
  getDeadStock: async (params?: { store_id?: string; days_threshold?: number }) => {
    const response = await api.get('/analytics-v2/dead-stock', { params });
    return response.data;
  },

  // 13: Loyalty
  getLoyaltyTiers: async (storeId?: string) => {
    const response = await api.get('/analytics-v2/loyalty/tiers', { params: { store_id: storeId } });
    return response.data;
  },
  earnLoyaltyPoints: async (data: { customer_id: string; order_id: string; amount: number }) => {
    const response = await api.post('/analytics-v2/loyalty/earn', data);
    return response.data;
  },
  redeemLoyaltyPoints: async (data: { customer_id: string; points: number; redemption_type: string }) => {
    const response = await api.post('/analytics-v2/loyalty/redeem', data);
    return response.data;
  },

  // 14: Contact Lens Subscriptions
  getCLSubscriptions: async (storeId?: string) => {
    const response = await api.get('/analytics-v2/cl-subscriptions', { params: { store_id: storeId } });
    return response.data;
  },
  sendCLReminder: async (customerId: string) => {
    const response = await api.post(`/analytics-v2/cl-subscription/reminder/${customerId}`);
    return response.data;
  },

  // 18: Eye Camps
  getEyeCamps: async (storeId?: string) => {
    const response = await api.get('/analytics-v2/eye-camps', { params: { store_id: storeId } });
    return response.data;
  },
  createEyeCamp: async (data: { name: string; date: string; location: string; type: string; target_attendees: number; staff_assigned: string[] }) => {
    const response = await api.post('/analytics-v2/eye-camps', data);
    return response.data;
  },
  updateEyeCamp: async (campId: string, data: { actual_attendees?: number; leads_captured?: number; conversions?: number; notes?: string }) => {
    const response = await api.patch(`/analytics-v2/eye-camps/${campId}`, data);
    return response.data;
  },

  // 19: Family Deals
  getFamilyDeals: async (storeId?: string) => {
    const response = await api.get('/analytics-v2/family-deals', { params: { store_id: storeId } });
    return response.data;
  },

  // 20: Staff Leaderboard
  getStaffLeaderboard: async (params?: { store_id?: string; period?: string }) => {
    const response = await api.get('/analytics-v2/staff-leaderboard', { params });
    return response.data;
  },

  // 22: Churn Prediction (SUPERADMIN)
  getChurnPrediction: async (storeId?: string) => {
    const response = await api.get('/analytics-v2/churn-prediction', { params: { store_id: storeId } });
    return response.data;
  },

  // 23: Anomaly Detection (SUPERADMIN)
  getAnomalyDetection: async (params?: { store_id?: string; date_from?: string; date_to?: string }) => {
    const response = await api.get('/analytics-v2/anomaly-detection', { params });
    return response.data;
  },

  // 25: Vendor Margins (SUPERADMIN)
  getVendorMargins: async (storeId?: string) => {
    const response = await api.get('/analytics-v2/vendor-margins', { params: { store_id: storeId } });
    return response.data;
  },
};
