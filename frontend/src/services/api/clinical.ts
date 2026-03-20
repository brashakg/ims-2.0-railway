// ============================================================================
// IMS 2.0 - Clinical / Eye Test API
// ============================================================================

import api from './client';

export const clinicalApi = {
  // Queue management
  getQueue: async (storeId: string) => {
    const response = await api.get('/clinical/queue', { params: { store_id: storeId } });
    return response.data;
  },

  addToQueue: async (data: {
    storeId: string;
    patientName: string;
    customerPhone: string;
    age?: number;
    reason?: string;
    customerId?: string;
  }) => {
    const response = await api.post('/clinical/queue', data);
    return response.data;
  },

  updateQueueStatus: async (queueId: string, status: string) => {
    const response = await api.patch(`/clinical/queue/${queueId}/status`, { status });
    return response.data;
  },

  removeFromQueue: async (queueId: string) => {
    const response = await api.delete(`/clinical/queue/${queueId}`);
    return response.data;
  },

  // Eye tests
  getTodayTests: async (storeId: string) => {
    const response = await api.get('/clinical/tests', { params: { store_id: storeId, date: 'today' } });
    return response.data;
  },

  getTest: async (testId: string) => {
    const response = await api.get(`/clinical/tests/${testId}`);
    return response.data;
  },

  startTest: async (queueId: string) => {
    const response = await api.post(`/clinical/queue/${queueId}/start-test`);
    return response.data;
  },

  completeTest: async (testId: string, data: {
    rightEye: { sphere: number | null; cylinder: number | null; axis: number | null; add?: number | null };
    leftEye: { sphere: number | null; cylinder: number | null; axis: number | null; add?: number | null };
    pd?: number;
    notes?: string;
  }) => {
    const response = await api.post(`/clinical/tests/${testId}/complete`, data);
    return response.data;
  },
};
