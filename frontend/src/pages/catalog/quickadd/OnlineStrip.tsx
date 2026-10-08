// Quick Add - ONLINE. Whether the product goes to the website is the BRAND's
// default (Settings > Brand Master), never a per-product switch (owner ruling
// 2026-09-29, D6): the strip shows the push gate's own verdict for the brand,
// and its reason, read-only (form.websiteVerdict). No
// Shopify tags box (POST /products never stored them) and no Shopify POS
// switch (IMS is the till). Hidden in review mode - the imported doc's real
// online status is in the banner.

import { Globe } from 'lucide-react';
import type { QuickAddForm } from './useQuickAddForm';

export function OnlineStrip({ form }: { form: QuickAddForm }) {
  const { isReviewMode, attributes, websiteVerdict } = form;
  const brand = (attributes.brand_name || '').trim();

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
              : websiteVerdict === undefined
                ? 'Website: checking...'
                : websiteVerdict === null
                  ? 'Website: could not be checked just now - the push asks Brand Master again when it runs.'
                  : websiteVerdict.online
                    ? `Website: yes - ${brand}'s brand default (Settings > Brand Master).`
                    : `Website: no - ${websiteVerdict.reason || 'the push refuses it'}.`}
          </p>
        </div>
      </div>
      )}
    </>
  );
}
