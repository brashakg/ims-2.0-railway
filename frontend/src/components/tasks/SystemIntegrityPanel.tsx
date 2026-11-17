// ============================================================================
// IMS 2.0 - System integrity & variance panel (Tasks)
// ============================================================================
// Manager-only: run the payment-variance scan (raises tasks for orders whose
// money doesn't reconcile) and see un-acknowledged ("silent") + implausibly
// fast-closed ("fake closure") tasks. Stock-count variance tasks are raised
// automatically on count completion and appear in the normal task list.

import { useCallback, useEffect, useState } from 'react';
import { ShieldAlert, ScanLine, Clock, AlertTriangle, Loader2 } from 'lucide-react';
import { tasksApi } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

const MANAGER_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'];

type Count = number | null | 'error';

function countOf(r: PromiseSettledResult<{ count?: unknown }>): Count {
  if (r.status !== 'fulfilled') return 'error';
  const n = Number(r.value?.count);
  return Number.isFinite(n) ? n : 'error';
}

const shown = (c: Count): string => (c === 'error' ? 'Unavailable' : c === null ? '—' : String(c));

export function SystemIntegrityPanel({ storeId }: { storeId?: string }) {
  const { user } = useAuth();
  const toast = useToast();
  const isManager = (user?.roles || []).some((r) => MANAGER_ROLES.includes(r));

  // A count, null while loading, or 'error' when its read failed (R45: a
  // failed read used to show as 0 suspect tasks). Each count stands alone.
  const [silent, setSilent] = useState<Count>(null);
  const [fake, setFake] = useState<Count>(null);
  const [scanning, setScanning] = useState(false);

  const load = useCallback(async () => {
    setSilent(null);
    setFake(null);
    const [s, f] = await Promise.allSettled([
      tasksApi.getSilentTasks(storeId),
      tasksApi.getFakeClosures(storeId),
    ]);
    setSilent(countOf(s));
    setFake(countOf(f));
  }, [storeId]);

  useEffect(() => { if (isManager) load(); }, [isManager, load]);

  if (!isManager) return null;

  const runScan = async () => {
    setScanning(true);
    try {
      const r = await tasksApi.scanPaymentVariance(7, storeId);
      toast.success(`Scanned ${r.scanned} orders · ${r.anomalies} anomalies · ${r.tasks_created} tasks raised`);
      await load();
    } catch {
      toast.error('Payment scan failed');
    } finally {
      setScanning(false);
    }
  };

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          <ShieldAlert className="w-4 h-4" /> System integrity &amp; variance
        </h3>
        <button onClick={runScan} disabled={scanning}
          className="btn-secondary text-xs inline-flex items-center gap-1">
          {scanning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ScanLine className="w-3.5 h-3.5" />}
          Run payment scan
        </button>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-gray-200 p-3">
          <div className="text-xs text-gray-500 flex items-center gap-1"><Clock className="w-3 h-3" /> Silent (unacknowledged)</div>
          <div className={silent === 'error' ? 'text-sm font-semibold text-gray-600 py-1.5' : 'text-2xl font-bold text-amber-600'}>{shown(silent)}</div>
        </div>
        <div className="rounded-lg border border-gray-200 p-3">
          <div className="text-xs text-gray-500 flex items-center gap-1"><AlertTriangle className="w-3 h-3" /> Fast-closed (suspect)</div>
          <div className={fake === 'error' ? 'text-sm font-semibold text-gray-600 py-1.5' : 'text-2xl font-bold text-red-600'}>{shown(fake)}</div>
        </div>
      </div>
      {(silent === 'error' || fake === 'error') && (
        <div className="flex items-center gap-2 mt-2 text-xs text-red-700">
          <AlertTriangle className="w-3.5 h-3.5" />
          <span>Could not read {silent === 'error' && fake === 'error' ? 'these counts' : 'one count'} - this is not a zero.</span>
          <button onClick={() => load()} className="btn sm ml-auto">Retry</button>
        </div>
      )}
      <p className="text-[11px] text-gray-400 mt-2">
        Stock-count variances raise tasks automatically on count completion.
      </p>
    </div>
  );
}

export default SystemIntegrityPanel;
