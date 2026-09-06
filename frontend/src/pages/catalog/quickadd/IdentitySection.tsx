// Quick Add - IDENTITY. The category tiles, the review-only name/tags pair,
// the OWNER-LOCKED 27-field grid (2026-07-04) with Weight injected after
// Warranty and the similar-products strip under the model field, the lens
// Power Grid pointer, Description (+ the AI draft) and Advanced HSN & GST.
// MOVED verbatim out of QuickAddPage.tsx (Wave 3 file diet): the field loop
// travelled as ONE unit, injections intact, and nothing about the order,
// the copy or the classes changed.

import { Fragment, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ChevronDown,
  Info,
  Loader2,
  Sparkles,
  Sparkles as SparklesIcon,
  Tag,
  X,
} from 'lucide-react';
import clsx from 'clsx';
import { useToast } from '../../../context/ToastContext';
import { productApi } from '../../../services/api/products';
import { hsnOptions } from '../../../constants/gstRuntime';
import {
  CAPS_ENTRY_FIELDS,
  CATEGORIES,
  getCategoryFields,
  type CategoryField,
} from '../productAddShared';
import { SimilarProductsHint } from '../SimilarProductsHint';
import { ConfirmChip, LockChip, Section } from './parts';
import type { QuickAddForm } from './useQuickAddForm';

