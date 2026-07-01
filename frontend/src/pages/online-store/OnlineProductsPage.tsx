// ============================================================================
// IMS 2.0 - Online Store - Products / PIM  (BVI Phase 1)
// ============================================================================
// Read-only list of the product catalog as it appears (or will appear) online.
// Each row surfaces the online-facing facts: title (brand + model), category,
// brand, the online price (offer_price, GST-inclusive), the bridged variant
// count when present, and whether the SKU is currently live online / mapped to
// physical stock (via the existing e-commerce online-status bridge).
//
// This is the FIRST live surface of the "Products / PIM" section on the Online
// Store shell (SECTIONS[0]). It is a LIST + SEARCH only for this phase — there
// is no editor here; the product-add / edit doors already live under
// /catalog/add and /inventory. Nothing is written to Shopify from this screen.
//
// Reads:
//   - GET /api/v1/products                (the canonical catalog list)
//   - POST /api/v1/catalog/online-status  (per-SKU online + online_stock bridge;
//                                           fully fail-soft, returns {} when off)
//
// FAIL-SOFT: both reads degrade quietly. If the catalog read fails the screen
// shows a friendly empty state (never a white screen); if the online-status
// bridge is off, the online column simply reads "—" (unknown) rather than
// blocking the list. Gated SUPERADMIN / ADMIN / CATALOG_MANAGER / DESIGN_MANAGER
// at the route (App.tsx), matching the rest of the module. Light theme only.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Package,
  ArrowLeft,
  RefreshCw,
  Loader2,
  Search,
  Layers,
  Globe,
  CircleSlash,
} from 'lucide-react';
import { productApi, catalogApi, type OnlineStatus } from '../../services/api/products';

// ---------------------------------------------------------------------------
// A slim, presentation-only product row (whatever the catalog list emits, we
// project onto these fields; everything optional so a partial doc never breaks).
// ---------------------------------------------------------------------------
interface ProductRow {
  product_id: string;
  sku: string | null;
  title: string;
  brand: string | null;
  category: string | null;
  online_price: number | null;
  mrp: number | null;
  variant_count: number | null;
}

/** Project a raw catalog doc onto ProductRow, tolerating the several shapes the
 *  catalog list has carried (title / model / model_no / name; variants array or
 *  a bridged variant_count). */
