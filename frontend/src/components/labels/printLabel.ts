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

  const built = buildProductLabel(data);
  const doc = wrapLabelDocument(built, copies);
  return printZpl(await getLabelPrinterName(), built.zpl, doc);
}
