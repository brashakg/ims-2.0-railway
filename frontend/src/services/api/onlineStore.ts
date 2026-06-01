// ============================================================================
// IMS 2.0 - Online Store (e-commerce / BVI merge) module summary
// ============================================================================
// Phase 1 foundation: a single read for the Online Store module shell. Returns
// module status + per-section counts so the shell can show "what's live yet".
//
// GRACEFUL DEGRADATION: the backend GET /api/v1/online-store/summary endpoint is
// rolled out separately and may not exist in a stale deploy. A 404 (or any
// error) resolves to a "not yet available" placeholder rather than throwing, so
// the shell always renders. Import directly (not via the api barrel).

import api from './client';

/** Per-section count surfaced on the shell cards. All optional + nullable so a
 *  partial backend payload never breaks rendering. */
export interface OnlineStoreCounts {
  products?: number | null;
  variants?: number | null;
  collections?: number | null;
  menus?: number | null;
  images_pending_design?: number | null;
  customers?: number | null;
  orders?: number | null;
}

export interface OnlineStoreSummary {
  /** Whether the backend module endpoint answered at all. false => placeholder. */
  available: boolean;
  /** High-level module phase/status string from the backend (e.g. "FOUNDATION"). */
  status?: string | null;
  /** Whether IMS is the live Shopify writer yet (kill-switch). Default false. */
  shopify_writes_enabled?: boolean | null;
  counts?: OnlineStoreCounts | null;
  /** Optional human note from the backend (e.g. "shadow sync only"). */
  message?: string | null;
}

const PLACEHOLDER: OnlineStoreSummary = {
  available: false,
  status: 'COMING_SOON',
  shopify_writes_enabled: false,
  counts: {},
  message: null,
};

export const onlineStoreApi = {
  /** Fetch the module summary. Never throws: any error (incl. a 404 on a stale
   *  deploy) resolves to the COMING_SOON placeholder so the shell still renders. */
  getSummary: async (): Promise<OnlineStoreSummary> => {
    try {
      const res = await api.get('/online-store/summary');
      const data = (res?.data ?? {}) as Partial<OnlineStoreSummary>;
      return {
        available: true,
        status: data.status ?? 'FOUNDATION',
        shopify_writes_enabled: data.shopify_writes_enabled ?? false,
        counts: data.counts ?? {},
        message: data.message ?? null,
      };
    } catch {
      return PLACEHOLDER;
    }
  },
};

// ============================================================================
// COLLECTIONS sub-api  (BVI Phase 2 — Collections module, "push-dark")
// ----------------------------------------------------------------------------
// CRUD + manual-product membership + smart-rule resolution against the IMS
// Mongo `ecom_collections` + `collection_products` collections, served by
// /api/v1/online-store/collections (the Phase-2 backend router). NO Shopify
// network writes happen here — collections are stored + edited inside IMS only;
// the single-writer Shopify push is Phase 5/6 (BVI_MERGE_PLAN.md section B).
//
// Field set mirrors the BVI Prisma `Collection` model
// (ecommerce/prisma/schema.prisma) mapped to snake_case for the IMS backend.
//
// GRACEFUL DEGRADATION: the Phase-2 backend may not be deployed yet. Every read
// resolves to a safe empty value rather than throwing, so the Collections screen
// always renders ("backend not yet available" rather than a crash). Writes
// surface a thrown error so the screen can toast it. Import this service
// DIRECTLY from this module (not the api barrel — the barrel re-export fails to
// resolve, TS2614, per past sessions).
// ============================================================================

/** A custom (manual SKU list) or smart (rule-based) collection. */
export type CollectionType = 'CUSTOM' | 'SMART';

/** One clause of a smart-collection rule set. `field`/`op`/`value` mirror
 *  Shopify's smart-collection rule shape (column/relation/condition). */
export interface SmartRule {
  field: string; // e.g. 'brand', 'category', 'tag', 'product_type', 'title', 'price'
  op: string;    // e.g. 'equals', 'not_equals', 'contains', 'starts_with', 'greater_than', 'less_than'
  value: string;
}

/** Smart-collection rule set: a list of clauses combined with AND (all) or
 *  OR (any). `disjunctive=true` => any rule matches (OR). */
export interface SmartRules {
  disjunctive: boolean;
  rules: SmartRule[];
}

