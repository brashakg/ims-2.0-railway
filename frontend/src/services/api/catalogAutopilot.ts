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
  // v2 reference audit: the EXACT page this candidate was scraped from, and
  // every {source, url} pair that contributed (rendered as reference chips +
  // persisted onto the created product).
  source_url?: string | null;
  references?: Array<{ source: string; url: string | null }>;
  title?: string;
  brand?: string;
  model?: string;
  color?: string | null;
  size?: string | null;
  image_urls?: string[];
  specs?: Record<string, unknown>;
  description?: string | null;
  usp?: string | null;
  // Enrichment hints the backend may attach (e.g. the AI-enrichment source).
  // All optional + additive: the card renders them only when present, so the
  // page works identically whether or not the enriching source is configured.
  category?: string | null;
  // v2 AI spec-mapping: backend-suggested {attributeName: value} for the job's
  // category. The FE merges these UNDER its deterministic mapper (gaps only).
  ai_attributes?: Record<string, string> | null;
  suggested_hsn?: string | null;
  suggested_gst_rate?: number | null;
  confidence?: number | null;
  needs_review?: boolean | null;
  existing_status?: string | null;
  existing_shopify_product_id?: string | null;
  score: number;
  matched?: Record<string, boolean>;
  decision?: string | null;
  rights_confirmed?: boolean;
}

// Source id the backend uses for the AI-enrichment adapter. Cards badge this
// distinctly ("AI-suggested") from scraped/catalog rows.
export const AI_ENRICH_SOURCE = 'ai_enrich';

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
  query: { brand: string; model: string; color?: string; size?: string; category?: string };
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

  // `category` (v2): a Quick Add picker code (SG/FR/...), canonical key, or
  // human label — the backend canonicalises + stamps it on every candidate.
  createJob: async (body: {
    brand: string;
    model: string;
    color?: string;
    size?: string;
    category?: string;
  }) => {
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
