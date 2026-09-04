// ============================================================================
// IMS 2.0 - Catalog Manager (/catalog, /catalog/review, /catalog/missing-photos)
// ============================================================================
// The owner's single "what exists and its truth" screen: ONE list page over
// TWO honest data sources, the ROUTE deciding which (design 2026-08-30, built
// 2026-09-04) —
//
//   /catalog                 → GET /products (the billing/stock SPINE,
//                              server-paginated via skip/limit+total_count)
//   /catalog/missing-photos  → the same list, pre-filtered ?photo=missing:
//                              the "cannot go online" work list
//   /catalog/review          → GET /catalog/products?needs_review=true&
//                              is_active=all (imported docs awaiting review;
//                              server-paginated via page/limit+total)
//
// Every row carries two SERVER-computed truths the frontend never re-derives:
//   has_photo — the Shopify push's own predicate (an absolute http(s) URL on
//               the doc the push reads), so "Photo: Missing" here IS "the
//               push would refuse it"
//   online    — LIVE / QUEUED / OFF / BLOCKED (blocked = no usable photo)
// and the counts row at the top is the server's tally with the same rule
// (GET /catalog/online-summary → catalog), so the figures and the rows
// cannot disagree. "Waiting to push" reads ecom.locally_modified — the flag
// the push sweep walks; cataloguing sets it, a human still presses publish.
//
// A row click opens the slide-over drawer (view / edit-in-place links /
// review + approve). Approving PROMOTES the imported doc in place (same id)
// — the only thing that clears needs_review. Bulk approve is a client-side
// loop over the single promote endpoint (concurrency 4, cap 200): every item
// passes the identical door validation, so bulk can never force-approve.
//
// POS is untouched.

