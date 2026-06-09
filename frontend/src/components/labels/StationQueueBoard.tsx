// ============================================================================
// IMS 2.0 - F2 Station Queue Board (lab floor map)
// ============================================================================
// Per-station columns showing how many jobs are at each in-house lab bench and
// how long each has been waiting (SLA-aged green/amber/red). A STORE_MANAGER can
// inline-edit a station's SLA threshold. Each column links to that bench's
// fullscreen scan terminal. Restrained: neutral greys + semantic colour only.

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Scan, RefreshCw, Loader2 } from 'lucide-react';
import { labelsApi } from '../../services/api/labels';
import type { LabStation, StationQueueRow } from '../../services/api/labels';

interface StationQueueBoardProps {
  storeId?: string;
  /** True when the caller may edit SLA thresholds (STORE_MANAGER+). */
  canConfigure?: boolean;
}

const CHIP_CLASS: Record<string, string> = {
  green: 'bg-green-50 text-green-700 border-green-200',
  amber: 'bg-amber-50 text-amber-700 border-amber-200',
  red: 'bg-red-50 text-red-700 border-red-200',
};

function humanDuration(minutes: number): string {
  if (!minutes || minutes < 1) return '<1m';
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return h <= 0 ? `${m}m` : `${h}h ${m}m`;
}

export function StationQueueBoard({ storeId, canConfigure = false }: StationQueueBoardProps) {
  const [stations, setStations] = useState<LabStation[]>([]);
  const [queues, setQueues] = useState<Record<string, StationQueueRow[]>>({});
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');

  const load = useMemo(
    () => async () => {
      if (!storeId) return;
      setLoading(true);
      try {
        const { stations: rows } = await labelsApi.listStations(storeId);
        const active = (rows || []).filter((s) => s.is_active);
        setStations(active);
        const entries = await Promise.all(
          active.map(async (s) => {
            try {
              const q = await labelsApi.getStationQueue(s.code, storeId);
              return [s.code, q.jobs || []] as const;
            } catch {
              return [s.code, []] as const;
            }
          }),
        );
        setQueues(Object.fromEntries(entries));
      } catch {
        /* fail-soft */
      } finally {
        setLoading(false);
      }
    },
    [storeId],
  );

  useEffect(() => {
    load();
  }, [load]);

  const saveSla = async (station: LabStation) => {
    const minutes = parseInt(editValue, 10);
    setEditing(null);
    if (Number.isNaN(minutes) || minutes < 0) return;
    try {
      await labelsApi.upsertStation({
        code: station.code,
        store_id: storeId,
        target_dwell_minutes: minutes,
      });
      await load();
    } catch {
      /* fail-soft */
    }
  };

  return (
    <div className="card">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-base font-semibold text-gray-900">Lab floor - stations</h3>
        <button onClick={load} disabled={loading} className="btn sm">
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </button>
      </div>

      {stations.length === 0 ? (
        <p className="py-4 text-center text-sm text-gray-400">
          No active lab stations configured for this store.
        </p>
      ) : (
        <div className="grid gap-3 grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
          {stations.map((s) => {
            const rows = queues[s.code] || [];
            return (
              <div key={s.code} className="min-h-[120px] rounded-lg border border-gray-200 bg-gray-50 p-2">
                <div className="mb-2 flex items-center justify-between">
                  <span className="truncate text-xs font-semibold uppercase tracking-wide text-gray-700" title={s.label}>
                    {s.label}
                  </span>
                  <span className="rounded-full bg-white px-2 py-0.5 text-xs font-bold text-gray-500">
                    {rows.length}
                  </span>
                </div>

                <div className="mb-2 flex items-center justify-between text-[11px] text-gray-500">
                  {editing === s.code ? (
                    <input
                      type="number"
                      autoFocus
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onBlur={() => saveSla(s)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') saveSla(s);
                        if (e.key === 'Escape') setEditing(null);
                      }}
                      className="w-16 rounded border border-gray-300 px-1 py-0.5 text-[11px]"
                    />
                  ) : (
                    <button
                      type="button"
                      disabled={!canConfigure}
                      onClick={() => {
                        setEditing(s.code);
                        setEditValue(String(s.target_dwell_minutes || 0));
                      }}
                      className={canConfigure ? 'underline-offset-2 hover:underline' : ''}
                      title={canConfigure ? 'Edit SLA (minutes)' : undefined}
                    >
                      SLA {s.target_dwell_minutes ? `${s.target_dwell_minutes}m` : 'none'}
                    </button>
                  )}
                  <Link
                    to={`/workshop/station/${s.code}`}
                    className="inline-flex items-center gap-1 text-gray-400 hover:text-gray-700"
                    title="Open bench scan terminal"
                  >
                    <Scan className="h-3.5 w-3.5" />
                  </Link>
                </div>

                <div className="space-y-1.5">
                  {rows.length === 0 ? (
                    <p className="py-2 text-center text-[11px] text-gray-400">--</p>
                  ) : (
                    rows.slice(0, 8).map((row) => (
                      <div key={row.job_id} className="rounded-md border border-gray-200 bg-white p-1.5 text-xs">
                        <div className="truncate font-mono font-semibold text-gray-900">{row.job_number}</div>
                        {row.customer_name && <div className="truncate text-gray-600">{row.customer_name}</div>}
                        <div className="mt-1">
                          <span
                            className={`rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${
                              CHIP_CLASS[row.sla_chip] || CHIP_CLASS.green
                            }`}
                          >
                            {humanDuration(row.dwell_minutes)}
                          </span>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default StationQueueBoard;
