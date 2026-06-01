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

// ============================================================================
// IMAGES sub-api  (BVI Phase 4 — Image Design Workflow, "push-dark")
// ----------------------------------------------------------------------------
// FLAGSHIP #3 of the BVI merge: the product-image DESIGN QUEUE — the design
// team's daily workflow, run entirely inside IMS. See
// docs/reference/BVI_MERGE_PLAN.md section A.1 (ProductImage -> product_images)
// and section B Phase 4.
//
// Each image record tracks one product/variant photo through its design
// lifecycle:  QUEUED -> IN_PROGRESS -> REVIEW -> APPROVED | REJECTED. A raw
// (cataloger) photo becomes an edited (designer) hero image; an editor/approver
// signs it off. role distinguishes the RAW source from the EDITED output (BVI
// ProductImage.role). NOTHING is pushed to Shopify here — images are stored +
// progressed inside IMS Mongo `product_images`; the single-writer Shopify image
// push (shopifyMediaId) is Phase 5/6.
//
// Field set mirrors the BVI Prisma `ProductImage` / `VariantImage` models
// (ecommerce/prisma/schema.prisma:254-292) mapped to snake_case, PLUS the
// per-image design-status lifecycle + assignee that IMS adds on top of BVI's
// product-level imageDesignStatus.
//
// GRACEFUL DEGRADATION: the Phase-4 backend images router (online_store_images)
// may not be deployed yet (it ships separately). Every read resolves to a safe
// empty value rather than throwing, so the Design Queue screen always renders
// ("backend not yet available" rather than a crash). Writes surface a thrown
// error so the screen can toast it. Import this service DIRECTLY from this
// module (NOT the api barrel — the barrel re-export fails to resolve, TS2614,
// per past sessions).
// ============================================================================

/** Where an image sits in the design lifecycle.
 *  - QUEUED      = raw image submitted by a cataloger, awaiting a designer
 *  - IN_PROGRESS = a designer has started editing it
 *  - REVIEW      = an edited image is attached, awaiting approver sign-off
 *  - APPROVED    = signed off; eligible for the (later) Shopify push
 *  - REJECTED    = sent back; needs a redo (mirrors a designer/approver bounce) */
export type ImageDesignStatus =
  | 'QUEUED'
  | 'IN_PROGRESS'
  | 'REVIEW'
  | 'APPROVED'
  | 'REJECTED';

/** The five lifecycle states, in pipeline order — drives the filter chip row
 *  + status columns on the Design Queue screen. */
export const IMAGE_DESIGN_STATUSES: ImageDesignStatus[] = [
  'QUEUED',
  'IN_PROGRESS',
  'REVIEW',
  'APPROVED',
  'REJECTED',
];

/** Image source role (BVI ProductImage.role).
 *  - RAW    = uploaded by the cataloger (not pushed to Shopify)
 *  - EDITED = the designer's finished hero image (the push candidate) */
export type ImageRole = 'RAW' | 'EDITED';

/** One product/variant image record progressing through the design queue.
 *  All presentation fields are optional + nullable so a partial backend payload
 *  never breaks rendering. */