import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, NavLink, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Search,
  RefreshCw,
  Plus,
  Loader2,
  AlertTriangle,
  ShieldCheck,
  CheckCircle2,
  ImagePlus,
  Pencil,
} from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { Pagination } from '../../components/common/Pagination';
import { ImageLightbox } from '../../components/common/ImageLightbox';
import { refreshNavCounts } from '../../components/shell/NavBadge';
// Import DIRECT from the modules (not the services/api barrel — TS2614).
import { productApi, catalogApi, type CatalogCounts } from '../../services/api/products';
import {
  catalogProductsApi,
  type CatalogProductDoc,
  type OnlineState,
  type PhotoFilter,
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
import { writeReviewQueue } from './reviewQueue';

const PAGE_SIZE = 48;
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

const fmtN = (n: number | undefined | null): string =>
  typeof n === 'number' && Number.isFinite(n) ? n.toLocaleString('en-IN') : '—';

// ---------------------------------------------------------------------------
// Chips — the server's booleans rendered with the app's own .chip tokens.
// ---------------------------------------------------------------------------
function PhotoChip({ has }: { has: boolean | undefined }) {
  if (has === undefined) return <span className="chip">—</span>;
  return has ? (
    <span className="chip ok">Yes</span>
  ) : (
    <span className="chip err" title="No usable photo — the push would refuse it">
      Missing
    </span>
  );
}

const ONLINE_LABEL: Record<OnlineState, { text: string; cls: string; title: string }> = {
  LIVE: { text: 'Live', cls: 'chip ok', title: 'On bettervision.in' },
  QUEUED: { text: 'Queued', cls: 'chip info', title: 'Waiting for a human to press push' },
  OFF: { text: 'Off', cls: 'chip', title: 'Not online, not queued' },
  BLOCKED: { text: 'Blocked', cls: 'chip', title: 'No usable photo — cannot go online' },
};

function OnlineChip({ state }: { state: OnlineState | undefined }) {
  const meta = state ? ONLINE_LABEL[state] : undefined;
  if (!meta) return <span className="chip">—</span>;
  return (
    <span className={clsx(meta.cls, state === 'BLOCKED' && 'text-gray-400')} title={meta.title}>
      {meta.text}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Counts row — four server figures (GET /catalog/online-summary → catalog).
// ---------------------------------------------------------------------------
function CountsRow({ counts }: { counts: CatalogCounts | null }) {
  const card = 'rounded-xl border border-gray-200 bg-white px-4 py-3';
  const eyebrow = 'text-[10.5px] font-medium uppercase tracking-[.12em] text-gray-500';
  const figure = 'mt-0.5 text-2xl font-semibold tabular-nums text-gray-900';
  const sub = 'text-[11.5px] text-gray-500';
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="catalog-counts">
      <div className={card}>
        <div className={eyebrow}>In catalog</div>
        <div className={figure}>{fmtN(counts?.in_catalog)}</div>
        <div className={sub}>
          {fmtN(counts?.smartglasses)} smart glasses · {fmtN(counts?.own)} own
        </div>
      </div>
      <Link to="/catalog/missing-photos" className={clsx(card, 'border-red-200 bg-red-50/40 hover:bg-red-50')}>
        <div className={eyebrow}>No usable photo</div>
        <div className={clsx(figure, 'text-red-700')}>{fmtN(counts?.no_photo)}</div>
        <div className={sub}>cannot go online</div>
      </Link>
      <div className={card}>
        <div className={eyebrow}>Live online</div>
        <div className={figure}>{fmtN(counts?.live)}</div>
        <div className={sub}>on bettervision.in</div>
      </div>
      <div className={card}>
        <div className={eyebrow}>Waiting to push</div>
        <div className={figure}>{fmtN(counts?.pending)}</div>
        <div className={sub}>queued, needs a human</div>
      </div>
    </div>
  );
}

// ===========================================================================
// Page
// ===========================================================================
export function CatalogManagerPage({
  segment = 'catalog',
  photoPreset,
}: {
  /** Which list this address shows. The ROUTE decides; never a query string. */
  segment?: Segment;
  /** /catalog/missing-photos: the catalog pre-filtered to rows with no usable
   *  photo. Fixed for that address (the page IS the filter). */
  photoPreset?: PhotoFilter;
}) {
  const navigate = useNavigate();
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();

  // Page lives in the URL (not useState) so the full-page review editor's
  // "Back to queue" (/catalog/review?page=N&focus=<id>) restores the exact
  // list position. Unknown/absent params fall back to page 1.
  const pageRaw = parseInt(searchParams.get('page') || '1', 10);
  const page = Number.isFinite(pageRaw) && pageRaw > 0 ? pageRaw : 1;

  const setPage = useCallback(
    (next: number) => {
      setSearchParams(
        (prev) => {
          const sp = new URLSearchParams(prev);
          if (next > 1) sp.set('page', String(next));
          else sp.delete('page');
          return sp;
        },
        { replace: true }
      );
    },
    [setSearchParams]
  );

  const [items, setItems] = useState<CatalogDrawerItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Toolbar state
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [category, setCategory] = useState('');
  const [brand, setBrand] = useState('');
  const [brandOptions, setBrandOptions] = useState<string[]>([]);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [photoPick, setPhotoPick] = useState<PhotoFilter | ''>('');
  const photo: PhotoFilter | undefined = photoPreset ?? (photoPick || undefined);

  // Counts row + review badge — the server's tally, one fetch.
  const [counts, setCounts] = useState<CatalogCounts | null>(null);
  const reviewCount = counts?.needs_review ?? 0;

  // Review machinery
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [failMap, setFailMap] = useState<Record<string, string>>({});
  const [bulkRunning, setBulkRunning] = useState(false);

  // Drawer: an index into `items` (list navigation), or a standalone item
  // (?focus= deep link to a product not on the current page).
  const [drawerIdx, setDrawerIdx] = useState<number | null>(null);
  const [focusItem, setFocusItem] = useState<CatalogDrawerItem | null>(null);

  // Page-level lightbox opened straight from a row thumbnail.
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
          photo,
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
          photo,
          page,
          limit: PAGE_SIZE,
        });
        const docs = (res?.products || []) as unknown as Array<Record<string, unknown>>;
        setItems(docs.map((doc) => ({ kind: 'imported' as const, doc })));
        setTotal(Number(res?.total ?? docs.length));
      }
    } catch (e: unknown) {
      setItems([]);
      setTotal(0);
      setError(e instanceof Error ? e.message : 'Could not load the catalog.');
    } finally {
      setLoading(false);
    }
  }, [segment, debouncedSearch, category, brand, includeInactive, photo, page]);

  useEffect(() => {
    void load();
  }, [load]);

  // The counts row + the review badge, independent of filters. Also pushes
  // the fresh figures to the sidebar badges (same server tally).
  const refreshCounts = useCallback(async () => {
    try {
      const res = await catalogApi.getOnlineSummary();
      setCounts(res?.catalog ?? null);
      void refreshNavCounts();
    } catch {
      /* fail-soft — the row just goes stale */
    }
  }, []);

  useEffect(() => {
    void refreshCounts();
  }, [refreshCounts]);

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

  // Reset page + selection when the view meaningfully changes. Skipped on the
  // FIRST run so a deep-linked ?page=N isn't clobbered back to page 1 on
  // mount (page lives in the URL).
  const viewResetReady = useRef(false);
  useEffect(() => {
    if (!viewResetReady.current) {
      viewResetReady.current = true;
      return;
    }
    setPage(1);
    setSelected(new Set());
    setDrawerIdx(null);
    // setPage is EXCLUDED on purpose: react-router's setSearchParams (and so
    // setPage) gets a new identity on every URL-search change, so depending on
    // it made ANY page change snap straight back to page 1 (and broke
    // Back-to-queue's ?page=N restore). setPage uses the functional-updater
    // form, so a stale closure is harmless.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segment, debouncedSearch, category, brand, includeInactive, photo]);

  // Keep the review-queue stash current: whenever the review segment shows a
  // page of imported docs, that page (+ filters + list position) IS the
  // reviewer's working queue — the full-page editor reads it for "Item N of M"
  // and Prev/Next. The drawer's "Edit everything" pins the index just before
  // navigating.
  useEffect(() => {
    if (segment !== 'review' || loading) return;
    const ids = items.filter((it) => it.kind === 'imported').map((it) => docId(it.doc));
    if (ids.length === 0) return;
    writeReviewQueue({
      ids,
      index: 0,
      offset: (page - 1) * PAGE_SIZE,
      total,
      filters: {
        search: debouncedSearch || undefined,
        category: category || undefined,
        brand: brand || undefined,
        page,
      },
    });
  }, [segment, loading, items, page, total, debouncedSearch, category, brand]);

  // "Edit everything" (drawer / row action) → full cataloguing page with the
  // queue stashed at this item's position. `hash` = '#images' lands on the
  // uploader ("Add photo").
  const handleEditEverything = useCallback(
    (id: string, hash = '') => {
      const ids = items.filter((it) => it.kind === 'imported').map((it) => docId(it.doc));
      const at = ids.indexOf(id);
      writeReviewQueue({
        ids: at >= 0 ? ids : [id],
        index: Math.max(0, at),
        offset: at >= 0 ? (page - 1) * PAGE_SIZE : 0,
        total: at >= 0 ? total : undefined,
        filters: {
          search: debouncedSearch || undefined,
          category: category || undefined,
          brand: brand || undefined,
          page,
        },
      });
      navigate(`/catalog/add?review=${encodeURIComponent(id)}${hash}`);
    },
    [items, page, total, debouncedSearch, category, brand, navigate]
  );

  // Row action. "Add photo" opens the ONE existing image upload (QuickAdd's
  // Product Images section) for this product; "Edit" opens the same editor.
  const openEditor = useCallback(
    (it: CatalogDrawerItem, hash = '') => {
      const id = docId(it.doc);
      if (it.kind === 'imported') handleEditEverything(id, hash);
      else navigate(`/catalog/add?edit=${encodeURIComponent(id)}${hash}`);
    },
    [handleEditEverything, navigate]
  );

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
      // Remove the approved row, keep the drawer on the SAME index (the list
      // shifts up), so the owner clears a rack without touching the list.
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
      setCounts((c) => (c ? { ...c, needs_review: Math.max(0, c.needs_review - 1) } : c));
      void refreshCounts();
    },
    [refreshCounts]
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
    void refreshCounts();
  }, [bulkRunning, selected, load, refreshCounts, toast]);

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
  const isMissingPhotos = photoPreset === 'missing';
  const title =
    segment === 'review' ? 'Needs review' : isMissingPhotos ? 'Missing photos' : 'Catalog';
  const subtitle = loading
    ? 'Loading…'
    : `${total.toLocaleString('en-IN')} product${total === 1 ? '' : 's'}${
        segment === 'review'
          ? ' waiting for review'
          : isMissingPhotos
            ? ' without a usable photo — cannot go online'
            : ' in the catalog'
      }`;

  const segmentLink = (active: boolean) =>
    clsx(
      'rounded-md px-3 py-1.5 text-sm font-medium transition-colors flex items-center gap-1.5',
      active ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'
    );

  return (
    <div className="p-4 sm:p-6 space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900">{title}</h1>
          <p className="text-sm text-gray-500">{subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              void load();
              void refreshCounts();
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

      {/* Counts row — the catalog's truth, from the server */}
      {segment === 'catalog' && <CountsRow counts={counts} />}

      {/* Amber review banner — links to the queue's own address */}
      {reviewCount > 0 && segment === 'catalog' && (
        <Link
          to="/catalog/review"
          className="w-full flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-left hover:bg-amber-100 transition-colors"
        >
          <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
          <span className="text-sm text-amber-800">
            <span className="font-semibold">{reviewCount.toLocaleString('en-IN')}</span> imported
            product{reviewCount === 1 ? ' is' : 's are'} waiting for review
          </span>
          <span className="ml-auto text-sm font-medium text-amber-700 underline">Review now</span>
        </Link>
      )}

      {/* Segment links — real addresses, not a query string */}
      <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
        <NavLink to="/catalog" end className={({ isActive }) => segmentLink(isActive)}>
          Catalog
        </NavLink>
        <NavLink to="/catalog/review" className={({ isActive }) => segmentLink(isActive)}>
          Needs review — imported
          {reviewCount > 0 && (
            <span className="inline-flex items-center justify-center rounded-full bg-amber-500 px-1.5 py-px text-[10px] font-semibold text-white min-w-[1.25rem]">
              {reviewCount > 999 ? '999+' : reviewCount}
            </span>
          )}
        </NavLink>
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
          {/* Photo filter — the server's predicate; fixed on /catalog/missing-photos */}
          {!photoPreset && (
            <select
              value={photoPick}
              onChange={(e) => setPhotoPick(e.target.value as PhotoFilter | '')}
              className="input-field w-40"
              title="Filter by photo"
              aria-label="Photo"
            >
              <option value="">Photo: Any</option>
              <option value="has">Photo: Has photo</option>
              <option value="missing">Photo: Missing</option>
            </select>
          )}
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
            className={clsx('ims-chip', category === '' && 'ims-chip--on')}
          >
            All
          </button>
          {CATEGORY_BROWSE_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              onClick={() => setCategory((c) => (c === o.value ? '' : o.value))}
              className={clsx('ims-chip', category === o.value && 'ims-chip--on')}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
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
          ) : isMissingPhotos ? (
            <>
              <CheckCircle2 className="mx-auto h-8 w-8 text-green-500" />
              <p className="mt-2 text-sm font-medium text-gray-900">Every product has a photo</p>
              <p className="text-xs text-gray-500">Nothing here is blocked from going online.</p>
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
          {/* The table scrolls inside its own box at narrow widths; the page
              never scrolls sideways. */}
          <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white">
            <table className="w-full min-w-[780px] text-sm" data-testid="catalog-table">
              <thead>
                <tr className="text-left text-[10px] font-semibold uppercase tracking-[.08em] text-gray-500">
                  {segment === 'review' && (
                    <th className="w-8 px-3 pb-2 pt-3">
                      <span className="sr-only">Select</span>
                    </th>
                  )}
                  <th className="w-12 px-3 pb-2 pt-3">
                    <span className="sr-only">Image</span>
                  </th>
                  <th className="px-3 pb-2 pt-3">Product</th>
                  <th className="px-3 pb-2 pt-3">SKU</th>
                  <th className="px-3 pb-2 pt-3">Category</th>
                  <th className="px-3 pb-2 pt-3 text-right">MRP</th>
                  <th className="px-3 pb-2 pt-3">Photo</th>
                  <th className="px-3 pb-2 pt-3">Online</th>
                  <th className="px-3 pb-2 pt-3">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((it, idx) => {
                  const doc = it.doc;
                  const pid = docId(doc);
                  const name = docName(doc);
                  const brandName = String(
                    doc.brand || (doc.attributes as Record<string, unknown>)?.brand_name || ''
                  );
                  const images = docImages(doc);
                  const mrp = docMrp(doc);
                  const offer = docOffer(doc);
                  const hasDiscount = mrp !== null && offer !== null && offer < mrp;
                  const inactive = doc.is_active === false;
                  const needsReview = it.kind === 'imported' && Boolean(doc.needs_review);
                  const hasPhoto = doc.has_photo as boolean | undefined;
                  const online = doc.online as OnlineState | undefined;
                  const openRow = () => {
                    setFocusItem(null);
                    setDrawerIdx(idx);
                  };
                  return (
                    <tr
                      key={pid || idx}
                      className={clsx(
                        'border-t border-gray-100 hover:bg-gray-50',
                        selected.has(pid) && 'bg-amber-50',
                        inactive && 'opacity-60'
                      )}
                    >
                      {segment === 'review' && (
                        <td className="px-3 py-2 align-middle">
                          <input
                            type="checkbox"
                            checked={selected.has(pid)}
                            onChange={() => toggleSelect(pid)}
                            className="h-3.5 w-3.5 accent-amber-500"
                            aria-label={`Select ${name}`}
                          />
                        </td>
                      )}
                      <td className="px-3 py-2 align-middle">
                        <button
                          type="button"
                          onClick={images.length > 0 ? () => setLightbox({ images, alt: name }) : openRow}
                          className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-md bg-gray-50"
                          aria-label={images.length > 0 ? `View images of ${name}` : name}
                        >
                          <CatalogImage
                            url={images[0] || ''}
                            alt={name}
                            className="max-h-full max-w-full object-contain"
                          />
                        </button>
                      </td>
                      <td className="px-3 py-2 align-middle">
                        <button type="button" onClick={openRow} className="text-left">
                          <div className="font-medium text-gray-900 leading-snug">{name}</div>
                          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-500">
                            {brandName && <span>{brandName}</span>}
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
                            {failMap[pid] && (
                              <span
                                className="inline-flex items-center rounded-full bg-orange-100 px-2 py-0.5 text-[10px] font-medium text-orange-700"
                                title={failMap[pid]}
                              >
                                needs fixes
                              </span>
                            )}
                          </div>
                        </button>
                      </td>
                      <td className="px-3 py-2 align-middle font-mono text-xs text-gray-700">
                        {String(doc.sku || doc.parent_sku || '—')}
                      </td>
                      <td className="px-3 py-2 align-middle text-gray-700">
                        {String(doc.category_name || doc.category || '—')}
                      </td>
                      <td className="px-3 py-2 align-middle text-right tabular-nums">
                        <span className="font-semibold text-gray-900">{fmtINR(offer ?? mrp)}</span>
                        {hasDiscount && (
                          <span className="ml-1 text-[10px] text-gray-500 line-through">{fmtINR(mrp)}</span>
                        )}
                      </td>
                      <td className="px-3 py-2 align-middle" data-testid="photo-cell">
                        <PhotoChip has={hasPhoto} />
                      </td>
                      <td className="px-3 py-2 align-middle" data-testid="online-cell">
                        <OnlineChip state={online} />
                      </td>
                      <td className="px-3 py-2 align-middle text-right">
                        {hasPhoto === false ? (
                          <button
                            type="button"
                            onClick={() => openEditor(it, '#images')}
                            className="inline-flex h-8 items-center gap-1 rounded-lg border border-gray-300 px-2.5 text-xs font-medium text-gray-800 hover:bg-gray-50"
                          >
                            <ImagePlus className="h-3.5 w-3.5" /> Add photo
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => openEditor(it)}
                            className="inline-flex h-8 items-center gap-1 rounded-lg border border-gray-300 px-2.5 text-xs font-medium text-gray-800 hover:bg-gray-50"
                          >
                            <Pencil className="h-3.5 w-3.5" /> Edit
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
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
          onEditEverything={handleEditEverything}
        />
      )}

      {/* Page-level lightbox (thumbnail click). ImageLightbox itself renders
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
