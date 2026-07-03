// ============================================================================
// IMS 2.0 - Collections Insights (merchandising surface — Collections Phase 1)
// ============================================================================
// Typed client for the KPI/insights endpoints on /api/v1/collections (Track 2
// backend) plus the ad-hoc rule PREVIEW and the governed SMART create used by
// the /collections chip builder.
//
// Contract (Track 2):
//   GET  /collections/insights/summary?limit=N   -> { collections:[SummaryRow] }
//   GET  /collections/{id}/insights?days=&store_id= -> CollectionInsights
//   GET  /collections/{id}/insights/stores       -> { stores:[StoreInsightRow] }
//   POST /collections/preview {rules,disjunctive} -> { match_count, units_on_hand, sample }
// Create goes through the EXISTING admin CRUD (POST /online-store/collections)
// with the backend-flat rule shape ({field, relation, value}) — NOT via
// onlineStore.collectionsApi.create, whose op->relation map has no 'IN' and
// would silently downgrade multi-value rules to EQUALS.
//
// Every READ is FAIL-SOFT (404/501/any error -> safe empty/null) because the
// Track 2 backend may not be deployed yet when this surface ships. The create
// WRITE throws so the caller can toast the message.
// Import DIRECTLY from this module (not the api barrel — TS2614).

import api from './client';

const BASE = '/collections';
const ADMIN_BASE = '/online-store/collections';

export type ValueBasis = 'cost' | 'offer' | 'mixed';

/** One compiled smart-collection rule clause, backend-flat shape. Multi-value
 *  chip groups compile to relation 'IN' with an array value; price bounds use
 *  GREATER_THAN / LESS_THAN with a numeric value. */
export interface InsightRule {
  field: string;
  relation: string; // 'EQUALS' | 'IN' | 'GREATER_THAN' | 'LESS_THAN' | ...
  value: string | number | string[];
}

export interface CollectionInsights {
  collection_id: string;
  title: string;
  members: number;
  units_on_hand: number;
  stock_value: number;
  value_basis: ValueBasis;
  stock_value_mrp: number;
  sold: { d7: number; d30: number; d90: number };
  revenue_30d: number;
  margin_30d: number | null;
  sell_through_30d: number | null;
  days_of_cover: number | null;
  membership_capped: boolean;
  materialized_at: string | null;
}

export interface CollectionStoreInsight {
  store_id: string;
  store_name: string;
  on_hand: number;
  stock_value: number;
  value_basis: ValueBasis;
  sold_30d: number;
  sell_through: number | null;
  days_of_cover: number | null;
}

export interface CollectionSummaryRow {
  collection_id: string;
  title: string;
  collection_type: 'CUSTOM' | 'SMART' | string;
  published: boolean;
  members: number;
  on_hand: number;
  stock_value: number;
  value_basis: ValueBasis;
  sold_30d: number;
}

export interface PreviewSample {
  sku: string;
  brand?: string | null;
  model?: string | null;
  title?: string | null;
  mrp?: number | null;
  image?: string | null;
}

export interface CollectionPreview {
  match_count: number;
  units_on_hand: number;
  sample: PreviewSample[];
}

/** Slim read of one admin collection that PRESERVES array rule values (the
 *  onlineStore normaliser stringifies them), for the read-only rule chips on
 *  the detail page. Fail-soft -> null. */
export interface CollectionMeta {
  id: string;
  title: string;
  collection_type: 'CUSTOM' | 'SMART' | string;
  published: boolean;
  rules: InsightRule[];
  disjunctive: boolean;
}

/** Auto a handle from a title (same rule as the online-store editor). */
export function slugifyTitle(s: string): string {
  return (s || '')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120);
}