export interface EcomCollection {
  id: string;
  title: string;
  handle?: string | null;
  description?: string | null;
  collection_type: CollectionType;
  // SEO + storefront presentation
  seo_title?: string | null;
  seo_description?: string | null;
  banner_image?: string | null;
  short_description?: string | null;
  // lower = sorted first in nav + bucket lists (BVI sortPriority, default 100)
  sort_priority?: number | null;
  published?: boolean | null;
  // count of member products (manual array length, or last smart-resolve count)
  products_count?: number | null;
  // SMART only: the stored rule set
  smart_rules?: SmartRules | null;
  // auto-collection lineage (BVI autoSource / categoryAnchor). Read-only in the
  // editor; set by the auto-generation pipeline (Phase 2b, not built here).
  auto_source?: string | null;
  category_anchor?: string | null;
  // dirty flag -> Phase-5 push queue. Informational in Phase 2.
  locally_modified?: boolean | null;
  shopify_collection_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** A product that belongs to (or matches) a collection — the shape returned by
 *  the manual-membership list and the smart-rule resolver preview. */
export interface CollectionProduct {
  product_id: string;
  sku?: string | null;
  title?: string | null;
  brand?: string | null;
  category?: string | null;
  image?: string | null;
  position?: number | null;
}

/** Create/update payload. All optional on update; the backend merges. */
export interface CollectionUpsert {
  title?: string;
  handle?: string;
  description?: string;
  collection_type?: CollectionType;
  seo_title?: string;
  seo_description?: string;
  banner_image?: string;
  short_description?: string;
  sort_priority?: number;
  published?: boolean;
  smart_rules?: SmartRules;
}

/** Slim catalog row for the manual product picker (search by sku/brand/title). */
export interface CatalogPick {
  product_id: string;
  sku?: string | null;
  title?: string | null;
  brand?: string | null;
  category?: string | null;
  image?: string | null;
}

const COLLECTIONS_BASE = '/online-store/collections';

/** Whether an axios error is a "backend not deployed yet" signal (404/501) —
 *  reads degrade silently on these so the screen renders pre-Phase-2-backend. */
function _isUnavailable(err: unknown): boolean {
  const status = (err as { response?: { status?: number } } | undefined)?.response?.status;
  return status === 404 || status === 501;
}

export const collectionsApi = {
  /** List all collections. Fail-soft: any error -> [] so the list renders. */
  list: async (): Promise<EcomCollection[]> => {
    try {
      const res = await api.get(COLLECTIONS_BASE);
      const data = res?.data;
      // Accept either a bare array or an envelope {collections:[...]} / {items:[...]}.
      const arr = Array.isArray(data)
        ? data
        : (data?.collections ?? data?.items ?? []);
      return (Array.isArray(arr) ? arr : []) as EcomCollection[];
    } catch {
      return [];
    }
  },

  /** Fetch one collection (with its smart_rules). Fail-soft -> null. */
  get: async (id: string): Promise<EcomCollection | null> => {
    try {
      const res = await api.get(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}`);
      return (res?.data ?? null) as EcomCollection | null;
    } catch {
      return null;
    }
  },

  /** Create a collection. Throws on failure (caller toasts the message). */
  create: async (payload: CollectionUpsert): Promise<EcomCollection> => {
    const res = await api.post(COLLECTIONS_BASE, payload);
    return res.data as EcomCollection;
  },

  /** Update a collection (partial merge). Throws on failure. */
  update: async (id: string, payload: CollectionUpsert): Promise<EcomCollection> => {
    const res = await api.put(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}`, payload);
    return res.data as EcomCollection;
  },

  /** Toggle/set the published flag. Throws on failure. */
  setPublished: async (id: string, published: boolean): Promise<EcomCollection> => {
    const res = await api.put(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}`, { published });
    return res.data as EcomCollection;
  },

  /** Delete a collection. Throws on failure. */
  remove: async (id: string): Promise<void> => {
    await api.delete(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}`);
  },

  // --- Manual (CUSTOM) membership ------------------------------------------

  /** The ordered manual product members of a CUSTOM collection. Fail-soft -> []. */
  members: async (id: string): Promise<CollectionProduct[]> => {
    try {
      const res = await api.get(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}/products`);
      const data = res?.data;
      const arr = Array.isArray(data) ? data : (data?.products ?? data?.items ?? []);
      return (Array.isArray(arr) ? arr : []) as CollectionProduct[];
    } catch {
      return [];
    }
  },

  /** Add a product to a CUSTOM collection. Throws on failure. */
  addProduct: async (id: string, productId: string): Promise<void> => {
    await api.post(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}/products`, {
      product_id: productId,
    });
  },

