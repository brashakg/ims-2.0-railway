// ============================================================================
// IMS 2.0 - Online vs In-store Stock (prevent overselling)
// ============================================================================
// Compares in-store physical on-hand (IMS) with online-listed stock (a live
// Shopify read via the IMS catalog mapping) per SKU, location by location,
// flags overselling risk, and shows what IMS sends to the website
// ("Recommended": each mapped shop's shelf minus the Shopify integration's
// safety buffer, and 0 for a product blocked from online sale or no longer
// sold -- the writer's own number; this page has no second buffer).

import { useCallback, useEffect, useState } from 'react';
import { RefreshCcw, Loader2, AlertTriangle, CheckCircle2, ShoppingCart } from 'lucide-react';
import { onlineStockApi, type ReconcileItem, type ReconcileResult } from '../../services/api/onlineStock';
import { storeApi } from '../../services/api/stores';
import { useToast } from '../../context/ToastContext';
import { SHARES_ITEM_LABEL } from './sharedItem';

interface StoreOpt { store_id: string; store_name?: string; store_code?: string; }

const STATUS_STYLE: Record<string, string> = {
  OVERSELL_RISK: 'bg-red-100 text-red-800 border-red-200',
  OVER_ALLOCATED: 'bg-amber-100 text-amber-800 border-amber-200',
  ONHAND_UNKNOWN: 'bg-blue-50 text-blue-700 border-blue-200',
  LISTED_UNKNOWN: 'bg-blue-50 text-blue-700 border-blue-200',
  OK: 'bg-green-100 text-green-800 border-green-200',
  SHARES_SHOPIFY_ITEM: 'bg-blue-50 text-blue-700 border-blue-200',
  NOT_ONLINE: 'bg-gray-100 text-gray-500 border-gray-200',
};
const STATUS_LABEL: Record<string, string> = {
  OVERSELL_RISK: 'Oversell risk',
  OVER_ALLOCATED: 'Over-allocated',
  ONHAND_UNKNOWN: 'On-hand unknown',
  LISTED_UNKNOWN: 'Unverified',
  OK: 'OK',
  SHARES_SHOPIFY_ITEM: SHARES_ITEM_LABEL,
  NOT_ONLINE: 'Not online',
};

// A row whose live Shopify number is not the one IMS sends is the row a drift
// task names (parity files drift both ways), so "Show only at-risk" keeps it:
// an under-listed SKU is status OK / delta 0 and would otherwise be hidden
// behind "No overselling risk" while the task sends the manager to it.
function listedOffRecommended(i: ReconcileItem): boolean {
  return typeof i.online === 'number' && typeof i.recommended === 'number' && i.online !== i.recommended;
}