export const collectionsInsightsApi = {
  /** Chain-or-store KPI rollup for one collection. Fail-soft -> null. */
  insights: async (
    id: string,
    opts?: { days?: number; store_id?: string },
  ): Promise<CollectionInsights | null> => {
    try {
      const params: Record<string, string | number> = {};
      if (opts?.days) params.days = opts.days;
      if (opts?.store_id) params.store_id = opts.store_id;
      const res = await api.get(`${BASE}/${encodeURIComponent(id)}/insights`, { params });
      return (res?.data ?? null) as CollectionInsights | null;
    } catch {
      return null;
    }
  },

  /** Per-store split for one collection. Fail-soft -> []. */
  storeInsights: async (id: string): Promise<CollectionStoreInsight[]> => {
    try {
      const res = await api.get(`${BASE}/${encodeURIComponent(id)}/insights/stores`);
      const arr = res?.data?.stores;
      return (Array.isArray(arr) ? arr : []) as CollectionStoreInsight[];
    } catch {
      return [];
    }
  },

  /** List rollup for the Collections landing page. Tries the requested limit
   *  first; if the backend rejects it (e.g. validation cap), retries with the
   *  server default. Fail-soft -> []. */
  summary: async (limit?: number): Promise<CollectionSummaryRow[]> => {
    const read = async (params?: { limit: number }) => {
      const res = await api.get(`${BASE}/insights/summary`, { params });
      const arr = res?.data?.collections;
      return (Array.isArray(arr) ? arr : []) as CollectionSummaryRow[];
    };
    try {
      return await read(limit ? { limit } : undefined);
    } catch {
      if (!limit) return [];
      try {
        return await read();
      } catch {
        return [];
      }
    }
  },

  /** Ad-hoc rule preview (no save): match count + on-hand units + sample rows.
   *  Fail-soft -> null so the builder can render "preview unavailable" while
   *  the Track 2 backend isn't deployed. */
  preview: async (
    rules: InsightRule[],
    disjunctive = false,
  ): Promise<CollectionPreview | null> => {
    try {
      const res = await api.post(`${BASE}/preview`, { rules, disjunctive });
      const data = res?.data;
      if (!data || typeof data !== 'object') return null;
      return {
        match_count: Number(data.match_count ?? 0),
        units_on_hand: Number(data.units_on_hand ?? 0),
        sample: (Array.isArray(data.sample) ? data.sample : []) as PreviewSample[],
      };
    } catch {
      return null;
    }
  },

  /** One admin collection with raw (array-preserving) rules. Fail-soft -> null. */
  collectionMeta: async (id: string): Promise<CollectionMeta | null> => {
    try {
      const res = await api.get(`${ADMIN_BASE}/${encodeURIComponent(id)}`);
      const data = res?.data;
      const row = (data && typeof data === 'object' && data.collection ? data.collection : data) as
        | Record<string, unknown>
        | null;
      if (!row) return null;
      const rules = (Array.isArray(row.rules) ? row.rules : []).map((r) => {
        const rr = r as Record<string, unknown>;
        return {
          field: String(rr.field ?? ''),
          relation: String(rr.relation ?? 'EQUALS'),
          value: (Array.isArray(rr.value)
            ? (rr.value as unknown[]).map((v) => String(v))
            : typeof rr.value === 'number'
              ? rr.value
              : String(rr.value ?? '')) as string | number | string[],
        };
      });
      return {
        id: String(row.id ?? row.collection_id ?? ''),
        title: String(row.title ?? ''),
        collection_type: String(row.collection_type ?? 'SMART'),
        published: !!row.published,
        rules,
        disjunctive: !!row.disjunctive,
      };
    } catch {
      return null;
    }
  },

  /** Create a SMART collection through the EXISTING admin CRUD, always with
   *  published:false (this surface never publishes online — that stays in the
   *  online-store editor). Throws on failure; returns the new id. */
  createSmart: async (args: {
    title: string;
    rules: InsightRule[];
    disjunctive?: boolean;
  }): Promise<{ id: string }> => {
    const res = await api.post(ADMIN_BASE, {
      title: args.title,
      handle: slugifyTitle(args.title),
      collection_type: 'SMART',
      published: false,
      rules: args.rules,
      disjunctive: !!args.disjunctive,
    });
    const data = res?.data;
    const row = (data && typeof data === 'object' && data.collection ? data.collection : data) as
      | Record<string, unknown>
      | undefined;
    const id = String(row?.id ?? row?.collection_id ?? '');
    if (!id) throw new Error('Create succeeded but no collection id was returned');
    return { id };
  },
};

export default collectionsInsightsApi;
