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

/** Editable enum types that map 1:1 onto a lens_catalog column. `series`
 *  is a per-brand list edited wholesale (out of scope for the simple editor)
 *  so it is excluded here. */
export type LensEnumType = 'brands' | 'coatings' | 'indexes' | 'materials' | 'lens_types';

export interface LensEnumMutationResponse {
  status: string;
  enum: { enum_id?: string; items?: Array<string | number> };
}

export interface LensEnumRenameResponse extends LensEnumMutationResponse {
  cascade: {
    old_value: string | number;
    new_value: string | number;
    catalog_rows_updated: number;
    stock_rows_stamped: number;
    affected_lens_line_ids: string[];
  };
}

export const lensEnumsApi = {
  list: async (): Promise<LensEnumsResponse> => {
    const res = await api.get('/lens-enums');
    return res.data as LensEnumsResponse;
  },

  /** Append one value to an enum list (idempotent -- dupes de-duped server
   *  side). For indexes pass a number; everything else a string. */
  addItem: async (
    enumType: LensEnumType,
    item: string | number,
  ): Promise<LensEnumMutationResponse> => {
    const res = await api.post(`/lens-enums/${encodeURIComponent(enumType)}/items`, { item });
    return res.data as LensEnumMutationResponse;
  },

  /** Rename a value and CASCADE the change onto every lens_line + stock row
   *  using it. Returns the cascade blast-radius counts. 409 if the rename
   *  would collide two lens lines onto one identity. */
  rename: async (
    enumType: LensEnumType,
    oldValue: string | number,
    newValue: string | number,
  ): Promise<LensEnumRenameResponse> => {
    const res = await api.post(`/lens-enums/${encodeURIComponent(enumType)}/rename`, {
      old_value: oldValue,
      new_value: newValue,
    });
    return res.data as LensEnumRenameResponse;
  },

  /** Delete a value. 409 (with the in-use count in the message) if any active
   *  lens line still references it. */
  deleteItem: async (
    enumType: LensEnumType,
    item: string | number,
  ): Promise<LensEnumMutationResponse> => {
    const res = await api.delete(
      `/lens-enums/${encodeURIComponent(enumType)}/items/${encodeURIComponent(String(item))}`,
    );
    return res.data as LensEnumMutationResponse;
  },
};
