// ============================================================================
// IMS 2.0 - Clinical / Eye Test API
// ============================================================================

import api from './client';

// CLI-11: single Dx code entry on a SOAP exam note.
export interface SoapDxCode {
  code: string;
  description?: string;
  system?: string;
}

// CLI-11: structured SOAP exam note shape sent to/received from the backend.
// All fields are optional; send undefined (or omit the whole block) for a
// refraction-only test — the backend treats absence as "no SOAP note".
export interface SoapNotePayload {
  // Subjective
  chiefComplaint?: string;
  historyPresentIllness?: string;
  ocularHistory?: string;
  systemicHistory?: string;
  familyHistory?: string;
  medications?: string;
  allergies?: string;
  vduUsage?: string;
  // Objective
  vaRightUnaided?: string;
  vaLeftUnaided?: string;
  vaRightAided?: string;
  vaLeftAided?: string;
  vaBinocular?: string;
  iopRight?: number;
  iopLeft?: number;
  colourVision?: string;
  coverTest?: string;
  dominantEye?: 'RIGHT' | 'LEFT';
  pupils?: string;
  ocularMotility?: string;
  slitLampSummary?: string;
  fundusSummary?: string;
  // Assessment
  assessment?: string;
  dxCodes?: SoapDxCode[];
  // Plan
  plan?: string;
  planReferral?: boolean;
  planReferralTo?: string;
  planFollowUp?: boolean;
  planFollowUpWeeks?: number;
  patientInstructions?: string;
}

// C6-B: full optometric-exam findings beyond refraction. Mirrors the backend
// ClinicalFindings model (camelCase aliases). All optional — a refraction-only
// test omits the whole block.
export interface ClinicalFindings {
  vaRightUnaided?: string;
  vaLeftUnaided?: string;
  vaRightAided?: string;
  vaLeftAided?: string;
  vaBinocular?: string;
  iopRight?: number;
  iopLeft?: number;
  chiefComplaint?: string;
  history?: string;
  diagnosis?: string;
  colourVision?: string;
  coverTest?: string;
  dominantEye?: 'RIGHT' | 'LEFT';
  additionalNotes?: string;
}

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
    /** The specific family member (patient) under the account being tested, so
     *  the resulting Rx groups under them in Family Rx rather than the holder. */
    patientId?: string;
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

  // Eye tests over a date RANGE (server-side). Pass a `range` keyword
  // (today | week | month | all) or explicit `from`/`to` (ISO YYYY-MM-DD).
  // Replaces the old getTodayTests + client-side filtering on Test History, so
  // Week / Month / All-Time actually query older rows. Each COMPLETED test row
  // carries `prescriptionId` (the auto-created Rx) so the Print button can open
  // the A5 card directly.
  getTests: async (
    storeId: string,
    opts?: { range?: 'today' | 'week' | 'month' | 'all'; from?: string; to?: string },
  ) => {
    const params: Record<string, string> = { store_id: storeId };
    if (opts?.from) params.from = opts.from;
    if (opts?.to) params.to = opts.to;
    if (opts?.range && !opts?.from && !opts?.to) params.range = opts.range;
    const response = await api.get('/clinical/tests', { params });
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
    rightEye: {
      sphere: number | null; cylinder: number | null; axis: number | null;
      add?: number | null; pd?: number | null;
      prism?: string | null; base?: string | null; va?: string | null;
    };
    leftEye: {
      sphere: number | null; cylinder: number | null; axis: number | null;
      add?: number | null; pd?: number | null;
      prism?: string | null; base?: string | null; va?: string | null;
    };
    pd?: number;
    // Parity fields mirrored into the prescriptions collection (see
    // clinical.complete_test): single IPD, lens type, next-checkup date.
    ipd?: string;
    lensRecommendation?: string;
    nextCheckup?: string;
    notes?: string;
    // C6-B: optional full-exam findings (VA / IOP / history / diagnosis / ...).
    // Omit entirely for a quick refraction-only test — the backend stores the
    // test exactly as before when this is absent.
    clinicalFindings?: ClinicalFindings;
    // CLI-11: optional structured SOAP exam note (S/O/A/P + Dx codes). Omit
    // entirely for a refraction-only test. Can also be saved/updated after
    // completion via saveSoapNote().
    soapNote?: SoapNotePayload;
  }) => {
    const response = await api.post(`/clinical/tests/${testId}/complete`, data);
    return response.data;
  },

  // CLI-11: retrieve the structured SOAP exam note for a completed test.
  // Returns { soapNote: {...} } or { soapNote: null } for refraction-only tests.
  getSoapNote: async (testId: string) => {
    const response = await api.get(`/clinical/tests/${testId}/soap-note`);
    return response.data as { soapNote: SoapNotePayload | null };
  },

  // CLI-11: save (or replace) the structured SOAP exam note for a test.
  // Allows post-completion charting without re-opening the test completion flow.
  saveSoapNote: async (testId: string, note: SoapNotePayload) => {
    const response = await api.post(`/clinical/tests/${testId}/soap-note`, note);
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
