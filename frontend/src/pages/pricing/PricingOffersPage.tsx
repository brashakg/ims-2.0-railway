// ============================================================================
// IMS 2.0 - Pricing & Offers (v2 slice 4)
// ============================================================================
// Bulk price update + bulk offer update across a filtered set of products.
// Dry-run-first: the operator picks a scope (category / brand / store) and an
// operation, PREVIEWS the per-row before/after with a cap-status chip, then
// commits ONLY the valid rows. Rows that would violate a discount cap or the
// MRP > offer rule are surfaced with a clear reason and are NEVER applied.
//
// All data is live (no mock): scope dropdowns from /products/{categories,
// brands}/list + /stores; the preview + apply hit /products/bulk-price and
// /products/bulk-offer. The backend (api/services/pricing_caps.py) is the
// single source of truth for the caps.

import { useCallback, useEffect, useRef, useState } from 'react';
import { Tag, Percent, RefreshCw, Loader2, AlertTriangle, ShieldAlert, CheckCircle2 } from 'lucide-react';
import {
  pricingApi,
  productApi,
  type BulkResponse,
  type BulkRowResult,
} from '../../services/api/products';
import { storeApi } from '../../services/api/stores';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

interface StoreOpt { store_id: string; store_name?: string; store_code?: string }

const inr = (n: number | undefined | null): string =>
  n == null ? '—' : `₹ ${Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;

// ----------------------------------------------------------------------------
// Cap-status chip
// ----------------------------------------------------------------------------

function CapChip({ row }: { row: BulkRowResult }) {
  if (!row.ok) {
    const luxe = row.reason === 'CAP_EXCEEDED';
    return (
      <span
        title={row.message || row.reason || 'Blocked'}
        className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium ${
          luxe ? 'bg-bv-50 text-bv' : 'bg-amber-100 text-amber-800'
        }`}
      >
        {luxe ? <ShieldAlert className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
        {row.reason === 'CAP_EXCEEDED'
          ? `Over cap ${row.effective_cap_pct}%`
          : row.reason === 'MRP_BELOW_OFFER'
            ? 'MRP < offer'
            : 'Invalid'}
      </span>
    );
  }
  if (!row.changed) {
    return <span className="inline-flex items-center rounded px-2 py-0.5 text-[11px] text-gray-400">Unchanged</span>;
  }
  return (
    <span className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium bg-green-100 text-green-800">
      <CheckCircle2 className="w-3 h-3" />
      OK {row.implied_discount_pct > 0 ? `· ${row.implied_discount_pct}% off` : ''}
    </span>
  );
}

// ----------------------------------------------------------------------------
// Shared scope picker
// ----------------------------------------------------------------------------

interface Scope { category: string; brand: string; storeId: string }

