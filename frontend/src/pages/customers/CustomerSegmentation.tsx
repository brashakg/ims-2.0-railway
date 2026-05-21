// ============================================================================
// IMS 2.0 - Customer Segmentation Page
// ============================================================================
// RFM Analysis: Champions, Loyal, Big Spenders, At Risk, Lost customer segments.
// Data is REAL — computed by GET /crm/customers/segment/rfm from the orders
// collection (recency / frequency / monetary). This page previously rendered a
// hardcoded SEGMENTS array with fabricated counts (145 Champions, 94% retention,
// 320% ROI, etc.) and a fake store filter — all removed.

import { useState, useEffect } from 'react';
import { Users, Loader2, BarChart3 } from 'lucide-react';
import clsx from 'clsx';
import api from '../../services/api/client';

interface RfmSegment {
  segment_id: string;
  segment_name: string;
  customer_count: number;
  avg_lifetime_value: number;
  description: string;
}

// Visual config only (not data). Keyed by the backend segment_id.
const SEGMENT_STYLE: Record<string, { color: string; bar: string }> = {
  champions: { color: 'text-green-600', bar: 'bg-green-500' },
  loyal: { color: 'text-blue-600', bar: 'bg-blue-500' },
  big_spenders: { color: 'text-yellow-600', bar: 'bg-yellow-500' },
  at_risk: { color: 'text-orange-600', bar: 'bg-orange-500' },
  lost: { color: 'text-red-600', bar: 'bg-red-500' },
};
const styleFor = (id: string) =>
  SEGMENT_STYLE[id] || { color: 'text-gray-600', bar: 'bg-gray-400' };

export function CustomerSegmentation() {
  const [segments, setSegments] = useState<RfmSegment[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await api.get('/crm/customers/segment/rfm');
        setSegments(Array.isArray(res.data) ? res.data : []);
      } catch {
        setSegments([]);
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  const totalCustomers = segments.reduce((s, x) => s + (x.customer_count || 0), 0);
  const totalValue = segments.reduce(
    (s, x) => s + (x.avg_lifetime_value || 0) * (x.customer_count || 0),
    0
  );

  return (
    <div className="inv-body">
      {/* Editorial header */}
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>CRM · Segmentation</div>
          <h1>Who to talk to, about what.</h1>
          <div className="hint">RFM segments computed from real purchase history: Champions, Loyal, Big Spenders, At Risk, Lost.</div>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
        </div>
      ) : totalCustomers === 0 ? (
        <div className="bg-white border border-gray-200 rounded-lg p-10 text-center text-gray-500">
          <BarChart3 className="w-12 h-12 mx-auto mb-3 opacity-40" />
          <p className="font-medium text-gray-700">No segmented customers yet</p>
          <p className="text-sm">RFM segments appear once customers have recorded purchases.</p>
        </div>
      ) : (
        <>
          {/* Summary Stats */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-gray-500 text-sm mb-1">Segmented Customers</p>
              <p className="text-2xl font-bold text-gray-900">{totalCustomers}</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-gray-500 text-sm mb-1">Total Value</p>
              <p className="text-2xl font-bold text-green-600">₹{(totalValue / 100000).toFixed(1)}L</p>
            </div>
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <p className="text-gray-500 text-sm mb-1">Avg Customer LTV</p>
              <p className="text-2xl font-bold text-blue-600">
                ₹{(totalValue / totalCustomers).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </p>
            </div>
          </div>

          {/* Segment Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {segments.map((segment) => {
              const st = styleFor(segment.segment_id);
              const pct = totalCustomers ? (segment.customer_count / totalCustomers) * 100 : 0;
              return (
                <div key={segment.segment_id} className="rounded-lg p-6 border bg-white border-gray-200">
                  <div className="flex items-start justify-between mb-4">
                    <div>
                      <h3 className={clsx('text-xl font-bold mb-1', st.color)}>{segment.segment_name}</h3>
                      <p className="text-gray-500 text-sm">{segment.description}</p>
                    </div>
                    <Users className={clsx('w-6 h-6', st.color)} />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-gray-500 text-xs mb-1">Customers</p>
                      <p className="text-2xl font-bold text-gray-900">{segment.customer_count}</p>
                      <p className={clsx('text-xs', st.color)}>{pct.toFixed(1)}% of segmented</p>
                    </div>
                    <div>
                      <p className="text-gray-500 text-xs mb-1">Avg LTV</p>
                      <p className="text-2xl font-bold text-green-600">
                        ₹{(segment.avg_lifetime_value / 1000).toFixed(1)}K
                      </p>
                      <p className="text-xs text-gray-500">per customer</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Segment Distribution Chart */}
          <div className="bg-white border border-gray-200 rounded-lg p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Customer Distribution</h3>
            <div className="space-y-3">
              {segments.map((segment) => {
                const st = styleFor(segment.segment_id);
                const percentage = totalCustomers ? (segment.customer_count / totalCustomers) * 100 : 0;
                return (
                  <div key={segment.segment_id}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm text-gray-500">{segment.segment_name}</span>
                      <span className={clsx('text-sm font-semibold', st.color)}>
                        {segment.customer_count} ({percentage.toFixed(1)}%)
                      </span>
                    </div>
                    <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
                      <div className={clsx('h-full', st.bar)} style={{ width: `${percentage}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default CustomerSegmentation;
