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
//   online    — LIVE / QUEUED / OFF / BLOCKED (blocked = no usable photo) /
//               NOT_FOR_WEBSITE (the push gate refuses the brand)
// and the counts row at the top is the server's tally with the same rule
// (GET /catalog/online-summary → catalog), so the figures and the rows
// cannot disagree. "Waiting to push" reads ecom.locally_modified — the flag
// the push sweep walks; cataloguing sets it, a human still presses publish.
//
// SORT, FILTERS, ROWS PER PAGE (owner 2026-10-12, mockup artboard Main):
// every choice lives in the page ADDRESS (catalogListState), the server sorts
// and filters the whole result (GET /products via services/catalog_list) and
// the filter options + their counts come from GET /products/facets. Rows per
// page is also remembered on this device.
//
// SELECT, FLAG, ARCHIVE (owner 2026-10-12, artboards Main + FlagDialog): tick
// rows, then Flag (ADMIN / SUPERADMIN / STORE_MANAGER -> one task per person
// who added them) or Delete (archive; admins, the existing soft delete);
// Flags: Archived lists them for Restore. The review segment's bulk Approve
// uses the SAME selection.
//
// A row click opens the slide-over drawer (view / edit-in-place links /
// review + approve). Approving PROMOTES the imported doc in place (same id)
// — the only thing that clears needs_review. Bulk approve is a client-side
// loop over the single promote endpoint (concurrency 4, cap 200): every item
// passes the identical door validation, so bulk can never force-approve.
//
// POS is untouched.

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, NavLink, useNavigate, useSearchParams } from 'react-router-dom';
import {
  RefreshCw,
  Plus,
  Loader2,
  AlertTriangle,
  ShieldCheck,
  CheckCircle2,
  ImagePlus,
  Pencil,
  Flag,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import { Pagination } from '../../components/common/Pagination';
import { ImageLightbox } from '../../components/common/ImageLightbox';
import { refreshNavCounts } from '../../components/shell/NavBadge';
// Import DIRECT from the modules (not the services/api barrel — TS2614).
import {
  productApi,
  catalogApi,
  type CatalogCounts,
  type CatalogFacets,
  type FamilyMember,
} from '../../services/api/products';
import {
  catalogBulkApi,
  catalogProductsApi,
  type CatalogProductDoc,
  type OnlineState,
  type PhotoFilter,
  type ProductFlag,
} from '../../services/api/catalog';
import {
  CatalogProductDrawer,
  CatalogImage,
  docId,
  docName,
  docImages,
  docMrp,
  docOffer,
  isOrderedDraft,
  type CatalogDrawerItem,
} from './CatalogProductDrawer';
import { EDIT_QUEUE_KEY, clearReviewQueue, writeReviewQueue } from './reviewQueue';
import { CatalogFiltersBar } from './CatalogFiltersBar';
import { ArchiveDialog, FlagDialog, SelectionBar } from './CatalogBulkActions';
import {
  PAGE_SIZES,
  PAGE_SIZE_STORAGE_KEY,
  addedColumn,
  flagLabel,
  readFilters,
  resultLine,
  toProductParams,
  writeFilters,
  type CatalogFilters,
  type FlagType,
} from './catalogListState';

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

/** This device's remembered rows per page (fail-soft: private mode etc.). */
function storedRows(): number | null {
  try {
    return Number(window.localStorage.getItem(PAGE_SIZE_STORAGE_KEY)) || null;
  } catch {
    return null;
  }
}

function rememberRows(n: number): void {
  try {
    window.localStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(n));
  } catch {
    /* not remembered on this device; the address still carries it */
  }
}

/** A row's open flags, newest first. */
function openFlags(doc: Record<string, unknown>): ProductFlag[] {
  const flags = Array.isArray(doc.flags) ? (doc.flags as ProductFlag[]) : [];
  return flags
    .filter((f) => f && f.status === 'open')
    .sort((a, b) => String(b.at || '').localeCompare(String(a.at || '')));
}

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
  NOT_FOR_WEBSITE: {
    text: 'Not for website',
    cls: 'chip',
    // The row's note carries the push gate's own reason (a brand switched off,
    // misspelt or inactive in Brand Master, a push lock); this is the fallback.
    title: 'The push refuses it',
  },
  DELIST_FAILED: {
    text: 'Still live',
    cls: 'chip err',
    title:
      'Still live on Shopify - take-down failed. Retired in IMS but the automatic take-down did not land; press Take down on the Online Store screen.',
  },
};

