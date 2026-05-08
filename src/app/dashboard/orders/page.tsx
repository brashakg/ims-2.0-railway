'use client';

import { useState, useEffect, Fragment } from 'react';
import Link from 'next/link';
import { ChevronDown, ChevronUp, Loader2, RefreshCw, Search } from 'lucide-react';
import Topbar from '@/components/Topbar';

type Segment = 'all' | 'unfulfilled' | 'unpaid' | 'open' | 'closed' | 'cancelled';

const SEGMENTS: Array<{ key: Segment; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'unfulfilled', label: 'Unfulfilled' },
  { key: 'unpaid', label: 'Unpaid' },
  { key: 'open', label: 'Open' },
  { key: 'closed', label: 'Closed' },
  { key: 'cancelled', label: 'Cancelled' },
];

interface Order {
  id: string;
  orderNumber: string;
  customerId: string;
  customerName: string;
  customerEmail: string;
  totalPrice: number;
  financialStatus: 'PAID' | 'PENDING' | 'REFUNDED';
  fulfillmentStatus: 'FULFILLED' | 'PARTIAL' | 'UNFULFILLED';
  createdAt: string;
  lineItems: Array<{
    id: string;
    title: string;
    quantity: number;
    price: number;
  }>;
}

interface Stats {
  totalOrders: number;
  openOrders: number;
  closedOrders: number;
  totalRevenue: number;
}

interface ExpandedOrder {
  [key: string]: boolean;
}

