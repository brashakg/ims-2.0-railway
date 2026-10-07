// ============================================================================
// IMS 2.0 - Stock lookup (owner ruling D7b, 2026-09-29)
// ============================================================================
// READ-ONLY. Counter staff search a frame by brand, model or SKU, or scan its
// barcode, and see how many the till can sell at this shop, at every other
// shop and on their way to each - every colour and eye size of the model. The
// server sends MRP and selling price only (GET /inventory/lookup builds its
// answer from an allow-list), so there is no cost to hide here. The price
// shown is the till's own (posPriceGuard), never a re-typed chain.

import { useRef, useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search, Loader2 } from 'lucide-react';
import api from '../../services/api/client';
import { apiDetailMessage } from '../../utils/errorHandler';
import { posPriceGuard } from '../../components/pos/productIntake';

interface ShopCount { store_id: string; store_name: string; available: number; in_transit: number }
interface LookupItem {
  product_id: string; sku?: string; name?: string; brand?: string; model?: string;
  color?: string; size?: string | number; mrp?: number; offer_price?: number;
  stores: ShopCount[];
}
interface LookupResult { store_id?: string; items: LookupItem[] }

const rupees = (n?: number) => (n == null ? '-' : `₹${n.toLocaleString('en-IN')}`);
/** What the till puts on the line; '-' where the till refuses the price. */
const tillPrice = (it: LookupItem) => {
  const g = posPriceGuard(it);
  return g.ok ? rupees(g.finalPrice) : '-';
};

export default function StockLookupPage() {
  const [text, setText] = useState('');
  const [q, setQ] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const { data, isFetching, error, refetch } = useQuery({
    queryKey: ['inventory', 'lookup', q],
    queryFn: async () => (await api.get<LookupResult>('/inventory/lookup', { params: { q } })).data,
    enabled: q.length > 0,
    staleTime: 0,
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next = text.trim();
    // The same barcode scanned again is the same query key, so setQ alone
    // would not ask again; a frame sold since must not still read 1.
    if (next && next === q) void refetch();
    else setQ(next);
    // A scanner types into the focused box and presses Enter: select what is
    // there so the next scan replaces it instead of being glued onto it.
    input.current?.select();
  };

  const items = data?.items ?? [];
  // This shop first, then the others in the server's (store code) order.
  const here = data?.store_id;
  const shops = [...(items[0]?.stores ?? [])].sort(
    (a, b) => Number(b.store_id === here) - Number(a.store_id === here),
  );

  return (
    <div className="p-4 md:p-6 max-w-6xl mx-auto">
      <h1 className="text-xl font-semibold text-gray-900">Stock lookup</h1>
      <p className="text-sm text-gray-500 mb-4">
        Search by brand, model or SKU, or scan the barcode. Shows what the till can sell now at every shop.
      </p>

      <form onSubmit={submit} className="flex gap-2 mb-4" role="search">
        <input
          ref={input}
          type="search"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="e.g. CA8895, Carrera, or scan"
          aria-label="Search stock"
          className="input-field flex-1 min-w-0"
          autoFocus
        />
        <button type="submit" className="btn primary" disabled={!text.trim()}>
          {isFetching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          Search
        </button>
      </form>

      {error ? (
        <p className="text-sm text-red-700">{apiDetailMessage(error, 'Could not look up stock. Try again.')}</p>
      ) : q && data && items.length === 0 ? (
        <p className="text-sm text-gray-600">No product matches "{q}".</p>
      ) : items.length > 0 ? (
        <div className="overflow-x-auto border border-gray-200 rounded-lg">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="px-3 py-2 font-medium min-w-[13rem]">Product</th>
                <th className="px-3 py-2 font-medium">Colour</th>
                <th className="px-3 py-2 font-medium">Size</th>
                <th className="px-3 py-2 font-medium text-right">MRP</th>
                <th className="px-3 py-2 font-medium text-right">Price</th>
                {shops.map((s) => (
                  <th key={s.store_id} className={`px-3 py-2 font-medium text-center ${s.store_id === here ? 'bg-blue-50 text-blue-900' : ''}`}>
                    {s.store_name}
                    {s.store_id === here && <div className="text-xs font-normal">this shop</div>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.map((it) => {
                const counts = new Map(it.stores.map((s) => [s.store_id, s]));
                return (
                  <tr key={it.product_id}>
                    <td className="px-3 py-2">
                      <div className="text-gray-900">{it.name || `${it.brand ?? ''} ${it.model ?? ''}`.trim()}</div>
                      <div className="text-xs text-gray-500">{it.sku}</div>
                    </td>
                    <td className="px-3 py-2">{it.color || '-'}</td>
                    <td className="px-3 py-2">{it.size || '-'}</td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">{rupees(it.mrp)}</td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">{tillPrice(it)}</td>
                    {shops.map((s) => {
                      const c = counts.get(s.store_id);
                      const n = c?.available ?? 0;
                      return (
                        <td key={s.store_id} className={`px-3 py-2 text-center ${s.store_id === here ? 'bg-blue-50' : ''}`}>
                          <span className={n > 0 ? 'font-semibold text-gray-900' : 'text-gray-400'}>{n}</span>
                          {(c?.in_transit ?? 0) > 0 && (
                            <div className="text-xs text-amber-700">+{c!.in_transit} on the way</div>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