export default function OnlineStockPage() {
  const toast = useToast();
  const [data, setData] = useState<ReconcileResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [storeId, setStoreId] = useState('');
  const [stores, setStores] = useState<StoreOpt[]>([]);
  const [onlyRisk, setOnlyRisk] = useState(true);

  useEffect(() => {
    storeApi.getStores().then((r) => setStores(r?.stores || [])).catch(() => setStores([]));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await onlineStockApi.reconcile({ store_id: storeId || undefined }));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load reconciliation');
    } finally { setLoading(false); }
  }, [storeId, toast]);

  useEffect(() => { load(); }, [load]);

  const s = data?.summary || {};
  const items = (data?.items || []).filter((i) =>
    onlyRisk ? (i.status === 'OVERSELL_RISK' || i.status === 'OVER_ALLOCATED' || i.status === 'SHARES_SHOPIFY_ITEM' || listedOffRecommended(i)) : true,
  );
  // An empty table never claims "no risk" for rows nobody could verify:
  // nothing verified (the live-listing read failed, or every row unknown)
  // vs some rows unverified (partial coverage) vs truly clear.
  const unverified = (s.listed_unknown ?? 0) + (s.onhand_unknown ?? 0);
  const total = (data?.items || []).length;
  const emptyText = data?.live_listings_unknown || (total > 0 && unverified >= total)
    ? 'Nothing here could be verified right now — see the note above.'
    : !onlyRisk
      ? 'No products to show.'
      : unverified > 0
        ? `No overselling risk among the verified rows; ${unverified} could not be verified (see the note above).`
        : 'No overselling risk — everything is within safe allocation.';

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
          <ShoppingCart className="w-5 h-5" /> Online vs In-store Stock
        </h1>
        <button type="button" onClick={load} className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg px-3 py-1.5">
          <RefreshCcw className="w-4 h-4" /> Refresh
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-4">Stops you selling the same item online and in-store. "Recommended" = what IMS sends to the website: each shop's shelf minus the safety buffer{typeof s.safety_buffer === 'number' ? ` (${s.safety_buffer})` : ''}, for the shops mapped to a Shopify location, and 0 for a product a SUPERADMIN blocked from online sale or IMS no longer sells.</p>

      {data && data.online_configured === false && !data.live_listings_unknown && (
        <div className="flex items-center gap-2 text-sm rounded-lg px-3 py-2 border bg-blue-50 border-blue-200 text-blue-800 mb-4">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          No products are mapped to Shopify yet, so there are no online quantities to reconcile.
          Once products are pushed online, this page flags real overselling risk.
        </div>
      )}

      {data && (data.online_configured !== false || data.live_listings_unknown) && data.listed_qty_live === false && (
        <div className="flex items-center gap-2 text-sm rounded-lg px-3 py-2 border bg-amber-50 border-amber-200 text-amber-800 mb-4">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {data.live_listings_unknown
            ? 'IMS could not read which products are live on the website right now, so every row shows "Unverified" (never "Not online") and oversell flags can\'t fire. Refresh in a moment.'
            : (data.listed_live_rows ?? 0) > 0
            ? `Live Shopify quantities cover ${data.listed_live_rows} of ${data.listed_mapped_rows} online SKUs on this page. Rows marked "Unverified" (Online = —) were not covered and cannot be cleared as OK.`
            : `Live Shopify quantities are unavailable right now, so online SKUs show "Unverified" (Online = —) and oversell flags can't fire.${(s.onhand_unknown ?? 0) > 0 ? '' : ' On-hand and recommended numbers are live.'}`}
        </div>
      )}

      {data && (s.onhand_unknown ?? 0) > 0 && (
        <div className="flex items-center gap-2 text-sm rounded-lg px-3 py-2 border bg-amber-50 border-amber-200 text-amber-800 mb-4">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          IMS could not read the shops' on-hand right now, so {s.onhand_unknown} online SKU{s.onhand_unknown === 1 ? '' : 's'} show "On-hand unknown" (In-store = —) and are not classified — an unreadable shelf is never shown as 0. Refresh, or check the sync-health tile.
        </div>
      )}

      <div className="flex flex-wrap items-end gap-3 mb-4">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Store</label>
          <select aria-label="Filter by store" value={storeId} onChange={(e) => setStoreId(e.target.value)} className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm">
            <option value="">All stores</option>
            {stores.map((st) => <option key={st.store_id} value={st.store_id}>{st.store_name || st.store_code || st.store_id}</option>)}
          </select>
        </div>
        <label className="text-sm text-gray-600 flex items-center gap-1.5 pb-1.5">
          <input type="checkbox" checked={onlyRisk} onChange={(e) => setOnlyRisk(e.target.checked)} /> Show only at-risk
        </label>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading...</div>
      ) : (
        <>
          {!data?.live_listings_unknown && <div className="grid grid-cols-3 gap-3 mb-4">
            <div className="bg-white border border-red-200 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-1">Oversell risk</p>
              <p className="text-xl font-semibold text-red-700">{s.oversell_risk || 0}</p>
              <p className="text-xs text-gray-400 mt-1">{s.oversell_risk_units || 0} units listed beyond stock</p>
            </div>
            <div className="bg-white border border-amber-200 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-1">Over-allocated</p>
              <p className="text-xl font-semibold text-amber-700">{s.over_allocated || 0}</p>
              <p className="text-xs text-gray-400 mt-1">listed above what IMS sends</p>
            </div>
            <div className="bg-white border border-green-200 rounded-lg p-4">
              <p className="text-xs text-gray-500 mb-1 flex items-center gap-1"><CheckCircle2 className="w-3 h-3" /> OK</p>
              <p className="text-xl font-semibold text-green-700">{s.ok || 0}</p>
              <p className="text-xs text-gray-400 mt-1">within safe allocation</p>
            </div>
          </div>}

          <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-xs">
                <tr>
                  <th className="text-left px-3 py-2">SKU</th>
                  <th className="text-left px-3 py-2">Product</th>
                  <th className="text-right px-3 py-2">In-store</th>
                  <th className="text-right px-3 py-2">Online</th>
                  <th className="text-right px-3 py-2">Recommended</th>
                  <th className="text-left px-3 py-2">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {items.length === 0 ? (
                  <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-400">
                    {emptyText}
                  </td></tr>
                ) : items.map((it) => (
                  <tr key={it.sku} className={it.status === 'OVERSELL_RISK' ? 'bg-red-50/40' : ''}>
                    <td className="px-3 py-2 font-mono text-xs text-gray-700">{it.sku}</td>
                    <td className="px-3 py-2 text-gray-700">{it.name}</td>
                    <td className="px-3 py-2 text-right">{typeof it.in_store === 'number' ? it.in_store : '—'}</td>
                    <td className="px-3 py-2 text-right">{typeof it.online === 'number' ? it.online : '—'}</td>
                    <td className="px-3 py-2 text-right font-medium">{typeof it.recommended === 'number' ? it.recommended : '—'}</td>
                    <td className="px-3 py-2">
                      <span className={`inline-flex items-center text-xs border rounded-full px-2 py-0.5 ${STATUS_STYLE[it.status]}`}>
                        {STATUS_LABEL[it.status]}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-gray-400 mt-2">
            After every in-store sale or restock, IMS automatically pushes the reduced available
            quantity for mapped SKUs to Shopify (the oversell guard). "Online" is a live Shopify
            read when available.
          </p>
        </>
      )}
    </div>
  );
}
