'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { ChevronDown, ChevronUp, Loader2, RefreshCw, Search } from 'lucide-react';

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
    <div className="min-h-screen bg-slate-50 p-4 sm:p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900 mb-2">Orders</h1>
          <p className="text-gray-600">Manage and track your orders</p>
        </div>

        {/* Stats Cards */}
        {stats && (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
            <div className="bg-white rounded-lg shadow p-6">
              <p className="text-gray-600 text-sm font-medium">Total Orders</p>
              <p className="text-3xl font-bold text-gray-900 mt-2">{stats.totalOrders}</p>
            </div>
            <div className="bg-white rounded-lg shadow p-6">
              <p className="text-gray-600 text-sm font-medium">Open</p>
              <p className="text-3xl font-bold text-blue-600 mt-2">{stats.openOrders}</p>
            </div>
            <div className="bg-white rounded-lg shadow p-6">
              <p className="text-gray-600 text-sm font-medium">Closed</p>
              <p className="text-3xl font-bold text-green-600 mt-2">{stats.closedOrders}</p>
            </div>
            <div className="bg-white rounded-lg shadow p-6">
              <p className="text-gray-600 text-sm font-medium">Total Revenue</p>
              <p className="text-3xl font-bold text-gray-900 mt-2">₹{(stats.totalRevenue || 0).toLocaleString('en-IN')}</p>
            </div>
          </div>
        )}

        {/* Error Message */}
        {error && (
          <div className="mb-6 bg-red-50 border border-red-200 rounded-lg p-4">
            <p className="text-red-800">{error}</p>
          </div>
        )}

        {/* Controls */}
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <div className="flex flex-col sm:flex-row gap-4 mb-4 items-start sm:items-center">
            <button
              onClick={handleSyncOrders}
              disabled={syncing}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-blue-400 transition"
            >
              {syncing ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <RefreshCw className="w-4 h-4" />
              )}
              Sync Orders from Shopify
            </button>
          </div>

          <div className="flex items-center gap-2 flex-wrap border-b border-gray-200 -mx-6 px-6 pb-3 mb-4">
            {SEGMENTS.map((s) => (
              <button
                key={s.key}
                onClick={() => {
                  setSegment(s.key);
                  setPage(1);
                }}
                className={`px-3 py-1.5 text-sm rounded-lg border transition-colors ${
                  segment === s.key
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>

          <div className="flex-1 relative">
            <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search by order number or customer..."
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
        </div>

        {/* Orders Table */}
        <div className="bg-white rounded-lg shadow overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
            </div>
          ) : orders.length === 0 ? (
            <div className="flex items-center justify-center py-12">
              <p className="text-gray-600">No orders found</p>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Order #</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Customer</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Items</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Total</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Financial Status</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Fulfillment</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Date</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {orders.map((order) => (
                      <tbody key={order.id}>
                        <tr className="hover:bg-gray-50">
                          <td className="px-6 py-4 text-sm font-medium">
                            <Link
                              href={`/dashboard/orders/${order.id}`}
                              className="text-blue-600 hover:underline"
                            >
                              #{order.orderNumber}
                            </Link>
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-900">{order.customerName}</td>
                          <td className="px-6 py-4 text-sm text-gray-600">{(order.lineItems || []).length} items</td>
                          <td className="px-6 py-4 text-sm font-medium text-gray-900">₹{(order.totalPrice || 0).toFixed(2)}</td>
                          <td className="px-6 py-4">
                            <span className={`inline-block px-3 py-1 text-xs font-medium rounded-full ${getFinancialStatusBadge(order.financialStatus)}`}>
                              {order.financialStatus}
                            </span>
                          </td>
                          <td className="px-6 py-4">
                            <span className={`inline-block px-3 py-1 text-xs font-medium rounded-full ${getFulfillmentStatusBadge(order.fulfillmentStatus)}`}>
                              {order.fulfillmentStatus}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-sm text-gray-600">{order.createdAt ? new Date(order.createdAt).toLocaleDateString() : '—'}</td>
                          <td className="px-6 py-4">
                            <button
                              onClick={() => toggleOrderExpand(order.id)}
                              aria-label={expandedOrders[order.id] ? 'Collapse line items' : 'Expand line items'}
                              className="p-1 rounded hover:bg-gray-200"
                            >
                              {expandedOrders[order.id] ? (
                                <ChevronUp className="w-4 h-4 text-gray-400" />
                              ) : (
                                <ChevronDown className="w-4 h-4 text-gray-400" />
                              )}
                            </button>
                          </td>
                        </tr>
                        {expandedOrders[order.id] && (
                          <tr className="bg-gray-50">
                            <td colSpan={8} className="px-6 py-4">
                              <div className="bg-white rounded border border-gray-200 p-4">
                                <h4 className="font-semibold text-gray-900 mb-3">Line Items</h4>
                                <div className="space-y-2">
                                  {order.lineItems.map((item) => (
                                    <div key={item.id} className="flex justify-between items-center text-sm">
                                      <span className="text-gray-700">{item.title}</span>
                                      <span className="text-gray-600">Qty: {item.quantity} × ₹{(item.price || 0).toFixed(2)}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </tbody>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-between px-6 py-4 border-t border-gray-200">
                <p className="text-sm text-gray-600">
                  Page {page} of {totalPages}
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setPage(Math.max(1, page - 1))}
                    disabled={page === 1}
                    className="px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => setPage(Math.min(totalPages, page + 1))}
                    disabled={page === totalPages}
                    className="px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50"
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
