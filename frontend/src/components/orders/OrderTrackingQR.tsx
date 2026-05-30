// ============================================================================
// IMS 2.0 — Order Tracking QR (staff-facing)
// ============================================================================
// Renders a scannable QR encoding the PUBLIC customer-tracking URL
//   `${origin}/track/{trackingToken}`
// so staff can show/print it on the invoice and the customer can scan to
// track their order without logging in.
//
// The QR image uses `qrcode.react`. If for some reason the token is missing
// we render nothing (the order predates tracking and the staff `get_order`
// backfill hasn't run, or the field didn't come through).

import { useMemo, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { QrCode, Copy, Check } from 'lucide-react';

interface OrderTrackingQRProps {
  /** The order's public tracking token (order.trackingToken / tracking_token). */
  trackingToken?: string | null;
  orderNumber?: string | null;
  /** Override the base URL (defaults to the current site origin or the
   *  VITE_FRONTEND_URL build var). */
  baseUrl?: string;
  size?: number;
}

function resolveBaseUrl(override?: string): string {
  if (override) return override.replace(/\/$/, '');
  const env = (import.meta.env.VITE_FRONTEND_URL as string | undefined)?.trim();
  if (env) return env.replace(/\/$/, '');
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin;
  }
  return 'https://ims-2-0-railway.vercel.app';
}

export function OrderTrackingQR({
  trackingToken,
  orderNumber,
  baseUrl,
  size = 132,
}: OrderTrackingQRProps) {
  const [copied, setCopied] = useState(false);

  const url = useMemo(() => {
    if (!trackingToken) return null;
    return `${resolveBaseUrl(baseUrl)}/track/${trackingToken}`;
  }, [trackingToken, baseUrl]);

  if (!url) return null;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be blocked (insecure context) — silently ignore.
    }
  };

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <p className="text-sm font-medium text-gray-900 mb-3 flex items-center gap-2">
        <QrCode className="w-4 h-4 text-gray-500" />
        Customer order-tracking link
      </p>
      <div className="flex items-center gap-4">
        <div className="shrink-0 rounded-lg border border-gray-100 p-2 bg-white">
          <QRCodeSVG value={url} size={size} level="M" includeMargin={false} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs text-gray-500">
            Scan to track {orderNumber ? `order ${orderNumber}` : 'this order'} — no
            login needed.
          </p>
          <div className="mt-2 text-xs text-gray-600 break-all bg-gray-50 border border-gray-200 rounded-md p-2">
            {url}
          </div>
          <button
            type="button"
            onClick={copy}
            className="mt-2 btn-outline text-xs inline-flex items-center gap-1.5"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
            {copied ? 'Copied' : 'Copy link'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default OrderTrackingQR;
