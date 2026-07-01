// ============================================================================
// IMS 2.0 - Online Store - Customers  (BVI Phase 3)
// ============================================================================
// Read-only list of the online-joined customers: the shoppers who joined via
// the storefront and were unified into the ONE IMS customer record by
// phone/email, each carrying their Shopify customer id.
//
// Online and in-store customers live in the SAME `customers` collection (a
// person who shops both is a single mobile-deduped record); they are told apart
// by an ORIGIN tag — channel/source == 'ONLINE' OR a non-empty
// shopify_customer_id. This screen asks the backend for exactly that segment via
// GET /api/v1/customers?channel=ONLINE (unification step-4), so it never has to
// re-implement the segregation client-side.
//
// This is the first live surface of the "Customers" section on the Online Store
// shell (SECTIONS[5]). It is a LIST + SEARCH only for this phase — the full
// customer record + 360 view already live under /customers. Nothing is written
// here.
//
// Reads: GET /api/v1/customers?channel=ONLINE  (paginated envelope)
//
// FAIL-SOFT: the read degrades quietly to a friendly empty state (never a white
// screen). Gated SUPERADMIN / ADMIN / CATALOG_MANAGER / DESIGN_MANAGER at the
// route (App.tsx), matching the rest of the module. Light theme only.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Users,
  ArrowLeft,
  RefreshCw,
  Loader2,
  Search,
  Mail,
  Phone,
  ShoppingBag,
} from 'lucide-react';
import { customerApi } from '../../services/api/customers';
import { formatDateIST } from '../../utils/datetime';

// ---------------------------------------------------------------------------
// A slim, presentation-only online-customer row.
// ---------------------------------------------------------------------------
interface OnlineCustomerRow {
  id: string;
  name: string;
  phone: string | null;
  email: string | null;
  shopify_customer_id: string | null;
  joined_at: string | null;
  orders_count: number | null;
  ltv: number | null;
}

/** Project a raw customer doc onto OnlineCustomerRow, tolerating the several
 *  field aliases the customers collection carries (mobile/phone; created_at;
 *  total_purchases / orders_count / total_spent). */
function toRow(c: Record<string, any>): OnlineCustomerRow {
  const ordersCount =
    typeof c.orders_count === 'number'
      ? c.orders_count
      : typeof c.total_orders === 'number'
        ? c.total_orders
        : null;
  const ltv =
    typeof c.total_spent === 'number'
      ? c.total_spent
      : typeof c.lifetime_value === 'number'
        ? c.lifetime_value
        : typeof c.total_purchases === 'number'
          ? c.total_purchases
          : null;
  return {
    id: String(c.customer_id ?? c.id ?? c._id ?? ''),
    name: c.name ?? c.customer_name ?? 'Unnamed shopper',
    phone: c.mobile ?? c.phone ?? null,
    email: c.email ?? null,
    shopify_customer_id:
      c.shopify_customer_id != null && c.shopify_customer_id !== ''
        ? String(c.shopify_customer_id)
        : null,
    joined_at: c.created_at ?? c.createdAt ?? c.joined_at ?? null,
    orders_count: ordersCount,
    ltv,
  };
}

function fmtMoney(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return '—';
  try {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(Math.round(amount));
  } catch {
    return String(Math.round(amount));
  }
}