  /** Remove a product from a CUSTOM collection. Throws on failure. */
  removeProduct: async (id: string, productId: string): Promise<void> => {
    await api.delete(
      `${COLLECTIONS_BASE}/${encodeURIComponent(id)}/products/${encodeURIComponent(productId)}`,
    );
  },

  /** Persist the manual member order (array of product_ids, first = position 0).
   *  Throws on failure. */
  reorder: async (id: string, productIds: string[]): Promise<void> => {
    await api.put(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}/products/reorder`, {
      product_ids: productIds,
    });
  },

  // --- Smart (SMART) resolution --------------------------------------------

  /** Resolve which products a smart-rule set matches (preview, no save).
   *  Posts the rules so the editor can preview BEFORE persisting them. For an
   *  already-saved SMART collection the backend may also resolve by id.
   *  Fail-soft: returns {products:[], total:0, available:false} when the
   *  Phase-2 backend isn't deployed, so the preview button never crashes. */
  resolvedProducts: async (
    args: { id?: string; rules?: SmartRules; limit?: number },
  ): Promise<{ products: CollectionProduct[]; total: number; available: boolean }> => {
    try {
      const res = await api.post(`${COLLECTIONS_BASE}/resolve`, {
        collection_id: args.id ?? null,
        smart_rules: args.rules ?? null,
        limit: args.limit ?? 50,
      });
      const data = res?.data ?? {};
      const arr = Array.isArray(data) ? data : (data.products ?? data.items ?? []);
      const products = (Array.isArray(arr) ? arr : []) as CollectionProduct[];
      const total = typeof data.total === 'number' ? data.total : products.length;
      return { products, total, available: true };
    } catch (err) {
      // 404/501 = backend not live yet -> degrade to an empty, "unavailable"
      // preview. Any other error also degrades (the editor stays usable).
      void _isUnavailable(err);
      return { products: [], total: 0, available: false };
    }
  },

  // --- Catalog search (for the manual product picker) ----------------------

  /** Search the orderable catalog for the manual picker. Reuses the existing
   *  products list endpoint (search by sku/brand/title) so we don't need a new
   *  backend route. Fail-soft -> []. */
  searchCatalog: async (query: string): Promise<CatalogPick[]> => {
    try {
      const res = await api.get('/products', { params: { search: query } });
      const data = res?.data;
      const arr = Array.isArray(data) ? data : (data?.products ?? data?.items ?? []);
      const rows = (Array.isArray(arr) ? arr : []) as Array<Record<string, any>>;
      return rows.map((p) => ({
        product_id: String(p.product_id ?? p.id ?? p._id ?? ''),
        sku: p.sku ?? null,
        title: p.title ?? p.name ?? p.model ?? null,
        brand: p.brand ?? null,
        category: p.category ?? null,
        image: (Array.isArray(p.images) ? p.images[0] : p.image) ?? null,
      }));
    } catch {
      return [];
    }
  },
};

// ============================================================================
// MENUS sub-api  (BVI Phase 3 — Menus / Mega-Menu editor, "push-dark")
// ----------------------------------------------------------------------------
// FLAGSHIP #2 of the BVI merge: edit the storefront navigation tree (the
// mega-menu) entirely inside IMS. CRUD over the IMS Mongo `ecom_menus`
// collection (items embedded as a tree), served by /api/v1/online-store/menus
// (the Phase-3 backend router). NO Shopify network write happens here — menus
// are stored + edited inside IMS only; the single-writer Shopify `menuUpdate`
// push is Phase 5/6 (BVI_MERGE_PLAN.md section B).
//
// Field set mirrors the BVI Prisma `Menu` + `MenuItem` models
// (ecommerce/prisma/schema.prisma:380-431) mapped to snake_case, incl. the
// round-2 mega-menu fields (icon_url, banner_url, badge_text, badge_color,
// pinned_to_top) and the nested parent/children item tree.
//
// GRACEFUL DEGRADATION: the Phase-3 backend menus router may not be deployed
// yet (it ships separately). Every read resolves to a safe empty value rather
// than throwing, so the Menus screen always renders ("backend not yet
// available" rather than a crash). Writes surface a thrown error so the screen
// can toast it. Import this service DIRECTLY from this module (NOT the api
// barrel — the barrel re-export fails to resolve, TS2614, per past sessions).
// ============================================================================

/** Shopify MenuItemType enum (mirrors BVI MenuItem.itemType — the comment on
 *  schema.prisma:409-411). Drives the item-type picker in the editor. */
export type MenuItemType =
  | 'COLLECTION'
  | 'COLLECTIONS'
  | 'PRODUCT'
  | 'PAGE'
  | 'BLOG'
  | 'ARTICLE'
  | 'FRONTPAGE'
  | 'CATALOG'
  | 'SEARCH'
  | 'HTTP'
  | 'SHOP_POLICY'
  | 'METAOBJECT';

/** One navigation node. Children are embedded (the backend returns the menu as
 *  a tree). All presentation fields are optional + nullable so a partial
 *  backend payload never breaks rendering. */
export interface MenuItem {
  id: string;
  // null / absent = top-level item.
  parent_id?: string | null;
  position?: number | null;
  title: string;
  item_type: MenuItemType;
  // HTTP/external => url; otherwise the resource gid (Collection/Page/...).
  url?: string | null;
  resource_id?: string | null;
  // CSV — "filter this collection by these tags".
  tags_filter?: string | null;
  // --- Mega-menu presentation (BVI round-2 M6/M11) ---
  icon_url?: string | null;     // small thumbnail next to the item
  banner_url?: string | null;   // wider banner for top-level mega-menu cards
  badge_text?: string | null;   // NEW / SALE / META / EXCLUSIVE
  badge_color?: string | null;  // hex; null = theme default
  pinned_to_top?: boolean | null;
  // Shopify-side mapping (informational in Phase 3).
  shopify_item_id?: string | null;
  // The embedded child subtree (ordered by position).
  children?: MenuItem[] | null;
}

/** A storefront menu (main-menu, footer, ...). Items embedded as a tree. */
export interface EcomMenu {
  id: string;
  handle: string;          // unique: main-menu, footer, links, ...
  title: string;
  is_default?: boolean | null;
  // active=false hides the menu from the editor + skips Shopify push without
  // deleting it (BVI Menu.active, schema.prisma:389).
  active?: boolean | null;
  // dirty flag -> Phase-5 push queue. Informational in Phase 3.
  locally_modified?: boolean | null;
  shopify_menu_id?: string | null;
  last_synced_at?: string | null;
  // Present on the GET-one (tree) payload; may be absent in the list payload.
  items?: MenuItem[] | null;
  // count of items (any depth) — surfaced on the list when items aren't.
  items_count?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** Create/update payload for the menu itself (not its items). */
export interface MenuUpsert {
  handle?: string;
  title?: string;
  is_default?: boolean;
  active?: boolean;
}

/** Create/update payload for a single item. All optional on update; the backend
 *  merges. `id` is omitted on create (the backend mints it). */
export interface MenuItemUpsert {
  parent_id?: string | null;
  position?: number;
  title?: string;
  item_type?: MenuItemType;
  url?: string | null;
  resource_id?: string | null;
  tags_filter?: string | null;
  icon_url?: string | null;
  banner_url?: string | null;
  badge_text?: string | null;
  badge_color?: string | null;
  pinned_to_top?: boolean;
}

const MENUS_BASE = '/online-store/menus';

/** Normalise a menu list payload into EcomMenu[] (accepts a bare array or an
 *  envelope {menus:[...]} / {items:[...]}). */
function _menusFrom(data: any): EcomMenu[] {
  const arr = Array.isArray(data) ? data : (data?.menus ?? data?.items ?? []);
  return (Array.isArray(arr) ? arr : []) as EcomMenu[];
}

/** Unwrap a single-menu payload (accepts {menu:{...}} or a bare object). */
function _menuFrom(data: any): EcomMenu | null {
  if (!data) return null;
  const m = data.menu ?? data;
  return (m && typeof m === 'object' ? m : null) as EcomMenu | null;
}

export const menusApi = {
  /** List all menus (without their full item trees). Fail-soft -> [] so the
   *  list renders even before the Phase-3 backend is deployed. */
  list: async (): Promise<EcomMenu[]> => {
    try {
      const res = await api.get(MENUS_BASE);
      return _menusFrom(res?.data);
    } catch {
      return [];
    }
  },

  /** Fetch one menu WITH its embedded item tree. Fail-soft -> null. */
  get: async (id: string): Promise<EcomMenu | null> => {
    try {
      const res = await api.get(`${MENUS_BASE}/${encodeURIComponent(id)}`);
      return _menuFrom(res?.data);
    } catch {
      return null;
    }
  },

  /** Create a menu. Throws on failure (caller toasts the message). */
  create: async (payload: MenuUpsert): Promise<EcomMenu> => {
    const res = await api.post(MENUS_BASE, payload);
    const m = _menuFrom(res?.data);
    if (!m) throw new Error('Create menu returned no menu');
    return m;
  },

  /** Update a menu's own fields (title/handle/active/default). Throws. */
  update: async (id: string, payload: MenuUpsert): Promise<EcomMenu> => {
    const res = await api.put(`${MENUS_BASE}/${encodeURIComponent(id)}`, payload);
    const m = _menuFrom(res?.data);
    if (!m) throw new Error('Update menu returned no menu');
    return m;
  },

  /** Toggle/set the active flag. Throws on failure. */
  setActive: async (id: string, active: boolean): Promise<EcomMenu> => {
    const res = await api.put(`${MENUS_BASE}/${encodeURIComponent(id)}`, { active });
    const m = _menuFrom(res?.data);
    if (!m) throw new Error('Update menu returned no menu');
    return m;
  },

  /** Delete a menu (and its items). Throws on failure. */
  remove: async (id: string): Promise<void> => {
    await api.delete(`${MENUS_BASE}/${encodeURIComponent(id)}`);
  },

  // --- Item operations -------------------------------------------------------
  // These hit /{menu_id}/items[...]. Each returns the FULL updated menu (tree)
  // so the editor can re-render from a single source of truth, mirroring the
  // collections membership endpoints (which return the updated collection).

  /** Add an item under `parent_id` (null/absent = top level). Throws. Returns
   *  the full updated menu tree. */
  addItem: async (menuId: string, payload: MenuItemUpsert): Promise<EcomMenu | null> => {
    const res = await api.post(
      `${MENUS_BASE}/${encodeURIComponent(menuId)}/items`,
      payload,
    );
    return _menuFrom(res?.data);
  },

  /** Patch one item's fields (title, type, url/resource, tags, mega-menu fields,
   *  pinned). Throws. Returns the full updated menu tree. */
  updateItem: async (
    menuId: string,
    itemId: string,
    payload: MenuItemUpsert,
  ): Promise<EcomMenu | null> => {
    const res = await api.put(
      `${MENUS_BASE}/${encodeURIComponent(menuId)}/items/${encodeURIComponent(itemId)}`,
      payload,
    );
    return _menuFrom(res?.data);
  },

  /** Remove an item (and its subtree). Throws. Returns the updated menu tree. */
  removeItem: async (menuId: string, itemId: string): Promise<EcomMenu | null> => {
    const res = await api.delete(
      `${MENUS_BASE}/${encodeURIComponent(menuId)}/items/${encodeURIComponent(itemId)}`,
    );
    return _menuFrom(res?.data);
  },

  /** Move an item: change its parent and/or position. The backend resequences
   *  siblings. Throws. Returns the updated menu tree. Used by the up/down
   *  reorder + (optional) re-parent controls. */
  moveItem: async (
    menuId: string,
    itemId: string,
    args: { parent_id?: string | null; position?: number },
  ): Promise<EcomMenu | null> => {
    const res = await api.put(
      `${MENUS_BASE}/${encodeURIComponent(menuId)}/items/${encodeURIComponent(itemId)}/move`,
      { parent_id: args.parent_id ?? null, position: args.position },
    );
    return _menuFrom(res?.data);
  },

  /** Persist a whole edited tree at once (fallback save path — PUT the menu with
   *  its full `items` tree). The Phase-3 backend accepts the tree on the menu
   *  PUT so the editor can also do a single bulk save. Throws on failure. */
  saveTree: async (id: string, items: MenuItem[]): Promise<EcomMenu> => {
    const res = await api.put(`${MENUS_BASE}/${encodeURIComponent(id)}`, { items });
    const m = _menuFrom(res?.data);
    if (!m) throw new Error('Save menu tree returned no menu');
    return m;
  },
};