function toRow(p: Record<string, any>): ProductRow {
  const brand = p.brand ?? null;
  const model = p.model ?? p.model_no ?? p.model_name ?? p.title ?? p.name ?? null;
  const title =
    [brand, model].filter(Boolean).join(' ').trim() ||
    (p.sku ? String(p.sku) : 'Untitled product');
  const variantCount =
    typeof p.variant_count === 'number'
      ? p.variant_count
      : Array.isArray(p.variants)
        ? p.variants.length
        : typeof p.variants_count === 'number'
          ? p.variants_count
          : null;
  return {
    product_id: String(p.product_id ?? p.id ?? p._id ?? ''),
    sku: p.sku ?? null,
    title,
    brand,
    category: p.category ?? null,
    online_price:
      typeof p.offer_price === 'number'
        ? p.offer_price
        : typeof p.online_price === 'number'
          ? p.online_price
          : typeof p.mrp === 'number'
            ? p.mrp
            : null,
    mrp: typeof p.mrp === 'number' ? p.mrp : null,
    variant_count: variantCount,
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

/** Humanise a raw category token ("READING_GLASSES" -> "Reading glasses"). */
function humanise(s: string | null | undefined): string {
  if (!s) return '—';
  const t = String(s).replace(/[_-]+/g, ' ').trim().toLowerCase();
  if (!t) return '—';
  return t.charAt(0).toUpperCase() + t.slice(1);
}

// ===========================================================================
// Page
// ===========================================================================
export default function OnlineProductsPage() {
  const [rows, setRows] = useState<ProductRow[]>([]);
  const [online, setOnline] = useState<Record<string, OnlineStatus>>({});
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // The catalog list. Fail-soft: any error -> empty list (friendly state).
      let raw: Record<string, any>[] = [];
      try {
        const res = await productApi.getProducts();
        const arr = Array.isArray(res) ? res : (res?.products ?? res?.items ?? []);
        raw = Array.isArray(arr) ? arr : [];
      } catch {
        raw = [];
      }
      const projected = raw.map(toRow).filter((r) => r.product_id);
      setRows(projected);

      // Enrich with the online-status bridge (per SKU). Fully fail-soft: an
      // unconfigured/unreachable bridge returns {} so the column reads "unknown".
      const skus = projected.map((r) => r.sku).filter(Boolean) as string[];
      if (skus.length > 0) {
        try {
          const statuses = await catalogApi.getOnlineStatus(skus);
          setOnline(statuses || {});
        } catch {
          setOnline({});
        }
      } else {
        setOnline({});
      }
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
      [r.title, r.sku, r.brand, r.category]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q)),
    );
  }, [rows, search]);

  const onlineCount = useMemo(
    () => Object.values(online).filter((s) => s?.online).length,
    [online],
  );

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
            <span className="text-gray-700">Products / PIM</span>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <Package className="w-5 h-5" /> Products / PIM
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
        The product catalog as it appears online — title, category, brand, the online price and the
        bridged variant tier. The <span className="font-medium text-gray-700">Online</span> column
        shows whether each SKU is currently live on the storefront and mapped to physical stock. This
        is a read-only view; products are added and edited from the catalog.
      </p>

      {/* Toolbar */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by title, SKU, brand, or category…"
            className="input-field w-full pl-9"
          />
        </div>
        {!loading && (
          <span className="text-xs text-gray-500">
            {visible.length.toLocaleString('en-IN')} product{visible.length !== 1 ? 's' : ''}
            {onlineCount > 0 ? ` · ${onlineCount.toLocaleString('en-IN')} live online` : ''}
          </span>
        )}
      </div>

      {/* List */}
      {loading ? (
        <div className="rounded-xl border border-gray-200 bg-white p-6 flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading products…
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-10 text-center text-gray-500">
          <Package className="w-10 h-10 mx-auto mb-2 opacity-50" />
          <p className="text-sm">
            {search
              ? 'No products match this search.'
              : 'No products in the catalog yet. Add products from the catalog to see them here.'}
          </p>
        </div>
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="text-left font-medium px-4 py-2.5">Product</th>
                  <th className="text-left font-medium px-4 py-2.5">Category</th>
                  <th className="text-right font-medium px-4 py-2.5 w-28">Online price</th>
                  <th className="text-right font-medium px-4 py-2.5 w-24">Variants</th>
                  <th className="text-left font-medium px-4 py-2.5 w-40">Online</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {visible.map((r, idx) => {
                  const status = r.sku ? online[r.sku] : undefined;
                  const isOnline = !!status?.online;
                  const knownStatus = !!status;
                  return (
                    <tr key={r.product_id || r.sku || idx} className="hover:bg-gray-50">
                      <td className="px-4 py-2.5">
                        <div className="font-medium text-gray-900">{r.title}</div>
                        <div className="text-xs text-gray-400">
                          {r.sku || '—'}
                          {r.brand ? ` · ${r.brand}` : ''}
                        </div>
                      </td>
                      <td className="px-4 py-2.5 text-gray-700">{humanise(r.category)}</td>
                      <td className="px-4 py-2.5 text-right text-gray-900 font-medium whitespace-nowrap">
                        {fmtMoney(r.online_price)}
                      </td>
                      <td className="px-4 py-2.5 text-right text-gray-700">
                        {r.variant_count === null ? (
                          <span className="text-gray-400">—</span>
                        ) : (
                          <span className="inline-flex items-center gap-1">
                            <Layers className="w-3.5 h-3.5 text-gray-400" />
                            {r.variant_count}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5">
                        {!knownStatus ? (
                          <span className="inline-flex items-center gap-1 text-xs text-gray-400">
                            <CircleSlash className="w-3.5 h-3.5" /> Unknown
                          </span>
                        ) : isOnline ? (
                          <span
                            className="inline-flex items-center gap-1 rounded-full bg-green-100 text-green-800 border border-green-200 px-2 py-0.5 text-[11px] font-medium"
                            title={
                              typeof status?.online_stock === 'number'
                                ? `${status.online_stock} in online stock`
                                : 'Live online'
                            }
                          >
                            <Globe className="w-3 h-3" /> Live
                            {typeof status?.online_stock === 'number'
                              ? ` · ${status.online_stock} in stock`
                              : ''}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 text-gray-600 border border-gray-200 px-2 py-0.5 text-[11px] font-medium">
                            <CircleSlash className="w-3 h-3" /> Not online
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <p className="mt-6 text-xs text-gray-400">
        Online Store module · Products / PIM. A read-only view of the catalog as it appears online.
      </p>
    </div>
  );
}
