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

// ----------------------------------------------------------------------------
// STOCK TALLY  (BVI Phase 5 — read-only reconciliation dashboard)
// ----------------------------------------------------------------------------
// GET /api/v1/online-store/stock-tally reconciles, per online-listed SKU, what
// the storefront lists vs the real physical on-hand vs what is already reserved,
// and flags oversell-risk (listed > sellable). It is STRICTLY read-only — no
// allocation/reserve is performed here (that write-path is a deferred follow-up).
// Graceful degradation: any error (404 stale deploy / 403 outside the ecom gate)
// resolves to an empty, unavailable envelope so the screen always renders.

/** One reconciliation row: what a single online-listed SKU lists vs what it can
 *  actually sell. All numeric fields default to 0; every field optional so a
 *  partial backend payload never breaks rendering. */
export interface StockTallyRow {
  sku: string;
  name?: string | null;
  /** What the storefront currently lists (Shopify/BVI online_stock). */
  online_listed_qty: number;
  /** AVAILABLE serialized stock units (physical on-hand). */
  on_hand: number;
  /** RESERVED serialized stock units. */
  reserved: number;
  /** on_hand - reserved (floored at 0) — what is actually free to sell. */
  sellable: number;
  /** A conservative reserve suggestion to keep off the listing (not enforced). */
  recommended_buffer: number;
  /** True when online_listed_qty > sellable (can sell a unit that isn't free). */
  oversell_risk: boolean;
}

export interface StockTallySummary {
  skus_checked: number;
  at_risk_count: number;
  total_online_listed: number;
  total_on_hand: number;
  total_reserved: number;
  total_sellable: number;
  /** Whether the e-commerce Postgres bridge is configured (else listed=0). */
  online_configured: boolean;
}

export interface StockTallyResult {
  items: StockTallyRow[];
  summary: StockTallySummary;
  /** false => the backend endpoint isn't deployed yet / not permitted; the
   *  screen shows the friendly "coming online" note rather than "0 SKUs". */
  available: boolean;
}

const STOCK_TALLY_PLACEHOLDER: StockTallyResult = {
  items: [],
  summary: {
    skus_checked: 0,
    at_risk_count: 0,
    total_online_listed: 0,
    total_on_hand: 0,
    total_reserved: 0,
    total_sellable: 0,
    online_configured: false,
  },
  available: false,
};

function _tallyRowFrom(r: Record<string, any>): StockTallyRow {
  const num = (v: any): number => (typeof v === 'number' && isFinite(v) ? v : 0);
  return {
    sku: String(r.sku ?? ''),
    name: r.name ?? null,
    online_listed_qty: num(r.online_listed_qty),
    on_hand: num(r.on_hand),
    reserved: num(r.reserved),
    sellable: num(r.sellable),
    recommended_buffer: num(r.recommended_buffer),
    oversell_risk: !!r.oversell_risk,
  };
}

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

  /** Read the READ-ONLY stock-tally reconciliation. NEVER throws: any error
   *  (404 stale deploy / 403 non-ecom viewer / network) resolves to an empty,
   *  unavailable envelope so the Stock tally screen always renders. */
  getStockTally: async (): Promise<StockTallyResult> => {
    try {
      const res = await api.get('/online-store/stock-tally');
      const data = (res?.data ?? {}) as Record<string, any>;
      const rawItems = Array.isArray(data.items) ? data.items : [];
      const s = (data.summary ?? {}) as Record<string, any>;
      const num = (v: any): number => (typeof v === 'number' && isFinite(v) ? v : 0);
      return {
        items: rawItems.map(_tallyRowFrom),
        summary: {
          skus_checked: num(s.skus_checked),
          at_risk_count: num(s.at_risk_count),
          total_online_listed: num(s.total_online_listed),
          total_on_hand: num(s.total_on_hand),
          total_reserved: num(s.total_reserved),
          total_sellable: num(s.total_sellable),
          online_configured: !!s.online_configured,
        },
        available: true,
      };
    } catch {
      return STOCK_TALLY_PLACEHOLDER;
    }
  },

  /** Fetch the Store health readiness dashboard. NEVER throws: any error (incl.
   *  a 404 on a stale deploy, or a 403 for a viewer outside the gate) resolves
   *  to a zeroed, unavailable envelope so the Store Health screen always renders. */
  getStoreHealth: async (): Promise<StoreHealth> => {
    try {
      const res = await api.get('/online-store/store-health');
      const d = (res?.data ?? {}) as Partial<StoreHealth>;
      return {
        available: true,
        readiness_pct: typeof d.readiness_pct === 'number' ? d.readiness_pct : 0,
        total_products: typeof d.total_products === 'number' ? d.total_products : 0,
        orphans: {
          total: d.orphans?.total ?? 0,
          orphan_count: d.orphans?.orphan_count ?? 0,
          no_mapping: d.orphans?.no_mapping ?? 0,
          not_in_collection: d.orphans?.not_in_collection ?? 0,
          missing_spine: d.orphans?.missing_spine ?? 0,
          orphans: Array.isArray(d.orphans?.orphans) ? d.orphans!.orphans : [],
        },
        coverage: {
          total: d.coverage?.total ?? 0,
          hsn_pct: d.coverage?.hsn_pct ?? 0,
          category_pct: d.coverage?.category_pct ?? 0,
          brand_pct: d.coverage?.brand_pct ?? 0,
          barcode_pct: d.coverage?.barcode_pct ?? 0,
          image_pct: d.coverage?.image_pct ?? 0,
          overall_pct: d.coverage?.overall_pct ?? 0,
          missing: d.coverage?.missing ?? {},
        },
        barcode_match: {
          total: d.barcode_match?.total ?? 0,
          with_barcode: d.barcode_match?.with_barcode ?? 0,
          missing_barcode: d.barcode_match?.missing_barcode ?? 0,
          duplicate_barcode: d.barcode_match?.duplicate_barcode ?? 0,
          unique_matched: d.barcode_match?.unique_matched ?? 0,
          match_pct: d.barcode_match?.match_pct ?? 0,
        },
        barcode_match_pct:
          typeof d.barcode_match_pct === 'number' ? d.barcode_match_pct : 0,
        fixes_needed: Array.isArray(d.fixes_needed) ? d.fixes_needed : [],
        sub_scores: {
          coverage_pct: d.sub_scores?.coverage_pct ?? 0,
          barcode_pct: d.sub_scores?.barcode_pct ?? 0,
          orphan_free_pct: d.sub_scores?.orphan_free_pct ?? 0,
        },
      };
    } catch {
      return STORE_HEALTH_PLACEHOLDER;
    }
  },
};

