// ============================================================================
// IMS 2.0 - High-level label printing orchestration
// ============================================================================
// Ties together: fetch label payload -> build ZPL + HTML -> print via QZ Tray
// (silent raw) OR fall back to an HTML print window. Always fail-soft.
//
// Direct imports only (no services/api barrel for new services).

import { labelsApi } from '../../services/api/labels';
import { printZpl } from '../../services/qz';
import type { PrintResult } from '../../services/qz';
import {
  buildJobLabel,
  buildProductLabel,
  wrapLabelDocument,
} from './labelTemplates';
import type { JobLabelData, ProductLabelData } from './labelTemplates';

/** Fill any EMPTY store-identity field on `data` from `fallback` (active store).
 *  Backend-populated values win; only blanks are filled. So the issuing-store
 *  identity is always present even when the backend label payload is thin -- the
 *  label never falls back to a hardcoded brand. */
function mergeStoreFallback<T extends Record<string, any>>(
  data: T,
  fallback?: Partial<T>,
): T {
  if (!fallback) return data;
  const out: T = { ...data };
  const keys: Array<keyof T> = [
    'store_name', 'store_code', 'store_brand', 'store_address',
    'store_gstin', 'store_phone', 'store_id',
  ] as Array<keyof T>;
  for (const k of keys) {
    const cur = out[k];
    if ((cur === undefined || cur === null || cur === '') && fallback[k]) {
      out[k] = fallback[k] as T[keyof T];
    }
  }
  return out;
}

/** Read the configured label printer name from persisted printer settings. */
async function getLabelPrinterName(): Promise<string | undefined> {
  try {
    const { settingsApi } = await import('../../services/api/settings');
    const s = await settingsApi.getPrinterSettings();
    // QZ off switch: if the user disabled QZ in settings, return undefined so
    // we always use the HTML print window.
    if (s && (s as any).qz_enabled === false) return undefined;
    return (s as any)?.label_printer_name || undefined;
  } catch {
    return undefined;
  }
}

/**
 * Print a job label (traveler | stage | ready). Fetches the payload from the
 * backend first; if that fails it still prints a minimal label carrying the
 * job id barcode so the job can be scanned.
 */
export async function printJobLabel(
  jobId: string,
  type: 'traveler' | 'stage' | 'ready',
  fallbackData?: Partial<JobLabelData>,
  copies = 1,
): Promise<PrintResult> {
  let data: JobLabelData;
  try {
    data = await labelsApi.getJobLabel(jobId, type);
    if (!data || !data.job_id) {
      data = { job_id: jobId, ...(fallbackData || {}) } as JobLabelData;
    }
  } catch {
    data = { job_id: jobId, ...(fallbackData || {}) } as JobLabelData;
  }

  // Fill the issuing-store identity from the active-store fallback when absent.
  data = mergeStoreFallback(data, fallbackData);

  const built = buildJobLabel(type, data);
  const doc = wrapLabelDocument(built, copies);
  return printZpl(await getLabelPrinterName(), built.zpl, doc);
}

/** Print a frame-tag / CL-box label for a product or stock unit. */
export async function printProductLabel(
  params: { product_id?: string; stock_id?: string },
  fallbackData?: Partial<ProductLabelData>,
  copies = 1,
): Promise<PrintResult> {
  let data: ProductLabelData;
  try {
    const resp = await labelsApi.getProductLabel(params);
    if (!resp || !resp.barcode_value) {
      data = {
        barcode_value: params.stock_id || params.product_id || '',
        ...(fallbackData || {}),
      } as ProductLabelData;
    } else {
      data = resp;
    }
  } catch {
    data = {
      barcode_value: params.stock_id || params.product_id || '',
      ...(fallbackData || {}),
    } as ProductLabelData;
  }

  // Fill the issuing-store identity from the active-store fallback when absent.
  data = mergeStoreFallback(data, fallbackData);

  const built = buildProductLabel(data);
  const doc = wrapLabelDocument(built, copies);
  return printZpl(await getLabelPrinterName(), built.zpl, doc);
}
