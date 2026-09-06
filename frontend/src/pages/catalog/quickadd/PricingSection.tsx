// Quick Add - PRICING. MRP / Offer / Cost (cost is role-gated, F35) with the
// live discount + margin readouts. The discount band is NOT picked here - the
// backend derives it from the Brand Master tier.
// MOVED verbatim out of QuickAddPage.tsx (Wave 3 file diet).

import { IndianRupee } from 'lucide-react';
import clsx from 'clsx';
import { ConfirmChip, Section } from './parts';
import type { QuickAddForm } from './useQuickAddForm';

export function PricingSection({ form }: { form: QuickAddForm }) {
  const {
    mrp, setMrp, offerPrice, setOfferPrice, costPrice, setCostPrice,
    errors, flaggedFields, clearFlag, canSeeCost,
    openSections, toggleSection, sectionIssues,
  } = form;

  const offerNum = parseFloat(offerPrice);
  const mrpNum = parseFloat(mrp);
  const costNum = parseFloat(costPrice);
  const discountPct =
    offerPrice && mrp && Number.isFinite(offerNum) && Number.isFinite(mrpNum) && offerNum < mrpNum
      ? Math.round(((mrpNum - offerNum) / mrpNum) * 100)
      : null;
  const marginPct =
    costPrice && mrp && Number.isFinite(costNum) && Number.isFinite(mrpNum) && mrpNum > 0
      ? Math.round(((mrpNum - costNum) / mrpNum) * 100)
      : null;

  return (
    <Section
      id="pricing"
      title="Pricing"
      icon={<IndianRupee className="w-5 h-5" />}
      subtitle="MRP, offer & cost (discount band auto-derives from Brand Master)"
      open={openSections.pricing}
      issues={sectionIssues.pricing}
      onToggle={toggleSection}
    >
      <div className="grid grid-cols-1 tablet:grid-cols-2 laptop:grid-cols-3 desktop:grid-cols-4 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-700 mb-1" htmlFor="qa-field-mrp">
            MRP <span className="text-red-500">*</span>
            {flaggedFields.has('mrp') && <ConfirmChip />}
          </label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">₹</span>
            <input
              id="qa-field-mrp"
              type="number"
              value={mrp}
              onChange={(e) => { setMrp(e.target.value); clearFlag('mrp'); }}
              className={clsx(
                'input-field w-full pl-8',
                flaggedFields.has('mrp') && 'ring-1 ring-amber-400 bg-amber-50/60'
              )}
              placeholder="0.00"
            />
          </div>
          {errors.mrp && <p className="text-red-500 text-xs mt-1">{errors.mrp}</p>}
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-700 mb-1" htmlFor="qa-field-offer_price">
            Offer Price
            {flaggedFields.has('offer_price') && <ConfirmChip />}
          </label>
          <div className="relative">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">₹</span>
            <input
              id="qa-field-offer_price"
              type="number"
              value={offerPrice}
              onChange={(e) => { setOfferPrice(e.target.value); clearFlag('offer_price'); }}
              className={clsx(
                'input-field w-full pl-8',
                flaggedFields.has('offer_price') && 'ring-1 ring-amber-400 bg-amber-50/60'
              )}
              placeholder="Same as MRP if blank"
            />
          </div>
          {errors.offer_price && <p className="text-red-500 text-xs mt-1">{errors.offer_price}</p>}
          {discountPct !== null && (
            <p className="text-green-600 text-xs mt-1">{discountPct}% discount</p>
          )}
        </div>

        {canSeeCost && (
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Cost Price
              {flaggedFields.has('cost_price') && <ConfirmChip />}
            </label>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500">₹</span>
              <input
                type="number"
                value={costPrice}
                onChange={(e) => { setCostPrice(e.target.value); clearFlag('cost_price'); }}
                className={clsx(
                  'input-field w-full pl-8',
                  flaggedFields.has('cost_price') && 'ring-1 ring-amber-400 bg-amber-50/60'
                )}
                placeholder="Your purchase cost"
              />
            </div>
            {marginPct !== null && (
              <p className="text-bv text-xs mt-1">Margin: {marginPct}%</p>
            )}
          </div>
        )}

        {/* Discount tier is NOT picked per product any more (owner rule:
            it is already set brand-wise + category-wise in Settings).
            The backend derives it: category force (HA/Services) > the
            brand's Brand Master tier. The derived band shows read-only
            in the Review below. */}
      </div>
    </Section>
  );
}
