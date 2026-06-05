// ============================================================================
// IMS 2.0 - Online Store - Collections editor  (BVI Phase 2, "push-dark")
// ============================================================================
// FLAGSHIP #1 of the e-commerce (BVI) merge: manage storefront Collections
// entirely inside IMS. See docs/reference/BVI_MERGE_PLAN.md section B / Phase 2.
//
// SCOPE (Phase 2): store + edit collections in IMS Mongo. There is NO Shopify
// network write here — that single-writer push is Phase 5/6. So this screen is
// a pure PIM editor over /api/v1/online-store/collections:
//   - LIST: title, type (CUSTOM/SMART) badge, products_count, published toggle,
//           sort_priority — with create/edit/delete.
//   - EDIT drawer (common fields): title, auto handle, description, SEO
//           title/description, banner_image, short_description, sort_priority,
//           published. Read-only auto-lineage (auto_source / category_anchor).
//   - type=CUSTOM: a manual product picker (search the catalog by sku/brand,
//           add/remove/reorder the member list).
//   - type=SMART: a simple rules editor (field/op/value rows + AND/OR) with a
//           "preview matches" button that calls the smart-rule resolver.
//
// FAIL-SOFT: the Phase-2 backend router may not be deployed yet. Reads degrade
// to empty lists (the screen renders a friendly "backend not yet available"
// note); writes toast the backend error. Gated SUPERADMIN / ADMIN /
// CATALOG_MANAGER / DESIGN_MANAGER at the route (App.tsx). Light theme only.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Layers,
  Plus,
  Search,
  X,
  Save,
  Loader2,
  Trash2,
  Pencil,
  ArrowLeft,
  ArrowUp,
  ArrowDown,
  Eye,
  Sparkles,
  ListChecks,
  PackagePlus,
  Info,
  RefreshCw,
  UploadCloud,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import {
  collectionsApi,
  pushApi,
  type EcomCollection,
  type CollectionType,
  type CollectionProduct,
  type CatalogPick,
  type SmartRule,
  type SmartRules,
} from '../../services/api/onlineStore';
import OnlineStoreSyncBanner, {
  SyncChip,
  formatPushResult,
  type OnlineStoreSyncBannerHandle,
} from '../../components/online-store/OnlineStoreSyncBanner';

// ---------------------------------------------------------------------------
// Smart-rule vocabulary (kept small + storefront-meaningful — Shopify-like).
// ---------------------------------------------------------------------------
const RULE_FIELDS: Array<{ value: string; label: string }> = [
  { value: 'brand', label: 'Brand' },
  { value: 'category', label: 'Category' },
  { value: 'product_type', label: 'Product type' },
  { value: 'title', label: 'Title' },
  { value: 'tag', label: 'Tag' },
  { value: 'price', label: 'Price' },
];

const RULE_OPS: Array<{ value: string; label: string }> = [
  { value: 'equals', label: 'is equal to' },
  { value: 'not_equals', label: 'is not equal to' },
  { value: 'contains', label: 'contains' },
  { value: 'starts_with', label: 'starts with' },
  { value: 'ends_with', label: 'ends with' },
  { value: 'greater_than', label: 'is greater than' },
  { value: 'less_than', label: 'is less than' },
];

// Auto a handle from a title: lowercase, spaces/punct -> single hyphen.
function slugify(s: string): string {
  return (s || '')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120);
}

function fmtCount(n: number | null | undefined): string {
  if (n === null || n === undefined) return '0';
  try {
    return n.toLocaleString('en-IN');
  } catch {
    return String(n);
  }
}

// A blank editor draft (used for "New collection").
function blankDraft(type: CollectionType): EditorDraft {
  return {
    id: null,
    title: '',
    handle: '',
    handleTouched: false,
    description: '',
    collection_type: type,
    seo_title: '',
    seo_description: '',
    banner_image: '',
    short_description: '',
    sort_priority: 100,
    published: true,
    disjunctive: false,
    rules: [{ field: 'brand', op: 'equals', value: '' }],
    auto_source: null,
    category_anchor: null,
  };
}

interface EditorDraft {
  id: string | null;
  title: string;
  handle: string;
  handleTouched: boolean; // once the user edits handle, stop auto-deriving it
  description: string;
  collection_type: CollectionType;
  seo_title: string;
  seo_description: string;
  banner_image: string;
  short_description: string;
  sort_priority: number;
  published: boolean;
  disjunctive: boolean;
  rules: SmartRule[];
  auto_source: string | null;
  category_anchor: string | null;
}

