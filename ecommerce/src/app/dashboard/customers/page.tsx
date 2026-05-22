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

        {/* Stats Cards — Polaris polaris-card with tabular-nums KPIs */}
        {stats && (
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
            {[
              { label: 'Total customers', value: stats.totalCustomers, tone: 'default' },
              { label: 'With orders', value: stats.customersWithOrders, tone: 'default' },
              { label: 'Accepts marketing', value: stats.acceptsMarketing, tone: 'success' },
              {
                label: 'Avg order value',
                value: `₹${(stats.avgOrderValue || 0).toFixed(2)}`,
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

        {/* Segment chip rail */}
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
                  borderColor: active ? 'var(--text)' : 'var(--border-strong)',
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
            <Search className="absolute left-3 top-2 text-slate-400" size={14} />
            <input
              type="text"
              placeholder="Search by name, email, or phone…"
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

        {/* Customers Table — Polaris polaris-table primitive */}
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
              <Loader2 className="animate-spin inline-block mr-2" size={14} />
              Loading…
            </div>
          ) : customers.length === 0 ? (
            <div
              style={{
                padding: 32,
                textAlign: 'center',
                color: 'var(--text-tertiary)',
                fontSize: 13,
              }}
            >
              No customers match this filter.
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="polaris-table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Email</th>
                      <th>Phone</th>
                      <th>City</th>
                      <th
                        className="tabular-nums"
                        style={{ textAlign: 'right' }}
                      >
                        Orders
                      </th>
                      <th
                        className="tabular-nums"
                        style={{ textAlign: 'right' }}
                      >
                        Total spent
                      </th>
                      <th>Marketing</th>
                    </tr>
                  </thead>
                  <tbody>
                    {customers.map((customer) => (
                      <tr key={customer.id}>
                        <td style={{ fontWeight: 500 }}>
                          <Link
                            href={`/dashboard/customers/${customer.id}`}
                            style={{
                              color: 'var(--brand-text)',
                              textDecoration: 'none',
                            }}
                            className="hover:underline"
                          >
                            {customerName(customer)}
                          </Link>
                        </td>
                        <td style={{ color: 'var(--text-secondary)' }}>
                          {customer.email || '—'}
                        </td>
                        <td style={{ color: 'var(--text-secondary)' }}>
                          {customer.phone || '—'}
                        </td>
                        <td style={{ color: 'var(--text-secondary)' }}>
                          {customer.city || '—'}
                        </td>
                        <td
                          className="tabular-nums"
                          style={{ textAlign: 'right', fontWeight: 500 }}
                        >
                          {customer.ordersCount}
                        </td>
                        <td
                          className="tabular-nums"
                          style={{ textAlign: 'right', fontWeight: 500 }}
                        >
                          ₹{(customer.totalSpent || 0).toFixed(2)}
                        </td>
                        <td>
                          {customer.acceptsMarketing ? (
                            <Check
                              size={14}
                              style={{ color: 'var(--success-text)' }}
                            />
                          ) : (
                            <X
                              size={14}
                              style={{ color: 'var(--text-tertiary)' }}
                            />
                          )}
                        </td>
                      </tr>
                    ))}
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
