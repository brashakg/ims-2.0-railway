// ============================================================================
// IMS 2.0 - Online order facts (Website 1)
// ============================================================================
// Two things an online (Shopify) order carries that an in-store one does not:
// the delivery address typed at checkout, and the check that the IMS invoice
// total is what Shopify charged. A MISMATCH is flagged here (and tasked to an
// admin server-side when it is booked live) - never silently accepted.
// Renders nothing otherwise.

import { AlertCircle } from 'lucide-react';
import type { ShippingAddress, ShopifyTotalsCheck } from '../../types';
import { formatCurrency } from '../../pages/finance/financeUtils';

interface OnlineOrderFactsProps {
  shippingAddress?: ShippingAddress | null;
  totalsCheck?: ShopifyTotalsCheck;
}

const join = (parts: (string | undefined)[], sep = ', ') =>
  parts.filter((p) => p && p.trim()).join(sep);

export function OnlineOrderFacts({ shippingAddress: a, totalsCheck }: OnlineOrderFactsProps) {
  const mismatch = totalsCheck?.status === 'MISMATCH';
  if (!a && !mismatch) return null;
  const place = join([join([a?.city, a?.province]), a?.zip], ' ');
  return (
    <>
      {mismatch && (
        <div
          role="alert"
          className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-start gap-2"
        >
          <AlertCircle className="w-4 h-4 text-red-700 mt-0.5 flex-shrink-0" />
          <p className="text-sm text-red-800">
            <span className="font-medium">Total differs from Shopify.</span> Shopify charged{' '}
            {formatCurrency(totalsCheck?.shopifyTotal ?? 0)}; this invoice is{' '}
            {formatCurrency(totalsCheck?.imsTotal ?? 0)} (difference{' '}
            {formatCurrency(totalsCheck?.difference ?? 0)}). Flagged for an admin to check
            before it ships or is filed.
          </p>
        </div>
      )}
      {a && (
        <div>
          <p className="text-sm text-gray-500">Ship to</p>
          <p className="font-medium">{a.name || join([a.first_name, a.last_name], ' ')}</p>
          {a.company && <p className="text-sm text-gray-500">{a.company}</p>}
          <p className="text-sm text-gray-500">{join([a.address1, a.address2])}</p>
          {place && <p className="text-sm text-gray-500">{place}</p>}
          {a.phone && <p className="text-sm text-gray-500">{a.phone}</p>}
        </div>
      )}
    </>
  );
}

export default OnlineOrderFacts;
