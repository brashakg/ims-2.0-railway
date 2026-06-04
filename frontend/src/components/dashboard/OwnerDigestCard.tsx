// ============================================================================
// IMS 2.0 — Owner digest card (Hub, SUPERADMIN / ADMIN only)
// ----------------------------------------------------------------------------
// The day-close snapshot an owner wants at a glance, surfaced IN the Hub (not
// over WhatsApp): today's sales / collections / expenses / cash-net / orders /
// new customers / pending tasks / low stock. A BRIEF KPI strip by default; an
// EXPAND toggle reveals month-to-date, per-store sales, the payment-mode split,
// the low-stock + pending-task lists, and staff presence.
//
// Read-only + fail-soft: any error renders nothing (the rest of the Hub is
// unaffected). Numbers come live from GET /api/v1/admin/owner-digest. Light
// theme only.
// ============================================================================

import { useEffect, useState } from 'react';
import {
  TrendingUp,
  Wallet,
  Receipt,
  Banknote,
  ShoppingBag,
  UserPlus,
  ListTodo,
  PackageX,
  ChevronDown,
  ChevronUp,
  Loader2,
  Store,
  Users2,
  CalendarRange,
} from 'lucide-react';
import { dashboardApi, type OwnerDigest } from '../../services/api/dashboard';

const INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

function inr(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  const v = Math.round(n);
  if (Math.abs(v) >= 100000) return `₹${(v / 100000).toFixed(2)}L`;
  return `₹${INR.format(v)}`;
}

function num(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  return INR.format(Math.round(n));
}

interface TileProps {
  label: string;
  value: string;
  icon: React.ReactNode;
  tone?: 'pos' | 'neg' | 'warn' | 'neutral';
  sub?: string;
}

function Tile({ label, value, icon, tone = 'neutral', sub }: TileProps) {
  const toneClass =
    tone === 'pos'
      ? 'text-green-700'
      : tone === 'neg'
        ? 'text-red-600'
        : tone === 'warn'
          ? 'text-amber-600'
          : 'text-gray-900';
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[10.5px] font-mono uppercase tracking-wider text-gray-400">
        {icon}
        {label}
      </div>
      <div className={`mt-1 text-lg font-semibold tabular-nums ${toneClass}`}>{value}</div>
      {sub ? <div className="text-[11px] text-gray-400">{sub}</div> : null}
    </div>
  );
}

