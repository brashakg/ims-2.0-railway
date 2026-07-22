// ============================================================================
// IMS 2.0 - Online Store - Products / PIM  (BVI Phase 1, rebuilt on the PIM)
// ============================================================================
// The online catalog master, listed from `catalog_products` (the collection the
// Shopify push actually reads/writes) — NOT the billing spine. Each row shows
// the online-facing facts: title, category, brand, the online price
// (offer_price, GST-inclusive) and a TRUTHFUL per-row website state driven by
// the doc's own ecom sub-doc (ecom.status + ecom.shopify_product_id +
// ecom.locally_modified), so the rows always agree with the header strip counts
// on this same screen.
//
// Server-side pagination + search (audit OS-003): GET /catalog/products with
// {search, page, limit, is_active: 'all'} and a total-driven Pagination — the
// full ~4,400-doc catalog is reachable, and search covers ALL of it, not just
// the first page. "Send to website" (audit OS-004) posts the catalog doc's own
// `id` — exactly the id space the push route resolves. The standard
// DARK/LIVE OnlineStoreSyncBanner sits above the list (audit OS-039), and a
// failed fetch renders a distinct "Couldn't load — Retry" state instead of
// masquerading as an empty catalog (audit OS-040).
//
// Reads:
//   - GET  /api/v1/catalog/products      (paged catalog_products list + total)
//   - GET  /api/v1/online-store/summary  (header strip; fail-soft)
//   - GET  /api/v1/online-store/push/status  (via OnlineStoreSyncBanner)
// Writes (SUPERADMIN / ADMIN only, mirroring online_store_push._PUSH_ROLES):
//   - POST /api/v1/online-store/push/product/{catalog_id}  (dry-run unless the
//     live gates are armed; formatPushResult stamps SIMULATED vs LIVE).
//
// Gated SUPERADMIN / ADMIN / CATALOG_MANAGER / DESIGN_MANAGER at the route
// (App.tsx), matching the rest of the module. Light theme only.

import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Package,
  ArrowLeft,
  RefreshCw,
  Loader2,
  Search,
  AlertTriangle,
  Send,
} from 'lucide-react';
import {
  catalogProductsApi,
  type CatalogProductDoc,
} from '../../services/api/catalog';
import { onlineStoreApi, pushApi, type OnlineStoreSummary } from '../../services/api/onlineStore';
import OnlineStoreSyncBanner, {
  SyncChip,
  formatPushResult,
  type OnlineStoreSyncBannerHandle,
} from '../../components/online-store/OnlineStoreSyncBanner';
import { Pagination } from '../../components/common/Pagination';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// A slim, presentation-only product row projected from a catalog_products doc.
// Everything optional-tolerant so a partial/legacy doc never breaks the list.
// ---------------------------------------------------------------------------
interface ProductRow {
  /** catalog_products.id — the exact id space the push route resolves. */
  product_id: string;
  sku: string | null;
  title: string;
  brand: string | null;
  category: string | null;
  online_price: number | null;
  mrp: number | null;
  /** The doc carries an ecom sub-doc (was staged for the online store). */
  staged: boolean;
  /** ecom.shopify_product_id present — pushed to Shopify at least once. */
  shopify_mapped: boolean;
  /** ecom.locally_modified — edited since the last push (dirty). */
  locally_modified: boolean;
  /** ecom.status (DRAFT | PUBLISHED | ARCHIVED) when staged. */
  ecom_status: string | null;
}

/** Project a raw catalog_products doc onto ProductRow, tolerating the shapes
 *  the collection carries (BVI-imported docs: sku/title; door-created spine
 *  mirrors: parent_sku/name; pricing either top-level or nested). */
