// ============================================================================
// IMS 2.0 - Online Store - Menus / Mega-Menu editor  (BVI Phase 3, "push-dark")
// ============================================================================
// FLAGSHIP #2 of the e-commerce (BVI) merge: edit the storefront navigation
// tree (the mega-menu) entirely inside IMS. See docs/reference/BVI_MERGE_PLAN.md
// section B / Phase 3 ("Menus + Mega-Menu editor ... thumbnails, badges,
// pin-to-top").
//
// SCOPE (Phase 3): store + edit menus in IMS Mongo (`ecom_menus`, items embedded
// as a tree). There is NO Shopify network write here — the single-writer
// `menuUpdate` push is Phase 5/6. So this screen is a pure nav-tree editor over
// /api/v1/online-store/menus:
//   - MENUS list (left): handle, title, active toggle, item count; create /
//     select / delete.
//   - TREE editor (right) for the selected menu: nested items with indent,
//     expand/collapse, add child / add sibling, edit (title, item-type picker,
//     url or resource_id, tags_filter) + mega-menu fields (icon_url, banner_url,
//     badge_text + badge_color, pinned_to_top), delete, and up/down reorder.
//   - SAVE persists the whole edited tree via PUT {id} (saveTree). Per-node
//     operations are applied to a LOCAL tree first so the editor stays fully
//     usable even before the Phase-3 backend is deployed; "Save changes" writes
//     the tree back.
//
// FAIL-SOFT: the Phase-3 backend menus router ships separately and may be absent
// in a stale deploy. Reads degrade to an empty menu list (a friendly "backend
// not yet available" note) and the local editor still works; the save toasts the
// backend error. Gated SUPERADMIN / ADMIN / CATALOG_MANAGER / DESIGN_MANAGER at
// the route (App.tsx). Light theme only — no non-ASCII.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Menu as MenuIcon,
  Plus,
  X,
  Save,
  Loader2,
  Trash2,
  Pencil,
  ArrowLeft,
  ArrowUp,
  ArrowDown,
  ChevronRight,
  ChevronDown,
  CornerDownRight,
  Pin,
  RefreshCw,
  Info,
  Image as ImageIcon,
  Tag,
  Link2,
  UploadCloud,
} from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { useAuth } from '../../context/AuthContext';
import {
  menusApi,
  pushApi,
  type EcomMenu,
  type MenuItem,
  type MenuItemType,
} from '../../services/api/onlineStore';
import OnlineStoreSyncBanner, {
  SyncChip,
  formatPushResult,
  type OnlineStoreSyncBannerHandle,
} from '../../components/online-store/OnlineStoreSyncBanner';

// ---------------------------------------------------------------------------
// Item-type vocabulary (mirrors the Shopify MenuItemType enum / BVI MenuItem).
// Split into "resource" types (need a resource_id) vs URL/derived so the editor
// can hint the right field. Kept storefront-meaningful + small.
// ---------------------------------------------------------------------------
const ITEM_TYPES: Array<{ value: MenuItemType; label: string }> = [
  { value: 'COLLECTION', label: 'Collection' },
  { value: 'COLLECTIONS', label: 'All collections' },
  { value: 'PRODUCT', label: 'Product' },
  { value: 'PAGE', label: 'Page' },
  { value: 'BLOG', label: 'Blog' },
  { value: 'ARTICLE', label: 'Article' },
  { value: 'FRONTPAGE', label: 'Home' },
  { value: 'CATALOG', label: 'Catalog' },
  { value: 'SEARCH', label: 'Search' },
  { value: 'HTTP', label: 'Link (URL)' },
  { value: 'SHOP_POLICY', label: 'Shop policy' },
  { value: 'METAOBJECT', label: 'Metaobject' },
];

// Types that resolve via a Shopify resource id (gid) rather than a raw URL.
const RESOURCE_TYPES = new Set<MenuItemType>([
  'COLLECTION',
  'PRODUCT',
  'PAGE',
  'BLOG',
  'ARTICLE',
  'METAOBJECT',
  'SHOP_POLICY',
]);

