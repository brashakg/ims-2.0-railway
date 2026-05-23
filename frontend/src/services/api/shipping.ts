// ============================================================================
// IMS 2.0 - Shipping (Shiprocket) API
// ============================================================================
// Book + track customer shipments via Shiprocket. Bookings are SIMULATED
// server-side unless DISPATCH_MODE=live AND Shiprocket credentials are set, so
// this is always safe to call.

import api from './client';

export type ShipmentStatus = 'BOOKED' | 'SIMULATED' | 'FAILED' | 'TRACKED';

export interface ShipAddressPayload {
  name?: string;
  last_name?: string;
  address?: string;
  city?: string;
  state?: string;
  pincode?: string;
  country?: string;
  phone?: string;
  email?: string;
  payment_method?: string;
  weight?: number;
  length?: number;
  breadth?: number;
  height?: number;
}

export interface BookShipmentPayload {
  order_id: string;
  store_id?: string;
  pickup_location?: string;
  address?: ShipAddressPayload;
}

export interface Shipment {
  shipment_id: string;
  order_id: string;
  order_number?: string;
  customer_name?: string;
  store_id?: string;
  provider?: string;
  awb?: string | null;
  courier?: string | null;
  label_url?: string | null;
  tracking_status?: string | null;
  tracking_url?: string | null;
  status: ShipmentStatus;
  simulated?: boolean;
  created_at?: string;
}

export interface BookShipmentResponse {
  shipment_id: string;
  order_id: string;
  status: ShipmentStatus;
  simulated: boolean;
  awb?: string | null;
  courier?: string | null;
  label_url?: string | null;
  tracking_status?: string | null;
  tracking_url?: string | null;
  message: string;
}

export interface TrackResponse {
  shipment_id: string;
  order_id?: string;
  awb?: string | null;
  courier?: string | null;
  tracking_status?: string | null;
  tracking_url?: string | null;
  live: boolean;
  source: 'shiprocket' | 'last_known';
  message?: string | null;
}

export const shippingApi = {
  book: async (payload: BookShipmentPayload): Promise<BookShipmentResponse> => {
    const response = await api.post('/shipping/shipments', payload);
    return response.data;
  },

  list: async (params?: {
    order_id?: string;
    store_id?: string;
    skip?: number;
    limit?: number;
  }): Promise<{ shipments: Shipment[]; total: number }> => {
    const response = await api.get('/shipping/shipments', { params });
    return response.data;
  },

  track: async (shipmentId: string): Promise<TrackResponse> => {
    const response = await api.get(`/shipping/shipments/${shipmentId}/track`);
    return response.data;
  },
};
