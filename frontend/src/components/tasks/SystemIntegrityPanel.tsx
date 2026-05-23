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

export function SystemIntegrityPanel({ storeId }: { storeId?: string }) {
  const { user } = useAuth();
  const toast = useToast();
  const isManager = (user?.roles || []).some((r) => MANAGER_ROLES.includes(r));

  const [silent, setSilent] = useState<number | null>(null);
  const [fake, setFake] = useState<number | null>(null);
  const [scanning, setScanning] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, f] = await Promise.all([
        tasksApi.getSilentTasks(storeId).catch(() => ({ count: 0 })),
        tasksApi.getFakeClosures(storeId).catch(() => ({ count: 0 })),
      ]);
      setSilent(s.count ?? 0);
      setFake(f.count ?? 0);
    } catch {
      /* fail-soft */
    }
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
          <div className="text-2xl font-bold text-amber-600">{silent ?? '—'}</div>
        </div>
        <div className="rounded-lg border border-gray-200 p-3">
          <div className="text-xs text-gray-500 flex items-center gap-1"><AlertTriangle className="w-3 h-3" /> Fast-closed (suspect)</div>
          <div className="text-2xl font-bold text-red-600">{fake ?? '—'}</div>
        </div>
      </div>
      <p className="text-[11px] text-gray-400 mt-2">
        Stock-count variances raise tasks automatically on count completion.
      </p>
    </div>
  );
}

export default SystemIntegrityPanel;