// ---------------------------------------------------------------------------
// STORE HEALTH (BVI Phase 5 — pre-cutover readiness dashboard)
// ---------------------------------------------------------------------------
// The read side of GET /api/v1/online-store/store-health: orphan SKUs, attribute
// coverage, barcode match + a composite readiness score. Read-only. Every field
// is defaulted so a partial backend payload never breaks rendering.

/** One orphaned (not list-ready) product surfaced in the sample list. */
export interface StoreHealthOrphan {
  sku?: string | null;
  product_id?: string | null;
  /** Why it is an orphan: any of no_mapping / not_in_collection / missing_spine. */
  reasons: string[];
}

/** One concrete, owner-actionable fix, e.g. {issue:"missing HSN code", count:12}. */
export interface StoreHealthFix {
  issue: string;
  count: number;
  /** Machine tag for the underlying check (hsn / barcode_dup / no_mapping / ...). */
  check?: string;
}

export interface StoreHealth {
  /** false => the backend didn't answer (stale deploy / 403) — zeroed envelope. */
  available: boolean;
  /** Composite readiness 0-100. */
  readiness_pct: number;
  total_products: number;
  orphans: {
    total: number;
    orphan_count: number;
    no_mapping: number;
    not_in_collection: number;
    missing_spine: number;
    orphans: StoreHealthOrphan[];
  };
  coverage: {
    total: number;
    hsn_pct: number;
    category_pct: number;
    brand_pct: number;
    barcode_pct: number;
    image_pct: number;
    overall_pct: number;
    missing: Record<string, number>;
  };
  barcode_match: {
    total: number;
    with_barcode: number;
    missing_barcode: number;
    duplicate_barcode: number;
    unique_matched: number;
    match_pct: number;
  };
  barcode_match_pct: number;
  fixes_needed: StoreHealthFix[];
  sub_scores: {
    coverage_pct: number;
    barcode_pct: number;
    orphan_free_pct: number;
  };
}

/** Safe zeroed placeholder used when the backend is absent / the viewer is
 *  outside the gate / any error occurs. Always reads as "0 / not available". */