export default function OrdersPage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [search, setSearch] = useState('');
  const [segment, setSegment] = useState<Segment>('all');
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [expandedOrders, setExpandedOrders] = useState<ExpandedOrder>({});
  const [error, setError] = useState<string | null>(null);

  const ITEMS_PER_PAGE = 10;

  // Fetch stats
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await fetch('/api/orders/stats');
        if (!res.ok) throw new Error('Failed to fetch stats');
        const data = await res.json();
        setStats(data.data || data);
      } catch (err) {
        console.error('Error fetching stats:', err);
        setError('Failed to load stats');
      }
    };
    fetchStats();
  }, []);

  // Fetch orders
  useEffect(() => {
    const fetchOrders = async () => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          page: page.toString(),
          limit: ITEMS_PER_PAGE.toString(),
          segment,
          ...(search && { search }),
        });
        const res = await fetch(`/api/orders?${params}`);
        if (!res.ok) throw new Error('Failed to fetch orders');
        const data = await res.json();
        setOrders(data.data || data.orders || []);
        setTotalPages(Math.ceil((data.pagination?.total || data.total || 0) / ITEMS_PER_PAGE));
      } catch (err) {
        console.error('Error fetching orders:', err);
        setError('Failed to load orders');
      } finally {
        setLoading(false);
      }
    };
    fetchOrders();
  }, [page, search, segment]);

  const handleSyncOrders = async () => {
    setSyncing(true);
    setError(null);
    try {
      const res = await fetch('/api/orders/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!res.ok) throw new Error('Failed to sync orders');
      
      // Refresh stats and orders
      const statsRes = await fetch('/api/orders/stats');
      const statsData = await statsRes.json();
      setStats(statsData.data || statsData);

      // Refresh current page
      const params = new URLSearchParams({
        page: page.toString(),
        limit: ITEMS_PER_PAGE.toString(),
        segment,
        ...(search && { search }),
      });
      const ordersRes = await fetch(`/api/orders?${params}`);
      const ordersData = await ordersRes.json();
      setOrders(ordersData.data || ordersData.orders || []);
    } catch (err) {
      console.error('Error syncing orders:', err);
      setError('Failed to sync orders');
    } finally {
      setSyncing(false);
    }
  };

  const toggleOrderExpand = (orderId: string) => {
    setExpandedOrders((prev) => ({
      ...prev,
      [orderId]: !prev[orderId],
    }));
  };

  const getFinancialStatusBadge = (status: string) => {
    const styles: Record<string, string> = {
      PAID: 'bg-green-100 text-green-800',
      PENDING: 'bg-yellow-100 text-yellow-800',
      REFUNDED: 'bg-red-100 text-red-800',
    };
    return styles[status] || 'bg-gray-100 text-gray-800';
  };

  const getFulfillmentStatusBadge = (status: string) => {
    const styles: Record<string, string> = {
      FULFILLED: 'bg-green-100 text-green-800',
      PARTIAL: 'bg-yellow-100 text-yellow-800',
      UNFULFILLED: 'bg-gray-100 text-gray-800',
    };
    return styles[status] || 'bg-gray-100 text-gray-800';
  };

  return (
    <>
      <Topbar
        title="Orders"
        subtitle="Read-only sync from Shopify"
        breadcrumb={[{ label: 'Home', href: '/dashboard' }, { label: 'Orders' }]}
        primaryAction={null}
        actions={
          <button
            type="button"
            onClick={handleSyncOrders}
            disabled={syncing}
            className="polaris-btn"
          >
            {syncing ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <RefreshCw className="w-3.5 h-3.5" />
            )}
            Sync from Shopify
          </button>
        }
      />
      <div style={{ padding: 24, maxWidth: 1400, margin: '0 auto' }}>

        {/* Stats Cards — Polaris polaris-card with tabular-nums KPI numbers */}
        {stats && (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
            {[
              { label: 'Total orders', value: stats.totalOrders, tone: 'default' },
              { label: 'Open', value: stats.openOrders, tone: 'default' },
              { label: 'Closed', value: stats.closedOrders, tone: 'success' },
              {
                label: 'Total revenue',
                value: `₹${(stats.totalRevenue || 0).toLocaleString('en-IN')}`,
                tone: 'default',
              },
            ].map((c) => (
              <div key={c.label} className="polaris-card" style={{ padding: 14 }}>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: 'var(--text-tertiary)',
                    textTransform: 'uppercase',
                    letterSpacing: 0.4,
                  }}
                >
                  {c.label}
                </div>
                <div
                  className="tabular-nums"
                  style={{
                    fontSize: 24,
                    fontWeight: 600,
                    letterSpacing: -0.4,
                    marginTop: 4,
                    color:
                      c.tone === 'success' ? 'var(--success-text)' : 'var(--text)',
                  }}
                >
                  {c.value}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Error banner */}
        {error && (
          <div
            className="polaris-card mb-3"
            style={{
              padding: 12,
              background: 'var(--critical-bg)',
              borderColor: 'var(--critical)',
              color: 'var(--critical-text)',
              fontSize: 13,
            }}
          >
            {error}
          </div>
        )}

        {/* Filter rail — Polaris saved-views chip pattern, not a bordered card */}
        <div className="flex items-center gap-2 flex-wrap mb-3">
          {SEGMENTS.map((s) => {
            const active = segment === s.key;
            return (
              <button
                key={s.key}
                type="button"
                onClick={() => {
                  setSegment(s.key);
                  setPage(1);
                }}
                className="polaris-btn polaris-btn-sm"
                style={{
                  background: active ? 'var(--text)' : 'var(--bg-surface)',
                  color: active ? 'white' : 'var(--text)',
                  borderColor: active
                    ? 'var(--text)'
                    : 'var(--border-strong)',
                  fontWeight: active ? 600 : 500,
                }}
              >
                {s.label}
              </button>
            );
          })}
        </div>

        {/* Search */}
        <div
          className="polaris-card mb-3"
          style={{ padding: 12, display: 'flex', gap: 8, alignItems: 'center' }}
        >
          <div className="relative flex-1">
            <Search
              className="absolute left-3 top-2 text-slate-400"
              size={14}
            />
            <input
              type="text"
              placeholder="Search by order number or customer…"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              style={{
                width: '100%',
                padding: '6px 10px 6px 32px',
                border: '1px solid var(--border-strong)',
                borderRadius: 8,
                fontSize: 13,
                height: 32,
              }}
            />
          </div>
        </div>

        {/* Orders table — Polaris polaris-table primitive */}
        <div className="polaris-card" style={{ overflow: 'hidden' }}>
          {loading ? (
            <div
              style={{
                padding: 32,
                textAlign: 'center',
                color: 'var(--text-tertiary)',
                fontSize: 13,
              }}
            >
              <Loader2
                className="animate-spin inline-block mr-2"
                size={14}
              />
              Loading…
            </div>
          ) : orders.length === 0 ? (
            <div
              style={{
                padding: 32,
                textAlign: 'center',
                color: 'var(--text-tertiary)',
                fontSize: 13,
              }}
            >
              No orders match this filter.
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="polaris-table">
                  <thead>
                    <tr>
                      <th>Order #</th>
                      <th>Customer</th>
                      <th>Items</th>
                      <th
                        className="tabular-nums"
                        style={{ textAlign: 'right' }}
                      >
                        Total
                      </th>
                      <th>Financial</th>
                      <th>Fulfillment</th>
                      <th>Date</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((order) => {
                      const finTone =
                        order.financialStatus === 'PAID'
                          ? 'polaris-badge polaris-badge-success'
                          : order.financialStatus === 'REFUNDED'
                            ? 'polaris-badge polaris-badge-critical'
                            : 'polaris-badge polaris-badge-warning';
                      const fulfilTone =
                        order.fulfillmentStatus === 'FULFILLED'
                          ? 'polaris-badge polaris-badge-success'
                          : order.fulfillmentStatus === 'PARTIAL'
                            ? 'polaris-badge polaris-badge-warning'
                            : 'polaris-badge';
                      return (
                        <Fragment key={order.id}>
                          <tr>
                            <td>
                              <Link
                                href={`/dashboard/orders/${order.id}`}
                                style={{
                                  fontWeight: 500,
                                  color: 'var(--brand-text)',
                                  textDecoration: 'none',
                                }}
                                className="hover:underline"
                              >
                                #{order.orderNumber}
                              </Link>
                            </td>
                            <td>{order.customerName}</td>
                            <td style={{ color: 'var(--text-secondary)' }}>
                              {(order.lineItems || []).length} item
                              {(order.lineItems || []).length === 1 ? '' : 's'}
                            </td>
                            <td
                              className="tabular-nums"
                              style={{ textAlign: 'right', fontWeight: 500 }}
                            >
                              ₹{(order.totalPrice || 0).toFixed(2)}
                            </td>
                            <td>
                              <span className={finTone}>
                                {order.financialStatus}
                              </span>
                            </td>
                            <td>
                              <span className={fulfilTone}>
                                {order.fulfillmentStatus}
                              </span>
                            </td>
                            <td
                              style={{
                                color: 'var(--text-tertiary)',
                                fontSize: 12,
                              }}
                            >
                              {order.createdAt
                                ? new Date(order.createdAt).toLocaleDateString(
                                    'en-IN',
                                    { month: 'short', day: 'numeric' }
                                  )
                                : '—'}
                            </td>
                            <td>
                              <button
                                type="button"
                                onClick={() => toggleOrderExpand(order.id)}
                                aria-label={
                                  expandedOrders[order.id]
                                    ? 'Collapse line items'
                                    : 'Expand line items'
                                }
                                className="polaris-btn polaris-btn-icon"
                              >
                                {expandedOrders[order.id] ? (
                                  <ChevronUp size={14} />
                                ) : (
                                  <ChevronDown size={14} />
                                )}
                              </button>
                            </td>
                          </tr>
                          {expandedOrders[order.id] && (
                            <tr>
                              <td
                                colSpan={8}
                                style={{
                                  background: 'var(--bg-surface-secondary)',
                                  padding: 12,
                                }}
                              >
                                <div
                                  style={{
                                    fontWeight: 600,
                                    fontSize: 12,
                                    marginBottom: 6,
                                    color: 'var(--text-secondary)',
                                  }}
                                >
                                  Line items
                                </div>
                                <div className="space-y-1">
                                  {order.lineItems.map((item) => (
                                    <div
                                      key={item.id}
                                      className="flex justify-between"
                                      style={{ fontSize: 12 }}
                                    >
                                      <span style={{ color: 'var(--text)' }}>
                                        {item.title}
                                      </span>
                                      <span
                                        className="tabular-nums"
                                        style={{
                                          color: 'var(--text-secondary)',
                                        }}
                                      >
                                        Qty {item.quantity} · ₹
                                        {(item.price || 0).toFixed(2)}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Pagination — Polaris compact footer */}
              <div
                style={{
                  padding: '8px 16px',
                  borderTop: '1px solid var(--border-subdued)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  fontSize: 12,
                  color: 'var(--text-tertiary)',
                }}
              >
                <span>
                  Page {page} of {totalPages}
                </span>
                <div className="flex gap-1">
                  <button
                    type="button"
                    onClick={() => setPage(Math.max(1, page - 1))}
                    disabled={page === 1}
                    className="polaris-btn polaris-btn-sm"
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    onClick={() => setPage(Math.min(totalPages, page + 1))}
                    disabled={page === totalPages}
                    className="polaris-btn polaris-btn-sm"
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
