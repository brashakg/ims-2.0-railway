// ============================================================================
// IMS 2.0 - Sales / Billing / Orders API
// ============================================================================

import api from './client';

// ============================================================================
// Order API
// ============================================================================

export const orderApi = {
  getOrders: async (params?: { storeId?: string; status?: string; date?: string; customerId?: string; limit?: number; skip?: number }) => {
    const response = await api.get('/orders', { params });
    return response.data;
  },

  getOrder: async (orderId: string) => {
    const response = await api.get(`/orders/${orderId}`);
    return response.data;
  },

  createOrder: async (data: Partial<import('../../types').Order>) => {
    const response = await api.post('/orders', data);
    return response.data;
  },

  addOrderItem: async (orderId: string, item: Partial<import('../../types').OrderItem>) => {
    const response = await api.post(`/orders/${orderId}/items`, item);
    return response.data;
  },

  removeOrderItem: async (orderId: string, itemId: string) => {
    const response = await api.delete(`/orders/${orderId}/items/${itemId}`);
    return response.data;
  },

  addPayment: async (orderId: string, payment: Partial<import('../../types').Payment>) => {
    const response = await api.post(`/orders/${orderId}/payments`, payment);
    return response.data;
  },

  confirmOrder: async (orderId: string) => {
    const response = await api.post(`/orders/${orderId}/confirm`);
    return response.data;
  },

  deliverOrder: async (orderId: string) => {
    const response = await api.post(`/orders/${orderId}/deliver`);
    return response.data;
  },

  cancelOrder: async (orderId: string, reason: string) => {
    const response = await api.post(`/orders/${orderId}/cancel`, { reason });
    return response.data;
  },
};

// ============================================================================
// Prescription API
// ============================================================================

export const prescriptionApi = {
  getPrescriptions: async (patientOrCustomerId: string) => {
    // Try patient_id first; if empty, fall back to customer_id
    let response = await api.get('/prescriptions', { params: { patient_id: patientOrCustomerId } });
    const data = response.data;
    const rxList = data?.prescriptions || data || [];
    if (Array.isArray(rxList) && rxList.length === 0) {
      // No results by patient_id — try as customer_id
      response = await api.get('/prescriptions', { params: { customer_id: patientOrCustomerId } });
      return response.data;
    }
    return data;
  },

  getPrescription: async (prescriptionId: string) => {
    const response = await api.get(`/prescriptions/${prescriptionId}`);
    return response.data;
  },

  createPrescription: async (data: Partial<import('../../types').Prescription>) => {
    const response = await api.post('/prescriptions', data);
    return response.data;
  },

  validatePrescription: async (prescriptionId: string) => {
    const response = await api.get(`/prescriptions/${prescriptionId}/validate`);
    return response.data;
  },
};

// ============================================================================
// Workshop API
// ============================================================================