const STORE_HEALTH_PLACEHOLDER: StoreHealth = {
  available: false,
  readiness_pct: 0,
  total_products: 0,
  orphans: {
    total: 0,
    orphan_count: 0,
    no_mapping: 0,
    not_in_collection: 0,
    missing_spine: 0,
    orphans: [],
  },
  coverage: {
    total: 0,
    hsn_pct: 0,
    category_pct: 0,
    brand_pct: 0,
    barcode_pct: 0,
    image_pct: 0,
    overall_pct: 0,
    missing: {},
  },
  barcode_match: {
    total: 0,
    with_barcode: 0,
    missing_barcode: 0,
    duplicate_barcode: 0,
    unique_matched: 0,
    match_pct: 0,
  },
  barcode_match_pct: 0,
  fixes_needed: [],
  sub_scores: { coverage_pct: 0, barcode_pct: 0, orphan_free_pct: 0 },
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
  // SUPERADMIN "block from online sale": when true, every product in this
  // collection is excluded from Shopify (never pushed; delisted if synced).
  online_sync_blocked?: boolean | null;
  online_sync_blocked_by?: string | null;
  online_sync_blocked_at?: string | null;
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

// Map the backend smart-rule relation vocabulary <-> the FE `op` vocabulary.
// The backend stores rules flat (top-level `rules:[{field,relation,value}]` +
// `disjunctive`); the FE editor works in `smart_rules:{disjunctive,rules:[{...op}]}`.
const _RELATION_TO_OP: Record<string, string> = {
  EQUALS: 'equals',
  NOT_EQUALS: 'not_equals',
  CONTAINS: 'contains',
  STARTS_WITH: 'starts_with',
  ENDS_WITH: 'ends_with',
  GREATER_THAN: 'greater_than',
  LESS_THAN: 'less_than',
};
const _OP_TO_RELATION: Record<string, string> = {
  equals: 'EQUALS',
  not_equals: 'NOT_EQUALS',
  contains: 'CONTAINS',
  starts_with: 'STARTS_WITH',
  ends_with: 'ENDS_WITH',
  greater_than: 'GREATER_THAN',
  less_than: 'LESS_THAN',
};

/** FE op -> backend relation (defaults to EQUALS for an unknown op). */
function opToRelation(op: string | null | undefined): string {
  return _OP_TO_RELATION[(op || '').toLowerCase()] ?? 'EQUALS';
}

/** Normalise a single collection payload: unwrap the {collection:{...}} envelope
 *  the get/create/update routes return, mirror the stable `id`, and rebuild the
 *  FE-shaped `smart_rules` ({disjunctive, rules:[{field,op,value}]}) from the
 *  backend's flat top-level `rules` + `disjunctive`. Tolerates a bare object. */
function _normCollection(data: any): EcomCollection {
  const c = (data && typeof data === 'object' && data.collection) ? data.collection : data;
  const row = (c ?? {}) as Record<string, any>;
  // Stable id (backend mirrors collection_id -> id, but fall back defensively).
  const id = String(row.id ?? row.collection_id ?? '');
  // Rebuild smart_rules from the flat backend shape when present.
  let smart_rules: SmartRules | null = null;
  if (Array.isArray(row.rules)) {
    smart_rules = {
      disjunctive: !!row.disjunctive,
      rules: row.rules.map((r: any) => ({
        field: String(r.field ?? ''),
        op: _RELATION_TO_OP[String(r.relation ?? '').toUpperCase()] ?? 'equals',
        value: String(r.value ?? ''),
      })),
    };
  } else if (row.smart_rules && typeof row.smart_rules === 'object') {
    smart_rules = row.smart_rules as SmartRules;
  }
  return { ...row, id, smart_rules } as EcomCollection;
}

/** Translate the FE create/update payload into the backend's flat shape: lift the
 *  nested `smart_rules` into top-level `rules` (with FE op -> backend relation) +
 *  `disjunctive` so smart-collection rules actually persist. Non-smart payloads
 *  pass through unchanged (minus the `smart_rules` key, which the backend rejects). */
function _toBackendUpsert(payload: CollectionUpsert): Record<string, any> {
  const { smart_rules, ...rest } = payload as CollectionUpsert & { smart_rules?: SmartRules };
  const out: Record<string, any> = { ...rest };
  if (smart_rules) {
    out.disjunctive = !!smart_rules.disjunctive;
    out.rules = (smart_rules.rules ?? []).map((r) => ({
      field: r.field,
      relation: opToRelation(r.op),
      value: r.value,
    }));
  }
  return out;
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
      return res?.data ? _normCollection(res.data) : null;
    } catch {
      return null;
    }
  },

  /** Create a collection. Throws on failure (caller toasts the message). */
  create: async (payload: CollectionUpsert): Promise<EcomCollection> => {
    const res = await api.post(COLLECTIONS_BASE, _toBackendUpsert(payload));
    return _normCollection(res.data);
  },

  /** Update a collection (partial merge). Throws on failure. */
  update: async (id: string, payload: CollectionUpsert): Promise<EcomCollection> => {
    const res = await api.put(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}`, _toBackendUpsert(payload));
    return _normCollection(res.data);
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

  // --- SUPERADMIN: block / unblock from online sale ------------------------
  // Flag a collection so ALL its products are excluded from Shopify (never
  // pushed; delisted if already synced). SUPERADMIN-only at the backend (a
  // non-SUPERADMIN call 403s). The delist obeys the dark write-gates: when DARK
  // it returns a SIMULATED plan; when the gates are live it fires a real
  // productUpdate (status -> DRAFT, reversible). Throws on failure so the screen
  // can toast it.

  /** Block a collection from online sale. Returns the block summary
   *  {blocked, member_count, delisted, mode, ...}. */
  block: async (
    id: string,
  ): Promise<{ blocked: boolean; member_count: number; delisted: number }> => {
    const res = await api.post(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}/block`);
    const d = (res?.data ?? {}) as Record<string, any>;
    return {
      blocked: !!d.blocked,
      member_count: typeof d.member_count === 'number' ? d.member_count : 0,
      delisted: typeof d.delisted === 'number' ? d.delisted : 0,
    };
  },

  /** Reverse the online-block flag (re-enables sync; a later push re-publishes).
   *  Returns {blocked:false, member_count, requeued}. */
  unblock: async (
    id: string,
  ): Promise<{ blocked: boolean; member_count: number; requeued: number }> => {
    const res = await api.post(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}/unblock`);
    const d = (res?.data ?? {}) as Record<string, any>;
    return {
      blocked: !!d.blocked,
      member_count: typeof d.member_count === 'number' ? d.member_count : 0,
      requeued: typeof d.requeued === 'number' ? d.requeued : 0,
    };
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

  /** Add a product (by SKU) to a CUSTOM collection. Throws on failure. The
   *  backend keys manual membership on `sku`, not the internal product id. */
  addProduct: async (id: string, sku: string): Promise<void> => {
    await api.post(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}/products`, {
      sku,
    });
  },

  /** Remove a product (by SKU) from a CUSTOM collection. Throws on failure. */
  removeProduct: async (id: string, sku: string): Promise<void> => {
    await api.delete(
      `${COLLECTIONS_BASE}/${encodeURIComponent(id)}/products/${encodeURIComponent(sku)}`,
    );
  },

  /** Persist the manual member order (array of SKUs, first = position 0).
   *  Throws on failure. */
  reorder: async (id: string, skus: string[]): Promise<void> => {
    await api.put(`${COLLECTIONS_BASE}/${encodeURIComponent(id)}/products/reorder`, {
      skus,
    });
  },

  // --- Smart (SMART) resolution --------------------------------------------

  /** Resolve which products a saved collection's rules match (preview, no save).
   *  The backend exposes only GET /{id}/resolved-products (resolve a SAVED
   *  collection by id) — there is no ad-hoc rule-resolver — so preview requires
   *  the collection to have been saved at least once. The endpoint returns
   *  {skus:[...], count}; we map each SKU to a slim CollectionProduct row.
   *  Fail-soft: returns {products:[], total:0, available:false} when there is no
   *  saved id or the Phase-2 backend isn't deployed, so the button never crashes. */
  resolvedProducts: async (
    args: { id?: string; rules?: SmartRules; limit?: number },
  ): Promise<{ products: CollectionProduct[]; total: number; available: boolean }> => {
    // No saved id -> nothing to resolve (the GET route is keyed on a stored id).
    if (!args.id) {
      return { products: [], total: 0, available: false };
    }
    try {
      const res = await api.get(
        `${COLLECTIONS_BASE}/${encodeURIComponent(args.id)}/resolved-products`,
        { params: { limit: args.limit ?? 50 } },
      );
      const data = res?.data ?? {};
      const skus = (Array.isArray(data.skus) ? data.skus : []) as string[];
      const products: CollectionProduct[] = skus.map((sku) => ({
        product_id: sku,
        sku,
      }));
      const total = typeof data.count === 'number' ? data.count : products.length;
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
   *  the full updated menu tree.
   *  BVI-7 fix: backend AddItem model expects {item: MenuItemIn, parent_id?, position?}
   *  NOT a flat MenuItemUpsert — wrap the payload in the {item} key. */
  addItem: async (
    menuId: string,
    payload: MenuItemUpsert,
    opts?: { parent_id?: string | null; position?: number },
  ): Promise<EcomMenu | null> => {
    const res = await api.post(
      `${MENUS_BASE}/${encodeURIComponent(menuId)}/items`,
      {
        item: payload,
        parent_id: opts?.parent_id ?? null,
        ...(opts?.position !== undefined ? { position: opts.position } : {}),
      },
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
   *  reorder + (optional) re-parent controls.
   *  BVI-8 fix: backend MoveItem model uses `new_parent_id` (not `parent_id`). */
  moveItem: async (
    menuId: string,
    itemId: string,
    args: { parent_id?: string | null; position?: number },
  ): Promise<EcomMenu | null> => {
    const res = await api.put(
      `${MENUS_BASE}/${encodeURIComponent(menuId)}/items/${encodeURIComponent(itemId)}/move`,
      { new_parent_id: args.parent_id ?? null, position: args.position },
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
 *  array or an envelope {images:[...]} / {items:[...]}), mapping each row's
 *  backend keys (status / assigned_to / image_id) onto the FE-shaped ones. */
function _imagesFrom(data: any): EcomProductImage[] {
  const arr = Array.isArray(data) ? data : (data?.images ?? data?.items ?? []);
  const rows = (Array.isArray(arr) ? arr : []) as any[];
  return rows.map((r) => _imageFrom(r)).filter(Boolean) as EcomProductImage[];
}

/** Unwrap a single-image payload (accepts {image:{...}} or a bare object) and
 *  map the backend's stored keys onto the ones the Design Queue reads:
 *  image_id -> id, status -> design_status, assigned_to -> assignee_id. Each map
 *  only fills an absent key, so a backend that already mirrors them is untouched. */
function _imageFrom(data: any): EcomProductImage | null {
  if (!data) return null;
  const raw = data.image ?? data;
  if (!raw || typeof raw !== 'object') return null;
  const m = { ...raw } as Record<string, any>;
  if (m.id == null && m.image_id != null) m.id = m.image_id;
  if (m.design_status == null && m.status != null) m.design_status = m.status;
  if (m.assignee_id == null && m.assigned_to != null) m.assignee_id = m.assigned_to;
  return m as EcomProductImage;
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
   *  unassign. Hits the dedicated POST /{id}/assign route (the generic PUT does
   *  NOT accept lifecycle fields). Throws on failure. */
  assign: async (id: string, assigneeId: string | null): Promise<EcomProductImage> => {
    const res = await api.post(`${IMAGES_BASE}/${encodeURIComponent(id)}/assign`, {
      assigned_to: assigneeId,
    });
    const m = _imageFrom(res?.data);
    if (!m) throw new Error('Assign image returned no record');
    return m;
  },

  /** Move the image to a new lifecycle status (Start -> IN_PROGRESS, Approve ->
   *  APPROVED, Reject -> REJECTED, ...) via the dedicated POST /{id}/status route
   *  (which enforces the valid-transition guard). `note` (e.g. a reject reason)
   *  is persisted first via the patchable `design_notes` field, since /status
   *  itself carries only the target status. Throws on failure. */
  setStatus: async (
    id: string,
    status: ImageDesignStatus,
    note?: string,
  ): Promise<EcomProductImage> => {
    if (note !== undefined && note !== '') {
      // design_notes IS patchable on the generic PUT; record the reason before
      // the status transition so it survives on the card.
      await api.put(`${IMAGES_BASE}/${encodeURIComponent(id)}`, { design_notes: note });
    }
    const res = await api.post(`${IMAGES_BASE}/${encodeURIComponent(id)}/status`, {
      status,
    });
    const m = _imageFrom(res?.data);
    if (!m) throw new Error('Set image status returned no record');
    return m;
  },

  /** Attach the designer's edited image URL and advance the record to REVIEW
   *  (awaiting approver sign-off) via the dedicated POST /{id}/edited route
   *  (which requires the image to be IN_PROGRESS and moves it to REVIEW).
   *  Throws on failure. Returns the updated record. */
  attachEdited: async (id: string, editedUrl: string): Promise<EcomProductImage> => {
    const res = await api.post(`${IMAGES_BASE}/${encodeURIComponent(id)}/edited`, {
      edited_url: editedUrl,
    });
    const m = _imageFrom(res?.data);
    if (!m) throw new Error('Attach edited image returned no record');
    return m;
  },

  /** Delete an image record. Throws on failure. */
  remove: async (id: string): Promise<void> => {
    await api.delete(`${IMAGES_BASE}/${encodeURIComponent(id)}`);
  },

  /** Upload a real image FILE (multipart) to durable storage and get its URL
   *  back. Phase 4a: the design queue can now take bytes directly instead of
   *  only a pasted, already-hosted URL. The backend validates type (png/jpeg/
   *  webp/avif) + size (<=10 MB), mints a safe storage key, and persists via the
   *  object_storage backend (S3/R2 in prod, local disk in dev). Returns the
   *  durable url + which backend stored it. Throws on failure (caller toasts);
   *  a 415 = bad type, 413 = too big, 503 = storage unavailable. */
  upload: async (
    file: File,
    opts: { product_id?: string; variant_id?: string; kind?: ImageRole | 'FINAL' } = {},
  ): Promise<{ url: string; storage_backend: string; kind: string; content_type: string; size: number }> => {
    const form = new FormData();
    form.append('file', file);
    if (opts.product_id) form.append('product_id', opts.product_id);
    if (opts.variant_id) form.append('variant_id', opts.variant_id);
    if (opts.kind) form.append('kind', opts.kind);
    const res = await api.post(`${IMAGES_BASE}/upload`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
    const d = (res?.data ?? {}) as Record<string, any>;
    if (!d.url) throw new Error('Upload returned no URL');
    return {
      url: String(d.url),
      storage_backend: String(d.storage_backend ?? 'unknown'),
      kind: String(d.kind ?? 'RAW'),
      content_type: String(d.content_type ?? ''),
      size: typeof d.size === 'number' ? d.size : 0,
    };
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

/** The POST /push/all-pending sweep result — the same engine, run over every
 *  pending/dirty ecom doc (a dry-run PLAN per doc when DARK). Mirrors the backend
 *  envelope; every field optional + nullable so a partial payload never breaks. */
export interface PushSweepResult {
  mode: PushMode;
  db_connected?: boolean | null;
  pushed_count?: number | null;
  limit_reached?: boolean | null;
  /** Per-entity {pushed, failed} tally. */
  summary?: Record<string, { pushed?: number; failed?: number } | null> | null;
  /** The per-doc PushResult rows (SIMULATED plans when DARK). */
  results?: PushResult[] | null;
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

  /** Sweep every pending/dirty ecom doc through the SAME engine and return the
   *  batch result. This drives the control panel's per-entity "Dry-run" buttons:
   *  when DARK (the default) each push is SIMULATED (a plan, no Shopify call), so
   *  this is always safe to run for preview. `entities` is an optional CSV filter
   *  (products,collections,menus,images); `limit` caps the sweep. The backend
   *  triple-gate is the real control over whether any of these become LIVE — this
   *  method never arms it and never bypasses it. Throws on HTTP failure so the
   *  caller can toast; the DARK/LIVE posture is carried in the returned `mode`. */
  pushAllPending: async (
    entities?: string,
    limit = 100,
  ): Promise<PushSweepResult> => {
    const params = new URLSearchParams();
    if (entities) params.set('entities', entities);
    params.set('limit', String(limit));
    const res = await api.post(`${PUSH_BASE}/all-pending?${params.toString()}`);
    const data = (res?.data ?? {}) as Partial<PushSweepResult>;
    const mode = (data.mode ?? {}) as PushMode;
    return {
      mode: {
        mode: mode.mode === 'LIVE' ? 'LIVE' : 'SIMULATED',
        is_live: mode.is_live ?? (mode.mode === 'LIVE'),
        writes_enabled: mode.writes_enabled ?? null,
        dispatch_mode: mode.dispatch_mode ?? null,
        creds_present: mode.creds_present ?? null,
        api_version: mode.api_version ?? null,
        single_writer_note: mode.single_writer_note ?? null,
      },
      db_connected: data.db_connected ?? null,
      pushed_count: data.pushed_count ?? 0,
      limit_reached: data.limit_reached ?? false,
      summary: (data.summary ?? {}) as PushSweepResult['summary'],
      results: Array.isArray(data.results) ? (data.results as PushResult[]) : [],
    };
  },
};

// ============================================================================
// SYNC-HEALTH DIAGNOSTICS sub-api  (BVI safety net — read-only, SUPERADMIN)
// ----------------------------------------------------------------------------
// Thin read-only wrappers over the existing admin diagnostics endpoints
// (backend/api/routers/admin.py ~848-943), surfaced as tiles on the Shopify sync
// control panel. All three are SUPERADMIN-gated on the backend and 100%
// fail-soft here: any error (403 non-superadmin / 404 stale deploy / network)
// resolves to a safe "unavailable" shape so a tile renders empty rather than
// crashing the page. NONE of these arm or bypass the push gates.
//   - GET /admin/online-store/sync-health   last sync / reconcile / webhooks / drift
//   - GET /admin/online-store/parity        IMS catalog counts vs what has a gid
//   - GET /admin/online-store/drift         live dual-writer drift check (needs creds)
// Import DIRECTLY from this module (NOT the api barrel — TS2614, per past sessions).
// ============================================================================

/** Parity oracle: per-entity total-vs-pushed-vs-missing counts (mirrors
 *  online_sync_health.parity_summary + the /admin/online-store/parity envelope,
 *  which also carries an uploads_audit block). All fields optional + nullable. */
export interface SyncParity {
  parity?: {
    entities?: Record<
      string,
      { total?: number; pushed?: number; missing?: number } | null
    > | null;
    ok?: boolean | null;
  } | null;
  uploads_audit?: {
    checked?: boolean | null;
    local_url_count?: number | null;
  } | null;
  /** Set when the read failed / was forbidden (tile renders "unavailable"). */
  unavailable?: boolean;
}

/** Sync-health summary (mirrors online_sync_health.sync_health). Only the fields
 *  the panel surfaces are typed; the rest pass through untyped. */
export interface SyncHealth {
  online_configured?: boolean | null;
  last_successful_shopify_sync_at?: string | null;
  last_shopify_sync?: { found?: boolean; ok?: boolean; ran_at?: string | null } | null;
  reconcile?: { checked?: boolean; oversell_risk?: number; count?: number } | null;
  webhooks?: { checked?: boolean; failed?: number; skipped?: number } | null;
  drift?: { checked?: boolean; reason?: string | null } | null;
  stock_miss?: { checked?: boolean; unresolved?: number } | null;
  [k: string]: any;
  unavailable?: boolean;
}

/** Live dual-writer drift result (mirrors online_sync_health.detect_drift). */
export interface SyncDrift {
  checked?: boolean | null;
  reason?: string | null;
  drifted?: Array<{ gid?: string; sku?: string }> | null;
  counts?: { scanned?: number; drifted?: number; no_timestamp?: number } | null;
  unavailable?: boolean;
}

const ADMIN_ONLINE_BASE = '/admin/online-store';

export const syncHealthApi = {
  /** Read the sync-health summary. Fail-soft -> {unavailable:true}. */
  getSyncHealth: async (): Promise<SyncHealth> => {
    try {
      const res = await api.get(`${ADMIN_ONLINE_BASE}/sync-health`);
      return { ...(res?.data ?? {}) } as SyncHealth;
    } catch {
      return { unavailable: true };
    }
  },

  /** Read the parity oracle + uploads audit. Fail-soft -> {unavailable:true}. */
  getParity: async (): Promise<SyncParity> => {
    try {
      const res = await api.get(`${ADMIN_ONLINE_BASE}/parity`);
      return { ...(res?.data ?? {}) } as SyncParity;
    } catch {
      return { unavailable: true };
    }
  },

  /** Run the LIVE dual-writer drift check (a real Shopify READ — no writes; needs
   *  creds, else it degrades to {checked:false}). Fail-soft -> {unavailable:true}. */
  getDrift: async (limit = 50): Promise<SyncDrift> => {
    try {
      const res = await api.get(`${ADMIN_ONLINE_BASE}/drift`, { params: { limit } });
      return { ...(res?.data ?? {}) } as SyncDrift;
    } catch {
      return { unavailable: true };
    }
  },
};

// ============================================================================
// ORDERS sub-api  (BVI Phase 3b — online-order ingestion into the IMS books)
// ----------------------------------------------------------------------------
// The READ side of the Shopify -> IMS order flow. A Shopify ORDER webhook lands
// in the generic `webhook_inbox` collection (api/routers/webhooks.py, HMAC
// verified) and the Phase-3b mapper (drained by NEXUS) turns it into a CANONICAL
// IMS order tagged `channel: "ONLINE"` so online sales flow into Orders + Finance
// exactly once (count-once / idempotent on the Shopify order id). This sub-api
// surfaces those mapped orders read-only + a re-map action for any Shopify order
// that FAILED to map (so an operator can retry after a fix), served by the
// Phase-3b backend router:
//   - GET  /api/v1/online-store/orders                       (list, channel=ONLINE)
//   - POST /api/v1/online-store/orders/remap/{shopify_order_id}  (retry one)
//
// GRACEFUL DEGRADATION: the Phase-3b backend may not be deployed yet (it ships
// separately) — every read resolves to a safe empty value rather than throwing,
// so the Online Orders screen always renders ("backend not yet available" rather
// than a crash). The remap write DOES throw on failure so the screen can toast
// it. Import this service DIRECTLY from this module (NOT the api barrel — the
// barrel re-export fails to resolve, TS2614, per past sessions).
// ============================================================================

/** Whether an online order was successfully mapped into the IMS books, or is
 *  stuck. MAPPED = a canonical IMS order exists; FAILED = the mapper could not
 *  build one (surfaces the Re-map action); PENDING = ingested, not yet drained. */
export type OnlineOrderMapStatus = 'MAPPED' | 'FAILED' | 'PENDING';

/** One online order as surfaced to the Online Orders screen. This mirrors the
 *  canonical IMS order fields the Phase-3b mapper writes (snake_case from the
 *  backend), PLUS the Shopify provenance + the map outcome. Every field is
 *  optional + nullable so a partial backend payload never breaks rendering. */
export interface OnlineOrder {
  /** The canonical IMS order id once mapped (absent on a FAILED/PENDING row). */
  id?: string | null;
  /** The IMS order number (e.g. ORD-...) once mapped. */
  order_number?: string | null;
  /** Shopify provenance — the numeric/gid order id + the human #1001 name. */
  shopify_order_id?: string | null;
  shopify_order_name?: string | null;
  /** Channel tag — always "ONLINE" for these rows (the count-once discriminator). */
  channel?: string | null;
  // --- Customer (mapper upserts the unified IMS customer) ---
  customer_name?: string | null;
  customer_phone?: string | null;
  customer_email?: string | null;
  // --- Money (canonical IMS order totals; GST-inclusive grand total) ---
  items_count?: number | null;
  grand_total?: number | null;
  currency?: string | null;
  // --- Status ---
  /** Canonical IMS order status (DRAFT/CONFIRMED/...) when mapped. */
  order_status?: string | null;
  /** Payment posture — either the IMS PAID/PARTIAL/PENDING or Shopify's
   *  financial_status (paid/pending/refunded/...). Rendered as-is, humanised. */
  payment_status?: string | null;
  /** Shopify fulfillment_status (fulfilled/partial/unfulfilled/null). */
  fulfillment_status?: string | null;
  // --- The map outcome (drives the Re-map affordance) ---
  map_status?: OnlineOrderMapStatus | null;
  /** Why a FAILED row failed (shown on the row so the operator knows the fix). */
  map_error?: string | null;
  // --- Timestamps (ISO strings; placed = Shopify created_at) ---
  placed_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** Filters for the online-orders list. All optional — omit for "everything". */
export interface OnlineOrderListFilters {
  /** Narrow to one map outcome (e.g. only the FAILED ones needing a re-map). */
  map_status?: OnlineOrderMapStatus;
  /** free-text over order number / shopify name / customer (backend-side). */
  search?: string;
  limit?: number;
}

/** The list envelope. `available=false` => the Phase-3b backend isn't deployed
 *  yet (orders is []), so the screen shows the friendly "coming online" note
 *  rather than an empty "no orders" state that would imply there are genuinely
 *  none. `failed_count` lets the screen badge the re-map queue without scanning. */
export interface OnlineOrdersResult {
  orders: OnlineOrder[];
  total: number;
  failed_count: number;
  available: boolean;
}

const ORDERS_BASE = '/online-store/orders';

/** Normalise one raw backend order row into OnlineOrder. Tolerates both the
 *  canonical IMS field names and a few common aliases (camelCase / Shopify
 *  names) so the screen renders whatever shape the mapper emits. */
function _onlineOrderFrom(o: Record<string, any>): OnlineOrder {
  const itemsCount =
    typeof o.items_count === 'number'
      ? o.items_count
      : Array.isArray(o.items)
        ? o.items.length
        : Array.isArray(o.line_items)
          ? o.line_items.length
          : null;
  // Map outcome: explicit field wins; otherwise infer from whether a canonical
  // IMS order id/number exists (mapped) vs a recorded error (failed).
  let mapStatus: OnlineOrderMapStatus | null =
    (o.map_status ?? o.mapping_status ?? null) as OnlineOrderMapStatus | null;
  if (!mapStatus) {
    if (o.id ?? o.order_id ?? o.order_number ?? o.orderNumber) mapStatus = 'MAPPED';
    else if (o.map_error ?? o.error) mapStatus = 'FAILED';
    else mapStatus = 'PENDING';
  }
  return {
    id: (o.id ?? o.order_id ?? o._id) != null ? String(o.id ?? o.order_id ?? o._id) : null,
    order_number: o.order_number ?? o.orderNumber ?? null,
    shopify_order_id:
      (o.shopify_order_id ?? o.shopifyOrderId ?? o.source_order_id) != null
        ? String(o.shopify_order_id ?? o.shopifyOrderId ?? o.source_order_id)
        : null,
    shopify_order_name: o.shopify_order_name ?? o.shopify_name ?? o.name ?? null,
    channel: o.channel ?? 'ONLINE',
    customer_name: o.customer_name ?? o.customerName ?? null,
    customer_phone: o.customer_phone ?? o.customerPhone ?? null,
    customer_email: o.customer_email ?? o.customerEmail ?? o.email ?? null,
    items_count: itemsCount,
    grand_total:
      typeof o.grand_total === 'number'
        ? o.grand_total
        : typeof o.grandTotal === 'number'
          ? o.grandTotal
          : typeof o.total_price === 'number'
            ? o.total_price
            : null,
    currency: o.currency ?? 'INR',
    order_status: o.order_status ?? o.orderStatus ?? o.status ?? null,
    payment_status: o.payment_status ?? o.paymentStatus ?? o.financial_status ?? null,
    fulfillment_status: o.fulfillment_status ?? o.fulfillmentStatus ?? null,
    map_status: mapStatus,
    map_error: o.map_error ?? o.error ?? o.mapping_error ?? null,
    placed_at: o.placed_at ?? o.processed_at ?? o.shopify_created_at ?? o.createdAt ?? o.created_at ?? null,
    created_at: o.created_at ?? o.createdAt ?? null,
    updated_at: o.updated_at ?? o.updatedAt ?? null,
  };
}

export const ordersApi = {
  /** List online orders (channel=ONLINE), optionally filtered by map outcome /
   *  search. NEVER throws: any error (incl. a 404 on a stale deploy, or a 403
   *  for a viewer outside the gate) resolves to an empty, unavailable result so
   *  the Online Orders screen always renders. */
  listOnline: async (filters: OnlineOrderListFilters = {}): Promise<OnlineOrdersResult> => {
    try {
      const params: Record<string, string | number> = {};
      if (filters.map_status) params.map_status = filters.map_status;
      if (filters.search) params.search = filters.search;
      if (typeof filters.limit === 'number') params.limit = filters.limit;
      const res = await api.get(ORDERS_BASE, { params });
      const data = res?.data;
      const arr = Array.isArray(data) ? data : (data?.orders ?? data?.items ?? []);
      const rows = (Array.isArray(arr) ? arr : []) as Array<Record<string, any>>;
      const orders = rows.map(_onlineOrderFrom);
      const total =
        typeof data?.total === 'number' ? data.total : orders.length;
      const failed_count =
        typeof data?.failed_count === 'number'
          ? data.failed_count
          : orders.filter((o) => o.map_status === 'FAILED').length;
      return { orders, total, failed_count, available: true };
    } catch {
      return { orders: [], total: 0, failed_count: 0, available: false };
    }
  },

  /** Re-run the mapper for ONE Shopify order that failed to map (after a fix).
   *  Throws on failure so the caller can toast it; the re-mapped IMS order is
   *  returned (normalised) on success. SUPERADMIN / ADMIN only at the backend. */
  remap: async (shopifyOrderId: string): Promise<OnlineOrder> => {
    const res = await api.post(
      `${ORDERS_BASE}/remap/${encodeURIComponent(shopifyOrderId)}`,
    );
    const data = res?.data;
    const row = (data && typeof data === 'object' && (data.order ?? data.result))
      ? (data.order ?? data.result)
      : data;
    return _onlineOrderFrom((row ?? {}) as Record<string, any>);
  },
};

// ============================================================================
// REFUND REVIEWS  (Shopify refund -> GST credit note; accountant queue consumer)
// ============================================================================
// A Shopify `refunds/create` webhook is turned into a proposed GST credit note +
// restock and, by DEFAULT (SHOPIFY_REFUND_AUTO off), parked in the
// `shopify_refund_review` queue for an ACCOUNTANT to confirm. This sub-api is the
// consumer for that queue, served by the backend router:
//   - GET  /api/v1/online-store/refund-reviews            (list, filter ?status=)
//   - POST /api/v1/online-store/refund-reviews/{id}/confirm  (post the credit note)
//   - POST /api/v1/online-store/refund-reviews/{id}/reject   (decline; no posting)
//
// GRACEFUL DEGRADATION: the list read resolves to a safe empty/unavailable value
// rather than throwing so the screen always renders. confirm / reject DO throw on
// failure so the screen can toast it. Import DIRECTLY from this module (NOT the
// api barrel — TS2614 per past sessions).
// ============================================================================

/** One Shopify refund review row as surfaced to the Refund reviews screen.
 *  Every field optional/nullable so a partial backend payload never breaks. */
export interface RefundReview {
  review_id: string;
  shopify_refund_id?: string | null;
  shopify_order_id?: string | null;
  order_id?: string | null;
  order_number?: string | null;
  customer_id?: string | null;
  customer_name?: string | null;
  store_id?: string | null;
  restock_store_id?: string | null;
  /** PENDING | DISCREPANCY | UNMATCHED | CREDIT_FAILED | NO_CUSTOMER | POSTED | REJECTED */
  status?: string | null;
  note?: string | null;
  resolved?: boolean | null;
  /** GST-inclusive gross of the computed credit note. */
  gross_refund?: number | null;
  /** What Shopify actually refunded (drives the DISCREPANCY flag). */
  shopify_refunded_amount?: number | null;
  /** The computed credit note (gross/taxable/tax split), as stored. */
  credit_note?: Record<string, any> | null;
  created_at?: string | null;
  updated_at?: string | null;
  resolved_by?: string | null;
  resolved_at?: string | null;
}

export interface RefundReviewsResult {
  reviews: RefundReview[];
  total: number;
  available: boolean;
}

export interface RefundReviewFilters {
  status?: string;
  resolved?: boolean;
  limit?: number;
}

const REFUND_REVIEWS_BASE = '/online-store/refund-reviews';

export const refundReviewsApi = {
  /** List refund review rows (optionally filtered by status). NEVER throws: any
   *  error (404 stale deploy / 403 outside the gate) resolves to an empty,
   *  unavailable result so the screen always renders. */
  list: async (filters: RefundReviewFilters = {}): Promise<RefundReviewsResult> => {
    try {
      const params: Record<string, string | number | boolean> = {};
      if (filters.status) params.status = filters.status;
      if (typeof filters.resolved === 'boolean') params.resolved = filters.resolved;
      params.limit = typeof filters.limit === 'number' ? filters.limit : 500;
      const res = await api.get(REFUND_REVIEWS_BASE, { params });
      const data = res?.data;
      const arr = Array.isArray(data) ? data : (data?.reviews ?? data?.items ?? []);
      const reviews = (Array.isArray(arr) ? arr : []) as RefundReview[];
      const total = typeof data?.total === 'number' ? data.total : reviews.length;
      return { reviews, total, available: true };
    } catch {
      return { reviews: [], total: 0, available: false };
    }
  },

  /** Confirm a review: post the credit note + restock from the stored row. Throws
   *  on failure so the caller can toast it. */
  confirm: async (reviewId: string): Promise<Record<string, any>> => {
    const res = await api.post(
      `${REFUND_REVIEWS_BASE}/${encodeURIComponent(reviewId)}/confirm`,
    );
    return (res?.data ?? {}) as Record<string, any>;
  },

  /** Reject a review: mark it resolved without posting anything. Throws on failure. */
  reject: async (reviewId: string): Promise<Record<string, any>> => {
    const res = await api.post(
      `${REFUND_REVIEWS_BASE}/${encodeURIComponent(reviewId)}/reject`,
    );
    return (res?.data ?? {}) as Record<string, any>;
  },
};