export interface EcomProductImage {
  id: string;
  // The IMS product this image belongs to (catalog_products / products _id or
  // the bridged sku — the backend resolves it). Variant images carry variant_sku
  // (BVI VariantImage merged into product_images via a variant discriminator).
  product_id: string;
  variant_sku?: string | null;
  // Human-friendly product reference for the card (backend joins these in).
  product_title?: string | null;
  brand?: string | null;
  model_no?: string | null;
  category?: string | null;
  // The image itself. `url` = current best (edited if present, else raw);
  // `raw_url`/`edited_url` are the explicit RAW source + EDITED output so the
  // card can show them side-by-side. `original_url` mirrors BVI originalUrl
  // (the un-rehosted source) for download.
  url?: string | null;
  raw_url?: string | null;
  edited_url?: string | null;
  original_url?: string | null;
  position?: number | null;
  role?: ImageRole | null;
  // The design lifecycle.
  design_status: ImageDesignStatus;
  // The designer/owner currently working it (user id) + a display name the
  // backend resolves for the card. null = unassigned.
  assignee_id?: string | null;
  assignee_name?: string | null;
  // Last reject reason / review note (shown on a REJECTED card).
  note?: string | null;
  // Shopify-side mapping (informational in Phase 4 — push is Phase 5/6).
  shopify_media_id?: string | null;
  // dirty flag -> Phase-5 push queue. Informational in Phase 4.
  locally_modified?: boolean | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** Create payload for a new image record (a cataloger submitting a raw photo).
 *  product_id + a raw url are the minimum; everything else is optional. */
export interface ImageCreate {
  product_id: string;
  variant_sku?: string | null;
  raw_url?: string;
  url?: string;
  original_url?: string;
  position?: number;
  role?: ImageRole;
}

/** Partial update payload (the backend merges). Used by the edit affordances
 *  + as the low-level primitive the higher-level helpers build on. */
export interface ImageUpdate {
  variant_sku?: string | null;
  raw_url?: string | null;
  edited_url?: string | null;
  original_url?: string | null;
  url?: string | null;
  position?: number;
  role?: ImageRole;
  design_status?: ImageDesignStatus;
  assignee_id?: string | null;
  note?: string | null;
}

/** Filters for the queue list. All optional — omit for "everything". */
export interface ImageListFilters {
  status?: ImageDesignStatus;
  product_id?: string;
  assignee_id?: string;
  /** free-text over product title / brand / model (backend-side). */
  search?: string;
  limit?: number;
}

const IMAGES_BASE = '/online-store/images';

/** Normalise an images list payload into EcomProductImage[] (accepts a bare
 *  array or an envelope {images:[...]} / {items:[...]}). */
function _imagesFrom(data: any): EcomProductImage[] {
  const arr = Array.isArray(data) ? data : (data?.images ?? data?.items ?? []);
  return (Array.isArray(arr) ? arr : []) as EcomProductImage[];
}

/** Unwrap a single-image payload (accepts {image:{...}} or a bare object). */
function _imageFrom(data: any): EcomProductImage | null {
  if (!data) return null;
  const m = data.image ?? data;
  return (m && typeof m === 'object' ? m : null) as EcomProductImage | null;
}

export const imagesApi = {
  /** List queue images, optionally filtered by status / product / assignee /
   *  search. Fail-soft: any error (incl. a 404 on a stale deploy) -> [] so the
   *  Design Queue renders even before the Phase-4 backend is deployed. */
  list: async (filters: ImageListFilters = {}): Promise<EcomProductImage[]> => {
    try {
      const params: Record<string, string | number> = {};
      if (filters.status) params.status = filters.status;
      if (filters.product_id) params.product_id = filters.product_id;
      if (filters.assignee_id) params.assignee_id = filters.assignee_id;
      if (filters.search) params.search = filters.search;
      if (typeof filters.limit === 'number') params.limit = filters.limit;
      const res = await api.get(IMAGES_BASE, { params });
      return _imagesFrom(res?.data);
    } catch {
      return [];
    }
  },

  /** Fetch one image record. Fail-soft -> null. */
  get: async (id: string): Promise<EcomProductImage | null> => {
    try {
      const res = await api.get(`${IMAGES_BASE}/${encodeURIComponent(id)}`);
      return _imageFrom(res?.data);
    } catch {
      return null;
    }
  },

  /** Create a new image record (cataloger submits a raw photo). Throws on
   *  failure (caller toasts the message). */
  create: async (payload: ImageCreate): Promise<EcomProductImage> => {
    const res = await api.post(IMAGES_BASE, payload);
    const m = _imageFrom(res?.data);
    if (!m) throw new Error('Create image returned no record');
    return m;
  },

  /** Patch an image record (partial merge). Throws on failure. Returns the
   *  updated record so the screen can re-render from a single source of truth. */
  update: async (id: string, payload: ImageUpdate): Promise<EcomProductImage> => {
    const res = await api.put(`${IMAGES_BASE}/${encodeURIComponent(id)}`, payload);
    const m = _imageFrom(res?.data);
    if (!m) throw new Error('Update image returned no record');
    return m;
  },

  /** Assign the image to a user (the designer who will work it). Pass null to
   *  unassign. Convenience wrapper over `update`. Throws on failure. */
  assign: async (id: string, assigneeId: string | null): Promise<EcomProductImage> => {
    const res = await api.put(`${IMAGES_BASE}/${encodeURIComponent(id)}`, {
      assignee_id: assigneeId,
    });
    const m = _imageFrom(res?.data);
    if (!m) throw new Error('Assign image returned no record');
    return m;
  },

  /** Move the image to a new lifecycle status (Start -> IN_PROGRESS, Approve ->
   *  APPROVED, Reject -> REJECTED, ...). `note` carries a reject/review reason.
   *  Throws on failure. Returns the updated record. */
  setStatus: async (
    id: string,
    status: ImageDesignStatus,
    note?: string,
  ): Promise<EcomProductImage> => {
    const res = await api.put(`${IMAGES_BASE}/${encodeURIComponent(id)}`, {
      design_status: status,
      ...(note !== undefined ? { note } : {}),
    });
    const m = _imageFrom(res?.data);
    if (!m) throw new Error('Set image status returned no record');
    return m;
  },

  /** Attach the designer's edited image URL and advance the record to REVIEW
   *  (awaiting approver sign-off), mirroring BVI's "upload edited -> publish"
   *  step but stopping at REVIEW (the approve gate is a separate action).
   *  Throws on failure. Returns the updated record. */
  attachEdited: async (id: string, editedUrl: string): Promise<EcomProductImage> => {
    const res = await api.put(`${IMAGES_BASE}/${encodeURIComponent(id)}`, {
      edited_url: editedUrl,
      role: 'EDITED' as ImageRole,
      design_status: 'REVIEW' as ImageDesignStatus,
    });
    const m = _imageFrom(res?.data);
    if (!m) throw new Error('Attach edited image returned no record');
    return m;
  },

  /** Delete an image record. Throws on failure. */
  remove: async (id: string): Promise<void> => {
    await api.delete(`${IMAGES_BASE}/${encodeURIComponent(id)}`);
  },
};

// ============================================================================
// PUSH sub-api  (BVI Phase 5 — IMS -> Shopify push control surface)
// ----------------------------------------------------------------------------
// The frontend half of the single-writer Shopify PUSH (backend router
// api/routers/online_store_push.py -> api/services/shopify_push.py). It drives
// the four per-entity push endpoints and reads the current push posture.
//
// ***** BUILT DARK (the non-negotiable safety contract) *****
// Every push is SIMULATED (a dry-run PLAN, NO Shopify network call) UNLESS ALL of
// IMS_SHOPIFY_WRITES on AND DISPATCH_MODE=live AND Shopify creds present. Default
// / missing-creds / gate-off => SIMULATED. Per #262 BVI is the single Shopify
// writer; the IMS push stays retired until the owner flips the gates in the
// Phase-6 cutover. So the UI must make the mode unmistakable — nobody should
// think a dry-run went live. The shared sync banner + the returned `mode` on
// every publish do exactly that.
//
// ROLE GATE: the push routes are SUPERADMIN / ADMIN only (narrower than the rest
// of the module). The UI also gates the Publish controls to those roles; the
// backend is the real enforcement (a non-admin call 403s).
//
// GRACEFUL DEGRADATION: getStatus never throws — any error (incl. a 404 on a
// stale deploy, or a 403 for a non-admin viewer) resolves to a safe DARK
// placeholder so the banner always renders as "writes OFF". The publish calls
// DO throw on failure so the screen can toast the error. Import this service
// DIRECTLY from this module (NOT the api barrel — the barrel re-export fails to
// resolve, TS2614, per past sessions).
// ============================================================================

/** Effective push posture + the three gate components (mirrors
 *  shopify_push.push_mode_status). `is_live` is the single source of truth the
 *  UI keys off; the components explain WHY when DARK. */
export interface PushMode {
  /** 'LIVE' only when all three gates align, else 'SIMULATED'. */
  mode: 'SIMULATED' | 'LIVE';
  /** IMS_SHOPIFY_WRITES env flag. */
  writes_enabled?: boolean | null;
  /** off | test | live (the destructive-write dispatch gate). */
  dispatch_mode?: string | null;
  /** shop_url + access_token present in the `integrations` config? */
  creds_present?: boolean | null;
  /** Convenience: writes_enabled && dispatch_mode==='live' && creds_present. */
  is_live?: boolean | null;
  api_version?: string | null;
  /** Advisory note (the single-writer / cutover explanation). */
  single_writer_note?: string | null;
}

/** Structured result of one push attempt (mirrors shopify_push.PushResult).
 *  `mode` tells the user whether this was a dry-run (SIMULATED) or a real write
 *  (LIVE); `shopify_id` is the gid (echoed if already mapped, set on a LIVE
 *  create); `ok=false` carries a human `error`; `reason` explains a SIMULATED. */
export interface PushResult {
  mode: 'SIMULATED' | 'LIVE';
  entity: 'product' | 'variant' | 'collection' | 'menu' | 'image' | string;
  action: 'create' | 'update' | 'skip' | 'noop' | string;
  target_id?: string | null;
  ok: boolean;
  shopify_id?: string | null;
  payload?: Record<string, any> | null;
  error?: string | null;
  reason?: string | null;
}

/** Per-entity pushed-vs-pending counts (shapes differ per entity, mirroring the
 *  backend GET /push/status counts block). All fields optional + nullable. */
export interface PushCounts {
  products?: { staged?: number; pushed?: number; pending?: number } | null;
  collections?: { total?: number; pushed?: number; pending?: number } | null;
  menus?: { total?: number; pushed?: number; pending?: number } | null;
  images?: { approved?: number; pushed?: number; pending?: number } | null;
}

/** The GET /push/status payload. `db_connected=false` => the push store is
 *  unavailable (counts are zeros). */
export interface PushStatus {
  mode: PushMode;
  db_connected: boolean;
  counts: PushCounts;
}

const PUSH_BASE = '/online-store/push';

/** A safe DARK placeholder for getStatus — used when the backend is absent, the
 *  viewer isn't an admin (403), or any error occurs. Always reads as "writes
 *  OFF" so the banner can never mislead. */
const PUSH_STATUS_PLACEHOLDER: PushStatus = {
  mode: { mode: 'SIMULATED', is_live: false },
  db_connected: false,
  counts: {},
};

/** Unwrap the {result: PushResult} envelope the push routes return. Tolerates a
 *  bare PushResult too. */
function _pushResultFrom(data: any): PushResult {
  const r = (data && typeof data === 'object' && data.result) ? data.result : data;
  return (r ?? {}) as PushResult;
}

export const pushApi = {
  /** Read the CURRENT push posture + per-entity counts. NEVER throws: any error
   *  (404 stale deploy / 403 non-admin / network) -> the DARK placeholder so the
   *  banner always renders as "writes OFF". */
  getStatus: async (): Promise<PushStatus> => {
    try {
      const res = await api.get(`${PUSH_BASE}/status`);
      const data = (res?.data ?? {}) as Partial<PushStatus>;
      const mode = (data.mode ?? {}) as PushMode;
      return {
        mode: {
          mode: mode.mode === 'LIVE' ? 'LIVE' : 'SIMULATED',
          writes_enabled: mode.writes_enabled ?? null,
          dispatch_mode: mode.dispatch_mode ?? null,
          creds_present: mode.creds_present ?? null,
          is_live: mode.is_live ?? (mode.mode === 'LIVE'),
          api_version: mode.api_version ?? null,
          single_writer_note: mode.single_writer_note ?? null,
        },
        db_connected: !!data.db_connected,
        counts: (data.counts ?? {}) as PushCounts,
      };
    } catch {
      return PUSH_STATUS_PLACEHOLDER;
    }
  },

  /** Push a catalog product (+ ecom sub-doc + variants). Throws on HTTP failure
   *  (the caller toasts); a SIMULATED dry-run is a normal ok=true result. */
  pushProduct: async (productId: string): Promise<PushResult> => {
    const res = await api.post(`${PUSH_BASE}/product/${encodeURIComponent(productId)}`);
    return _pushResultFrom(res?.data);
  },

  /** Push an ecom_collections doc (collectionCreate/Update + smart ruleSet). */
  pushCollection: async (collectionId: string): Promise<PushResult> => {
    const res = await api.post(`${PUSH_BASE}/collection/${encodeURIComponent(collectionId)}`);
    return _pushResultFrom(res?.data);
  },

  /** Push an ecom_menus doc (the nav / mega-menu tree). */
  pushMenu: async (menuId: string): Promise<PushResult> => {
    const res = await api.post(`${PUSH_BASE}/menu/${encodeURIComponent(menuId)}`);
    return _pushResultFrom(res?.data);
  },

  /** Push ONE APPROVED product image (productCreateMedia onto its parent). A
   *  non-APPROVED image is NOT an HTTP error — the engine returns ok=false
   *  action=skip, which the caller surfaces honestly. */
  pushImage: async (imageId: string): Promise<PushResult> => {
    const res = await api.post(`${PUSH_BASE}/image/${encodeURIComponent(imageId)}`);
    return _pushResultFrom(res?.data);
  },
};
