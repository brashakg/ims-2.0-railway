// ============================================================================
// IMS 2.0 — Workshop Productivity Report
// ============================================================================
// Per-technician scorecard over a date range: jobs completed, avg turnaround,
// QC-failure rate, on-time rate, and relative utilization. Complements the
// point-in-time workshop dashboard KPIs + the pending-jobs queue report.

import { useEffect, useState } from 'react';
import { AlertTriangle, Download, Loader2, Wrench } from 'lucide-react';
import { reportsApi } from '../../../services/api';
import { exportToCSV } from '../../../utils/exportUtils';

type Data = Awaited<ReturnType<typeof reportsApi.getWorkshopProductivity>>;

function pct(n: number | null): string {
  return n === null || n === undefined ? '—' : (n * 100).toFixed(0) + '%';
}
function num(n: number | null): string {
  return n === null || n === undefined ? '—' : String(n);
}

function defaultFrom(): string {
  const d = new Date();
  d.setDate(d.getDate() - 30);
  return d.toISOString().slice(0, 10);
}
function defaultTo(): string {
  return new Date().toISOString().slice(0, 10);
}

export function WorkshopProductivityCard({ storeId }: { storeId?: string }) {
  const [fromDate, setFromDate] = useState(defaultFrom());
  const [toDate, setToDate] = useState(defaultTo());
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    reportsApi
      .getWorkshopProductivity(storeId, fromDate, toDate)
      .then(setData)
      .catch((e) => setError(e?.response?.data?.detail || e?.message || 'Failed to load'))
      .finally(() => setLoading(false));
  };

  // Reload when the store changes; the date range reloads via the Apply button
  // so a partial date edit doesn't fire a request mid-typing.
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storeId]);

  const exportCsv = () => {
    if (!data || data.technicians.length === 0) return;
    exportToCSV(
      data.technicians.map((t) => ({
        technician: t.technician_id || 'Unassigned',
        jobs_completed: t.jobs_completed,
        avg_turnaround_days: t.avg_turnaround_days ?? '',
        qc_fail_rate: t.qc_fail_rate === null ? '' : (t.qc_fail_rate * 100).toFixed(1) + '%',
        qc_jobs: t.qc_jobs,
        on_time_rate: t.on_time_rate === null ? '' : (t.on_time_rate * 100).toFixed(1) + '%',
        remake_jobs: t.remake_jobs,
        utilization: t.utilization === null ? '' : (t.utilization * 100).toFixed(0) + '%',
      })),
      `workshop_productivity_${fromDate}_to_${toDate}`,
      [
        { key: 'technician', label: 'Technician' },
        { key: 'jobs_completed', label: 'Jobs Completed' },
        { key: 'avg_turnaround_days', label: 'Avg Turnaround (days)' },
        { key: 'qc_fail_rate', label: 'QC Fail Rate' },
        { key: 'qc_jobs', label: 'QC Jobs' },
        { key: 'on_time_rate', label: 'On-time Rate' },
        { key: 'remake_jobs', label: 'Remakes' },
        { key: 'utilization', label: 'Utilization' },
      ],
    );
  };

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="font-semibold text-gray-900 inline-flex items-center gap-2">
          <Wrench className="w-4 h-4 text-bv-red-600" />
          Workshop Productivity
        </h3>
        <div className="flex items-center gap-2 flex-wrap">
          <input
            type="date"
            value={fromDate}
            max={toDate}
            onChange={(e) => setFromDate(e.target.value)}
            className="border border-gray-300 rounded px-2 py-1 text-sm"
          />
          <span className="text-gray-400 text-sm">to</span>
          <input
            type="date"
            value={toDate}
            min={fromDate}
            onChange={(e) => setToDate(e.target.value)}
            className="border border-gray-300 rounded px-2 py-1 text-sm"
          />
          <button onClick={load} disabled={loading} className="btn sm">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Apply'}
          </button>
          <button
            onClick={exportCsv}
            disabled={!data || data.technicians.length === 0}
            className="btn sm inline-flex items-center gap-1 disabled:opacity-50"
          >
            <Download className="w-4 h-4" /> CSV
          </button>
        </div>
      </div>
      <p className="text-xs text-gray-500 mb-4 max-w-3xl">
        Per-technician scorecard for jobs <strong>closed</strong> in the window.
        Utilization is each technician's completed jobs relative to the busiest.
      </p>

      {loading && (
        <div className="h-32 flex items-center justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-bv-red-600" />
        </div>
      )}
      {error && (
        <div className="flex items-start gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded p-3">
          <AlertTriangle className="w-4 h-4 mt-0.5" /> {error}
        </div>
      )}
      {data && !loading && (
        <>
          <div className="grid grid-cols-2 tablet:grid-cols-4 gap-3 mb-4">
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Jobs completed</p>
              <p className="text-lg font-bold text-gray-900 mt-1 tabular-nums">{data.totals.jobs_completed}</p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">Avg turnaround</p>
              <p className="text-lg font-bold text-gray-900 mt-1 tabular-nums">
                {num(data.totals.avg_turnaround_days)}<span className="text-xs text-gray-400"> d</span>
              </p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">QC fail rate</p>
              <p className={`text-lg font-bold mt-1 tabular-nums ${
                (data.totals.qc_fail_rate ?? 0) > 0.15 ? 'text-red-600' :
                (data.totals.qc_fail_rate ?? 0) > 0.05 ? 'text-amber-600' : 'text-gray-900'
              }`}>{pct(data.totals.qc_fail_rate)}</p>
            </div>
            <div className="bg-white rounded p-3 border border-gray-100">
              <p className="text-xs text-gray-500">On-time rate</p>
              <p className="text-lg font-bold text-gray-900 mt-1 tabular-nums">{pct(data.totals.on_time_rate)}</p>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs text-gray-500 border-b">
                <tr>
                  <th className="text-left py-2 px-2 font-medium">Technician</th>
                  <th className="text-right py-2 px-2 font-medium">Completed</th>
                  <th className="text-right py-2 px-2 font-medium">Avg TAT (d)</th>
                  <th className="text-right py-2 px-2 font-medium">QC fail</th>
                  <th className="text-right py-2 px-2 font-medium">On-time</th>
                  <th className="text-right py-2 px-2 font-medium">Remakes</th>
                  <th className="text-right py-2 px-2 font-medium">Utilization</th>
                </tr>
              </thead>
              <tbody>
                {data.technicians.length === 0 && (
                  <tr><td colSpan={7} className="py-3 text-center text-gray-400 text-xs">No jobs closed in this window.</td></tr>
                )}
                {data.technicians.map((t) => (
                  <tr key={t.technician_id || 'unassigned'} className="border-b last:border-b-0">
                    <td className="py-1.5 px-2 font-mono text-xs">{t.technician_id || 'Unassigned'}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{t.jobs_completed}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{num(t.avg_turnaround_days)}</td>
                    <td className={`py-1.5 px-2 text-right tabular-nums ${
                      (t.qc_fail_rate ?? 0) > 0.15 ? 'text-red-600' :
                      (t.qc_fail_rate ?? 0) > 0.05 ? 'text-amber-600' : 'text-gray-700'
                    }`}>
                      {pct(t.qc_fail_rate)}<span className="text-[10px] text-gray-400"> ({t.qc_jobs})</span>
                    </td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{pct(t.on_time_rate)}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums text-gray-500">{t.remake_jobs}</td>
                    <td className="py-1.5 px-2 text-right tabular-nums">{pct(t.utilization)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
