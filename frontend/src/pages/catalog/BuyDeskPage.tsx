// ============================================================================
// IMS 2.0 - Buy Desk (the one-screen catalog -> purchase landing)
// ============================================================================
// One table, every catalogued product: is the catalog DONE (readiness), its
// honest Online Store state, on-hand + on-order stock, and a netted buy signal.
// "Purchase" unlocks the moment a product is catalog-complete (purchasable).
// Read-only data (GET /buy-desk/rows); the Purchase action routes to the
// existing Purchase module. Restrained/neutral styling, one accent.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Loader2,
  Search,
  RefreshCw,
  ShoppingCart,
  AlertTriangle,
  CheckCircle2,
  Lock,
} from 'lucide-react';
import { buyDeskApi, type BuyDeskRow, type EcomState } from '../../services/api/buyDesk';

const ECOM_LABEL: Record<EcomState, string> = {
  NOT_LISTED: 'Not listed',
  STAGED: 'Staged',
  LIVE: 'Live',
  PUSH_LOCKED: 'Push-locked',
};

function readinessChip(row: BuyDeskRow) {
  if (row.readiness.purchasable) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-green-50 px-2 py-0.5 text-xs font-medium text-green-700">
        <CheckCircle2 className="h-3 w-3" /> Ready
      </span>
    );
  }
  const gaps = [...(row.readiness.blockers || []), ...(row.readiness.missing || [])];
  const label = gaps.length ? `Missing: ${gaps.slice(0, 3).join(', ')}${gaps.length > 3 ? '…' : ''}` : 'Incomplete';
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700"
      title={gaps.join(', ')}
    >
      <AlertTriangle className="h-3 w-3" /> {label}
    </span>
  );
}

function ecomChip(state: EcomState) {
  const tone =
    state === 'LIVE'
      ? 'bg-green-50 text-green-700'
      : state === 'PUSH_LOCKED'
        ? 'bg-red-50 text-red-700'
        : state === 'STAGED'
          ? 'bg-blue-50 text-blue-700'
          : 'bg-gray-100 text-gray-600';
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>
      {state === 'PUSH_LOCKED' && <Lock className="h-3 w-3" />}
      {ECOM_LABEL[state]}
    </span>
  );
}

export default function BuyDeskPage() {
  const [rows, setRows] = useState<BuyDeskRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await buyDeskApi.getRows({ limit: 500 });
      setRows(resp.rows || []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load the Buy Desk');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) =>
      [r.sku, r.name, r.brand, r.category].some((v) => (v || '').toLowerCase().includes(q)),
    );
  }, [rows, query]);

  const readyCount = useMemo(() => rows.filter((r) => r.purchasable).length, [rows]);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Buy Desk</h1>
          <p className="text-sm text-gray-500">
            Every product: catalog status, online state, stock, and what to buy — purchase unlocks
            the moment the catalog is complete.
          </p>
        </div>
        <button
          onClick={() => void load()}
          className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
        >
          <RefreshCw className="h-4 w-4" /> Refresh
        </button>
      </div>

      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-md">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search SKU, brand, name, category…"
            className="input-field pl-9"
          />
        </div>
        <div className="text-sm text-gray-500">
          {rows.length} products · <span className="font-medium text-green-700">{readyCount} ready to buy</span>
        </div>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center gap-2 p-12 text-gray-500">
            <Loader2 className="h-5 w-5 animate-spin" /> Loading…
          </div>
        ) : error ? (
          <div className="p-8 text-center text-red-600">{error}</div>
        ) : filtered.length === 0 ? (
          <div className="p-12 text-center text-gray-500">
            {rows.length === 0 ? (
              <>
                <p className="font-medium text-gray-700">No products yet</p>
                <p className="mt-1 text-sm">
                  Add products in{' '}
                  <Link to="/catalog/add" className="text-blue-600 hover:underline">
                    Catalog
                  </Link>{' '}
                  — they appear here ready to purchase once complete.
                </p>
              </>
            ) : (
              'No products match your search.'
            )}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-2.5">Product</th>
                <th className="px-4 py-2.5">Catalog</th>
                <th className="px-4 py-2.5">Online</th>
                <th className="px-4 py-2.5 text-right">On hand</th>
                <th className="px-4 py-2.5 text-right">On order</th>
                <th className="px-4 py-2.5 text-right">Buy</th>
                <th className="px-4 py-2.5 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((r) => (
                <tr key={r.product_id} className="hover:bg-gray-50">
                  <td className="px-4 py-2.5">
                    <div className="font-medium text-gray-900">{r.name || r.sku || r.product_id}</div>
                    <div className="text-xs text-gray-500">
                      {[r.brand, r.category, r.sku].filter(Boolean).join(' · ')}
                    </div>
                  </td>
                  <td className="px-4 py-2.5">{readinessChip(r)}</td>
                  <td className="px-4 py-2.5">{ecomChip(r.ecom_state)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{r.on_hand}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-gray-500">{r.on_order}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums font-semibold text-gray-900">
                    {r.buy_signal === null ? '—' : r.buy_signal}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    {r.purchasable ? (
                      <Link
                        to="/purchase"
                        className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
                      >
                        <ShoppingCart className="h-3.5 w-3.5" /> Purchase
                      </Link>
                    ) : (
                      <span
                        className="inline-flex items-center gap-1 rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-400"
                        title="Finish cataloguing this product to purchase it"
                      >
                        <ShoppingCart className="h-3.5 w-3.5" /> Purchase
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
