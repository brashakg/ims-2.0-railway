// ============================================================================
// IMS 2.0 - Order Shipping Card (Shiprocket)
// ============================================================================
// Compact, additive shipping panel for the order detail modal. Lists shipments
// booked for the order and offers a "Book shipment" action + per-shipment
// tracking refresh. Bookings are SIMULATED server-side unless DISPATCH_MODE=live
// and Shiprocket creds are configured, so this is always safe to click.
//
// COURIER PAYMENT. This card used to post {order_id, store_id} only, which the
// server reads as Prepaid - and Prepaid on an order with nothing paid is a hard
// refusal, so a web COD order (imported UNPAID, the whole bill still owed) could
// not be shipped from the product at all. The choice is now explicit, and it
// defaults to COD exactly in that case. On COD the courier collects the
// BALANCE, so the card states the figure before anyone books.

import { useCallback, useEffect, useState } from 'react';
import { Truck, Package, RefreshCw, ExternalLink, Loader2 } from 'lucide-react';
import { shippingApi, type Shipment } from '../../services/api/shipping';
import { ApiError } from '../../services/api/client';
import { useToast } from '../../context/ToastContext';

interface OrderShippingCardProps {
  orderId: string;
  orderNumber: string;
  storeId?: string;
  /** What the order still owes - what a COD courier will collect. Undefined
   *  on a legacy/imported row; the server then reads the whole bill as owed. */
  balanceDue?: number;
  /** The bill - what the server collects when balanceDue is not recorded. */
  grandTotal?: number;
  /** UNPAID / PARTIAL / PAID ... - drives the default choice. */
  paymentStatus?: string;
}

type CourierPayment = 'COD' | 'Prepaid';

/** The 409 body: the parcel already out for this order. */
interface ExistingShipment {
  shipment_id?: string;
  awb?: string | null;
  status?: string;
  message?: string;
}

const money = (amount: number) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(Math.round(amount || 0));

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

export function OrderShippingCard({
  orderId,
  orderNumber,
  storeId,
  balanceDue,
  grandTotal,
  paymentStatus,
}: OrderShippingCardProps) {
  const toast = useToast();
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [loading, setLoading] = useState(false);
  const [booking, setBooking] = useState(false);
  const [trackingId, setTrackingId] = useState<string | null>(null);
  const [existing, setExisting] = useState<ExistingShipment | null>(null);

  // The server refuses a COD booking with nothing to collect, and refuses a
  // Prepaid one on an order with no payment at all. So: nothing paid + money
  // owed is the COD case, everything else is Prepaid. The card does NOT
  // guess when the balance is not recorded: the server reads such a row as
  // owing the whole bill, so COD stays offerable and the figure shown is the
  // bill (or, with neither figure, whatever the server confirms).
  const collectable = balanceDue ?? grandTotal;
  const codDisabled = balanceDue !== undefined && balanceDue <= 0;
  const defaultMethod: CourierPayment =
    (paymentStatus ?? 'UNPAID') === 'UNPAID' && !codDisabled ? 'COD' : 'Prepaid';
  const [method, setMethod] = useState<CourierPayment>(defaultMethod);
  // The modal reuses this card across orders, so re-seed on a new order.
  useEffect(() => {
    setMethod(defaultMethod);
    setExisting(null);
  }, [orderId, defaultMethod]);

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

  // rebook = the user has confirmed against the named existing shipment (a
  // courier no-show / split parcel); without it the server answers 409.
  const handleBook = async (rebook = false) => {
    setBooking(true);
    setExisting(null);
    try {
      const res = await shippingApi.book({
        order_id: orderId,
        store_id: storeId,
        address: { payment_method: method },
        ...(rebook ? { rebook: true } : {}),
      });
      if (res.simulated) {
        toast.info(res.message || 'Shipment simulated (not dispatched live)');
      } else if (res.status === 'FAILED') {
        toast.error(res.message || 'Shipment booking failed');
      } else {
        toast.success(res.message || 'Shipment booked');
      }
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.code === 'SHIPMENT_ALREADY_BOOKED') {
        const detail = (err.detail ?? {}) as ExistingShipment;
        setExisting({ ...detail, message: detail.message || err.message });
        return;
      }
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
          onClick={() => handleBook()}
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

      {/* 409: a parcel is already out. Re-booking is a confirmed act - the
          courier would otherwise be told to collect the same balance twice. */}
      {existing && (
        <div
          role="alert"
          className="mb-3 border border-amber-200 bg-amber-50 rounded-lg p-2.5 text-xs text-amber-900"
        >
          <p>
            Shipment <span className="font-medium">{existing.awb || existing.shipment_id}</span>
            {existing.status ? ` (${existing.status})` : ''} is already out for this order.
            Book again only if that parcel is not coming.
          </p>
          <div className="flex items-center gap-3 mt-2">
            <button
              type="button"
              onClick={() => handleBook(true)}
              disabled={booking}
              className="min-h-[44px] sm:min-h-0 inline-flex items-center text-xs font-medium text-amber-900 bg-white hover:bg-amber-100 border border-amber-300 rounded-lg px-2.5 py-1 disabled:opacity-60"
            >
              Book again anyway
            </button>
            <button
              type="button"
              onClick={() => setExisting(null)}
              className="text-xs text-gray-600 hover:text-gray-900"
            >
              Keep the existing shipment
            </button>
          </div>
        </div>
      )}

      {/* Courier payment - COD collects the balance, Prepaid collects nothing */}
      <fieldset className="mb-3">
        <legend className="text-xs text-gray-500 mb-1.5">Courier payment</legend>
        <div className="grid grid-cols-2 gap-2">
          <label
            className={`flex items-center gap-2 min-h-[44px] px-2.5 rounded-lg border cursor-pointer transition-colors ${
              method === 'COD'
                ? 'border-blue-300 bg-blue-50'
                : 'border-gray-200 hover:bg-gray-50'
            } ${codDisabled ? 'opacity-60 cursor-not-allowed' : ''}`}
          >
            <input
              type="radio"
              name={`courier-payment-${orderId}`}
              value="COD"
              checked={method === 'COD'}
              disabled={codDisabled}
              onChange={() => setMethod('COD')}
              className="w-4 h-4"
            />
            <span className="text-xs leading-tight">
              <span className="block font-medium text-gray-900">COD</span>
              <span className="block text-gray-500">
                {codDisabled
                  ? 'Nothing to collect'
                  : collectable !== undefined && collectable > 0
                    ? `Collect ${money(collectable)}`
                    : 'Amount confirmed by the server'}
              </span>
            </span>
          </label>
          <label
            className={`flex items-center gap-2 min-h-[44px] px-2.5 rounded-lg border cursor-pointer transition-colors ${
              method === 'Prepaid'
                ? 'border-blue-300 bg-blue-50'
                : 'border-gray-200 hover:bg-gray-50'
            }`}
          >
            <input
              type="radio"
              name={`courier-payment-${orderId}`}
              value="Prepaid"
              checked={method === 'Prepaid'}
              onChange={() => setMethod('Prepaid')}
              className="w-4 h-4"
            />
            <span className="text-xs leading-tight">
              <span className="block font-medium text-gray-900">Prepaid</span>
              <span className="block text-gray-500">Courier collects nothing</span>
            </span>
          </label>
        </div>
      </fieldset>

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
