// ============================================================================
// IMS 2.0 - Lens / contact-lens power-grid API
// ============================================================================
// Import directly (not via the services/api barrel) -- newly-added services
// don't resolve through the barrel re-export.

import api from './client';

export interface LensCell {
  count: number;
  skus: number;
  in_stock: boolean;
}

export interface LensGrid {
  sph_range: string[];
  cyl_range: string[];
  grid: Record<string, Record<string, LensCell>>;
  total_units: number;
  out_of_range_units?: number;
  lens_skus?: number;
}

export interface ClCell {
  count: number;
  skus: number;
  near_expiry: boolean;
  in_stock: boolean;
}

export interface ClGrid {
  power_range: string[];
  curve_range: string[];
  grid: Record<string, Record<string, ClCell>>;
  total_units: number;
  cl_skus?: number;
  near_expiry_days?: number;
}

export const powerGridApi = {
  lens: async (storeId?: string) => {
    const res = await api.get('/inventory/lenses/power-grid', {
      params: storeId ? { store_id: storeId } : {},
    });
    return res.data as LensGrid;
  },
  contactLens: async (storeId?: string, nearExpiryDays = 90) => {
    const res = await api.get('/inventory/contact-lenses/power-grid', {
      params: { ...(storeId ? { store_id: storeId } : {}), near_expiry_days: nearExpiryDays },
    });
    return res.data as ClGrid;
  },
};
