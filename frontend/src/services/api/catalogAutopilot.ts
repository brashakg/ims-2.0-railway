// ============================================================================
// IMS 2.0 - Catalog Autopilot API (Phase 1)
// ============================================================================
// Enter brand + model -> search prioritised sources -> review candidates ->
// approve. Backed by /api/v1/catalog-autopilot/*.

import api from './client';

export interface AutopilotCandidate {
  candidate_id: string;
  job_id: string;
  source: string;
  source_class: 'AUTHORIZED' | 'UNVERIFIED';
  url?: string | null;
  title?: string;
  brand?: string;
  model?: string;
  color?: string | null;
  size?: string | null;
  image_urls?: string[];
  specs?: Record<string, unknown>;
  description?: string | null;
  usp?: string | null;
  existing_status?: string | null;
  existing_shopify_product_id?: string | null;
  score: number;
  matched?: Record<string, boolean>;
  decision?: string | null;
  rights_confirmed?: boolean;
}

export interface AutopilotSource {
  name: string;
  label: string;
  source_class: 'AUTHORIZED' | 'UNVERIFIED';
  priority: number;
  enabled: boolean;
  reason: string;
}

export interface AutopilotJobResult {
  job_id: string;
  query: { brand: string; model: string; color?: string; size?: string };
  candidates: AutopilotCandidate[];
  sources: AutopilotSource[];
  candidate_count: number;
  persisted: boolean;
}

export const catalogAutopilotApi = {
  getSources: async () => {
    const res = await api.get('/catalog-autopilot/sources');
    return res.data as { sources: AutopilotSource[] };
  },

  createJob: async (body: { brand: string; model: string; color?: string; size?: string }) => {
    const res = await api.post('/catalog-autopilot/jobs', body);
    return res.data as AutopilotJobResult;
  },

  listJobs: async () => {
    const res = await api.get('/catalog-autopilot/jobs');
    return res.data as { jobs: Array<Record<string, unknown>>; total: number };
  },

  decide: async (
    candidateId: string,
    body: { decision: 'APPROVE' | 'REJECT' | 'SPECS_ONLY' | 'NEEDS_REVIEW'; rights_confirmed?: boolean; note?: string },
  ) => {
    const res = await api.post(`/catalog-autopilot/candidates/${candidateId}/decision`, body);
    return res.data;
  },
};