function OnlineChip({ state, note }: { state: OnlineState | undefined; note?: string | null }) {
  const meta = state ? ONLINE_LABEL[state] : undefined;
  if (!meta) return <span className="chip">—</span>;
  return (
    <>
      <span className={clsx(meta.cls, state === 'BLOCKED' && 'text-gray-400')} title={note || meta.title}>
        {meta.text}
      </span>
      {note && <div className="mt-0.5 max-w-[16rem] text-[11px] leading-snug text-amber-700">{note}</div>}
    </>
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
  const { user, hasRole } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  // Who may do what (the server enforces the same; these only hide buttons).
  const canEdit = hasRole(['SUPERADMIN', 'ADMIN', 'CATALOG_MANAGER']);
  const canFlag = hasRole(['SUPERADMIN', 'ADMIN', 'STORE_MANAGER']);
  const isAdmin = hasRole(['SUPERADMIN', 'ADMIN']);
  const isCataloguer = (user?.roles || []).includes('CATALOG_MANAGER');

  // Page lives in the URL (not useState) so the full-page review editor's
  // "Back to queue" (/catalog/review?page=N&focus=<id>) restores the exact
  // list position. Unknown/absent params fall back to page 1.
  const pageRaw = parseInt(searchParams.get('page') || '1', 10);
  const page = Number.isFinite(pageRaw) && pageRaw > 0 ? pageRaw : 1;

  // Sort + filters + rows per page: the address is the state.
  // This device's remembered size: state, read only when the address has
  // no rows (an old link, a plain /catalog) -- and then written into it.
  const [rowsMemory, setRowsMemory] = useState(storedRows);
  const filtersKey = JSON.stringify(readFilters(searchParams, rowsMemory, segment, isAdmin));
  const filters: CatalogFilters = useMemo(() => JSON.parse(filtersKey), [filtersKey]);
  const pageSize = filters.rows;
  const photo: PhotoFilter | undefined = photoPreset ?? (filters.photo || undefined);
  // G1: the catalogue lists one row per MODEL -- except a photo work list,
  // where every colour/size needs its own photo, so each is its own row.
  const grouped = segment === 'catalog' && !photo;
  const archivedView = segment === 'catalog' && filters.flag === 'archived';

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

  // The address ALWAYS carries rows per page (50 too): a page number then
  // means the same rows on every device, in every tab and shared link.
  useEffect(() => {
    if (searchParams.get('rows') === String(pageSize)) return;
    setSearchParams(
      (prev) => {
        const sp = new URLSearchParams(prev);
        sp.set('rows', String(pageSize));
        return sp;
      },
      { replace: true }
    );
  }, [searchParams, pageSize, setSearchParams]);

  const [items, setItems] = useState<CatalogDrawerItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  // The latest list request: an answer to an older one is dropped (a slow
  // page-5 answer must not move the page a newer choice already set).
  const loadSeq = useRef(0);
  const [error, setError] = useState<string | null>(null);

  // Search box: typed text is local, the debounced text goes to the address.
  const [search, setSearch] = useState(filters.q);
  const [reviewBrands, setReviewBrands] = useState<string[]>([]);

  // Counts row + review badge — the server's tally, one fetch.
  const [counts, setCounts] = useState<CatalogCounts | null>(null);
  const reviewCount = counts?.needs_review ?? 0;
  // Filter options with counts + the open-flag counts (red banner).
  const [facets, setFacets] = useState<CatalogFacets | null>(null);

  // ONE selection (flag / archive / restore on the catalog, Approve on the
  // review queue), kept across pages.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [failMap, setFailMap] = useState<Record<string, string>>({});
  const [bulkRunning, setBulkRunning] = useState(false);
  const [flagType, setFlagType] = useState<FlagType>('needs_revision');
  const [dialog, setDialog] = useState<'flag' | 'archive' | null>(null);

  // Drawer: an index into `items` (list navigation), or a standalone item
  // (?focus= deep link to a product not on the current page).
  const [drawerIdx, setDrawerIdx] = useState<number | null>(null);
  const [focusItem, setFocusItem] = useState<CatalogDrawerItem | null>(null);

  // Page-level lightbox opened straight from a row thumbnail.
  const [lightbox, setLightbox] = useState<{ images: string[]; alt: string } | null>(null);

  const clearSelection = useCallback(() => {
    setSelected(new Set());
  }, []);

  /** Change the filters: written to the address, back to page 1, selection
   *  and drawer cleared. An updater reads the CURRENT address, so a late
   *  search flush never undoes a filter picked meanwhile. */
  const setFilters = useCallback(
    (next: CatalogFilters | ((current: CatalogFilters) => CatalogFilters)) => {
      setSearchParams(
        (prev) => {
          const f =
            typeof next === 'function'
              ? next(readFilters(prev, rowsMemory, segment, isAdmin))
              : next;
          const sp = writeFilters(f, prev, segment);
          sp.delete('page');
          return sp;
        },
        { replace: true }
      );
      clearSelection();
      setDrawerIdx(null);
    },
    [setSearchParams, clearSelection, rowsMemory, segment, isAdmin]
  );

  // ---- Search debounce (300ms; Enter flushes for barcode scanners) --------
  const flushSearch = useCallback(
    (text: string) => {
      if (text.trim() !== filters.q) setFilters((f) => ({ ...f, q: text.trim() }));
    },
    [filters.q, setFilters]
  );
  useEffect(() => {
    const t = window.setTimeout(() => flushSearch(search), 300);
    return () => window.clearTimeout(t);
    // Only the typed text re-arms the timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);
  // Back / a shared link changed the address: the box follows it.
  useEffect(() => {
    setSearch((s) => (s.trim() === filters.q ? s : filters.q));
  }, [filters.q]);

  // ---- Data loads -----------------------------------------------------------
  // The Catalog list's query (no skip/limit). The edit page's Save & next asks
  // for the list's next page with these very params (reviewQueue `list`).
  const catalogParams = useMemo(
    () => ({
      ...toProductParams(filters),
      // G1 (owner 2026-10-18): one row per model, its colours/sizes folded in.
      group: grouped ? ('model' as const) : undefined,
      photo,
    }),
    [filters, grouped, photo]
  );
  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    const stale = () => seq !== loadSeq.current;
    setLoading(true);
    setError(null);
    // A page past the end (a shared link, or a bulk archive that emptied the
    // last page) goes to the last page that has rows -- never a dead end.
    let pastEnd = false;
    const show = (next: CatalogDrawerItem[], n: number) => {
      if (stale()) return;
      const last = Math.max(1, Math.ceil(n / pageSize));
      if (next.length === 0 && page > last) {
        pastEnd = true;
        setPage(last);
        return;
      }
      setItems(next);
      setTotal(n);
    };
    try {
      if (segment === 'catalog') {
        const res = await productApi.getProducts({
          ...catalogParams,
          skip: (page - 1) * pageSize,
          limit: pageSize,
        });
        const docs = (res?.products || []) as Array<Record<string, unknown>>;
        show(
          docs.map((doc) => ({ kind: 'spine' as const, doc })),
          Number(res?.total_count ?? res?.total ?? docs.length)
        );
      } else {
        const res = await catalogProductsApi.list({
          needs_review: true,
          is_active: 'all',
          search: filters.q || undefined,
          category: filters.category || undefined,
          brand: filters.brand || undefined,
          photo,
          sort: filters.sort,
          page,
          limit: pageSize,
        });
        const docs = (res?.products || []) as unknown as Array<Record<string, unknown>>;
        show(
          docs.map((doc) =>
            // A manager's typed-in draft (audit C1) already HAS its billing
            // row: it opens in the product editor, never the import approve.
            isOrderedDraft(doc)
              ? { kind: 'spine' as const, doc: { ...doc, product_id: doc.spine_product_id } }
              : { kind: 'imported' as const, doc }
          ),
          Number(res?.total ?? docs.length)
        );
      }
    } catch (e: unknown) {
      if (stale()) return;
      setItems([]);
      setTotal(0);
      setError(e instanceof Error ? e.message : 'Could not load the catalog.');
    } finally {
      // Past the end: still loading -- the last page's load takes over. A
      // newer request owns the spinner.
      if (!pastEnd && !stale()) setLoading(false);
    }
  }, [segment, filters, catalogParams, page, pageSize, setPage]);

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

  // Filter options + flag counts (the spine catalogue; Include inactive
  // widens what they count, exactly as it widens the list).
  const refreshFacets = useCallback(async () => {
    if (segment !== 'catalog') return;
    try {
      setFacets(await productApi.getFacets({ is_active: filters.inactive ? 'all' : 'true' }));
    } catch {
      setFacets(null); // fail-soft: the selects just offer "Any"
    }
  }, [segment, filters.inactive]);

  useEffect(() => {
    void refreshFacets();
  }, [refreshFacets]);

  // Brand choices for the imported review queue (its docs are not on the
  // spine the facets count): the Brand Master list, as before.
  useEffect(() => {
    if (segment !== 'review') return;
    let alive = true;
    productApi
      .getBrandOptions(filters.category || undefined)
      .then((r) => {
        if (alive) setReviewBrands((r.brands || []).map((b) => b.name).filter(Boolean));
      })
      .catch(() => {
        if (alive) setReviewBrands([]);
      });
    return () => {
      alive = false;
    };
  }, [segment, filters.category]);

  // A different address (segment / preset) is a different list: drop the
  // selection and the drawer. Skipped on the FIRST run.
  const viewResetReady = useRef(false);
  useEffect(() => {
    if (!viewResetReady.current) {
      viewResetReady.current = true;
      return;
    }
    clearSelection();
    setDrawerIdx(null);
  }, [segment, photoPreset, clearSelection]);

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
      offset: (page - 1) * pageSize,
      total,
      filters: {
        search: filters.q || undefined,
        category: filters.category || undefined,
        brand: filters.brand || undefined,
        page,
        address: searchParams.toString(),
      },
    });
  }, [segment, loading, items, page, pageSize, total, filters, searchParams]);

  // Same for the Catalog list: the edit page (?edit=<id>, from a row's Edit or
  // the drawer's) walks these products with Save & next / Skip. Another list
  // (Needs review) clears it, so its drafts open with a plain Save.
  useEffect(() => {
    if (loading) return;
    if (segment !== 'catalog') {
      clearReviewQueue(EDIT_QUEUE_KEY);
      return;
    }
    writeReviewQueue(
      {
        ids: items.filter((it) => it.kind === 'spine').map((it) => docId(it.doc)),
        index: 0,
        offset: (page - 1) * pageSize,
        total,
        filters: { page, address: searchParams.toString() },
        list: {
          path: photoPreset ? '/catalog/missing-photos' : '/catalog',
          params: catalogParams,
          pageSize,
        },
      },
      EDIT_QUEUE_KEY
    );
  }, [segment, loading, items, page, pageSize, total, searchParams, photoPreset, catalogParams]);

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
        offset: at >= 0 ? (page - 1) * pageSize : 0,
        total: at >= 0 ? total : undefined,
        filters: {
          search: filters.q || undefined,
          category: filters.category || undefined,
          brand: filters.brand || undefined,
          page,
          address: searchParams.toString(),
        },
      });
      navigate(`/catalog/add?review=${encodeURIComponent(id)}${hash}`);
    },
    [items, page, pageSize, total, filters, navigate, searchParams]
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

  // ---- Model families (G1): a model row expands to its colours/sizes --------
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggleExpanded = useCallback((leadId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(leadId)) next.delete(leadId);
      else next.add(leadId);
      return next;
    });
  }, []);
  // A variant opens in the same drawer (the ?focus deep link's path).
  const openMember = useCallback(
    async (productId: string) => {
      try {
        const doc = (await productApi.getProduct(productId)) as Record<string, unknown>;
        setDrawerIdx(null);
        setFocusItem({ kind: 'spine', doc });
      } catch {
        toast.error('Could not open that colour/size.');
      }
    },
    [toast]
  );

  // ---- Selection ------------------------------------------------------------
  const toggleSelect = useCallback((pid: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(pid)) {
        next.delete(pid);
      } else if (next.size < BULK_CAP) {
        next.add(pid);
      }
      return next;
    });
  }, []);

  // On Needs review a tick is for Approve, and an archived row is never
  // approved (only Restore brings it back): it gets no tick box.
  // In Needs review a tick is for the bulk approve: never an archived row,
  // nor an ordered draft (finished in the product editor, never approved).
  const canTick = (it: CatalogDrawerItem) =>
    segment !== 'review' || (!it.doc.deleted_at && !isOrderedDraft(it.doc));
  const tickable = items.filter(canTick);
  // G1: a model row stands for ALL its colours/sizes -- ticking it ticks
  // every one of them (the bar and the dialogs count items, not rows).
  const rowIds = (it: CatalogDrawerItem): string[] => {
    const fam = it.kind === 'spine' ? (it.doc.family as { members?: FamilyMember[] } | undefined) : undefined;
    const ids = fam?.members?.length ? fam.members.map((m) => m.product_id) : [docId(it.doc)];
    return ids.filter(Boolean);
  };
  const pageIds = tickable.flatMap(rowIds);
  const allOnPage = pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const toggleIds = useCallback((ids: string[]) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (ids.every((id) => next.has(id))) {
        for (const id of ids) next.delete(id);
      } else {
        for (const id of ids) if (next.size < BULK_CAP) next.add(id);
      }
      return next;
    });
  }, []);
  const toggleAllOnPage = () => toggleIds(pageIds);

  const afterBulk = useCallback(
    (message: string, ok: boolean) => {
      setDialog(null);
      toast[ok ? 'success' : 'warning'](message);
      clearSelection();
      void load();
      void refreshFacets();
      void refreshCounts();
    },
    [toast, clearSelection, load, refreshFacets, refreshCounts]
  );

  const handleRestore = useCallback(async () => {
    const ids = Array.from(selected);
    if (ids.length === 0 || bulkRunning) return;
    const ok = window.confirm(
      `Restore ${ids.length} product${ids.length === 1 ? '' : 's'}?\n\n` +
        'They come back in the catalogue and at the till. They do NOT go back on the ' +
        'website by themselves - publish them again from Online Store when they are ready. ' +
        'One still on a listing that is live on the website comes back switched off.'
    );
    if (!ok) return;
    setBulkRunning(true);
    try {
      const res = await catalogBulkApi.restore(ids);
      const done = res.results.filter((r) => r.status === 'done').length;
      const off = res.results.filter((r) => r.status === 'done' && r.switched_on === false);
      const no = res.results.filter((r) => r.status === 'refused');
      afterBulk(
        `${done} restored` +
          (off.length ? ` - ${off.length} switched off. ${off[0].note}` : '') +
          (no.length ? ` - ${no.length} refused (${no[0].reason})` : ''),
        no.length === 0 && off.length === 0
      );
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Could not restore the products.');
    } finally {
      setBulkRunning(false);
    }
  }, [selected, bulkRunning, afterBulk, toast]);

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
    : `${total.toLocaleString('en-IN')} ${grouped ? 'model' : 'product'}${total === 1 ? '' : 's'}${
        segment === 'review'
          ? ' waiting for review'
          : isMissingPhotos
            ? ' without a usable photo — cannot go online'
            : ' in the catalog'
      }`;
  // The result line's "of N": the catalogue in scope (or the archived count).
  const scopeTotal =
    segment !== 'catalog'
      ? null
      : archivedView
        ? facets?.flags?.archived ?? null
        : facets?.total ?? null;

  const flaggedOpen = facets?.flags?.open ?? 0;
  const flaggedMine = facets?.flags?.mine ?? 0;
  const showChecks = segment === 'review' || canFlag || isAdmin;

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
              void refreshFacets();
            }}
            className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
          >
            <RefreshCw className="h-4 w-4" /> Refresh
          </button>
          {canEdit && (
            <button
              onClick={() => navigate('/catalog/add')}
              className="btn-primary inline-flex items-center gap-1.5"
            >
              <Plus className="h-4 w-4" /> Add product
            </button>
          )}
        </div>
      </div>

      {/* Counts row — the catalog's truth, from the server */}
      {segment === 'catalog' && <CountsRow counts={counts} />}

      {/* Amber review banner — links to the queue's own address */}
      {reviewCount > 0 && segment === 'catalog' && canEdit && (
        <Link
          to="/catalog/review"
          className="w-full flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-left hover:bg-amber-100 transition-colors"
        >
          <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
          <span className="text-sm text-amber-800">
            {/* Imports AND items a manager ordered before they were catalogued
                (audit C1, R1-99): the queue is not only imports any more. */}
            <span className="font-semibold">{reviewCount.toLocaleString('en-IN')}</span> product
            {reviewCount === 1 ? ' is' : 's are'} waiting for review
          </span>
          <span className="ml-auto text-sm font-medium text-amber-700 underline">Review now</span>
        </Link>
      )}

      {/* Segment links — real addresses, not a query string */}
      {canEdit && (
        <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
          <NavLink to="/catalog" end className={({ isActive }) => segmentLink(isActive)}>
            Catalog
          </NavLink>
          <NavLink to="/catalog/review" className={({ isActive }) => segmentLink(isActive)}>
            Needs review
            {reviewCount > 0 && (
              <span className="inline-flex items-center justify-center rounded-full bg-amber-500 px-1.5 py-px text-[10px] font-semibold text-white min-w-[1.25rem]">
                {reviewCount > 999 ? '999+' : reviewCount}
              </span>
            )}
          </NavLink>
        </div>
      )}

      {/* Red flags banner — products flagged as needing work (server counts) */}
      {segment === 'catalog' && flaggedOpen > 0 && (
        <button
          type="button"
          onClick={() =>
            setFilters({ ...filters, flag: isCataloguer && flaggedMine > 0 ? 'mine' : 'any' })
          }
          className="w-full flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-left hover:bg-red-100 transition-colors"
          data-testid="flags-banner"
        >
          <Flag className="h-4 w-4 text-red-700 shrink-0" />
          <span className="text-sm text-red-900">
            <span className="font-semibold">
              {flaggedOpen.toLocaleString('en-IN')} product{flaggedOpen === 1 ? ' is' : 's are'}
            </span>{' '}
            flagged as needing work
            {flaggedMine > 0 && (
              <>
                {' '}- <span className="font-semibold">{flaggedMine.toLocaleString('en-IN')} of them
                {flaggedMine === 1 ? ' is' : ' are'} yours</span>
              </>
            )}
          </span>
          <span className="ml-auto text-sm font-medium text-red-800 underline">Show them</span>
        </button>
      )}

      <CatalogFiltersBar
        segment={segment}
        filters={filters}
        onChange={setFilters}
        facets={facets}
        reviewBrands={reviewBrands}
        search={search}
        onSearch={setSearch}
        onSearchFlush={() => flushSearch(search)}
        photoFixed={Boolean(photoPreset)}
        isAdmin={isAdmin}
      />

      {!loading && !error && (
        <p className="text-sm text-gray-700" data-testid="result-line">
          {resultLine(total, scopeTotal, filters.sort, grouped ? 'model' : 'product')}
        </p>
      )}

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
              never scrolls sideways.

              `contain:paint` is LOAD-BEARING, not decoration. The header's
              sr-only labels are position:absolute, and a scroll container is
              NOT the containing block of its absolutely positioned
              descendants -- so each label sits at its static position (the
              far-right column, ~690px) OUTSIDE this box's clip and widens
              whichever ancestor IS its containing block: #main-content
              (body-hscroll) while .ims-anim-page still carried a transform,
              the document (doc-overflow) now that it does not. Containing
              paint makes this box their containing block, so they stay
              clipped with the table. Measured 687 -> 350 at 360px. */}
          <div className="overflow-x-auto [contain:paint] rounded-xl border border-gray-200 bg-white">
            <table className="w-full min-w-[960px] text-sm" data-testid="catalog-table">
              <thead>
                <tr className="text-left text-[10px] font-semibold uppercase tracking-[.08em] text-gray-500">
                  {showChecks && (
                    <th className="w-8 px-3 pb-2 pt-3">
                      <input
                        type="checkbox"
                        checked={allOnPage}
                        onChange={toggleAllOnPage}
                        className="h-3.5 w-3.5 accent-gray-700"
                        aria-label="Select all on this page"
                      />
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
                  <th className="px-3 pb-2 pt-3">Added</th>
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
                  const orderedDraft = isOrderedDraft(doc);
                  const inactive = doc.is_active === false && !orderedDraft;
                  const needsReview = it.kind === 'imported' && Boolean(doc.needs_review);
                  const hasPhoto = doc.has_photo as boolean | undefined;
                  const online = doc.online as OnlineState | undefined;
                  const flags = openFlags(doc);
                  const noted = flags.find((f) => f.note);
                  const added = addedColumn(doc);
                  const openRow = () => {
                    setFocusItem(null);
                    setDrawerIdx(idx);
                  };
                  // G1: a model with more than one item (the grouped list).
                  const family =
                    it.kind === 'spine'
                      ? (doc.family as { lead_id: string; count: number; members: FamilyMember[] } | undefined)
                      : undefined;
                  const isOpen = Boolean(family && expanded.has(family.lead_id));
                  const ids = rowIds(it);
                  const ticked = ids.filter((id) => selected.has(id)).length;
                  const memberLabel = (m: FamilyMember) =>
                    [m.color, m.size].filter(Boolean).join(' · ') || m.sku || 'variant';
                  return (
                    <Fragment key={pid || idx}>
                    <tr
                      className={clsx(
                        'border-t border-gray-100 hover:bg-gray-50',
                        ticked > 0 && 'bg-amber-50',
                        inactive && 'opacity-60'
                      )}
                    >
                      {showChecks && (
                        <td className="px-3 py-2 align-middle">
                          {canTick(it) && (
                            <input
                              type="checkbox"
                              checked={ticked > 0 && ticked === ids.length}
                              ref={(el) => {
                                if (el) el.indeterminate = ticked > 0 && ticked < ids.length;
                              }}
                              onChange={() => (ids.length > 1 ? toggleIds(ids) : toggleSelect(pid))}
                              className="h-3.5 w-3.5 accent-amber-500"
                              aria-label={ids.length > 1 ? `Select all ${ids.length} items of ${name}` : `Select ${name}`}
                            />
                          )}
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
                        <div className="flex items-start gap-1">
                        {family && (
                          <button
                            type="button"
                            onClick={() => toggleExpanded(family.lead_id)}
                            className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded text-gray-500 hover:bg-gray-100"
                            aria-expanded={isOpen}
                            aria-label={`${isOpen ? 'Hide' : 'Show'} the ${family.count} colours and sizes of ${name}`}
                            data-testid="family-toggle"
                          >
                            {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                          </button>
                        )}
                        <button type="button" onClick={openRow} className="text-left">
                          <div className="font-medium text-gray-900 leading-snug">{name}</div>
                          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-gray-500">
                            {brandName && <span>{brandName}</span>}
                            {needsReview ? (
                              <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                                <AlertTriangle className="h-3 w-3" /> Needs review
                              </span>
                            ) : orderedDraft ? (
                              <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">
                                <AlertTriangle className="h-3 w-3" /> Ordered — finish it
                              </span>
                            ) : doc.deleted_at ? (
                              <span className="inline-flex items-center rounded-full bg-gray-200 px-2 py-0.5 text-[10px] font-medium text-gray-700">
                                Archived
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
                            {flags.map((f) => (
                              <span
                                key={f.flag_id}
                                className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-[10px] font-semibold text-red-800"
                                data-testid="flag-chip"
                              >
                                <Flag className="h-2.5 w-2.5" /> {flagLabel(f.type)}
                              </span>
                            ))}
                            {failMap[pid] && (
                              <span
                                className="inline-flex items-center rounded-full bg-orange-100 px-2 py-0.5 text-[10px] font-medium text-orange-700"
                                title={failMap[pid]}
                              >
                                needs fixes
                              </span>
                            )}
                          </div>
                          {noted && (
                            <div className="mt-1 text-[11.5px] text-red-800">
                              Note from {noted.by_name || 'a manager'}: {noted.note}
                            </div>
                          )}
                          {family && (
                            <div className="mt-1 flex flex-wrap items-center gap-1" data-testid="family-chips">
                              <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold text-gray-700">
                                {family.count} variants
                              </span>
                              {family.members.slice(0, 6).map((m) => (
                                <span
                                  key={m.product_id}
                                  className="rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[10px] text-gray-600"
                                >
                                  {memberLabel(m)}
                                </span>
                              ))}
                              {family.members.length > 6 && (
                                <span className="text-[10px] text-gray-500">+{family.members.length - 6} more</span>
                              )}
                            </div>
                          )}
                        </button>
                        </div>
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
                        <OnlineChip state={online} note={doc.online_note as string | null | undefined} />
                      </td>
                      <td className="px-3 py-2 align-middle text-[11.5px] leading-snug" data-testid="added-cell">
                        <div className="font-medium text-gray-900">{added.by}</div>
                        <div className="text-gray-600">{added.when}</div>
                        <div className="text-gray-500">{added.edited}</div>
                      </td>
                      <td className="px-3 py-2 align-middle text-right">
                        {!canEdit ? null : hasPhoto === false ? (
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
                    {isOpen &&
                      family!.members.map((m) => (
                        <tr
                          key={`${family!.lead_id}:${m.product_id}`}
                          className={clsx('border-t border-gray-50 bg-gray-50/60 text-xs', !m.is_active && 'opacity-60')}
                          data-testid="family-member-row"
                        >
                          {showChecks && (
                            <td className="px-3 py-1.5">
                              {canTick(it) && (
                                <input
                                  type="checkbox"
                                  checked={selected.has(m.product_id)}
                                  onChange={() => toggleSelect(m.product_id)}
                                  className="h-3.5 w-3.5 accent-amber-500"
                                  aria-label={`Select ${memberLabel(m)}`}
                                />
                              )}
                            </td>
                          )}
                          <td />
                          <td className="px-3 py-1.5">
                            <button
                              type="button"
                              onClick={() => void openMember(m.product_id)}
                              className="pl-6 text-left text-gray-800 hover:underline"
                            >
                              {memberLabel(m)}
                              {m.is_lead && (
                                <span className="ml-1.5 rounded-full bg-bv-50 px-1.5 py-0.5 text-[10px] font-medium text-bv">
                                  model
                                </span>
                              )}
                              {!m.is_active && <span className="ml-1.5 text-gray-500">(off)</span>}
                            </button>
                          </td>
                          <td className="px-3 py-1.5 font-mono text-gray-700">{m.sku || '—'}</td>
                          <td />
                          <td className="px-3 py-1.5 text-right tabular-nums text-gray-900">
                            {fmtINR(m.offer_price ?? m.mrp ?? null)}
                          </td>
                          <td colSpan={4} />
                        </tr>
                      ))}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Pages + Rows per page: whenever the list has rows to page through,
          so no page is ever a dead end. */}
      {!error && total > 0 && (
        <div className="flex flex-wrap items-end justify-between gap-3">
          <span className="pb-2 text-xs text-gray-500">Times shown in India time (IST)</span>
          <div className="flex flex-wrap items-end gap-3">
            <Pagination
              currentPage={page}
              totalItems={total}
              pageSize={pageSize}
              onPageChange={setPage}
            />
            <label className="flex items-center gap-2 pb-1 text-sm text-gray-700">
              Rows per page
              <select
                value={pageSize}
                onChange={(e) => {
                  const rows = Number(e.target.value);
                  rememberRows(rows);
                  setRowsMemory(rows);
                  setFilters({ ...filters, rows });
                }}
                className="input-field w-20"
                aria-label="Rows per page"
              >
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
      )}

      {/* Selection bar — catalog: flag / archive / restore (light grey) */}
      {segment === 'catalog' && selected.size > 0 && (canFlag || isAdmin) && (
        <SelectionBar
          count={selected.size}
          cap={BULK_CAP}
          canFlag={canFlag}
          canArchive={isAdmin}
          archivedView={archivedView}
          flagType={flagType}
          onFlagType={setFlagType}
          onFlag={() => setDialog('flag')}
          onArchive={() => setDialog('archive')}
          onRestore={() => void handleRestore()}
          onClear={clearSelection}
          busy={bulkRunning}
        />
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
              onClick={clearSelection}
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

      {dialog === 'flag' && (
        <FlagDialog
          ids={Array.from(selected)}
          initialType={flagType}
          onClose={() => setDialog(null)}
          onDone={afterBulk}
        />
      )}
      {dialog === 'archive' && (
        <ArchiveDialog
          ids={Array.from(selected)}
          onClose={() => setDialog(null)}
          onDone={afterBulk}
        />
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
