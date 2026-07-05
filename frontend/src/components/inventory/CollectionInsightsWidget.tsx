// ============================================================================
// IMS 2.0 - Collection Insights (Inventory > Insights > Collections)
// ============================================================================
// KPI table per collection over the EXISTING batched summary endpoint
// (GET /collections/insights/summary — the same rollup the /collections page
// uses), so the two surfaces can never disagree. The summary provides:
// title, type, members, on-hand, stock value (+basis label), sold 30d.
// Sell-through and days-of-cover are derived CLIENT-SIDE from those two
// server fields with the exact backend formulas (collection_insights.
// sell_through / days_of_cover: sold/(sold+on_hand); on_hand/(sold/30)
// capped at 999) — no invented data, same math as the detail page.
// Row titles deep-link to /collections/:id (CollectionDetailPage).

import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Boxes } from 'lucide-react';
// Import DIRECT from the modules (not the api barrel — TS2614).
import {
  collectionsInsightsApi,
  type CollectionSummaryRow,
} from '../../services/api/collectionsInsights';
import { rupee, fmtInt, basisLabel, pct, daysOfCover } from '../../pages/collections/collectionsShared';

/** Same formula as backend collection_insights.sell_through: sold30 /
 *  (sold30 + on_hand) as a fraction; null when there is no signal. */
function sellThrough30(sold30: number, onHand: number): number | null {
  const sold = Math.max(sold30 || 0, 0);
  const hand = Math.max(onHand || 0, 0);
  const denom = sold + hand;
  if (denom === 0) return null;
  return sold / denom;
}

/** Same formula as backend collection_insights.days_of_cover: on_hand /
 *  (sold30/30), capped at 999; null when no stock AND no sales. */
function cover30(onHand: number, sold30: number): number | null {
  const sold = Math.max(sold30 || 0, 0);
  const hand = Math.max(onHand || 0, 0);
  if (hand === 0 && sold === 0) return null;
  if (sold === 0) return 999;
  return Math.min(hand / (sold / 30), 999);
}

export function CollectionInsightsWidget() {
  const [rows, setRows] = useState<CollectionSummaryRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      // Fail-soft service: errors -> [] (Track 2 backend contract).
      const data = await collectionsInsightsApi.summary(2000);
      if (!cancelled) {
        setRows(data);
        setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Active collections first (the summary is already sold_30d desc); keep
  // empty ones visible below so merchandisers still see dormant collections.
  const visible = [...rows].sort(
    (a, b) =>
      (b.sold_30d ?? 0) - (a.sold_30d ?? 0) ||
      (b.stock_value ?? 0) - (a.stock_value ?? 0) ||
      String(a.title ?? '').localeCompare(String(b.title ?? '')),
  );

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
      <div className="p-4 border-b border-gray-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Boxes className="w-5 h-5 text-blue-600" />
          <h3 className="font-semibold text-gray-900">Collection Insights (30d)</h3>
        </div>
        <Link to="/collections" className="text-sm text-blue-600 hover:underline">
          Open Collections
        </Link>
      </div>

      {loading ? (
        <div className="p-4 text-center text-gray-500">Loading...</div>
      ) : visible.length === 0 ? (
        <div className="p-4 text-center text-gray-500">No collections found</div>
      ) : (
        <div className="max-h-96 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="bg-white border-b border-gray-200 sticky top-0">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-semibold text-gray-500">Collection</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">Members</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">On hand</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">Stock value</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">Sold 30d</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">Sell-through</th>
                <th className="px-4 py-2 text-right text-xs font-semibold text-gray-500">Days cover</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((c) => {
                const st = sellThrough30(c.sold_30d ?? 0, c.on_hand ?? 0);
                const dc = cover30(c.on_hand ?? 0, c.sold_30d ?? 0);
                const basis = basisLabel(c.value_basis);
                return (
                  <tr key={c.collection_id} className="border-b border-gray-200 hover:bg-white">
                    <td className="px-4 py-2">
                      <Link
                        to={`/collections/${encodeURIComponent(c.collection_id)}`}
                        className="text-gray-900 font-medium hover:text-blue-600 hover:underline"
                      >
                        {c.title || c.collection_id}
                      </Link>
                      <span className="ml-2 text-[10px] uppercase tracking-wide text-gray-400">
                        {c.collection_type}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right text-gray-700">{fmtInt(c.members)}</td>
                    <td className="px-4 py-2 text-right text-gray-700">{fmtInt(c.on_hand)}</td>
                    <td className="px-4 py-2 text-right text-gray-700">
                      {rupee(c.stock_value)}
                      {basis && <span className="ml-1 text-[10px] text-gray-400">{basis}</span>}
                    </td>
                    <td className="px-4 py-2 text-right text-gray-900 font-medium">{fmtInt(c.sold_30d)}</td>
                    <td className="px-4 py-2 text-right">
                      {st === null ? (
                        <span className="text-gray-400">—</span>
                      ) : (
                        <div className="flex items-center justify-end gap-2">
                          <div className="w-16 bg-gray-100 rounded-full h-1.5">
                            <div
                              className="bg-green-500 h-1.5 rounded-full"
                              style={{ width: `${Math.min(st * 100, 100)}%` }}
                            />
                          </div>
                          <span className="text-green-600 font-semibold w-12 text-right">{pct(st)}</span>
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right text-gray-700">{daysOfCover(dc)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
