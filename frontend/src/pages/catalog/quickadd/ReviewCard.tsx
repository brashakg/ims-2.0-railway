// Quick Add - the live Review card at the bottom of the page: every filled
// attribute named by the registry label the form itself shows, the prices,
// the HSN/GST promise and the derived discount band.
// MOVED verbatim out of QuickAddPage.tsx (Wave 3 file diet).

import { Sparkles } from 'lucide-react';
import { categoryName, fieldLabelFor } from '../productAddShared';
import { ReviewRow } from './parts';
import type { QuickAddForm } from './useQuickAddForm';

export function ReviewCard({ form }: { form: QuickAddForm }) {
  const {
    selectedCategory, attributes, mrp, offerPrice, canSeeCost, costPrice,
    weight, hsnCode, gstRate, hsnMatchesCategory, discountCategory, brandTiers,
    isReviewMode, reorderLevel, images, syncToShopify,
  } = form;

  return (
    <aside className="space-y-3">
      <div className="card">
        <div className="flex items-center gap-2 mb-3">
          <Sparkles className="w-4 h-4 text-bv" />
          <h3 className="font-semibold text-gray-900">Review</h3>
        </div>

        <dl className="grid grid-cols-1 tablet:grid-cols-2 laptop:grid-cols-3 gap-x-8 gap-y-1.5 text-sm">
          <ReviewRow label="Category" value={categoryName(selectedCategory) || '—'} />
          <ReviewRow label="Brand" value={attributes.brand_name || '—'} />
          <ReviewRow
            label="Model"
            value={attributes.model_no || attributes.model_name || '—'}
          />
          {/* Remaining filled category attributes (parity with the Guided
              wizard's review, which listed every filled attribute), named
              by the registry label the form itself uses -- never a raw key
              beside a curated one. */}
          {Object.entries(attributes)
            .filter(([k, v]) => v && !['brand_name', 'model_no', 'model_name'].includes(k))
            .map(([k, v]) => (
              <ReviewRow key={k} label={fieldLabelFor(selectedCategory, k)} value={v} />
            ))}
          <ReviewRow label="MRP" value={mrp ? `₹${mrp}` : '—'} />
          <ReviewRow
            label="Offer"
            value={offerPrice ? `₹${offerPrice}` : mrp ? `₹${mrp} (= MRP)` : '—'}
          />
          {canSeeCost && costPrice && <ReviewRow label="Cost" value={`₹${costPrice}`} />}
          {weight && <ReviewRow label="Weight" value={`${weight} g`} />}
          <ReviewRow
            label="HSN / GST"
            value={
              !selectedCategory
                ? '—'
                : `${hsnCode || '—'} · ${hsnMatchesCategory ? `${gstRate}%` : 'set from the HSN on save'}`
            }
          />
          <ReviewRow
            label="Discount band"
            value={
              discountCategory ||
              (attributes.brand_name && brandTiers[attributes.brand_name]
                ? `${brandTiers[attributes.brand_name]} (from Brand Master)`
                : 'Auto (Brand Master tier)')
            }
          />
          {!isReviewMode && <ReviewRow label="Reorder level" value={reorderLevel || '—'} />}
          {images.length > 0 && (
            <ReviewRow label="Images" value={`${images.length} uploaded`} />
          )}
          {syncToShopify && !isReviewMode && <ReviewRow label="Shopify" value="Will sync" />}
        </dl>
      </div>
    </aside>
  );
}