export default function OwnerDigestCard({ storeId }: { storeId?: string }) {
  const [digest, setDigest] = useState<OwnerDigest | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setFailed(false);
    dashboardApi
      .getOwnerDigest(storeId)
      .then((d) => {
        if (alive) setDigest(d);
      })
      .catch(() => {
        if (alive) setFailed(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [storeId]);

  // Fail-soft: a backend hiccup must not break the Hub — render nothing.
  if (failed) return null;

  const t = digest?.today;
  const m = digest?.mtd;
  const ex = digest?.expanded;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      {/* header */}
      <div className="mb-3 flex items-center justify-between">
        <div>
          <div className="text-[10.5px] font-mono uppercase tracking-wider text-gray-400">
            Owner digest · Today {digest?.date ? `· ${digest.date}` : ''}
            {storeId ? ` · ${storeId}` : ' · all stores'}
          </div>
          <h2 className="text-base font-semibold text-gray-900">Day at a glance</h2>
        </div>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-50"
          title={expanded ? 'Show less' : 'Show more detail'}
          disabled={loading}
        >
          {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          {expanded ? 'Brief' : 'Expand'}
        </button>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 py-6 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading today's numbers…
        </div>
      ) : (
        <>
          {/* BRIEF — KPI strip */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2.5">
            <Tile label="Sales" value={inr(t?.sales)} icon={<TrendingUp className="w-3 h-3" />} tone="pos" />
            <Tile label="Collected" value={inr(t?.collections)} icon={<Wallet className="w-3 h-3" />} />
            <Tile label="Expenses" value={inr(t?.expenses)} icon={<Receipt className="w-3 h-3" />} tone="neg" />
            <Tile
              label="Cash net"
              value={inr(t?.cash_net)}
              icon={<Banknote className="w-3 h-3" />}
              tone={(t?.cash_net ?? 0) >= 0 ? 'pos' : 'neg'}
              sub="collected − expenses"
            />
            <Tile label="Orders" value={num(t?.orders)} icon={<ShoppingBag className="w-3 h-3" />} />
            <Tile label="New customers" value={num(t?.new_customers)} icon={<UserPlus className="w-3 h-3" />} />
            <Tile
              label="Pending tasks"
              value={num(t?.pending_tasks)}
              icon={<ListTodo className="w-3 h-3" />}
              tone={(t?.pending_tasks ?? 0) > 0 ? 'warn' : 'neutral'}
            />
            <Tile
              label="Low / out of stock"
              value={`${num(t?.low_stock)} / ${num(t?.out_of_stock)}`}
              icon={<PackageX className="w-3 h-3" />}
              tone={(t?.out_of_stock ?? 0) > 0 ? 'neg' : (t?.low_stock ?? 0) > 0 ? 'warn' : 'neutral'}
            />
          </div>

          {/* EXPANDED */}
          {expanded && (
            <div className="mt-4 space-y-4 border-t border-gray-100 pt-4">
              {/* MTD */}
              <div>
                <div className="mb-1.5 flex items-center gap-1.5 text-[10.5px] font-mono uppercase tracking-wider text-gray-400">
                  <CalendarRange className="w-3 h-3" /> Month to date
                </div>
                <div className="grid grid-cols-3 gap-2.5">
                  <Tile label="Sales" value={inr(m?.sales)} icon={<TrendingUp className="w-3 h-3" />} tone="pos" />
                  <Tile label="Expenses" value={inr(m?.expenses)} icon={<Receipt className="w-3 h-3" />} tone="neg" />
                  <Tile label="Orders" value={num(m?.orders)} icon={<ShoppingBag className="w-3 h-3" />} />
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {/* By store */}
                <div>
                  <div className="mb-1.5 flex items-center gap-1.5 text-[10.5px] font-mono uppercase tracking-wider text-gray-400">
                    <Store className="w-3 h-3" /> Sales by store (today)
                  </div>
                  {ex && ex.by_store.length > 0 ? (
                    <ul className="rounded-lg border border-gray-200 divide-y divide-gray-100 text-sm">
                      {ex.by_store.map((s) => (
                        <li key={s.store_id} className="flex items-center justify-between px-3 py-1.5">
                          <span className="text-gray-700">{s.store_id}</span>
                          <span className="tabular-nums text-gray-900">
                            {inr(s.sales)} <span className="text-gray-400">· {s.orders}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-gray-400">No sales recorded today.</p>
                  )}
                </div>

                {/* Payment modes */}
                <div>
                  <div className="mb-1.5 flex items-center gap-1.5 text-[10.5px] font-mono uppercase tracking-wider text-gray-400">
                    <Wallet className="w-3 h-3" /> Collected by mode (today)
                  </div>
                  {ex && Object.keys(ex.payment_modes).length > 0 ? (
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(ex.payment_modes).map(([mode, amt]) => (
                        <span
                          key={mode}
                          className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-50 px-2.5 py-1 text-xs text-gray-700"
                        >
                          {mode}
                          <span className="font-semibold tabular-nums text-gray-900">{inr(amt)}</span>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-gray-400">No payment detail recorded today.</p>
                  )}
                </div>

                {/* Low stock list */}
                <div>
                  <div className="mb-1.5 flex items-center gap-1.5 text-[10.5px] font-mono uppercase tracking-wider text-gray-400">
                    <PackageX className="w-3 h-3" /> Low / out of stock
                  </div>
                  {ex && ex.low_stock_items.length > 0 ? (
                    <ul className="rounded-lg border border-gray-200 divide-y divide-gray-100 text-sm">
                      {ex.low_stock_items.map((p, i) => (
                        <li key={(p.sku || p.name || '') + i} className="flex items-center justify-between px-3 py-1.5">
                          <span className="truncate text-gray-700">{p.name || p.sku || '—'}</span>
                          <span
                            className={`tabular-nums ${p.qty <= 0 ? 'text-red-600' : 'text-amber-600'}`}
                          >
                            {p.qty} left{p.reorder_point ? ` / RP ${p.reorder_point}` : ''}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-gray-400">Nothing below reorder point. 👍</p>
                  )}
                </div>

                {/* Pending tasks + staff */}
                <div>
                  <div className="mb-1.5 flex items-center gap-1.5 text-[10.5px] font-mono uppercase tracking-wider text-gray-400">
                    <ListTodo className="w-3 h-3" /> Pending tasks
                  </div>
                  {ex && ex.pending_task_list.length > 0 ? (
                    <ul className="rounded-lg border border-gray-200 divide-y divide-gray-100 text-sm">
                      {ex.pending_task_list.map((tk, i) => (
                        <li key={i} className="flex items-center gap-2 px-3 py-1.5">
                          <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] font-semibold text-gray-600">
                            {tk.priority}
                          </span>
                          <span className="truncate text-gray-700">{tk.title}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-xs text-gray-400">No pending tasks.</p>
                  )}
                  {ex?.staff ? (
                    <div className="mt-2 inline-flex items-center gap-1.5 text-xs text-gray-500">
                      <Users2 className="w-3.5 h-3.5" />
                      Staff present today: <span className="font-semibold text-gray-800">{ex.staff.present_today}</span> / {ex.staff.total_staff}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
