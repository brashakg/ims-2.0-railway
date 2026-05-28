// ============================================================================
// IMS 2.0 - Lens Catalog API (Branch B' sub-PR 2)
// ============================================================================
// Typed lens-line master + enum config. Backed by Branch B' sub-PR 1 routers
// at /api/v1/lens-catalog and /api/v1/lens-enums.
//
// Import directly (not via the services/api barrel) -- newly-added services
// do NOT resolve through the barrel re-export (TS2614). The same direct-import
// pattern is used by returns.ts, shipping.ts, onlineStock.ts and powerGrid.ts.

import api from './client';

// ----------------------------------------------------------------------------
// Types
// ----------------------------------------------------------------------------

export interface LensRange {
  min: number;
  max: number;
  step: number;
}

export interface LensMrpBand {
  sph_min?: number;
  sph_max?: number;
  cyl_min?: number;
  cyl_max?: number;
  mrp?: number;
  cost_price?: number;
}

export interface LensLine {
  lens_line_id: string;
  brand: string;
  series: string;
  index: number;
  material: string;
  lens_type: string;
  coating: string;
  sph_range?: LensRange | null;
  cyl_range?: LensRange | null;
  has_add?: boolean | null;
  add_range?: LensRange | null;
  mrp?: number;
  cost_price?: number;
  mrp_table?: LensMrpBand[] | null;
  gst_rate?: number;
  hsn_code?: string;
  notes?: string | null;
  is_active?: boolean;
  created_at?: string;
  updated_at?: string;
  created_by?: string;
}

export interface LensCatalogListResponse {
  lens_lines: LensLine[];
  total: number;
}

export interface LensCatalogDetailResponse {
  lens_line: LensLine;
}

export interface LensCatalogMetaOptions {
  enums: {
    coatings?: string[];
    brands?: string[];
    series?: Array<Record<string, string[]>> | Record<string, string[]>;
    indexes?: number[];
    materials?: string[];
    lens_types?: string[];
  };
  enum_types: string[];
}

export interface LensCatalogListParams {
  brand?: string;
  series?: string;
  index?: number;
  material?: string;
  lens_type?: string;
  coating?: string;
  q?: string;
  active?: boolean;
  limit?: number;
}

// ----------------------------------------------------------------------------
// Catalog API
// ----------------------------------------------------------------------------

export const lensCatalogApi = {
  list: async (params: LensCatalogListParams = {}): Promise<LensCatalogListResponse> => {
    // Drop undefined / null params so axios doesn't serialise them as
    // 'brand=undefined'. The backend treats absent params as "no filter".
    const clean: Record<string, string | number | boolean> = {};
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') {
        clean[k] = v as string | number | boolean;
      }
    });
    const res = await api.get('/lens-catalog', { params: clean });
    return res.data as LensCatalogListResponse;
  },

  get: async (lensLineId: string): Promise<LensCatalogDetailResponse> => {
    const res = await api.get(`/lens-catalog/${encodeURIComponent(lensLineId)}`);
    return res.data as LensCatalogDetailResponse;
  },

  metaOptions: async (): Promise<LensCatalogMetaOptions> => {
    const res = await api.get('/lens-catalog/meta/options');
    return res.data as LensCatalogMetaOptions;
  },
};

// ----------------------------------------------------------------------------
// Enums API (read-only here -- writes are SUPERADMIN/ADMIN via Settings)
// ----------------------------------------------------------------------------

export interface LensEnumsResponse {
  enums: {
    coatings?: string[];
    brands?: string[];
    series?: unknown;
    indexes?: number[];
    materials?: string[];
    lens_types?: string[];
  };
}

export const lensEnumsApi = {
  list: async (): Promise<LensEnumsResponse> => {
    const res = await api.get('/lens-enums');
    return res.data as LensEnumsResponse;
  },
};
