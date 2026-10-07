// Quick Add - ONLINE. Whether the product goes to the website is the BRAND's
// default (Settings > Brand Master), never a per-product switch (owner ruling
// 2026-09-29, D6): the strip says what the brand decides, read-only. No
// Shopify tags box (POST /products never stored them) and no Shopify POS
// switch (IMS is the till). Hidden in review mode - the imported doc's real
// online status is in the banner.

import { Globe } from 'lucide-react';
import type { QuickAddForm } from './useQuickAddForm';

export function OnlineStrip({ form }: { form: QuickAddForm }) {
  const { isReviewMode, attributes, brandGoesOnline } = form;
  const brand = (attributes.brand_name || '').trim();
  const goesOnline = brandGoesOnline(brand);

  return (
    <>
      {!isReviewMode && (
      <div className="rounded-lg border border-gray-200 bg-white px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <span className="flex items-center gap-1.5 text-sm font-semibold text-gray-900">
            <Globe className="w-4 h-4 text-bv" />
            Online
          </span>
          <p className="text-sm text-gray-700">
            {!brand
              ? 'Website: decided by the brand default (Settings > Brand Master).'
              : goesOnline === undefined
                ? `Website: no - ${brand} has no brand default in Settings > Brand Master.`
                : `Website: ${goesOnline ? 'yes' : 'no'} - ${brand}'s brand default (Settings > Brand Master).`}
          </p>
        </div>
      </div>
      )}
    </>
  );
}
