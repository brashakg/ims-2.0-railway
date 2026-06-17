// ============================================================================
// IMS 2.0 - Contact-lens refill-due worklist (CRM-2 phase 2)
// ============================================================================
// An IN-APP work-list of customers whose contact-lens refill is due (or
// overdue). Staff call them to reorder. "Create reminders" turns the list into
// deduped in-app follow-up tasks (the SAME task engine the SLA reminders use,
// so they ride the bell + escalation). This is dark on the customer side: NO
// WhatsApp/SMS is ever sent from here.

import { useCallback, useEffect, useState } from 'react';
import { Loader2, RefreshCw, BellPlus, Eye } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { crmApi } from '../../services/api/crm';
import type { CLRefillRow } from '../../services/api/crm';

function prettyDate(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso + 'T00:00:00');
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function CLRefillWorklistPage() {
  const { user } = useAuth();
  const { success, error: toastError, info } = useToast();
  const roles = user?.roles || [];
  const canCreate = roles.some((r) =>
    ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'].includes(r),
  );

  const [storeId, setStoreId] = useState<string>(user?.activeStoreId || '');
  const [dueWithin, setDueWithin] = useState(14);
  const [rows, setRows] = useState<CLRefillRow[]>([]);
  const [overdueCount, setOverdueCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    if (!storeId) return;
    setLoading(true);
    try {
      const res = await crmApi.getCLRefillWorklist(storeId, dueWithin);
      setRows(res.items || []);
      setOverdueCount(res.overdue_count || 0);
    } catch {
      toastError('Could not load the refill worklist.');
    } finally {
      setLoading(false);
    }
  }, [storeId, dueWithin, toastError]);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreateReminders = useCallback(async () => {
    if (!storeId) return;
    setCreating(true);
    try {
      const res = await crmApi.createCLRefillReminders(storeId, { dueWithinDays: dueWithin });
      if (res.created > 0) {
        success(`Created ${res.created} follow-up task${res.created === 1 ? '' : 's'}.`);
      } else {
        info(
          res.deduped > 0
            ? `No new tasks — ${res.deduped} already exist for these refills.`
            : 'No refills due in this window.',
        );
      }
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response
        ?.data?.detail;
      toastError(detail ? `Could not create reminders: ${detail}` : 'Could not create reminders.');
    } finally {
      setCreating(false);
    }
  }, [storeId, dueWithin, success, info, toastError]);

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <Eye className="w-6 h-6 text-bv-red-600" />
          <h1 className="text-2xl font-display font-semibold text-gray-900">
            Contact-Lens Refill Due
          </h1>
        </div>
        <p className="text-sm text-gray-500">
          Customers whose contact-lens supply is running out. Call them to reorder. No
          message is sent automatically.
        </p>
      </header>

      <div className="card p-5 space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Store</label>
            <input
              className="input-field w-40"
              value={storeId}
              onChange={(e) => setStoreId(e.target.value)}
              placeholder="Store ID"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Due within (days)</label>
            <input
              className="input-field w-28"
              type="number"
              min={0}
              max={120}
              value={dueWithin}
              onChange={(e) => setDueWithin(parseInt(e.target.value, 10) || 0)}
            />
          </div>
          <button
            className="btn-secondary flex items-center gap-2"
            onClick={load}
            disabled={loading || !storeId}
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </button>
          {canCreate && (
            <button
              className="btn-primary flex items-center gap-2 ml-auto"
              onClick={handleCreateReminders}
              disabled={creating || !storeId || rows.length === 0}
            >
              {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <BellPlus className="w-4 h-4" />}
              Create follow-up tasks
            </button>
          )}
        </div>

        <div className="flex gap-4 text-sm text-gray-500">
          <span>
            <span className="font-semibold text-gray-900">{rows.length}</span> due
          </span>
          <span>
            <span className="font-semibold text-bv-red-600">{overdueCount}</span> overdue
          </span>
        </div>
      </div>

      <div className="card overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16 text-gray-400">
            <Loader2 className="w-6 h-6 animate-spin" />
          </div>
        ) : rows.length === 0 ? (
          <div className="py-16 text-center text-sm text-gray-400">
            No contact-lens refills due in this window.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-3 text-left">Customer</th>
                <th className="px-4 py-3 text-left">Due</th>
                <th className="px-4 py-3 text-left">Status</th>
                <th className="px-4 py-3 text-left">SKU / Modality</th>
                <th className="px-4 py-3 text-left">Last order</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((r) => (
                <tr key={r.customer_id} className={r.overdue ? 'bg-red-50/40' : ''}>
                  <td className="px-4 py-3 text-gray-800">
                    {r.customer_name || r.customer_id}
                  </td>
                  <td className="px-4 py-3 text-gray-600">{prettyDate(r.refill_due_date)}</td>
                  <td className="px-4 py-3">
                    {r.overdue ? (
                      <span className="rounded bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                        Overdue {Math.abs(r.days_remaining)}d
                      </span>
                    ) : (
                      <span className="rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">
                        Due in {r.days_remaining}d
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-gray-500">
                    {(r.sku || '—')}
                    {r.modality ? ` · ${r.modality}` : ''}
                  </td>
                  <td className="px-4 py-3 text-gray-400">{prettyDate(r.last_cl_order_date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default CLRefillWorklistPage;
