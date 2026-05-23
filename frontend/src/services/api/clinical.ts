// ============================================================================
// IMS 2.0 - Clinical / Eye Test API
// ============================================================================

import api from './client';

// Shape returned by GET /clinical/abuse-detection. Mirrors the backend
// _build_abuse_alerts() output and the AbuseDetection component's renderer.
export interface AbuseAlert {
  id: string;
  type: 'high-redo-rate' | 'exact-copy' | 'suspicious-speed';
  severity: 'warning' | 'critical';
  optometristName: string;
  optometristId: string;
  details: string;
  timestamp: string;
  prescriptionIds?: string[];
  redoRate?: number;
}

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

  // Prescription printout (A5 card) — returns a self-contained HTML string.
  // Fetched through the authenticated client (not window.open(url)) so the
  // Bearer token is attached; the caller writes the HTML into a new window.
  // Mirrors payrollApi.getPayslipHtml.
  getPrescriptionPrintHtml: async (prescriptionId: string): Promise<string> => {
    const response = await api.get(`/clinical/prescriptions/${prescriptionId}/print`, {
      responseType: 'text',
    });
    return response.data as string;
  },

  // Redo tracking (lens remake / re-dispense) — gated server-side to optometry
  // + manager roles.
  recordRedo: async (prescriptionId: string, reason: string) => {
    const response = await api.post(`/clinical/prescriptions/${prescriptionId}/redo`, { reason });
    return response.data;
  },

  getRedos: async (prescriptionId: string) => {
    const response = await api.get(`/clinical/prescriptions/${prescriptionId}/redos`);
    return response.data;
  },

  // Clinical abuse / fraud-signal detection (management view). Server-gated to
  // STORE_MANAGER / AREA_MANAGER / ADMIN / SUPERADMIN. Returns the alert list
  // computed over the last `days` for the given store.
  getAbuseAlerts: async (storeId?: string, days = 30) => {
    const params: Record<string, string | number> = { days };
    if (storeId) params.store_id = storeId;
    const response = await api.get('/clinical/abuse-detection', { params });
    return response.data as { alerts: AbuseAlert[]; generated_at: string };
  },
};
