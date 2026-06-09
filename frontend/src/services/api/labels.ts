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

// ----------------------------------------------------------------------------
// F2 -- internal lab routing (disposable barcoded job cards)
// ----------------------------------------------------------------------------

export type LabStationCode =
  | 'INTAKE'
  | 'EDGING'
  | 'COATING'
  | 'QC_LAB'
  | 'DISPATCH'
  | 'PICKUP';

export type LabScanReason =
  | 'REPO_UNAVAILABLE'
  | 'NOT_FOUND'
  | 'NO_STATIONS'
  | 'TERMINAL_STAGE'
  | 'UNKNOWN_STATION'
  | 'WRONG_STATION'
  | 'ALREADY_HERE'
  | 'CONCURRENT_CONFLICT'
  | 'WRITE_FAILED';

export interface LabScanResult {
  ok: boolean;
  reason?: LabScanReason;
  message: string;
  job_id?: string;
  job_number?: string;
  customer_name?: string;
  store_id?: string;
  previous_station?: string | null;
  current_station?: string | null;
  station_label?: string;
  stage?: string;
  advanced_status?: string | null;
  auto_notify?: boolean;
  stamped_at?: string;
  expected?: string | null;
  got?: string | null;
}

export interface LabStation {
  station_id: string;
  store_id: string;
  code: LabStationCode;
  label: string;
  sequence_order: number;
  is_active: boolean;
  target_dwell_minutes: number;
  advances_job_status: string | null;
  auto_notify_customer: boolean;
}

export interface StationQueueRow {
  job_id: string;
  job_number: string;
  customer_name: string;
  current_station: string;
  entered_at: string | null;
  dwell_minutes: number;
  sla_minutes: number;
  sla_chip: 'green' | 'amber' | 'red';
  status: string;
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

  // --- F2 internal lab routing ---

  /** Scan a disposable job card at a lab bench (forward-only station gate). */
  labScan: async (payload: {
    scanned_code: string;
    station_code: string;
    store_id?: string;
  }): Promise<LabScanResult> => {
    const resp = await api.post('/workshop/scan', payload);
    return resp.data as LabScanResult;
  },

  /** List the lab stations configured for a store (sequence order). */
  listStations: async (storeId?: string): Promise<{ stations: LabStation[]; store_id: string }> => {
    const resp = await api.get('/workshop/stations', { params: storeId ? { store_id: storeId } : {} });
    return resp.data as { stations: LabStation[]; store_id: string };
  },

  /** Create / update one station config (STORE_MANAGER+). */
  upsertStation: async (
    payload: Partial<LabStation> & { code: string; store_id?: string },
  ): Promise<{ ok: boolean; station: LabStation }> => {
    const resp = await api.post('/workshop/stations', payload);
    return resp.data as { ok: boolean; station: LabStation };
  },

  /** Jobs currently AT a station for a store (oldest-first, SLA-chipped). */
  getStationQueue: async (
    code: string,
    storeId?: string,
  ): Promise<{ station: string; jobs: StationQueueRow[]; total: number }> => {
    const resp = await api.get(`/workshop/stations/${code}/queue`, {
      params: storeId ? { store_id: storeId } : {},
    });
    return resp.data as { station: string; jobs: StationQueueRow[]; total: number };
  },

  /** Stamp job_card_printed_at + return the traveler barcode value. */
  printJobCard: async (jobId: string): Promise<{ ok: boolean; barcode_value: string }> => {
    const resp = await api.post(`/workshop/jobs/${jobId}/print-job-card`, {});
    return resp.data as { ok: boolean; barcode_value: string };
  },
};

export default labelsApi;
