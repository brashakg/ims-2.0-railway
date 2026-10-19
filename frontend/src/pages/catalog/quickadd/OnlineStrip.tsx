// Quick Add - ONLINE. Whether the product goes to the website is the BRAND's
// default (Settings > Brand Master), never a per-product switch (owner ruling
// 2026-09-29, D6): the strip shows the push gate's own verdict for the brand,
// and its reason, read-only (form.websiteVerdict). No
// Shopify tags box (POST /products never stored them) and no Shopify POS
// switch (IMS is the till). Hidden in review mode - the imported doc's real
// online status is in the banner.
//
// Website page (owner 2026-10-19): Standard / Pre-order (+ 'Ships in about N
// days') / Preview only -- one per MODEL (product_master.MODEL_LEVEL_FIELDS),
// so a colour/size of a model shows the model's and is edited on the model.

import { Globe } from 'lucide-react';
import { WEB_PAGE_TYPES } from '../productAddShared';
import { LockChip } from './parts';
import type { QuickAddForm } from './useQuickAddForm';

export function OnlineStrip({ form }: { form: QuickAddForm }) {
  const { isReviewMode, attributes, websiteVerdict, setAttr, errors, editMode, variantCtx } = form;
  const brand = (attributes.brand_name || '').trim();
  const pageType = attributes.web_page_type || 'STANDARD';
  const onModel = Boolean(variantCtx) || (editMode?.kind === 'spine' && Boolean(editMode.variantOf));

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
        <div className="mt-3 flex flex-wrap items-start gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1" htmlFor="qa-field-web_page_type">
              Website page
              {onModel && <LockChip text="edit on the model" />}
            </label>
            <select
              id="qa-field-web_page_type"
              value={pageType}
              onChange={(e) => setAttr('web_page_type', e.target.value)}
              disabled={onModel}
              className="input-field w-48"
            >
              {WEB_PAGE_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
            {errors.web_page_type && <p className="text-red-500 text-xs mt-1">{errors.web_page_type}</p>}
          </div>
          {pageType === 'PRE_ORDER' && (
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1" htmlFor="qa-field-preorder_ship_days">
                Ships in about (days)<span className="text-red-500 ml-1">*</span>
              </label>
              <input
                id="qa-field-preorder_ship_days"
                type="number"
                min={1}
                max={365}
                step={1}
                value={attributes.preorder_ship_days || ''}
                onChange={(e) => setAttr('preorder_ship_days', e.target.value)}
                disabled={onModel}
                placeholder="e.g. 10"
                className="input-field w-32"
              />
              {errors.preorder_ship_days && (
                <p className="text-red-500 text-xs mt-1">{errors.preorder_ship_days}</p>
              )}
            </div>
          )}
          <p className="text-xs text-gray-500 max-w-sm self-center">
            {pageType === 'PRE_ORDER'
              ? 'Sells on the website at 0 stock with a Pre-order button.'
              : pageType === 'PREVIEW_ONLY'
                ? 'No buy button: Enquire on WhatsApp and See it in store.'
                : 'The normal page: sells only what is in stock.'}
          </p>
        </div>
      </div>
      )}
    </>
  );
}
