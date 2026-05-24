// ============================================================================
// IMS 2.0 - Labels + Scan-to-Advance API
// ============================================================================
// Backend at /api/v1/workshop/* (scan-advance + label payloads) and
// /api/v1/print/qz/* (QZ signing, handled separately in services/qz.ts).
//
// IMPORTANT: import this DIRECTLY from its module, NOT via the services/api
// barrel -- barrel re-export of newly-added services fails to resolve for
// consumers (TS2614) in this codebase.

import api from './client';
import type { JobLabelData, ProductLabelData } from '../../components/labels/labelTemplates';

export type ScanReason =
  | 'WRONG_JOB'
  | 'TERMINAL_STAGE'
  | 'WRONG_STATION'
  | 'UNKNOWN_STATION'
  | 'NOT_FOUND'
  | 'REPO_UNAVAILABLE'
  | 'WRITE_FAILED';

export interface ScanAdvanceResult {
  ok: boolean;
  reason?: ScanReason;
  message: string;
  job_id?: string;
  job_number?: string;
  previous?: string;
  stage?: string;
  stage_label?: string;
  station?: string | null;
  stamped_at?: string;
  expected?: string | null;
  got?: string | null;
}

export const labelsApi = {
  /** Advance a job to the next legal stage by scanning its barcode. */
  scanAdvance: async (
    jobId: string,
    payload: { scanned_code?: string; station?: string | null },
  ): Promise<ScanAdvanceResult> => {
    const resp = await api.post(`/workshop/jobs/${jobId}/scan-advance`, payload);
    return resp.data as ScanAdvanceResult;
  },

  /** Fetch the data a job label needs (traveler | stage | ready). */
  getJobLabel: async (
    jobId: string,
    type: 'traveler' | 'stage' | 'ready' = 'traveler',
  ): Promise<JobLabelData & { ok?: boolean }> => {
    const resp = await api.get(`/workshop/jobs/${jobId}/label`, { params: { type } });
    return resp.data as JobLabelData & { ok?: boolean };
  },

  /** Fetch a frame-tag / CL-box label payload from a product or stock id. */
  getProductLabel: async (params: {
    product_id?: string;
    stock_id?: string;
  }): Promise<ProductLabelData & { ok?: boolean; reason?: string }> => {
    const resp = await api.get('/workshop/product-label', { params });
    return resp.data as ProductLabelData & { ok?: boolean; reason?: string };
  },
};

export default labelsApi;
