// ============================================================================
// IMS 2.0 - Display Fixtures API (v2-2b)
// ============================================================================
// Typed client for the /api/v1/display-fixtures endpoints shipped in v2-2a
// (PR #275). The owner uses these to declare every physical fixture in a
// store (wall, counter, pillar, locked cabinet, drawer, fridge, etc.) so the
// inventory system can place each SKU on a known spot.
//
// IMPORTANT: import this DIRECTLY, not through the services/api barrel:
//   import { displayFixturesApi } from '../../services/api/displayFixtures';
// The barrel re-export triggers a TS2614 resolution issue we've hit on other
// newly-added services (see CLAUDE.md gotcha for returnsApi / shippingApi).

import api from './client';

export type FixtureType =
  | 'window'
  | 'wall'
  | 'pillar'
  | 'counter'
  | 'cabinet'
  | 'gondola'
  | 'drawer'
  | 'fridge';

export type FixtureFloor = 'ground' | 'storage' | 'clinic';
export type FixtureZone = 'A' | 'B' | 'C' | '-';
export type CatalogMerchType = 'Frame' | 'Lens' | 'CL' | 'Access.';

export interface DisplayFixture {
  fixture_id: string;
  store_id: string;
  code: string;
  name: string;
  type: FixtureType;
  floor: FixtureFloor;
  zone: FixtureZone;
  capacity: number;
  lockable: boolean;
  merch: CatalogMerchType[];
  last_audit_at?: string | null;
  mannequin?: boolean;
  spotlit?: boolean;
  temp_ctrl?: string | null;
  no_qr?: boolean;
  key_holder?: string | null;
  is_active: boolean;
  notes?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface FixtureListParams {
  store_id?: string;
  type?: FixtureType;
  floor?: FixtureFloor;
  zone?: FixtureZone;
  active?: boolean;
}

export interface FixtureListResponse {
  fixtures: DisplayFixture[];
  total: number;
}

export interface FixtureMetaOptions {
  types: string[];
  floors: string[];
  zones: string[];
  catalog_types: string[];
}

// Payload accepted by create / update. fixture_id is omitted; the backend
// derives it from `code` on create and refuses to mutate it on update.
export type FixturePayload = Partial<Omit<DisplayFixture, 'fixture_id' | 'created_at' | 'updated_at'>>;

export const displayFixturesApi = {
  list: async (params?: FixtureListParams): Promise<FixtureListResponse> => {
    const response = await api.get('/display-fixtures', { params });
    return response.data as FixtureListResponse;
  },

  get: async (fixture_id: string): Promise<DisplayFixture> => {
    const response = await api.get(`/display-fixtures/${fixture_id}`);
    // Backend wraps in { fixture: {...} }
    return (response.data?.fixture ?? response.data) as DisplayFixture;
  },

  create: async (payload: FixturePayload): Promise<{ fixture_id: string; fixture: DisplayFixture }> => {
    const response = await api.post('/display-fixtures', payload);
    const fixture = (response.data?.fixture ?? response.data) as DisplayFixture;
    return { fixture_id: fixture.fixture_id, fixture };
  },

  update: async (fixture_id: string, payload: FixturePayload): Promise<{ message: string; fixture?: DisplayFixture }> => {
    const response = await api.patch(`/display-fixtures/${fixture_id}`, payload);
    return {
      message: (response.data?.status as string) ?? 'updated',
      fixture: response.data?.fixture as DisplayFixture | undefined,
    };
  },

  softDelete: async (fixture_id: string): Promise<{ message: string }> => {
    const response = await api.delete(`/display-fixtures/${fixture_id}`);
    return { message: (response.data?.status as string) ?? 'deleted' };
  },

  metaOptions: async (): Promise<FixtureMetaOptions> => {
    const response = await api.get('/display-fixtures/meta/options');
    return response.data as FixtureMetaOptions;
  },
};
