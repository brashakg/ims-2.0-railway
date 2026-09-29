// Quick Add - ONLINE. Whether the product goes to the website is the BRAND's
// default (Settings > Brand Master), never a per-product switch (owner ruling
// 2026-09-29, D6): the strip says what the brand decides, read-only, and the
// tag box shows only for a brand that goes online. No Shopify POS switch
// either (IMS is the till). Hidden in review mode - the imported doc's real
// online status is in the banner.

import { Globe, X } from 'lucide-react';
import type { QuickAddForm } from './useQuickAddForm';

export function OnlineStrip({ form }: { form: QuickAddForm }) {
  const { isReviewMode, attributes, brandSyncs, shopifyTags, setShopifyTags } = form;
  const brand = (attributes.brand_name || '').trim();
  const goesOnline = brandSyncs[brand];

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
        {goesOnline && (
          <div className="mt-2.5">
            <label className="block text-xs font-medium text-gray-700 mb-1" htmlFor="qa-shopify-tags">
              Shopify tags
            </label>
            <input
              id="qa-shopify-tags"
              type="text"
              className="input-field w-full"
              placeholder="Type a tag, press Enter or comma"
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ',') {
                  e.preventDefault();
                  const input = e.currentTarget;
                  const value = input.value.trim();
                  if (value && !shopifyTags.includes(value)) {
                    setShopifyTags([...shopifyTags, value]);
                    input.value = '';
                  }
                }
              }}
            />
            {shopifyTags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {shopifyTags.map((tag) => (
                  <span key={tag} className="inline-flex items-center px-2 py-0.5 text-xs bg-gray-100 rounded-full">
                    {tag}
                    <button
                      type="button"
                      onClick={() => setShopifyTags(shopifyTags.filter((t) => t !== tag))}
                      className="ml-1 text-gray-500 hover:text-gray-700"
                      aria-label={`Remove tag ${tag}`}
                      title={`Remove tag ${tag}`}
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      )}
    </>
  );
}
