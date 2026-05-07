'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { Loader2, Search, Check, X, RefreshCw } from 'lucide-react';
import Topbar from '@/components/Topbar';

type Segment = 'all' | 'subscribed' | 'has_orders' | 'no_orders';

const SEGMENTS: Array<{ key: Segment; label: string }> = [
  { key: 'all', label: 'All customers' },
  { key: 'has_orders', label: 'With orders' },
  { key: 'subscribed', label: 'Email subscribers' },
  { key: 'no_orders', label: 'No orders yet' },
];

interface Customer {
  id: string;
  name?: string;
  firstName?: string | null;
  lastName?: string | null;
  email: string | null;
  phone: string | null;
  city: string | null;
  ordersCount: number;
  totalSpent: number;
  acceptsMarketing: boolean;
}

function customerName(c: Customer): string {
  const full = [c.firstName, c.lastName].filter(Boolean).join(' ').trim();
  return c.name || full || c.email || c.phone || 'Unnamed';
}

interface Stats {
  totalCustomers: number;
  customersWithOrders: number;
  acceptsMarketing: number;
  avgOrderValue: number;
}

export default function CustomersPage() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [segment, setSegment] = useState<Segment>('all');
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);

  const ITEMS_PER_PAGE = 10;

  const handleSyncCustomers = async () => {
    setSyncing(true);
    setSyncMessage(null);
    try {
      const res = await fetch('/api/customers/sync', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed to sync customers');
      setSyncMessage(data.message);
      // Refresh list
      setPage(1);
    } catch (err: any) {
      setError(err.message || 'Failed to sync customers');
    } finally {
      setSyncing(false);
    }
  };

  // Fetch stats
  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await fetch('/api/customers/stats');
        if (!res.ok) throw new Error('Failed to fetch stats');
        const json = await res.json();
        const d = json.data || json;
        setStats({
          totalCustomers: d.totalCustomers || 0,
          customersWithOrders: d.withOrders ?? d.customersWithOrders ?? 0,
          acceptsMarketing: d.acceptsMarketing || 0,
          avgOrderValue: d.avgOrderValue || 0,
        });
      } catch (err) {
        console.error('Error fetching stats:', err);
        setError('Failed to load stats');
      }
    };
    fetchStats();
  }, []);

  // Fetch customers
  useEffect(() => {
    const fetchCustomers = async () => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          page: page.toString(),
          limit: ITEMS_PER_PAGE.toString(),
          segment,
          ...(search && { search }),
        });
        const res = await fetch(`/api/customers?${params}`);
        if (!res.ok) throw new Error('Failed to fetch customers');
        const data = await res.json();
        setCustomers(data.data || data.customers || []);
        setTotalPages(Math.ceil((data.pagination?.total || data.total || 0) / ITEMS_PER_PAGE));
      } catch (err) {
        console.error('Error fetching customers:', err);
        setError('Failed to load customers');
      } finally {
        setLoading(false);
      }
    };
    fetchCustomers();
  }, [page, search, segment]);

  return (
    <>
      <Topbar
        title="Customers"
        subtitle="Synced from Shopify"
        breadcrumb={[{ label: 'Home', href: '/dashboard' }, { label: 'Customers' }]}
        primaryAction={null}
        actions={
          <button
            type="button"
            onClick={handleSyncCustomers}
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
        {syncMessage && (
          <p className="text-xs text-green-700 mb-3">{syncMessage}</p>
        )}

        {/* Stats Cards */}
        {stats && (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
            <div className="bg-white rounded-lg shadow p-6">
              <p className="text-gray-600 text-sm font-medium">Total Customers</p>
              <p className="text-3xl font-bold text-gray-900 mt-2">{stats.totalCustomers}</p>
            </div>
            <div className="bg-white rounded-lg shadow p-6">
              <p className="text-gray-600 text-sm font-medium">With Orders</p>
              <p className="text-3xl font-bold text-blue-600 mt-2">{stats.customersWithOrders}</p>
            </div>
            <div className="bg-white rounded-lg shadow p-6">
              <p className="text-gray-600 text-sm font-medium">Accepts Marketing</p>
              <p className="text-3xl font-bold text-green-600 mt-2">{stats.acceptsMarketing}</p>
            </div>
            <div className="bg-white rounded-lg shadow p-6">
              <p className="text-gray-600 text-sm font-medium">Avg Order Value</p>
              <p className="text-3xl font-bold text-gray-900 mt-2">₹{(stats.avgOrderValue || 0).toFixed(2)}</p>
            </div>
          </div>
        )}


        {/* Error Message */}
        {error && (
          <div className="mb-6 bg-red-50 border border-red-200 rounded-lg p-4">
            <p className="text-red-800">{error}</p>
          </div>
        )}

        {/* Segment tabs + search */}
        <div className="bg-white rounded-lg shadow p-4 mb-6">
          <div className="flex items-center gap-2 flex-wrap border-b border-gray-200 -mx-4 px-4 pb-3 mb-3">
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
          <div className="relative">
            <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search by name, email, or phone..."
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
        </div>

        {/* Customers Table */}
        <div className="bg-white rounded-lg shadow overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 animate-spin text-blue-600" />
            </div>
          ) : customers.length === 0 ? (
            <div className="flex items-center justify-center py-12">
              <p className="text-gray-600">No customers found</p>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Name</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Email</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Phone</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">City</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Orders</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Total Spent</th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-700 uppercase">Marketing</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {customers.map((customer) => (
                      <tr key={customer.id} className="hover:bg-gray-50">
                        <td className="px-6 py-4 text-sm font-medium text-gray-900">
                          <Link
                            href={`/dashboard/customers/${customer.id}`}
                            className="text-blue-700 hover:underline"
                          >
                            {customerName(customer)}
                          </Link>
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-600">{customer.email || '—'}</td>
                        <td className="px-6 py-4 text-sm text-gray-600">{customer.phone || '—'}</td>
                        <td className="px-6 py-4 text-sm text-gray-600">{customer.city || '—'}</td>
                        <td className="px-6 py-4 text-sm font-medium text-gray-900">{customer.ordersCount}</td>
                        <td className="px-6 py-4 text-sm font-medium text-gray-900">₹{(customer.totalSpent || 0).toFixed(2)}</td>
                        <td className="px-6 py-4">
                          {customer.acceptsMarketing ? (
                            <Check className="w-5 h-5 text-green-600" />
                          ) : (
                            <X className="w-5 h-5 text-gray-400" />
                          )}
                        </td>
                      </tr>
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
    </>
  );
}