// A small palette for badge colors (mega-menu chips). value is a hex; the editor
// also allows a free hex via the color input.
const BADGE_PRESETS: Array<{ label: string; color: string }> = [
  { label: 'Red', color: '#e11d48' },
  { label: 'Green', color: '#16a34a' },
  { label: 'Blue', color: '#2563eb' },
  { label: 'Amber', color: '#d97706' },
  { label: 'Purple', color: '#7c3aed' },
  { label: 'Slate', color: '#475569' },
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

// Stable-ish client id for locally-created items (before the backend mints one).
let _tmpSeq = 0;
function tmpId(): string {
  _tmpSeq += 1;
  return `tmp_${Date.now().toString(36)}_${_tmpSeq}`;
}

// ---------------------------------------------------------------------------
// Local tree helpers (pure). The editor operates on a normalised MenuItem[]
// (children embedded), then PUTs it back. Keeping these pure makes the editor
// deterministic and testable, and means it works with or without the backend.
// ---------------------------------------------------------------------------

/** Deep-clone + ensure every node has an id, an item_type, and a children[]. */
function normaliseTree(items: MenuItem[] | null | undefined): MenuItem[] {
  const walk = (nodes: MenuItem[] | null | undefined): MenuItem[] =>
    (nodes ?? []).map((n, i) => ({
      ...n,
      id: n.id || tmpId(),
      title: n.title ?? '',
      item_type: (n.item_type ?? 'HTTP') as MenuItemType,
      position: typeof n.position === 'number' ? n.position : i,
      children: walk(n.children),
    }));
  return walk(items);
}

/** Re-number `position` to match array order at every level (0-based). */
function resequence(items: MenuItem[]): MenuItem[] {
  return items.map((n, i) => ({
    ...n,
    position: i,
    children: n.children && n.children.length ? resequence(n.children) : (n.children ?? []),
  }));
}

/** Return a new tree with `fn` applied to the node with `id` (identity else). */
function updateNode(
  items: MenuItem[],
  id: string,
  fn: (n: MenuItem) => MenuItem,
): MenuItem[] {
  return items.map((n) => {
    if (n.id === id) return fn(n);
    if (n.children && n.children.length) {
      return { ...n, children: updateNode(n.children, id, fn) };
    }
    return n;
  });
}

/** Remove the node with `id` (and its subtree) anywhere in the tree. */
function removeNode(items: MenuItem[], id: string): MenuItem[] {
  const out: MenuItem[] = [];
  for (const n of items) {
    if (n.id === id) continue;
    out.push(
      n.children && n.children.length ? { ...n, children: removeNode(n.children, id) } : n,
    );
  }
  return out;
}

/** Insert `node` as a child of `parentId` (append). parentId null = top level. */
function insertChild(items: MenuItem[], parentId: string | null, node: MenuItem): MenuItem[] {
  if (parentId === null) return [...items, node];
  return items.map((n) => {
    if (n.id === parentId) {
      return { ...n, children: [...(n.children ?? []), node] };
    }
    if (n.children && n.children.length) {
      return { ...n, children: insertChild(n.children, parentId, node) };
    }
    return n;
  });
}

/** Insert `node` immediately after the sibling with id `afterId`, at whatever
 *  level that sibling lives (used for "add sibling"). */
function insertAfter(items: MenuItem[], afterId: string, node: MenuItem): MenuItem[] {
  const idx = items.findIndex((n) => n.id === afterId);
  if (idx >= 0) {
    const next = items.slice();
    next.splice(idx + 1, 0, node);
    return next;
  }
  return items.map((n) =>
    n.children && n.children.length
      ? { ...n, children: insertAfter(n.children, afterId, node) }
      : n,
  );
}

/** Move the node with `id` up (-1) or down (+1) among its OWN siblings. */
function moveSibling(items: MenuItem[], id: string, dir: -1 | 1): MenuItem[] {
  const idx = items.findIndex((n) => n.id === id);
  if (idx >= 0) {
    const target = idx + dir;
    if (target < 0 || target >= items.length) return items;
    const next = items.slice();
    const [it] = next.splice(idx, 1);
    next.splice(target, 0, it);
    return next;
  }
  return items.map((n) =>
    n.children && n.children.length ? { ...n, children: moveSibling(n.children, id, dir) } : n,
  );
}

/** Find a node by id (depth-first). */
function findNode(items: MenuItem[], id: string): MenuItem | null {
  for (const n of items) {
    if (n.id === id) return n;
    if (n.children && n.children.length) {
      const f = findNode(n.children, id);
      if (f) return f;
    }
  }
  return null;
}

/** Count items at any depth. */
function countNodes(items: MenuItem[] | null | undefined): number {
  let c = 0;
  for (const n of items ?? []) {
    c += 1 + countNodes(n.children);
  }
  return c;
}

// ===========================================================================
// Page
// ===========================================================================
export default function MenusPage() {
  const toast = useToast();
  const { hasRole } = useAuth();
  // Publishing the live nav is integration-critical -> SUPERADMIN / ADMIN only
  // (matches the backend push router gate; backend is the real enforcement).
  const canPublish = hasRole(['SUPERADMIN', 'ADMIN']);
  const bannerRef = useRef<OnlineStoreSyncBannerHandle>(null);
  const [publishing, setPublishing] = useState(false);

  const [menus, setMenus] = useState<EcomMenu[]>([]);
  const [loadingMenus, setLoadingMenus] = useState(true);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [menuMeta, setMenuMeta] = useState<EcomMenu | null>(null);
  const [tree, setTree] = useState<MenuItem[]>([]);
  const [loadingTree, setLoadingTree] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  // Which subtrees are expanded (by node id). Default: everything expanded.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  // Item editor drawer.
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorItemId, setEditorItemId] = useState<string | null>(null);

  // New-menu inline form.
  const [creatingMenu, setCreatingMenu] = useState(false);
  const [newMenuTitle, setNewMenuTitle] = useState('');
  const [newMenuHandle, setNewMenuHandle] = useState('');

  const loadMenus = useCallback(async () => {
    setLoadingMenus(true);
    try {
      const rows = await menusApi.list();
      setMenus(rows);
      // Auto-select the first menu (prefer the default) if none selected.
      setSelectedId((cur) => {
        if (cur && rows.some((m) => m.id === cur)) return cur;
        const def = rows.find((m) => m.is_default) ?? rows[0];
        return def ? def.id : null;
      });
    } finally {
      setLoadingMenus(false);
    }
  }, []);

  useEffect(() => {
    loadMenus();
  }, [loadMenus]);

  // Load the selected menu's full tree whenever the selection changes.
  const loadTree = useCallback(async (id: string) => {
    setLoadingTree(true);
    try {
      const full = await menusApi.get(id);
      setMenuMeta(full);
      setTree(normaliseTree(full?.items ?? []));
      setCollapsed(new Set());
      setDirty(false);
    } finally {
      setLoadingTree(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId) {
      loadTree(selectedId);
    } else {
      setMenuMeta(null);
      setTree([]);
      setDirty(false);
    }
  }, [selectedId, loadTree]);

  const selectMenu = (id: string) => {
    if (dirty && !window.confirm('Discard unsaved changes to this menu?')) return;
    setSelectedId(id);
  };

  // ---- Menu-level ops ------------------------------------------------------

  const createMenu = async () => {
    const title = newMenuTitle.trim();
    if (!title) {
      toast.error('Menu title is required');
      return;
    }
    const handle = (newMenuHandle || slugify(title)).trim();
    setCreatingMenu(true);
    try {
      const created = await menusApi.create({ title, handle, active: true });
      toast.success('Menu created');
      setNewMenuTitle('');
      setNewMenuHandle('');
      await loadMenus();
      setSelectedId(created.id);
    } catch (e: any) {
      toast.error(
        e?.message || 'Could not create menu — is the Online Store backend deployed?',
      );
    } finally {
      setCreatingMenu(false);
    }
  };

  const toggleActive = async (m: EcomMenu) => {
    const next = !(m.active ?? true);
    setMenus((prev) => prev.map((x) => (x.id === m.id ? { ...x, active: next } : x)));
    try {
      await menusApi.setActive(m.id, next);
      toast.success(next ? 'Menu enabled' : 'Menu hidden');
    } catch (e: any) {
      setMenus((prev) => prev.map((x) => (x.id === m.id ? { ...x, active: !next } : x)));
      toast.error(e?.message || 'Could not update menu');
    }
  };

  // Publish (push) a menu to Shopify. DARK by default -> a SIMULATED dry-run; the
  // returned mode (SIMULATED vs LIVE) is surfaced in the toast so a dry-run is
  // never mistaken for a live write. We block a publish while the tree has
  // unsaved edits (you'd push the last-saved tree, not what's on screen). On a
  // LIVE push refresh the menu + banner so the Synced chip + counts update.
  const publishMenu = async (m: EcomMenu) => {
    if (m.id === selectedId && dirty) {
      toast.warning('Save your menu changes before publishing.');
      return;
    }
    setPublishing(true);
    try {
      const result = await pushApi.pushMenu(m.id);
      if (result.ok) {
        toast.success(formatPushResult(`Menu "${m.title || m.handle}"`, result));
      } else {
        toast.warning(formatPushResult(`Menu "${m.title || m.handle}"`, result));
      }
      if (result.ok && result.mode === 'LIVE') {
        await loadMenus();
        if (m.id === selectedId) await loadTree(m.id);
        bannerRef.current?.refresh();
      }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || 'Could not publish menu');
    } finally {
      setPublishing(false);
    }
  };

  const deleteMenu = async (m: EcomMenu) => {
    if (!window.confirm(`Delete menu "${m.title}" and all its items? This cannot be undone.`))
      return;
    try {
      await menusApi.remove(m.id);
      toast.success('Menu deleted');
      if (selectedId === m.id) setSelectedId(null);
      await loadMenus();
    } catch (e: any) {
      toast.error(e?.message || 'Could not delete menu');
    }
  };

  // ---- Tree (item) ops — operate on the LOCAL tree, mark dirty -------------

  const blankItem = (): MenuItem => ({
    id: tmpId(),
    title: 'New item',
    item_type: 'HTTP',
    url: '',
    children: [],
  });

  const addTopLevel = () => {
    const node = blankItem();
    setTree((t) => resequence([...t, node]));
    setDirty(true);
    openEditor(node.id);
  };

  const addChild = (parentId: string) => {
    const node = blankItem();
    setTree((t) => resequence(insertChild(t, parentId, node)));
    // ensure parent is expanded so the new child is visible
    setCollapsed((c) => {
      const next = new Set(c);
      next.delete(parentId);
      return next;
    });
    setDirty(true);
    openEditor(node.id);
  };

  const addSibling = (afterId: string) => {
    const node = blankItem();
    setTree((t) => resequence(insertAfter(t, afterId, node)));
    setDirty(true);
    openEditor(node.id);
  };

  const deleteItem = (id: string) => {
    const node = findNode(tree, id);
    const childCount = countNodes(node?.children);
    const msg = childCount
      ? `Delete "${node?.title || 'item'}" and its ${childCount} sub-item${childCount === 1 ? '' : 's'}?`
      : `Delete "${node?.title || 'item'}"?`;
    if (!window.confirm(msg)) return;
    setTree((t) => resequence(removeNode(t, id)));
    setDirty(true);
    if (editorItemId === id) closeEditor();
  };

  const move = (id: string, dir: -1 | 1) => {
    setTree((t) => resequence(moveSibling(t, id, dir)));
    setDirty(true);
  };

  const patchItem = (id: string, patch: Partial<MenuItem>) => {
    setTree((t) => updateNode(t, id, (n) => ({ ...n, ...patch })));
    setDirty(true);
  };

  const toggleCollapse = (id: string) =>
    setCollapsed((c) => {
      const next = new Set(c);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const openEditor = (id: string) => {
    setEditorItemId(id);
    setEditorOpen(true);
  };
  const closeEditor = () => {
    setEditorOpen(false);
    setEditorItemId(null);
  };

  // ---- Save the whole tree -------------------------------------------------

  const saveTree = async () => {
    if (!selectedId) return;
    setSaving(true);
    try {
      // Strip temp ids so the backend mints stable ones; keep real ids.
      const clean = (nodes: MenuItem[]): MenuItem[] =>
        resequence(nodes).map((n) => ({
          ...n,
          id: n.id.startsWith('tmp_') ? '' : n.id,
          children: n.children && n.children.length ? clean(n.children) : [],
        }));
      const saved = await menusApi.saveTree(selectedId, clean(tree));
      toast.success('Menu saved');
      setMenuMeta(saved);
      setTree(normaliseTree(saved.items ?? tree));
      setDirty(false);
      // refresh the list (item counts may have changed)
      loadMenus();
    } catch (e: any) {
      toast.error(
        e?.message || 'Could not save the menu — is the Online Store backend deployed?',
      );
    } finally {
      setSaving(false);
    }
  };

  const editorItem = editorItemId ? findNode(tree, editorItemId) : null;
  const totalItems = useMemo(() => countNodes(tree), [tree]);

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
            <span className="text-gray-700">Mega-menu</span>
          </div>
          <h1 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
            <MenuIcon className="w-5 h-5" /> Mega-menu editor
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={loadMenus}
            className="btn-outline inline-flex items-center gap-1.5 text-sm"
            title="Reload menus"
          >
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
          <button
            type="button"
            onClick={saveTree}
            disabled={!selectedId || saving || !dirty}
            className="btn-primary inline-flex items-center gap-1.5 text-sm"
            title={dirty ? 'Save changes to this menu' : 'No unsaved changes'}
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Save changes
          </button>
        </div>
      </div>
      <p className="text-sm text-gray-500 mb-4 max-w-3xl">
        Build the storefront navigation tree — nested items with thumbnails, badges and pin-to-top.
        Edits are saved inside IMS. Pushing the menu live to the storefront is a later, owner-approved
        step, so nothing here changes the live site yet.
      </p>

      {/* Shopify publish (DARK / LIVE) banner */}
      <OnlineStoreSyncBanner ref={bannerRef} className="mb-4" />

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        {/* ---- Menus list (left) ---- */}
        <aside className="rounded-xl border border-gray-200 bg-white overflow-hidden h-fit">
          <div className="px-3 py-2 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
            <span className="text-xs font-medium text-gray-600">Menus</span>
            <button
              type="button"
              onClick={() => setCreatingMenu((v) => !v)}
              className="p-1 rounded hover:bg-gray-200 text-gray-500"
              title="New menu"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>

          {creatingMenu && (
            <div className="p-3 border-b border-gray-100 space-y-2 bg-gray-50/50">
              <input
                type="text"
                value={newMenuTitle}
                onChange={(e) => {
                  setNewMenuTitle(e.target.value);
                  setNewMenuHandle((h) => (h ? h : slugify(e.target.value)));
                }}
                placeholder="Menu title (e.g. Main menu)"
                className="input-field w-full text-sm"
              />
              <input
                type="text"
                value={newMenuHandle}
                onChange={(e) => setNewMenuHandle(slugify(e.target.value))}
                placeholder="handle (e.g. main-menu)"
                className="input-field w-full text-xs font-mono"
              />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={createMenu}
                  disabled={creatingMenu && !newMenuTitle.trim()}
                  className="btn-primary text-xs inline-flex items-center gap-1 flex-1 justify-center"
                >
                  <Plus className="w-3.5 h-3.5" /> Create
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setCreatingMenu(false);
                    setNewMenuTitle('');
                    setNewMenuHandle('');
                  }}
                  className="btn-outline text-xs"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {loadingMenus ? (
            <div className="flex items-center gap-2 text-sm text-gray-500 p-4">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading menus…
            </div>
          ) : menus.length === 0 ? (
            <div className="p-4 text-center">
              <MenuIcon className="w-7 h-7 text-gray-300 mx-auto mb-2" />
              <p className="text-xs font-medium text-gray-700">No menus yet.</p>
              <p className="text-[11px] text-gray-500 mt-1">
                Create one with the + above. If you expected existing menus, the Online Store backend
                may not be deployed yet — this screen is fail-soft and fills in once it is.
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-gray-100">
              {menus.map((m) => {
                const active = m.active ?? true;
                const isSel = m.id === selectedId;
                const count =
                  isSel ? totalItems : typeof m.items_count === 'number' ? m.items_count : countNodes(m.items);
                return (
                  <li
                    key={m.id}
                    className={
                      'px-3 py-2.5 cursor-pointer ' +
                      (isSel ? 'bg-bv-red-50/60 border-l-2 border-bv-red-500' : 'hover:bg-gray-50 border-l-2 border-transparent')
                    }
                    onClick={() => selectMenu(m.id)}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-gray-900 truncate flex items-center gap-1.5">
                          {m.title || '(untitled)'}
                          {m.is_default ? (
                            <span className="inline-flex items-center rounded bg-gray-100 text-gray-500 border border-gray-200 px-1 py-0 text-[10px]">
                              default
                            </span>
                          ) : null}
                        </div>
                        <div className="text-[11px] text-gray-400 font-mono truncate">/{m.handle}</div>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          type="button"
                          role="switch"
                          aria-checked={active ? "true" : "false"}
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleActive(m);
                          }}
                          className={
                            'relative inline-flex h-4 w-7 items-center rounded-full transition-colors ' +
                            (active ? 'bg-green-500' : 'bg-gray-300')
                          }
                          title={active ? 'Enabled — click to hide' : 'Hidden — click to enable'}
                        >
                          <span
                            className={
                              'inline-block h-3 w-3 transform rounded-full bg-white transition-transform ' +
                              (active ? 'translate-x-3.5' : 'translate-x-0.5')
                            }
                          />
                        </button>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteMenu(m);
                          }}
                          className="p-1 rounded hover:bg-red-50 text-gray-300 hover:text-red-600"
                          title="Delete menu"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                    <div className="text-[11px] text-gray-400 mt-0.5">
                      {count} item{count === 1 ? '' : 's'}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        {/* ---- Tree editor (right) ---- */}
        <section className="rounded-xl border border-gray-200 bg-white overflow-hidden min-h-[320px] flex flex-col">
          {!selectedId ? (
            <div className="flex-1 flex flex-col items-center justify-center p-8 text-center">
              <MenuIcon className="w-8 h-8 text-gray-300 mb-2" />
              <p className="text-sm font-medium text-gray-700">Select a menu to edit its tree.</p>
              <p className="text-xs text-gray-500 mt-1 max-w-sm">
                Pick a menu on the left, or create one. Then add nested items, set badges and
                thumbnails, and pin top categories.
              </p>
            </div>
          ) : (
            <>
              {/* tree toolbar */}
              <div className="px-4 py-2.5 border-b border-gray-200 bg-gray-50 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-sm font-semibold text-gray-900 truncate">
                    {menuMeta?.title || 'Menu'}
                  </span>
                  <span className="text-xs text-gray-400 font-mono truncate">/{menuMeta?.handle}</span>
                  {dirty && (
                    <span className="inline-flex items-center rounded-full bg-amber-100 text-amber-800 border border-amber-200 px-2 py-0.5 text-[11px] font-medium">
                      Unsaved
                    </span>
                  )}
                  <SyncChip
                    synced={!!menuMeta?.shopify_menu_id}
                    pending={!!menuMeta?.locally_modified}
                  />
                </div>
                <div className="flex items-center gap-2">
                  {canPublish && menuMeta && (
                    <button
                      type="button"
                      onClick={() => publishMenu(menuMeta)}
                      disabled={publishing || dirty}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                      title={
                        dirty
                          ? 'Save changes before publishing'
                          : 'Push this menu to Shopify (dry-run unless live writes are armed)'
                      }
                    >
                      {publishing ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <UploadCloud className="w-3.5 h-3.5" />
                      )}
                      Publish
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={addTopLevel}
                    className="btn-outline inline-flex items-center gap-1.5 text-xs"
                    title="Add a top-level menu item"
                  >
                    <Plus className="w-3.5 h-3.5" /> Add item
                  </button>
                </div>
              </div>

              {/* tree body */}
              <div className="flex-1 overflow-y-auto p-3">
                {loadingTree ? (
                  <div className="flex items-center gap-2 text-sm text-gray-500 p-4">
                    <Loader2 className="w-4 h-4 animate-spin" /> Loading menu…
                  </div>
                ) : tree.length === 0 ? (
                  <div className="p-6 text-center">
                    <CornerDownRight className="w-7 h-7 text-gray-300 mx-auto mb-2" />
                    <p className="text-sm font-medium text-gray-700">This menu has no items yet.</p>
                    <button
                      type="button"
                      onClick={addTopLevel}
                      className="btn-primary inline-flex items-center gap-1.5 text-sm mt-3"
                    >
                      <Plus className="w-4 h-4" /> Add first item
                    </button>
                  </div>
                ) : (
                  <ul className="space-y-1">
                    {tree.map((node, idx) => (
                      <TreeRow
                        key={node.id}
                        node={node}
                        depth={0}
                        index={idx}
                        siblingCount={tree.length}
                        collapsed={collapsed}
                        selectedItemId={editorItemId}
                        onToggleCollapse={toggleCollapse}
                        onEdit={openEditor}
                        onAddChild={addChild}
                        onAddSibling={addSibling}
                        onDelete={deleteItem}
                        onMove={move}
                      />
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}
        </section>
      </div>

      <p className="mt-4 text-xs text-gray-400">
        Online Store · Mega-menu · Phase 3 (stored in IMS; storefront push is a later phase).
      </p>

      {/* Item editor drawer */}
      {editorOpen && editorItem && (
        <ItemEditorDrawer
          key={editorItem.id}
          item={editorItem}
          onClose={closeEditor}
          onChange={(patch) => patchItem(editorItem.id, patch)}
        />
      )}
    </div>
  );
}

// ===========================================================================
// One tree row (recursive). Renders indent, expand/collapse, the item summary,
// and per-node action buttons. Children render recursively below.
// ===========================================================================
function TreeRow({
  node,
  depth,
  index,
  siblingCount,
  collapsed,
  selectedItemId,
  onToggleCollapse,
  onEdit,
  onAddChild,
  onAddSibling,
  onDelete,
  onMove,
}: {
  node: MenuItem;
  depth: number;
  index: number;
  siblingCount: number;
  collapsed: Set<string>;
  selectedItemId: string | null;
  onToggleCollapse: (id: string) => void;
  onEdit: (id: string) => void;
  onAddChild: (parentId: string) => void;
  onAddSibling: (afterId: string) => void;
  onDelete: (id: string) => void;
  onMove: (id: string, dir: -1 | 1) => void;
}) {
  const hasChildren = !!(node.children && node.children.length);
  const isCollapsed = collapsed.has(node.id);
  const isSelected = selectedItemId === node.id;
  const typeLabel = ITEM_TYPES.find((t) => t.value === node.item_type)?.label || node.item_type;

  return (
    <li>
      <div
        className={
          'group flex items-center gap-1.5 rounded-lg border px-2 py-1.5 ' +
          (isSelected ? 'border-bv-red-300 bg-bv-red-50/50' : 'border-gray-200 bg-white hover:bg-gray-50')
        }
        style={{ marginLeft: depth * 20 }}
      >
        {/* expand/collapse */}
        {hasChildren ? (
          <button
            type="button"
            onClick={() => onToggleCollapse(node.id)}
            className="p-0.5 rounded hover:bg-gray-200 text-gray-500 shrink-0"
            title={isCollapsed ? 'Expand' : 'Collapse'}
          >
            {isCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        ) : (
          <span className="w-5 shrink-0" />
        )}

        {/* icon thumbnail (mega-menu) */}
        {node.icon_url ? (

          <img
            src={node.icon_url}
            alt=""
            className="w-6 h-6 rounded object-cover border border-gray-200 shrink-0"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = 'none';
            }}
          />
        ) : null}

        {/* title + meta */}
        <button
          type="button"
          onClick={() => onEdit(node.id)}
          className="flex-1 min-w-0 text-left"
          title="Edit this item"
        >
          <span className="text-sm font-medium text-gray-900 truncate inline-flex items-center gap-1.5">
            {node.title || '(untitled)'}
            {node.pinned_to_top ? (
              <Pin className="w-3 h-3 text-bv-red-500" />
            ) : null}
            {node.badge_text ? (
              <span
                className="inline-flex items-center rounded-full px-1.5 py-0 text-[10px] font-semibold text-white"
                style={{ backgroundColor: node.badge_color || '#475569' }}
              >
                {node.badge_text}
              </span>
            ) : null}
          </span>
          <span className="block text-[11px] text-gray-400 truncate">
            {typeLabel}
            {node.resource_id ? ` · ${node.resource_id}` : node.url ? ` · ${node.url}` : ''}
            {node.tags_filter ? ` · tags: ${node.tags_filter}` : ''}
          </span>
        </button>

        {/* actions */}
        <div className="flex items-center gap-0.5 shrink-0 opacity-70 group-hover:opacity-100">
          <button
            type="button"
            onClick={() => onMove(node.id, -1)}
            disabled={index === 0}
            className="p-1 rounded hover:bg-gray-100 text-gray-400 disabled:opacity-30"
            title="Move up"
          >
            <ArrowUp className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onMove(node.id, 1)}
            disabled={index === siblingCount - 1}
            className="p-1 rounded hover:bg-gray-100 text-gray-400 disabled:opacity-30"
            title="Move down"
          >
            <ArrowDown className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onAddChild(node.id)}
            className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-bv-red-600"
            title="Add a sub-item under this"
          >
            <CornerDownRight className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onAddSibling(node.id)}
            className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-bv-red-600"
            title="Add an item after this (same level)"
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onEdit(node.id)}
            className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-700"
            title="Edit"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onDelete(node.id)}
            className="p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-600"
            title="Delete"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* children */}
      {hasChildren && !isCollapsed && (
        <ul className="mt-1 space-y-1">
          {node.children!.map((child, i) => (
            <TreeRow
              key={child.id}
              node={child}
              depth={depth + 1}
              index={i}
              siblingCount={node.children!.length}
              collapsed={collapsed}
              selectedItemId={selectedItemId}
              onToggleCollapse={onToggleCollapse}
              onEdit={onEdit}
              onAddChild={onAddChild}
              onAddSibling={onAddSibling}
              onDelete={onDelete}
              onMove={onMove}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

// ===========================================================================
// Item editor drawer (slide-over). Edits the selected item's fields, incl. the
// mega-menu presentation fields. Changes flow up via onChange and are applied
// to the local tree immediately (the drawer is a controlled view of the node).
// ===========================================================================
function ItemEditorDrawer({
  item,
  onClose,
  onChange,
}: {
  item: MenuItem;
  onClose: () => void;
  onChange: (patch: Partial<MenuItem>) => void;
}) {
  const isResource = RESOURCE_TYPES.has(item.item_type);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* backdrop */}
      <div className="absolute inset-0 bg-black bg-opacity-40" onClick={onClose} aria-hidden="true" />
      {/* panel */}
      <div className="relative h-full w-full max-w-md bg-white shadow-xl flex flex-col">
        {/* header */}
        <div className="sticky top-0 z-10 flex items-center justify-between p-4 border-b border-gray-200 bg-white">
          <div className="flex items-center gap-2 min-w-0">
            <Pencil className="w-5 h-5 text-bv-red-600 shrink-0" />
            <div className="min-w-0">
              <h2 className="text-base font-semibold text-gray-900 truncate">Edit menu item</h2>
              <p className="text-xs text-gray-500 truncate">{item.title || '(untitled)'}</p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="p-2 hover:bg-gray-100 rounded-lg" title="Close" aria-label="Close">
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {/* Basics */}
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Title *</label>
              <input
                type="text"
                value={item.title}
                onChange={(e) => onChange({ title: e.target.value })}
                placeholder="e.g. Sunglasses"
                className="input-field w-full"
              />
            </div>

            <div>
              <label htmlFor="item-type-select" className="block text-sm font-medium text-gray-700 mb-1">Links to</label>
              <select
                id="item-type-select"
                value={item.item_type}
                onChange={(e) => onChange({ item_type: e.target.value as MenuItemType })}
                className="input-field w-full"
                title="Link type select"
              >
                {ITEM_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>

            {isResource ? (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1 inline-flex items-center gap-1.5">
                  <Link2 className="w-3.5 h-3.5" /> Resource ID
                </label>
                <input
                  type="text"
                  value={item.resource_id ?? ''}
                  onChange={(e) => onChange({ resource_id: e.target.value })}
                  placeholder="gid://shopify/Collection/123"
                  className="input-field w-full font-mono text-xs"
                />
                <p className="text-xs text-gray-400 mt-1">
                  The Shopify resource this item points at (collection, page, product …).
                </p>
              </div>
            ) : (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1 inline-flex items-center gap-1.5">
                  <Link2 className="w-3.5 h-3.5" /> URL
                </label>
                <input
                  type="text"
                  value={item.url ?? ''}
                  onChange={(e) => onChange({ url: e.target.value })}
                  placeholder="https://… or /pages/about"
                  className="input-field w-full"
                />
              </div>
            )}

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1 inline-flex items-center gap-1.5">
                <Tag className="w-3.5 h-3.5" /> Tags filter
              </label>
              <input
                type="text"
                value={item.tags_filter ?? ''}
                onChange={(e) => onChange({ tags_filter: e.target.value })}
                placeholder="comma,separated,tags"
                className="input-field w-full"
              />
              <p className="text-xs text-gray-400 mt-1">
                Optional — show only products matching these tags within the linked collection.
              </p>
            </div>
          </div>

          {/* Mega-menu presentation */}
          <details className="rounded-lg border border-gray-200" open>
            <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-gray-700 inline-flex items-center gap-1.5">
              <ImageIcon className="w-4 h-4" /> Mega-menu look
            </summary>
            <div className="p-3 pt-0 space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Thumbnail (icon) URL</label>
                <input
                  type="text"
                  value={item.icon_url ?? ''}
                  onChange={(e) => onChange({ icon_url: e.target.value })}
                  placeholder="https://… small image shown next to the item"
                  className="input-field w-full text-xs"
                />
                {item.icon_url ? (
                  <div className="mt-2">

                    <img
                      src={item.icon_url}
                      alt="Thumbnail preview"
                      className="h-12 w-12 object-cover rounded border border-gray-200"
                      onError={(e) => {
                        (e.currentTarget as HTMLImageElement).style.display = 'none';
                      }}
                    />
                  </div>
                ) : null}
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Banner URL</label>
                <input
                  type="text"
                  value={item.banner_url ?? ''}
                  onChange={(e) => onChange({ banner_url: e.target.value })}
                  placeholder="https://… wide banner for the mega-menu card"
                  className="input-field w-full text-xs"
                />
                {item.banner_url ? (
                  <div className="mt-2">

                    <img
                      src={item.banner_url}
                      alt="Banner preview"
                      className="h-16 w-full object-cover rounded border border-gray-200"
                      onError={(e) => {
                        (e.currentTarget as HTMLImageElement).style.display = 'none';
                      }}
                    />
                  </div>
                ) : null}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Badge text</label>
                  <input
                    type="text"
                    value={item.badge_text ?? ''}
                    onChange={(e) => onChange({ badge_text: e.target.value })}
                    maxLength={16}
                    placeholder="NEW / SALE"
                    className="input-field w-full text-xs"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Badge color</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={item.badge_color || '#475569'}
                      onChange={(e) => onChange({ badge_color: e.target.value })}
                      className="h-9 w-10 rounded border border-gray-200 p-0.5 cursor-pointer"
                      title="Pick a badge color"
                    />
                    <input
                      type="text"
                      value={item.badge_color ?? ''}
                      onChange={(e) => onChange({ badge_color: e.target.value })}
                      placeholder="#hex"
                      className="input-field flex-1 text-xs font-mono"
                    />
                  </div>
                </div>
              </div>

              {/* badge presets */}
              <div className="flex flex-wrap gap-1.5">
                {BADGE_PRESETS.map((p) => (
                  <button
                    key={p.color}
                    type="button"
                    onClick={() => onChange({ badge_color: p.color })}
                    className="inline-flex items-center gap-1 rounded-full border border-gray-200 px-2 py-0.5 text-[11px] hover:bg-gray-50"
                    title={`Use ${p.label}`}
                  >
                    <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: p.color }} />
                    {p.label}
                  </button>
                ))}
              </div>

              {/* badge preview */}
              {item.badge_text ? (
                <div className="text-xs text-gray-500 flex items-center gap-2">
                  Preview:
                  <span
                    className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold text-white"
                    style={{ backgroundColor: item.badge_color || '#475569' }}
                  >
                    {item.badge_text}
                  </span>
                </div>
              ) : null}

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={!!item.pinned_to_top}
                  onChange={(e) => onChange({ pinned_to_top: e.target.checked })}
                  className="w-4 h-4 rounded"
                />
                <span className="text-sm text-gray-700 inline-flex items-center gap-1.5">
                  <Pin className="w-3.5 h-3.5" /> Pin to top
                </span>
              </label>
              <p className="text-[11px] text-gray-400 -mt-2">
                Pinned items show first (top categories up top, niche ones in the submenu).
              </p>
            </div>
          </details>

          <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs text-gray-600 flex items-start gap-1.5">
            <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>
              Changes apply to the tree as you type. Use <strong>Save changes</strong> at the top to
              store the whole menu in IMS. Nothing is pushed to the live storefront yet.
            </span>
          </div>
        </div>

        {/* footer */}
        <div className="sticky bottom-0 flex items-center justify-end p-4 border-t border-gray-200 bg-gray-50">
          <button type="button" onClick={onClose} className="btn-primary inline-flex items-center gap-2">
            <Save className="w-4 h-4" /> Done
          </button>
        </div>
      </div>
    </div>
  );
}
