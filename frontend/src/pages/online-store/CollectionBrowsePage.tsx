// ============================================================================
// IMS 2.0 - Collection Browse  (unification step-13)
// ============================================================================
// Read-only, restrained browse over MATERIALISED collection membership, served
// by GET /api/v1/collections + GET /api/v1/collections/{handle}/products (the
// fast `collection_products` view). This is the catalogue-facing browse — the
// rule editor + manual membership live in CollectionsPage. Pick a collection on
// the left, browse its paged members on the right; catalogue roles can force a
// re-materialise. Light theme, neutral + single accent. Fail-soft throughout.

import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Layers, ArrowLeft, RefreshCw, Loader2, Sparkles, ListChecks } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import {
  collectionBrowseApi,
  type BrowseCollection,
  type BrowsePage,
} from '../../services/api/collectionBrowse';

const PAGE_SIZE = 24;
const REFRESH_ROLES = ['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER', 'DESIGN_MANAGER'];

function rupee(n?: number | null): string {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return `₹${Number(n).toLocaleString('en-IN')}`;
}

export default function CollectionBrowsePage() {
  const toast = useToast();
  const { user } = useAuth();
  const roles: string[] = (user?.roles as string[]) || [];
  const canRefresh = roles.some((r) => REFRESH_ROLES.includes(r));

  const [collections, setCollections] = useState<BrowseCollection[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [active, setActive] = useState<string | null>(null);
  const [page, setPage] = useState<BrowsePage | null>(null);
  const [loadingPage, setLoadingPage] = useState(false);
  const [skip, setSkip] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoadingList(true);
    collectionBrowseApi.list().then((rows) => {
      if (!alive) return;
      setCollections(rows);
      setLoadingList(false);
      if (rows.length && !active) setActive(rows[0].handle);
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadPage = useCallback((handle: string, nextSkip: number) => {
    setLoadingPage(true);
    collectionBrowseApi.products(handle, nextSkip, PAGE_SIZE).then((p) => {
      setPage(p);
      setSkip(nextSkip);
      setLoadingPage(false);
    });
  }, []);

  useEffect(() => {
    if (active) loadPage(active, 0);
  }, [active, loadPage]);

  const onRefresh = async () => {
    if (!active) return;
    setRefreshing(true);
    try {
      const res = await collectionBrowseApi.refresh(active);
      toast.success(`Recomputed membership: ${res.products_count} products`);
      loadPage(active, 0);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Refresh failed';
      toast.error(msg);
    } finally {
      setRefreshing(false);
    }
  };

  const total = page?.total ?? 0;
  const showingTo = Math.min(skip + PAGE_SIZE, total);

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <Link
            to="/online-store/collections"
            className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-1"
          >
            <ArrowLeft size={14} /> Collections editor
          </Link>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <Layers size={20} /> Browse collections
          </h1>
          <p className="text-sm text-gray-500">
            Materialised membership — what a shopper sees in each collection.
          </p>
        </div>
        {canRefresh && active && (
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="btn-secondary inline-flex items-center gap-2"
          >
            {refreshing ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
            Recompute
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[18rem_1fr] gap-6">
        {/* Collection list */}
        <aside className="card p-0 overflow-hidden self-start">
          <div className="px-4 py-3 border-b border-gray-100 text-sm font-medium text-gray-700">
            Collections
          </div>
          {loadingList ? (
            <div className="p-6 text-center text-gray-400">
              <Loader2 size={18} className="animate-spin mx-auto" />
            </div>
          ) : collections.length === 0 ? (
            <div className="p-6 text-sm text-gray-400 text-center">No collections yet.</div>
          ) : (
            <ul className="divide-y divide-gray-100 max-h-[70vh] overflow-auto">
              {collections.map((c) => (
                <li key={c.id || c.handle}>
                  <button
                    type="button"
                    onClick={() => setActive(c.handle)}
                    className={`w-full text-left px-4 py-3 flex items-center justify-between gap-2 hover:bg-gray-50 ${
                      active === c.handle ? 'bg-gray-50 border-l-2 border-bv-red-600' : ''
                    }`}
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-gray-900">{c.title}</span>
                      <span className="block truncate text-xs text-gray-400">/{c.handle}</span>
                    </span>
                    <span className="shrink-0 inline-flex items-center gap-1 text-xs text-gray-500">
                      {c.collection_type === 'SMART' ? (
                        <Sparkles size={12} />
                      ) : (
                        <ListChecks size={12} />
                      )}
                      {c.products_count}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        {/* Member grid */}
        <section>
          {!active ? (
            <div className="card p-10 text-center text-gray-400">Pick a collection.</div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-3">
                <p className="text-sm text-gray-500">
                  {total === 0
                    ? 'No products in this collection.'
                    : `Showing ${skip + 1}–${showingTo} of ${total}`}
                </p>
              </div>
              {loadingPage ? (
                <div className="card p-10 text-center text-gray-400">
                  <Loader2 size={20} className="animate-spin mx-auto" />
                </div>
              ) : !page || page.products.length === 0 ? (
                <div className="card p-10 text-center text-gray-400">Empty collection.</div>
              ) : (
                <>
                  <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 gap-4">
                    {page.products.map((p) => (
                      <div key={p.sku} className="card p-3 flex flex-col">
                        <div className="aspect-square bg-gray-50 rounded-md mb-2 overflow-hidden flex items-center justify-center">
                          {p.image ? (
                            // eslint-disable-next-line jsx-a11y/img-redundant-alt
                            <img
                              src={p.image}
                              alt={p.title || p.sku}
                              className="object-cover w-full h-full"
                              loading="lazy"
                            />
                          ) : (
                            <Layers size={28} className="text-gray-300" />
                          )}
                        </div>
                        <span className="text-xs text-gray-400">{p.brand || p.category || '—'}</span>
                        <span className="text-sm text-gray-900 truncate" title={p.title || p.sku}>
                          {p.title || p.sku}
                        </span>
                        <span className="text-sm font-medium text-gray-900 mt-auto pt-1">
                          {rupee(p.offer_price)}
                        </span>
                      </div>
                    ))}
                  </div>
                  {total > PAGE_SIZE && (
                    <div className="flex items-center justify-center gap-3 mt-6">
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={skip === 0}
                        onClick={() => loadPage(active, Math.max(0, skip - PAGE_SIZE))}
                      >
                        Previous
                      </button>
                      <button
                        type="button"
                        className="btn-secondary"
                        disabled={showingTo >= total}
                        onClick={() => loadPage(active, skip + PAGE_SIZE)}
                      >
                        Next
                      </button>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
