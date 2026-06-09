// ============================================================================
// IMS 2.0 - F2 Station Scan Terminal (fullscreen tablet bench surface)
// ============================================================================
// A single in-house lab bench (INTAKE / EDGING / COATING / QC_LAB / DISPATCH /
// PICKUP) runs this page on a fixed tablet with a USB/Bluetooth wedge scanner.
// The technician scans the disposable Code128 job card; the backend gates the
// scan to the correct station in sequence and advances the job.
//
// Restrained, operational UI: neutral greys + a single semantic accent. Colour
// is used ONLY for meaning (green = within SLA, amber = nearing, red = over /
// scan rejected). No sidebar rail, no top nav -- a pure fullscreen surface so a
// bench operator cannot wander off into the rest of the app.

import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { labelsApi } from '../../services/api/labels';
import type { LabScanResult, StationQueueRow } from '../../services/api/labels';
import { useAuth } from '../../context/AuthContext';
import { useNow } from '../../hooks/useNow';

const STATION_LABELS: Record<string, string> = {
  INTAKE: 'Intake',
  EDGING: 'Edging Bench',
  COATING: 'Coating',
  QC_LAB: 'Lab QC',
  DISPATCH: 'Dispatch',
  PICKUP: 'Front-desk Pickup',
};

type Flash =
  | { kind: 'success'; result: LabScanResult }
  | { kind: 'error'; result: LabScanResult }
  | null;

const CHIP_CLASS: Record<string, string> = {
  green: 'bg-green-50 text-green-700 border-green-200',
  amber: 'bg-amber-50 text-amber-700 border-amber-200',
  red: 'bg-red-50 text-red-700 border-red-200',
};

function humanDuration(minutes: number): string {
  if (!minutes || minutes < 1) return '<1m';
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (h <= 0) return `${m}m`;
  return `${h}h ${m}m`;
}

export function StationScanPage() {
  const { stationCode = '' } = useParams<{ stationCode: string }>();
  const code = stationCode.toUpperCase();
  const { user } = useAuth();
  const storeId = user?.activeStoreId;
  const now = useNow(1000);

  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const [queue, setQueue] = useState<StationQueueRow[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const flashTimer = useRef<number | null>(null);

  const stationLabel = STATION_LABELS[code] || code;

  const loadQueue = useMemo(
    () => async () => {
      try {
        const data = await labelsApi.getStationQueue(code, storeId);
        setQueue(data.jobs || []);
      } catch {
        /* fail-soft: leave the last queue on a transient error */
      }
    },
    [code, storeId],
  );

  // Initial focus + queue load, then refresh the queue every 30s.
  useEffect(() => {
    inputRef.current?.focus();
    loadQueue();
    const id = window.setInterval(loadQueue, 30000);
    return () => window.clearInterval(id);
  }, [loadQueue]);

  useEffect(() => () => {
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
  }, []);

  const showFlash = (f: Flash) => {
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
    setFlash(f);
    // Success clears after 3s; an error lingers 5s so the operator reads it.
    const ttl = f?.kind === 'success' ? 3000 : 5000;
    flashTimer.current = window.setTimeout(() => setFlash(null), ttl);
  };

  const submit = async () => {
    const scanned = value.trim();
    if (!scanned || busy) return;
    setBusy(true);
    try {
      const result = await labelsApi.labScan({
        scanned_code: scanned,
        station_code: code,
        store_id: storeId,
      });
      if (result.ok) {
        showFlash({ kind: 'success', result });
        loadQueue();
      } else {
        showFlash({ kind: 'error', result });
      }
    } catch {
      showFlash({
        kind: 'error',
        result: { ok: false, reason: 'WRITE_FAILED', message: 'Scan failed - network error.' },
      });
    } finally {
      setBusy(false);
      setValue('');
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      submit();
    } else if (e.key === 'Escape') {
      setValue('');
      setFlash(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-white text-gray-900">
      {/* Top strip */}
      <div className="flex items-center justify-between border-b border-gray-200 px-6 py-3">
        <div className="flex items-center gap-3">
          <span className="text-lg font-semibold text-gray-900">{stationLabel}</span>
          <span className="rounded-full bg-gray-100 px-2.5 py-0.5 text-sm font-medium text-gray-700">
            {queue.length} in queue
          </span>
        </div>
        <div className="flex items-center gap-4">
          <span className="font-mono text-sm text-gray-500">
            {now.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: false })} IST
          </span>
          <Link to="/workshop" className="text-sm text-gray-400 hover:text-gray-700">
            Exit
          </Link>
        </div>
      </div>

      {/* Centre: large autofocus scan input + flash */}
      <div className="flex flex-1 flex-col items-center px-6 pt-10">
        <div className="w-full max-w-2xl">
          <label className="mb-2 block text-center text-sm font-medium text-gray-500">
            Scan job card at {stationLabel}
          </label>
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={busy}
            placeholder="Scan job barcode..."
            className="h-14 w-full rounded-lg border-2 border-gray-300 px-4 text-lg font-mono focus:border-gray-900 focus:outline-none disabled:opacity-50"
            autoComplete="off"
            spellCheck={false}
          />

          {flash?.kind === 'success' && (
            <div
              className="mt-5 rounded-lg border border-green-500 bg-green-50 px-5 py-4 text-green-800"
              role="status"
            >
              <div className="text-sm uppercase tracking-wide text-green-600">Scanned in</div>
              <div className="mt-1 text-xl font-semibold">
                {flash.result.job_number}{' '}
                <span className="font-normal text-green-700">- {flash.result.customer_name}</span>
              </div>
              <div className="mt-0.5 text-sm text-green-700">
                Now at {flash.result.station_label}
                {flash.result.advanced_status ? ` (${flash.result.advanced_status})` : ''}
                {flash.result.auto_notify ? ' - customer notified' : ''}
              </div>
            </div>
          )}

          {flash?.kind === 'error' && (
            <div
              className="mt-5 rounded-lg border border-red-500 bg-red-50 px-5 py-4 text-red-800"
              role="alert"
            >
              <div className="text-sm font-bold uppercase tracking-wide text-red-600">
                {flash.result.reason || 'Error'}
              </div>
              <div className="mt-1 text-base">{flash.result.message}</div>
            </div>
          )}
        </div>

        {/* Live queue table */}
        <div className="mt-10 w-full max-w-3xl">
          <div className="mb-2 text-sm font-medium text-gray-500">
            At this station (oldest first)
          </div>
          <div className="overflow-hidden rounded-lg border border-gray-200">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                <tr>
                  <th className="px-4 py-2 font-medium">Job</th>
                  <th className="px-4 py-2 font-medium">Customer</th>
                  <th className="px-4 py-2 font-medium">Time here</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {queue.length === 0 && (
                  <tr>
                    <td className="px-4 py-3 text-gray-400" colSpan={3}>
                      No jobs at this station.
                    </td>
                  </tr>
                )}
                {queue.slice(0, 20).map((row) => (
                  <tr key={row.job_id}>
                    <td className="px-4 py-2 font-mono text-gray-900">{row.job_number}</td>
                    <td className="px-4 py-2 text-gray-700">{row.customer_name}</td>
                    <td className="px-4 py-2">
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                          CHIP_CLASS[row.sla_chip] || CHIP_CLASS.green
                        }`}
                      >
                        {humanDuration(row.dwell_minutes)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

export default StationScanPage;