// ===========================================================================
// Page
// ===========================================================================
export default function CollectionsPage() {
  const toast = useToast();
  const { hasRole } = useAuth();
  // Publishing to the live storefront is integration-critical -> SUPERADMIN /
  // ADMIN only (matches the backend push router gate). The backend is the real
  // enforcement; this just hides the control from everyone else.
  const canPublish = hasRole(['SUPERADMIN', 'ADMIN']);

  const [collections, setCollections] = useState<EcomCollection[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  // Which collection id is mid-publish (disables its button + spins).
  const [publishingId, setPublishingId] = useState<string | null>(null);
  const bannerRef = useRef<OnlineStoreSyncBannerHandle>(null);

  // Drawer state
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [draft, setDraft] = useState<EditorDraft>(() => blankDraft('CUSTOM'));
  const [saving, setSaving] = useState(false);
  const [editingExisting, setEditingExisting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await collectionsApi.list();
      setCollections(rows);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return collections;
    return collections.filter(
      (c) =>
        (c.title || '').toLowerCase().includes(q) ||
        (c.handle || '').toLowerCase().includes(q) ||
        (c.auto_source || '').toLowerCase().includes(q),
    );
  }, [collections, search]);

  const openCreate = (type: CollectionType) => {
    setDraft(blankDraft(type));
    setEditingExisting(false);
    setDrawerOpen(true);
  };

  const openEdit = async (c: EcomCollection) => {
    // Pull the full record (smart_rules may not be in the list payload).
    const full = (await collectionsApi.get(c.id)) ?? c;
    const sr: SmartRules | null = full.smart_rules ?? null;
    setDraft({
      id: full.id,
      title: full.title ?? '',
      handle: full.handle ?? '',
      handleTouched: true, // never clobber an existing handle from the title
      description: full.description ?? '',
      collection_type: (full.collection_type ?? 'CUSTOM') as CollectionType,
      seo_title: full.seo_title ?? '',
      seo_description: full.seo_description ?? '',
      banner_image: full.banner_image ?? '',
      short_description: full.short_description ?? '',
      sort_priority: typeof full.sort_priority === 'number' ? full.sort_priority : 100,
      published: full.published ?? true,
      disjunctive: sr?.disjunctive ?? false,
      rules:
        sr?.rules && sr.rules.length > 0
          ? sr.rules
          : [{ field: 'brand', op: 'equals', value: '' }],
      auto_source: full.auto_source ?? null,
      category_anchor: full.category_anchor ?? null,
    });
    setEditingExisting(true);
    setDrawerOpen(true);
  };

  const closeDrawer = () => {
    if (saving) return;
    setDrawerOpen(false);
  };

  const togglePublished = async (c: EcomCollection) => {
    const next = !(c.published ?? false);
    // optimistic
    setCollections((prev) =>
      prev.map((x) => (x.id === c.id ? { ...x, published: next } : x)),
    );
    try {
      await collectionsApi.setPublished(c.id, next);
      toast.success(next ? 'Collection published' : 'Collection unpublished');
    } catch (e: any) {
      // revert
      setCollections((prev) =>
        prev.map((x) => (x.id === c.id ? { ...x, published: !next } : x)),
      );
      toast.error(e?.message || 'Could not update published status');
    }
  };

  // Publish (push) one collection to Shopify. DARK by default -> a SIMULATED
  // dry-run; the returned mode (SIMULATED vs LIVE) is surfaced in the toast so a
  // dry-run is never mistaken for a live write. On a successful LIVE create the
  // collection gains a shopify_collection_id; refresh the list + banner so the
  // Synced chip + counts update.
  const publishCollection = async (c: EcomCollection) => {
    setPublishingId(c.id);
    try {
      const result = await pushApi.pushCollection(c.id);
      if (result.ok) {
        toast.success(formatPushResult(`Collection "${c.title || c.handle}"`, result));
      } else {
        toast.warning(formatPushResult(`Collection "${c.title || c.handle}"`, result));
      }
      if (result.ok && result.mode === 'LIVE') {
        // Live write may have minted/updated the Shopify id -> reflect it.
        await load();
        bannerRef.current?.refresh();
      }
    } catch (e: any) {
      toast.error(
        e?.response?.data?.detail || e?.message || 'Could not publish collection',
      );
    } finally {
      setPublishingId(null);
    }
  };

  const deleteCollection = async (c: EcomCollection) => {
    if (!window.confirm(`Delete collection "${c.title}"? This cannot be undone.`)) return;
    try {
      await collectionsApi.remove(c.id);
      setCollections((prev) => prev.filter((x) => x.id !== c.id));
      toast.success('Collection deleted');
    } catch (e: any) {
      toast.error(e?.message || 'Could not delete collection');
    }
  };

  // Save (create or update) the common fields + (for SMART) the rules.
  const saveDraft = async (): Promise<string | null> => {
    if (!draft.title.trim()) {
      toast.error('Title is required');
      return null;
    }
    setSaving(true);
    try {
      const payload = {
        title: draft.title.trim(),
        handle: (draft.handle || slugify(draft.title)).trim(),
        description: draft.description,
        collection_type: draft.collection_type,
        seo_title: draft.seo_title,
        seo_description: draft.seo_description,
        banner_image: draft.banner_image,
        short_description: draft.short_description,
        sort_priority: Number.isFinite(draft.sort_priority) ? draft.sort_priority : 100,
        published: draft.published,
        ...(draft.collection_type === 'SMART'
          ? {
              smart_rules: {
                disjunctive: draft.disjunctive,
                rules: draft.rules.filter((r) => r.value.trim() !== ''),
              } as SmartRules,
            }
          : {}),
      };
      let saved: EcomCollection;
      if (draft.id) {
        saved = await collectionsApi.update(draft.id, payload);
      } else {
        saved = await collectionsApi.create(payload);
      }
      toast.success(draft.id ? 'Collection saved' : 'Collection created');
      await load();
      // Keep the drawer open on create so the user can immediately add products
      // to a CUSTOM collection; flip to "editing existing" with the new id.
      setDraft((d) => ({ ...d, id: saved.id }));
      setEditingExisting(true);
      return saved.id;
    } catch (e: any) {
      toast.error(e?.message || 'Could not save collection — is the Online Store backend deployed?');
      return null;
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-1">
        <div>
          <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
            <Link to="/online-store" className="inline-flex items-center gap-1 hover:text-gray-700">
              <ArrowLeft className="w-3.5 h-3.5" /> Online Store
            </Link>
            <span>/</span>
            <span className="text-gray-700">Collections</span>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <Layers className="w-5 h-5" /> Collections
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => openCreate('SMART')}
            className="btn-outline inline-flex items-center gap-1.5 text-sm"
            title="A collection whose members are decided by rules"
          >
            <Sparkles className="w-4 h-4" /> New smart
          </button>
          <button
            type="button"
            onClick={() => openCreate('CUSTOM')}
            className="btn-primary inline-flex items-center gap-1.5 text-sm"
            title="A collection you fill by hand-picking products"
          >
            <Plus className="w-4 h-4" /> New collection
          </button>
        </div>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        Group products into storefront collections — by hand (custom) or by rules (smart). Edits are
        saved inside IMS. Pushing collections live to the storefront is a later, owner-approved step,
        so nothing here changes the live site yet.
      </p>

      {/* Shopify publish (DARK / LIVE) banner */}
      <OnlineStoreSyncBanner ref={bannerRef} className="mb-4" />

      {/* Toolbar */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search collections…"
            className="input-field w-full pl-9"
          />
        </div>
        <button
          type="button"
          onClick={load}
          className="btn-outline inline-flex items-center gap-1.5 text-sm"
          title="Reload"
        >
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      {/* List */}
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500 p-6">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading collections…
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-8 text-center">
            <Layers className="w-8 h-8 text-gray-300 mx-auto mb-2" />
            <p className="text-sm font-medium text-gray-700">
              {search ? 'No collections match your search.' : 'No collections yet.'}
            </p>
            {!search && (
              <p className="text-xs text-gray-500 mt-1 max-w-md mx-auto">
                Create your first collection with “New collection” (hand-picked) or “New smart”
                (rule-based). If you expected existing collections here, the Online Store backend may
                not be deployed yet — this screen is fail-soft and will fill in once it is.
              </p>
            )}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 border-b border-gray-200 bg-gray-50">
                <th className="px-4 py-2 font-medium">Title</th>
                <th className="px-4 py-2 font-medium">Type</th>
                <th className="px-4 py-2 font-medium text-right">Products</th>
                <th className="px-4 py-2 font-medium text-right">Priority</th>
                <th className="px-4 py-2 font-medium text-center">Published</th>
                <th className="px-4 py-2 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <tr key={c.id} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
                  <td className="px-4 py-2.5">
                    <button
                      type="button"
                      onClick={() => openEdit(c)}
                      className="font-medium text-gray-900 hover:text-bv-red-600 text-left"
                    >
                      {c.title || '(untitled)'}
                    </button>
                    <div className="text-xs text-gray-400 flex items-center gap-2 flex-wrap">
                      {c.handle ? <span>/{c.handle}</span> : null}
                      {c.auto_source ? (
                        <span
                          className="inline-flex items-center rounded bg-gray-100 text-gray-500 border border-gray-200 px-1.5 py-0.5"
                          title="Auto-generated lineage"
                        >
                          auto: {c.auto_source}
                        </span>
                      ) : null}
                      <SyncChip
                        synced={!!c.shopify_collection_id}
                        pending={!!c.locally_modified}
                      />
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    {c.collection_type === 'SMART' ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-purple-100 text-purple-800 border border-purple-200 px-2 py-0.5 text-[11px] font-medium">
                        <Sparkles className="w-3 h-3" /> Smart
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full bg-blue-100 text-blue-800 border border-blue-200 px-2 py-0.5 text-[11px] font-medium">
                        <ListChecks className="w-3 h-3" /> Custom
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right text-gray-700">{fmtCount(c.products_count)}</td>
                  <td className="px-4 py-2.5 text-right text-gray-700">
                    {typeof c.sort_priority === 'number' ? c.sort_priority : 100}
                  </td>
                  <td className="px-4 py-2.5 text-center">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={c.published ? "true" : "false"}
                      onClick={() => togglePublished(c)}
                      className={
                        'relative inline-flex h-5 w-9 items-center rounded-full transition-colors ' +
                        (c.published ? 'bg-green-500' : 'bg-gray-300')
                      }
                      title={c.published ? 'Published — click to unpublish' : 'Hidden — click to publish'}
                    >
                      <span
                        className={
                          'inline-block h-4 w-4 transform rounded-full bg-white transition-transform ' +
                          (c.published ? 'translate-x-4' : 'translate-x-1')
                        }
                      />
                    </button>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center justify-end gap-1">
                      {canPublish && (
                        <button
                          type="button"
                          onClick={() => publishCollection(c)}
                          disabled={publishingId === c.id}
                          className="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-white px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                          title="Push this collection to Shopify (dry-run unless live writes are armed)"
                        >
                          {publishingId === c.id ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <UploadCloud className="w-3.5 h-3.5" />
                          )}
                          Publish
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => openEdit(c)}
                        className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-500"
                        title="Edit"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteCollection(c)}
                        className="p-1.5 rounded-lg hover:bg-red-50 text-gray-400 hover:text-red-600"
                        title="Delete"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <p className="mt-4 text-xs text-gray-400">
        Online Store · Collections · Phase 2 (stored in IMS; storefront push is a later phase).
      </p>

      {/* Editor drawer */}
      {drawerOpen && (
        <CollectionDrawer
          draft={draft}
          setDraft={setDraft}
          editingExisting={editingExisting}
          saving={saving}
          onClose={closeDrawer}
          onSave={saveDraft}
        />
      )}
    </div>
  );
}

// ===========================================================================
// Editor drawer (slide-over from the right)
// ===========================================================================
function CollectionDrawer({
  draft,
  setDraft,
  editingExisting,
  saving,
  onClose,
  onSave,
}: {
  draft: EditorDraft;
  setDraft: React.Dispatch<React.SetStateAction<EditorDraft>>;
  editingExisting: boolean;
  saving: boolean;
  onClose: () => void;
  onSave: () => Promise<string | null>;
}) {
  const set = <K extends keyof EditorDraft>(key: K, value: EditorDraft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  // Auto-derive handle from title until the user edits the handle directly.
  const onTitleChange = (v: string) =>
    setDraft((d) => ({
      ...d,
      title: v,
      handle: d.handleTouched ? d.handle : slugify(v),
    }));

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* backdrop */}
      <div
        className="absolute inset-0 bg-black bg-opacity-40"
        onClick={onClose}
        aria-hidden="true"
      />
      {/* panel */}
      <div className="relative h-full w-full max-w-2xl bg-white shadow-xl flex flex-col">
        {/* header */}
        <div className="sticky top-0 z-10 flex items-center justify-between p-4 border-b border-gray-200 bg-white">
          <div className="flex items-center gap-2">
            {draft.collection_type === 'SMART' ? (
              <Sparkles className="w-5 h-5 text-purple-600" />
            ) : (
              <ListChecks className="w-5 h-5 text-blue-600" />
            )}
            <div>
              <h2 className="text-base font-semibold text-gray-900">
                {draft.id ? 'Edit collection' : `New ${draft.collection_type === 'SMART' ? 'smart' : 'custom'} collection`}
              </h2>
              <p className="text-xs text-gray-500">
                {draft.collection_type === 'SMART'
                  ? 'Members are decided by rules'
                  : 'Members are hand-picked'}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="p-2 hover:bg-gray-100 rounded-lg"
            title="Close"
            aria-label="Close"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {/* --- Common fields --- */}
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Title *</label>
              <input
                type="text"
                value={draft.title}
                onChange={(e) => onTitleChange(e.target.value)}
                placeholder="e.g. Ray-Ban Aviators"
                className="input-field w-full"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Handle (URL)</label>
              <input
                type="text"
                value={draft.handle}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, handle: slugify(e.target.value), handleTouched: true }))
                }
                placeholder="auto from title"
                className="input-field w-full font-mono text-xs"
              />
              <p className="text-xs text-gray-400 mt-1">
                The storefront path: /collections/<span className="font-mono">{draft.handle || 'handle'}</span>
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Short description</label>
              <input
                type="text"
                value={draft.short_description}
                onChange={(e) => set('short_description', e.target.value)}
                maxLength={200}
                placeholder="1–2 sentence summary (under 200 chars)"
                className="input-field w-full"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
              <textarea
                value={draft.description}
                onChange={(e) => set('description', e.target.value)}
                rows={3}
                placeholder="Longer collection description (shown on the collection page)"
                className="input-field w-full"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Banner image URL</label>
              <input
                type="text"
                value={draft.banner_image}
                onChange={(e) => set('banner_image', e.target.value)}
                placeholder="https://… (shown at the top of the collection page)"
                className="input-field w-full"
              />
              {draft.banner_image ? (
                <div className="mt-2">

                  <img
                    src={draft.banner_image}
                    alt="Banner preview"
                    className="h-20 w-full object-cover rounded-lg border border-gray-200"
                    onError={(e) => {
                      (e.currentTarget as HTMLImageElement).style.display = 'none';
                    }}
                  />
                </div>
              ) : null}
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label htmlFor="sort-priority" className="block text-sm font-medium text-gray-700 mb-1">Sort priority</label>
                <input
                  id="sort-priority"
                  type="number"
                  value={draft.sort_priority}
                  onChange={(e) => set('sort_priority', parseInt(e.target.value, 10) || 0)}
                  className="input-field w-full"
                  title="Sort Priority"
                  placeholder="Sort Priority"
                />
                <p className="text-xs text-gray-400 mt-1">Lower sorts first.</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Visibility</label>
                <label className="flex items-center gap-2 mt-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={draft.published}
                    onChange={(e) => set('published', e.target.checked)}
                    className="w-4 h-4 rounded"
                  />
                  <span className="text-sm text-gray-700">Published</span>
                </label>
              </div>
            </div>

            {/* SEO */}
            <details className="rounded-lg border border-gray-200">
              <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-gray-700">
                SEO
              </summary>
              <div className="p-3 pt-0 space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">SEO title</label>
                  <input
                    type="text"
                    value={draft.seo_title}
                    onChange={(e) => set('seo_title', e.target.value)}
                    className="input-field w-full"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">SEO description</label>
                  <textarea
                    value={draft.seo_description}
                    onChange={(e) => set('seo_description', e.target.value)}
                    rows={2}
                    className="input-field w-full"
                  />
                </div>
              </div>
            </details>

            {/* Auto-lineage (read-only) */}
            {(draft.auto_source || draft.category_anchor) && (
              <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs text-gray-600">
                <div className="flex items-center gap-1.5 font-medium text-gray-700 mb-1">
                  <Info className="w-3.5 h-3.5" /> Auto-generated lineage (read-only)
                </div>
                {draft.auto_source && (
                  <div>
                    Source: <span className="font-mono">{draft.auto_source}</span>
                  </div>
                )}
                {draft.category_anchor && (
                  <div>
                    Category anchor: <span className="font-mono">{draft.category_anchor}</span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* --- Type-specific section --- */}
          {draft.collection_type === 'CUSTOM' ? (
            <ManualPicker
              collectionId={draft.id}
              editingExisting={editingExisting}
            />
          ) : (
            <SmartRulesEditor
              collectionId={draft.id}
              disjunctive={draft.disjunctive}
              rules={draft.rules}
              setDraft={setDraft}
            />
          )}
        </div>

        {/* footer */}
        <div className="sticky bottom-0 flex items-center justify-between p-4 border-t border-gray-200 bg-gray-50">
          <button type="button" onClick={onClose} disabled={saving} className="btn-outline">
            Close
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saving}
            className="btn-primary inline-flex items-center gap-2"
          >
            {saving ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" /> Saving…
              </>
            ) : (
              <>
                <Save className="w-4 h-4" /> {draft.id ? 'Save changes' : 'Create collection'}
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

// ===========================================================================
// CUSTOM: manual product picker (search catalog + add/remove/reorder members)
// ===========================================================================
function ManualPicker({
  collectionId,
  editingExisting,
}: {
  collectionId: string | null;
  editingExisting: boolean;
}) {
  const toast = useToast();
  const [members, setMembers] = useState<CollectionProduct[]>([]);
  const [loadingMembers, setLoadingMembers] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<CatalogPick[]>([]);
  const [searching, setSearching] = useState(false);

  const loadMembers = useCallback(async () => {
    if (!collectionId) {
      setMembers([]);
      return;
    }
    setLoadingMembers(true);
    try {
      setMembers(await collectionsApi.members(collectionId));
    } finally {
      setLoadingMembers(false);
    }
  }, [collectionId]);

  useEffect(() => {
    loadMembers();
  }, [loadMembers]);

  const runSearch = async () => {
    const q = query.trim();
    if (q.length < 2) {
      toast.info('Type at least 2 characters to search');
      return;
    }
    setSearching(true);
    try {
      setResults(await collectionsApi.searchCatalog(q));
    } finally {
      setSearching(false);
    }
  };

  // Membership is keyed on SKU at the backend, so de-dupe + identify by SKU.
  const memberSkus = useMemo(
    () => new Set(members.map((m) => m.sku).filter(Boolean) as string[]),
    [members],
  );

  const add = async (p: CatalogPick) => {
    if (!collectionId) {
      toast.info('Save the collection first, then add products');
      return;
    }
    if (!p.sku) {
      toast.error('This product has no SKU and cannot be added');
      return;
    }
    if (memberSkus.has(p.sku)) return;
    try {
      await collectionsApi.addProduct(collectionId, p.sku);
      // optimistic append
      setMembers((prev) => [
        ...prev,
        {
          product_id: p.product_id,
          sku: p.sku,
          title: p.title,
          brand: p.brand,
          category: p.category,
          image: p.image,
          position: prev.length,
        },
      ]);
      toast.success('Added to collection');
    } catch (e: any) {
      toast.error(e?.message || 'Could not add product');
    }
  };

  const remove = async (sku: string | null | undefined) => {
    if (!collectionId || !sku) return;
    try {
      await collectionsApi.removeProduct(collectionId, sku);
      setMembers((prev) => prev.filter((m) => m.sku !== sku));
      toast.success('Removed from collection');
    } catch (e: any) {
      toast.error(e?.message || 'Could not remove product');
    }
  };

  const move = async (index: number, dir: -1 | 1) => {
    const next = index + dir;
    if (next < 0 || next >= members.length || !collectionId) return;
    const reordered = members.slice();
    const [item] = reordered.splice(index, 1);
    reordered.splice(next, 0, item);
    setMembers(reordered); // optimistic
    try {
      await collectionsApi.reorder(
        collectionId,
        reordered.map((m) => m.sku).filter(Boolean) as string[],
      );
    } catch (e: any) {
      // revert by reloading the source of truth
      await loadMembers();
      toast.error(e?.message || 'Could not save the new order');
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-gray-900">
        <PackagePlus className="w-4 h-4" /> Products in this collection
      </div>

      {!collectionId && !editingExisting && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
          Save this collection first (button below). Then you can search the catalog and add
          products to it.
        </div>
      )}

      {/* Search the catalog */}
      <div className="rounded-lg border border-gray-200 p-3">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  runSearch();
                }
              }}
              placeholder="Search catalog by SKU, brand or name…"
              className="input-field w-full pl-9"
              disabled={!collectionId}
            />
          </div>
          <button
            type="button"
            onClick={runSearch}
            disabled={!collectionId || searching}
            className="btn-outline inline-flex items-center gap-1.5 text-sm"
          >
            {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            Search
          </button>
        </div>

        {results.length > 0 && (
          <ul className="mt-3 max-h-56 overflow-y-auto divide-y divide-gray-100 border-t border-gray-100">
            {results.map((p) => {
              const already = !!p.sku && memberSkus.has(p.sku);
              return (
                <li key={p.product_id} className="flex items-center justify-between gap-2 py-2">
                  <div className="min-w-0">
                    <div className="text-sm text-gray-900 truncate">
                      {p.title || p.sku || p.product_id}
                    </div>
                    <div className="text-xs text-gray-400 truncate">
                      {[p.brand, p.category, p.sku].filter(Boolean).join(' · ')}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => add(p)}
                    disabled={already}
                    className={
                      'text-xs rounded-lg px-2.5 py-1 border ' +
                      (already
                        ? 'bg-gray-100 text-gray-400 border-gray-200 cursor-default'
                        : 'bg-white text-bv-red-600 border-gray-200 hover:bg-gray-50')
                    }
                  >
                    {already ? 'Added' : 'Add'}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Current members */}
      <div className="rounded-lg border border-gray-200">
        <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-100 flex items-center justify-between">
          <span>{members.length} product{members.length === 1 ? '' : 's'}</span>
          {loadingMembers && <Loader2 className="w-3.5 h-3.5 animate-spin text-gray-400" />}
        </div>
        {members.length === 0 ? (
          <div className="p-4 text-center text-xs text-gray-400">
            No products yet. Search above to add some.
          </div>
        ) : (
          <ul className="divide-y divide-gray-100">
            {members.map((m, i) => (
              <li key={m.product_id} className="flex items-center justify-between gap-2 px-3 py-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-xs text-gray-400 w-5 text-right">{i + 1}</span>
                  <div className="min-w-0">
                    <div className="text-sm text-gray-900 truncate">
                      {m.title || m.sku || m.product_id}
                    </div>
                    <div className="text-xs text-gray-400 truncate">
                      {[m.brand, m.category, m.sku].filter(Boolean).join(' · ')}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => move(i, -1)}
                    disabled={i === 0}
                    className="p-1 rounded hover:bg-gray-100 text-gray-400 disabled:opacity-30"
                    title="Move up"
                  >
                    <ArrowUp className="w-4 h-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => move(i, 1)}
                    disabled={i === members.length - 1}
                    className="p-1 rounded hover:bg-gray-100 text-gray-400 disabled:opacity-30"
                    title="Move down"
                  >
                    <ArrowDown className="w-4 h-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => remove(m.sku)}
                    className="p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-600"
                    title="Remove"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ===========================================================================
// SMART: rules editor (field/op/value rows + AND/OR) + preview matches
// ===========================================================================
function SmartRulesEditor({
  collectionId,
  disjunctive,
  rules,
  setDraft,
}: {
  collectionId: string | null;
  disjunctive: boolean;
  rules: SmartRule[];
  setDraft: React.Dispatch<React.SetStateAction<EditorDraft>>;
}) {
  const toast = useToast();
  const [preview, setPreview] = useState<CollectionProduct[] | null>(null);
  const [previewTotal, setPreviewTotal] = useState(0);
  const [previewing, setPreviewing] = useState(false);

  const setRule = (idx: number, patch: Partial<SmartRule>) =>
    setDraft((d) => ({
      ...d,
      rules: d.rules.map((r, i) => (i === idx ? { ...r, ...patch } : r)),
    }));

  const addRule = () =>
    setDraft((d) => ({
      ...d,
      rules: [...d.rules, { field: 'brand', op: 'equals', value: '' }],
    }));

  const removeRule = (idx: number) =>
    setDraft((d) => ({
      ...d,
      rules: d.rules.length <= 1 ? d.rules : d.rules.filter((_, i) => i !== idx),
    }));

  const runPreview = async () => {
    const clean = rules.filter((r) => r.value.trim() !== '');
    if (clean.length === 0) {
      toast.info('Add at least one rule with a value to preview');
      return;
    }
    // The backend resolves a SAVED collection by id (there is no ad-hoc resolver),
    // so the preview reflects the rules as last saved. Prompt the user to save.
    if (!collectionId) {
      toast.info('Save the collection first to preview the products its rules match.');
      return;
    }
    setPreviewing(true);
    try {
      const res = await collectionsApi.resolvedProducts({
        id: collectionId,
        rules: { disjunctive, rules: clean },
        limit: 50,
      });
      setPreview(res.products);
      setPreviewTotal(res.total);
      if (!res.available) {
        toast.info('Rule preview is unavailable until the Online Store backend is deployed.');
      }
    } finally {
      setPreviewing(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm font-semibold text-gray-900">
        <Sparkles className="w-4 h-4" /> Smart rules
      </div>

      {/* AND/OR selector */}
      <div className="flex items-center gap-2 text-sm">
        <span className="text-gray-600">Products must match</span>
        <div className="inline-flex rounded-lg border border-gray-200 overflow-hidden">
          <button
            type="button"
            onClick={() => setDraft((d) => ({ ...d, disjunctive: false }))}
            className={
              'px-3 py-1 text-xs font-medium ' +
              (!disjunctive ? 'bg-bv-red-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50')
            }
          >
            ALL rules (AND)
          </button>
          <button
            type="button"
            onClick={() => setDraft((d) => ({ ...d, disjunctive: true }))}
            className={
              'px-3 py-1 text-xs font-medium border-l border-gray-200 ' +
              (disjunctive ? 'bg-bv-red-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50')
            }
          >
            ANY rule (OR)
          </button>
        </div>
      </div>

      {/* Rule rows */}
      <div className="space-y-2">
        {rules.map((r, idx) => (
          <div key={idx} className="flex items-center gap-2">
            <select
              value={r.field}
              onChange={(e) => setRule(idx, { field: e.target.value })}
              className="input-field flex-1"
              title="Rule field select"
              aria-label="Rule field select"
            >
              {RULE_FIELDS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
            <select
              value={r.op}
              onChange={(e) => setRule(idx, { op: e.target.value })}
              className="input-field flex-1"
              title="Rule operator select"
              aria-label="Rule operator select"
            >
              {RULE_OPS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={r.value}
              onChange={(e) => setRule(idx, { value: e.target.value })}
              placeholder="value"
              className="input-field flex-1"
            />
            <button
              type="button"
              onClick={() => removeRule(idx)}
              disabled={rules.length <= 1}
              className="p-1.5 rounded-lg hover:bg-red-50 text-gray-400 hover:text-red-600 disabled:opacity-30"
              title="Remove rule"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={addRule}
          className="text-sm text-bv-red-600 inline-flex items-center gap-1 hover:underline"
        >
          <Plus className="w-4 h-4" /> Add rule
        </button>
        <button
          type="button"
          onClick={runPreview}
          disabled={previewing}
          className="btn-outline inline-flex items-center gap-1.5 text-sm"
        >
          {previewing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Eye className="w-4 h-4" />}
          Preview matches
        </button>
      </div>

      {/* Preview results */}
      {preview !== null && (
        <div className="rounded-lg border border-gray-200">
          <div className="px-3 py-2 text-xs text-gray-500 border-b border-gray-100">
            {previewTotal > 0
              ? `${fmtCount(previewTotal)} product${previewTotal === 1 ? '' : 's'} match${
                  previewTotal === 1 ? 'es' : ''
                } these rules${preview.length < previewTotal ? ` (showing first ${preview.length})` : ''}`
              : 'No products match these rules yet.'}
          </div>
          {preview.length > 0 && (
            <ul className="max-h-56 overflow-y-auto divide-y divide-gray-100">
              {preview.map((m) => (
                <li key={m.product_id} className="px-3 py-2">
                  <div className="text-sm text-gray-900 truncate">
                    {m.title || m.sku || m.product_id}
                  </div>
                  <div className="text-xs text-gray-400 truncate">
                    {[m.brand, m.category, m.sku].filter(Boolean).join(' · ')}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <p className="text-xs text-gray-400">
        Smart collections recompute their members from these rules. The exact set is resolved when
        the collection is pushed to the storefront (a later phase); this preview shows what matches
        right now.
      </p>
    </div>
  );
}