function ScopePicker({
  scope,
  setScope,
  categories,
  brands,
  stores,
}: {
  scope: Scope;
  setScope: (s: Scope) => void;
  categories: string[];
  brands: string[];
  stores: StoreOpt[];
}) {
  return (
    <aside className="bg-white border border-gray-200 rounded-xl p-0 self-start">
      <div className="px-4 py-3 border-b border-gray-100">
        <div className="text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-2">Scope · Category</div>
        <select
          value={scope.category}
          onChange={(e) => setScope({ ...scope, category: e.target.value })}
          className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
        >
          <option value="">All categories</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div className="px-4 py-3 border-b border-gray-100">
        <div className="text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-2">Scope · Brand</div>
        <select
          value={scope.brand}
          onChange={(e) => setScope({ ...scope, brand: e.target.value })}
          className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
        >
          <option value="">All brands</option>
          {brands.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>
      </div>
      <div className="px-4 py-3">
        <div className="text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-2">Scope · Store</div>
        <select
          value={scope.storeId}
          onChange={(e) => setScope({ ...scope, storeId: e.target.value })}
          className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
        >
          <option value="">All stores (global catalog)</option>
          {stores.map((s) => (
            <option key={s.store_id} value={s.store_id}>{s.store_name || s.store_code || s.store_id}</option>
          ))}
        </select>
        <p className="mt-2 text-[11px] text-gray-400 leading-snug">
          Price is a global product field. The store filter limits the set to SKUs that have stock at that store.
        </p>
      </div>
    </aside>
  );
}

// ----------------------------------------------------------------------------
// Summary stat strip (shared)
// ----------------------------------------------------------------------------

function StatStrip({ result }: { result: BulkResponse | null }) {
  const c = result?.summary.counts;
  const cells = [
    { l: 'In scope', v: c?.total ?? 0, tone: 'text-gray-900' },
    { l: 'Valid changes', v: c?.valid ?? 0, tone: 'text-green-700' },
    { l: 'Cap violations', v: c?.violations ?? 0, tone: (c?.violations ?? 0) > 0 ? 'text-bv' : 'text-gray-900' },
    { l: 'Unchanged', v: c?.unchanged ?? 0, tone: 'text-gray-500' },
    { l: result?.dry_run === false ? 'Committed' : 'Will commit', v: result?.dry_run === false ? result?.summary.committed ?? 0 : c?.valid ?? 0, tone: 'text-gray-900' },
  ];
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 bg-white border border-gray-200 rounded-xl mb-4 divide-x divide-gray-100">
      {cells.map((cell) => (
        <div key={cell.l} className="px-4 py-3">
          <div className="text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">{cell.l}</div>
          <div className={`text-2xl font-semibold tabular-nums ${cell.tone}`}>{cell.v}</div>
        </div>
      ))}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Preview table (shared)
// ----------------------------------------------------------------------------

function PreviewTable({ rows, kind }: { rows: BulkRowResult[]; kind: 'price' | 'offer' }) {
  if (rows.length === 0) {
    return (
      <div className="bg-white border border-dashed border-gray-300 rounded-xl px-6 py-10 text-center text-sm text-gray-500">
        No products match this scope yet. Adjust the filters, then Preview.
      </div>
    );
  }
  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[10.5px] font-mono uppercase tracking-wider text-gray-400 bg-gray-50 border-b border-gray-200">
            <th className="text-left px-3 py-2.5">SKU</th>
            <th className="text-left px-3 py-2.5">Brand · Model</th>
            <th className="text-left px-3 py-2.5">Tier</th>
            <th className="text-right px-3 py-2.5">MRP</th>
            {kind === 'price' && <th className="text-right px-3 py-2.5">New MRP</th>}
            <th className="text-right px-3 py-2.5">Offer</th>
            <th className="text-right px-3 py-2.5">Proposed</th>
            <th className="text-right px-3 py-2.5">Cap</th>
            <th className="text-left px-3 py-2.5">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const curMrp = kind === 'price' ? r.old_mrp : r.mrp;
            return (
              <tr
                key={r.product_id}
                className={`border-b border-gray-100 last:border-0 ${!r.ok ? 'bg-bv-50/40' : r.changed ? 'bg-green-50/40' : ''}`}
              >
                <td className="px-3 py-2.5 font-mono text-[11px] text-gray-600">{r.sku}</td>
                <td className="px-3 py-2.5">
                  <div className="text-[10px] uppercase tracking-wide text-gray-400">{r.brand}</div>
                  <div className="font-medium text-gray-900">{r.model}</div>
                </td>
                <td className="px-3 py-2.5">
                  <span className="inline-flex items-center rounded bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600">
                    {r.discount_category || r.category || '—'}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{inr(curMrp)}</td>
                {kind === 'price' && (
                  <td className={`px-3 py-2.5 text-right tabular-nums ${r.new_mrp !== curMrp ? 'font-semibold text-gray-900' : 'text-gray-400'}`}>
                    {inr(r.new_mrp)}
                  </td>
                )}
                <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{inr(r.old_offer_price)}</td>
                <td className={`px-3 py-2.5 text-right tabular-nums ${r.new_offer_price !== r.old_offer_price ? 'font-semibold text-gray-900' : 'text-gray-400'}`}>
                  {inr(r.new_offer_price)}
                </td>
                <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{r.effective_cap_pct}%</td>
                <td className="px-3 py-2.5"><CapChip row={r} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Bulk price tab
// ----------------------------------------------------------------------------

function BulkPriceTab({ categories, brands, stores }: { categories: string[]; brands: string[]; stores: StoreOpt[] }) {
  const toast = useToast();
  const { user } = useAuth();
  const [scope, setScope] = useState<Scope>({ category: '', brand: '', storeId: '' });
  const [mode, setMode] = useState<'PERCENT' | 'FLAT'>('PERCENT');
  const [target, setTarget] = useState<'OFFER' | 'MRP' | 'BOTH'>('OFFER');
  const [amount, setAmount] = useState<number>(-5);
  const [result, setResult] = useState<BulkResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const validCount = result?.summary.counts.valid ?? 0;

  const run = useCallback(async (apply: boolean) => {
    setBusy(true);
    try {
      const res = await pricingApi.bulkPrice({
        category: scope.category || undefined,
        brand: scope.brand || undefined,
        store_id: scope.storeId || undefined,
        mode,
        target,
        amount,
        apply,
        reason: apply ? `Bulk price ${mode} ${amount} to ${target} by ${user?.name || 'user'}` : undefined,
      });
      setResult(res);
      if (apply) {
        toast.success(`Applied ${res.summary.committed} price change${res.summary.committed === 1 ? '' : 's'}` +
          (res.summary.counts.violations ? ` · ${res.summary.counts.violations} skipped (cap)` : ''));
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Bulk price operation failed');
    } finally {
      setBusy(false);
    }
  }, [scope, mode, target, amount, user, toast]);

  // Auto-run a dry-run preview so the SKU list populates on load and stays live
  // as the scope/operation changes -- the operator shouldn't have to discover
  // the Preview button to see any SKUs at all. Dry-run is read-only (apply=false);
  // debounced to coalesce rapid edits. A ref keeps the latest `run` without
  // re-firing the effect on every callback identity change.
  const runRef = useRef(run);
  useEffect(() => {
    runRef.current = run;
  }, [run]);
  useEffect(() => {
    const t = setTimeout(() => runRef.current(false), 450);
    return () => clearTimeout(t);
  }, [scope.category, scope.brand, scope.storeId, mode, target, amount]);

  return (
    <>
      <StatStrip result={result} />

      {/* Dark bulk-action bar (high contrast on purpose — destructive operation) */}
      <div className="bg-gray-900 text-white rounded-lg px-4 py-3 mb-4 flex flex-wrap items-center gap-3">
        <div className="text-[11px] font-mono uppercase tracking-wider text-gray-400">Apply</div>
        <select value={mode} onChange={(e) => setMode(e.target.value as 'PERCENT' | 'FLAT')}
          className="bg-gray-800 border border-gray-700 text-white rounded px-2.5 py-1.5 text-sm [&>option]:bg-white [&>option]:text-gray-900">
          <option value="PERCENT">Percentage %</option>
          <option value="FLAT">Absolute &#8377;</option>
        </select>
        <input type="number" value={amount} onChange={(e) => setAmount(Number(e.target.value))}
          className="bg-gray-800 border border-gray-700 text-white rounded px-2.5 py-1.5 text-sm w-24 tabular-nums" />
        <div className="text-[11px] font-mono uppercase tracking-wider text-gray-400">to</div>
        <select value={target} onChange={(e) => setTarget(e.target.value as 'OFFER' | 'MRP' | 'BOTH')}
          className="bg-gray-800 border border-gray-700 text-white rounded px-2.5 py-1.5 text-sm [&>option]:bg-white [&>option]:text-gray-900">
          <option value="OFFER">Selling price</option>
          <option value="MRP">MRP</option>
          <option value="BOTH">Both</option>
        </select>
        <div className="flex-1" />
        <button type="button" onClick={() => run(false)} disabled={busy}
          className="inline-flex items-center gap-1.5 border border-gray-600 text-gray-200 hover:bg-gray-800 rounded px-3.5 py-1.5 text-sm disabled:opacity-50">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Preview
        </button>
        <button type="button" onClick={() => run(true)} disabled={busy || !result || validCount === 0}
          className="inline-flex items-center gap-1.5 bg-bv hover:bg-bv-600 text-white rounded px-3.5 py-1.5 text-sm font-medium disabled:opacity-40">
          Apply {validCount} valid row{validCount === 1 ? '' : 's'}
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4 items-start">
        <ScopePicker scope={scope} setScope={setScope} categories={categories} brands={brands} stores={stores} />
        <div>
          {result && result.summary.counts.violations > 0 && (
            <div className="mb-3 flex items-start gap-2 rounded-lg border border-bv/30 bg-bv-50 px-3 py-2 text-[12.5px] text-bv">
              <ShieldAlert className="w-4 h-4 mt-0.5 shrink-0" />
              <span>
                {result.summary.counts.violations} row(s) exceed a category / luxury-brand discount cap and will be
                skipped. Caps: MASS 15% · PREMIUM 20% · LUXURY 5% · SERVICE 10% · NON_DISCOUNTABLE 0%; Cartier/Chopard/Bvlgari 2% · Gucci/Prada/Versace/Burberry 5%.
              </span>
            </div>
          )}
          <PreviewTable rows={result?.rows ?? []} kind="price" />
        </div>
      </div>
    </>
  );
}

// ----------------------------------------------------------------------------
// Bulk offer tab
// ----------------------------------------------------------------------------

function BulkOfferTab({ categories, brands, stores }: { categories: string[]; brands: string[]; stores: StoreOpt[] }) {
  const toast = useToast();
  const { user } = useAuth();
  const [scope, setScope] = useState<Scope>({ category: '', brand: '', storeId: '' });
  const [action, setAction] = useState<'SET' | 'CLEAR'>('SET');
  const [valueMode, setValueMode] = useState<'PERCENT' | 'FLAT'>('PERCENT');
  const [discountPct, setDiscountPct] = useState<number>(10);
  const [flatPrice, setFlatPrice] = useState<number>(0);
  const [result, setResult] = useState<BulkResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const validCount = result?.summary.counts.valid ?? 0;

  const run = useCallback(async (apply: boolean) => {
    setBusy(true);
    try {
      const res = await pricingApi.bulkOffer({
        category: scope.category || undefined,
        brand: scope.brand || undefined,
        store_id: scope.storeId || undefined,
        action,
        discount_percent: action === 'SET' && valueMode === 'PERCENT' ? discountPct : undefined,
        offer_price: action === 'SET' && valueMode === 'FLAT' ? flatPrice : undefined,
        apply,
        reason: apply ? `Bulk offer ${action} by ${user?.name || 'user'}` : undefined,
      });
      setResult(res);
      if (apply) {
        toast.success(`Applied ${res.summary.committed} offer change${res.summary.committed === 1 ? '' : 's'}` +
          (res.summary.counts.violations ? ` · ${res.summary.counts.violations} skipped (cap)` : ''));
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Bulk offer operation failed');
    } finally {
      setBusy(false);
    }
  }, [scope, action, valueMode, discountPct, flatPrice, user, toast]);

  // Auto-run a dry-run preview (read-only) so the SKU list is never mysteriously
  // empty -- mirrors the price tab. Debounced; latest `run` held in a ref.
  const runRef = useRef(run);
  useEffect(() => {
    runRef.current = run;
  }, [run]);
  useEffect(() => {
    const t = setTimeout(() => runRef.current(false), 450);
    return () => clearTimeout(t);
  }, [scope.category, scope.brand, scope.storeId, action, valueMode, discountPct, flatPrice]);

  return (
    <>
      <StatStrip result={result} />

      {/* Offer builder card */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">Offer action</label>
            <select value={action} onChange={(e) => setAction(e.target.value as 'SET' | 'CLEAR')}
              className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm">
              <option value="SET">Set offer (discount off MRP)</option>
              <option value="CLEAR">Clear offer (reset to MRP)</option>
            </select>
          </div>
          {action === 'SET' && (
            <>
              <div>
                <label className="block text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">By</label>
                <select value={valueMode} onChange={(e) => setValueMode(e.target.value as 'PERCENT' | 'FLAT')}
                  className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm">
                  <option value="PERCENT">Discount %</option>
                  <option value="FLAT">Flat offer price &#8377;</option>
                </select>
              </div>
              <div>
                <label className="block text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">
                  {valueMode === 'PERCENT' ? 'Percent off MRP' : 'Offer price'}
                </label>
                {valueMode === 'PERCENT' ? (
                  <div className="relative">
                    <input type="number" value={discountPct} min={0} max={100}
                      onChange={(e) => setDiscountPct(Number(e.target.value))}
                      className="border border-gray-300 rounded-lg pl-3 pr-7 py-1.5 text-sm w-28 tabular-nums" />
                    <Percent className="w-3.5 h-3.5 text-gray-400 absolute right-2.5 top-2.5" />
                  </div>
                ) : (
                  <input type="number" value={flatPrice} min={0}
                    onChange={(e) => setFlatPrice(Number(e.target.value))}
                    className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm w-32 tabular-nums" />
                )}
              </div>
            </>
          )}
          <div className="flex-1" />
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => run(false)} disabled={busy}
              className="inline-flex items-center gap-1.5 border border-gray-300 text-gray-600 hover:bg-gray-50 rounded-lg px-3.5 py-1.5 text-sm disabled:opacity-50">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Preview
            </button>
            <button type="button" onClick={() => run(true)} disabled={busy || !result || validCount === 0}
              className="inline-flex items-center gap-1.5 bg-bv hover:bg-bv-600 text-white rounded-lg px-3.5 py-1.5 text-sm font-medium disabled:opacity-40">
              Apply {validCount} valid row{validCount === 1 ? '' : 's'}
            </button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4 items-start">
        <ScopePicker scope={scope} setScope={setScope} categories={categories} brands={brands} stores={stores} />
        <div>
          {result && result.summary.counts.violations > 0 && (
            <div className="mb-3 flex items-start gap-2 rounded-lg border border-bv/30 bg-bv-50 px-3 py-2 text-[12.5px] text-bv">
              <ShieldAlert className="w-4 h-4 mt-0.5 shrink-0" />
              <span>
                {result.summary.counts.violations} row(s) exceed a discount cap and will be skipped (not clamped).
                Lower the discount or narrow the scope.
              </span>
            </div>
          )}
          <PreviewTable rows={result?.rows ?? []} kind="offer" />
        </div>
      </div>
    </>
  );
}

// ----------------------------------------------------------------------------
// Top-level page
// ----------------------------------------------------------------------------

export default function PricingOffersPage() {
  const toast = useToast();
  const [tab, setTab] = useState<'price' | 'offer'>('price');
  const [categories, setCategories] = useState<string[]>([]);
  const [brands, setBrands] = useState<string[]>([]);
  const [stores, setStores] = useState<StoreOpt[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [cat, brd, st] = await Promise.all([
          productApi.getCategories().catch(() => ({ categories: [] })),
          productApi.getBrands().catch(() => ({ brands: [] })),
          storeApi.getStores().catch(() => ({ stores: [] })),
        ]);
        if (!alive) return;
        setCategories((cat?.categories || []).filter(Boolean).sort());
        setBrands((brd?.brands || []).filter(Boolean).sort());
        setStores(st?.stores || []);
      } catch (e) {
        if (alive) toast.error(e instanceof Error ? e.message : 'Failed to load scope options');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [toast]);

  return (
    <div className="p-6 max-w-full">
      <div className="mb-5">
        <div className="text-[10.5px] font-mono uppercase tracking-wider text-gray-400 mb-1.5">Pricing &amp; Offers</div>
        <h1 className="text-2xl font-semibold text-gray-900 flex items-center gap-2">
          <Tag className="w-5 h-5" /> Edit many SKUs at once
        </h1>
        <p className="mt-1 text-sm text-gray-500">
          Preview every change against the discount caps before committing. Cap-violating rows are skipped, never silently clamped.
        </p>
      </div>

      <div className="flex gap-1 border-b border-gray-200 mb-4">
        {([['price', 'Bulk price update'], ['offer', 'Bulk offer update']] as const).map(([t, label]) => (
          <button key={t} type="button" onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px ${tab === t ? 'border-bv text-bv' : 'border-transparent text-gray-500 hover:text-gray-700'}`}>
            {label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading scope options...</div>
      ) : tab === 'price' ? (
        <BulkPriceTab categories={categories} brands={brands} stores={stores} />
      ) : (
        <BulkOfferTab categories={categories} brands={brands} stores={stores} />
      )}
    </div>
  );
}
