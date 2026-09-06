// Quick Add - ONLINE. The compact always-visible Shopify strip (sync switch,
// the POS switch that says what it waits on, and the tag box). Hidden in
// review mode - the imported doc's real online status is in the banner and
// these create-time flags are not part of the review PUT.
// MOVED verbatim out of QuickAddPage.tsx (Wave 3 file diet).

import { Globe, X } from 'lucide-react';
import clsx from 'clsx';
import type { QuickAddForm } from './useQuickAddForm';

export function OnlineStrip({ form }: { form: QuickAddForm }) {
  const {
    isReviewMode, syncToShopify, setSyncToShopify, publishPOS, setPublishPOS,
    shopifyTags, setShopifyTags,
  } = form;

  return (
    <>
      {!isReviewMode && (
      <div className="rounded-lg border border-gray-200 bg-white px-4 py-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <span className="flex items-center gap-1.5 text-sm font-semibold text-gray-900">
            <Globe className="w-4 h-4 text-bv" />
            Online
          </span>
          <label className="flex min-h-10 items-center gap-2 cursor-pointer">
            <span className="relative inline-flex items-center">
              <input
                type="checkbox"
                title="Sync to Shopify"
                aria-label="Sync to Shopify"
                checked={syncToShopify}
                onChange={(e) => setSyncToShopify(e.target.checked)}
                className="sr-only peer"
              />
              <span className="w-9 h-5 bg-gray-300 rounded-full peer peer-checked:after:translate-x-4 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-bv"></span>
            </span>
            <span className="text-sm text-gray-800">Sync to Shopify</span>
          </label>
          <label className={clsx('flex min-h-10 items-center gap-2', syncToShopify ? 'cursor-pointer' : 'cursor-not-allowed')}>
            <span className="relative inline-flex items-center">
              <input
                type="checkbox"
                title="Publish to Shopify POS"
                aria-label="Publish to Shopify POS"
                checked={publishPOS}
                disabled={!syncToShopify}
                onChange={(e) => setPublishPOS(e.target.checked)}
                className="sr-only peer"
              />
              <span className={clsx(
                "w-9 h-5 rounded-full peer peer-checked:after:translate-x-4 after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-bv",
                syncToShopify ? 'bg-gray-300' : 'bg-gray-200'
              )}></span>
            </span>
            <span className={clsx('text-sm', syncToShopify ? 'text-gray-800' : 'text-gray-500')}>
              Publish to Shopify POS
            </span>
            {!syncToShopify && (
              <span className="text-xs text-gray-500">— turn on Sync to Shopify first</span>
            )}
          </label>
        </div>
        {syncToShopify && (
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
