// ============================================================================
// IMS 2.0 - Catalog Manager (/catalog)
// ============================================================================
// The owner's single "what exists and its truth" screen: a photo-grid browse
// over TWO honest data sources behind one segmented control —
//
//   Segment "Catalog"        → GET /products (the billing/stock SPINE,
//                               server-paginated via skip/limit+total_count)
//   Segment "Needs review"   → GET /catalog/products?needs_review=true&
//                               is_active=all (the 4,393 BVI-imported docs;
//                               server-paginated via page/limit+total)
//
// A card click opens the slide-over drawer (view / edit-in-place links /
// review + approve). Approving PROMOTES the imported doc in place (same id)
// — the only thing that clears needs_review. Bulk approve is a client-side
// loop over the single promote endpoint (concurrency 4, cap 200): every item
// passes the identical door validation, so bulk can never force-approve.
//
// POS is untouched — its card look is mirrored here for familiarity only.

import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Search,
  RefreshCw,
  Plus,
  Loader2,
  AlertTriangle,
  ShieldCheck,
  CheckCircle2,
} from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { Pagination } from '../../components/common/Pagination';
import { ImageLightbox } from '../../components/common/ImageLightbox';
// Import DIRECT from the modules (not the services/api barrel — TS2614).
import { productApi } from '../../services/api/products';
import {
  catalogProductsApi,
  type CatalogProductDoc,
} from '../../services/api/catalog';
import { CATEGORY_BROWSE_OPTIONS } from '../../utils/categoryNormalize';
import {
  CatalogProductDrawer,
  CatalogImage,
  docId,
  docName,
  docImages,
  docMrp,
  docOffer,
  type CatalogDrawerItem,
} from './CatalogProductDrawer';

const PAGE_SIZE = 48; // divisible by every column tier (2/3/4/6)
const BULK_CAP = 200;
const BULK_CONCURRENCY = 4;

type Segment = 'catalog' | 'review';

const fmtINR = (n: number | null): string => {
  if (n === null || !Number.isFinite(n)) return '—';
  try {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(n);
  } catch {
    return String(Math.round(n));
  }
};

// ---------------------------------------------------------------------------
// Product card (module level — POS-grid look mirrored, POS never touched).
// ---------------------------------------------------------------------------
function CatalogCard({
  item,
  selectable,
  selected,
  failMessage,
  onToggleSelect,
  onOpen,
  onImageClick,
}: {
  item: CatalogDrawerItem;
  selectable: boolean;
  selected: boolean;
  failMessage?: string;
  onToggleSelect: () => void;
  onOpen: () => void;
  onImageClick: () => void;
}) {
  const doc = item.doc;
  const name = docName(doc);
  const brand = String(
    doc.brand || (doc.attributes as Record<string, unknown>)?.brand_name || ''
  );
  const images = docImages(doc);
  const mrp = docMrp(doc);
  const offer = docOffer(doc);
  const hasDiscount = mrp !== null && offer !== null && offer < mrp;
  const inactive = doc.is_active === false;
  const needsReview = item.kind === 'imported' && Boolean(doc.needs_review);

  return (
    <div
      className={clsx(
        'relative rounded-xl border bg-white text-left transition-all hover:shadow-md',
        selected ? 'border-amber-400 ring-1 ring-amber-300' : 'border-gray-200 hover:border-bv-red-300',
        inactive && 'opacity-50'
      )}
    >
      {selectable && (
        <label className="absolute left-2 top-2 z-10 flex h-6 w-6 cursor-pointer items-center justify-center rounded-md bg-white/90 border border-gray-300 shadow-sm">
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggleSelect}
            className="h-3.5 w-3.5 accent-amber-500"
            aria-label={`Select ${name}`}
          />
        </label>
      )}

      {/* Image area (click → lightbox) */}
      <button
        type="button"
        onClick={images.length > 0 ? onImageClick : onOpen}
        className="block w-full h-36 p-2 rounded-t-xl bg-white flex items-center justify-center overflow-hidden"
        aria-label={images.length > 0 ? `View images of ${name}` : name}
      >
        <CatalogImage
          url={images[0] || ''}
          alt={name}
          className="max-h-full max-w-full object-contain"
        />
      </button>

      {/* Body (click → drawer) */}
      <button type="button" onClick={onOpen} className="block w-full text-left px-3 pb-3">
        {brand && <p className="text-xs font-bold text-gray-900 truncate">{brand}</p>}
        <p className="text-xs text-gray-700 leading-snug line-clamp-2 min-h-[2rem]">{name}</p>
        <div className="mt-1.5 flex items-baseline gap-1.5">
          <span className="text-sm font-bold text-gray-900">{fmtINR(offer ?? mrp)}</span>
          {hasDiscount && (
            <span className="text-[10px] text-gray-500 line-through">{fmtINR(mrp)}</span>
          )}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          {needsReview ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">
              <AlertTriangle className="h-3 w-3" /> Needs review
            </span>
          ) : inactive ? (
            <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-500">
              Inactive
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2 py-0.5 text-[10px] font-medium text-green-700">
              <ShieldCheck className="h-3 w-3" /> POS-ready
            </span>
          )}
          {failMessage && (
            <span
              className="inline-flex items-center rounded-full bg-orange-100 px-2 py-0.5 text-[10px] font-medium text-orange-700"
              title={failMessage}
            >
              needs fixes
            </span>
          )}
        </div>
      </button>
    </div>
  );
}

