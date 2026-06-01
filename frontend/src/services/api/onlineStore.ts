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