export const workshopApi = {
  getJobs: async (storeId: string, status?: string) => {
    const response = await api.get('/workshop/jobs', { params: { store_id: storeId, status } });
    return response.data;
  },

  getJob: async (jobId: string) => {
    const response = await api.get(`/workshop/jobs/${jobId}`);
    return response.data;
  },

  updateJobStatus: async (jobId: string, status: string, notes?: string) => {
    const response = await api.patch(`/workshop/jobs/${jobId}/status`, { status, notes });
    return response.data;
  },

  assignJob: async (jobId: string, staffId: string) => {
    const response = await api.post(`/workshop/jobs/${jobId}/assign`, { staff_id: staffId });
    return response.data;
  },

  createJob: async (data: {
    order_id: string;
    frame_details: Record<string, any>;
    lens_details: Record<string, any>;
    prescription_id: string;
    fitting_instructions?: string;
    special_notes?: string;
    expected_date: string;
  }) => {
    const response = await api.post('/workshop/jobs', data);
    return response.data;
  },

  // Phase 6.4 — single-call workshop KPIs for the dashboard header.
  // Replaces 4 client-side list calls + local counting with one small payload.
  getDashboardKpis: async (storeId?: string) => {
    const response = await api.get('/workshop/dashboard-kpis', {
      params: storeId ? { store_id: storeId } : {},
    });
    return response.data as {
      pending: number;
      in_progress: number;
      qc_failed: number;
      ready_for_pickup: number;
      overdue: number;
      completed_today: number;
      delivered_today: number;
      avg_turnaround_days: number | null;
      store_id: string | null;
      as_of: string;
    };
  },

  // Phase 6.8 — attach / update lens fitting details on a workshop job.
  // Called from the POS LensFittingFormModal right after an Rx order is
  // created, so the workshop tech sees the full fitting measurements
  // + sales confirmation when they pick up the job.
  updateFittingDetails: async (
    jobId: string,
    fittingDetails: {
      dia?: string;
      fh?: string;
      b_size?: string;
      dbl?: string;
      tint?: string;
      base_curve?: string;
      coating?: string;
      other?: string;
      vendor_order_id?: string;
      order_date?: string;
      order_time?: string;
      ordered_by?: string;
      ordered_by_name?: string;
      expected_lens_receive_date?: string;
      confirmed_by_sales: boolean;
      confirmed_at?: string;
    },
  ) => {
    const response = await api.patch(`/workshop/jobs/${jobId}/fitting-details`, {
      fitting_details: fittingDetails,
    });
    return response.data;
  },

  // Phase 6.4 — pending jobs report with aging buckets + per-tech breakdown.
  getPendingJobsReport: async (storeId?: string) => {
    const response = await api.get('/reports/workshop/pending-jobs', {
      params: storeId ? { store_id: storeId } : {},
    });
    return response.data as {
      data: Array<{
        job_id: string;
        job_number: string | null;
        order_id: string | null;
        status: string;
        technician_id: string | null;
        expected_date: string | null;
        created_at: string | null;
        age_days: number | null;
        aging_bucket: '0-3d' | '3-7d' | '7+d';
        is_overdue: boolean;
      }>;
      summary: {
        total_pending: number;
        overdue: number;
        by_aging_bucket: Record<'0-3d' | '3-7d' | '7+d', number>;
        by_technician: Array<{ technician_id: string; count: number }>;
      };
    };
  },
};

// ============================================================================
// Admin API - Discount Rules
// ============================================================================

export const adminDiscountApi = {
  getDiscountRules: async () => {
    const response = await api.get('/admin/discounts/rules');
    return response.data;
  },

  getRoleDiscountCaps: async () => {
    const response = await api.get('/admin/discounts/role-caps');
    return response.data;
  },

  setRoleDiscountCap: async (role: string, maxDiscount: number) => {
    const response = await api.post('/admin/discounts/role-caps', { role, max_discount: maxDiscount });
    return response.data;
  },

  getTierDiscounts: async () => {
    const response = await api.get('/admin/discounts/tier-discounts');
    return response.data;
  },

  setTierDiscount: async (tier: string, discount: number) => {
    const response = await api.post('/admin/discounts/tier-discounts', { tier, discount });
    return response.data;
  },

  createPromoCode: async (data: {
    code: string;
    discountType: 'PERCENTAGE' | 'FIXED';
    discountValue: number;
    minPurchase?: number;
    maxDiscount?: number;
    validFrom: string;
    validTo: string;
    usageLimit?: number;
    categories?: string[];
  }) => {
    const response = await api.post('/admin/discounts/promo-codes', data);
    return response.data;
  },

  getPromoCodes: async (params?: { active?: boolean }) => {
    const response = await api.get('/admin/discounts/promo-codes', { params });
    return response.data;
  },

  deletePromoCode: async (codeId: string) => {
    const response = await api.delete(`/admin/discounts/promo-codes/${codeId}`);
    return response.data;
  },
};