function toRow(p: CatalogProductDoc): ProductRow {
  const raw = p as Record<string, any>;
  const brand = raw.brand ?? null;
  const model = raw.model ?? raw.model_no ?? raw.model_name ?? null;
  const title =
    (raw.title ?? raw.name ?? '') ||
    [brand, model].filter(Boolean).join(' ').trim() ||
    (raw.sku ? String(raw.sku) : raw.parent_sku ? String(raw.parent_sku) : 'Untitled product');
  const pricing = (raw.pricing && typeof raw.pricing === 'object' ? raw.pricing : {}) as Record<
    string,
    any
  >;
  const num = (v: unknown): number | null => (typeof v === 'number' && isFinite(v) ? v : null);
  const ecomRaw = raw.ecom;
  const staged = !!(ecomRaw && typeof ecomRaw === 'object');
  const ecom = (staged ? ecomRaw : {}) as Record<string, any>;
  return {
    product_id: String(raw.id ?? raw.product_id ?? raw._id ?? ''),
    sku: raw.sku ?? raw.parent_sku ?? null,
    title,
    brand,
    category: raw.category ?? raw.category_name ?? null,
    online_price:
      num(raw.offer_price) ?? num(pricing.offer_price) ?? num(raw.mrp) ?? num(pricing.mrp),
    mrp: num(raw.mrp) ?? num(pricing.mrp),
    staged,
    shopify_mapped: !!ecom.shopify_product_id,
    locally_modified: !!ecom.locally_modified,
    ecom_status: ecom.status ? String(ecom.status).toUpperCase() : null,
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

/** Format an integer count (null/undefined -> "0") with en-IN grouping. */
function fmtInt(n: number | null | undefined): string {
  const v = typeof n === 'number' && isFinite(n) ? n : 0;
  try {
    return v.toLocaleString('en-IN');
  } catch {
    return String(v);
  }
}

/** Humanise a raw category token ("READING_GLASSES" -> "Reading glasses"). */
function humanise(s: string | null | undefined): string {
  if (!s) return '—';
  const t = String(s).replace(/[_-]+/g, ' ').trim().toLowerCase();
  if (!t) return '—';
  return t.charAt(0).toUpperCase() + t.slice(1);
}

// ---------------------------------------------------------------------------
// Plain-English website-visibility chip (same vocabulary as the catalog
// drawer's EcomStatusChip + the header strip): staged draft vs draft on the
// website vs published vs archived vs never staged. Rendered NEXT TO the
// SyncChip — SyncChip answers "pushed / dirty?", this answers "visible?".
// ---------------------------------------------------------------------------
function EcomStatusChip({ row }: { row: ProductRow }) {
  if (!row.staged) {
    return (
      <span
        className="inline-flex items-center rounded-full bg-gray-100 text-gray-500 border border-gray-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap"
        title="No ecom sub-doc — this product was never staged for the online store"
      >
        Not staged
      </span>
    );
  }
  const st = (row.ecom_status || '').toUpperCase();
  if (st === 'PUBLISHED') {
    return (
      <span
        className="inline-flex items-center rounded-full bg-green-100 text-green-800 border border-green-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap"
        title="Published — visible on the website"
      >
        Published
      </span>
    );
  }
  if (st === 'ARCHIVED') {
    return (
      <span
        className="inline-flex items-center rounded-full bg-gray-100 text-gray-500 border border-gray-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap"
        title="Archived — hidden from the website"
      >
        Archived
      </span>
    );
  }
  // DRAFT (or a staged doc with no explicit status — treated as draft).
  return row.shopify_mapped ? (
    <span
      className="inline-flex items-center rounded-full bg-gray-100 text-gray-700 border border-gray-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap"
      title="On the website as a hidden draft — not visible to customers yet"
    >
      Draft on website
    </span>
  ) : (
    <span
      className="inline-flex items-center rounded-full bg-gray-100 text-gray-700 border border-gray-200 px-2 py-0.5 text-[11px] font-medium whitespace-nowrap"
      title="Staged for online but not sent to the website yet"
    >
      Staged (draft)
    </span>
  );
}

// ===========================================================================
// Page
// ===========================================================================
export default function OnlineProductsPage() {
  const { hasRole } = useAuth();
  const toast = useToast();
  // "Send to website" is a live-write affordance -> SUPERADMIN / ADMIN only,
  // mirroring the backend push gate (online_store_push.py _PUSH_ROLES). CM/DM
  // see the sync chips but not the button (the backend would 403 them anyway).
  const canPush = hasRole(['SUPERADMIN', 'ADMIN']);

  const [rows, setRows] = useState<ProductRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [summary, setSummary] = useState<OnlineStoreSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [pushingId, setPushingId] = useState<string | null>(null);
  const bannerRef = useRef<OnlineStoreSyncBannerHandle>(null);

  // Debounce the search box into the server-side query; a new search always
  // lands on page 1 (both set in the same tick so load() fires once).
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedSearch(search.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(async () => {
    setLoading(true);
    setFetchError(false);

    // The DRAFT/PUBLISHED/text-only breakdown for the header strip (fail-soft;
    // getSummary never throws into the page).
    onlineStoreApi.getSummary().then(setSummary).catch(() => {});

    // The catalog master, server-paged + server-searched. is_active 'all'
    // because staged BVI imports carry is_active=false — the header strip
    // counts ALL staged docs, so the list must too or the two contradict.
    try {
      const res = await catalogProductsApi.list({
        search: debouncedSearch || undefined,
        is_active: 'all',
        page,
        limit: PAGE_SIZE,
      });
      const docs = Array.isArray(res?.products) ? res.products : [];
      setRows(docs.map(toRow).filter((r) => r.product_id));
      setTotal(Number(res?.total ?? docs.length));
    } catch {
      // A failed fetch is an ERROR state, never "empty catalog" (audit OS-040).
      setRows([]);
      setTotal(0);
      setFetchError(true);
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, page]);

  useEffect(() => {
    load();
  }, [load]);

  // Single-product push. DARK by default: unless the owner has armed the
  // triple-gate on the server this is a SIMULATED dry-run and nothing reaches
  // the storefront — formatPushResult stamps SIMULATED vs LIVE on the toast so
  // it can never be mistaken, and the banner above the list shows the current
  // posture BEFORE the click.
  const pushRow = async (row: ProductRow) => {
    if (!canPush || pushingId) return;
    const ok = window.confirm(
      `Send "${row.title}" to the website?\n\n` +
        'If the live gates are off this runs as a dry-run (SIMULATED) and nothing reaches ' +
        'the storefront. When the gates are armed this writes to the live website.',
    );
    if (!ok) return;
    setPushingId(row.product_id);
    try {
      const res = await pushApi.pushProduct(row.product_id);
      const msg = formatPushResult(row.title, res);
      if (res.ok) toast.success(msg);
      else toast.error(msg);
      // A LIVE push may have mapped a Shopify id -> refresh rows, header
      // counts AND the posture banner.
      load();
      bannerRef.current?.refresh();
    } catch (e: any) {
      toast.error(
        `${row.title}: push failed — ${e?.response?.data?.detail || e?.message || 'error'}`,
      );
    } finally {
      setPushingId(null);
    }
  };

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
        The online product catalog — title, category, brand, the online price and each product{"'"}s
        <span className="font-medium text-gray-700"> website</span> state: whether it has been sent
        to the website, is visible to customers (published) or hidden (draft), and whether it
        carries local edits not yet pushed. Products are added and edited from the catalog; admins
        can send a product to the website here.
      </p>

      {/* The standard DARK/LIVE push-posture banner every push surface carries. */}
      <OnlineStoreSyncBanner ref={bannerRef} className="mb-4" />

      {/* Toolbar */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search the whole catalog by title, SKU, or attributes…"
            className="input-field w-full pl-9"
          />
        </div>
        {!loading && !fetchError && (
          <span className="text-xs text-gray-500">
            {fmtInt(total)} product{total !== 1 ? 's' : ''}
            {debouncedSearch ? ' matching' : ''}
          </span>
        )}
      </div>

      {/* Staged-product breakdown (DRAFT vs PUBLISHED vs text-only) — makes the
          publish decision visible. Reads the extended /online-store/summary. */}
      {summary?.products_ecom && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span
            className="inline-flex items-center rounded-full bg-gray-100 text-gray-700 border border-gray-200 px-2.5 py-1 text-xs font-medium"
            title="Staged for online but not visible to customers yet"
          >
            {fmtInt(summary.products_ecom.draft)} staged as draft
          </span>
          <span
            className="inline-flex items-center rounded-full bg-green-100 text-green-800 border border-green-200 px-2.5 py-1 text-xs font-medium"
            title="Published — visible on the website"
          >
            {fmtInt(summary.products_ecom.published)} published
          </span>
          <span
            className="inline-flex items-center rounded-full bg-amber-100 text-amber-800 border border-amber-200 px-2.5 py-1 text-xs font-medium"
            title="Staged products carrying no images — text-only until re-imaged"
          >
            {fmtInt(summary.products_ecom.text_only)} text-only (no images)
          </span>
        </div>
      )}

      {/* List */}
      {fetchError ? (
        // Distinct ERROR state (audit OS-040) — never confusable with an empty
        // catalog on a system holding 4,400+ products.
        <div className="rounded-xl border border-red-200 bg-red-50 p-10 text-center">
          <AlertTriangle className="w-10 h-10 mx-auto mb-2 text-red-400" />
          <p className="text-sm font-medium text-red-800 mb-1">{"Couldn't load products"}</p>
          <p className="text-xs text-red-700/80 mb-4">
            The catalog did not respond. Your products are safe — this is a loading problem, not an
            empty catalog.
          </p>
          <button
            type="button"
            onClick={load}
            className="btn-outline inline-flex items-center gap-1.5 text-sm"
          >
            <RefreshCw className="w-4 h-4" /> Retry
          </button>
        </div>
      ) : loading ? (
        <div className="rounded-xl border border-gray-200 bg-white p-6 flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading products…
        </div>
      ) : rows.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-10 text-center text-gray-500">
          <Package className="w-10 h-10 mx-auto mb-2 opacity-50" />
          <p className="text-sm">
            {debouncedSearch
              ? 'No products match this search.'
              : 'No products in the catalog yet. Add products from the catalog to see them here.'}
          </p>
        </div>
      ) : (
        <>
          <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-600">
                  <tr>
                    <th className="text-left font-medium px-4 py-2.5">Product</th>
                    <th className="text-left font-medium px-4 py-2.5">Category</th>
                    <th className="text-right font-medium px-4 py-2.5 w-28">Online price</th>
                    <th className="text-left font-medium px-4 py-2.5 w-72">Website</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {rows.map((r, idx) => (
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
                      <td className="px-4 py-2.5">
                        <div className="flex flex-wrap items-center gap-2">
                          {/* Truthful per-row state (audit OS-023): pushed?
                              (SyncChip, dirty when edited since) + visible?
                              (EcomStatusChip) — both read the doc's own ecom
                              sub-doc, the same fields the header strip counts. */}
                          <SyncChip
                            synced={r.shopify_mapped}
                            pending={r.shopify_mapped && r.locally_modified}
                          />
                          <EcomStatusChip row={r} />
                          {canPush && (
                            <button
                              type="button"
                              onClick={() => pushRow(r)}
                              disabled={pushingId === r.product_id || !r.staged}
                              className="inline-flex items-center gap-1 rounded-lg border border-gray-300 bg-white px-2 py-1 text-[11px] font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
                              title={
                                r.staged
                                  ? 'Send this product to the website (a dry-run unless the live gates are armed)'
                                  : 'Stage this product for the online store first — it has no ecom sub-doc'
                              }
                            >
                              {pushingId === r.product_id ? (
                                <Loader2 className="w-3 h-3 animate-spin" />
                              ) : (
                                <Send className="w-3 h-3" />
                              )}
                              Send to website
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <Pagination
            currentPage={page}
            totalItems={total}
            pageSize={PAGE_SIZE}
            onPageChange={setPage}
          />
        </>
      )}

      <p className="mt-6 text-xs text-gray-400">
        Online Store module · Products / PIM. The catalog as it appears online. Sending to the
        website is a dry-run (SIMULATED) unless the owner has armed the live gates — see the banner
        above and the Shopify sync page.
      </p>
    </div>
  );
}