// ===========================================================================
// Page
// ===========================================================================
export function CatalogManagerPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();

  const [segment, setSegment] = useState<Segment>('catalog');
  const [items, setItems] = useState<CatalogDrawerItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Toolbar state
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [category, setCategory] = useState('');
  const [brand, setBrand] = useState('');
  const [brandOptions, setBrandOptions] = useState<string[]>([]);
  const [includeInactive, setIncludeInactive] = useState(false);

  // Review machinery
  const [reviewCount, setReviewCount] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [failMap, setFailMap] = useState<Record<string, string>>({});
  const [bulkRunning, setBulkRunning] = useState(false);

  // Drawer: an index into `items` (grid navigation), or a standalone item
  // (?focus= deep link to a product not on the current page).
  const [drawerIdx, setDrawerIdx] = useState<number | null>(null);
  const [focusItem, setFocusItem] = useState<CatalogDrawerItem | null>(null);

  // Page-level lightbox opened straight from a card image.
  const [lightbox, setLightbox] = useState<{ images: string[]; alt: string } | null>(null);

  // ---- Search debounce (300ms; Enter flushes for barcode scanners) --------
  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search]);

  // ---- Data loads -----------------------------------------------------------
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (segment === 'catalog') {
        const res = await productApi.getProducts({
          search: debouncedSearch || undefined,
          category: category || undefined,
          brand: brand || undefined,
          is_active: includeInactive ? 'all' : 'true',
          skip: (page - 1) * PAGE_SIZE,
          limit: PAGE_SIZE,
        });
        const docs = (res?.products || []) as Array<Record<string, unknown>>;
        setItems(docs.map((doc) => ({ kind: 'spine' as const, doc })));
        setTotal(Number(res?.total_count ?? res?.total ?? docs.length));
      } else {
        const res = await catalogProductsApi.list({
          needs_review: true,
          is_active: 'all',
          search: debouncedSearch || undefined,
          category: category || undefined,
          brand: brand || undefined,
          page,
          limit: PAGE_SIZE,
        });
        const docs = (res?.products || []) as unknown as Array<Record<string, unknown>>;
        setItems(docs.map((doc) => ({ kind: 'imported' as const, doc })));
        setTotal(Number(res?.total ?? docs.length));
        setReviewCount((prev) =>
          debouncedSearch || category || brand ? prev : Number(res?.total ?? prev)
        );
      }
    } catch (e: unknown) {
      setItems([]);
      setTotal(0);
      setError(e instanceof Error ? e.message : 'Could not load the catalog.');
    } finally {
      setLoading(false);
    }
  }, [segment, debouncedSearch, category, brand, includeInactive, page]);

  useEffect(() => {
    void load();
  }, [load]);

  // Live review-queue count (badge + amber banner), independent of filters.
  const refreshReviewCount = useCallback(async () => {
    try {
      const res = await catalogProductsApi.list({
        needs_review: true,
        is_active: 'all',
        page: 1,
        limit: 1,
      });
      setReviewCount(Number(res?.total ?? 0));
    } catch {
      /* fail-soft — badge just goes stale */
    }
  }, []);

  useEffect(() => {
    void refreshReviewCount();
  }, [refreshReviewCount]);

  // Brand select options (Brand Master; re-scoped when a category is picked).
  useEffect(() => {
    let alive = true;
    productApi
      .getBrandOptions(category || undefined)
      .then((r) => {
        if (alive) setBrandOptions((r.brands || []).map((b) => b.name).filter(Boolean));
      })
      .catch(() => {
        if (alive) setBrandOptions([]);
      });
    return () => {
      alive = false;
    };
  }, [category]);

  // Reset page + selection when the view meaningfully changes.
  useEffect(() => {
    setPage(1);
    setSelected(new Set());
    setDrawerIdx(null);
  }, [segment, debouncedSearch, category, brand, includeInactive]);

  // ---- ?focus=<id> read-once deep link (drawer reopen after QuickAdd edit) --
  const focusHandled = useRef(false);
  useEffect(() => {
    if (focusHandled.current) return;
    const focusId = searchParams.get('focus');
    if (!focusId) {
      focusHandled.current = true;
      return;
    }
    focusHandled.current = true;
    (async () => {
      try {
        const doc = (await productApi.getProduct(focusId)) as Record<string, unknown>;
        setFocusItem({ kind: 'spine', doc });
      } catch {
        try {
          const doc = await catalogProductsApi.get(focusId);
          setFocusItem({ kind: 'imported', doc: doc as unknown as Record<string, unknown> });
        } catch {
          toast.error('Could not find that product.');
        }
      } finally {
        const next = new URLSearchParams(searchParams);
        next.delete('focus');
        setSearchParams(next, { replace: true });
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  // ---- Selection ------------------------------------------------------------
  const toggleSelect = useCallback((pid: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid);
      else if (next.size < BULK_CAP) next.add(pid);
      return next;
    });
  }, []);

  // ---- Approve flows ----------------------------------------------------------
  const advanceAfterApprove = useCallback(
    async (approvedId: string) => {
      // Remove the approved card, keep the drawer on the SAME index (the list
      // shifts up), so the owner clears a rack without touching the grid.
      setItems((prev) => {
        const nextItems = prev.filter((it) => docId(it.doc) !== approvedId);
        setDrawerIdx((idx) => {
          if (idx === null) return idx;
          if (nextItems.length === 0) return null;
          return Math.min(idx, nextItems.length - 1);
        });
        return nextItems;
      });
      setSelected((prev) => {
        if (!prev.has(approvedId)) return prev;
        const next = new Set(prev);
        next.delete(approvedId);
        return next;
      });
      setTotal((t) => Math.max(0, t - 1));
      setReviewCount((c) => Math.max(0, c - 1));
      void refreshReviewCount();
    },
    [refreshReviewCount]
  );

  const handleApproved = useCallback(
    (productId: string, sku?: string | null) => {
      toast.success(`Approved for POS${sku ? ` — SKU ${sku}` : ''}.`);
      setFailMap((prev) => {
        if (!prev[productId]) return prev;
        const next = { ...prev };
        delete next[productId];
        return next;
      });
      if (focusItem && docId(focusItem.doc) === productId) setFocusItem(null);
      void advanceAfterApprove(productId);
    },
    [advanceAfterApprove, focusItem, toast]
  );

  const handleBulkApprove = useCallback(async () => {
    if (bulkRunning || selected.size === 0) return;
    const ids = Array.from(selected).slice(0, BULK_CAP);
    setBulkRunning(true);
    const failures: Record<string, string> = {};
    let approved = 0;
    const queue = [...ids];
    await Promise.all(
      Array.from({ length: BULK_CONCURRENCY }, async () => {
        for (;;) {
          const pid = queue.shift();
          if (!pid) return;
          try {
            await catalogProductsApi.promote(pid);
            approved += 1;
          } catch (e: unknown) {
            failures[pid] =
              e instanceof Error && e.message ? e.message : 'Validation failed';
          }
        }
      })
    );
    setBulkRunning(false);
    setFailMap((prev) => ({ ...prev, ...failures }));
    const failedIds = Object.keys(failures);
    // Failures stay selected (amber "needs fixes" badges); successes leave.
    setSelected(new Set(failedIds));
    toast[failedIds.length > 0 ? 'warning' : 'success'](
      `${approved} approved · ${failedIds.length} need fixes`
    );
    await load();
    void refreshReviewCount();
  }, [bulkRunning, selected, load, refreshReviewCount, toast]);

  // ---- Drawer plumbing -------------------------------------------------------
  const drawerItem: CatalogDrawerItem | null =
    focusItem ?? (drawerIdx !== null ? items[drawerIdx] ?? null : null);

  const closeDrawer = useCallback(() => {
    setDrawerIdx(null);
    setFocusItem(null);
  }, []);

  const drawerPrev =
    !focusItem && drawerIdx !== null && drawerIdx > 0
      ? () => setDrawerIdx((i) => (i === null ? i : Math.max(0, i - 1)))
      : undefined;
  const drawerNext =
    !focusItem && drawerIdx !== null && drawerIdx < items.length - 1
      ? () => setDrawerIdx((i) => (i === null ? i : Math.min(items.length - 1, i + 1)))
      : undefined;

  const handleDrawerUpdated = useCallback((fresh: CatalogProductDoc) => {
    const freshDoc = fresh as unknown as Record<string, unknown>;
    setItems((prev) =>
      prev.map((it) => (docId(it.doc) === docId(freshDoc) ? { ...it, doc: freshDoc } : it))
    );
    setFocusItem((prev) =>
      prev && docId(prev.doc) === docId(freshDoc) ? { ...prev, doc: freshDoc } : prev
    );
  }, []);

  // ---------------------------------------------------------------------------
  return (
    <div className="p-6 space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Catalog</h1>
          <p className="text-sm text-gray-500">
            {loading ? 'Loading…' : `${total.toLocaleString('en-IN')} product${total === 1 ? '' : 's'}`}
            {segment === 'review' ? ' waiting for review' : ' in the catalog'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              void load();
              void refreshReviewCount();
            }}
            className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
          >
            <RefreshCw className="h-4 w-4" /> Refresh
          </button>
          <button
            onClick={() => navigate('/catalog/add')}
            className="btn-primary inline-flex items-center gap-1.5"
          >
            <Plus className="h-4 w-4" /> Add product
          </button>
        </div>
      </div>

      {/* Amber review banner — THE review entry point */}
      {reviewCount > 0 && segment === 'catalog' && (
        <button
          type="button"
          onClick={() => setSegment('review')}
          className="w-full flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-left hover:bg-amber-100 transition-colors"
        >
          <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
          <span className="text-sm text-amber-800">
            <span className="font-semibold">{reviewCount.toLocaleString('en-IN')}</span> imported
            product{reviewCount === 1 ? ' is' : 's are'} waiting for review
          </span>
          <span className="ml-auto text-sm font-medium text-amber-700 underline">Review now</span>
        </button>
      )}

      {/* Segmented control */}
      <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
        <button
          type="button"
          onClick={() => setSegment('catalog')}
          className={clsx(
            'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
            segment === 'catalog' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
          )}
        >
          Catalog
        </button>
        <button
          type="button"
          onClick={() => setSegment('review')}
          className={clsx(
            'rounded-md px-3 py-1.5 text-sm font-medium transition-colors flex items-center gap-1.5',
            segment === 'review' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
          )}
        >
          Needs review — imported
          {reviewCount > 0 && (
            <span className="inline-flex items-center justify-center rounded-full bg-amber-500 px-1.5 py-px text-[10px] font-semibold text-white min-w-[1.25rem]">
              {reviewCount > 999 ? '999+' : reviewCount}
            </span>
          )}
        </button>
      </div>

      {/* Toolbar */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[220px] max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                // Barcode scanners type digits + Enter — flush the debounce.
                if (e.key === 'Enter') setDebouncedSearch(search.trim());
              }}
              placeholder="Search name, SKU or scan a barcode…"
              className="input-field w-full pl-9"
            />
          </div>
          <select
            value={brand}
            onChange={(e) => setBrand(e.target.value)}
            className="input-field w-44"
            title="Filter by brand"
          >
            <option value="">All brands</option>
            {brandOptions.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
          {segment === 'catalog' && (
            <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={includeInactive}
                onChange={(e) => setIncludeInactive(e.target.checked)}
                className="h-3.5 w-3.5 accent-gray-600"
              />
              Include inactive
            </label>
          )}
        </div>

        {/* Category chips (the ONE shared browse vocabulary) */}
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => setCategory('')}
            className={clsx(
              'rounded-full px-3 py-1 text-xs font-medium border transition-colors',
              category === ''
                ? 'bg-gray-900 text-white border-gray-900'
                : 'bg-white text-gray-600 border-gray-200 hover:border-gray-400'
            )}
          >
            All
          </button>
          {CATEGORY_BROWSE_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => setCategory((c) => (c === o.value ? '' : o.value))}
              className={clsx(
                'rounded-full px-3 py-1 text-xs font-medium border transition-colors',
                category === o.value
                  ? 'bg-gray-900 text-white border-gray-900'
                  : 'bg-white text-gray-600 border-gray-200 hover:border-gray-400'
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {/* Grid */}
      {error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-center text-sm text-red-700">
          {error}
        </div>
      ) : loading && items.length === 0 ? (
        <div className="flex items-center justify-center py-24 text-gray-400">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-10 text-center">
          {segment === 'review' ? (
            <>
              <CheckCircle2 className="mx-auto h-8 w-8 text-green-500" />
              <p className="mt-2 text-sm font-medium text-gray-900">Review queue is clear</p>
              <p className="text-xs text-gray-500">
                Every imported product matching these filters has been handled.
              </p>
            </>
          ) : (
            <>
              <p className="text-sm font-medium text-gray-900">No products found</p>
              <p className="text-xs text-gray-500">Try clearing the search or filters.</p>
            </>
          )}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
            {items.map((it, idx) => {
              const pid = docId(it.doc);
              return (
                <CatalogCard
                  key={pid || idx}
                  item={it}
                  selectable={segment === 'review'}
                  selected={selected.has(pid)}
                  failMessage={failMap[pid]}
                  onToggleSelect={() => toggleSelect(pid)}
                  onOpen={() => {
                    setFocusItem(null);
                    setDrawerIdx(idx);
                  }}
                  onImageClick={() =>
                    setLightbox({ images: docImages(it.doc), alt: docName(it.doc) })
                  }
                />
              );
            })}
          </div>
          <Pagination
            currentPage={page}
            totalItems={total}
            pageSize={PAGE_SIZE}
            onPageChange={setPage}
          />
        </>
      )}

      {/* Sticky bulk-approve bar (review segment) */}
      {segment === 'review' && selected.size > 0 && (
        <div className="sticky bottom-4 z-10 flex items-center justify-between gap-4 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-lg">
          <span className="text-sm text-gray-700">
            <span className="font-semibold text-gray-900">{selected.size}</span> selected
            {selected.size >= BULK_CAP && (
              <span className="ml-1 text-xs text-gray-400">(max {BULK_CAP} per batch)</span>
            )}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSelected(new Set())}
              className="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100"
              disabled={bulkRunning}
            >
              Clear
            </button>
            <button
              onClick={() => void handleBulkApprove()}
              disabled={bulkRunning}
              className="inline-flex items-center gap-1.5 rounded-lg bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-60"
            >
              {bulkRunning ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="h-4 w-4" />
              )}
              Approve for POS
            </button>
          </div>
        </div>
      )}

      {/* Drawer */}
      {drawerItem && (
        <CatalogProductDrawer
          item={drawerItem}
          onClose={closeDrawer}
          onPrev={drawerPrev}
          onNext={drawerNext}
          onUpdated={handleDrawerUpdated}
          onApproved={handleApproved}
        />
      )}

      {/* Page-level lightbox (card image click). ImageLightbox itself renders
          null for an empty list, so no extra guard is needed. */}
      {lightbox && (
        <ImageLightbox
          images={lightbox.images}
          alt={lightbox.alt}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  );
}

export default CatalogManagerPage;
