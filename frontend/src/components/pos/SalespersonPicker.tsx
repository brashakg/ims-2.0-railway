// ============================================================================
// IMS 2.0 - POS salesperson picker
// ============================================================================
// Extracted VERBATIM from POSLayout (Wave 4) so the new one-surface POS can
// reuse it without importing the whole classic layout. Owner spec 10: manager
// tier (Store Manager and up) may attribute a sale to ANOTHER salesperson;
// everyone below is auto-attributed to themselves and gets no picker.

import { useEffect, useState } from 'react';
import { usePOSStore } from '../../stores/posStore';
import { useAuth } from '../../context/AuthContext';
import { adminStoreApi } from '../../services/api';

export function SalespersonPicker({ compact = false }: { compact?: boolean } = {}) {
  const store = usePOSStore();
  const { user } = useAuth();
  const [people, setPeople] = useState<Array<{ id: string; name: string }>>([]);
  const [loading, setLoading] = useState(false);

  // Only manager-tier (Store Manager and up) may attribute a sale to ANOTHER
  // salesperson. Everyone below Store Manager (sales staff / cashier /
  // optometrist / workshop) is auto-attributed to themselves -- no picker.
  const MANAGER_TIER = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'];
  const canPick = (user?.roles || []).some((r: string) => MANAGER_TIER.includes(r));
  const selfId = user?.id || (user as any)?.user_id || '';
  const selfName =
    (user as any)?.name || (user as any)?.full_name || (user as any)?.username || 'You';

  // Below-manager: lock the salesperson to the logged-in user (no choice).
  useEffect(() => {
    if (!canPick && selfId && store.salesperson_id !== selfId) {
      store.setSalesperson(selfId, selfName);
    }
  }, [canPick, selfId, selfName, store.salesperson_id]);

  // Manager-tier only: load the store's sales-floor users for the dropdown.
  useEffect(() => {
    if (!canPick) return;
    const sid = store.store_id || user?.activeStoreId;
    if (!sid) return;
    let cancelled = false;
    setLoading(true);
    // Sales-attributable roles only. SUPERADMIN/ADMIN/AREA_MANAGER are
    // cross-store and never on the shop floor; ACCOUNTANT is back-office;
    // OPTOMETRIST runs the exam chamber not the till. Anyone else is a
    // future role we'll add by request.
    adminStoreApi
      .getStoreUsers(sid, {
        roles: ['STORE_MANAGER', 'SALES_STAFF', 'OPTICIAN', 'CASHIER'],
        activeOnly: true,
      })
      .then((r: any) => {
        if (cancelled) return;
        const list = (r?.users || r || []) as any[];
        const mapped = list
          .map((u) => ({
            id: u.user_id || u.id || u._id || u.username,
            name: u.name || u.full_name || u.username || u.user_id,
          }))
          .filter((u) => u.id);
        setPeople(mapped);
      })
      .catch(() => setPeople([]))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [canPick, store.store_id, user?.activeStoreId]);

  // Below Store Manager: no picker -- the sale is auto-attributed to the
  // logged-in user (set by the effect above). Read-only display.
  if (!canPick) {
    return (
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Salesperson</label>
        <div className="w-full px-3 py-2.5 border-2 border-gray-200 rounded-xl text-sm bg-gray-50 text-gray-700">
          {selfName} <span className="text-gray-400">(you)</span>
        </div>
      </div>
    );
  }

  // Compact mode: a chip-sized select for the new one-surface POS bill strip
  // (owner: the labelled block "takes up too much space"). Same data, same
  // manager-tier rule — only the chrome differs.
  if (compact) {
    return (
      <select
        aria-label="Salesperson"
        value={store.salesperson_id}
        onChange={(e) => {
          const p = people.find((x) => x.id === e.target.value);
          store.setSalesperson(e.target.value, p?.name || '');
        }}
        className={
          'h-9 max-w-[190px] rounded-lg border px-2 text-xs bg-white ' +
          (store.salesperson_id ? 'border-gray-200 text-gray-900' : 'border-amber-300 text-amber-700')
        }
      >
        <option value="">{loading ? 'Loading…' : 'Salesperson *'}</option>
        {people.map((p) => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>
    );
  }

  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-2">
        Salesperson <span className="text-red-500">*</span>
      </label>
      <select
        aria-label="Select salesperson"
        value={store.salesperson_id}
        onChange={(e) => {
          const p = people.find((x) => x.id === e.target.value);
          store.setSalesperson(e.target.value, p?.name || '');
        }}
        className="w-full px-3 py-2.5 border-2 border-gray-300 rounded-xl text-sm bg-white"
      >
        <option value="">{loading ? 'Loading staff…' : '— Select salesperson —'}</option>
        {people.map((p) => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>
      {!store.salesperson_id && (
        <p className="text-xs text-gray-500 mt-1">Required — pick who is handling this sale.</p>
      )}
    </div>
  );
}