export function IdentitySection({ form }: { form: QuickAddForm }) {
  const toast = useToast();
  const {
    registryReady, selectedCategory, setSelectedCategory, attributes, setAttr,
    description, setDescription, hsnCode, setHsnCode, gstRate, setGstRate,
    hsnMatchesCategory, weight, setWeight, displayName, setDisplayName,
    reviewTags, setReviewTags, subbrandsByBrand, errors, showAdvanced,
    setShowAdvanced, openSections, toggleSection, liveErrors, sectionIssues,
    firstFieldRef, editMode, isReviewMode, variantCtx, flaggedFields, clearFlag,
    reviewSnapshot, handleSimilarPick, handleSimilarOpen,
  } = form;
  // "Auto-fill with AI" (description) round-trip in flight.
  const [generatingDesc, setGeneratingDesc] = useState(false);

  // Field list with required flags from the canonical registry (registryReady
  // is a render dependency so the markers update when the registry arrives).
  void registryReady;
  const fields: CategoryField[] = selectedCategory ? getCategoryFields(selectedCategory) : [];
  const isLens = selectedCategory === 'LS';
  // Eyewear (SG/FR): the Weight input renders inline in the attribute grid
  // (between Warranty and UPC — owner-locked order) instead of under Advanced.
  const isEyewear = selectedCategory === 'SG' || selectedCategory === 'FR';
  // The lens stock-power fields are entered via the Power Grid, not here.
  const lensPowerFields = new Set(['sph', 'cyl', 'axis', 'add']);
  const visibleFields = (isLens ? fields.filter((f) => !lensPowerFields.has(f.name)) : fields)
    // Sub Brand follows the SELECTED brand: when that brand has sub-brands in
    // the Brand Master the field becomes a select restricted to them (the
    // server enforces the same rule); a brand without sub-brands (or no brand
    // picked yet) keeps the free-text input.
    .map((f) => {
      if (f.name !== 'subbrand') return f;
      const subs = subbrandsByBrand[attributes.brand_name || ''];
      return subs && subs.length > 0 ? { ...f, type: 'select' as const, options: subs } : f;
    });

  // Similar-products strip anchor: it renders under the category's MODEL
  // field (model_no for eyewear/watches, model_name for the categories keyed
  // on it). null when the category has neither (e.g. SERVICES-like sets) —
  // and in REVIEW mode, where its pick-a-sibling/variant actions would blow
  // away the imported doc being reviewed (variant is a create affordance).
  const similarAnchorField = isReviewMode
    ? null
    : visibleFields.some((f) => f.name === 'model_no')
      ? 'model_no'
      : visibleFields.some((f) => f.name === 'model_name')
        ? 'model_name'
        : null;

  // -------- small presentational helpers -----------------------------------
  const renderField = (field: CategoryField, autoFocus = false) => {
    // Variant mode: brand/model are LOCKED (the variant shares them); copied
    // sizes stay amber-flagged until the operator confirms (touches) them.
    const isLocked = Boolean(variantCtx?.locked.has(field.name));
    const isFlagged = !isLocked && flaggedFields.has(field.name);
    // CAPS while typing, on the fields the SERVER re-cases on save. This is a
    // CSS transform only: e.target.value still carries exactly what was typed,
    // so there is no controlled-input fight, no caret jump, no IME or paste
    // breakage - and the server stays the sole authority on the stored case.
    // Never applied to a select (the option match is exact, so an uppercased
    // VALUE would render the dropdown blank while the form still saved).
    const capsEntry = field.type === 'text' && CAPS_ENTRY_FIELDS.has(field.name);
    const fieldClass = clsx(
      'input-field w-full',
      capsEntry && 'uppercase placeholder:normal-case',
      isFlagged && 'ring-1 ring-amber-400 bg-amber-50/60',
      isLocked && 'bg-gray-50 text-gray-500 cursor-not-allowed'
    );
    const fieldId = `qa-field-${field.name}`;
    return (
    <div key={field.name}>
      <label className="block text-xs font-medium text-gray-700 mb-1" htmlFor={fieldId}>
        {field.label}
        {field.required && <span className="text-red-500 ml-1">*</span>}
        {isLocked && <LockChip text="locked · same as the model" />}
        {isFlagged && <ConfirmChip />}
      </label>
      {field.type === 'select' ? (
        <select
          ref={autoFocus ? (el) => { firstFieldRef.current = el; } : undefined}
          id={fieldId}
          title={field.label}
          value={attributes[field.name] || ''}
          onChange={(e) => setAttr(field.name, e.target.value)}
          disabled={isLocked}
          className={fieldClass}
        >
          <option value="">Select {field.label}</option>
          {field.options?.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
          {/* A locked value must stay visible even if it's not in the current
              options list (e.g. legacy brand) — inject it as its own option. */}
          {isLocked &&
            attributes[field.name] &&
            !field.options?.includes(attributes[field.name]) && (
              <option value={attributes[field.name]}>{attributes[field.name]}</option>
            )}
        </select>
      ) : field.type === 'date' ? (
        <input
          ref={autoFocus ? (el) => { firstFieldRef.current = el; } : undefined}
          id={fieldId}
          type="date"
          title={field.label}
          value={attributes[field.name] || ''}
          onChange={(e) => setAttr(field.name, e.target.value)}
          disabled={isLocked}
          className={fieldClass}
        />
      ) : (
        <input
          ref={autoFocus ? (el) => { firstFieldRef.current = el; } : undefined}
          id={fieldId}
          type={field.type}
          title={field.label}
          value={attributes[field.name] || ''}
          onChange={(e) => setAttr(field.name, e.target.value)}
          placeholder={field.placeholder || field.label}
          disabled={isLocked}
          className={fieldClass}
        />
      )}
      {/* Brand Master is the single source for brands: an EMPTY options list
          (owner hasn't added brands yet) gets a pointer instead of silently
          offering nothing. */}
      {field.type === 'select' && field.options?.length === 0 && (
        <p className="text-amber-600 text-xs mt-1">
          No {field.name === 'brand_name' ? 'brands' : 'values'} saved yet — add them in{' '}
          <Link to="/settings" className="underline font-medium">
            Settings → {field.name === 'brand_name' ? 'Brand Master' : 'Catalog Dictionary'}
          </Link>.
        </p>
      )}
      {errors[field.name] && (
        <p className="text-red-500 text-xs mt-1">{errors[field.name]}</p>
      )}
    </div>
    );
  };

  // --- AI description ("Auto-fill with AI") ---------------------------------
  // Needs at least brand + model to have anything meaningful to write about.
  // model_no is the eyewear model key; model_name covers the categories that
  // use it instead (CL / smartwatch / accessories).
  const aiDescIdentityMissing =
    !String(attributes.brand_name || '').trim() ||
    !String(attributes.model_no || attributes.model_name || '').trim();

  const handleGenerateDescription = async () => {
    if (generatingDesc || aiDescIdentityMissing || !selectedCategory) return;
    setGeneratingDesc(true);
    try {
      // Send every FILLED attribute value — the backend drafts from these.
      const filled: Record<string, string> = {};
      Object.entries(attributes).forEach(([k, v]) => {
        const val = String(v ?? '').trim();
        if (val) filled[k] = val;
      });
      const res = await productApi.generateDescription({
        category: selectedCategory,
        attributes: filled,
      });
      if (res?.status === 'GENERATED' && res.description) {
        // Overwrite (operator can edit afterwards).
        setDescription(res.description);
      } else if (res?.status === 'FAILED_NO_KEY') {
        toast.error('AI unavailable — add the Anthropic key in Settings');
      } else {
        toast.error(
          res?.message || `Could not generate a description (${res?.status || 'no response'}).`
        );
      }
    } catch {
      // Never blocks saving — the description stays manual on failure.
      toast.error('Could not generate a description — please try again.');
    } finally {
      setGeneratingDesc(false);
    }
  };

  // Weight is a TOP-LEVEL payload field (weight_grams), not an attribute. For
  // SG/FR it renders inline in the attribute grid (between Warranty and UPC —
  // owner-locked order); for every other category it stays under Advanced.
  // Plain render helper (NOT a nested component — avoids the remount bug).
  const renderWeightInput = () => (
    <div>
      <label className="block text-xs font-medium text-gray-700 mb-1">
        Weight (g)
        {flaggedFields.has('weight') && <ConfirmChip />}
      </label>
      <input
        type="number"
        title="Weight (g)"
        value={weight}
        onChange={(e) => { setWeight(e.target.value); clearFlag('weight'); }}
        className={clsx(
          'input-field w-full',
          flaggedFields.has('weight') && 'ring-1 ring-amber-400 bg-amber-50/60'
        )}
        placeholder="e.g. 50"
      />
    </div>
  );

  return (
    <Section
      id="identity"
      title="Identity"
      icon={<Tag className="w-5 h-5" />}
      subtitle="Category, brand, model & specs"
      open={openSections.identity}
      issues={sectionIssues.identity}
      onToggle={toggleSection}
    >
      {/* Category picker (locked in variant mode — a sibling variant is
          by definition the same category as its model — AND in SPINE edit
          mode: category is identity; changing it on an ACTIVE row would
          skip the forward-only restamp, so v1 locks it. Clone-as-new-SKU
          is the wrong-category workaround. REVIEW mode keeps the picker
          ENABLED — re-categorising imports is the whole point; the
          server re-derives HSN & GST on save.) */}
      <div className="mb-5">
        <label className="block text-xs font-medium text-gray-700 mb-2">
          Category <span className="text-red-500">*</span>
          {variantCtx && <LockChip text="locked · same as the model" />}
          {editMode?.kind === 'spine' && <LockChip text="locked while editing" />}
        </label>
        {/* id + tabIndex: the "Still missing: Category" chip lands here.
            Tiles carry a floor height so a two-line name (Colour Contact
            Lens at tablet width) cannot leave a ragged row. */}
        <div
          id="qa-field-category"
          tabIndex={-1}
          className="grid grid-cols-3 tablet:grid-cols-4 laptop:grid-cols-6 gap-2 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-bv"
        >
          {CATEGORIES.map((c) => (
            <button
              key={c.code}
              type="button"
              disabled={
                (Boolean(variantCtx) || editMode?.kind === 'spine') &&
                selectedCategory !== c.code
              }
              onClick={() => setSelectedCategory(c.code)}
              className={clsx(
                'flex flex-col items-center justify-center gap-1 px-2 py-2 min-h-[72px] rounded-lg border text-center transition-all',
                selectedCategory === c.code
                  ? 'border-bv bg-bv-50 ring-1 ring-bv'
                  : 'border-gray-200 hover:border-gray-300',
                (Boolean(variantCtx) || editMode?.kind === 'spine') &&
                  selectedCategory !== c.code &&
                  'opacity-40 cursor-not-allowed hover:border-gray-200'
              )}
            >
              <span className="text-xl leading-none">{c.icon}</span>
              <span className="text-xs font-medium text-gray-800">{c.name}</span>
            </button>
          ))}
        </div>
        {/* Review mode: the category changed — NO local HSN/GST rewrite
            happens; the server re-derives both on save and the response
            re-seeds the fields below. */}
        {isReviewMode &&
          reviewSnapshot &&
          selectedCategory &&
          selectedCategory !== reviewSnapshot.values.category && (
            <p className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-2.5 py-1 text-xs text-blue-700">
              <Info className="w-3.5 h-3.5" />
              HSN &amp; GST update automatically for the new category.
            </p>
          )}
        {errors.category && (
          <p className="text-red-500 text-xs mt-2">{errors.category}</p>
        )}
      </div>

      {/* Review-only: display name (PUT `name` -> name + title) and the
          governed tags editor. Rendered even before a category is picked
          so an unmapped import can still be named/tagged. */}
      {isReviewMode && (
        <div className="mb-5 grid grid-cols-1 tablet:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Display name
            </label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="input-field w-full"
              placeholder="Shown on catalog cards & online listings"
              title="Display name"
            />
            <p className="text-xs text-gray-400 mt-1">
              Saving updates the product's name and title.
            </p>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-700 mb-1">Tags</label>
            <input
              type="text"
              className="input-field w-full"
              placeholder="Type a tag, press Enter or comma"
              title="Add tag"
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ',') {
                  e.preventDefault();
                  const input = e.currentTarget;
                  const value = input.value.trim();
                  if (value && !reviewTags.includes(value)) {
                    setReviewTags([...reviewTags, value]);
                    input.value = '';
                  }
                }
              }}
            />
            {reviewTags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {reviewTags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex items-center px-2 py-0.5 text-xs bg-gray-100 rounded-full"
                  >
                    {tag}
                    <button
                      type="button"
                      onClick={() => setReviewTags(reviewTags.filter((t) => t !== tag))}
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
        </div>
      )}

      {selectedCategory && (
        <>
          {/* Category-specific fields. For SG/FR the Weight input is
              injected right after Warranty (before UPC) per the
              owner-locked field order. */}
          <div className="grid grid-cols-1 tablet:grid-cols-2 laptop:grid-cols-3 desktop:grid-cols-4 gap-3">
            {visibleFields.map((f, i) => {
              if (isEyewear && f.name === 'warranty') {
                return (
                  <Fragment key={f.name}>
                    {renderField(f, i === 0)}
                    {renderWeightInput()}
                  </Fragment>
                );
              }
              if (f.name === similarAnchorField) {
                // Similar-products strip (Phase 2): a full-width quiet
                // line straight under the model field. The component
                // renders NOTHING (no cell, no gap) until it has fresh
                // matches, so the grid is untouched while typing.
                return (
                  <Fragment key={f.name}>
                    {renderField(f, i === 0)}
                    <SimilarProductsHint
                      category={selectedCategory}
                      brand={attributes.brand_name || ''}
                      model={attributes.model_no || attributes.model_name || ''}
                      colour={attributes.colour_code || ''}
                      size={attributes.size || ''}
                      variantMode={Boolean(variantCtx)}
                      onPickSibling={handleSimilarPick}
                      onOpenExisting={handleSimilarOpen}
                    />
                  </Fragment>
                );
              }
              return renderField(f, i === 0);
            })}
            {/* Fallback: eyewear categories missing a warranty field
                still get the inline Weight input at the end. */}
            {isEyewear && !visibleFields.some((f) => f.name === 'warranty') && renderWeightInput()}
          </div>

          {/* Lenses: route power entry to the Power Grid */}
          {isLens && (
            <div className="mt-4 flex items-start gap-3 rounded-lg border border-bv-50 bg-bv-soft p-3 text-sm">
              <Sparkles className="w-4 h-4 text-bv mt-0.5 shrink-0" />
              <div className="text-gray-700">
                Optical-lens stock power (SPH × CYL) is managed in the{' '}
                <Link to="/inventory/power-grid" className="font-medium text-bv underline">
                  Power Grid
                </Link>{' '}
                — enter per-power on-hand there instead of one SKU at a time. Brand, index &
                coating saved here become the grid's identity.
              </div>
            </div>
          )}

          {/* Description (+ AI auto-fill; generation never blocks save) */}
          <div className="mt-4">
            <div className="flex items-center justify-between mb-1">
              <label className="block text-xs font-medium text-gray-700">Description</label>
              <button
                type="button"
                onClick={() => void handleGenerateDescription()}
                disabled={generatingDesc || aiDescIdentityMissing}
                title={
                  aiDescIdentityMissing
                    ? 'Fill Brand and Model first'
                    : 'Draft a description from the filled fields'
                }
                className="inline-flex items-center gap-1 rounded-md border border-blue-200 bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {generatingDesc ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <SparklesIcon className="w-3 h-3" />
                )}
                Auto-fill with AI
              </button>
            </div>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              className="input-field w-full"
              placeholder="Optional product description…"
            />
          </div>

          {/* Advanced (HSN / GST / weight) — collapsed by default */}
          <div className="mt-4 border-t border-gray-100 pt-3">
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              className="flex items-center gap-1.5 text-sm font-medium text-gray-600 hover:text-gray-900"
            >
              <ChevronDown className={clsx('w-4 h-4 transition-transform', showAdvanced && 'rotate-180')} />
              {isEyewear ? 'Advanced — HSN & GST' : 'Advanced — HSN, GST & weight'}
              {/* The row itself says when the required HSN is missing,
                  so "auto-filled" never reads as "nothing to do here". */}
              {liveErrors.hsn_code ? (
                <span className="ml-1 inline-flex items-center rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-medium text-red-700">
                  HSN Code required
                </span>
              ) : (
                <span className="ml-1 text-xs text-gray-400">(auto-filled from category)</span>
              )}
            </button>

            {showAdvanced && (
              <div className="mt-3 space-y-3">
                <div className="grid grid-cols-1 tablet:grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-700 mb-1" htmlFor="qa-field-hsn_code">
                      HSN Code <span className="text-red-500">*</span>
                    </label>
                    <select
                      id="qa-field-hsn_code"
                      title="HSN Code"
                      value={hsnCode}
                      onChange={(e) => {
                        setHsnCode(e.target.value);
                        const option = hsnOptions().find((o) => o.value === e.target.value);
                        if (option) setGstRate(option.gstRate.toString());
                        clearFlag('hsn_code');
                        clearFlag('gst_rate');
                      }}
                      className={clsx(
                        'input-field w-full',
                        flaggedFields.has('hsn_code') && 'ring-1 ring-amber-400 bg-amber-50/60'
                      )}
                    >
                      <option value="">Select HSN Code</option>
                      {/* Server-fed (gstRuntime.hsnOptions): the list has
                          to contain every code the category autofill above
                          can set, or this REQUIRED field renders blank on
                          the categories that need it most -- 852580
                          (smartglasses) and 9993 (eye tests) exist only in
                          the server's canonical table. */}
                      {hsnOptions().map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                    {errors.hsn_code ? (
                      <p className="text-red-500 text-xs mt-1">{errors.hsn_code}</p>
                    ) : (
                      <p className="text-xs text-gray-500 mt-1">
                        {isReviewMode
                          ? 'Leave untouched to auto-derive from the category on save'
                          : 'Auto-selected based on category'}
                      </p>
                    )}
                  </div>
                  <div>
                    <span className="block text-xs font-medium text-gray-700 mb-1">GST Rate</span>
                    {/* Plain text, not a read-only box: nothing here is
                        typed. The HSN decides the rate, and the SERVER
                        reads the HSN -- the save derives gst_rate from
                        hsn_code (product_master.normalise_payload). So
                        the moment this HSN is not the one this category
                        implies, the number goes and only the promise
                        stays. The value still posts (gst_rate). */}
                    <p
                      className="flex min-h-9 flex-wrap items-center gap-2 text-sm"
                      data-testid="qa-gst-rate"
                    >
                      {hsnMatchesCategory && (
                        <span className="text-base font-semibold text-gray-900">{gstRate}%</span>
                      )}
                      <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600">
                        {hsnMatchesCategory
                          ? 'settled from the HSN on save'
                          : `settled from HSN ${hsnCode} on save`}
                      </span>
                    </p>
                  </div>
                  {/* SG/FR render Weight inline in the attribute grid
                      (between Warranty and UPC) — not duplicated here. */}
                  {!isEyewear && renderWeightInput()}
                </div>

              </div>
            )}
          </div>
        </>
      )}
    </Section>
  );
}