// ===========================================================================
// Page
// ===========================================================================
export default function OnlineCustomersPage() {
  const [rows, setRows] = useState<OnlineCustomerRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [available, setAvailable] = useState(true);
  const [search, setSearch] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Ask the backend for only the online-origin segment (step-4 channel tag).
      const res = await customerApi.getCustomers({ channel: 'ONLINE', limit: 500 });
      const arr = Array.isArray(res)
        ? res
        : (res?.customers ?? res?.data ?? res?.items ?? []);
      const raw = (Array.isArray(arr) ? arr : []) as Record<string, any>[];
      setRows(raw.map(toRow).filter((r) => r.id));
      setAvailable(true);
    } catch {
      // Fail-soft: an unreachable backend -> friendly empty state, not a crash.
      setRows([]);
      setAvailable(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) =>
      [r.name, r.phone, r.email, r.shopify_customer_id]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q)),
    );
  }, [rows, search]);

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header + breadcrumb */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <div>
          <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
            <Link to="/online-store" className="inline-flex items-center gap-1 hover:text-gray-700">
              <ArrowLeft className="w-3.5 h-3.5" /> Online Store
            </Link>
            <span>/</span>
            <span className="text-gray-700">Customers</span>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <Users className="w-5 h-5" /> Online customers
          </h1>
        </div>
        <button
          type="button"
          onClick={load}
          className="btn-outline inline-flex items-center gap-1.5 text-sm"
          title="Reload"
        >
          <RefreshCw className={'w-4 h-4 ' + (loading ? 'animate-spin' : '')} /> Refresh
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        Shoppers who joined via the storefront, unified into the one IMS customer record by
        phone/email and carrying their Shopify customer id. A shopper who also buys in-store is the
        same record here and in the store — this view just surfaces the online-origin segment. Read
        only; open the full customer for their 360 view.
      </p>

      {/* Toolbar */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name, phone, email, or Shopify id…"
            className="input-field w-full pl-9"
          />
        </div>
        {!loading && available && (
          <span className="text-xs text-gray-500">
            {visible.length.toLocaleString('en-IN')} customer{visible.length !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* List */}
      {loading ? (
        <div className="rounded-xl border border-gray-200 bg-white p-6 flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading online customers…
        </div>
      ) : !available ? (
        <div className="rounded-xl border border-blue-200 bg-blue-50 p-6 text-center">
          <Users className="w-10 h-10 mx-auto mb-2 text-blue-400" />
          <p className="text-sm font-medium text-blue-900">Online customers are coming online</p>
          <p className="text-xs text-blue-700 mt-1 max-w-md mx-auto">
            Online-joined shoppers appear here as storefront orders flow in and their records are
            unified into IMS.
          </p>
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-10 text-center text-gray-500">
          <Users className="w-10 h-10 mx-auto mb-2 opacity-50" />
          <p className="text-sm">
            {search
              ? 'No online customers match this search.'
              : 'No online customers yet. Shoppers who join via the storefront will show up here.'}
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="text-left font-medium px-4 py-2.5">Customer</th>
                  <th className="text-left font-medium px-4 py-2.5">Contact</th>
                  <th className="text-left font-medium px-4 py-2.5 w-40">Shopify id</th>
                  <th className="text-left font-medium px-4 py-2.5 w-32">Joined</th>
                  <th className="text-right font-medium px-4 py-2.5 w-24">Orders</th>
                  <th className="text-right font-medium px-4 py-2.5 w-28">Lifetime</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {visible.map((r, idx) => (
                  <tr key={r.id || idx} className="hover:bg-gray-50">
                    <td className="px-4 py-2.5">
                      {r.id ? (
                        <Link
                          to={`/customers/${r.id}`}
                          className="font-medium text-gray-900 hover:text-bv-red-600"
                        >
                          {r.name}
                        </Link>
                      ) : (
                        <span className="font-medium text-gray-900">{r.name}</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-gray-700">
                      <div className="flex flex-col gap-0.5">
                        {r.phone && (
                          <span className="inline-flex items-center gap-1.5 text-xs">
                            <Phone className="w-3 h-3 text-gray-400" /> {r.phone}
                          </span>
                        )}
                        {r.email && (
                          <span className="inline-flex items-center gap-1.5 text-xs truncate max-w-[220px]">
                            <Mail className="w-3 h-3 text-gray-400" /> {r.email}
                          </span>
                        )}
                        {!r.phone && !r.email && <span className="text-gray-400">—</span>}
                      </div>
                    </td>
                    <td className="px-4 py-2.5">
                      {r.shopify_customer_id ? (
                        <span className="inline-flex items-center rounded-full bg-gray-100 text-gray-600 border border-gray-200 px-2 py-0.5 text-[11px] font-mono">
                          {r.shopify_customer_id}
                        </span>
                      ) : (
                        <span className="text-gray-400">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-gray-500 text-xs whitespace-nowrap">
                      {r.joined_at ? formatDateIST(r.joined_at) : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right text-gray-700">
                      {r.orders_count === null ? (
                        <span className="text-gray-400">—</span>
                      ) : (
                        <span className="inline-flex items-center gap-1">
                          <ShoppingBag className="w-3.5 h-3.5 text-gray-400" />
                          {r.orders_count}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-right text-gray-900 font-medium whitespace-nowrap">
                      {fmtMoney(r.ltv)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <p className="mt-6 text-xs text-gray-400">
        Online Store module · Customers. The online-origin segment of the unified IMS customer base.
      </p>
    </div>
  );
}
