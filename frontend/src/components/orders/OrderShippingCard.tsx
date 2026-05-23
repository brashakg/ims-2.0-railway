// ============================================================================
// IMS 2.0 - Order Shipping Card (Shiprocket)
// ============================================================================
// Compact, additive shipping panel for the order detail modal. Lists shipments
// booked for the order and offers a "Book shipment" action + per-shipment
// tracking refresh. Bookings are SIMULATED server-side unless DISPATCH_MODE=live
// and Shiprocket creds are configured, so this is always safe to click.

import { useCallback, useEffect, useState } from 'react';
import { Truck, Package, RefreshCw, ExternalLink, Loader2 } from 'lucide-react';
import { shippingApi } from '../../services/api';
import type { Shipment } from '../../services/api/shipping';
import { useToast } from '../../context/ToastContext';

interface OrderShippingCardProps {
  orderId: string;
  orderNumber: string;
  storeId?: string;
}

function statusChipClasses(status?: string | null): string {
  const s = (status || '').toUpperCase();
  if (s === 'BOOKED' || s === 'DELIVERED') {
    return 'text-green-700 bg-green-50 border-green-200';
  }
  if (s === 'FAILED') {
    return 'text-red-700 bg-red-50 border-red-200';
  }
  if (s === 'SIMULATED') {
    return 'text-amber-700 bg-amber-50 border-amber-200';
  }
  return 'text-blue-700 bg-blue-50 border-blue-200';
}

export function OrderShippingCard({ orderId, orderNumber, storeId }: OrderShippingCardProps) {
  const toast = useToast();
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [loading, setLoading] = useState(false);
  const [booking, setBooking] = useState(false);
  const [trackingId, setTrackingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!orderId) return;
    setLoading(true);
    try {
      const res = await shippingApi.list({ order_id: orderId, store_id: storeId });
      setShipments(res.shipments || []);
    } catch {
      // Fail-soft: leave the list empty; never block the modal.
      setShipments([]);
    } finally {
      setLoading(false);
    }
  }, [orderId, storeId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleBook = async () => {
    setBooking(true);
    try {
      const res = await shippingApi.book({ order_id: orderId, store_id: storeId });
      if (res.simulated) {
        toast.info(res.message || 'Shipment simulated (not dispatched live)');
      } else if (res.status === 'FAILED') {
        toast.error(res.message || 'Shipment booking failed');
      } else {
        toast.success(res.message || 'Shipment booked');
      }
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to book shipment');
    } finally {
      setBooking(false);
    }
  };

  const handleTrack = async (shipmentId: string) => {
    setTrackingId(shipmentId);
    try {
      const res = await shippingApi.track(shipmentId);
      const label = res.tracking_status || 'Unknown';
      if (res.live) {
        toast.success(`Tracking: ${label}`);
      } else {
        toast.info(`${label} (${res.message || 'last-known status'})`);
      }
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to track shipment');
    } finally {
      setTrackingId(null);
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <h3 className="font-medium text-gray-900 text-sm flex items-center gap-1.5">
          <Truck className="w-4 h-4" />
          Shipping
        </h3>
        <button
          type="button"
          onClick={handleBook}
          disabled={booking}
          className="inline-flex items-center gap-1 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200 rounded-lg px-2.5 py-1 transition-colors disabled:opacity-60"
        >
          {booking ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <Package className="w-3 h-3" />
          )}
          Book shipment
        </button>
      </div>
      <p className="text-xs text-gray-500 mb-3">Shiprocket &middot; #{orderNumber}</p>

      {/* Shipment list */}
      {loading ? (
        <p className="text-xs text-gray-500 flex items-center gap-1.5">
          <Loader2 className="w-3 h-3 animate-spin" /> Loading shipments...
        </p>
      ) : shipments.length === 0 ? (
        <p className="text-xs text-gray-500">
          No shipments yet. Book one to generate an AWB and tracking link.
        </p>
      ) : (
        <ul className="space-y-2">
          {shipments.map((s) => (
            <li
              key={s.shipment_id}
              className="border border-gray-100 rounded-lg p-2.5 bg-gray-50/60"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-gray-900">
                  {s.awb || s.shipment_id}
                </span>
                <span
                  className={`inline-flex items-center text-xs border rounded-full px-2 py-0.5 ${statusChipClasses(
                    s.tracking_status || s.status,
                  )}`}
                >
                  {s.tracking_status || s.status}
                </span>
              </div>
              <div className="flex items-center justify-between mt-1.5">
                <span className="text-xs text-gray-500">
                  {s.courier || (s.simulated ? 'Simulated' : 'Pending courier')}
                </span>
                <div className="flex items-center gap-2">
                  {s.tracking_url && (
                    <a
                      href={s.tracking_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-blue-700 hover:underline"
                    >
                      <ExternalLink className="w-3 h-3" />
                      Track link
                    </a>
                  )}
                  <button
                    type="button"
                    onClick={() => handleTrack(s.shipment_id)}
                    disabled={trackingId === s.shipment_id}
                    className="inline-flex items-center gap-1 text-xs font-medium text-gray-700 hover:text-gray-900 disabled:opacity-60"
                  >
                    {trackingId === s.shipment_id ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <RefreshCw className="w-3 h-3" />
                    )}
                    Refresh
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default OrderShippingCard;
