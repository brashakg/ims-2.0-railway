// ============================================================================
// IMS 2.0 - Collections (Phase 1) — merchandising landing page
// ============================================================================
// KPI cards over EVERY collection (custom + smart) from
// GET /collections/insights/summary. The BVI online catalogue carries ~1,160
// auto-generated collections that would drown the list, so the DEFAULT view
// shows only rows that are commercially alive (sold in the last 30 days OR
// holding stock); a "Show all" toggle reveals the full online catalogue.
// Fail-soft: while the Track 2 insights backend isn't deployed the page
// renders an explanatory empty state (never crashes).

import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Layers, Plus, Search, Loader2, Sparkles, ListChecks, FileDown } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import {
  collectionsInsightsApi,
  type CollectionSummaryRow,
} from '../../services/api/collectionsInsights';
import CataloguePdfModal from '../../components/catalogue/CataloguePdfModal';
import { rupee, fmtInt, basisLabel } from './collectionsShared';

// Roles that may CREATE from this surface (a pure STORE_MANAGER is view-only;
// mirrors the hidden "New collection" button — backend enforces write authz).
const CREATE_ROLES = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'CATALOG_MANAGER'];

// Ask for enough rows to cover the full BVI online catalogue (~1,160). The
// service retries with the server default if this limit is rejected.
const SUMMARY_LIMIT = 1500;

