// ============================================================================
// IMS 2.0 - Lens Stock API (Branch B' sub-PR 2)
// ============================================================================
// Per-cell power-matrix CRUD + atomic reserve/commit/release. Backed by
// Branch B' sub-PR 1 router at /api/v1/lens-stock.
//
// Import directly (not via the services/api barrel) -- newly-added services
// do NOT resolve through the barrel re-export. Same pattern as lensCatalog.ts.

import api from './client';

// ----------------------------------------------------------------------------
// Types
// ----------------------------------------------------------------------------

export interface LensStockCell {
  line_stock_id: string;
  lens_line_id: string;
  store_id: string;
  sph: number;
  cyl: number;
  add: number | null;
  on_hand: number;
  reserved: number;
  reorder_point?: number;
  safety_stock?: number;
  available: number;
  last_counted_at?: string | null;
  last_counted_by?: string | null;
  last_movement_at?: string | null;
}

export interface LensStockMatrixResponse {
  cells: LensStockCell[];
  total: number;
  lens_line_id: string;
}

export interface LensStockCellResponse {
  cell: LensStockCell;
}

export interface LensGapPlannerCell extends LensStockCell {
  gap: number;
}

export interface LensGapPlannerResponse {
  cells: LensGapPlannerCell[];
  total: number;
}

export interface LensStockAuditRow {
  audit_id?: string;
  line_stock_id: string;
  lens_line_id: string;
  store_id: string;
  action: string;
  delta_on_hand: number;
  delta_reserved: number;
  prior?: { on_hand: number; reserved: number };
  after?: { on_hand: number; reserved: number };
  source_type?: string | null;
  source_id?: string | null;
  by_user_id?: string;
  by_user_name?: string;
  notes?: string | null;
  at?: string;
}

export interface LensStockAuditResponse {
  audit: LensStockAuditRow[];
  total: number;
}

// ----------------------------------------------------------------------------
// API
// ----------------------------------------------------------------------------

export const lensStockApi = {
  /** Full per-cell matrix for a lens line at a store. Backend returns
   *  cells the FE turns into the (sph x cyl[, add]) grid. */
  matrix: async (lensLineId: string, storeId?: string): Promise<LensStockMatrixResponse> => {
    const res = await api.get(`/lens-stock/${encodeURIComponent(lensLineId)}`, {
      params: storeId ? { store_id: storeId } : {},
    });
    return res.data as LensStockMatrixResponse;
  },

  /** Single cell by line_stock_id (id of the row in lens_stock_lines). */
  cell: async (lineStockId: string): Promise<LensStockCellResponse> => {
    const res = await api.get(`/lens-stock/cell/${encodeURIComponent(lineStockId)}`);
    return res.data as LensStockCellResponse;
  },

  /** Cells where (on_hand - reserved) < reorder_point at the active store. */
  gapPlanner: async (storeId?: string): Promise<LensGapPlannerResponse> => {
    const res = await api.get('/lens-stock/gap-planner', {
      params: storeId ? { store_id: storeId } : {},
    });
    return res.data as LensGapPlannerResponse;
  },

  /** Adjustment history for a single cell. */
  audit: async (lineStockId: string, limit = 100): Promise<LensStockAuditResponse> => {
    const res = await api.get(`/lens-stock/audit/${encodeURIComponent(lineStockId)}`, {
      params: { limit },
    });
    return res.data as LensStockAuditResponse;
  },
};
