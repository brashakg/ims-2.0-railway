// ============================================================================
// IMS 2.0 - Display Placements API (v2-2b)
// ============================================================================
// Typed client for /api/v1/display-placements endpoints (v2-2a PR #275).
// One placement row = (SKU x fixture x store). A single SKU can have
// multiple placements (typically primary display + back-stock drawer).
//
// IMPORTANT: import directly, NOT through the services/api barrel.

import api from './client';

export interface DisplayPlacement {
  placement_id: string;
  sku: string;
  store_id: string;
  fixture_id: string;
  qty: number;
  position?: string | null;
  is_primary: boolean;
  last_moved_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface PlacementListParams {
  store_id?: string;
  sku?: string;
  fixture_id?: string;
}

export interface PlacementListResponse {
  placements: DisplayPlacement[];
  total: number;
}

export interface PlacementCreatePayload {
  sku: string;
  store_id: string;
  fixture_id: string;
  qty: number;
  position?: string;
  is_primary?: boolean;
  product_category?: string;
}

export interface PlacementUpdatePayload {
  qty?: number;
  position?: string;
  is_primary?: boolean;
}

export const displayPlacementsApi = {
  list: async (params?: PlacementListParams): Promise<PlacementListResponse> => {
    const response = await api.get('/display-placements', { params });
    return response.data as PlacementListResponse;
  },

  get: async (placement_id: string): Promise<DisplayPlacement> => {
    const response = await api.get(`/display-placements/${placement_id}`);
    return (response.data?.placement ?? response.data) as DisplayPlacement;
  },

  create: async (
    payload: PlacementCreatePayload,
  ): Promise<{ placement_id: string; placement: DisplayPlacement; stacked?: boolean }> => {
    const response = await api.post('/display-placements', payload);
    const placement = (response.data?.placement ?? response.data) as DisplayPlacement;
    return {
      placement_id: placement.placement_id,
      placement,
      stacked: response.data?.stacked === true,
    };
  },

  update: async (
    placement_id: string,
    payload: PlacementUpdatePayload,
  ): Promise<{ message: string; placement?: DisplayPlacement }> => {
    const response = await api.patch(`/display-placements/${placement_id}`, payload);
    return {
      message: (response.data?.status as string) ?? 'updated',
      placement: response.data?.placement as DisplayPlacement | undefined,
    };
  },

  delete: async (placement_id: string): Promise<{ message: string }> => {
    const response = await api.delete(`/display-placements/${placement_id}`);
    return { message: (response.data?.status as string) ?? 'deleted' };
  },

  move: async (
    placement_id: string,
    target_fixture_id: string,
  ): Promise<{ message: string; placement?: DisplayPlacement; stacked?: boolean }> => {
    const response = await api.post('/display-placements/move', {
      placement_id,
      target_fixture_id,
    });
    return {
      message: (response.data?.status as string) ?? 'moved',
      placement: response.data?.placement as DisplayPlacement | undefined,
      stacked: response.data?.stacked === true,
    };
  },
};