export default function CollectionsListPage() {
  const { user } = useAuth();
  const roles: string[] = (user?.roles as string[]) || [];
  const canCreate = roles.some((r) => CREATE_ROLES.includes(r));

  const [rows, setRows] = useState<CollectionSummaryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [search, setSearch] = useState('');
  // "Share as PDF" target (collection id + title) for the reusable modal.
  const [shareTarget, setShareTarget] = useState<{ id: string; title: string } | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    collectionsInsightsApi.summary(SUMMARY_LIMIT).then((list) => {
      if (!alive) return;
      setRows(list);
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, []);

  const activeRows = useMemo(
    () => rows.filter((r) => (r.sold_30d ?? 0) > 0 || (r.on_hand ?? 0) > 0),
    [rows],
  );

  const visible = useMemo(() => {
    const base = showAll ? rows : activeRows;
    const q = search.trim().toLowerCase();
    const filtered = q ? base.filter((r) => (r.title || '').toLowerCase().includes(q)) : base;
    // Commercially-hot first: sold 30d desc, then on-hand desc, then title.
    return [...filtered].sort(
      (a, b) =>
        (b.sold_30d ?? 0) - (a.sold_30d ?? 0) ||
        (b.on_hand ?? 0) - (a.on_hand ?? 0) ||
        (a.title || '').localeCompare(b.title || ''),
    );
  }, [rows, activeRows, showAll, search]);

  const hiddenCount = rows.length - activeRows.length;

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-6">
        <div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <Layers size={20} /> Collections
          </h1>
          <p className="text-sm text-gray-500">
            Stock and sales at a glance for every merchandising group — frames, brands, lens
            features, price bands.
          </p>
        </div>
        {canCreate && (
          <Link to="/collections/new" className="btn-primary inline-flex items-center gap-2">
            <Plus size={16} /> New collection
          </Link>
        )}
      </div>

      {/* Toolbar: search + show-all toggle */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="relative w-full sm:w-80">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search collections…"
            className="input-field pl-9 w-full"
          />
        </div>
        {hiddenCount > 0 && (
          <button
            type="button"
            onClick={() => setShowAll((v) => !v)}
            className="btn-secondary text-sm"
          >
            {showAll
              ? `Hide inactive (${hiddenCount})`
              : `Show all (online catalogue, +${hiddenCount})`}
          </button>
        )}
        {!loading && (
          <span className="text-xs text-gray-400 ml-auto">
            Showing {visible.length} of {rows.length} collections
          </span>
        )}
      </div>

      {/* Body */}
      {loading ? (
        <div className="card p-10 text-center text-gray-400">
          <Loader2 size={20} className="animate-spin mx-auto mb-2" />
          Loading collection insights…
        </div>
      ) : rows.length === 0 ? (
        <div className="card p-10 text-center">
          <Layers size={28} className="mx-auto mb-3 text-gray-300" />
          <p className="text-sm font-medium text-gray-700 mb-1">No collection insights yet</p>
          <p className="text-sm text-gray-500 mb-4">
            Either no collections exist, or the insights service isn't deployed yet.
            {canCreate && ' You can still build a collection now — its numbers appear once the service is live.'}
          </p>
          {canCreate && (
            <Link to="/collections/new" className="btn-primary inline-flex items-center gap-2">
              <Plus size={16} /> New collection
            </Link>
          )}
        </div>
      ) : visible.length === 0 ? (
        <div className="card p-10 text-center text-sm text-gray-500">
          Nothing matches
          {search.trim() ? ` "${search.trim()}"` : ''}
          {!showAll && hiddenCount > 0 && (
            <>
              {' '}
              in the active set —{' '}
              <button
                type="button"
                onClick={() => setShowAll(true)}
                className="text-bv-red-600 hover:underline"
              >
                search the full online catalogue
              </button>
            </>
          )}
          .
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {visible.map((c) => {
            const basis = basisLabel(c.value_basis);
            return (
              <Link
                key={c.collection_id}
                to={`/collections/${encodeURIComponent(c.collection_id)}`}
                className="card p-4 block hover:shadow-md transition-shadow"
              >
                <div className="flex items-start justify-between gap-2 mb-3">
                  <span className="text-sm font-medium text-gray-900 leading-snug line-clamp-2">
                    {c.title || '(untitled)'}
                  </span>
                  <span className="shrink-0 inline-flex items-center gap-1.5">
                    <span
                      className="inline-flex items-center gap-1 text-[11px] text-gray-500"
                      title={c.collection_type === 'SMART' ? 'Rule-based' : 'Manual list'}
                    >
                      {c.collection_type === 'SMART' ? (
                        <Sparkles size={12} />
                      ) : (
                        <ListChecks size={12} />
                      )}
                      {c.collection_type === 'SMART' ? 'Smart' : 'Manual'}
                    </span>
                    <span
                      className={`px-1.5 py-0.5 rounded-full text-[10px] font-medium ${
                        c.published
                          ? 'bg-green-100 text-green-700'
                          : 'bg-gray-100 text-gray-600'
                      }`}
                    >
                      {c.published ? 'Online' : 'Internal'}
                    </span>
                    {/* Share as PDF — opens the reusable modal (preventDefault so
                        the card link does not navigate). */}
                    <button
                      type="button"
                      title="Share as PDF"
                      aria-label="Share as PDF"
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setShareTarget({ id: c.collection_id, title: c.title || 'Collection' });
                      }}
                      className="text-gray-400 hover:text-bv-red-600"
                    >
                      <FileDown size={14} />
                    </button>
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-gray-400">Products</div>
                    <div className="text-gray-900">{fmtInt(c.members)}</div>
                  </div>
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-gray-400">On hand</div>
                    <div className="text-gray-900">{fmtInt(c.on_hand)}</div>
                  </div>
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-gray-400">
                      Stock value
                    </div>
                    <div className="text-gray-900">
                      {rupee(c.stock_value)}
                      {basis && (
                        <span className="ml-1 text-[10px] text-amber-600 align-middle">{basis}</span>
                      )}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-gray-400">Sold 30d</div>
                    <div className="text-gray-900">{fmtInt(c.sold_30d)}</div>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}

      <CataloguePdfModal
        open={!!shareTarget}
        onClose={() => setShareTarget(null)}
        collectionId={shareTarget?.id}
        title={shareTarget?.title}
      />
    </div>
  );
}
