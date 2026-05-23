// ============================================================================
// IMS 2.0 - Scan-to-Advance box (keyboard-wedge)
// ============================================================================
// A focused input that accepts a job barcode scan (keyboard-wedge scanners
// type fast + send Enter). Scanning a job label advances that job to its next
// legal workshop stage via the backend (stage-gated, no skipping). On success
// it auto-prints the next stage sticker; on a wrong scan / wrong stage it
// shows a LOUD alert and does not change state.

import { useEffect, useRef, useState } from 'react';
import { Scan, CheckCircle2, AlertTriangle, X } from 'lucide-react';
import clsx from 'clsx';
import { labelsApi } from '../../services/api/labels';
import type { ScanAdvanceResult } from '../../services/api/labels';
import { printJobLabel } from './printLabel';

interface ScanToAdvanceProps {
  /** Resolve a scanned code to a job id. Returns null when no job matches. */
  resolveJobId: (scannedCode: string) => string | null;
  /** Station hint (INTAKE/FITTING/QC/PICKUP) or empty for any forward move. */
  station?: string;
  /** Whether to auto-print the next stage sticker on a successful advance. */
  autoPrintStage?: boolean;
  /** Called after a successful advance so the parent can refresh its list. */
  onAdvanced?: (result: ScanAdvanceResult) => void;
}

type Feedback =
  | { kind: 'success'; result: ScanAdvanceResult }
  | { kind: 'error'; result: ScanAdvanceResult }
  | { kind: 'nomatch'; code: string }
  | null;

const STATIONS = ['', 'INTAKE', 'FITTING', 'QC', 'PICKUP'];

export function ScanToAdvance({
  resolveJobId,
  station: stationProp,
  autoPrintStage = true,
  onAdvanced,
}: ScanToAdvanceProps) {
  const [value, setValue] = useState('');
  const [station, setStation] = useState(stationProp || '');
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const submit = async () => {
    const code = value.trim();
    if (!code || busy) return;

    const jobId = resolveJobId(code);
    if (!jobId) {
      setFeedback({ kind: 'nomatch', code });
      setValue('');
      inputRef.current?.focus();
      return;
    }

    setBusy(true);
    try {
      const result = await labelsApi.scanAdvance(jobId, {
        scanned_code: code,
        station: station || undefined,
      });
      if (result.ok) {
        setFeedback({ kind: 'success', result });
        onAdvanced?.(result);
        // Auto-print the next stage sticker (fail-soft inside printJobLabel).
        if (autoPrintStage) {
          printJobLabel(jobId, 'stage').catch(() => {
            /* fail-soft: never block the scan flow */
          });
        }
      } else {
        setFeedback({ kind: 'error', result });
      }
    } catch {
      setFeedback({
        kind: 'error',
        result: { ok: false, message: 'Scan failed - network error.' },
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
      setFeedback(null);
    }
  };

  return (
    <div className="card">
      <div className="flex items-center gap-2 mb-3">
        <Scan className="w-5 h-5 text-bv-red-600" />
        <h3 className="text-base font-semibold text-gray-900">Scan to advance stage</h3>
      </div>

      <div className="flex flex-col sm:flex-row gap-2">
        <div className="relative flex-1">
          <Scan className="w-5 h-5 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={busy}
            placeholder="Scan a job label barcode..."
            className="input-field pl-10"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <select
          value={station}
          onChange={(e) => setStation(e.target.value)}
          className="input-field sm:w-44"
          title="Station (optional). Restricts the scan to that station's step."
        >
          {STATIONS.map((s) => (
            <option key={s || 'any'} value={s}>
              {s ? `Station: ${s}` : 'Any station'}
            </option>
          ))}
        </select>
        <button onClick={submit} disabled={busy || !value.trim()} className="btn-primary disabled:opacity-50">
          {busy ? 'Working...' : 'Advance'}
        </button>
      </div>

      {/* Loud feedback banner */}
      {feedback && (
        <div
          className={clsx(
            'mt-3 flex items-start gap-2 rounded-lg border p-3 text-sm',
            feedback.kind === 'success'
              ? 'bg-green-50 border-green-300 text-green-800'
              : 'bg-red-50 border-red-300 text-red-800',
          )}
          role="alert"
        >
          {feedback.kind === 'success' ? (
            <CheckCircle2 className="w-5 h-5 mt-0.5 shrink-0" />
          ) : (
            <AlertTriangle className="w-5 h-5 mt-0.5 shrink-0" />
          )}
          <div className="flex-1">
            {feedback.kind === 'success' && (
              <p>
                <span className="font-bold">{feedback.result.job_number || feedback.result.job_id}</span>{' '}
                advanced{' '}
                <span className="font-mono">{feedback.result.previous}</span> {'->'}{' '}
                <span className="font-bold">{feedback.result.stage_label || feedback.result.stage}</span>.
              </p>
            )}
            {feedback.kind === 'error' && (
              <p>
                <span className="font-bold uppercase">{feedback.result.reason || 'Error'}:</span>{' '}
                {feedback.result.message}
              </p>
            )}
            {feedback.kind === 'nomatch' && (
              <p>
                <span className="font-bold uppercase">No match:</span> no job in this store matches
                the scanned code <span className="font-mono">{feedback.code}</span>.
              </p>
            )}
          </div>
          <button onClick={() => setFeedback(null)} className="p-0.5 hover:opacity-70">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}

export default ScanToAdvance;
