// ============================================================================
// IMS 2.0 - Integration Settings (supplementary panel)
// ============================================================================
// This is NO LONGER an integration card grid. The unified IntegrationsHub
// (IntegrationsHub.tsx) is the single integration grid; it absorbed the per-
// integration configure/test/banners/env-callout that used to live here.
//
// What remains here is purely supplementary, rendered by the Hub below its
// catalog grid:
//   1. IntegrationStatusCard  -- read-only, SELF-GATES to SUPERADMIN (renders
//                                null for everyone else). Honest "live vs
//                                dormant" view from /jarvis/integrations/status.
//   2. TallyExportsPanel      -- per-store Tally voucher XML download table.
//
// Keeping these two as a thin component (instead of inlining them into the Hub)
// avoids churn and keeps the Tally export concern self-contained.

import { useState, useEffect, useCallback } from 'react';
import {
  Check, Loader2, Download, RefreshCw, AlertTriangle, FileDown,
} from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { adminIntegrationApi } from '../../services/api/settings';
import { IntegrationStatusCard } from '../integrations/IntegrationStatusCard';

export function IntegrationSettings() {
  return (
    <div className="space-y-6">
      {/* Read-only, SUPERADMIN-only honest status (renders null otherwise) */}
      <IntegrationStatusCard />

      {/* Tally per-store voucher export panel (Phase I-6). */}
      <TallyExportsPanel />
    </div>
  );
}

// ============================================================================
// Tally per-store export panel
// ============================================================================

interface TallyExportRow {
  store_id: string;
  store_code: string;
  store_name: string;
  voucher_count: number;
  balanced: boolean;
  balance_check?: { ok: boolean; mismatch_count: number; batch_delta: number };
  generated_at: string;
}

function todayIso(): string {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function TallyExportsPanel() {
  const toast = useToast();
  const [date, setDate] = useState<string>(todayIso());
  const [rows, setRows] = useState<TallyExportRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [regenAll, setRegenAll] = useState(false);
  const [regenStoreId, setRegenStoreId] = useState<string | null>(null);

  const load = useCallback(async (selectedDate: string) => {
    setLoading(true);
    try {
      const resp = await adminIntegrationApi.listTallyExports(selectedDate);
      setRows(resp.exports || []);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(date);
  }, [date, load]);

  const handleDownload = async (storeId: string, storeCode: string) => {
    setDownloadingId(storeId);
    try {
      await adminIntegrationApi.downloadTallyVoucherXml(date, storeId);
      toast.success(`Downloaded voucher XML for ${storeCode}`);
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        `Failed to download voucher for ${storeCode}`;
      toast.error(message);
    } finally {
      setDownloadingId(null);
    }
  };

  const handleRegenerate = async (storeId?: string) => {
    if (storeId) setRegenStoreId(storeId);
    else setRegenAll(true);
    try {
      const result = await adminIntegrationApi.regenerateTallyExport(date, storeId);
      if (result.ok) {
        toast.success(result.notes || 'Regenerated');
        await load(date);
      } else {
        toast.error(result.error || result.notes || 'Regenerate failed');
      }
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        'Failed to regenerate';
      toast.error(message);
    } finally {
      setRegenAll(false);
      setRegenStoreId(null);
    }
  };

  return (
    <div className="bg-white rounded-lg border-2 border-gray-200 p-6">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div>
          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
            <FileDown className="w-4 h-4 text-bv-red-600" />
            Tally voucher exports — per store
          </h3>
          <p className="text-sm text-gray-500 mt-0.5">
            One XML per active store. Import each in the matching Tally Company on your RDP machine.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="tally-export-date" className="text-xs font-medium text-gray-500">
            Date
          </label>
          <input
            id="tally-export-date"
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="px-3 py-1.5 border border-gray-300 rounded text-sm"
          />
          <button
            type="button"
            onClick={() => handleRegenerate()}
            disabled={regenAll || loading}
            className={clsx(
              'flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition',
              regenAll || loading
                ? 'bg-gray-100 text-gray-500 cursor-not-allowed'
                : 'bg-gray-100 hover:bg-gray-200 text-gray-700'
            )}
            title="Re-run the export for every active store on this date"
          >
            {regenAll ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <RefreshCw className="w-3.5 h-3.5" />
            )}
            Regenerate all
          </button>
        </div>
      </div>

      {loading && rows.length === 0 ? (
        <div className="flex items-center justify-center py-8 text-gray-500">
          <Loader2 className="w-5 h-5 animate-spin" />
        </div>
      ) : rows.length === 0 ? (
        <div className="text-center py-8 text-gray-500 text-sm">
          No exports for {date}. Either the day's nightly tick hasn't run yet, or no
          stores had qualifying orders. Use "Regenerate all" above to run on demand.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                <th className="py-2 pr-4">Store</th>
                <th className="py-2 pr-4">Vouchers</th>
                <th className="py-2 pr-4">Balanced</th>
                <th className="py-2 pr-4">Generated</th>
                <th className="py-2 pr-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.store_id} className="border-t border-gray-100">
                  <td className="py-2 pr-4">
                    <div className="font-medium text-gray-900">{row.store_name}</div>
                    <div className="text-xs font-mono text-gray-500">{row.store_code}</div>
                  </td>
                  <td className="py-2 pr-4 font-mono">{row.voucher_count}</td>
                  <td className="py-2 pr-4">
                    {row.balanced ? (
                      <span className="inline-flex items-center gap-1 text-green-700 text-xs font-medium">
                        <Check className="w-3.5 h-3.5" /> ok
                      </span>
                    ) : (
                      <span
                        className="inline-flex items-center gap-1 text-amber-700 text-xs font-medium"
                        title={
                          row.balance_check
                            ? `${row.balance_check.mismatch_count} mismatched, batch delta ${row.balance_check.batch_delta}`
                            : 'Failed validation'
                        }
                      >
                        <AlertTriangle className="w-3.5 h-3.5" />
                        unbalanced
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-4 text-xs text-gray-500">
                    {new Date(row.generated_at).toLocaleString('en-IN', {
                      day: '2-digit',
                      month: 'short',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </td>
                  <td className="py-2 pr-2 text-right">
                    <div className="inline-flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => handleRegenerate(row.store_id)}
                        disabled={regenStoreId === row.store_id}
                        className="p-1.5 rounded hover:bg-gray-100 text-gray-600 transition disabled:opacity-50"
                        title="Regenerate just this store"
                      >
                        {regenStoreId === row.store_id ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <RefreshCw className="w-3.5 h-3.5" />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDownload(row.store_id, row.store_code)}
                        disabled={downloadingId === row.store_id}
                        className={clsx(
                          'flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition',
                          downloadingId === row.store_id
                            ? 'bg-gray-100 text-gray-500 cursor-not-allowed'
                            : row.balanced
                              ? 'bg-bv-red-600 text-white hover:bg-bv-red-700'
                              : 'bg-amber-600 text-white hover:bg-amber-700'
                        )}
                      >
                        {downloadingId === row.store_id ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <Download className="w-3.5 h-3.5" />
                        )}
                        Download
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
